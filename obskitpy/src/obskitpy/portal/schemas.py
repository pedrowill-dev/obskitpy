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


_ID = r"^[A-Za-z0-9_-]{1,40}$"


class NodeIn(BaseModel):
    id: str = Field(pattern=_ID)
    type: str = Field(max_length=40)
    x: float = Field(default=0, ge=-100000, le=100000)
    y: float = Field(default=0, ge=-100000, le=100000)
    config: dict = Field(default_factory=dict)


class EdgeIn(BaseModel):
    source: str = Field(pattern=_ID)
    port: str = Field(default="next", max_length=20)
    target: str = Field(pattern=_ID)


class GraphIn(BaseModel):
    nodes: list[NodeIn] = Field(default_factory=list, max_length=60)
    edges: list[EdgeIn] = Field(default_factory=list, max_length=150)


class AutomationIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    enabled: bool = True
    graph: GraphIn


class AutomationOut(BaseModel):
    id: int
    name: str
    enabled: bool
    graph: dict
    state: dict
    updated_at: datetime


class RunOut(BaseModel):
    id: int
    automation_id: int | None
    automation_name: str
    trigger: str
    event: str
    value: int | None
    ok: bool
    steps: list
    at: datetime


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
