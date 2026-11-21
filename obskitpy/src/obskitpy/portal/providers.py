"""Provedores de notificação (Fase 1): Telegram, Microsoft Teams (Workflows), Slack (webhook) e e-mail SMTP.

Cada provedor sabe validar a sua configuração, testar a conexão e enviar uma mensagem. Os segredos chegam
descriptografados só aqui, no backend, e nunca aparecem em respostas, logs ou erros.
"""
import asyncio
import html
import ipaddress
import re
import smtplib
import socket
import ssl
from dataclasses import dataclass, field
from email.message import EmailMessage
from urllib.parse import urlparse

import httpx

HTTP_TRANSPORT: httpx.AsyncBaseTransport | None = None  # os testes injetam um transporte falso
TIMEOUT = 10.0
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


@dataclass
class Message:
    title: str
    text: str
    severity: str = "info"  # info | warning | critical
    fields: list[tuple[str, str]] = field(default_factory=list)


@dataclass
class Result:
    ok: bool
    detail: str = ""
    retry_after: int | None = None


# ---------- proteção contra SSRF ----------

def _private(ip: str) -> bool:
    addr = ipaddress.ip_address(ip)
    return addr.is_private or addr.is_loopback or addr.is_link_local or addr.is_reserved or addr.is_multicast or addr.is_unspecified


def assert_public_host(host: str, allow_private: bool) -> None:
    """Recusa destinos que resolvem para endereços internos (evita usar o portal como proxy para a rede interna)."""
    if allow_private:
        return
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror:
        raise ValueError(f"não consegui resolver o endereço {host!r}")
    for info in infos:
        if _private(info[4][0]):
            raise ValueError(f"o endereço {host!r} aponta para a rede interna e foi bloqueado")


def check_webhook(url: str, suffixes: tuple[str, ...], allow_private: bool) -> str:
    parsed = urlparse(url)
    if parsed.scheme != "https" or not parsed.hostname:
        raise ValueError("a URL do webhook precisa começar com https://")
    host = parsed.hostname.lower()
    if not any(host == s.lstrip(".") or host.endswith(s if s.startswith(".") else "." + s) for s in suffixes):
        raise ValueError(f"o endereço {host!r} não é um webhook válido deste provedor (esperado: {', '.join(suffixes)})")
    assert_public_host(host, allow_private)
    return url


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=TIMEOUT, transport=HTTP_TRANSPORT, follow_redirects=False)


def _clean(text: str, secrets: dict) -> str:
    for value in secrets.values():
        if isinstance(value, str) and len(value) > 3:
            text = text.replace(value, "***")
    return text[:300]


def _retry_after(resp: httpx.Response) -> int | None:
    try:
        return int(resp.headers.get("Retry-After") or resp.json().get("parameters", {}).get("retry_after"))
    except (ValueError, TypeError, AttributeError):
        return None


async def _post(url: str, json: dict, secrets: dict) -> Result:
    try:
        async with _client() as client:
            resp = await client.post(url, json=json)
    except httpx.HTTPError as exc:
        return Result(False, _clean(f"falha de rede: {type(exc).__name__}", secrets))
    if resp.status_code == 429:
        return Result(False, "limite de envios do provedor (429)", _retry_after(resp))
    if resp.status_code >= 400:
        return Result(False, _clean(f"o provedor respondeu {resp.status_code}: {resp.text}", secrets))
    return Result(True, "enviado")


# ---------- formatação por provedor ----------

ICON = {"critical": "🔴", "warning": "🟠", "info": "🔵"}

# O `render` marca o que é código (traceback, erro, origem) entre estes caracteres de controle; cada canal os converte
# para o seu formato (bloco de código no Slack/Telegram, fonte monoespaçada no Teams, texto puro no e-mail).
CODE_OPEN, CODE_CLOSE = "\x02", "\x03"


