"""OpenTelemetry: traces, métricas e logs exportados via OTLP/gRPC para o Collector.

O endpoint vem de OTEL_EXPORTER_OTLP_ENDPOINT e o nome do serviço de OTEL_SERVICE_NAME.
"""
import logging

from opentelemetry import metrics, trace
from opentelemetry._logs import set_logger_provider
from opentelemetry.exporter.otlp.proto.grpc._log_exporter import OTLPLogExporter
from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import OTLPMetricExporter
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.requests import RequestsInstrumentor
from opentelemetry.sdk._logs import LoggerProvider, LoggingHandler
from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

# Os instrumentos são resolvidos de forma preguiçosa: valem depois de setup_telemetry().
tracer = trace.get_tracer("rpa.wikipedia")
_meter = metrics.get_meter("rpa.wikipedia")
pages_processed = _meter.create_counter(
    "rpa.pages.processed", unit="{page}", description="Páginas processadas, por status"
)
fetch_retries = _meter.create_counter(
    "rpa.fetch.retries", unit="{retry}", description="Tentativas repetidas de download"
)
page_duration = _meter.create_histogram(
    "rpa.page.duration", unit="ms", description="Duração do processamento de uma página"
)


def setup_telemetry():
    """Configura os três sinais e devolve a função que os descarrega ao encerrar."""
    resource = Resource.create(
        {"service.version": "1.0.0", "deployment.environment": "local"}
    )

    tracer_provider = TracerProvider(resource=resource)
    tracer_provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
    trace.set_tracer_provider(tracer_provider)

    meter_provider = MeterProvider(
        resource=resource,
        metric_readers=[
            PeriodicExportingMetricReader(OTLPMetricExporter(), export_interval_millis=10_000)
        ],
    )
    metrics.set_meter_provider(meter_provider)

    logger_provider = LoggerProvider(resource=resource)
    logger_provider.add_log_record_processor(BatchLogRecordProcessor(OTLPLogExporter()))
    set_logger_provider(logger_provider)

    # Console + OTLP; os logs enviados já carregam trace_id/span_id do span ativo.
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    logging.getLogger().addHandler(
        LoggingHandler(level=logging.INFO, logger_provider=logger_provider)
    )
    for noisy in ("urllib3", "elastic_transport"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    # Cada requests.get vira um span HTTP filho do span ativo.
    RequestsInstrumentor().instrument()

    def shutdown():
        tracer_provider.shutdown()
        meter_provider.shutdown()
        logger_provider.shutdown()

    return shutdown
