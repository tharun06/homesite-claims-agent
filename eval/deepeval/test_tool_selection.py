"""Tool selection — does the agent route each question to the right tool?

This is the routing decision, which is the orchestrator's main job. Nothing else
in the suite tests it, and it is the failure mode most invisible to users: ask
"how many claims are fraud flagged" and get a vector search instead of SQL, and
you get a confident answer built from five sampled rows.

Uses DeepEval's `ToolCorrectnessMetric` with `tools_called` / `expected_tools`.
That metric is DETERMINISTIC — it compares tool names, no judge involved — so
unlike faithfulness it needs no validation before being trusted.

The schemas come from the real FastMCP registry, so the docstrings under test
are the ones production ships. Tool descriptions ARE the routing prompt: most
wrong-tool bugs are fixed by rewriting a description, not the system prompt.

    pytest eval/deepeval/test_tool_selection.py -v
"""
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "adjuster-dashboard" / "copilot"))

# Each question, and the tool an adjuster would expect it to reach.
ROUTING_CASES = [
    ("How many claims do I have in each status?",            "query_claims_data"),
    ("Which region has the most claims?",                    "query_claims_data"),
    ("What is the average estimated amount on my claims?",   "query_claims_data"),
    ("What is my approval limit for a total loss?",          "search_policy_docs"),
    ("What does the policy say about rental reimbursement?", "search_policy_docs"),
    ("Have we seen a claim like this rear-end collision?",   "search_similar_claims"),
    ("What is the status of claim CLM-313426?",              "get_claim_status"),
    ("Show me my claims",                                    "list_my_claims"),
    ("What are my pending tasks?",                           "get_my_pending_tasks"),
    ("How is the queue looking overall?",                    "queue_metrics"),
]


@pytest.fixture(scope="session")
def bound_llm():
    """The real tool schemas, bound to the real model.

    Pulled from FastMCP's registry rather than hand-written, so a docstring
    change in mcp_server.py is picked up here automatically — which is the point,
    since that is what actually drives routing.
    """
    os.environ.setdefault("DASHBOARD_URL", "http://localhost:8100")
    from langchain_openai import AzureChatOpenAI
    import mcp_server

    registry = mcp_server.mcp._tool_manager._tools
    schemas = []
    for name, tool in registry.items():
        schemas.append({
            "type": "function",
            "function": {
                "name": name,
                "description": (tool.description or "")[:1024],
                "parameters": tool.parameters or {"type": "object", "properties": {}},
            },
        })
    # query_claims_data is a LOCAL tool in agent.py, not an MCP tool, so it is
    # absent from the registry — but it is the correct destination for every
    # aggregate question. Omitting it would make those cases unanswerable.
    from agent import SYSTEM_PROMPT  # noqa: F401  (kept in sync with production)
    schemas.append({
        "type": "function",
        "function": {
            "name": "query_claims_data",
            "description": (
                "Answer AGGREGATE / ANALYTICAL questions about your claims by running "
                "SQL: counts, sums, averages, group-by, trends. Use this when the answer "
                "needs math across many claims rather than a single lookup."),
            "parameters": {"type": "object",
                           "properties": {"question": {"type": "string"}},
                           "required": ["question"]},
        },
    })

    llm = AzureChatOpenAI(
        azure_endpoint=os.getenv("AZURE_OPENAI_ENDPOINT"),
        api_key=os.getenv("AZURE_OPENAI_KEY"),
        azure_deployment=os.getenv("AZURE_OPENAI_DEPLOYMENT"),
        api_version="2024-08-01-preview",
        temperature=0,
    )
    return llm.bind_tools(schemas), SYSTEM_PROMPT


@pytest.fixture(scope="session")
def routed(bound_llm) -> dict:
    """Ask once per question; record which tools the model chose.

    Only the ROUTING decision is exercised — no tool is executed. That keeps the
    test hermetic: it needs no database, no backend and no MCP subprocess.
    """
    llm, system = bound_llm
    out = {}
    for question, _expected in ROUTING_CASES:
        try:
            reply = llm.invoke([("system", system), ("user", question)])
            out[question] = [c["name"] for c in (reply.tool_calls or [])]
        except Exception as e:
            out[question] = [f"<error: {type(e).__name__}>"]
    return out


@pytest.mark.parametrize("question,expected", ROUTING_CASES,
                         ids=[q[:34] for q, _ in ROUTING_CASES])
def test_tool_correctness(question, expected, routed):
    """DeepEval's ToolCorrectnessMetric — deterministic name comparison."""
    from deepeval.metrics import ToolCorrectnessMetric
    from deepeval.test_case import LLMTestCase, ToolCall

    from conftest import record_metric

    called = routed[question]
    metric = ToolCorrectnessMetric(threshold=1.0)
    metric.measure(LLMTestCase(
        input=question,
        actual_output="",                       # routing only; nothing executed
        tools_called=[ToolCall(name=n) for n in called],
        expected_tools=[ToolCall(name=expected)],
    ))
    record_metric(question[:34], "tool_correctness", metric.score, 1.0,
                  f"called={called} expected={expected}")
    assert metric.score >= 1.0, f"expected {expected}, called {called}"


def test_read_questions_never_route_to_a_write_tool(routed):
    """None of these questions asks to change anything. If a read question ever
    reaches update/add/reassign, the approval gate becomes the only thing between
    a question and a mutation — and a gate is a worse defence than never routing
    there in the first place."""
    from agent import WRITE_TOOLS
    offenders = {q: c for q, c in routed.items() if set(c) & WRITE_TOOLS}
    assert not offenders, f"read questions routed to write tools: {offenders}"
