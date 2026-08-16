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
