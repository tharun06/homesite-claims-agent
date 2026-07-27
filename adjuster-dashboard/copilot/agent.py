import os
from contextlib import asynccontextmanager
from typing import Annotated, TypedDict
from dotenv import load_dotenv
load_dotenv()

from langgraph.graph import StateGraph, START, END
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

from langchain_openai import AzureChatOpenAI

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from langchain_mcp_adapters.tools import load_mcp_tools

import httpx
from langchain_core.tools import tool
from sql_graph import build_sql_graph
from sql_runtime import database_available

# the NL2SQL subgraph — compiled once and reused across every request
_SQL_GRAPH = build_sql_graph()


class State(TypedDict):
    messages: Annotated[list, add_messages]

# Every tool that MUTATES claim data must route through the `action` node so it
# pauses for human approval. add_note_to_claim writes a Conversation row and
# broadcasts on the WS hub; reassign_claim moves a claim between adjusters —
# both were previously executing ungated.
WRITE_TOOLS = {"update_claim_status", "add_note_to_claim", "reassign_claim"}

# confirmed via a direct A/B test: without this, the model cites sources in
# vague prose ("the adjuster authority matrix") instead of the exact file name.
SYSTEM_PROMPT = (
    "You are an assistant for insurance adjusters. When you use information "
    "from search_policy_docs, you MUST name the source document explicitly "
    "(e.g. 'per adjuster-authority-matrix.txt') and, if a numeric limit or "
    "threshold is relevant, state the exact number and directly compare it "
    "to the user's figures."
)

def router_after_agent(state: State):
    last_message = state["messages"][-1]
    if not last_message.tool_calls:
        return END
    # Check EVERY call in the batch, not just the last one. The model can emit
    # parallel tool calls; if a write sat anywhere but the final position we used
    # to route the whole batch to `tools`, skipping the approval gate entirely.
    if any(call["name"] in WRITE_TOOLS for call in last_message.tool_calls):
        return "action"
    return "tools"


async def _resolve_caller(token: str | None):
    """Resolve (user_id, role) from the caller's token via the backend /me.
    This is how the NL2SQL subgraph gets its scope — from the JWT, never the LLM.
    Returns (None, 'adjuster') when there's no valid token."""
    if not token:
        return None, "adjuster"
    url = os.getenv("DASHBOARD_URL", "http://localhost:8100")
    try:
        async with httpx.AsyncClient() as client:
            r = await client.get(f"{url}/me", headers={"Authorization": f"Bearer {token}"})
            r.raise_for_status()
            me = r.json()
        return me["id"], me["role"]
    except Exception:
        return None, "adjuster"


