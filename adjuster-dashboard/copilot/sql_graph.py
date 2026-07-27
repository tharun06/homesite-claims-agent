"""
The NL2SQL subgraph — assembled node by node.

Step 3: SqlState + ground_values (feed the LLM the DB's real vocabulary).
Step 4: generate_sql + execute_sql + the self-correcting retry route.
"""
import os
import re
from typing import TypedDict

from dotenv import load_dotenv, find_dotenv
from langchain_openai import AzureChatOpenAI
from langgraph.graph import StateGraph, START, END

from schema_profiles import SEMANTIC_PROFILES
from sql_runtime import scoped_connection, distinct_values, run_select

load_dotenv(find_dotenv())

MAX_ATTEMPTS = 3


class SqlState(TypedDict, total=False):
    """The 'clipboard' every node reads from and writes to."""
    question: str      # the English question from the orchestrator
    user_id: int       # who is asking  ─┐ from the JWT — NEVER the LLM
    role: str          # their role      ─┘
    samples: dict      # real distinct values for free-text columns (grounding)
    sql: str           # the SQL the LLM produced
    columns: list      # result column names
    rows: list         # result rows
    error: str         # last DB/validation error, fed back on retry
    attempts: int      # retry counter
    answer: str        # final structured reply out
    samples: dict      # real distinct values for free-text columns (grounding)
    tables: list       # schema-linking: tables chosen as relevant to the question
    sql: str           # the SQL the LLM produced


# ── Node 1: ground_values ────────────────────────────────────────────────────
GROUND_COLUMNS = [
    ("my_claims", "incident_city"),   # scoped: only cities the caller may see
    ("user", "name"),                 # org directory (not per-claim sensitive)
    ("team", "name"),
]


def ground_values(state: SqlState) -> dict:
    """Probe free-text columns for their real distinct values so the LLM writes
    `WHERE incident_city = 'Charlotte'` (what's stored), not a guess."""
    samples: dict = {}
    with scoped_connection(state["user_id"], state["role"]) as conn:
        for source, column in GROUND_COLUMNS:
            samples[f"{source}.{column}"] = distinct_values(conn, source, column, limit=30)
    return {"samples": samples}


# ── Node 2: generate_sql ─────────────────────────────────────────────────────
_llm_cache = {}


def _llm():
    if "llm" not in _llm_cache:
        _llm_cache["llm"] = AzureChatOpenAI(
            azure_endpoint=os.getenv("AZURE_OPENAI_ENDPOINT"),
            api_key=os.getenv("AZURE_OPENAI_KEY"),
            azure_deployment=os.getenv("AZURE_OPENAI_DEPLOYMENT"),
            api_version="2024-08-01-preview",
            temperature=0,          # deterministic SQL
        )
    return _llm_cache["llm"]

# ── Node: select_tables (schema linking) ─────────────────────────────────────
def _table_catalog() -> str:
    """Compact list of table name + one-line description. Stays small even with
    hundreds of tables — so we can send it every call, let the LLM pick, then
    render only the picked tables' full schema in generate_sql."""
    return "\n".join(
        f"- {('my_claims' if t == 'claim' else t)}: {p['description']}"
        for t, p in SEMANTIC_PROFILES.items()
    )

def _parse_tables(text: str) -> list:
    known = set(SEMANTIC_PROFILES)                 # {'claim','user','team'}
    picked = []
    for tok in re.split(r"[,\n]", text):
        t = tok.strip().lower().strip("`\"'.-* ")
        if t == "my_claims":
            t = "claim"
        if t in known and t not in picked:
            picked.append(t)
    return picked or list(SEMANTIC_PROFILES)       # fallback: use everything

_SELECT_SYSTEM = (
    "You pick which database tables are needed to answer a question. Return ONLY "
    "a comma-separated list of table names from the catalog, nothing else."
)

def select_tables(state: SqlState) -> dict:
    """Choose only the tables relevant to the question so generate_sql renders a
    FOCUSED schema. Trivial at 3 tables; it's what keeps accuracy and cost sane
    as the schema grows to dozens/hundreds of tables."""
    human = f"Catalog:\n{_table_catalog()}\n\nQuestion: {state['question']}"
    resp = _llm().invoke([("system", _SELECT_SYSTEM), ("human", human)])
    return {"tables": _parse_tables(resp.content)}


def _render_schema(tables=None) -> str:
    """Turn the semantic profiles into prompt text. The `claim` table is
    presented AS `my_claims` — the scoped view the LLM must query."""
    keys = tables or list(SEMANTIC_PROFILES)      # only the chosen tables
    out = []
    for tname in keys:
        prof = SEMANTIC_PROFILES[tname]
        name = "my_claims" if tname == "claim" else tname
        out.append(f"TABLE {name}: {prof['description']}")
        if tname == "claim":
            out.append("  NOTE: my_claims is a per-user SCOPED VIEW with all claim columns —")
            out.append("        it is already filtered to what this user may see.")
        for col, meta in prof["columns"].items():
            out.append(f"  - {col} ({meta['type']}): {meta['description']}")
        for rel in prof["relations"]:
            out.append(f"  join: {rel.replace('claim.', 'my_claims.')}")
        out.append("")
    return "\n".join(out)