def split_code(text: str) -> list[tuple[bool, str]]:
    """Divide o texto em trechos (é_código, conteúdo), na ordem em que aparecem."""
    parts, pos = [], 0
    for m in re.finditer(f"{CODE_OPEN}(.*?){CODE_CLOSE}", text, re.S):
        if m.start() > pos:
            parts.append((False, text[pos:m.start()]))
        parts.append((True, m.group(1)))
        pos = m.end()
    if pos < len(text):
        parts.append((False, text[pos:]))
    return parts


def plain(text: str) -> str:
    return text.replace(CODE_OPEN, "").replace(CODE_CLOSE, "")


def _is_block(code: str) -> bool:
    return "\n" in code or len(code) > 60


def to_slack(text: str) -> str:
    """Código vira bloco (várias linhas ou longo) ou trecho em linha; nunca texto comum."""
    out = []
    for is_code, chunk in split_code(text):
        if not is_code:
            out.append(chunk)
            continue
        chunk = chunk.replace("`", "\u02cb")  # crase dentro do código não pode fechar o bloco
        out.append(f"```\n{chunk}\n```" if _is_block(chunk) else f"`{chunk}`")
    return "".join(out)


def _icon(m: Message) -> str:
    """O ícone da gravidade, a menos que o título já comece com ele (evita 🔴 🔴)."""
    icon = ICON.get(m.severity, "🔵")
    return "" if plain(m.title).lstrip().startswith(icon) else icon + " "


def _telegram_text(m: Message) -> str:
    body = "".join((f"<pre>{html.escape(c)}</pre>" if _is_block(c) else f"<code>{html.escape(c)}</code>") if is_code else html.escape(c)
                   for is_code, c in split_code(m.text))
    lines = [f"{_icon(m)}<b>{html.escape(plain(m.title))}</b>", body]
    lines += [f"<b>{html.escape(k)}:</b> {html.escape(v)}" for k, v in m.fields]
    return "\n".join(lines)


def _slack_payload(m: Message) -> dict:
    lines = [f"{_icon(m)}*{plain(m.title)}*", to_slack(m.text)] + [f"*{k}:* {plain(v)}" for k, v in m.fields]
    return {"text": "\n".join(lines)}


def _teams_payload(m: Message) -> dict:
    color = {"critical": "Attention", "warning": "Warning"}.get(m.severity, "Accent")
    body = [{"type": "TextBlock", "text": plain(m.title), "weight": "Bolder", "size": "Medium", "color": color, "wrap": True},
            *[{"type": "TextBlock", "text": c.strip("\n"), "wrap": True, **({"fontType": "Monospace"} if is_code else {})}
              for is_code, c in split_code(m.text) if c.strip()]]
    if m.fields:
        body.append({"type": "FactSet", "facts": [{"title": k, "value": v} for k, v in m.fields]})
    return {"type": "message", "attachments": [{"contentType": "application/vnd.microsoft.card.adaptive", "content": {
        "$schema": "http://adaptivecards.io/schemas/adaptive-card.json", "type": "AdaptiveCard", "version": "1.4", "body": body}}]}


# ---------- Slack conectado por OAuth ("Conectar com Slack") ----------

SLACK_API = "https://slack.com/api"
SLACK_BOT_SCOPES = ("chat:write", "chat:write.customize", "chat:write.public", "channels:read", "groups:read")
SLACK_ERRORS = {
    "invalid_auth": "a conexão com o Slack não é mais válida: conecte de novo",
    "token_revoked": "o acesso do obskitpy foi revogado no Slack: conecte de novo",
    "account_inactive": "a conta do app no Slack está inativa: conecte de novo",
    "channel_not_found": "o canal não foi encontrado (confira se ele existe e se é público, ou convide o robô)",
    "not_in_channel": "o robô não está nesse canal: convide-o com /invite no Slack",
    "is_archived": "esse canal está arquivado",
    "missing_scope": "faltam permissões no app do Slack: conecte de novo",
    "ratelimited": "limite de envios do Slack",
}


