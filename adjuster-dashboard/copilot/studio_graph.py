"""Studio-only entry point for `langgraph dev` / LangSmith Studio.

Wraps the SAME agent graph as agent.py, but adapted for the dev server:
  - fetches a dev adjuster token at startup (backend on :8100 must be running)
  - keeps the MCP session alive for the dev server's whole lifetime
    (module-level AsyncExitStack instead of `async with`)
  - compiles WITHOUT a checkpointer (langgraph dev manages its own persistence)

Your real request flow (agent.py + main.py) is untouched. This file exists
only so LangSmith Studio can import and visualize the graph.
"""
import os
from contextlib import AsyncExitStack
from pathlib import Path
from typing import Annotated, TypedDict

import httpx
from dotenv import load_dotenv

# project root .env  (copilot/ -> adjuster-dashboard/ -> homesite-claims-agent/)
load_dotenv(Path(__file__).resolve().parents[2] / ".env")

from langgraph.graph import StateGraph, START
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode, tools_condition
from langchain_openai import AzureChatOpenAI
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from langchain_mcp_adapters.tools import load_mcp_tools

HERE = Path(__file__).resolve().parent
DASHBOARD_URL = os.getenv("DASHBOARD_URL", "http://localhost:8100")
STUDIO_EMAIL = os.getenv("STUDIO_ADJUSTER_EMAIL", "lrobinson@example.com")


class State(TypedDict):
    messages: Annotated[list, add_messages]


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

    llm = AzureChatOpenAI(
        azure_endpoint=os.getenv("AZURE_OPENAI_ENDPOINT"),
        api_key=os.getenv("AZURE_OPENAI_KEY"),
        azure_deployment=os.getenv("AZURE_OPENAI_DEPLOYMENT"),
        api_version="2024-08-01-preview",
    ).bind_tools(tools)

    async def agent_node(state: State):
        return {"messages": [await llm.ainvoke(state["messages"])]}

    gb = StateGraph(State)
    gb.add_node("agent", agent_node)
    gb.add_node("tools", ToolNode(tools))
    gb.add_edge(START, "agent")
    gb.add_conditional_edges("agent", tools_condition)
    gb.add_edge("tools", "agent")
    _graph = gb.compile()  # no checkpointer: langgraph dev provides persistence
    return _graph
