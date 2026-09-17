"""Opt-in, app-scoped telemetry. No payloads, SQL, headers or exception messages."""

import json
import logging
from contextlib import contextmanager
from contextvars import ContextVar
from functools import wraps
from inspect import signature
from time import perf_counter
from uuid import UUID, uuid4

from opentelemetry import trace
from opentelemetry.trace import SpanKind, StatusCode

_runtime = ContextVar("nexus_telemetry", default=None)
_fields = ContextVar("nexus_correlation", default=None)
SAFE_ERRORS = {
    "BusinessError",
    "HTTPException",
    "RequestValidationError",
    "GraphRecursionError",
    "OperationalError",
    "IntegrityError",
    "TimeoutError",
    "APIConnectionError",
    "APITimeoutError",
    "RateLimitError",
    "InjectedFailure",
    "ValueError",
    "RuntimeError",
}
LOG_FIELDS = (
    "trace_id",
    "conversation_id",
    "turn_id",
    "request_id",
    "node",
    "status",
    "error_type",
)
logger = logging.getLogger("nexus.telemetry")


def safe_id(value):
    try:
        return str(UUID(str(value)))
    except (ValueError, TypeError, AttributeError):
        return ""


def bind(**values):
    # A fresh dictionary belongs to each request. Worker threads share that request's
    # dictionary, so the final HTTP record sees business IDs set by sync handlers.
    fields = _fields.get()
    if fields is not None:
        for key in ("conversation_id", "turn_id", "request_id"):
            if key in values:
                fields[key] = safe_id(values[key])


def error_type(exc):
    name = type(exc).__name__
    return name if name in SAFE_ERRORS else "InternalError"


def mark_error(exc):
    fields = _fields.get()
    if fields is not None:
        fields["error_type"] = error_type(exc)


class InjectedFailure(RuntimeError):
    """Local deterministic fault, enabled only in demo mode."""


class Observability:
    def __init__(self, config, exporter=None, log_sink=None):
        self.enabled = config.observability_enabled
        self.fault_node = config.observability_fault_node if config.demo_mode else ""
        self.provider = None
        self.registry = None
        self.log_sink = log_sink or self._write_log
        if not self.enabled:
            return
        if self.fault_node not in ("", "understand"):
            raise ValueError("NEXUS_OBS_FAULT_NODE supports only understand")
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import (
            BatchSpanProcessor,
            SimpleSpanProcessor,
        )
        from prometheus_client import CollectorRegistry, Counter, Histogram

        self.provider = TracerProvider(
            resource=Resource({"service.name": "nexusagent"})
        )
        if exporter is not None:
            self.provider.add_span_processor(SimpleSpanProcessor(exporter))
        elif config.otel_endpoint:
            from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
                OTLPSpanExporter,
            )

            self.provider.add_span_processor(
                BatchSpanProcessor(
                    OTLPSpanExporter(endpoint=config.otel_endpoint, timeout=2),
                    max_queue_size=2048,
                    schedule_delay_millis=1000,
                    export_timeout_millis=3000,
                )
            )
        self.tracer = self.provider.get_tracer("nexusagent.observability", "1.0")
        self.registry = CollectorRegistry()
        self.http_count = Counter(
            "nexus_http_requests_total",
            "HTTP responses, including 4xx/5xx",
            ["method", "route", "status_code"],
            registry=self.registry,
        )
        self.http_duration = Histogram(
            "nexus_http_request_duration_seconds",
            "HTTP response duration",
            ["method", "route"],
            registry=self.registry,
        )
        self.operations = Counter(
            "nexus_operations_total",
            "Operation attempts, not unique business writes",
            ["category", "operation", "status"],
            registry=self.registry,
        )
        self.duration = Histogram(
            "nexus_operation_duration_seconds",
            "Operation attempt duration",
            ["category", "operation"],
            registry=self.registry,
        )
        self.outcomes = Counter(
            "nexus_agent_outcomes_total",
            "Send/decision outcomes, including retries",
            ["action", "outcome"],
            registry=self.registry,
        )
        self.retrievals = Counter(
            "nexus_retrieval_total",
            "Knowledge retrieval results",
            ["outcome"],
            registry=self.registry,
        )

    @staticmethod
    def _write_log(record):
        if not logger.handlers:
            handler = logging.StreamHandler()
            handler.setFormatter(logging.Formatter("%(message)s"))
            logger.addHandler(handler)
            logger.setLevel(logging.INFO)
            logger.propagate = False
        logger.info(json.dumps(record, ensure_ascii=False))

    def log(self, node, status, failure=""):
        fields = _fields.get() or {}
        span_context = trace.get_current_span().get_span_context()
        record = {key: "" for key in LOG_FIELDS}
        record.update(
            {
                key: safe_id(fields.get(key))
                for key in ("conversation_id", "turn_id", "request_id")
            }
        )
        record.update(
            trace_id=format(span_context.trace_id, "032x")
            if span_context.is_valid
            else "",
            node=node,
            status=status,
            error_type=failure,
        )
        try:
            self.log_sink(record)
        except Exception:  # noqa: BLE001, S110 -- sink failure must not change business writes
            # A telemetry sink must never roll back or duplicate a business action.
            pass

    @contextmanager
    def span(self, name, category):
        if not self.enabled:
            yield
            return
        from langgraph.errors import GraphInterrupt

        start, status, failure = perf_counter(), "ok", ""
        with self.tracer.start_as_current_span(
            name, record_exception=False, set_status_on_exception=False
        ) as span:
            try:
                if category == "node" and name == "agent." + self.fault_node:
                    raise InjectedFailure()
                yield
            except GraphInterrupt:
                status = "waiting"
                raise
            except Exception as exc:
                status, failure = "error", error_type(exc)
                if category == "retrieval":
                    self.retrievals.labels("error").inc()
                if category == "service":
                    self.outcomes.labels(name.removeprefix("agent."), "error").inc()
                mark_error(exc)
                span.set_status(StatusCode.ERROR)
                span.set_attribute("error.type", failure)
                # Never call record_exception: exception text/stack can contain keys,
                # prompts, SQL parameters or cookies.
                span.add_event("exception", {"exception.type": failure})
                raise
            finally:
                span.set_attribute("nexus.status", status)
                for key, value in (_fields.get() or {}).items():
                    if key in ("conversation_id", "turn_id", "request_id") and safe_id(
                        value
                    ):
                        span.set_attribute("nexus." + key, safe_id(value))
                self.operations.labels(category, name, status).inc()
                self.duration.labels(category, name).observe(perf_counter() - start)
                self.log(name, status, failure)

    def shutdown(self):
        if self.provider is not None:
            self.provider.shutdown()


