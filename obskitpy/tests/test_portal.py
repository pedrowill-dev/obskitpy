import json

import pytest

pytest.importorskip("fastapi")
import httpx  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import text  # noqa: E402

from obskitpy.portal import providers, security  # noqa: E402
from obskitpy.portal.app import create_app  # noqa: E402
from obskitpy.portal.settings import Settings  # noqa: E402

SECRET = "k" * 40
TOKEN = "123456789:" + "A" * 30
CSRF = {"X-Obskitpy-Csrf": "1"}


@pytest.fixture(autouse=True)
def public_dns(monkeypatch):
    """Todo host resolve para um IP público, a menos que o teste diga o contrário."""
    monkeypatch.setattr(providers.socket, "getaddrinfo", lambda host, *a, **k: [(2, 1, 6, "", ("93.184.216.34", 0))])


@pytest.fixture
def requests_log():
    return []


@pytest.fixture
def client(monkeypatch, requests_log):
    def handler(request: httpx.Request) -> httpx.Response:
        requests_log.append(request)
        if "getMe" in request.url.path:
            ok = TOKEN in str(request.url)
            return httpx.Response(200 if ok else 401, json={"ok": ok, "result": {"username": "meu_bot"}})
        if "sendMessage" in request.url.path:
            if json.loads(request.content)["chat_id"] == "999":
                return httpx.Response(429, headers={"Retry-After": "7"}, json={"ok": False})
            return httpx.Response(200, json={"ok": True})
        if request.url.host == "hooks.slack.com":
            return httpx.Response(200, text="ok")
        return httpx.Response(400, text=f"erro com {request.url}")
    monkeypatch.setattr(providers, "HTTP_TRANSPORT", httpx.MockTransport(handler))
    settings = Settings("sqlite:///:memory:", SECRET, 3600, False, False, "admin@empresa.com", "senha-forte-123")
    app = create_app(settings)
    with TestClient(app) as c:
        c.app_state = app.state
        yield c


def login(client, email="admin@empresa.com", password="senha-forte-123"):
    r = client.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert r.status_code == 200, r.text
    return r


def make_user(client, email, role):
    login(client)
    r = client.post("/api/v1/users", json={"email": email, "password": "outra-senha-123", "role": role}, headers=CSRF)
    assert r.status_code == 201, r.text
    client.post("/api/v1/auth/logout", headers=CSRF)
    client.cookies.clear()


# ---------- segurança ----------

def test_password_hash_and_session_token():
    stored = security.hash_password("segredo-123456")
    assert security.verify_password("segredo-123456", stored) and not security.verify_password("errada", stored)
    assert stored != security.hash_password("segredo-123456")  # sal diferente
    token = security.make_token(SECRET, 7, 60)
    assert security.read_token(SECRET, token) == 7
    assert security.read_token(SECRET, token + "x") is None and security.read_token("outra" * 10, token) is None
    assert security.read_token(SECRET, security.make_token(SECRET, 7, -1)) is None


def test_secrets_are_encrypted_and_roundtrip():
    enc = security.encrypt_secrets(SECRET, {"bot_token": TOKEN})
    assert TOKEN not in enc and security.decrypt_secrets(SECRET, enc) == {"bot_token": TOKEN}
    with pytest.raises(ValueError):
        security.decrypt_secrets("z" * 40, enc)


def test_login_limiter():
    lim = security.LoginLimiter(attempts=2, window=60)
    lim.fail("a"); assert not lim.blocked("a")
    lim.fail("a"); assert lim.blocked("a")
    lim.reset("a"); assert not lim.blocked("a")


# ---------- provedores ----------

def test_webhook_validation_blocks_bad_hosts(monkeypatch):
    with pytest.raises(ValueError, match="https"):
        providers.check_webhook("http://hooks.slack.com/x", ("hooks.slack.com",), False)
    with pytest.raises(ValueError, match="não é um webhook válido"):
        providers.check_webhook("https://evil.example.com/x", ("hooks.slack.com",), False)
    assert providers.check_webhook("https://hooks.slack.com/services/T/B/x", ("hooks.slack.com",), False)
    monkeypatch.setattr(providers.socket, "getaddrinfo", lambda *a, **k: [(2, 1, 6, "", ("10.0.0.5", 0))])
    with pytest.raises(ValueError, match="rede interna"):
        providers.check_webhook("https://hooks.slack.com/x", ("hooks.slack.com",), False)
    assert providers.check_webhook("https://hooks.slack.com/x", ("hooks.slack.com",), True)  # liberado explicitamente


def test_validate_requires_fields_and_formats():
    with pytest.raises(ValueError, match="obrigatório"):
        providers.validate("telegram", {}, {}, False)
    with pytest.raises(ValueError, match="token"):
        providers.validate("telegram", {}, {"bot_token": "curto"}, False)
    with pytest.raises(ValueError, match="desconhecido"):
        providers.validate("whatsapp", {}, {}, False)
    cfg, sec = providers.validate("email", {"host": "smtp.empresa.com", "from_addr": "a@empresa.com", "username": "u"}, {"password": "p"}, False)
    assert cfg["port"] == 587 and sec == {"password": "p"}
    with pytest.raises(ValueError, match="e-mail"):
        providers.validate("email", {"host": "h", "from_addr": "sem-arroba"}, {}, False)


