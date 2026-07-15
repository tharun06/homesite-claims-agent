# Plan 2 — NL2SQL Subgraph (structured-data querying as a tool)

## Why this exists

The copilot today has two retrieval modes, and **neither can answer aggregate
questions**:

| Capability | Answers | Example |
|---|---|---|
| REST tools (existing) | single-record lookups, already JWT-scoped | "Status of CLM-146670?" |
| Vector search (existing) | fuzzy/semantic similarity over free text | "Any past claims like this one?" |
| **NL2SQL subgraph (this plan)** | exact aggregates across columns/joins | "Avg estimate for hail claims by team?" |

COUNT / AVG / GROUP BY / JOIN questions are structurally impossible for vector
search or per-record REST lookups. SQL is the only correct tool for that class.

## Architecture: subgraph-as-a-tool

A subgraph is just a `StateGraph.compile()` result — a `Runnable`. We wrap its
`.invoke()` inside a plain `@tool` function (`query_claims_data`) and drop it into
the same tools list as `list_my_claims` / `search_policy_docs`. The **LLM decides**
when to call it. The orchestrator's `agent` / `tools` nodes never change and never
see the subgraph's internals — only a question (string) goes in, an answer (string)
comes out.

NL2SQL deserves a subgraph (not a flat function like `search_similar_claims`)
because it is a **loop**: generate SQL → run → on error, feed the error back and
retry → cap retries → format rows into prose. That is genuine multi-node,
conditional-looping state — exactly what `StateGraph` is for.

---

## Scope: 3 tables, one join chain

```
Claim.adjuster_id → User.id → User.team_id → Team.id
```

This chain answers "avg estimate by team", "which adjuster has most SLA breaches",
"fraud rate by peril". We deliberately leave `policy` / `vehicle` out even though
`Claim` FKs to them — keeping the LLM's world to 3 tables makes generated SQL far
more reliable.

---

## Part 1 — Semantic profiles

Lives in `copilot/schema_profiles.py`. This dict is the **only** view of the DB the
LLM gets. Column descriptions do real work — they encode business logic the raw
schema never states (e.g. what an "SLA breach" is).

```python
# copilot/schema_profiles.py

SEMANTIC_PROFILES = {
    "team": {
        "description": "An adjuster team, organized by US region. Adjusters belong to one team.",
        "columns": {
            "id":     {"type": "int",  "description": "Primary key."},
            "name":   {"type": "str",  "description": "Team name, e.g. 'Team Southeast'. FREE TEXT — ground before filtering."},
            "region": {"type": "str",  "description": "One of: Northeast, Southeast, Midwest, West, Southwest."},
        },
        "relations": ["user.team_id -> team.id (a team has many users)"],
    },
    "user": {
        "description": "A staff member: adjuster, senior adjuster, SIU investigator, or admin. Claims are assigned to users via Claim.adjuster_id.",
        "columns": {
            "id":      {"type": "int",  "description": "Primary key. Join target for claim.adjuster_id."},
            "name":    {"type": "str",  "description": "Full name. FREE TEXT — ground before filtering."},
            "role":    {"type": "str",  "description": "One of: adjuster, senior_adjuster, siu_investigator, admin."},
            "team_id": {"type": "int",  "description": "FK to team.id. NULL for SIU/admin (no team)."},
            "active":  {"type": "bool", "description": "Whether the user is currently employed/active."},
        },
        "relations": [
            "user.team_id -> team.id",
            "claim.adjuster_id -> user.id (a user owns many claims)",
        ],
    },
    "claim": {
        "description": "The core fact table: one auto-insurance claim. Money is in USD. Dates drive SLA logic.",
        "columns": {
            "id":               {"type": "int",   "description": "Primary key."},
            "claim_number":     {"type": "str",   "description": "Human ID, e.g. 'CLM-146670'."},
            "adjuster_id":      {"type": "int",   "description": "FK to user.id -- who owns this claim. USE THIS FOR THE MANDATORY AUTH FILTER."},
            "status":           {"type": "str",   "description": "One of: FNOL, Under Review, Investigation, Appraisal, Pending Approval, Approved, Denied, Closed, SIU Flagged."},
            "peril_type":       {"type": "str",   "description": "One of: Collision, Comprehensive, Theft, Vandalism, Weather, Glass."},
            "loss_date":        {"type": "date",  "description": "When the incident happened."},
            "reported_date":    {"type": "date",  "description": "When it was reported (FNOL)."},
            "incident_city":    {"type": "str",   "description": "City of loss (North Carolina only). FREE TEXT — ground before filtering."},
            "incident_state":   {"type": "str",   "description": "State of loss (always 'NC' in this dataset)."},
            "estimated_amount": {"type": "float", "description": "Estimated repair/loss cost, USD."},
            "reserve_amount":   {"type": "float", "description": "Money reserved against the claim, USD."},
            "approved_amount":  {"type": "float", "description": "Final approved payout, USD. NULL until approved."},
            "deductible":       {"type": "float", "description": "Customer's out-of-pocket, USD."},
            "fraud_score":      {"type": "float", "description": "Model fraud risk 0.0-1.0."},
            "fraud_flagged":    {"type": "bool",  "description": "True if flagged for fraud/SIU review."},
            "sla_due_date":     {"type": "datetime", "description": "Deadline to act. An SLA BREACH = sla_due_date < now AND status NOT IN ('Closed','Denied')."},
            "created_at":       {"type": "datetime", "description": "Row creation time."},
            "updated_at":       {"type": "datetime", "description": "Last update time."},
        },
        "relations": [
            "claim.adjuster_id -> user.id",
            "claim.policy_id -> policy.id (omitted from this profile set)",
            "claim.vehicle_id -> vehicle.id (omitted from this profile set)",
        ],
    },
}
```

