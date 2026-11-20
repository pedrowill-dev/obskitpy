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


def _telegram_text(m: Message) -> str:
    lines = [f"{ICON.get(m.severity, '🔵')} <b>{html.escape(m.title)}</b>", html.escape(m.text)]
    lines += [f"<b>{html.escape(k)}:</b> {html.escape(v)}" for k, v in m.fields]
    return "\n".join(lines)


def _slack_payload(m: Message) -> dict:
    lines = [f"{ICON.get(m.severity, '🔵')} *{m.title}*", m.text] + [f"*{k}:* {v}" for k, v in m.fields]
    return {"text": "\n".join(lines)}


def _teams_payload(m: Message) -> dict:
    color = {"critical": "Attention", "warning": "Warning"}.get(m.severity, "Accent")
    body = [{"type": "TextBlock", "text": m.title, "weight": "Bolder", "size": "Medium", "color": color, "wrap": True},
            {"type": "TextBlock", "text": m.text, "wrap": True}]
    if m.fields:
        body.append({"type": "FactSet", "facts": [{"title": k, "value": v} for k, v in m.fields]})
    return {"type": "message", "attachments": [{"contentType": "application/vnd.microsoft.card.adaptive", "content": {
        "$schema": "http://adaptivecards.io/schemas/adaptive-card.json", "type": "AdaptiveCard", "version": "1.4", "body": body}}]}


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
            msg["Subject"], msg["From"], msg["To"] = f"[obskitpy] {m.title}", config["from_addr"], to
            msg.set_content("\n".join([m.text] + [f"{k}: {v}" for k, v in m.fields]))
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
    return [{"kind": p.kind, "label": p.label, "help": p.help, "config_fields": list(p.config_fields),
             "secret_fields": list(p.secret_fields), "needs_target": p.needs_target, "target_label": p.target_label}
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
    if kind == "telegram" and clean_sec.get("bot_token") and not re.fullmatch(r"\d+:[\w-]{20,}", clean_sec["bot_token"]):
        raise ValueError("o token do Telegram parece inválido (formato esperado: 123456:ABC...)")
    return clean_cfg, clean_sec


def validate_target(kind: str, target: str) -> str:
    provider = PROVIDERS[kind]
    target = (target or "").strip()
    if provider.needs_target and not target:
        raise ValueError(f"informe o {provider.target_label.lower()}")
    if kind == "email" and not EMAIL_RE.match(target):
        raise ValueError("o destino precisa ser um e-mail válido")
    if kind == "telegram" and not re.fullmatch(r"-?\d+|@[\w]{4,}", target):
        raise ValueError("o ID do chat do Telegram é um número (ou @canal)")
    return target