def test_targets_validated():
    assert providers.validate_target("telegram", "-1001234") == "-1001234"
    with pytest.raises(ValueError):
        providers.validate_target("telegram", "abc")
    with pytest.raises(ValueError):
        providers.validate_target("email", "nao-e-email")
    assert providers.validate_target("slack", "") == ""


# ---------- API ----------

def test_health_is_public(client):
    assert client.get("/api/v1/health").json()["status"] == "ok"


def test_auth_required_and_wrong_password(client):
    assert client.get("/api/v1/integrations").status_code == 401
    assert client.post("/api/v1/auth/login", json={"email": "admin@empresa.com", "password": "errada"}).status_code == 401
    login(client)
    assert client.get("/api/v1/auth/me").json()["role"] == "admin"


def test_login_is_rate_limited(client):
    for _ in range(5):
        client.post("/api/v1/auth/login", json={"email": "admin@empresa.com", "password": "errada"})
    assert client.post("/api/v1/auth/login", json={"email": "admin@empresa.com", "password": "senha-forte-123"}).status_code == 429


def test_csrf_header_required_for_cookie_sessions(client):
    login(client)
    body = {"kind": "slack", "name": "s", "secrets": {"webhook_url": "https://hooks.slack.com/services/T/B/x"}}
    assert client.post("/api/v1/integrations", json=body).status_code == 403
    assert client.post("/api/v1/integrations", json=body, headers=CSRF).status_code == 201


def test_integration_secrets_never_leave_the_api_and_are_encrypted_at_rest(client):
    login(client)
    r = client.post("/api/v1/integrations", json={"kind": "telegram", "name": "Bot", "secrets": {"bot_token": TOKEN}}, headers=CSRF)
    assert r.status_code == 201 and TOKEN not in r.text and r.json()["stored_secrets"] == ["bot_token"]
    assert TOKEN not in client.get("/api/v1/integrations").text
    with client.app_state.sessions() as db:
        raw = db.execute(text("SELECT secrets_enc, config FROM provider_connections")).one()
    assert TOKEN not in raw[0] and TOKEN not in str(raw[1])


def test_integration_validation_and_duplicates(client):
    login(client)
    assert client.post("/api/v1/integrations", json={"kind": "telegram", "name": "x", "secrets": {}}, headers=CSRF).status_code == 422
    bad_host = {"kind": "slack", "name": "s", "secrets": {"webhook_url": "https://evil.example.com/x"}}
    assert client.post("/api/v1/integrations", json=bad_host, headers=CSRF).status_code == 422
    ok = {"kind": "slack", "name": "s", "secrets": {"webhook_url": "https://hooks.slack.com/services/T/B/x"}}
    assert client.post("/api/v1/integrations", json=ok, headers=CSRF).status_code == 201
    assert client.post("/api/v1/integrations", json=ok, headers=CSRF).status_code == 409


def test_update_merges_secrets_and_resets_status(client):
    login(client)
    cid = client.post("/api/v1/integrations", json={"kind": "email", "name": "mail", "config": {"host": "smtp.empresa.com", "from_addr": "a@empresa.com", "username": "u"}, "secrets": {"password": "p1"}}, headers=CSRF).json()["id"]
    r = client.put(f"/api/v1/integrations/{cid}", json={"config": {"port": 465}}, headers=CSRF)  # sem tocar nos segredos
    assert r.status_code == 200 and r.json()["stored_secrets"] == ["password"] and r.json()["config"]["port"] == 465
    assert r.json()["status"] == "untested"


def test_test_integration_telegram_ok_and_failures(client, requests_log):
    login(client)
    cid = client.post("/api/v1/integrations", json={"kind": "telegram", "name": "Bot", "secrets": {"bot_token": TOKEN}}, headers=CSRF).json()["id"]
    r = client.post(f"/api/v1/integrations/{cid}/test", json={}, headers=CSRF).json()
    assert r["ok"] and "@meu_bot" in r["detail"]  # só valida o token
    dest = client.post("/api/v1/destinations", json={"connection_id": cid, "name": "Operações", "target": "-100123"}, headers=CSRF).json()
    r = client.post(f"/api/v1/integrations/{cid}/test", json={"destination_id": dest["id"]}, headers=CSRF).json()
    assert r["ok"] and any("sendMessage" in q.url.path for q in requests_log)
    limited = client.post("/api/v1/destinations", json={"connection_id": cid, "name": "Limite", "target": "999"}, headers=CSRF).json()
    r = client.post(f"/api/v1/integrations/{cid}/test", json={"destination_id": limited["id"]}, headers=CSRF).json()
    assert not r["ok"] and r["retry_after"] == 7
    assert client.get("/api/v1/integrations").json()[0]["status"] == "failed"


