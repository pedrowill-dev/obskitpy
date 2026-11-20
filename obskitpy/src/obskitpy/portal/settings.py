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
    )
