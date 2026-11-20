"""Automações: fluxos (grafos de nós) que reagem aos logs, traces e métricas do Elasticsearch.

Um fluxo tem **gatilhos** (o que observar), **condições** (decidir o caminho) e **ações** (avisar um destino).
O avaliador roda a cada ciclo, mede os gatilhos e, quando algo acontece, percorre o fluxo a partir do gatilho.
"""
import logging
import re
from datetime import datetime, timedelta

import httpx
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from . import providers
from .models import Automation, AutomationRun, Destination, now

log = logging.getLogger("obskitpy.automations")

MAX_NODES, MAX_EDGES = 60, 150
SEVERITIES = ("info", "warning", "critical")
SERVICE_FIELD = "resource.attributes.service.name"
VARIABLES = ("service", "value", "window", "automation", "event", "time")


def _f(name, label, type_="text", default=None, **extra):
    return {"name": name, "label": label, "type": type_, "default": default, **extra}


_SERVICE = _f("service", "Serviço (automação)", "service", "", help="Vazio = todos os serviços")
_WINDOW = _f("window_minutes", "Janela (minutos)", "number", 5, min=1, max=10080)
_REPEAT = _f("repeat_minutes", "Relembrar a cada (min, 0 = não)", "number", 30, min=0, max=10080)
_NORMAL = ("fired", "Disparou"), ("resolved", "Normalizou")

# tipo -> definição. A tela do editor é montada a partir disto (campos, portas, textos).
NODE_TYPES: dict[str, dict] = {
    "trigger.error_logs": {"group": "Gatilhos", "label": "Erros nos logs", "help": "Dispara quando houver logs ERROR/CRITICAL acima do limite na janela.",
                           "outputs": _NORMAL, "fields": [_SERVICE, _f("threshold", "Limite (erros)", "number", 1, min=1, max=1_000_000), _WINDOW, _REPEAT]},
    "trigger.failed_spans": {"group": "Gatilhos", "label": "Etapas com falha", "help": "Dispara quando etapas (traces) terminarem com falha acima do limite.",
                             "outputs": _NORMAL, "fields": [_SERVICE, _f("threshold", "Limite (falhas)", "number", 1, min=1, max=1_000_000), _WINDOW, _REPEAT]},
    "trigger.no_data": {"group": "Gatilhos", "label": "Automação parada", "help": "Dispara quando a automação não envia nenhum dado na janela (parou, travou ou não rodou).",
                        "outputs": _NORMAL, "fields": [_SERVICE, _WINDOW, _REPEAT]},
    "trigger.schedule": {"group": "Gatilhos", "label": "A cada intervalo", "help": "Executa o fluxo de tempos em tempos (bom para resumos). O valor é a quantidade de erros no intervalo.",
                         "outputs": (("fired", "Executa"),), "fields": [_SERVICE, _f("every_minutes", "A cada (minutos)", "number", 60, min=1, max=10080)]},
    "logic.if": {"group": "Lógica", "label": "Condição", "help": "Escolhe o caminho: Sim ou Não, conforme o valor do gatilho.", "inputs": True,
                 "outputs": (("yes", "Sim"), ("no", "Não")),
                 "fields": [_f("field", "Se", "select", "value", options=[["value", "o valor (quantidade)"], ["window", "a janela (min)"], ["service", "o nome do serviço"]]),
                            _f("operator", "for", "select", ">=", options=[[">=", "maior ou igual a"], [">", "maior que"], ["<=", "menor ou igual a"], ["<", "menor que"],
                                                                         ["==", "igual a"], ["!=", "diferente de"], ["contains", "contém"]]),
                            _f("value", "este valor", "text", "10", max=100)]},
    "action.notify": {"group": "Ações", "label": "Enviar aviso", "help": "Manda uma mensagem para um destino (canal do Slack, chat do Telegram, e-mail…).", "inputs": True,
                      "outputs": (("next", "Depois"),),
                      "fields": [_f("destination_id", "Destino", "destination", None, required=True),
                                 _f("severity", "Gravidade", "select", "warning", options=[["info", "Informativo"], ["warning", "Atenção"], ["critical", "Crítico"]]),
                                 _f("title", "Título", "text", "{{automation}}", max=120),
                                 _f("message", "Mensagem", "textarea", "{{event}}: {{value}} em {{window}} min — {{service}}", max=800)]},
}
for _t in NODE_TYPES.values():
    _t.setdefault("inputs", False)