def observed(name, category, ids=None):
    """Wrap sync operations, preserving their return/exception and transaction boundary."""

    def decorate(function):
        parameters = signature(function)

        @wraps(function)
        def wrapped(*args, **kwargs):
            obs = _runtime.get()
            if obs is None or not obs.enabled:
                return function(*args, **kwargs)
            arguments = parameters.bind(*args, **kwargs).arguments
            if ids:
                bind(**{key: arguments.get(arg) for key, arg in ids.items()})
            with obs.span(name, category):
                result = function(*args, **kwargs)
                if category == "retrieval":
                    obs.retrievals.labels(
                        "found" if result["found"] else "no_match"
                    ).inc()
                return result

        return wrapped

    return decorate


def outcome(action, value):
    obs = _runtime.get()
    if obs is not None and obs.enabled:
        obs.outcomes.labels(action, value).inc()


class TelemetryMiddleware:
    """ASGI server span; W3C traceparent only, no baggage or raw URL collection."""

    def __init__(self, app, telemetry):
        self.app, self.telemetry = app, telemetry

    async def __call__(self, scope, receive, send):
        obs = self.telemetry
        if scope["type"] != "http" or not obs.enabled or scope["path"] == "/metrics":
            return await self.app(scope, receive, send)
        from opentelemetry.trace.propagation.tracecontext import (
            TraceContextTextMapPropagator,
        )
        from starlette.responses import JSONResponse

        carrier = {
            key.decode("ascii"): value.decode("ascii", errors="ignore")
            for key, value in scope.get("headers", [])
            if key == b"traceparent"
        }
        context = TraceContextTextMapPropagator().extract(carrier)
        token = _runtime.set(obs)
        fields_token = _fields.set({"request_id": str(uuid4())})
        start, code, started = perf_counter(), 500, False
        method = (
            scope["method"]
            if scope["method"]
            in {"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"}
            else "OTHER"
        )
        with obs.tracer.start_as_current_span(
            "HTTP",
            context=context,
            kind=SpanKind.SERVER,
            record_exception=False,
            set_status_on_exception=False,
        ) as span:

            async def respond(message):
                nonlocal code, started
                if message["type"] == "http.response.start":
                    code, started = message["status"], True
                    message["headers"] = [
                        *message.get("headers", []),
                        (
                            b"x-trace-id",
                            format(span.get_span_context().trace_id, "032x").encode(),
                        ),
                    ]
                await send(message)

            try:
                await self.app(scope, receive, respond)
            except Exception as exc:
                mark_error(exc)
                if started:
                    code = 500
                    raise
                # Avoid Uvicorn printing a raw exception/traceback containing inputs.
                await JSONResponse(
                    {"detail": "请求失败，请用原请求重试。"}, status_code=500
                )(scope, receive, respond)
            finally:
                route_object = scope.get("route")
                route = getattr(route_object, "path", None) or "unmatched"
                if route == "/assets":
                    route = "/assets/*"
                span.update_name(method + " " + route)
                span.set_attribute("http.request.method", method)
                span.set_attribute("http.route", route)
                span.set_attribute("http.response.status_code", code)
                failure = (_fields.get() or {}).get("error_type", "")
                if code >= 500:
                    span.set_status(StatusCode.ERROR)
                    span.set_attribute("error.type", failure or "HTTPServerError")
                for key, value in (_fields.get() or {}).items():
                    if key in ("conversation_id", "turn_id", "request_id") and safe_id(
                        value
                    ):
                        span.set_attribute("nexus." + key, safe_id(value))
                obs.http_count.labels(method, route, str(code)).inc()
                obs.http_duration.labels(method, route).observe(perf_counter() - start)
                obs.log(
                    "http",
                    "error" if code >= 500 else "rejected" if code >= 400 else "ok",
                    failure,
                )
                _fields.reset(fields_token)
                _runtime.reset(token)
