"""API do portal do obskitpy.

Executar:  uvicorn obskitpy.portal.app:create_app --factory --host 0.0.0.0 --port 8000
"""
import asyncio
import json
import re
import secrets as pysecrets
import urllib.parse
from contextlib import asynccontextmanager
from importlib.metadata import PackageNotFoundError, version

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from . import automations, providers, schemas
from .db import Base, make_engine, make_session_factory
from .models import (Automation, AutomationRun, AuditEvent, Contact, ContactGroup, Destination, OAuthApp, ProviderConnection, User, now)
from .security import (LoginLimiter, decrypt_secrets, encrypt_secrets, hash_password, make_state, make_token, read_state,
                       read_token, verify_password)
from .settings import Settings, load

COOKIE = "obskitpy_session"
CSRF_HEADER = "x-obskitpy-csrf"
RANK = {"viewer": 0, "operator": 1, "admin": 2}


def _version() -> str:
    try:
        return version("obskitpy")
    except PackageNotFoundError:
        return "dev"


def create_app(settings: Settings | None = None, engine=None) -> FastAPI:
    settings = settings or load()
    engine = engine or make_engine(settings.database_url)
    sessions = make_session_factory(engine)
    limiter = LoginLimiter()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        Base.metadata.create_all(engine)
        with sessions() as db:  # primeiro acesso: cria o administrador definido no ambiente
            if settings.admin_email and settings.admin_password and not db.scalar(select(User.id).limit(1)):
                db.add(User(email=settings.admin_email.lower(), name="Administrador", role="admin",
                            password_hash=hash_password(settings.admin_password)))
                db.commit()
        worker = asyncio.create_task(automations.run_forever(sessions, settings.secret_key, settings.es_url, settings.eval_interval)) if settings.es_url else None
        yield
        if worker:
            worker.cancel()

    app = FastAPI(title="obskitpy portal", version=_version(), lifespan=lifespan, docs_url="/api/docs", openapi_url="/api/openapi.json")
    app.state.settings, app.state.sessions = settings, sessions

    # ---------- dependências ----------
    def get_db():
        with sessions() as db:
            yield db

    def current_user(request: Request, db: Session = Depends(get_db)) -> User:
        bearer = request.headers.get("authorization", "")
        token = bearer[7:] if bearer.lower().startswith("bearer ") else request.cookies.get(COOKIE)
        uid = read_token(settings.secret_key, token) if token else None
        user = db.get(User, uid) if uid else None
        if not user or not user.active:
            raise HTTPException(401, "faça login para continuar")
        # sessão por cookie + método que altera dados: exige o cabeçalho (defesa contra CSRF)
        if not bearer and request.method not in ("GET", "HEAD", "OPTIONS") and not request.headers.get(CSRF_HEADER):
            raise HTTPException(403, "requisição sem o cabeçalho de proteção X-Obskitpy-Csrf")
        return user

    def need(role: str):
        def check(user: User = Depends(current_user)) -> User:
            if RANK[user.role] < RANK[role]:
                raise HTTPException(403, f"esta ação exige o perfil {role}")
            return user
        return check

    def audit(db: Session, actor: str, action: str, entity: str = "", entity_id="", **detail) -> None:
        db.add(AuditEvent(actor=actor, action=action, entity=entity, entity_id=str(entity_id), detail=detail))
        db.commit()

    def conflict(db: Session, what: str):
        db.rollback()
        raise HTTPException(409, f"já existe {what} com esse nome")

    def bad(exc: Exception):
        raise HTTPException(422, str(exc))

    # ---------- saída ----------
    def out_integration(c: ProviderConnection) -> schemas.IntegrationOut:
        stored = list(decrypt_secrets(settings.secret_key, c.secrets_enc)) if c.secrets_enc else []
        return schemas.IntegrationOut(id=c.id, kind=c.kind, label=providers.PROVIDERS[c.kind].label, name=c.name, config=c.config,
                                      stored_secrets=stored, status=c.status, last_tested_at=c.last_tested_at,
                                      last_error=c.last_error, destinations=len(c.destinations))

    def out_destination(d: Destination) -> schemas.DestinationOut:
        return schemas.DestinationOut(id=d.id, connection_id=d.connection_id, connection=d.connection.name,
                                      kind=d.connection.kind, name=d.name, target=d.target)

    # ---------- saúde e autenticação ----------
    @app.get("/api/v1/health")
    def health(db: Session = Depends(get_db)):
        db.execute(text("SELECT 1"))
        return {"status": "ok", "version": _version()}

    @app.post("/api/v1/auth/login", response_model=schemas.UserOut)
    def login(body: schemas.LoginIn, request: Request, response: Response, db: Session = Depends(get_db)):
        key = f"{body.email.lower()}|{request.client.host if request.client else ''}"
        if limiter.blocked(key):
            raise HTTPException(429, "muitas tentativas; aguarde um minuto")
        user = db.scalar(select(User).where(User.email == body.email.lower()))
        if not user or not user.active or not verify_password(body.password, user.password_hash):
            limiter.fail(key)
            audit(db, body.email.lower(), "login.failed")
            raise HTTPException(401, "e-mail ou senha incorretos")
        limiter.reset(key)
        response.set_cookie(COOKIE, make_token(settings.secret_key, user.id, settings.session_ttl_seconds),
                            max_age=settings.session_ttl_seconds, httponly=True, samesite="strict", secure=settings.cookie_secure, path="/")
        audit(db, user.email, "login")
        return user

    @app.post("/api/v1/auth/logout", status_code=204)
    def logout(response: Response):
        response.delete_cookie(COOKIE, path="/")

    @app.get("/api/v1/auth/me", response_model=schemas.UserOut)
    def me(user: User = Depends(current_user)):
        return user

    # ---------- usuários ----------
    @app.get("/api/v1/users", response_model=list[schemas.UserOut])
    def list_users(db: Session = Depends(get_db), _: User = Depends(need("admin"))):
        return db.scalars(select(User).order_by(User.email)).all()

    @app.post("/api/v1/users", response_model=schemas.UserOut, status_code=201)
    def create_user(body: schemas.UserIn, db: Session = Depends(get_db), actor: User = Depends(need("admin"))):
        user = User(email=body.email.lower(), name=body.name, role=body.role, password_hash=hash_password(body.password))
        db.add(user)
        try:
            db.commit()
        except IntegrityError:
            conflict(db, "um usuário")
        audit(db, actor.email, "user.create", "user", user.id, email=user.email, role=user.role)
        return user

    @app.delete("/api/v1/users/{user_id}", status_code=204)
    def deactivate_user(user_id: int, db: Session = Depends(get_db), actor: User = Depends(need("admin"))):
        user = db.get(User, user_id)
        if not user:
            raise HTTPException(404, "usuário não encontrado")
        if user.id == actor.id:
            raise HTTPException(422, "você não pode desativar o seu próprio usuário")
        user.active = False
        db.commit()
        audit(db, actor.email, "user.deactivate", "user", user.id)

    # ---------- integrações ----------
    @app.get("/api/v1/integrations/kinds")
    def kinds(_: User = Depends(current_user)):
        return providers.catalog()

    @app.get("/api/v1/integrations", response_model=list[schemas.IntegrationOut])
    def list_integrations(db: Session = Depends(get_db), _: User = Depends(current_user)):
        return [out_integration(c) for c in db.scalars(select(ProviderConnection).order_by(ProviderConnection.name))]

    @app.post("/api/v1/integrations", response_model=schemas.IntegrationOut, status_code=201)
    def create_integration(body: schemas.IntegrationIn, db: Session = Depends(get_db), actor: User = Depends(need("admin"))):
        if body.kind == "slack_app":
            raise HTTPException(422, "o Slack conectado é criado pelo botão “Conectar com Slack”, não por este cadastro")
        try:
            config, secrets = providers.validate(body.kind, body.config, body.secrets, settings.allow_private_webhooks)
        except ValueError as exc:
            bad(exc)
        conn = ProviderConnection(kind=body.kind, name=body.name.strip(), config=config, secrets_enc=encrypt_secrets(settings.secret_key, secrets))
        db.add(conn)
        try:
            db.commit()
        except IntegrityError:
            conflict(db, "uma integração")
        audit(db, actor.email, "integration.create", "integration", conn.id, kind=conn.kind, name=conn.name)
        return out_integration(conn)

    @app.put("/api/v1/integrations/{conn_id}", response_model=schemas.IntegrationOut)
    def update_integration(conn_id: int, body: schemas.IntegrationUpdate, db: Session = Depends(get_db), actor: User = Depends(need("admin"))):
        conn = db.get(ProviderConnection, conn_id)
        if not conn:
            raise HTTPException(404, "integração não encontrada")
        if conn.kind == "slack_app" and body.secrets:
            raise HTTPException(422, "o token do Slack só muda reconectando (botão “Conectar com Slack”)")
        stored = decrypt_secrets(settings.secret_key, conn.secrets_enc) if conn.secrets_enc else {}
        try:
            config, new_secrets = providers.validate(conn.kind, {**conn.config, **(body.config or {})}, body.secrets or {},
                                                     settings.allow_private_webhooks, partial_secrets=True)
        except ValueError as exc:
            bad(exc)
        merged = {**stored, **new_secrets}
        if body.name:
            conn.name = body.name.strip()
        conn.config, conn.secrets_enc = config, encrypt_secrets(settings.secret_key, merged)
        conn.status, conn.last_error = "untested", ""  # mudou: precisa testar de novo
        try:
            db.commit()
        except IntegrityError:
            conflict(db, "uma integração")
        audit(db, actor.email, "integration.update", "integration", conn.id, secrets_changed=bool(new_secrets))
        return out_integration(conn)

    @app.delete("/api/v1/integrations/{conn_id}", status_code=204)
    def delete_integration(conn_id: int, db: Session = Depends(get_db), actor: User = Depends(need("admin"))):
        conn = db.get(ProviderConnection, conn_id)
        if not conn:
            raise HTTPException(404, "integração não encontrada")
        name = conn.name
        db.delete(conn)
        db.commit()
        audit(db, actor.email, "integration.delete", "integration", conn_id, name=name)

    @app.post("/api/v1/integrations/{conn_id}/test", response_model=schemas.TestOut)
    async def test_integration(conn_id: int, body: schemas.TestIn, db: Session = Depends(get_db), actor: User = Depends(need("operator"))):
        conn = db.get(ProviderConnection, conn_id)
        if not conn:
            raise HTTPException(404, "integração não encontrada")
        target = None
        if body.destination_id is not None:
            dest = db.get(Destination, body.destination_id)
            if not dest or dest.connection_id != conn.id:
                raise HTTPException(422, "esse destino não pertence a esta integração")
            target = dest.target
        provider = providers.PROVIDERS[conn.kind]
        secrets = decrypt_secrets(settings.secret_key, conn.secrets_enc) if conn.secrets_enc else {}
        message = providers.Message("Teste do obskitpy", "Se você recebeu esta mensagem, a integração está funcionando.", "info",
                                    [("Integração", conn.name), ("Testado por", actor.email)])
        result = await provider.test(conn.config, secrets, target, message)
        conn.status, conn.last_tested_at = ("ok" if result.ok else "failed"), now()
        conn.last_error = "" if result.ok else result.detail[:500]
        db.commit()
        audit(db, actor.email, "integration.test", "integration", conn.id, ok=result.ok, destination_id=body.destination_id)
        return schemas.TestOut(ok=result.ok, detail=result.detail, retry_after=result.retry_after)

    # ---------- Slack: "Conectar com Slack" (OAuth) ----------
    origin_ok = re.compile(r"^https?://(localhost|127\.0\.0\.1)(:\d{2,5})?$")

    def slack_redirect_uri() -> str:
        return f"{settings.public_origin}/api/v1/oauth/slack/callback" if settings.public_origin else settings.oauth_relay_url

    @app.get("/api/v1/oauth/slack/status")
    def slack_status(db: Session = Depends(get_db), _: User = Depends(current_user)):
        row = db.get(OAuthApp, "slack")
        redirect = slack_redirect_uri()
        manifest = json.dumps(providers.slack_manifest(redirect), separators=(",", ":"), ensure_ascii=False)
        return {"configured": bool(row), "client_id": row.client_id if row else "", "redirect_uri": redirect,
                "mode": "direct" if settings.public_origin else "relay", "scopes": list(providers.SLACK_BOT_SCOPES),
                "manifest_url": "https://api.slack.com/apps?new_app=1&manifest_json=" + urllib.parse.quote(manifest, safe="")}

    @app.put("/api/v1/oauth/slack/app", status_code=204)
    def slack_save_app(body: schemas.SlackAppIn, db: Session = Depends(get_db), actor: User = Depends(need("admin"))):
        client_id, secret = body.client_id.strip(), body.client_secret.strip()
        if not re.fullmatch(r"\d+\.\d+", client_id):
            raise HTTPException(422, "o Client ID do Slack tem o formato 1234567890.1234567890")
        if not re.fullmatch(r"[0-9a-fA-F]{20,64}", secret):
            raise HTTPException(422, "o Client Secret do Slack é uma sequência de letras e números (copie de Basic Information)")
        row = db.get(OAuthApp, "slack") or OAuthApp(provider="slack")
        row.client_id, row.client_secret_enc = client_id, encrypt_secrets(settings.secret_key, {"secret": secret})
        db.add(row)
        db.commit()
        audit(db, actor.email, "oauth.slack.configure", "oauth_app", "slack", client_id=client_id)

    @app.delete("/api/v1/oauth/slack/app", status_code=204)
    def slack_delete_app(db: Session = Depends(get_db), actor: User = Depends(need("admin"))):
        row = db.get(OAuthApp, "slack")
        if row:
            db.delete(row)
            db.commit()
            audit(db, actor.email, "oauth.slack.remove", "oauth_app", "slack")

    @app.post("/api/v1/oauth/slack/start")
    def slack_start(body: schemas.SlackStartIn, db: Session = Depends(get_db), actor: User = Depends(need("admin"))):
        row = db.get(OAuthApp, "slack")
        if not row:
            raise HTTPException(422, "informe primeiro o Client ID e o Client Secret do app do Slack")
        origin = body.origin.rstrip("/")
        if not origin_ok.match(origin) and origin != settings.public_origin:
            raise HTTPException(422, "origem não permitida para o retorno do Slack")
        state = make_state(settings.secret_key, {"o": origin, "u": actor.id, "n": pysecrets.token_urlsafe(8)})
        query = urllib.parse.urlencode({"client_id": row.client_id, "scope": ",".join(providers.SLACK_BOT_SCOPES),
                                        "redirect_uri": slack_redirect_uri(), "state": state})
        audit(db, actor.email, "oauth.slack.start", "oauth_app", "slack")
        return {"url": f"https://slack.com/oauth/v2/authorize?{query}"}

    @app.get("/api/v1/oauth/slack/callback")
    async def slack_callback(code: str = "", state: str = "", error: str = "", db: Session = Depends(get_db)):
        # chega por redirecionamento vindo do Slack (outro site): não há cookie, o `state` assinado identifica o usuário
        payload = read_state(settings.secret_key, state)
        if not payload:
            return HTMLResponse("<h1>Link expirado ou inválido</h1><p>Volte ao obskitpy e clique em “Conectar com Slack” de novo.</p>", status_code=400)

        def back(**query) -> RedirectResponse:
            return RedirectResponse(f"{payload['o']}/#integrations?{urllib.parse.urlencode(query)}", status_code=303)

        if error:
            return back(slack_error=error)
        user, row = db.get(User, payload["u"]), db.get(OAuthApp, "slack")
        if not user or not user.active or user.role != "admin" or not row:
            return back(slack_error="sessão inválida")
        secret = decrypt_secrets(settings.secret_key, row.client_secret_enc)["secret"]
        body = await providers.slack_call("oauth.v2.access", data={"client_id": row.client_id, "client_secret": secret, "code": code,
                                                                  "redirect_uri": slack_redirect_uri()})
        if not body.get("ok") or not body.get("access_token"):
            return back(slack_error=body.get("error", "erro"))
        team = body.get("team") or {}
        config_new = {"team_id": team.get("id", ""), "team_name": team.get("name", ""), "bot_user_id": body.get("bot_user_id", "")}
        secrets_enc = encrypt_secrets(settings.secret_key, {"bot_token": body["access_token"]})
        conn = next((c for c in db.scalars(select(ProviderConnection).where(ProviderConnection.kind == "slack_app"))
                     if c.config.get("team_id") == config_new["team_id"]), None)
        if conn:  # reconectar o mesmo workspace: troca o token e mantém o nome e o ícone do robô
            conn.config, conn.secrets_enc = {**conn.config, **config_new}, secrets_enc
            conn.status, conn.last_error = "untested", ""
        else:
            conn = ProviderConnection(kind="slack_app", name=f"Slack · {team.get('name') or 'workspace'}", secrets_enc=secrets_enc,
                                      config={"bot_name": "obskitpy", **config_new})
            db.add(conn)
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            return back(slack_error="já existe uma integração com esse nome")
        audit(db, user.email, "oauth.slack.connect", "integration", conn.id, team=config_new["team_name"])
        return back(connected=conn.id)

    @app.get("/api/v1/integrations/{conn_id}/slack/channels")
    async def slack_channels(conn_id: int, db: Session = Depends(get_db), _: User = Depends(current_user)):
        conn = db.get(ProviderConnection, conn_id)
        if not conn or conn.kind != "slack_app":
            raise HTTPException(404, "integração do Slack não encontrada")
        token = decrypt_secrets(settings.secret_key, conn.secrets_enc)["bot_token"]
        channels, cursor = [], ""
        for _page in range(5):
            data = {"types": "public_channel,private_channel", "exclude_archived": "true", "limit": "200"}
            if cursor:
                data["cursor"] = cursor
            body = await providers.slack_call("conversations.list", token, data=data)
            if not body.get("ok"):
                raise HTTPException(502, providers.SLACK_ERRORS.get(body.get("error", ""), f"o Slack respondeu: {body.get('error', 'erro')}"))
            channels += [{"id": c["id"], "name": c["name"], "private": bool(c.get("is_private")), "member": bool(c.get("is_member"))}
                         for c in body.get("channels", [])]
            cursor = (body.get("response_metadata") or {}).get("next_cursor", "")
            if not cursor:
                break
        return sorted(channels, key=lambda c: c["name"])

    # ---------- destinos ----------
    @app.get("/api/v1/destinations", response_model=list[schemas.DestinationOut])
    def list_destinations(db: Session = Depends(get_db), _: User = Depends(current_user)):
        return [out_destination(d) for d in db.scalars(select(Destination).order_by(Destination.name))]

    @app.post("/api/v1/destinations", response_model=schemas.DestinationOut, status_code=201)
    def create_destination(body: schemas.DestinationIn, db: Session = Depends(get_db), actor: User = Depends(need("admin"))):
        conn = db.get(ProviderConnection, body.connection_id)
        if not conn:
            raise HTTPException(422, "integração não encontrada")
        try:
            target = providers.validate_target(conn.kind, body.target)
        except ValueError as exc:
            bad(exc)
        dest = Destination(connection_id=conn.id, name=body.name.strip(), target=target)
        db.add(dest)
        db.commit()
        audit(db, actor.email, "destination.create", "destination", dest.id, connection=conn.name)
        return out_destination(dest)

    @app.delete("/api/v1/destinations/{dest_id}", status_code=204)
    def delete_destination(dest_id: int, db: Session = Depends(get_db), actor: User = Depends(need("admin"))):
        dest = db.get(Destination, dest_id)
        if not dest:
            raise HTTPException(404, "destino não encontrado")
        db.delete(dest)
        db.commit()
        audit(db, actor.email, "destination.delete", "destination", dest_id)

    # ---------- automações ----------
    def out_auto(a: Automation) -> schemas.AutomationOut:
        return schemas.AutomationOut(id=a.id, name=a.name, enabled=a.enabled, graph=a.graph, state=a.state or {}, updated_at=a.updated_at or a.created_at)

    def clean_graph(db: Session, body: schemas.AutomationIn) -> dict:
        try:
            return automations.validate_graph(db, body.graph.model_dump())
        except ValueError as exc:
            bad(exc)

    @app.get("/api/v1/automations/nodes")
    def automation_nodes(_: User = Depends(current_user)):
        return {"evaluator": bool(settings.es_url), "variables": list(automations.VARIABLES), "nodes": automations.catalog()}

    @app.get("/api/v1/automations", response_model=list[schemas.AutomationOut])
    def list_automations(db: Session = Depends(get_db), _: User = Depends(current_user)):
        return [out_auto(a) for a in db.scalars(select(Automation).order_by(Automation.name))]

    @app.get("/api/v1/automations/runs", response_model=list[schemas.RunOut])
    def list_runs(limit: int = 50, automation_id: int | None = None, db: Session = Depends(get_db), _: User = Depends(current_user)):
        q = select(AutomationRun).order_by(AutomationRun.at.desc()).limit(min(max(limit, 1), 200))
        if automation_id is not None:
            q = q.where(AutomationRun.automation_id == automation_id)
        return db.scalars(q).all()

    @app.post("/api/v1/automations", response_model=schemas.AutomationOut, status_code=201)
    def create_automation(body: schemas.AutomationIn, db: Session = Depends(get_db), actor: User = Depends(need("admin"))):
        auto = Automation(name=body.name.strip(), enabled=body.enabled, graph=clean_graph(db, body), state={})
        db.add(auto)
        try:
            db.commit()
        except IntegrityError:
            conflict(db, "uma automação")
        audit(db, actor.email, "automation.create", "automation", auto.id)
        return out_auto(auto)

    @app.put("/api/v1/automations/{auto_id}", response_model=schemas.AutomationOut)
    def update_automation(auto_id: int, body: schemas.AutomationIn, db: Session = Depends(get_db), actor: User = Depends(need("admin"))):
        auto = db.get(Automation, auto_id)
        if not auto:
            raise HTTPException(404, "automação não encontrada")
        auto.graph, auto.name, auto.enabled = clean_graph(db, body), body.name.strip(), body.enabled
        ids = {n["id"] for n in auto.graph["nodes"]}
        auto.state = {k: v for k, v in (auto.state or {}).items() if k in ids} if auto.enabled else {}
        try:
            db.commit()
        except IntegrityError:
            conflict(db, "uma automação")
        audit(db, actor.email, "automation.update", "automation", auto.id)
        return out_auto(auto)

    @app.delete("/api/v1/automations/{auto_id}", status_code=204)
    def delete_automation(auto_id: int, db: Session = Depends(get_db), actor: User = Depends(need("admin"))):
        auto = db.get(Automation, auto_id)
        if auto:
            db.execute(update(AutomationRun).where(AutomationRun.automation_id == auto_id).values(automation_id=None))  # o histórico fica
            db.delete(auto)
            db.commit()
            audit(db, actor.email, "automation.delete", "automation", auto_id)

    @app.post("/api/v1/automations/{auto_id}/test")
    async def test_automation(auto_id: int, db: Session = Depends(get_db), actor: User = Depends(need("operator"))):
        """Roda o fluxo agora (de verdade: os avisos são enviados, marcados com [Teste]) para conferir o caminho e os destinos."""
        auto = db.get(Automation, auto_id)
        if not auto:
            raise HTTPException(404, "automação não encontrada")
        steps, values = [], {}
        for node in [n for n in auto.graph["nodes"] if n["type"].startswith("trigger.")]:
            value = 0
            if settings.es_url:
                try:
                    value = await automations.measure(settings.es_url, node["type"], node["config"])
                except Exception as exc:
                    raise HTTPException(502, f"não consegui consultar o Elasticsearch ({type(exc).__name__})")
            values[node["id"]] = value
            ctx = automations._context(auto, node, value, "teste")
            ran = await automations.run_flow(db, settings.secret_key, auto.graph, node["id"], "fired", ctx, test=True)
            automations._record(db, auto, node, "teste", value, ran)
            steps += ran
        db.commit()
        audit(db, actor.email, "automation.test", "automation", auto.id)
        return {"steps": steps, "values": values}

    # ---------- contatos e grupos ----------
    def out_contact(c: Contact) -> schemas.ContactOut:
        return schemas.ContactOut(id=c.id, name=c.name, email=c.email, destination_ids=[d.id for d in c.destinations])

    def out_group(g: ContactGroup) -> schemas.GroupOut:
        return schemas.GroupOut(id=g.id, name=g.name, member_ids=[m.id for m in g.members])

    @app.get("/api/v1/contacts", response_model=list[schemas.ContactOut])
    def list_contacts(db: Session = Depends(get_db), _: User = Depends(current_user)):
        return [out_contact(c) for c in db.scalars(select(Contact).order_by(Contact.name))]

    @app.post("/api/v1/contacts", response_model=schemas.ContactOut, status_code=201)
    def create_contact(body: schemas.ContactIn, db: Session = Depends(get_db), actor: User = Depends(need("admin"))):
        contact = Contact(name=body.name.strip(), email=body.email.strip())
        contact.destinations = list(db.scalars(select(Destination).where(Destination.id.in_(body.destination_ids))))
        db.add(contact)
        db.commit()
        audit(db, actor.email, "contact.create", "contact", contact.id)
        return out_contact(contact)

    @app.delete("/api/v1/contacts/{contact_id}", status_code=204)
    def delete_contact(contact_id: int, db: Session = Depends(get_db), actor: User = Depends(need("admin"))):
        contact = db.get(Contact, contact_id)
        if not contact:
            raise HTTPException(404, "contato não encontrado")
        db.delete(contact)
        db.commit()
        audit(db, actor.email, "contact.delete", "contact", contact_id)

    @app.get("/api/v1/contact-groups", response_model=list[schemas.GroupOut])
    def list_groups(db: Session = Depends(get_db), _: User = Depends(current_user)):
        return [out_group(g) for g in db.scalars(select(ContactGroup).order_by(ContactGroup.name))]

    @app.post("/api/v1/contact-groups", response_model=schemas.GroupOut, status_code=201)
    def create_group(body: schemas.GroupIn, db: Session = Depends(get_db), actor: User = Depends(need("admin"))):
        group = ContactGroup(name=body.name.strip())
        group.members = list(db.scalars(select(Contact).where(Contact.id.in_(body.member_ids))))
        db.add(group)
        try:
            db.commit()
        except IntegrityError:
            conflict(db, "um grupo")
        audit(db, actor.email, "group.create", "group", group.id)
        return out_group(group)

    @app.delete("/api/v1/contact-groups/{group_id}", status_code=204)
    def delete_group(group_id: int, db: Session = Depends(get_db), actor: User = Depends(need("admin"))):
        group = db.get(ContactGroup, group_id)
        if not group:
            raise HTTPException(404, "grupo não encontrado")
        db.delete(group)
        db.commit()
        audit(db, actor.email, "group.delete", "group", group_id)

    # ---------- auditoria ----------
    @app.get("/api/v1/audit", response_model=list[schemas.AuditOut])
    def list_audit(limit: int = 100, db: Session = Depends(get_db), _: User = Depends(need("admin"))):
        return db.scalars(select(AuditEvent).order_by(AuditEvent.id.desc()).limit(min(max(limit, 1), 500))).all()

    return app
