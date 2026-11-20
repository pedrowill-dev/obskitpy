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
