"""OpenTelemetry tracing setup.

IMPORTANT: No document contents in span attributes — the data is client data by premise.
Only document_id, partition_id, field_path, and operational metadata are allowed.
"""

from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

from pipeline.config import settings

_tracer_provider: TracerProvider | None = None


def init_tracing() -> TracerProvider:
    """Initialize OpenTelemetry tracing. Call once at app startup."""
    global _tracer_provider

    resource = Resource.create(
        {
            "service.name": settings.otel_service_name,
            "service.version": "0.1.0",
        }
    )

    _tracer_provider = TracerProvider(resource=resource)

    if settings.otel_exporter_otlp_endpoint:
        exporter = OTLPSpanExporter(endpoint=settings.otel_exporter_otlp_endpoint)
        _tracer_provider.add_span_processor(BatchSpanProcessor(exporter))

    trace.set_tracer_provider(_tracer_provider)
    return _tracer_provider


def get_tracer(name: str) -> trace.Tracer:
    """Get a tracer for instrumenting a module."""
    return trace.get_tracer(name, "0.1.0")


# Span names for the interactive tier
SPAN_UPLOAD_PART = "upload_part"
SPAN_COMPLETE = "complete"
SPAN_PARTITION = "partition"
SPAN_EXTRACT_CALL = "extract_call"
SPAN_MERGE = "merge"
SPAN_VALIDATE = "validate"
SPAN_COMMIT = "commit"

# Span names for the batch tier
SPAN_SPARK_STAGE = "spark_stage"
SPAN_DBT_MODEL = "dbt_model"
SPAN_QUALITY_GATE = "quality_gate"