@asynccontextmanager
async def build_graph(adjuster_token: str | None = None):
    server_params = StdioServerParameters(
        command="python",
        args=["mcp_server.py"],
        env={
            **os.environ,
            "DASHBOARD_URL": os.getenv("DASHBOARD_URL", "http://localhost:8100"),
            "ADJUSTER_TOKEN": adjuster_token or "",
        },
    )
    # tools that CHANGE data — these must pause for human approval

    # COPILOT_DB points at the mounted Azure Files share in deployment, so
    # conversation history and any pending human-approval state survive a
    # restart. A relative path lands on ephemeral container disk and is lost.
    async with AsyncSqliteSaver.from_conn_string(
        os.getenv("COPILOT_DB", "copilot.db")
    ) as saver:
        async with stdio_client(server_params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                tools = await load_mcp_tools(session)

                # NL2SQL subgraph as one local tool. Resolve the caller (id +
                # role) from their token so the subgraph scopes SQL to exactly
                # what they may see — the scope comes from the JWT, not the LLM.
                sql_user_id, sql_role = await _resolve_caller(adjuster_token)

                @tool
                def query_claims_data(question: str) -> str:
                    """Answer AGGREGATE / ANALYTICAL questions about your claims by
                    running SQL: counts, sums, averages, group-by, trends — e.g.
                    'how many claims by status', 'average estimate by month',
                    'fraud rate by team', 'total reserve on open claims'. Use this
                    when the answer needs math across many claims rather than a
                    single lookup. Automatically scoped to the claims you may see."""
                    if sql_user_id is None:
                        return "Cannot run analytics: no authenticated user."
                    if not database_available():
                        return ("Analytics are unavailable in this deployment: the "
                                "claims database is not reachable from the copilot "
                                "service. Use list_my_claims or queue_metrics instead.")
                    final = _SQL_GRAPH.invoke({
                        "question": question, "user_id": sql_user_id,
                        "role": sql_role, "attempts": 0,
                    })
                    return final.get("answer", "No result.")

                tools = list(tools) + [query_claims_data]
                write_tools = [t for t in tools if t.name in WRITE_TOOLS]
                read_tools = [t for t in tools if t.name not in WRITE_TOOLS]
                llm = AzureChatOpenAI(
                    azure_endpoint=os.getenv("AZURE_OPENAI_ENDPOINT"),
                    api_key=os.getenv("AZURE_OPENAI_KEY"),
                    azure_deployment=os.getenv("AZURE_OPENAI_DEPLOYMENT"),
                    api_version="2024-08-01-preview",
                ).bind_tools(tools)

                def agent_node(state: State):
                    messages = [("system", SYSTEM_PROMPT), *state["messages"]]
                    return {"messages": [llm.invoke(messages)]}

                tool_node = ToolNode(read_tools)
                action_node = ToolNode(write_tools)

                graph_builder = StateGraph(State)
                graph_builder.add_node("agent", agent_node)
                graph_builder.add_node("tools", tool_node)
                graph_builder.add_node("action", action_node)

                graph_builder.add_edge(START, "agent")
                graph_builder.add_conditional_edges("agent", router_after_agent, {"tools": "tools", "action": "action", END: END})
                graph_builder.add_edge("tools", "agent")
                graph_builder.add_edge("action", "agent")

                yield graph_builder.compile(checkpointer=saver, interrupt_before=["action"])

# friendly phrases for the raw MCP tool names, shown as status while a tool runs
TOOL_STATUS = {
    "queue_metrics": "checking your queue metrics",
    "list_my_claims": "looking up your claims",
    "get_claim_status": "looking up that claim",
    "get_my_pending_tasks": "checking your pending tasks",
    "update_claim_status": "preparing the status change",
    "add_note_to_claim": "adding your note",
    "reassign_claim": "reassigning the claim",
    "search_policy_docs": "searching policy documents",
    "search_similar_claims": "searching for similar past claims",
    "query_claims_data": "crunching the numbers",
}


async def stream_chat(graph, message: str, config: dict):
    async for event in graph.astream_events(
        {"messages": [("user", message)]},
        config=config,
        version="v2",
    ):
        kind = event["event"]
        if kind == "on_chat_model_stream":
            # Only stream the top-level agent's tokens. The query_claims_data
            # subgraph runs its own LLM calls (select_tables / generate_sql) that
            # also emit chat-model events; without this filter the raw SQL would
            # leak into the user's answer.
            if event.get("metadata", {}).get("langgraph_node") != "agent":
                continue
            text = event["data"]["chunk"].content
            if text:
                yield {"delta": text}
        elif kind == "on_tool_start":
            phrase = TOOL_STATUS.get(event["name"], f"running {event['name']}")
            yield {"status": f"🔍 {phrase}…"}
        elif kind == "on_tool_end":
            yield {"status": "✍️ writing your answer…"}

    snapshot = await graph.aget_state(config)
    if snapshot.next:
        pending = snapshot.values["messages"][-1].tool_calls[-1]
        yield {
            "done": True,
            "pending": True,
            "action": {"tool": pending["name"], "args": pending["args"]},
        }
    else:
        yield {
            "done": True,
            "pending": False,
            "answer": snapshot.values["messages"][-1].content,
        }