async def slack_call(method: str, token: str | None = None, *, data: dict | None = None, json: dict | None = None) -> dict:
    """Chama a Web API do Slack (host fixo: slack.com). Devolve o JSON (sempre com a chave `ok`)."""
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    try:
        async with _client() as client:
            resp = await client.post(f"{SLACK_API}/{method}", headers=headers, data=data, json=json)
    except httpx.HTTPError as exc:
        return {"ok": False, "error": f"falha de rede: {type(exc).__name__}"}
    try:
        body = resp.json()
    except ValueError:
        return {"ok": False, "error": f"resposta inesperada do Slack ({resp.status_code})"}
    if resp.status_code == 429:
        body = {"ok": False, "error": "ratelimited", "retry_after": _retry_after(resp)}
    return body


def slack_manifest(redirect_url: str, bot_name: str = "obskitpy") -> dict:
    """Manifesto para criar o app do Slack com um clique (já com permissões e URL de retorno)."""
    return {"display_information": {"name": bot_name, "description": "Alertas de observabilidade das suas automações"},
            "features": {"bot_user": {"display_name": bot_name, "always_online": False}},
            "oauth_config": {"redirect_urls": [redirect_url], "scopes": {"bot": list(SLACK_BOT_SCOPES)}},
            "settings": {"org_deploy_enabled": False, "socket_mode_enabled": False, "token_rotation_enabled": False}}


async def slack_app_send(config: dict, secrets: dict, target: str, m: Message) -> Result:
    payload = {"channel": target, "text": _slack_payload(m)["text"]}
    if config.get("bot_name"):
        payload["username"] = config["bot_name"]
    if config.get("icon_emoji"):
        payload["icon_emoji"] = config["icon_emoji"]
    body = await slack_call("chat.postMessage", secrets["bot_token"], json=payload)
    if body.get("ok"):
        return Result(True, "enviado")
    err = body.get("error", "erro")
    return Result(False, SLACK_ERRORS.get(err, f"o Slack respondeu: {err}"), body.get("retry_after"))


async def slack_app_test(config: dict, secrets: dict, target: str | None, m: Message) -> Result:
    body = await slack_call("auth.test", secrets["bot_token"])
    if not body.get("ok"):
        return Result(False, SLACK_ERRORS.get(body.get("error", ""), f"o Slack respondeu: {body.get('error', 'erro')}"), body.get("retry_after"))
    who = f"{body.get('team', 'workspace')} · {body.get('user', 'bot')}"
    if not target:
        return Result(True, f"conectado a {who} (escolha um canal para enviar uma mensagem de teste)")
    sent = await slack_app_send(config, secrets, target, m)
    return Result(sent.ok, (f"{who}: enviado" if sent.ok else sent.detail), sent.retry_after)


# ---------- provedores ----------

TELEGRAM_API = "https://api.telegram.org"


async def telegram_send(config: dict, secrets: dict, target: str, m: Message) -> Result:
    token = secrets["bot_token"]
    return await _post(f"{TELEGRAM_API}/bot{token}/sendMessage",
                       {"chat_id": target, "text": _telegram_text(m), "parse_mode": "HTML", "disable_web_page_preview": True}, secrets)


async def telegram_test(config: dict, secrets: dict, target: str | None, m: Message) -> Result:
    try:
        async with _client() as client:
            resp = await client.get(f"{TELEGRAM_API}/bot{secrets['bot_token']}/getMe")
    except httpx.HTTPError as exc:
        return Result(False, f"falha de rede: {type(exc).__name__}")
    if resp.status_code != 200 or not resp.json().get("ok"):
        return Result(False, "o Telegram recusou o token do bot (verifique com o BotFather)")
    bot = resp.json()["result"].get("username", "bot")
    if not target:
        return Result(True, f"token válido: @{bot} (escolha um destino para enviar uma mensagem de teste)")
    sent = await telegram_send(config, secrets, target, m)
    return Result(sent.ok, f"@{bot}: " + sent.detail, sent.retry_after)


async def slack_send(config: dict, secrets: dict, target: str, m: Message) -> Result:
    return await _post(secrets["webhook_url"], _slack_payload(m), secrets)


async def teams_send(config: dict, secrets: dict, target: str, m: Message) -> Result:
    return await _post(secrets["webhook_url"], _teams_payload(m), secrets)


