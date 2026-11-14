"""Optional OTLP traces for dashboard requests, without personal data attributes."""
import os
from time import perf_counter

from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.trace import Status, StatusCode


_configured = False


def configure_telemetry() -> bool:
    global _configured
    endpoint = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT", "").strip()
    if not endpoint:
        return False
    if _configured:
        return True
    provider = TracerProvider(resource=Resource.create({"service.name": "sa-alert-dashboard"}))
    provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(
        endpoint=endpoint.rstrip("/") + "/v1/traces", timeout=5)))
    trace.set_tracer_provider(provider)
    _configured = True
    return True


def instrument_dashboard(app):
    if not configure_telemetry():
        return
    tracer = trace.get_tracer(__name__)

    @app.middleware("http")
    async def trace_request(request, call_next):
        if not request.url.path.startswith("/api/") or request.url.path.startswith("/api/profile-photo/"):
            return await call_next(request)
        started = perf_counter()
        with tracer.start_as_current_span("dashboard.request") as span:
            span.set_attribute("http.request.method", request.method)
            try:
                response = await call_next(request)
            except Exception as exc:
                span.record_exception(exc)
                span.set_status(Status(StatusCode.ERROR))
                raise
            route = request.scope.get("route")
            span.set_attribute("http.route", route.path if route else "unknown")
            span.set_attribute("http.response.status_code", response.status_code)
            span.set_attribute("dashboard.duration_ms", round((perf_counter() - started) * 1000, 2))
            if response.status_code >= 500:
                span.set_status(Status(StatusCode.ERROR))
            return response