def test_failed_test_does_not_leak_the_secret(client):
    login(client)
    hook = "https://hooks.slack.com/services/T000/B000/SEGREDOSEGREDO123"
    cid = client.post("/api/v1/integrations", json={"kind": "teams", "name": "t", "secrets": {"webhook_url": "https://x.webhook.office.com/segredo-do-teams"}}, headers=CSRF).json()["id"]
    r = client.post(f"/api/v1/integrations/{cid}/test", json={}, headers=CSRF).json()
    assert not r["ok"] and "segredo-do-teams" not in json.dumps(r)
    assert "segredo-do-teams" not in json.dumps(client.get("/api/v1/integrations").json())
    assert hook  # (o Slack responde ok; o Teams do teste responde 400 no transporte falso)


def test_destination_must_belong_to_integration(client):
    login(client)
    a = client.post("/api/v1/integrations", json={"kind": "telegram", "name": "A", "secrets": {"bot_token": TOKEN}}, headers=CSRF).json()["id"]
    b = client.post("/api/v1/integrations", json={"kind": "slack", "name": "B", "secrets": {"webhook_url": "https://hooks.slack.com/services/T/B/x"}}, headers=CSRF).json()["id"]
    dest = client.post("/api/v1/destinations", json={"connection_id": a, "name": "d", "target": "1"}, headers=CSRF).json()
    assert client.post(f"/api/v1/integrations/{b}/test", json={"destination_id": dest["id"]}, headers=CSRF).status_code == 422
    assert client.post("/api/v1/destinations", json={"connection_id": a, "name": "x", "target": "nao-numero"}, headers=CSRF).status_code == 422


def test_rbac_roles(client):
    make_user(client, "viewer@empresa.com", "viewer")
    make_user(client, "op@empresa.com", "operator")
    login(client, "admin@empresa.com", "senha-forte-123")
    cid = client.post("/api/v1/integrations", json={"kind": "slack", "name": "s", "secrets": {"webhook_url": "https://hooks.slack.com/services/T/B/x"}}, headers=CSRF).json()["id"]
    client.cookies.clear()

    login(client, "viewer@empresa.com", "outra-senha-123")
    assert client.get("/api/v1/integrations").status_code == 200
    assert client.post(f"/api/v1/integrations/{cid}/test", json={}, headers=CSRF).status_code == 403
    assert client.post("/api/v1/integrations", json={}, headers=CSRF).status_code == 403
    assert client.get("/api/v1/audit").status_code == 403
    client.cookies.clear()

    login(client, "op@empresa.com", "outra-senha-123")
    assert client.post(f"/api/v1/integrations/{cid}/test", json={}, headers=CSRF).status_code == 200
    assert client.delete(f"/api/v1/integrations/{cid}", headers=CSRF).status_code == 403
    assert client.get("/api/v1/users").status_code == 403


def test_contacts_groups_and_audit(client):
    login(client)
    cid = client.post("/api/v1/integrations", json={"kind": "telegram", "name": "Bot", "secrets": {"bot_token": TOKEN}}, headers=CSRF).json()["id"]
    dest = client.post("/api/v1/destinations", json={"connection_id": cid, "name": "d", "target": "1"}, headers=CSRF).json()
    contact = client.post("/api/v1/contacts", json={"name": "Ana", "email": "ana@empresa.com", "destination_ids": [dest["id"]]}, headers=CSRF).json()
    assert contact["destination_ids"] == [dest["id"]]
    group = client.post("/api/v1/contact-groups", json={"name": "Equipe SAP", "member_ids": [contact["id"]]}, headers=CSRF).json()
    assert group["member_ids"] == [contact["id"]]
    assert client.post("/api/v1/contact-groups", json={"name": "Equipe SAP"}, headers=CSRF).status_code == 409
    actions = [e["action"] for e in client.get("/api/v1/audit").json()]
    assert {"login", "integration.create", "destination.create", "contact.create", "group.create"} <= set(actions)
    assert client.delete(f"/api/v1/integrations/{cid}", headers=CSRF).status_code == 204
    assert client.get("/api/v1/destinations").json() == []  # destinos saem junto com a integração


def test_new_user_password_policy_and_self_deactivation(client):
    login(client)
    assert client.post("/api/v1/users", json={"email": "x@y.com", "password": "curta"}, headers=CSRF).status_code == 422
    me = client.get("/api/v1/auth/me").json()
    assert client.delete(f"/api/v1/users/{me['id']}", headers=CSRF).status_code == 422


def test_bearer_token_works_without_csrf_header(client):
    r = login(client)
    token = r.cookies.get("obskitpy_session")
    client.cookies.clear()
    r = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200


# ---------- Slack: "Conectar com Slack" (OAuth) ----------
import urllib.parse as up  # noqa: E402

from obskitpy.portal import security as sec  # noqa: E402

BOT_TOKEN = "xoxb-1111-2222-SEGREDODOBOT"


