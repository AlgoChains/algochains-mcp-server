"""Optional OpenTelemetry helpers for AlgoChains MCP execution boundaries.

The module is intentionally dependency-optional. If OTLP is not configured or
OpenTelemetry packages are absent, all helpers become no-ops. Tool arguments are
never exported as span attributes; only hashes and policy metadata are emitted.
"""
from __future__ import annotations

import contextlib
import hashlib
import json
import os
from collections.abc import Iterator
from typing import Any

_TRACER: Any | None = None
_INIT_ATTEMPTED = False


def tracing_enabled() -> bool:
    return bool(os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT")) and (
        os.getenv("OTEL_SDK_DISABLED", "").lower() not in {"1", "true", "yes"}
    )


def content_capture_enabled() -> bool:
    return os.getenv("OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT", "").lower() in {
        "1",
        "true",
        "yes",
    }


def get_tracer() -> Any | None:
    global _TRACER, _INIT_ATTEMPTED
    if _TRACER is not None:
        return _TRACER
    if _INIT_ATTEMPTED or not tracing_enabled():
        return None
    _INIT_ATTEMPTED = True
    try:
        from opentelemetry import trace
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor

        resource = Resource.create(
            {
                "service.name": os.getenv("OTEL_SERVICE_NAME", "algochains-mcp-server"),
                "algochains.component": "mcp_server",
            }
        )
        provider = TracerProvider(resource=resource)
        provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
        trace.set_tracer_provider(provider)
        _TRACER = trace.get_tracer("algochains.mcp")
        return _TRACER
    except Exception:
        return None


def redacted_argument_hash(arguments: dict[str, Any] | None) -> str:
    """Hash canonicalized arguments after replacing secret-like values."""

    def _redact(value: Any) -> Any:
        if isinstance(value, dict):
            redacted = {}
            for key, inner in value.items():
                lowered = str(key).lower()
                if any(s in lowered for s in ("token", "secret", "password", "api_key", "key")):
                    redacted[key] = "[REDACTED]"
                else:
                    redacted[key] = _redact(inner)
            return redacted
        if isinstance(value, list):
            return [_redact(item) for item in value]
        return value

    canonical = json.dumps(_redact(arguments or {}), sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _safe_attrs(attrs: dict[str, Any] | None) -> dict[str, Any]:
    safe: dict[str, Any] = {}
    for key, value in (attrs or {}).items():
        lowered = key.lower()
        if any(secret in lowered for secret in ("token", "secret", "password", "key")):
            safe[key] = "[REDACTED]"
        elif isinstance(value, (str, int, float, bool)) or value is None:
            safe[key] = value
        else:
            safe[key] = str(value)[:300]
    return safe


@contextlib.contextmanager
def trace_span(name: str, attrs: dict[str, Any] | None = None) -> Iterator[Any | None]:
    tracer = get_tracer()
    if tracer is None:
        yield None
        return
    with tracer.start_as_current_span(name) as span:
        for key, value in _safe_attrs(attrs).items():
            if value is not None:
                span.set_attribute(key, value)
        try:
            yield span
        except Exception as exc:
            record_exception(span, exc)
            raise


def record_exception(span: Any | None, exc: BaseException) -> None:
    if span is None:
        return
    try:
        from opentelemetry.trace import Status, StatusCode

        span.record_exception(exc)
        span.set_status(Status(StatusCode.ERROR, str(exc)[:300]))
    except Exception:
        return


def span_attrs_for_component(
    *,
    component: str,
    name: str,
    status: str | None = None,
    authority_level: str | None = None,
    content_hash: str | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build Phoenix/OpenInference-friendly attributes without raw content."""
    attrs: dict[str, Any] = {
        "openinference.span.kind": component,
        "algochains.component.name": name,
        "algochains.authority_level": authority_level,
        "algochains.content_hash": content_hash,
        "algochains.status": status,
        "algochains.content_capture": content_capture_enabled(),
    }
    if extra:
        attrs.update(extra)
    return _safe_attrs(attrs)


def retriever_attrs(name: str, *, store: str, query_hash: str, result_count: int, authority_level: str) -> dict[str, Any]:
    return span_attrs_for_component(
        component="retriever",
        name=name,
        authority_level=authority_level,
        content_hash=query_hash,
        extra={"retrieval.store": store, "retrieval.result_count": result_count},
    )


def reranker_attrs(name: str, *, input_count: int, output_count: int) -> dict[str, Any]:
    return span_attrs_for_component(
        component="reranker",
        name=name,
        extra={"reranker.input_count": input_count, "reranker.output_count": output_count},
    )


def evaluator_attrs(name: str, *, metric_authority: str, passed: bool) -> dict[str, Any]:
    return span_attrs_for_component(
        component="evaluator",
        name=name,
        status="pass" if passed else "fail",
        extra={"evaluation.metric_authority": metric_authority},
    )


def guardrail_attrs(name: str, *, decision: str, reason: str | None = None) -> dict[str, Any]:
    return span_attrs_for_component(
        component="guardrail",
        name=name,
        status=decision,
        extra={"guardrail.reason": reason},
    )


def scheduler_attrs(job_id: str, *, state: str, repeat: bool | None = None) -> dict[str, Any]:
    return span_attrs_for_component(
        component="scheduler",
        name=job_id,
        status=state,
        extra={"scheduler.job_id": job_id, "scheduler.repeat": repeat},
    )