_SYSTEM = (
    "You are a SQLite expert. Write ONE read-only SELECT that answers the question.\n"
    "Rules:\n"
    "- Query the `my_claims` view for all claim data. NEVER reference a table named `claim`.\n"
    "- You may JOIN to `user` (my_claims.adjuster_id = user.id) and `team` (user.team_id = team.id).\n"
    "- my_claims is ALREADY scoped to the current user. Do NOT add any adjuster_id/user filter "
    "for permission reasons; only filter adjuster_id if the question names a specific adjuster.\n"
    "- Use the EXACT stored strings from the schema enums and the sample values "
    "(e.g. status = 'UNDER_REVIEW', not 'Under Review').\n"
    "- Return ONLY the SQL. No explanation, no markdown fences."
)


def _extract_sql(text: str) -> str:
    t = text.strip()
    m = re.search(r"```(?:sql)?\s*(.*?)```", t, re.S | re.I)
    if m:
        t = m.group(1)
    return t.strip().rstrip(";").strip()


def generate_sql(state: SqlState) -> dict:
    """Hand the LLM the schema + grounded samples + question (+ any prior error)
    and get back a single SELECT."""
    samples = "\n".join(
        f"  {k}: {', '.join(map(str, v))}" for k, v in state.get("samples", {}).items()
    )
    human = (
        f"Schema:\n{_render_schema(state.get('tables'))}\n"
        f"Sample values (use these exact strings):\n{samples}\n\n"
        f"Question: {state['question']}"
    )
    if state.get("error"):
        human += (
            f"\n\nYour previous query FAILED.\nSQL: {state.get('sql','')}\n"
            f"Error: {state['error']}\nFix it and return corrected SQL only."
        )
    resp = _llm().invoke([("system", _SYSTEM), ("human", human)])
    return {"sql": _extract_sql(resp.content)}


# ── Node 3: execute_sql ──────────────────────────────────────────────────────
def execute_sql(state: SqlState) -> dict:
    """Run the SQL through the safe runtime (read-only + scoped view + guard).
    On any failure, record the error and bump the attempt counter."""
    try:
        with scoped_connection(state["user_id"], state["role"]) as conn:
            cols, rows = run_select(conn, state["sql"])
        return {"columns": cols, "rows": rows, "error": ""}
    except Exception as e:
        return {"error": str(e), "attempts": state.get("attempts", 0) + 1}


# ── conditional edge: retry or move on ───────────────────────────────────────
def route_after_execute(state: SqlState) -> str:
    if not state.get("error"):
        return "format_answer"                       # success
    if state.get("attempts", 0) < MAX_ATTEMPTS:
        return "generate_sql"                        # self-correct with the error
    return "format_answer"                           # give up gracefully


# ── Node 4: format_answer ────────────────────────────────────────────────────
def format_answer(state: SqlState) -> dict:
    """Turn raw rows into a clean, compact result the orchestrator can narrate.
    Also handles the fallback case (retries exhausted) gracefully."""
    if state.get("error"):
        return {"answer": f"Could not answer this query after {MAX_ATTEMPTS} attempts "
                          f"(last error: {state['error']})."}
    cols, rows = state.get("columns", []), state.get("rows", [])
    if not rows:
        return {"answer": "No matching claims found."}
    table = " | ".join(cols) + "\n" + "\n".join(
        " | ".join(str(x) for x in r) for r in rows[:50]
    )
    return {"answer": f"{len(rows)} row(s):\n{table}"}


# ── assemble + compile the subgraph ──────────────────────────────────────────
def build_sql_graph():
    """Wire the four nodes into a compiled LangGraph.

    START → ground_values → generate_sql → execute_sql →(router)→ generate_sql (retry)
                                                        └────────→ format_answer → END
    """
    g = StateGraph(SqlState)
    g.add_node("ground_values", ground_values)
    g.add_node("select_tables", select_tables)
    g.add_node("generate_sql", generate_sql)
    g.add_node("execute_sql", execute_sql)
    g.add_node("format_answer", format_answer)

    g.add_edge(START, "ground_values")            # entry
    g.add_edge("ground_values", "select_tables")        # was: ground_values → generate_sql
    g.add_edge("select_tables", "generate_sql")         # NEW   # always ground first
    g.add_edge("generate_sql", "execute_sql")     # then run
    g.add_conditional_edges(                       # then branch on the result
        "execute_sql",
        route_after_execute,
        {"generate_sql": "generate_sql", "format_answer": "format_answer"},
    )
    g.add_edge("format_answer", END)              # exit
    return g.compile()                            # no checkpointer: runs start→end in one call


if __name__ == "__main__":
    graph = build_sql_graph()
    for question in [
        "How many of my claims are in Charlotte?",
        "What's my average estimated amount by status, highest first?",
        "How many fraud-flagged claims do I have and what's their total reserve?",
    ]:
        # invoke the COMPILED graph — LangGraph runs every node for us
        final = graph.invoke(
            {"question": question, "user_id": 1, "role": "adjuster", "attempts": 0}
        )
        print("Q:", question)
        print("  SQL:", final["sql"].replace("\n", " "))
        print("  ANSWER:", final["answer"].replace("\n", " | "))
        print()
