"""Robô que extrai as notícias de https://prefeitura.sp.gov.br/noticias, instrumentado com obskit.

Variáveis de ambiente:
  ELASTICSEARCH_URL       (padrão http://localhost:9200)
  DIAS                    quantos dias para trás extrair, contando hoje (padrão 7)
  RPA_INTERVAL_SECONDS    0 = executa uma vez e encerra (padrão); >0 repete a cada N segundos
"""
import logging
import os
import time
import uuid
from datetime import date, datetime, timedelta, timezone

import obskit
from opentelemetry.trace import Status, StatusCode

from . import client, parser
from .store import Store

log = logging.getLogger("noticias_sp")
tracer = obskit.tracer("noticias_sp")
_meter = obskit.meter("noticias_sp")
processed = _meter.create_counter("noticias.processadas", unit="{noticia}", description="Notícias por status")
duration = _meter.create_histogram("noticias.duracao", unit="ms", description="Duração por notícia")


def process_item(store: Store, item: dict, run_id: str) -> bool:
    started = time.perf_counter()
    status = "failure"
    with tracer.start_as_current_span("noticia.processar", attributes={"noticia.id": str(item.get("id"))}) as span:
        try:
            with tracer.start_as_current_span("noticia.parse"):
                doc = parser.parse(item)
            doc["run_id"] = run_id
            doc["extracted_at"] = datetime.now(timezone.utc).isoformat()
            doc["trace_id"] = format(span.get_span_context().trace_id, "032x")
            with tracer.start_as_current_span("noticia.salvar"):
                store.save(doc)
            status = "success"
        except Exception as exc:
            span.record_exception(exc)
            span.set_status(Status(StatusCode.ERROR, str(exc)))
            log.exception("falha ao processar notícia", extra={"noticia.id": str(item.get("id"))})
        finally:
            processed.add(1, {"status": status})
            duration.record((time.perf_counter() - started) * 1000, {"status": status})
    return status == "success"


def process_day(store: Store, day: date, run_id: str) -> tuple[int, int]:
    with tracer.start_as_current_span("dia.processar", attributes={"dia": str(day)}) as span:
        try:
            with tracer.start_as_current_span("dia.baixar"):
                items = client.fetch_day(day)
        except Exception as exc:
            span.record_exception(exc)
            span.set_status(Status(StatusCode.ERROR, str(exc)))
            log.exception("falha ao baixar o dia", extra={"dia": str(day)})
            return 0, 0
        ok = sum(process_item(store, item, run_id) for item in items)
        span.set_attribute("dia.noticias", len(items))
        log.info("dia processado", extra={"dia": str(day), "noticias": len(items), "salvas": ok})
        return ok, len(items) - ok


def run(store: Store, days: int) -> None:
    run_id = uuid.uuid4().hex[:12]
    with tracer.start_as_current_span("execucao", attributes={"run_id": run_id, "dias": days}) as span:
        today = datetime.now(timezone(timedelta(hours=-3))).date()  # fuso de São Paulo
        ok = failed = 0
        for offset in range(days):
            day_ok, day_failed = process_day(store, today - timedelta(days=offset), run_id)
            ok, failed = ok + day_ok, failed + day_failed
        span.set_attribute("noticias.salvas", ok)
        span.set_attribute("noticias.falhas", failed)
        log.info("execução concluída", extra={"run_id": run_id, "salvas": ok, "falhas": failed})


def main() -> None:
    shutdown = obskit.setup("noticias-sp")
    store = Store(os.getenv("ELASTICSEARCH_URL", "http://localhost:9200"))
    store.ensure_index()
    days = int(os.getenv("DIAS", "7"))
    interval = int(os.getenv("RPA_INTERVAL_SECONDS", "0"))
    try:
        while True:
            run(store, days)
            if interval <= 0:
                break
            time.sleep(interval)
    finally:
        shutdown()


if __name__ == "__main__":
    main()