@pytest.fixture
def slack(monkeypatch):
    """Cliente com um Slack falso: responde às chamadas da Web API e guarda o que recebeu."""
    state = {"calls": [], "channels": [{"id": "C0123456789", "name": "operacoes", "is_private": False, "is_member": True},
                                       {"id": "G0123456789", "name": "alertas-sap", "is_private": True, "is_member": True}],
             "post_error": None, "auth_ok": True, "pages": False}

    def handler(request: httpx.Request) -> httpx.Response:
        method = request.url.path.rsplit("/", 1)[-1]
        body = dict(up.parse_qsl(request.content.decode())) if request.headers.get("content-type", "").startswith("application/x-www") \
            else (json.loads(request.content) if request.content else {})
        state["calls"].append((method, body, request.headers.get("authorization", "")))
        if method == "oauth.v2.access":
            if body.get("code") != "codigo-valido":
                return httpx.Response(200, json={"ok": False, "error": "invalid_code"})
            return httpx.Response(200, json={"ok": True, "access_token": BOT_TOKEN, "bot_user_id": "U999", "team": {"id": "T1", "name": "Empresa"}})
        if method == "auth.test":
            return httpx.Response(200, json={"ok": True, "team": "Empresa", "user": "obskitpy"} if state["auth_ok"] else {"ok": False, "error": "invalid_auth"})
        if method == "chat.postMessage":
            return httpx.Response(200, json={"ok": False, "error": state["post_error"]} if state["post_error"] else {"ok": True})
        if method == "conversations.list":
            if state["pages"] and not body.get("cursor"):
                return httpx.Response(200, json={"ok": True, "channels": state["channels"][:1], "response_metadata": {"next_cursor": "pg2"}})
            rest = state["channels"][1:] if state["pages"] else state["channels"]
            return httpx.Response(200, json={"ok": True, "channels": rest, "response_metadata": {"next_cursor": ""}})
        return httpx.Response(404)
    monkeypatch.setattr(providers, "HTTP_TRANSPORT", httpx.MockTransport(handler))
    return state


@pytest.fixture
def sclient(slack):
    settings = Settings("sqlite:///:memory:", SECRET, 3600, False, False, "admin@empresa.com", "senha-forte-123")
    app = create_app(settings)
    with TestClient(app, follow_redirects=False) as c:
        c.app_state = app.state
        yield c


CLIENT_ID, CLIENT_SECRET = "1234567890.0987654321", "a1b2c3d4e5f60718293a4b5c6d7e8f90"


def configure_slack_app(client):
    r = client.put("/api/v1/oauth/slack/app", json={"client_id": CLIENT_ID, "client_secret": CLIENT_SECRET}, headers=CSRF)
    assert r.status_code == 204, r.text


def start_oauth(client, origin="http://localhost:8602"):
    r = client.post("/api/v1/oauth/slack/start", json={"origin": origin}, headers=CSRF)
    assert r.status_code == 200, r.text
    return r.json()["url"]


def state_of(url):
    return up.parse_qs(up.urlparse(url).query)["state"][0]


def connect_slack(client, team_ok=True):
    login(client)
    configure_slack_app(client)
    state = state_of(start_oauth(client))
    r = client.get("/api/v1/oauth/slack/callback", params={"code": "codigo-valido", "state": state})
    assert r.status_code == 303, r.text
    return r


def test_slack_status_has_manifest_and_relay(sclient):
    login(sclient)
    st = sclient.get("/api/v1/oauth/slack/status").json()
    assert st["configured"] is False and st["mode"] == "relay" and st["redirect_uri"].startswith("https://")
    manifest = json.loads(up.unquote(st["manifest_url"].split("manifest_json=")[1]))
    assert manifest["oauth_config"]["redirect_urls"] == [st["redirect_uri"]]
    assert {"chat:write", "chat:write.customize", "channels:read"} <= set(manifest["oauth_config"]["scopes"]["bot"])
    assert st["manifest_url"].startswith("https://api.slack.com/apps?new_app=1&manifest_json=")


def test_slack_app_credentials_validated_and_secret_not_returned(sclient):
    login(sclient)
    assert sclient.put("/api/v1/oauth/slack/app", json={"client_id": "abc", "client_secret": CLIENT_SECRET}, headers=CSRF).status_code == 422
    assert sclient.put("/api/v1/oauth/slack/app", json={"client_id": CLIENT_ID, "client_secret": "nao-hexadecimal!!"}, headers=CSRF).status_code == 422
    configure_slack_app(sclient)
    st = sclient.get("/api/v1/oauth/slack/status").json()
    assert st["configured"] and st["client_id"] == CLIENT_ID and CLIENT_SECRET not in json.dumps(st)
    with sclient.app_state.sessions() as db:
        assert CLIENT_SECRET not in db.execute(text("SELECT client_secret_enc FROM oauth_apps")).scalar()


def test_start_requires_app_admin_and_allowed_origin(sclient):
    login(sclient)
    assert sclient.post("/api/v1/oauth/slack/start", json={"origin": "http://localhost:8602"}, headers=CSRF).status_code == 422  # sem app
    configure_slack_app(sclient)
    assert sclient.post("/api/v1/oauth/slack/start", json={"origin": "https://evil.example.com"}, headers=CSRF).status_code == 422
    url = start_oauth(sclient, "http://localhost:8602/")
    q = up.parse_qs(up.urlparse(url).query)
    assert url.startswith("https://slack.com/oauth/v2/authorize?") and q["client_id"] == [CLIENT_ID]
    assert q["redirect_uri"][0].endswith("slack-callback.html") and "chat:write.customize" in q["scope"][0]
    payload = sec.read_state(SECRET, q["state"][0])
    assert payload["o"] == "http://localhost:8602" and payload["u"] >= 1