def _smtp_deliver(config: dict, secrets: dict, to: str | None, m: Message) -> Result:
    host, port = config["host"], int(config.get("port") or 587)
    try:
        smtp = smtplib.SMTP_SSL(host, port, timeout=TIMEOUT, context=ssl.create_default_context()) if port == 465 \
            else smtplib.SMTP(host, port, timeout=TIMEOUT)
        with smtp:
            if port != 465 and config.get("starttls", True):
                smtp.starttls(context=ssl.create_default_context())
            if config.get("username"):
                smtp.login(config["username"], secrets["password"])
            if not to:
                return Result(True, "login no servidor SMTP ok (escolha um destino para enviar um e-mail de teste)")
            msg = EmailMessage()
            msg["Subject"], msg["From"], msg["To"] = f"[obskitpy] {plain(m.title)}", config["from_addr"], to
            msg.set_content("\n".join([plain(m.text)] + [f"{k}: {v}" for k, v in m.fields]))
            smtp.send_message(msg)
        return Result(True, "e-mail enviado")
    except smtplib.SMTPAuthenticationError:
        return Result(False, "o servidor SMTP recusou o usuário ou a senha")
    except (smtplib.SMTPException, OSError) as exc:
        return Result(False, _clean(f"falha no SMTP: {type(exc).__name__}: {exc}", secrets))


async def email_send(config: dict, secrets: dict, target: str, m: Message) -> Result:
    return await asyncio.to_thread(_smtp_deliver, config, secrets, target, m)


async def email_test(config: dict, secrets: dict, target: str | None, m: Message) -> Result:
    return await asyncio.to_thread(_smtp_deliver, config, secrets, target, m)


@dataclass(frozen=True)
class Provider:
    kind: str
    label: str
    help: str
    config_fields: tuple[dict, ...]
    secret_fields: tuple[dict, ...]
    needs_target: bool
    target_label: str
    send: object
    test: object
    webhook_suffixes: tuple[str, ...] = ()


def _f(name, label, type_="text", required=True, default=None, placeholder=""):
    return {"name": name, "label": label, "type": type_, "required": required, "default": default, "placeholder": placeholder}


PROVIDERS: dict[str, Provider] = {
    "slack_app": Provider(
        "slack_app", "Slack (conectado)", "Conecte o Slack com um clique e escolha o canal e o nome do robô.",
        (_f("bot_name", "Nome do robô", default="obskitpy", placeholder="obskitpy"),
         _f("icon_emoji", "Ícone do robô (emoji)", required=False, placeholder=":eyes:"),
         _f("team_id", "", "hidden", required=False), _f("team_name", "", "hidden", required=False), _f("bot_user_id", "", "hidden", required=False)),
        (_f("bot_token", "Token do robô", "password", required=False),),
        True, "Canal", slack_app_send, slack_app_test),
    "telegram": Provider(
        "telegram", "Telegram", "Crie um bot no @BotFather, copie o token e use o ID do chat (o bot só escreve para quem já falou com ele).",
        (), (_f("bot_token", "Token do bot", "password", placeholder="123456:ABC-DEF..."),),
        True, "ID do chat", telegram_send, telegram_test),
    "teams": Provider(
        "teams", "Microsoft Teams", "Use um Workflow do Teams (\"Postar em um canal quando uma solicitação de webhook for recebida\") e cole a URL gerada.",
        (), (_f("webhook_url", "URL do webhook (Workflows)", "password", placeholder="https://prod-00.westus.logic.azure.com/..."),),
        False, "", teams_send, lambda c, s, t, m: teams_send(c, s, t or "", m),
        (".logic.azure.com", ".webhook.office.com", ".powerplatform.com", ".azure.com")),
    "slack": Provider(
        "slack", "Slack", "Crie um Incoming Webhook no seu app do Slack e cole a URL. O canal é o que foi escolhido ao criar o webhook.",
        (), (_f("webhook_url", "URL do Incoming Webhook", "password", placeholder="https://hooks.slack.com/services/..."),),
        False, "", slack_send, lambda c, s, t, m: slack_send(c, s, t or "", m), ("hooks.slack.com",)),
    "email": Provider(
        "email", "E-mail (SMTP)", "Qualquer servidor SMTP. No Gmail, use smtp.gmail.com:587 com uma senha de app (a conexão por OAuth chega na próxima fase).",
        (_f("host", "Servidor SMTP", placeholder="smtp.gmail.com"), _f("port", "Porta", "number", default=587),
         _f("starttls", "Usar STARTTLS", "checkbox", required=False, default=True), _f("username", "Usuário", required=False),
         _f("from_addr", "Remetente (From)", placeholder="alertas@empresa.com")),
        (_f("password", "Senha", "password", required=False),),
        True, "E-mail do destinatário", email_send, email_test),
}


