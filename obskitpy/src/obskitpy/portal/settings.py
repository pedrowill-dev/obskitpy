"""Configuração do portal, lida de variáveis de ambiente (arquivo observability/.portal.env no compose)."""
import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    database_url: str
    secret_key: str
    session_ttl_seconds: int
    cookie_secure: bool
    allow_private_webhooks: bool
    admin_email: str
    admin_password: str
    public_origin: str = ""        # se o portal tem endereço HTTPS público, o OAuth volta direto para ele
    oauth_relay_url: str = "https://pedrowill-dev.github.io/obskitpy/oauth/slack-callback.html"
    es_url: str = ""               # Elasticsearch para avaliar os alertas (vazio = avaliador desligado)
    eval_interval: int = 30
    ai_key: str = ""               # chave do Gemini; vazio = sem explicação por IA
    ai_model: str = "gemini-3.5-flash-lite"
    ai_daily_cap: int = 30         # teto de chamadas à IA por dia


def load() -> Settings:
    secret = os.environ.get("OBSKITPY_SECRET_KEY", "")
    if len(secret) < 32:
        raise RuntimeError("OBSKITPY_SECRET_KEY ausente ou curta demais (mínimo 32 caracteres). Rode `obskitpy portal enable`.")
    return Settings(
        database_url=os.environ.get("DATABASE_URL", "sqlite:///./portal.db"),
        secret_key=secret,
        session_ttl_seconds=int(os.environ.get("OBSKITPY_SESSION_TTL", "28800")),
        cookie_secure=os.environ.get("OBSKITPY_COOKIE_SECURE", "0") == "1",
        allow_private_webhooks=os.environ.get("OBSKITPY_ALLOW_PRIVATE_WEBHOOKS", "0") == "1",
        admin_email=os.environ.get("OBSKITPY_ADMIN_EMAIL", ""),
        admin_password=os.environ.get("OBSKITPY_ADMIN_PASSWORD", ""),
        public_origin=os.environ.get("OBSKITPY_PUBLIC_ORIGIN", "").rstrip("/"),
        oauth_relay_url=os.environ.get("OBSKITPY_OAUTH_RELAY", "https://pedrowill-dev.github.io/obskitpy/oauth/slack-callback.html"),
        es_url=os.environ.get("OBSKITPY_ES_URL", "").rstrip("/"),
        eval_interval=int(os.environ.get("OBSKITPY_EVAL_INTERVAL", "30")),
        ai_key=os.environ.get("OBSKITPY_GEMINI_KEY", ""),
        ai_model=os.environ.get("OBSKITPY_AI_MODEL", "gemini-3.5-flash-lite"),
        ai_daily_cap=int(os.environ.get("OBSKITPY_AI_DAILY_CAP", "30")),
    )