def test_oauth_state_is_signed_and_expires():
    state = sec.make_state(SECRET, {"o": "http://localhost:1", "u": 1})
    assert sec.read_state(SECRET, state)["o"] == "http://localhost:1"
    assert sec.read_state(SECRET, state[:-2] + "xx") is None and sec.read_state("outra" * 10, state) is None
    assert sec.read_state(SECRET, sec.make_state(SECRET, {"o": "x"}, ttl=-1)) is None


def test_slack_connect_creates_encrypted_connection_and_redirects(sclient, slack):
    r = connect_slack(sclient)
    assert r.headers["location"].startswith("http://localhost:8602/#integrations?connected=")
    exchange = next(c for c in slack["calls"] if c[0] == "oauth.v2.access")[1]
    assert exchange["client_secret"] == CLIENT_SECRET and exchange["code"] == "codigo-valido" and exchange["redirect_uri"].startswith("https://")
    ints = sclient.get("/api/v1/integrations").json()
    assert len(ints) == 1 and ints[0]["kind"] == "slack_app" and ints[0]["name"] == "Slack · Empresa"
    assert ints[0]["config"]["bot_name"] == "obskitpy" and ints[0]["config"]["team_name"] == "Empresa"
    assert BOT_TOKEN not in json.dumps(ints)
    with sclient.app_state.sessions() as db:
        raw = db.execute(text("SELECT secrets_enc, config FROM provider_connections")).one()
    assert BOT_TOKEN not in raw[0] and BOT_TOKEN not in str(raw[1])
    assert "oauth.slack.connect" in [e["action"] for e in sclient.get("/api/v1/audit").json()]


def test_slack_reconnect_same_workspace_keeps_bot_name(sclient, slack):
    connect_slack(sclient)
    cid = sclient.get("/api/v1/integrations").json()[0]["id"]
    assert sclient.put(f"/api/v1/integrations/{cid}", json={"config": {"bot_name": "Robô de Compras", "icon_emoji": ":robot_face:"}}, headers=CSRF).status_code == 200
    state = state_of(start_oauth(sclient))
    assert sclient.get("/api/v1/oauth/slack/callback", params={"code": "codigo-valido", "state": state}).status_code == 303
    ints = sclient.get("/api/v1/integrations").json()
    assert len(ints) == 1 and ints[0]["config"]["bot_name"] == "Robô de Compras" and ints[0]["config"]["team_id"] == "T1"


def test_callback_rejects_bad_state_and_handles_slack_errors(sclient, slack):
    login(sclient)
    configure_slack_app(sclient)
    assert sclient.get("/api/v1/oauth/slack/callback", params={"code": "x", "state": "lixo"}).status_code == 400
    assert sclient.get("/api/v1/oauth/slack/callback", params={"code": "x", "state": sec.make_state(SECRET, {"o": "http://localhost:1", "u": 1}, ttl=-5)}).status_code == 400
    state = state_of(start_oauth(sclient))
    r = sclient.get("/api/v1/oauth/slack/callback", params={"error": "access_denied", "state": state})
    assert r.status_code == 303 and "slack_error=access_denied" in r.headers["location"]
    r = sclient.get("/api/v1/oauth/slack/callback", params={"code": "codigo-errado", "state": state})
    assert "slack_error=invalid_code" in r.headers["location"] and sclient.get("/api/v1/integrations").json() == []


def test_callback_requires_the_admin_who_started(sclient, slack):
    login(sclient)
    configure_slack_app(sclient)
    make_user_state = sec.make_state(SECRET, {"o": "http://localhost:8602", "u": 9999, "n": "x"})  # usuário inexistente
    r = sclient.get("/api/v1/oauth/slack/callback", params={"code": "codigo-valido", "state": make_user_state})
    assert "slack_error" in r.headers["location"] and sclient.get("/api/v1/integrations").json() == []


def test_slack_channels_listing_paginates(sclient, slack):
    slack["pages"] = True
    connect_slack(sclient)
    cid = sclient.get("/api/v1/integrations").json()[0]["id"]
    chans = sclient.get(f"/api/v1/integrations/{cid}/slack/channels").json()
    assert [c["name"] for c in chans] == ["alertas-sap", "operacoes"] and chans[0]["private"] is True
    token_header = [c[2] for c in slack["calls"] if c[0] == "conversations.list"][0]
    assert token_header == f"Bearer {BOT_TOKEN}"