---

## Part 2 — Subgraph nodes

```python
class SqlState(TypedDict):
    question: str          # NL question from the orchestrator
    adjuster_id: int       # injected by the tool wrapper — the auth boundary
    samples: dict          # distinct values for free-text cols (grounding)
    sql: str               # current SQL attempt
    rows: list             # query result
    error: str             # last DB error, fed back on retry
    attempts: int          # retry counter
    answer: str            # final structured result out
```

| Step | Node | What it does |
|---|---|---|
| read semantic profiles | *(no node — constant injected into `generate_sql` prompt)* | LLM always sees full profile text. |
| get sample data for WHERE | `ground_values` | `SELECT DISTINCT` only on **free-text** cols (`incident_city`, `team.name`, `user.name`) so the LLM maps "Charlotte"/"West team" -> a real DB value. Enums already in profile -> no probe. |
| generate SQL | `generate_sql` | LLM sees profiles + samples + question + (last error). Emits **read-only SELECT only**. |
| hit DB / get data | `execute_sql` | Runs against `dashboard.db` on a **read-only connection**, with the mandatory `adjuster_id` scope enforced. |
| delete the sample data | *(inside `execute_sql`)* | On success, clear `samples` from state so it never reaches the answer or bloats context. |
| fall back if required | `route_after_execute` (conditional edge) | If `error` and `attempts < 3` -> back to `generate_sql` with the error. If retries exhausted -> `format_answer` with a graceful fallback. |
| send structured data back | `format_answer` | Turns rows into a compact structured reply returned as the tool result. |

**Graph shape:**

```
START -> ground_values -> generate_sql -> execute_sql -> [retry -> generate_sql | ok -> format_answer] -> END
```

Compile it, wrap `.invoke({"question": q, "adjuster_id": me})` in a
`query_claims_data` tool, add to the tools list. Orchestrator never sees the above.

---

## The auth boundary (non-negotiable)

This is the reason it can't be a dumb text->SQL call. Two layers, both required:

1. **Read-only connection** — open `dashboard.db` with `mode=ro` (or
   `PRAGMA query_only=ON`). Even a hallucinated `DROP`/`UPDATE` cannot execute.
2. **Mandatory scope** — the tool wrapper receives the caller's `adjuster_id` from
   the JWT (never from the LLM). A plain adjuster is forced through
   `WHERE claim.adjuster_id = :me`; senior/admin relax this, mirroring
   `scope_claims()`.

**Recommended enforcement:** don't rewrite arbitrary generated SQL (subqueries,
joins, aliases make that fragile). Instead point the read-only connection at a
**pre-scoped SQL view** (`my_claims`) created per-request, so the LLM queries
`my_claims` and physically cannot see rows outside it. Simpler and safer than SQL
surgery.

---

## Architectural notes / deltas from today

- **First direct DB access from the copilot process.** Today `mcp_server.py` has
  zero DB access — every tool goes through the backend over HTTP with the caller's
  JWT (which is how `scope_claims()` enforces row-level auth). This subgraph is the
  first component to open `dashboard.db` directly. Deliberate change; name it.
- Value grounding is only needed for **free-text** columns. Enum columns (status,
  peril_type, role, region) carry their allowed values in the profile statically —
  no live probe.

---

## Build order

1. `copilot/schema_profiles.py` — the profiles dict (Part 1).
2. Read-only connection helper + per-request `my_claims` view.
3. `SqlState` + `ground_values` node.
4. `generate_sql` node (prompt = profiles + samples + question + error).
5. `execute_sql` node + `route_after_execute` retry edge.
6. `format_answer` node; compile subgraph.
7. `query_claims_data` tool wrapper; register in the orchestrator's tools list.
8. Test: adjuster A vs adjuster B scoping holds on an aggregate query.
