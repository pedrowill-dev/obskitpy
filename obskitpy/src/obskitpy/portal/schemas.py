"""Contratos da API (Pydantic)."""
from datetime import datetime

from pydantic import BaseModel, Field


class LoginIn(BaseModel):
    email: str
    password: str


class UserOut(BaseModel):
    id: int
    email: str
    name: str
    role: str
    active: bool


class UserIn(BaseModel):
    email: str = Field(min_length=3, max_length=255)
    name: str = Field(default="", max_length=120)
    password: str = Field(min_length=10, max_length=200)
    role: str = Field(default="viewer", pattern="^(viewer|operator|admin)$")


class IntegrationIn(BaseModel):
    kind: str
    name: str = Field(min_length=1, max_length=120)
    config: dict = Field(default_factory=dict)
    secrets: dict = Field(default_factory=dict)


class IntegrationUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    config: dict | None = None
    secrets: dict | None = None


class IntegrationOut(BaseModel):
    id: int
    kind: str
    label: str
    name: str
    config: dict
    stored_secrets: list[str]  # só os NOMES dos segredos guardados; os valores nunca são devolvidos
    status: str
    last_tested_at: datetime | None
    last_error: str
    destinations: int


class TestIn(BaseModel):
    destination_id: int | None = None


class TestOut(BaseModel):
    ok: bool
    detail: str
    retry_after: int | None = None


class DestinationIn(BaseModel):
    connection_id: int
    name: str = Field(min_length=1, max_length=120)
    target: str = Field(default="", max_length=255)


class DestinationOut(BaseModel):
    id: int
    connection_id: int
    connection: str
    kind: str
    name: str
    target: str


class ContactIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    email: str = Field(default="", max_length=255)
    destination_ids: list[int] = Field(default_factory=list)


class ContactOut(BaseModel):
    id: int
    name: str
    email: str
    destination_ids: list[int]


class GroupIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    member_ids: list[int] = Field(default_factory=list)


class GroupOut(BaseModel):
    id: int
    name: str
    member_ids: list[int]


class RuleIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    service: str = Field(default="", max_length=120)
    condition: str
    threshold: int = Field(default=1, ge=1, le=1_000_000)
    window_minutes: int = Field(default=5, ge=1, le=10_080)
    severity: str = "warning"
    destination_ids: list[int] = Field(min_length=1)
    cooldown_minutes: int = Field(default=30, ge=0, le=10_080)
    enabled: bool = True


class RuleOut(RuleIn):
    id: int
    state: str
    last_value: int | None
    last_eval_at: datetime | None
    last_error: str


class IncidentOut(BaseModel):
    id: int
    rule_name: str
    service: str
    severity: str
    value: int
    opened_at: datetime
    resolved_at: datetime | None
    notified: str


class SlackAppIn(BaseModel):
    client_id: str = Field(min_length=3, max_length=60)
    client_secret: str = Field(min_length=10, max_length=120)


class SlackStartIn(BaseModel):
    origin: str = Field(max_length=200)


class AuditOut(BaseModel):
    id: int
    at: datetime
    actor: str
    action: str
    entity: str
    entity_id: str
    detail: dict
