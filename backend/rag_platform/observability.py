import json
import logging
import time
import uuid

from prometheus_client import Counter, Gauge, Histogram
from starlette.responses import JSONResponse

from .config import settings

REQUESTS = Counter("rag_http_requests_total", "API responses", ["method", "route", "status"])
LATENCY = Histogram("rag_http_seconds", "API latency", ["route"])
QUERIES = Counter("rag_queries_total", "Grounded query outcomes", ["outcome"])
JOBS = Counter("rag_jobs_total", "Worker outcomes", ["outcome"])
MODEL = Histogram("rag_model_seconds", "Local model duration", ["kind"])
BACKLOG = Gauge("rag_job_backlog", "Outstanding jobs across configured tenants", ["status"])
WORKER_HEARTBEAT = Gauge("rag_worker_heartbeat_timestamp_seconds", "Last successful worker queue cycle")
AUDIT_PENDING = Gauge("rag_audit_pending", "Audit outbox events pending delivery")


class JsonFormatter(logging.Formatter):
    def format(self, record):
        return json.dumps({"timestamp": record.created, "level": record.levelname,
                           "logger": record.name, "message": record.getMessage()}, ensure_ascii=False)


def configure_logging():
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    logging.getLogger().handlers = [handler]
    logging.getLogger().setLevel(logging.INFO)
    # SDK log messages may contain extracted content. Keep provider/LightRAG logs out of production telemetry.
    for name in ["lightrag", "httpx", "httpx2", "httpcore", "httpcore2", "sqlalchemy.engine"]:
        logging.getLogger(name).setLevel(logging.ERROR)
    cfg = settings()
    if cfg.otel_exporter_otlp_endpoint:
        from opentelemetry import trace
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
        provider = TracerProvider(resource=Resource.create({"service.name": "enterprise-rag"}))
        provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=cfg.otel_exporter_otlp_endpoint.rstrip("/") + "/v1/traces")))
        trace.set_tracer_provider(provider)


class BodyLimit:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        limit = settings().max_upload_bytes + 1024 * 1024
        length = dict(scope.get("headers", [])).get(b"content-length", b"0")
        try:
            too_large = int(length) > limit
        except ValueError:
            too_large = True
        if too_large:
            return await JSONResponse({"detail": "Request body exceeds limit"}, status_code=413)(scope, receive, send)
        size = 0

        async def bounded_receive():
            nonlocal size
            message = await receive()
            size += len(message.get("body", b""))
            if size > limit:
                from starlette.exceptions import HTTPException
                raise HTTPException(413, "Request body exceeds limit")
            return message
        return await self.app(scope, bounded_receive, send)


class RequestTelemetry:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        request_id = str(uuid.uuid4())
        scope.setdefault("state", {})["request_id"] = request_id
        start = time.monotonic()
        status = 500

        async def wrapped(message):
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
                headers = list(message.get("headers", []))
                headers += [(b"x-request-id", request_id.encode()), (b"x-content-type-options", b"nosniff"),
                            (b"referrer-policy", b"same-origin"), (b"cache-control", b"no-store"),
                            (b"x-frame-options", b"DENY")]
                headers.append((b"content-security-policy", b"default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; object-src 'none'; base-uri 'self'; form-action 'self'"))
                message["headers"] = headers
            await send(message)
        try:
            from opentelemetry import trace
            with trace.get_tracer(__name__).start_as_current_span("http.request") as span:
                await self.app(scope, receive, wrapped)
                route = getattr(scope.get("route"), "path", "unmatched")
                span.set_attribute("http.route", route)
                span.set_attribute("http.response.status_code", status)
        finally:
            route = getattr(scope.get("route"), "path", "unmatched")
            REQUESTS.labels(scope["method"], route, str(status)).inc()
            LATENCY.labels(route).observe(time.monotonic() - start)
