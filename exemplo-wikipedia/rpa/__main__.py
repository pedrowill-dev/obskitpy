"""Robô (RPA) que extrai dados de artigos da Wikipedia, instrumentado com OpenTelemetry."""
import json
import logging
import os
import time
import uuid
from datetime import datetime, timezone

from opentelemetry.trace import Status, StatusCode

from . import scraper
from .error_chain import extract_error_chain
from .store import Store
from .telemetry import page_duration, pages_processed, setup_telemetry, tracer

log = logging.getLogger("rpa")

# O último tópico não existe de propósito: demonstra o rastreio de erros no dashboard.
DEFAULT_TOPICS = (
    "Python_(linguagem_de_programação),Brasil,Inteligência_artificial,Linux,Robótica,"
    "Pagina_inexistente_para_teste_de_erro"
)


def process_page(store: Store, lang: str, slug: str, run_id: str) -> bool:
    started = time.perf_counter()
    status = "failure"
    with tracer.start_as_current_span("rpa.process_page", attributes={"page.slug": slug}) as span:
        try:
            with tracer.start_as_current_span("rpa.fetch"):
                html = scraper.fetch(lang, slug)
            with tracer.start_as_current_span("rpa.parse") as parse_span:
                doc = scraper.parse(html, slug, lang)
                parse_span.set_attribute("page.word_count", doc["word_count"])
            doc["run_id"] = run_id
            doc["extracted_at"] = datetime.now(timezone.utc).isoformat()
            doc["trace_id"] = format(span.get_span_context().trace_id, "032x")
            with tracer.start_as_current_span("rpa.store"):
                store.save(doc)
            status = "success"
            log.info(
                "página extraída",
                extra={"page.slug": slug, "page.word_count": doc["word_count"]},
            )
        except Exception as exc:
            chain = extract_error_chain(exc)
            origin = f"{chain[-1]['arquivo']}:{chain[-1]['funcao']}:{chain[-1]['linha']}"
            span.record_exception(exc)
            span.set_status(Status(StatusCode.ERROR, str(exc)))
            log.error(
                "falha ao processar página",
                exc_info=True,
                extra={
                    "page.slug": slug,
                    "error.type": type(exc).__name__,
                    "error.origin": origin,
                    "error.chain": json.dumps(chain, ensure_ascii=False),
                },
            )
        finally:
            attrs = {"status": status}
            pages_processed.add(1, attrs)
            page_duration.record((time.perf_counter() - started) * 1000, attrs)
    return status == "success"


def run(store: Store, lang: str, topics: list[str]) -> None:
    run_id = uuid.uuid4().hex[:12]
    with tracer.start_as_current_span(
        "rpa.run", attributes={"rpa.run_id": run_id, "rpa.topics": len(topics)}
    ) as span:
        log.info("execução iniciada", extra={"rpa.run_id": run_id, "rpa.topics": len(topics)})
        results = [process_page(store, lang, slug, run_id) for slug in topics]
        failures = results.count(False)
        span.set_attribute("rpa.failures", failures)
        log.info(
            "execução finalizada",
            extra={"rpa.run_id": run_id, "rpa.successes": len(results) - failures, "rpa.failures": failures},
        )


def main() -> None:
    shutdown = setup_telemetry()
    try:
        store = Store(os.environ.get("ELASTICSEARCH_URL", "http://localhost:9200"))
        store.ensure_index()
        lang = os.environ.get("WIKI_LANG", "pt")
        topics = [t.strip() for t in os.environ.get("TOPICS", DEFAULT_TOPICS).split(",") if t.strip()]
        interval = int(os.environ.get("RPA_INTERVAL_SECONDS", "0"))
        while True:
            run(store, lang, topics)
            if interval <= 0:
                break
            time.sleep(interval)
    finally:
        shutdown()


if __name__ == "__main__":
    main()