def test_slack_test_sends_with_custom_bot_name(sclient, slack):
    connect_slack(sclient)
    cid = sclient.get("/api/v1/integrations").json()[0]["id"]
    sclient.put(f"/api/v1/integrations/{cid}", json={"config": {"bot_name": "Robô SAP", "icon_emoji": ":rotating_light:"}}, headers=CSRF)
    dest = sclient.post("/api/v1/destinations", json={"connection_id": cid, "name": "#operacoes", "target": "C0123456789"}, headers=CSRF).json()
    assert sclient.post("/api/v1/destinations", json={"connection_id": cid, "name": "x", "target": "operacoes"}, headers=CSRF).status_code == 422
    r = sclient.post(f"/api/v1/integrations/{cid}/test", json={"destination_id": dest["id"]}, headers=CSRF).json()
    assert r["ok"] and "Empresa" in r["detail"]
    post = next(c[1] for c in slack["calls"] if c[0] == "chat.postMessage")
    assert post["channel"] == "C0123456789" and post["username"] == "Robô SAP" and post["icon_emoji"] == ":rotating_light:"
    assert sclient.get("/api/v1/integrations").json()[0]["status"] == "ok"


def test_slack_errors_are_explained_in_portuguese(sclient, slack):
    connect_slack(sclient)
    cid = sclient.get("/api/v1/integrations").json()[0]["id"]
    dest = sclient.post("/api/v1/destinations", json={"connection_id": cid, "name": "#x", "target": "C0123456789"}, headers=CSRF).json()
    slack["post_error"] = "not_in_channel"
    r = sclient.post(f"/api/v1/integrations/{cid}/test", json={"destination_id": dest["id"]}, headers=CSRF).json()
    assert not r["ok"] and "/invite" in r["detail"]
    slack["post_error"], slack["auth_ok"] = None, False
    r = sclient.post(f"/api/v1/integrations/{cid}/test", json={}, headers=CSRF).json()
    assert not r["ok"] and "conecte de novo" in r["detail"]


def test_slack_app_cannot_be_created_or_have_its_token_set_by_hand(sclient, slack):
    login(sclient)
    assert sclient.post("/api/v1/integrations", json={"kind": "slack_app", "name": "x", "secrets": {"bot_token": "xoxb-falso"}}, headers=CSRF).status_code == 422
    connect_slack(sclient)
    cid = sclient.get("/api/v1/integrations").json()[0]["id"]
    assert sclient.put(f"/api/v1/integrations/{cid}", json={"secrets": {"bot_token": "xoxb-outro"}}, headers=CSRF).status_code == 422
    assert sclient.put(f"/api/v1/integrations/{cid}", json={"config": {"icon_emoji": "sem-dois-pontos"}}, headers=CSRF).status_code == 422
    assert sclient.put(f"/api/v1/integrations/{cid}", json={"config": {"bot_name": "x" * 81}}, headers=CSRF).status_code == 422


def test_direct_mode_uses_the_portal_callback_when_public_origin_is_set(slack):
    settings = Settings("sqlite:///:memory:", SECRET, 3600, False, False, "admin@empresa.com", "senha-forte-123", public_origin="https://obs.empresa.com")
    with TestClient(create_app(settings)) as c:
        login(c)
        st = c.get("/api/v1/oauth/slack/status").json()
        assert st["mode"] == "direct" and st["redirect_uri"] == "https://obs.empresa.com/api/v1/oauth/slack/callback"
        configure_slack_app(c)
        assert c.post("/api/v1/oauth/slack/start", json={"origin": "https://obs.empresa.com"}, headers=CSRF).status_code == 200


def test_kinds_catalog_marks_slack_app_as_oauth_and_hides_internal_fields(sclient):
    login(sclient)
    kinds = {k["kind"]: k for k in sclient.get("/api/v1/integrations/kinds").json()}
    assert kinds["slack_app"]["oauth"] == "slack" and kinds["telegram"]["oauth"] is None
    assert [f["name"] for f in kinds["slack_app"]["config_fields"]] == ["bot_name", "icon_emoji"]


# ---------- automações ----------

import asyncio  # noqa: E402


async def fake_error(es_url, ntype, config):
    return {"erro": "KeyError: x", "origem": "m.py:f:1", "trace": "Traceback (x)"}
from datetime import timedelta  # noqa: E402

from obskitpy.portal import automations  # noqa: E402
from obskitpy.portal.models import Automation, AutomationRun  # noqa: E402


def make_dest(client):
    login(client)
    integ = client.post("/api/v1/integrations", json={"kind": "telegram", "name": "bot", "secrets": {"bot_token": TOKEN}}, headers=CSRF).json()
    return client.post("/api/v1/destinations", json={"connection_id": integ["id"], "name": "ops", "target": "42"}, headers=CSRF).json()["id"]


def flow(dest, trigger="trigger.error_logs", extra_nodes=(), extra_edges=(), **cfg):
    nodes = [{"id": "t", "type": trigger, "x": 0, "y": 0, "config": {"service": "meu-robo", "threshold": 3, "window_minutes": 5, "repeat_minutes": 10, **cfg}},
             {"id": "n", "type": "action.notify", "x": 300, "y": 0, "config": {"destination_id": dest, "severity": "critical", "message": "{{event}}: {{value}} erros em {{service}}"}},
             {"id": "r", "type": "action.notify", "x": 300, "y": 100, "config": {"destination_id": dest, "title": "Voltou", "message": "tudo certo ({{value}})"}}, *extra_nodes]
    edges = [{"source": "t", "port": "fired", "target": "n"}, {"source": "t", "port": "resolved", "target": "r"}, *extra_edges]
    return {"nodes": nodes, "edges": edges}