def catalog() -> list[dict]:
    return [{"kind": p.kind, "label": p.label, "help": p.help, "config_fields": [f for f in p.config_fields if f["type"] != "hidden"],
             "secret_fields": list(p.secret_fields), "needs_target": p.needs_target, "target_label": p.target_label,
             "oauth": "slack" if p.kind == "slack_app" else None}
            for p in PROVIDERS.values()]


def validate(kind: str, config: dict, secrets: dict, allow_private: bool, partial_secrets: bool = False) -> tuple[dict, dict]:
    """Valida e normaliza (config, secrets). Levanta ValueError com mensagem clara em português."""
    provider = PROVIDERS.get(kind)
    if not provider:
        raise ValueError(f"provedor desconhecido: {kind!r} (use: {', '.join(PROVIDERS)})")
    clean_cfg, clean_sec = {}, {}
    for spec in provider.config_fields:
        value = config.get(spec["name"], spec["default"])
        if spec["required"] and value in (None, ""):
            raise ValueError(f"o campo {spec['label']!r} é obrigatório")
        if value not in (None, ""):
            clean_cfg[spec["name"]] = value
    for spec in provider.secret_fields:
        value = secrets.get(spec["name"])
        if spec["required"] and not value and not partial_secrets:
            raise ValueError(f"o campo {spec['label']!r} é obrigatório")
        if value:
            clean_sec[spec["name"]] = str(value)
    if provider.webhook_suffixes and clean_sec.get("webhook_url"):
        check_webhook(clean_sec["webhook_url"], provider.webhook_suffixes, allow_private)
    if kind == "email":
        if clean_cfg.get("from_addr") and not EMAIL_RE.match(str(clean_cfg["from_addr"])):
            raise ValueError("o remetente (From) não parece um e-mail válido")
        if clean_cfg.get("host"):
            assert_public_host(str(clean_cfg["host"]), allow_private)
        if clean_cfg.get("username") and not clean_sec.get("password") and not partial_secrets:
            raise ValueError("informe a senha do usuário SMTP")
    if kind == "slack_app":
        name = str(clean_cfg.get("bot_name", ""))
        if len(name) > 80:
            raise ValueError("o nome do robô pode ter no máximo 80 caracteres")
        if clean_cfg.get("icon_emoji") and not re.fullmatch(r":[\w+\-]{1,50}:", str(clean_cfg["icon_emoji"])):
            raise ValueError("o ícone precisa ser um emoji do Slack no formato :nome:")
    if kind == "telegram" and clean_sec.get("bot_token") and not re.fullmatch(r"\d+:[\w-]{20,}", clean_sec["bot_token"]):
        raise ValueError("o token do Telegram parece inválido (formato esperado: 123456:ABC...)")
    return clean_cfg, clean_sec


def validate_target(kind: str, target: str) -> str:
    provider = PROVIDERS[kind]
    target = (target or "").strip()
    if provider.needs_target and not target:
        raise ValueError(f"informe o {provider.target_label.lower()}")
    if kind == "slack_app" and not re.fullmatch(r"[CGD][A-Z0-9]{6,}", target):
        raise ValueError("escolha um canal da lista")
    if kind == "email" and not EMAIL_RE.match(target):
        raise ValueError("o destino precisa ser um e-mail válido")
    if kind == "telegram" and not re.fullmatch(r"-?\d+|@[\w]{4,}", target):
        raise ValueError("o ID do chat do Telegram é um número (ou @canal)")
    return target
