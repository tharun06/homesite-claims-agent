"""Application Insights wiring — one call, shared by every service.

Enabled only when APPLICATIONINSIGHTS_CONNECTION_STRING is set, so local runs
and tests stay silent and free. A missing package is not fatal either: telemetry
that breaks the app it observes is worse than no telemetry.

What this buys, and why it was the biggest gap in the project:

  requests      latency and failure rate per endpoint
  dependencies  every outbound httpx call - Azure OpenAI, Azure AI Search, the
                backend - with duration and result. This is what makes an LLM
                call visible at all; nothing else in Azure shows it.
  correlation   operation_Id is propagated automatically, so one user question
                can be followed across the copilot, the MCP server and the
                backend. That was previously impossible: a user reporting a bad
                answer left no trail.
  exceptions    stack traces, tied to the request that caused them

Cost: Application Insights includes 5 GB/month free ingestion across the
account. This project ingests a rounding error against that, and the component
carries a 0.2 GB/day cap so a logging loop cannot turn into a bill.
"""
import logging
import os

_configured = False


def setup(service_name: str) -> bool:
    """Wire up Azure Monitor. Returns True if telemetry is live.

    Safe to call more than once; only the first call configures anything.
    """
    global _configured
    if _configured:
        return True

    conn = os.getenv("APPLICATIONINSIGHTS_CONNECTION_STRING", "")
    if not conn:
        return False

    # OTEL_SERVICE_NAME is what actually populates AppRoleName. Passing
    # resource_attributes={"service.name": ...} to configure_azure_monitor did
    # NOT do it - everything arrived as "unknown_service", so the three services
    # were indistinguishable in the dependency map. setdefault, so an explicit
    # env var still wins.
    os.environ.setdefault("OTEL_SERVICE_NAME", service_name)

    try:
        from azure.monitor.opentelemetry import configure_azure_monitor
    except ImportError:
        logging.getLogger(__name__).info(
            "APPLICATIONINSIGHTS_CONNECTION_STRING is set but "
            "azure-monitor-opentelemetry is not installed - telemetry disabled")
        return False

    try:
        configure_azure_monitor(
            connection_string=conn,
            # Shows up as cloud_RoleName, which is how the three services are
            # told apart in the dependency map.
            resource_attributes={"service.name": service_name},
        )
        # Do NOT pass logger_name=None here. It is not the same as omitting it -
        # the metrics exporter calls it and dies with
        # "TypeError: 'NoneType' object is not callable", which crashes the
        # container on startup.
        _configured = True
        logging.getLogger(__name__).info("telemetry enabled for %s", service_name)
        return True
    except Exception as exc:                      # never break the app for this
        logging.getLogger(__name__).warning("telemetry setup failed: %s", exc)
        return False


# ── GenAI spans ──────────────────────────────────────────────────────────────
# Azure Monitor's "Agents" view reads OpenTelemetry GenAI semantic conventions:
# gen_ai.system, gen_ai.request.model, token usage, tool calls. Generic HTTP
# instrumentation cannot produce those - it sees a POST to an Azure OpenAI host,
# not a completion that used 3,200 prompt tokens and called search_policy_docs.
#
# The official instrumentation (opentelemetry-instrumentation-openai-v2) pins
# opentelemetry-instrumentation ~=0.60b0 while azure-monitor-opentelemetry 1.8.9
# pins >=0.64b0,<0.65.0. Those ranges do not overlap, so it cannot be installed
# alongside the Azure distro today. We emit the spans ourselves instead: the
# attributes are a published spec, we already have every value, and this cannot
# be broken by either package moving.
#
# Message CONTENT is deliberately not captured. The spec has a flag for it, but
# prompts here contain policy text and claim details, and putting that in Log
# Analytics is a privacy decision rather than a config one.
from contextlib import contextmanager

_GEN_AI_SYSTEM = "az.ai.openai"


@contextmanager
def chat_span(model: str):
    """Wrap one model call. Span name follows the spec: "<operation> <model>"."""
    if not _configured:
        yield None
        return
    try:
        from opentelemetry import trace
        from opentelemetry.trace import SpanKind
    except ImportError:
        yield None
        return

    tracer = trace.get_tracer("homesite.genai")
    with tracer.start_as_current_span(f"chat {model}", kind=SpanKind.CLIENT) as span:
        span.set_attribute("gen_ai.system", _GEN_AI_SYSTEM)
        span.set_attribute("gen_ai.operation.name", "chat")
        span.set_attribute("gen_ai.request.model", model)
        yield span


def record_usage(span, reply) -> None:
    """Copy token counts and finish reason off a LangChain reply onto the span.

    usage_metadata is the normalised shape LangChain exposes; response_metadata
    is the raw provider payload and is used only as a fallback.
    """
    if span is None or reply is None:
        return
    try:
        usage = getattr(reply, "usage_metadata", None) or {}
        if not usage:
            usage = (getattr(reply, "response_metadata", {}) or {}).get("token_usage", {}) or {}
        for attr, keys in (
            ("gen_ai.usage.input_tokens", ("input_tokens", "prompt_tokens")),
            ("gen_ai.usage.output_tokens", ("output_tokens", "completion_tokens")),
        ):
            for k in keys:
                if usage.get(k) is not None:
                    span.set_attribute(attr, int(usage[k]))
                    break
        meta = getattr(reply, "response_metadata", {}) or {}
        if meta.get("finish_reason"):
            span.set_attribute("gen_ai.response.finish_reasons", [str(meta["finish_reason"])])
        if meta.get("model_name"):
            span.set_attribute("gen_ai.response.model", str(meta["model_name"]))
        # Which tools the model asked for - this is what turns a completion into
        # an agent step in the Agents view.
        calls = getattr(reply, "tool_calls", None) or []
        if calls:
            span.set_attribute("gen_ai.response.tool_calls", [c.get("name", "?") for c in calls])
    except Exception:
        pass          # telemetry must never break the call it is describing


def tool_span(name: str, start_ns: int, end_ns: int, error: str = "") -> None:
    """Record a completed tool execution.

    Emitted after the fact with explicit timestamps because the tool runs inside
    LangGraph's node, which we do not wrap - the event stream tells us when it
    started and finished.
    """
    if not _configured:
        return
    try:
        from opentelemetry import trace
        from opentelemetry.trace import SpanKind, Status, StatusCode

        tracer = trace.get_tracer("homesite.genai")
        span = tracer.start_span(f"execute_tool {name}", kind=SpanKind.INTERNAL,
                                 start_time=start_ns)
        span.set_attribute("gen_ai.system", _GEN_AI_SYSTEM)
        span.set_attribute("gen_ai.operation.name", "execute_tool")
        span.set_attribute("gen_ai.tool.name", name)
        if error:
            span.set_status(Status(StatusCode.ERROR, error[:200]))
        span.end(end_time=end_ns)
    except Exception:
        pass
