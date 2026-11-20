"""Alertas: avalia as regras contra o Elasticsearch e avisa os destinos (abre e resolve incidentes)."""
import asyncio
import logging
from datetime import datetime, timedelta

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from . import providers
from .models import AlertRule, Destination, Incident, now
from .security import decrypt_secrets

log = logging.getLogger("obskitpy.alerts")

SEVERITIES = ("info", "warning", "critical")
# condição -> (rótulo, descrição). Os índices consultados são os mesmos que o dash usa.
CONDITIONS = {
    "error_logs": ("Logs de erro", "quantidade de logs ERROR/CRITICAL na janela"),
    "failed_spans": ("Etapas com falha", "quantidade de etapas (traces) com status de erro na janela"),
    "no_data": ("Automação parada", "nenhum log nem trace recebido na janela"),
}
SERVICE_FIELD = "resource.attributes.service.name"


def _query(rule: AlertRule, extra: list[dict]) -> dict:
    filters = [{"range": {"@timestamp": {"gte": f"now-{rule.window_minutes}m"}}}, *extra]
    if rule.service:
        filters.append({"term": {SERVICE_FIELD: rule.service}})
    return {"query": {"bool": {"filter": filters}}}


async def _count(client: httpx.AsyncClient, es_url: str, index: str, body: dict) -> int:
    resp = await client.post(f"{es_url}/{index}/_count", json=body, params={"ignore_unavailable": "true"})
    if resp.status_code == 404:
        return 0  # o data stream só existe depois do primeiro dado
    resp.raise_for_status()
    return int(resp.json()["count"])


async def measure(es_url: str, rule: AlertRule) -> int:
    """Valor atual da condição da regra (para `no_data`, o total de documentos recebidos)."""
    async with httpx.AsyncClient(timeout=10) as client:
        if rule.condition == "error_logs":
            levels = {"terms": {"severity_text": ["ERROR", "CRITICAL", "FATAL"]}}
            return await _count(client, es_url, "logs-generic.otel-*", _query(rule, [levels]))
        if rule.condition == "failed_spans":
            return await _count(client, es_url, "traces-generic.otel-*", _query(rule, [{"term": {"status.code": "Error"}}]))
        logs = await _count(client, es_url, "logs-generic.otel-*", _query(rule, []))
        traces = await _count(client, es_url, "traces-generic.otel-*", _query(rule, []))
        return logs + traces


def breached(rule: AlertRule, value: int) -> bool:
    return value == 0 if rule.condition == "no_data" else value >= rule.threshold


def describe(rule: AlertRule, value: int) -> str:
    who = f"“{rule.service}”" if rule.service else "qualquer serviço"
    if rule.condition == "no_data":
        return f"{who} não enviou nenhum dado nos últimos {rule.window_minutes} min"
    label = CONDITIONS[rule.condition][0].lower()
    return f"{value} {label} em {rule.window_minutes} min em {who} (limite: {rule.threshold})"


async def deliver(db: Session, secret_key: str, rule: AlertRule, message: providers.Message) -> str:
    """Envia a mensagem para todos os destinos da regra. Devolve um resumo (ex.: "2 de 3 destinos")."""
    ok, problems = 0, []
    dests = [d for d in (db.get(Destination, i) for i in rule.destination_ids) if d]
    for dest in dests:
        conn = dest.connection
        secrets = decrypt_secrets(secret_key, conn.secrets_enc) if conn.secrets_enc else {}
        try:
            result = await providers.PROVIDERS[conn.kind].send(conn.config, secrets, dest.target, message)
        except Exception as exc:  # um destino com problema não pode impedir os outros
            result = providers.Result(False, f"{type(exc).__name__}")
        if result.ok:
            ok += 1
        else:
            problems.append(f"{dest.name}: {result.detail}")
    if not dests:
        return "nenhum destino válido"
    return f"{ok} de {len(dests)} destinos" + (f" ({'; '.join(problems)})"[:200] if problems else "")


def _message(rule: AlertRule, value: int, kind: str) -> providers.Message:
    fields = [("Regra", rule.name), ("Serviço", rule.service or "todos"), ("Janela", f"{rule.window_minutes} min")]
    if kind == "resolved":
        return providers.Message(f"✅ Resolvido: {rule.name}", "A condição voltou ao normal.", "info", fields)
    title = f"{'Lembrete' if kind == 'reminder' else 'Alerta'}: {rule.name}"
    return providers.Message(title, describe(rule, value), rule.severity, fields)


async def evaluate(db: Session, secret_key: str, es_url: str, rule: AlertRule, measure_fn=measure, deliver_fn=deliver) -> str:
    """Avalia uma regra e age: abre incidente + avisa, relembra após o cooldown, ou resolve. Devolve o que aconteceu."""
    try:
        value = await measure_fn(es_url, rule)
    except Exception as exc:
        rule.last_error, rule.last_eval_at = f"não consegui consultar o Elasticsearch ({type(exc).__name__})", now()
        db.commit()
        return "erro"
    rule.last_error, rule.last_value, rule.last_eval_at = "", value, now()
    action = "ok"
    if breached(rule, value):
        open_inc = db.scalar(select(Incident).where(Incident.rule_id == rule.id, Incident.resolved_at.is_(None)))
        if rule.state != "firing" or not open_inc:
            summary = await deliver_fn(db, secret_key, rule, _message(rule, value, "alert"))
            db.add(Incident(rule_id=rule.id, rule_name=rule.name, service=rule.service, severity=rule.severity, value=value, notified=summary))
            rule.state, rule.last_notified_at, action = "firing", now(), "alert"
        else:
            open_inc.value = max(open_inc.value, value)
            due = rule.cooldown_minutes and now() - _aware(rule.last_notified_at) >= timedelta(minutes=rule.cooldown_minutes)
            if due:
                open_inc.notified = await deliver_fn(db, secret_key, rule, _message(rule, value, "reminder"))
                rule.last_notified_at, action = now(), "reminder"
    elif rule.state == "firing":
        for inc in db.scalars(select(Incident).where(Incident.rule_id == rule.id, Incident.resolved_at.is_(None))):
            inc.resolved_at = now()
        await deliver_fn(db, secret_key, rule, _message(rule, value, "resolved"))
        rule.state, action = "ok", "resolved"
    db.commit()
    return action


def _aware(dt: datetime | None) -> datetime:
    dt = dt or now()
    return dt if dt.tzinfo else dt.replace(tzinfo=now().tzinfo)


async def run_forever(sessions, secret_key: str, es_url: str, interval: int) -> None:
    """Laço do avaliador: roda enquanto o portal estiver no ar."""
    while True:
        try:
            with sessions() as db:
                for rule in db.scalars(select(AlertRule).where(AlertRule.enabled.is_(True))).all():
                    await evaluate(db, secret_key, es_url, rule)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("falha ao avaliar os alertas")
        await asyncio.sleep(interval)