def catalog() -> list[dict]:
    return [{"type": k, "group": v["group"], "label": v["label"], "help": v["help"], "inputs": v["inputs"],
             "outputs": [{"port": p, "label": lbl} for p, lbl in v["outputs"]], "fields": v["fields"]} for k, v in NODE_TYPES.items()]


# ---------- validação do grafo ----------

def _clean_config(db: Session, ntype: str, label: str, raw: dict) -> dict:
    out = {}
    for f in NODE_TYPES[ntype]["fields"]:
        val, name = raw.get(f["name"], f["default"]), f["name"]
        if f["type"] == "number":
            try:
                val = int(val)
            except (TypeError, ValueError):
                raise ValueError(f"“{label}”: o campo “{f['label']}” precisa ser um número")
            if not f["min"] <= val <= f["max"]:
                raise ValueError(f"“{label}”: “{f['label']}” precisa estar entre {f['min']} e {f['max']}")
        elif f["type"] == "select":
            if val not in [o[0] for o in f["options"]]:
                raise ValueError(f"“{label}”: valor inválido em “{f['label']}”")
        elif f["type"] == "destination":
            if val in (None, ""):
                raise ValueError(f"“{label}”: escolha o destino (conecte um canal em Integrações)")
            try:
                val = int(val)
            except (TypeError, ValueError):
                raise ValueError(f"“{label}”: destino inválido")
            if not db.get(Destination, val):
                raise ValueError(f"“{label}”: o destino escolhido não existe mais")
        else:
            val = str(val if val is not None else "").strip()[: f.get("max", 120 if f["type"] != "textarea" else 800)]
            if name == "value" and ntype == "logic.if" and not val:
                raise ValueError(f"“{label}”: informe o valor da condição")
        out[name] = val
    return out


def validate_graph(db: Session, graph: dict) -> dict:
    """Confere o fluxo e devolve a versão limpa. Levanta ValueError com a explicação em português."""
    nodes, edges = graph.get("nodes", []), graph.get("edges", [])
    if len(nodes) > MAX_NODES or len(edges) > MAX_EDGES:
        raise ValueError(f"o fluxo pode ter no máximo {MAX_NODES} etapas e {MAX_EDGES} ligações")
    by_id, clean_nodes = {}, []
    for n in nodes:
        if n["type"] not in NODE_TYPES:
            raise ValueError(f"tipo de etapa desconhecido: {n['type']}")
        if n["id"] in by_id:
            raise ValueError("há duas etapas com o mesmo identificador")
        label = NODE_TYPES[n["type"]]["label"]
        by_id[n["id"]] = n["type"]
        clean_nodes.append({"id": n["id"], "type": n["type"], "x": round(float(n["x"]), 1), "y": round(float(n["y"]), 1),
                            "config": _clean_config(db, n["type"], label, n.get("config") or {})})
    if not any(t.startswith("trigger.") for t in by_id.values()):
        raise ValueError("o fluxo precisa de pelo menos um gatilho (o que observar)")
    seen, clean_edges, adj = set(), [], {i: [] for i in by_id}
    for e in edges:
        s, t, port = e["source"], e["target"], e["port"]
        if s not in by_id or t not in by_id:
            raise ValueError("há uma ligação apontando para uma etapa que não existe")
        if port not in [p for p, _ in NODE_TYPES[by_id[s]]["outputs"]]:
            raise ValueError(f"“{NODE_TYPES[by_id[s]]['label']}” não tem a saída “{port}”")
        if not NODE_TYPES[by_id[t]]["inputs"]:
            raise ValueError(f"nada pode ser ligado a “{NODE_TYPES[by_id[t]]['label']}” (gatilhos só começam o fluxo)")
        if s == t:
            raise ValueError("uma etapa não pode ligar nela mesma")
        if (s, port, t) in seen:
            continue
        seen.add((s, port, t))
        adj[s].append(t)
        clean_edges.append({"source": s, "port": port, "target": t})
    state = {}  # detecta ciclos (DFS com cores)

    def visit(n):
        state[n] = 1
        for m in adj[n]:
            if state.get(m) == 1 or (m not in state and visit(m)):
                return True
        state[n] = 2
        return False
    if any(i not in state and visit(i) for i in adj):
        raise ValueError("o fluxo não pode voltar para uma etapa anterior (ciclo)")
    return {"nodes": clean_nodes, "edges": clean_edges}


# ---------- medição no Elasticsearch ----------

