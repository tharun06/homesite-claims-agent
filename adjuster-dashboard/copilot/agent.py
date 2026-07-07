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

class State(TypedDict):
    messages: Annotated[list, add_messages]

WRITE_TOOLS = {"update_claim_status"}

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
    last_tool_call = last_message.tool_calls[-1]
    if last_tool_call["name"] in WRITE_TOOLS:
        return "action"
    return "tools"

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

    async with AsyncSqliteSaver.from_conn_string("copilot.db") as saver:
        async with stdio_client(server_params) as (read, write):
            async with ClientSession(read, write) as session:
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
}


async def stream_chat(graph, message: str, config: dict):
    async for event in graph.astream_events(
        {"messages": [("user", message)]},
        config=config,
        version="v2",
    ):
        kind = event["event"]
        if kind == "on_chat_model_stream":
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