def make_auto(client, dest, **kw):
    r = client.post("/api/v1/automations", json={"name": kw.pop("name", "Erros do robô"), "graph": flow(dest, **kw)}, headers=CSRF)
    assert r.status_code == 201, r.text
    return r.json()["id"]


def run_eval(client, auto_id, value, sent):
    async def fake_measure(es_url, ntype, config):
        return value

    async def fake_send(db, key, dest_id, message):
        sent.append(message)
        return True, "enviado"

    with client.app_state.sessions() as db:
        return asyncio.run(automations.evaluate(db, SECRET, "http://es", db.get(Automation, auto_id), fake_measure, fake_send, fake_error))


def test_automation_fires_reminds_and_resolves_through_the_flow(client):
    aid, sent = make_auto(client, make_dest(client)), []
    assert run_eval(client, aid, 1, sent) == [] and not sent                                   # abaixo do limite
    assert run_eval(client, aid, 5, sent) == ["disparou"] and sent[-1].text == "disparou: 5 erros em meu-robo" and sent[-1].severity == "critical"
    assert run_eval(client, aid, 6, sent) == [] and len(sent) == 1                             # continua: ainda sem lembrete
    with client.app_state.sessions() as db:
        auto = db.get(Automation, aid)
        st = {k: dict(v) for k, v in auto.state.items()}
        st["t"]["notified_at"] = (automations.now() - timedelta(minutes=11)).isoformat()
        auto.state = st
        db.commit()
    assert run_eval(client, aid, 6, sent) == ["lembrete"] and sent[-1].text.startswith("lembrete:")
    assert run_eval(client, aid, 0, sent) == ["normalizou"] and sent[-1].title == "Voltou"   # saída "Normalizou" do gatilho
    runs = client.get("/api/v1/automations/runs").json()
    assert [r["event"] for r in runs] == ["normalizou", "lembrete", "disparou"] and all(r["ok"] for r in runs)
    assert client.get("/api/v1/automations").json()[0]["state"]["t"]["state"] == "ok"


def test_condition_node_routes_yes_and_no(client):
    dest = make_dest(client)
    cond = {"id": "c", "type": "logic.if", "x": 150, "y": 0, "config": {"field": "value", "operator": ">=", "value": "10"}}
    g = flow(dest, extra_nodes=[cond])
    g["edges"] = [{"source": "t", "port": "fired", "target": "c"}, {"source": "c", "port": "yes", "target": "n"}, {"source": "c", "port": "no", "target": "r"}]
    aid = client.post("/api/v1/automations", json={"name": "Com condição", "graph": g}, headers=CSRF).json()["id"]
    sent = []
    run_eval(client, aid, 4, sent)                      # 4 < 10 -> caminho "Não" (mensagem "Voltou")
    assert [m.title for m in sent] == ["Voltou"]
    run_eval(client, aid, 0, sent)                      # normaliza (sem ligação em "Normalizou")
    run_eval(client, aid, 12, sent)                     # 12 >= 10 -> caminho "Sim"
    assert sent[-1].severity == "critical"


def test_no_data_and_schedule_triggers(client):
    dest = make_dest(client)
    aid = make_auto(client, dest, trigger="trigger.no_data", name="Parou")
    sent = []
    assert run_eval(client, aid, 0, sent) == ["disparou"] and run_eval(client, aid, 9, sent) == ["normalizou"]
    g = {"nodes": [{"id": "t", "type": "trigger.schedule", "x": 0, "y": 0, "config": {"service": "x", "every_minutes": 1}},
                   {"id": "n", "type": "action.notify", "x": 200, "y": 0, "config": {"destination_id": dest, "message": "{{value}} erros"}}],
         "edges": [{"source": "t", "port": "fired", "target": "n"}]}
    sid = client.post("/api/v1/automations", json={"name": "Resumo", "graph": g}, headers=CSRF).json()["id"]
    assert run_eval(client, sid, 7, sent) == []          # primeiro ciclo só marca o relógio
    with client.app_state.sessions() as db:
        auto = db.get(Automation, sid)
        auto.state = {"t": {"state": "ok", "last_run": (automations.now() - timedelta(minutes=2)).isoformat()}}
        db.commit()
    assert run_eval(client, sid, 7, sent) == ["agendado"] and sent[-1].text == "7 erros"


def test_query_failure_is_recorded_and_state_kept(client):
    aid = make_auto(client, make_dest(client))

    async def boom(es_url, ntype, config):
        raise httpx.ConnectError("x")

    with client.app_state.sessions() as db:
        assert asyncio.run(automations.evaluate(db, SECRET, "http://es", db.get(Automation, aid), boom, error_fn=fake_error)) == []
    st = client.get("/api/v1/automations").json()[0]["state"]["t"]
    assert st["state"] == "ok" and "Elasticsearch" in st["error"]