def _query(service: str, window: int, extra: list[dict]) -> dict:
    filters = [{"range": {"@timestamp": {"gte": f"now-{window}m"}}}, *extra]
    if service:
        filters.append({"term": {SERVICE_FIELD: service}})
    return {"query": {"bool": {"filter": filters}}}


async def _count(client: httpx.AsyncClient, es_url: str, index: str, body: dict) -> int:
    resp = await client.post(f"{es_url}/{index}/_count", json=body, params={"ignore_unavailable": "true"})
    if resp.status_code == 404:
        return 0  # o data stream só existe depois do primeiro dado
    resp.raise_for_status()
    return int(resp.json()["count"])


async def measure(es_url: str, ntype: str, config: dict) -> int:
    """Valor atual de um gatilho (para `no_data`, o total de documentos recebidos na janela)."""
    service = config.get("service", "")
    window = config.get("every_minutes") if ntype == "trigger.schedule" else config.get("window_minutes", 5)
    async with httpx.AsyncClient(timeout=10) as client:
        if ntype in ("trigger.error_logs", "trigger.schedule"):
            levels = {"terms": {"severity_text": ["ERROR", "CRITICAL", "FATAL"]}}
            return await _count(client, es_url, "logs-generic.otel-*", _query(service, window, [levels]))
        if ntype == "trigger.failed_spans":
            return await _count(client, es_url, "traces-generic.otel-*", _query(service, window, [{"term": {"status.code": "Error"}}]))
        return (await _count(client, es_url, "logs-generic.otel-*", _query(service, window, []))
                + await _count(client, es_url, "traces-generic.otel-*", _query(service, window, [])))


def breached(ntype: str, config: dict, value: int) -> bool:
    return value == 0 if ntype == "trigger.no_data" else value >= config.get("threshold", 1)


# ---------- execução do fluxo ----------

def render(template: str, ctx: dict) -> str:
    """Troca {{variavel}} pelo valor. Só as variáveis conhecidas: nada é executado."""
    return re.sub(r"\{\{\s*(\w+)\s*\}\}", lambda m: str(ctx.get(m.group(1), m.group(0))) if m.group(1) in VARIABLES else m.group(0), template)


def _compare(config: dict, ctx: dict) -> bool:
    left, op, right = ctx.get(config["field"]), config["operator"], config["value"]
    if config["field"] == "service":
        left = str(left or "")
        return {"==": left == right, "!=": left != right, "contains": right.lower() in left.lower()}.get(op, False)
    try:
        r = float(right.replace(",", "."))
    except ValueError:
        return False
    left = float(left or 0)
    return {">=": left >= r, ">": left > r, "<=": left <= r, "<": left < r, "==": left == r, "!=": left != r}.get(op, False)


async def send_to(db: Session, secret_key: str, dest_id: int, message: providers.Message) -> tuple[bool, str]:
    from .security import decrypt_secrets
    dest = db.get(Destination, dest_id)
    if not dest:
        return False, "o destino foi removido"
    conn = dest.connection
    secrets = decrypt_secrets(secret_key, conn.secrets_enc) if conn.secrets_enc else {}
    try:
        result = await providers.PROVIDERS[conn.kind].send(conn.config, secrets, dest.target, message)
    except Exception as exc:  # um destino com problema não pode derrubar o resto do fluxo
        return False, f"falha ao enviar ({type(exc).__name__})"
    return result.ok, (f"enviado para {dest.name}" if result.ok else f"{dest.name}: {result.detail}")


async def run_flow(db: Session, secret_key: str, graph: dict, trigger_id: str, port: str, ctx: dict, send_fn=send_to, test: bool = False) -> list[dict]:
    """Percorre o fluxo a partir da saída `port` do gatilho. Cada etapa visitada vira um registro em `steps`."""
    nodes = {n["id"]: n for n in graph["nodes"]}
    out: dict[tuple[str, str], list[str]] = {}
    for e in graph["edges"]:
        out.setdefault((e["source"], e["port"]), []).append(e["target"])
    steps, visited, queue = [], set(), list(out.get((trigger_id, port), []))
    while queue:
        nid = queue.pop(0)
        if nid in visited:
            continue
        visited.add(nid)
        node = nodes[nid]
        label = NODE_TYPES[node["type"]]["label"]
        cfg = node["config"]
        if node["type"] == "logic.if":
            yes = _compare(cfg, ctx)
            steps.append({"node": nid, "label": label, "ok": True, "detail": "Sim" if yes else "Não"})
            queue += out.get((nid, "yes" if yes else "no"), [])
        elif node["type"] == "action.notify":
            title = render(cfg["title"] or "{{automation}}", ctx)
            msg = providers.Message(("[Teste] " if test else "") + title, render(cfg["message"], ctx), cfg["severity"],
                                    [("Automação", ctx["automation"]), ("Serviço", ctx["service"] or "todos")])
            ok, detail = await send_fn(db, secret_key, cfg["destination_id"], msg)
            steps.append({"node": nid, "label": label, "ok": ok, "detail": detail})
            queue += out.get((nid, "next"), [])
    return steps


