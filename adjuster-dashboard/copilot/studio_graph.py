"""Studio-only entry point for `langgraph dev` / LangSmith Studio.

Wraps the SAME agent graph as agent.py, but adapted for the dev server:
  - fetches a dev adjuster token at startup (backend on :8100 must be running)
  - keeps the MCP session alive for the dev server's whole lifetime
    (module-level AsyncExitStack instead of `async with`)
  - compiles WITHOUT an explicit checkpointer (langgraph dev provides its own
    persistence automatically, including for interrupt_before pause/resume)

Kept in sync with agent.py: same router, same action-node gate, same system
prompt. Your real request flow (agent.py + main.py) is untouched. This file
exists only so LangSmith Studio can import and visualize the graph.
"""
import os
from contextlib import AsyncExitStack
from pathlib import Path
from typing import Annotated, TypedDict

import httpx
from dotenv import load_dotenv

# project root .env  (copilot/ -> adjuster-dashboard/ -> homesite-claims-agent/)
load_dotenv(Path(__file__).resolve().parents[2] / ".env")

from langgraph.graph import StateGraph, START, END
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode
from langchain_openai import AzureChatOpenAI
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from langchain_mcp_adapters.tools import load_mcp_tools

HERE = Path(__file__).resolve().parent
DASHBOARD_URL = os.getenv("DASHBOARD_URL", "http://localhost:8100")
STUDIO_EMAIL = os.getenv("STUDIO_ADJUSTER_EMAIL", "howardmaurice@example.com")  # Gabrielle Davis

WRITE_TOOLS = {"update_claim_status"}

SYSTEM_PROMPT = (
    "You are an assistant for insurance adjusters. When you use information "
    "from search_policy_docs, you MUST name the source document explicitly "
    "(e.g. 'per adjuster-authority-matrix.txt') and, if a numeric limit or "
    "threshold is relevant, state the exact number and directly compare it "
    "to the user's figures."
)


class State(TypedDict):
    messages: Annotated[list, add_messages]


def router_after_agent(state: State):
    last_message = state["messages"][-1]
    if not last_message.tool_calls:
        return END
    last_tool_call = last_message.tool_calls[-1]
    if last_tool_call["name"] in WRITE_TOOLS:
        return "action"
    return "tools"


# kept alive for the dev server's lifetime so the MCP subprocess never closes
_stack: AsyncExitStack | None = None
_graph = None


async def _get_dev_token() -> str:
    """Log in as the demo adjuster to get a JWT for the MCP tools."""
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{DASHBOARD_URL}/auth/login", data={"email": STUDIO_EMAIL}
        )
        resp.raise_for_status()
        return resp.json()["access_token"]


async def make_graph(config=None):
    """Async graph factory referenced by langgraph.json. Builds once, reuses after."""
    global _stack, _graph
    if _graph is not None:
        return _graph

    token = await _get_dev_token()
    server_params = StdioServerParameters(
        command="python",
        args=[str(HERE / "mcp_server.py")],
        env={
            **os.environ,
            "DASHBOARD_URL": DASHBOARD_URL,
            "ADJUSTER_TOKEN": token,
        },
    )

    _stack = AsyncExitStack()
    read, write = await _stack.enter_async_context(stdio_client(server_params))
    session = await _stack.enter_async_context(ClientSession(read, write))
    await session.initialize()
    tools = await load_mcp_tools(session)
    write_tools = [t for t in tools if t.name in WRITE_TOOLS]
    read_tools = [t for t in tools if t.name not in WRITE_TOOLS]

    llm = AzureChatOpenAI(
        azure_endpoint=os.getenv("AZURE_OPENAI_ENDPOINT"),
        api_key=os.getenv("AZURE_OPENAI_KEY"),
        azure_deployment=os.getenv("AZURE_OPENAI_DEPLOYMENT"),
        api_version="2024-08-01-preview",
    ).bind_tools(tools)

    async def agent_node(state: State):
        messages = [("system", SYSTEM_PROMPT), *state["messages"]]
        return {"messages": [await llm.ainvoke(messages)]}

    gb = StateGraph(State)
    gb.add_node("agent", agent_node)
    gb.add_node("tools", ToolNode(read_tools))
    gb.add_node("action", ToolNode(write_tools))
    gb.add_edge(START, "agent")
    gb.add_conditional_edges("agent", router_after_agent, {"tools": "tools", "action": "action", END: END})
    gb.add_edge("tools", "agent")
    gb.add_edge("action", "agent")
    _graph = gb.compile(interrupt_before=["action"])
    return _graph
