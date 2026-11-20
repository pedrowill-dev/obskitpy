"""Uso:

    import obskit
    shutdown = obskit.setup("meu-servico")     # logs, traces e métricas via OTLP

    @obskit.traced()                            # span por chamada
    def processar(item): ...

    log.exception("falhou")                     # já sai com error.type/origin/chain

O endpoint vem de OTEL_EXPORTER_OTLP_ENDPOINT (padrão http://localhost:4317).
"""
import functools
import logging
import os

from opentelemetry import metrics, trace
from opentelemetry.trace import Status, StatusCode

from .errors import error_attributes, error_chain

__all__ = ["setup", "traced", "tracer", "meter", "error_chain", "error_attributes"]

_NOISY_LOGGERS = ("urllib3", "elastic_transport", "opentelemetry")


def tracer(name: str = "app"):
    return trace.get_tracer(name)


def meter(name: str = "app"):
    return metrics.get_meter(name)


def traced(name: str | None = None):
    """Decorator: abre um span por chamada e marca erro (com a exceção) se ela propagar."""
    def wrap(fn):
        span_name = name or f"{fn.__module__}.{fn.__qualname__}"

        @functools.wraps(fn)
        def inner(*args, **kwargs):
            with trace.get_tracer(fn.__module__).start_as_current_span(span_name) as span:
                try:
                    return fn(*args, **kwargs)
                except Exception as exc:
                    span.record_exception(exc)
                    span.set_status(Status(StatusCode.ERROR, str(exc)))
                    raise
        return inner
    return wrap


class _ErrorAttributes(logging.Filter):
    """Todo log com exc_info ganha error.type/origin/chain (sem o dev precisar passar `extra`)."""

    def filter(self, record: logging.LogRecord) -> bool:
        if record.exc_info and record.exc_info[1] is not None:
            for key, value in error_attributes(record.exc_info[1]).items():
                record.__dict__.setdefault(key, value)
        return True


def setup(service_name: str, *, environment: str | None = None, version: str = "0.0.0",
          metrics_interval_ms: int = 10_000, instrument_requests: bool = True):
    """Configura traces, métricas e logs. Devolve a função que os descarrega ao encerrar."""
    # O modo "otel" do Elasticsearch descarta histogramas cumulativos.
    os.environ.setdefault("OTEL_EXPORTER_OTLP_METRICS_TEMPORALITY_PREFERENCE", "delta")
    os.environ.setdefault("OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4317")

    from opentelemetry._logs import set_logger_provider
    from opentelemetry.exporter.otlp.proto.grpc._log_exporter import OTLPLogExporter
    from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import OTLPMetricExporter
    from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
    from opentelemetry.sdk._logs import LoggerProvider, LoggingHandler
    from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
    from opentelemetry.sdk.metrics import MeterProvider
    from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor

    resource = Resource.create({
        "service.name": service_name,
        "service.version": version,
        "deployment.environment": environment or os.getenv("APP_ENV", "local"),
    })

    tracer_provider = TracerProvider(resource=resource)
    tracer_provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
    trace.set_tracer_provider(tracer_provider)

    meter_provider = MeterProvider(resource=resource, metric_readers=[
        PeriodicExportingMetricReader(OTLPMetricExporter(), export_interval_millis=metrics_interval_ms)])
    metrics.set_meter_provider(meter_provider)

    logger_provider = LoggerProvider(resource=resource)
    logger_provider.add_log_record_processor(BatchLogRecordProcessor(OTLPLogExporter()))
    set_logger_provider(logger_provider)

    root = logging.getLogger()
    if not root.handlers:
        logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    handler = LoggingHandler(level=logging.INFO, logger_provider=logger_provider)
    handler.addFilter(_ErrorAttributes())
    root.addHandler(handler)
    for name in _NOISY_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)

    if instrument_requests:
        try:
            from opentelemetry.instrumentation.requests import RequestsInstrumentor
            RequestsInstrumentor().instrument()
        except ImportError:
            pass  # extra opcional: pip install "obskit[requests]"

    def shutdown():
        root.removeHandler(handler)
        tracer_provider.shutdown()
        meter_provider.shutdown()
        logger_provider.shutdown()

    return shutdown