def test_graph_validation_and_permissions(client):
    dest = make_dest(client)
    def post(graph, name="x"):
        return client.post("/api/v1/automations", json={"name": name, "graph": graph}, headers=CSRF)
    good = flow(dest)
    assert post({"nodes": [good["nodes"][1]], "edges": []}).status_code == 422                          # sem gatilho
    assert post({**good, "nodes": [{**good["nodes"][0], "type": "foo.bar"}, *good["nodes"][1:]]}).status_code == 422
    assert post({**good, "edges": [{"source": "t", "port": "fired", "target": "t"}]}).status_code == 422  # nada liga ao gatilho
    assert post({**good, "edges": [{"source": "t", "port": "nope", "target": "n"}]}).status_code == 422  # porta inexistente
    assert post({**good, "edges": [{"source": "t", "port": "fired", "target": "zz"}]}).status_code == 422
    two = [{"source": "n", "port": "next", "target": "r"}, {"source": "r", "port": "next", "target": "n"}]
    assert post({**good, "edges": good["edges"] + two}).status_code == 422                                # ciclo
    bad_dest = {**good, "nodes": [good["nodes"][0], {**good["nodes"][1], "config": {"destination_id": 999}}]}
    assert post(bad_dest).status_code == 422 and "destino" in post(bad_dest).json()["detail"]
    over = {**good, "nodes": [{**good["nodes"][0], "config": {"threshold": 0}}, *good["nodes"][1:]]}
    assert post(over).status_code == 422                                                                 # limite fora da faixa
    assert post(good, "A").status_code == 201 and post(good, "A").status_code == 409
    aid = client.get("/api/v1/automations").json()[0]["id"]
    client.post("/api/v1/auth/logout", headers=CSRF)
    client.cookies.clear()
    make_user(client, "v@empresa.com", "viewer")
    login(client, "v@empresa.com", "outra-senha-123")
    assert client.get("/api/v1/automations").status_code == 200 and client.get("/api/v1/automations/nodes").json()["nodes"]
    assert client.delete(f"/api/v1/automations/{aid}", headers=CSRF).status_code == 403
    assert client.post("/api/v1/automations", json={"name": "y", "graph": good}, headers=CSRF).status_code == 403


def test_update_disable_and_delete(client):
    dest = make_dest(client)
    aid = make_auto(client, dest)
    sent = []
    run_eval(client, aid, 9, sent)
    r = client.put(f"/api/v1/automations/{aid}", json={"name": "Renomeada", "enabled": False, "graph": flow(dest)}, headers=CSRF)
    assert r.status_code == 200 and r.json()["name"] == "Renomeada" and r.json()["state"] == {}      # pausar zera o estado
    assert client.delete(f"/api/v1/automations/{aid}", headers=CSRF).status_code == 204
    assert client.get("/api/v1/automations").json() == []
    assert client.get("/api/v1/automations/runs").json()[0]["automation_id"] is None                 # histórico sobrevive


def test_test_endpoint_runs_the_flow_for_real(client, requests_log):
    aid = make_auto(client, make_dest(client))
    r = client.post(f"/api/v1/automations/{aid}/test", headers=CSRF)
    assert r.status_code == 200 and [s["label"] for s in r.json()["steps"]] == ["Enviar aviso"] and r.json()["steps"][0]["ok"]
    sent = [q for q in requests_log if "sendMessage" in q.url.path]
    assert sent and "[Teste]" in json.loads(sent[0].content)["text"]
    assert client.get("/api/v1/automations/runs").json()[0]["event"] == "teste"


def test_template_variables_are_whitelisted():
    ctx = {"service": "a", "value": 3, "secret": "x"}
    assert automations.render("{{ service }}={{value}} {{secret}} {{desconhecida}}", ctx) == "a=3 {{secret}} {{desconhecida}}"


def test_error_variables_and_latest_error(monkeypatch):
    ctx = automations._context(type("A", (), {"name": "x"})(), {"config": {"service": "s", "window_minutes": 5}}, 2, "disparou",
                               {"erro": "KeyError: 'a'", "origem": "m.py:f:3", "trace": "Traceback..."})
    assert automations.render("{{erro}} | {{origem}} | {{trace}}", ctx) == "KeyError: 'a' | m.py:f:3 | Traceback..."
    assert automations._context(type("A", (), {"name": "x"})(), {"config": {}}, 0, "e")["trace"] == ""  # sem erro, vazio (não fica {{trace}} cru)

    hit = {"hits": {"hits": [{"_source": {"body": {"text": "falhou"}, "attributes": {
        "error.type": "KeyError", "error.origin": "m.py:f:3", "exception.stacktrace": "x" * 3000 + "\nKeyError: 'FIM'"}}}]}}

    class Resp:
        status_code = 200
        def json(self): return hit

    class Client:
        def __init__(self, *a, **k): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): pass
        async def post(self, *a, **k): return Resp()

    monkeypatch.setattr(automations.httpx, "AsyncClient", Client)
    got = asyncio.run(automations.latest_error("http://es", "trigger.error_logs", {"window_minutes": 5}))
    assert got["erro"] == "KeyError: 'FIM'" and got["origem"] == "m.py:f:3"
    assert got["trace"].startswith("…") and got["trace"].endswith("'FIM'") and len(got["trace"]) <= automations.TRACE_MAX + 1
    assert asyncio.run(automations.latest_error("http://es", "trigger.no_data", {})) == {}
