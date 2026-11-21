"""Modelo de dados da Fase 1: usuários, conexões com provedores, destinos, contatos, grupos e auditoria."""
from datetime import datetime, timezone

from sqlalchemy import JSON, Boolean, Column, DateTime, ForeignKey, String, Table, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


def now() -> datetime:
    return datetime.now(timezone.utc)


group_members = Table(
    "contact_group_members", Base.metadata,
    Column("group_id", ForeignKey("contact_groups.id", ondelete="CASCADE"), primary_key=True),
    Column("contact_id", ForeignKey("contacts.id", ondelete="CASCADE"), primary_key=True),
)
contact_destinations = Table(
    "contact_destinations", Base.metadata,
    Column("contact_id", ForeignKey("contacts.id", ondelete="CASCADE"), primary_key=True),
    Column("destination_id", ForeignKey("notification_destinations.id", ondelete="CASCADE"), primary_key=True),
)


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(120), default="")
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(20), default="viewer")  # viewer | operator | admin
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class ProviderConnection(Base):
    """Autorização usada para enviar mensagens (uma conta de bot, um webhook, um servidor SMTP)."""
    __tablename__ = "provider_connections"
    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(30))
    name: Mapped[str] = mapped_column(String(120), unique=True)
    config: Mapped[dict] = mapped_column(JSON, default=dict)          # campos não secretos
    secrets_enc: Mapped[str] = mapped_column(Text, default="")        # segredos criptografados (nunca saem pela API)
    status: Mapped[str] = mapped_column(String(20), default="untested")  # untested | ok | failed
    last_tested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str] = mapped_column(String(500), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    destinations: Mapped[list["Destination"]] = relationship(back_populates="connection", cascade="all, delete-orphan")


class Destination(Base):
    """Para onde a mensagem vai: um chat, um canal, um endereço de e-mail."""
    __tablename__ = "notification_destinations"
    id: Mapped[int] = mapped_column(primary_key=True)
    connection_id: Mapped[int] = mapped_column(ForeignKey("provider_connections.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(120))
    target: Mapped[str] = mapped_column(String(255), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    connection: Mapped[ProviderConnection] = relationship(back_populates="destinations")


class Contact(Base):
    """Pessoa que recebe notificações (o destinatário lógico, separado da conexão e do destino)."""
    __tablename__ = "contacts"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    email: Mapped[str] = mapped_column(String(255), default="")
    destinations: Mapped[list[Destination]] = relationship(secondary=contact_destinations)


class ContactGroup(Base):
    __tablename__ = "contact_groups"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120), unique=True)
    members: Mapped[list[Contact]] = relationship(secondary=group_members)


class OAuthApp(Base):
    """Credenciais do app do provedor (ex.: o app do Slack que o usuário criou) usadas no \"Conectar com...\"."""
    __tablename__ = "oauth_apps"
    provider: Mapped[str] = mapped_column(String(30), primary_key=True)
    client_id: Mapped[str] = mapped_column(String(120))
    client_secret_enc: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Automation(Base):
    """Fluxo de automação: um grafo de etapas (gatilhos, condições e ações) desenhado no editor do dash."""
    __tablename__ = "automations"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120), unique=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    graph: Mapped[dict] = mapped_column(JSON, default=lambda: {"nodes": [], "edges": []})   # {"nodes": [...], "edges": [...]}
    state: Mapped[dict] = mapped_column(JSON, default=dict)                                   # estado de cada gatilho (ok/firing, último valor, erro)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)


class AutomationRun(Base):
    """Uma execução do fluxo: o que disparou e o resultado de cada etapa."""
    __tablename__ = "automation_runs"
    id: Mapped[int] = mapped_column(primary_key=True)
    automation_id: Mapped[int | None] = mapped_column(ForeignKey("automations.id", ondelete="SET NULL"), nullable=True)
    automation_name: Mapped[str] = mapped_column(String(120))
    trigger: Mapped[str] = mapped_column(String(60), default="")
    event: Mapped[str] = mapped_column(String(20), default="")      # disparou | lembrete | normalizou | agendado | teste
    value: Mapped[int | None] = mapped_column(nullable=True)
    ok: Mapped[bool] = mapped_column(Boolean, default=True)
    steps: Mapped[list] = mapped_column(JSON, default=list)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, index=True)


class AuditEvent(Base):
    __tablename__ = "audit_events"
    id: Mapped[int] = mapped_column(primary_key=True)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, index=True)
    actor: Mapped[str] = mapped_column(String(255), default="")
    action: Mapped[str] = mapped_column(String(60))
    entity: Mapped[str] = mapped_column(String(60), default="")
    entity_id: Mapped[str] = mapped_column(String(60), default="")
    detail: Mapped[dict] = mapped_column(JSON, default=dict)


class AiExplanation(Base):
    """Cache das explicações da IA por assinatura de erro (vazio = tentativa que falhou, vale por pouco tempo)."""
    __tablename__ = "ai_explanations"
    signature: Mapped[str] = mapped_column(String(40), primary_key=True)
    text: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, index=True)