def _context(auto: Automation, node: dict, value: int, event: str) -> dict:
    cfg = node["config"]
    window = cfg.get("every_minutes") or cfg.get("window_minutes") or 0
    return {"service": cfg.get("service", ""), "value": value, "window": window, "automation": auto.name, "event": event,
            "time": now().astimezone().strftime("%d/%m %H:%M")}


def _record(db: Session, auto: Automation, node: dict, event: str, value: int | None, steps: list[dict]) -> None:
    db.add(AutomationRun(automation_id=auto.id, automation_name=auto.name, trigger=NODE_TYPES[node["type"]]["label"], event=event, value=value,
                         ok=all(s["ok"] for s in steps), steps=steps))


def _aware(dt: datetime | None) -> datetime:
    dt = dt or now()
    return dt if dt.tzinfo else dt.replace(tzinfo=now().tzinfo)


async def evaluate(db: Session, secret_key: str, es_url: str, auto: Automation, measure_fn=measure, send_fn=send_to) -> list[str]:
    """Avalia os gatilhos da automação. Devolve os eventos que aconteceram (para logs e testes)."""
    state, events = {k: dict(v) for k, v in (auto.state or {}).items()}, []
    for node in [n for n in auto.graph["nodes"] if n["type"].startswith("trigger.")]:
        nid, cfg, st = node["id"], node["config"], state.setdefault(node["id"], {"state": "ok"})
        stamp = now()
        if node["type"] == "trigger.schedule":
            if not st.get("last_run"):
                st["last_run"] = stamp.isoformat()  # o primeiro disparo vem depois de um intervalo completo
                continue
            if stamp - datetime.fromisoformat(st["last_run"]) < timedelta(minutes=cfg["every_minutes"]):
                continue
        try:
            value = await measure_fn(es_url, node["type"], cfg)
        except Exception as exc:
            st["error"], st["eval_at"] = f"não consegui consultar o Elasticsearch ({type(exc).__name__})", stamp.isoformat()
            continue
        st.update(error="", value=value, eval_at=stamp.isoformat())
        if node["type"] == "trigger.schedule":
            st["last_run"] = stamp.isoformat()
            steps = await run_flow(db, secret_key, auto.graph, nid, "fired", _context(auto, node, value, "agendado"), send_fn)
            _record(db, auto, node, "agendado", value, steps)
            events.append("agendado")
        elif breached(node["type"], cfg, value):
            repeat = cfg.get("repeat_minutes", 0)
            if st["state"] != "firing":
                event = "disparou"
            elif repeat and stamp - _aware(datetime.fromisoformat(st["notified_at"])) >= timedelta(minutes=repeat):
                event = "lembrete"
            else:
                continue
            steps = await run_flow(db, secret_key, auto.graph, nid, "fired", _context(auto, node, value, event), send_fn)
            _record(db, auto, node, event, value, steps)
            st.update(state="firing", notified_at=stamp.isoformat())
            events.append(event)
        elif st["state"] == "firing":
            steps = await run_flow(db, secret_key, auto.graph, nid, "resolved", _context(auto, node, value, "normalizou"), send_fn)
            _record(db, auto, node, "normalizou", value, steps)
            st["state"] = "ok"
            events.append("normalizou")
    auto.state = state
    db.commit()
    return events


async def run_forever(sessions, secret_key: str, es_url: str, interval: int) -> None:
    """Laço do avaliador: roda enquanto o portal estiver no ar."""
    import asyncio
    while True:
        try:
            with sessions() as db:
                for auto in db.scalars(select(Automation).where(Automation.enabled.is_(True))).all():
                    await evaluate(db, secret_key, es_url, auto)
                if db.scalar(select(func.count(AutomationRun.id))) > 1200:  # histórico limitado
                    keep = db.scalars(select(AutomationRun.id).order_by(AutomationRun.id.desc()).limit(1000)).all()
                    db.execute(delete(AutomationRun).where(AutomationRun.id < min(keep)))
                    db.commit()
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("falha ao avaliar as automações")
        await asyncio.sleep(interval)
