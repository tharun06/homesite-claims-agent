# Phase 2 — Adjuster Copilot (LangGraph): Build-It-Yourself Guide

> **Mode:** you write the code. This doc tells you *what to do*, *what to write*,
> and *which libraries to pull* for each step — but never writes the code for you.
> Nothing is pre-built. Your starting point is `main.py` (hello world).

> Status: ⬜ not started · 🔨 in progress · ✅ done

---

## 1. What we're building (and why it helps the adjuster)

An embedded copilot the adjuster chats with. It can **answer questions** about
their claims, **take one action** (change a claim's status), **remember
conversations**, and **always ask before changing anything**. It reuses what
exists — the backend API (`:8100`, JWT-scoped per adjuster) and the frontend
Copilot page that POSTs to `/chat`.

## 2. Current state

- `adjuster-dashboard/copilot/` contains only: `main.py` (hello-world FastAPI on
  `:8200`), `README.md`, `PLAN.md`. No implementation yet — you write it.
- Reused, unchanged: backend `:8100`, frontend Copilot page (`:5173`).
- Not a git repo — be deliberate about deletes.

## 3. Target architecture

```
 Frontend Copilot.jsx ──POST /chat──►  Copilot service  (FastAPI :8200)
                                        │
                                        ▼
                                  LangGraph StateGraph ──checkpointer──► copilot.db
                                   (agent ⇄ tools)        (saved history, per thread_id)
                                        │  ▲
                            interrupt_before│ │ (human-in-the-loop on write actions)
                                        ▼  │
                                   MCP client ──stdio──► MCP server (FastMCP)
                                                              │  carries ADJUSTER_TOKEN
                                                              ▼
                                              Dashboard API  (:8100, role-scoped)
```

## 4. Milestones (you build each)

| # | Milestone | File you create | Status |
|---|-----------|-----------------|--------|
| 0 | Seed | `main.py` (exists) | ✅ |
| A | MCP tool layer | `mcp_server.py` (+ optional `test_mcp.py`) | ⬜ |
| B | LangGraph agent + checkpointer | `agent.py`, edit `main.py` | ⬜ |
| C | Human-in-the-loop on the write tool | edit `agent.py` + `main.py` | ⬜ |
| D | Polish (optional) | claim context, streaming, FE approval card | ⬜ |

---

## 5. How to build each step

### Step A — MCP tool layer (`mcp_server.py`)

**Goal:** expose your dashboard API as a handful of typed tools, carrying the
adjuster's JWT so every call is auto-scoped.

**Libraries (all installed):** `mcp` (use `FastMCP` from `mcp.server.fastmcp`),
`httpx`, plus stdlib `os`, `json`.

**What to write:**
1. Read two env vars: `DASHBOARD_URL` (default `http://localhost:8100`) and
   `ADJUSTER_TOKEN` (the JWT, supplied by whoever launches the server).
2. Create one server object: `FastMCP("homesite-claims")`.
3. A small helper that GETs a backend path with header
   `Authorization: Bearer <ADJUSTER_TOKEN>` and returns `.json()`.
4. A helper to resolve a human claim number (`CLM-123456`) → its summary record
   (call `GET /claims?search=...&limit=5`, match `claim_number`; the record
   carries the numeric `id` you need for writes).
5. Define tools by decorating plain functions with `@mcp.tool()`. The function's
   **type hints become the input schema** and its **docstring tells the LLM when
   to use it** — so write clear docstrings. Return a JSON string.
   - Read tools: `list_my_claims(status="", search="")` → `GET /claims`;
     `get_claim_status(claim_number)` → resolve then return it;
     `my_pending_tasks()` → `GET /me/tasks?days=30` (filter Pending/In Progress/Overdue);
     `queue_metrics()` → `GET /metrics/queue`.
   - Write tool: `update_claim_status(claim_number, new_status)` →
     `PATCH /claims/{id}/status`. **Send form data** (`data={"new_status": ...}`),
     not JSON — the endpoint uses a form field. Validate `new_status` against the
     allowed list first (see §6) and return a clear error if invalid.
6. End with `if __name__ == "__main__": mcp.run()` (runs over stdio).

**How to verify:** write a tiny client (optional `test_mcp.py`) using
`ClientSession` + `StdioServerParameters` from `mcp`, and `stdio_client` from
`mcp.client.stdio`. Steps: log in via `POST /auth/login` (form field `email`,
pick an adjuster from `GET /auth/users`) to get a JWT; spawn `mcp_server.py` with
`ADJUSTER_TOKEN` in its env; `await session.initialize()`;
`await session.list_tools()`; `await session.call_tool("queue_metrics", {})`.
Don't call the write tool yet — it mutates a claim and only runs behind approval
(Step C). Backend must be running on `:8100`.

### Step B — LangGraph agent + checkpointer (`agent.py`, edit `main.py`)

**Goal:** an agent that loops *LLM ⇄ tools*, persists every conversation, and is
reachable at `POST /chat`.

**Libraries:** `pip install langchain-mcp-adapters` (the only missing one).
Already installed: `langgraph` (`StateGraph`, `START`, `END` from
`langgraph.graph`; `ToolNode`, `tools_condition` from `langgraph.prebuilt`),
`SqliteSaver` from `langgraph.checkpoint.sqlite`, `AzureChatOpenAI` from
`langchain_openai`.

**What to write:**
1. **State**: a `TypedDict` with one field `messages`, annotated with the
   `add_messages` reducer (`from langgraph.graph.message import add_messages`).
   The reducer is what makes each turn *append* to the conversation.
2. **Tools from MCP**: open an MCP `ClientSession` (same stdio spawn as Step A),
   then `load_mcp_tools(session)` from `langchain_mcp_adapters.tools` to get
   LangChain-compatible tools. Manage the session's lifetime with an
   `AsyncExitStack` so it stays open while the service runs.
3. **Model**: build `AzureChatOpenAI` from the root `.env` vars
   (`AZURE_OPENAI_ENDPOINT`, `AZURE_OPENAI_KEY`, `AZURE_OPENAI_DEPLOYMENT`,
   `api_version="2024-08-01-preview"`). Call `model.bind_tools(tools)`.
4. **Nodes**: an `agent` node that invokes the bound model on `state["messages"]`
   and returns the new message; a `tools` node = `ToolNode(tools)`.
5. **Edges**: `START → agent`; conditional edge from `agent` using
   `tools_condition` (routes to `tools` if the model asked for a tool, else
   `END`); `tools → agent` to close the loop.
6. **Checkpointer**: compile with `graph.compile(checkpointer=saver)`.
   ⚠️ Gotcha: in current LangGraph, `SqliteSaver.from_conn_string(...)` is a
   *context manager*. For a long-lived FastAPI app it's simpler to build a
   `sqlite3` connection yourself (`check_same_thread=False`) and pass it to
   `SqliteSaver(conn)`. Use a file like `copilot.db`.
7. **/chat**: in `main.py`, accept `{message, thread_id}` + the `Authorization`
   header. Call `graph.invoke({"messages":[("user", message)]},
   config={"configurable": {"thread_id": thread_id}})`. The `thread_id` is the
   conversation key — same id later = resumed history. Return the last message.

**How to verify:** run `uvicorn main:app --port 8200 --reload`; `curl` a question
like "what are my pending tasks?" with a `thread_id`; ask a follow-up with the
*same* `thread_id` and confirm it remembers; restart the service and confirm the
thread still resumes (proves the checkpointer persisted it).

### Step C — Human-in-the-loop on the write tool (edit `agent.py` + `main.py`)

**Goal:** before `update_claim_status` runs, the graph pauses, surfaces the
proposed action, and only proceeds on your approval.

**Library concepts:** LangGraph `interrupt_before` (set at `compile`) + the
checkpointer (already there) to pause and resume.

**What to write:**
1. **Separate the write tool** into its own node (e.g. an `action` node) so reads
   still run freely; route the model's tool call to `action` when the tool name
   is in your write-tools set, else to the normal `tools` node.
2. Compile with `interrupt_before=["action"]`. Now when the model wants to change
   a status, `graph.invoke(...)` stops *before* executing it; inspect
   `graph.get_state(config)` to read the pending tool call (claim + new status).
3. **/chat** returns a `pending_action` payload instead of a final answer when an
   interrupt is hit.
4. **/chat/approve**: resume by calling `graph.invoke(None, config)` (None = "keep
   going from the checkpoint"). **/chat/reject**: write a tool message saying the
   user declined (via `graph.update_state`) then resume, so the model explains it
   didn't act.

**How to verify:** ask "move CLM-… to Pending Approval" → `/chat` returns a
pending action → call `/chat/approve` → confirm the status actually changed by
reading the claim's events (`GET /claims/{id}/events`). Then try a `/chat/reject`
on another and confirm nothing changed.

---

## 6. MCP tools reference

| Tool | Kind | Wraps | Helps the adjuster |
|------|------|-------|--------------------|
| `list_my_claims(status, search)` | read | `GET /claims` | see their queue at a glance |
| `get_claim_status(claim_number)` | read | `GET /claims` (resolve) | check one claim's details |
| `my_pending_tasks()` | read | `GET /me/tasks` | know what's pending / overdue |
| `queue_metrics()` | read | `GET /metrics/queue` | totals, SLA breaches, fraud count |
| `update_claim_status(claim_number, new_status)` | **write** | `PATCH /claims/{id}/status` | move a claim forward — with approval |

Valid statuses: `FNOL, Under Review, Investigation, Appraisal, Pending Approval,
Approved, Denied, Closed, SIU Flagged`.

Future write tools (same pattern, later): `reassign_claim`
(`POST /claims/{id}/reassign`, senior/admin only), `add_claim_note`
(`POST /claims/{id}/notes`).

## 7. Requirements (each framed by what it does for the user)

1. **Ask in plain English, get accurate live answers** — no digging through
   screens. *(A–B)*
2. **Have the copilot perform a routine action** (update status) — busywork is
   faster. *(C)*
3. **Be asked to approve every change before it happens** — stay in control. *(C)*
4. **Conversations are saved and resumable** — pick up where I left off. *(B)*
5. **Copilot only sees/touches what I'm allowed to** — JWT scoping flows through
   every tool. *(A)*
6. **(Phase 3) Ask policy questions, get cited answers** — consistent,
   defensible decisions. *(Azure AI Search RAG, later)*

## 8. Dependencies

Already installed (`python3.11`): fastapi, uvicorn, openai, **mcp**, **langgraph**
(+ `SqliteSaver`), **langchain-openai**, httpx.
**To install when you start Step B:** `pip install langchain-mcp-adapters`.

## 9. Decisions made

- Write tool for the HITL demo = `update_claim_status` (real, audited, allowed for
  a regular adjuster).
- LLM = existing Azure OpenAI deployment (root `.env`).
- MCP transport = stdio (service spawns the MCP server; JWT flows in cleanly).
- Checkpointer = `SqliteSaver` → `copilot.db`.

## 10. Phase 3 (deferred): RAG with Azure AI Search

Index policy docs + claims records; add retrieval tools (`search_policy_docs`,
`search_similar_claims`) as just more MCP tools. Hybrid/semantic search on Azure
AI Search, Azure OpenAI embeddings. Helps the adjuster get cited, precedent-aware
answers.

## 11. Progress log

- Step 0 — seed `main.py` present. ✅
- Step A — not started (you write `mcp_server.py`). ⬜
```
