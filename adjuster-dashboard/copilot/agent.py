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

# ── context budget ───────────────────────────────────────────────────────────
# A turn is not one LLM call. The agent node runs, a tool runs, the agent node
# runs again — and every one of those calls re-sends the whole message list.
# So a tool result is not paid for once; it is paid for on every subsequent call
# for the rest of the conversation.
#
# Measured on this codebase:
#
#   all 9 tool schemas        522 tokens   (small — not worth optimising)
#   ONE search_policy_docs  ~2,560 tokens   (5 chunks x 512)
#
# The retrieval result costs 5x the entire tool surface, and it is the thing
# that repeats. By turn five, four stale retrievals are being re-sent on every
# call — ~10,000 tokens of chunks the model already read and summarised.
#
# Two rules below, in order of how much they save:
#
#   1. truncate OLD tool results (everything before the current turn)
#   2. then hold the whole thing under a token budget
#
# IMPORTANT: this trims what we SEND, never what we STORE. The checkpointer keeps
# the full history — that is the audit trail, and it is what lets us pull the
# exact state of any conversation by thread_id when a user reports a bad answer.
# Pruning state itself (e.g. RemoveMessage) would buy the same tokens and cost us
# that, and it risks breaking resume-from-interrupt, which depends on the message
# structure being intact.
TOOL_RESULT_KEEP_CHARS = int(os.getenv("COPILOT_TOOL_RESULT_KEEP", "240"))
CONTEXT_BUDGET_TOKENS = int(os.getenv("COPILOT_CONTEXT_BUDGET", "12000"))

try:
    import tiktoken
    _ENC = tiktoken.get_encoding("cl100k_base")
    def _tok(text: str) -> int:
        return len(_ENC.encode(text))
except Exception:                      # tiktoken absent — approximate
    def _tok(text: str) -> int:
        return len(text) // 4


def _count_tokens(messages) -> int:
    """Rough token count for a message list. Only needs to be good enough to
    decide what to drop, so it approximates the per-message overhead rather than
    reproducing the exact chat-format accounting."""
    total = 0
    for m in messages:
        content = getattr(m, "content", "") or ""
        total += _tok(content if isinstance(content, str) else str(content)) + 4
        for call in (getattr(m, "tool_calls", None) or []):
            total += _tok(str(call.get("args", ""))) + 10
    return total


def _truncate_old_tool_results(messages: list) -> list:
    """Shorten tool output from PREVIOUS turns; leave the current turn intact.

    Truncating the content rather than dropping the message is deliberate. The
    chat API requires every ToolMessage to match a preceding tool_call id — drop
    one side of that pair and the next request fails with a 400. Editing content
    in place keeps every pair valid, and still removes almost all the bulk.

    Copies rather than mutates: these objects are the ones held in graph state,
    and editing them in place would corrupt the stored history we just said we
    were preserving."""
    from langchain_core.messages import HumanMessage, ToolMessage

    last_human = max(
        (i for i, m in enumerate(messages) if isinstance(m, HumanMessage)),
        default=-1,
    )
    out = []
    for i, m in enumerate(messages):
        is_old_tool_result = (
            i < last_human
            and isinstance(m, ToolMessage)
            and isinstance(m.content, str)
            and len(m.content) > TOOL_RESULT_KEEP_CHARS
        )
        if is_old_tool_result:
            dropped = len(m.content) - TOOL_RESULT_KEEP_CHARS
            out.append(m.model_copy(update={
                "content": m.content[:TOOL_RESULT_KEEP_CHARS]
                + f"\n…[{dropped} chars of earlier tool output trimmed from context."
                  " Call the tool again if you need the full detail.]"
            }))
        else:
            out.append(m)
    return out


def _prepare_context(messages: list) -> list:
    """What the LLM actually sees. Never what we store."""
    trimmed = _truncate_old_tool_results(messages)
    try:
        from langchain_core.messages import trim_messages
        # start_on="human" is the safety property: it guarantees the window can
        # never begin on an orphaned ToolMessage whose matching tool_call was cut.
        kept = trim_messages(
            trimmed,
            max_tokens=CONTEXT_BUDGET_TOKENS,
            strategy="last",
            token_counter=_count_tokens,
            start_on="human",
            allow_partial=False,
        )
        return kept or trimmed[-2:]      # never send an empty context
    except Exception:
        # Budgeting is an optimisation. If it ever fails, send the full list and
        # let the model sort it out — degrading to "expensive" beats "broken".
        return trimmed


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


@asynccontextmanager
async def _checkpointer():
    """The graph's memory: after every node LangGraph saves state here, which is
    what lets the graph PAUSE at the `action` node and later resume on /approve.

    Postgres when CHECKPOINT_DATABASE_URL is set (deployed), SQLite otherwise
    (local dev). Postgres matters because container storage is ephemeral: with a
    local file, conversation history and any pending approval die on restart, and
    two replicas cannot see each other's state.

    Note the DSN must be the plain `postgresql://` form — the saver drives psycopg
    directly, so SQLAlchemy's `postgresql+psycopg://` prefix is not valid here.

    WINDOWS: leave CHECKPOINT_DATABASE_URL unset locally. async psycopg refuses to
    run on asyncio's ProactorEventLoop, but that is exactly the loop Windows needs
    in order to spawn the stdio MCP server subprocess — SelectorEventLoop cannot
    create subprocesses on Windows. The two requirements are mutually exclusive
    there, so local dev uses the SQLite saver. Linux containers have no such
    conflict and use Postgres.
    """
    url = os.getenv("CHECKPOINT_DATABASE_URL", "")
    if url.startswith("postgres"):
        from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
        async with AsyncPostgresSaver.from_conn_string(url) as saver:
            # creates the checkpoints / checkpoint_writes / checkpoint_blobs
            # tables. The SQLite saver does this implicitly; Postgres needs it
            # called explicitly, and it is safe to re-run.
            await saver.setup()
            yield saver
    else:
        async with AsyncSqliteSaver.from_conn_string(
            os.getenv("COPILOT_DB", "copilot.db")
        ) as saver:
            yield saver


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

    async with _checkpointer() as saver:
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
                    # A tool must never kill the whole conversation. The DB can be
                    # present but unusable — e.g. SQLite on an Azure Files (SMB)
                    # share raises "database is locked" because SMB does not
                    # support the POSIX locks SQLite needs. Report it and let the
                    # agent carry on with its other tools.
                    try:
                        final = _SQL_GRAPH.invoke({
                            "question": question, "user_id": sql_user_id,
                            "role": sql_role, "attempts": 0,
                        })
                    except Exception as e:
                        return (f"Analytics are unavailable right now ({type(e).__name__}: {e}). "
                                "Answer from list_my_claims or queue_metrics instead.")
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
                    # _prepare_context bounds what we SEND. state["messages"] —
                    # and therefore the checkpoint — keeps everything.
                    messages = [("system", SYSTEM_PROMPT), *_prepare_context(state["messages"])]
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
