import os
from contextlib import asynccontextmanager
from typing import Annotated, TypedDict
from dotenv import load_dotenv
load_dotenv()

from langgraph.graph import StateGraph, START, END
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode, tools_condition
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

from langchain_openai import AzureChatOpenAI

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from langchain_mcp_adapters.tools import load_mcp_tools

class State(TypedDict):
    messages: Annotated[list, add_messages]

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
    async with AsyncSqliteSaver.from_conn_string("copilot.db") as saver:
        async with stdio_client(server_params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                tools = await load_mcp_tools(session)
                llm = AzureChatOpenAI(
                    azure_endpoint=os.getenv("AZURE_OPENAI_ENDPOINT"),
                    api_key=os.getenv("AZURE_OPENAI_KEY"),
                    azure_deployment=os.getenv("AZURE_OPENAI_DEPLOYMENT"),
                    api_version="2024-08-01-preview",
                ).bind_tools(tools)

                def agent_node(state: State):
                    return {"messages": [llm.invoke(state["messages"])]}

                tool_node = ToolNode(tools)

                graph_builder = StateGraph(State)
                graph_builder.add_node("agent", agent_node)
                graph_builder.add_node("tools", tool_node)

                graph_builder.add_edge(START, "agent")
                graph_builder.add_conditional_edges("agent", tools_condition)
                graph_builder.add_edge("tools", "agent")

                yield graph_builder.compile(checkpointer=saver)
