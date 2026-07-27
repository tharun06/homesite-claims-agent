"""
Safe SQL runtime for the NL2SQL subgraph.

This is the boundary that makes it acceptable to let an LLM write SQL against
the real claims database. Three layers of defense, strongest first:

  1. READ-ONLY CONNECTION — SQLite itself rejects every write. Even a
     hallucinated `DROP TABLE claim` fails at the engine level.
  2. SCOPED VIEW — per request we build a `my_claims` TEMP VIEW that already
     contains only the rows this caller may see. The LLM queries `my_claims`
     and never names the raw `claim` table. Temp views are connection-local,
     so each request is isolated with zero concurrency risk.
  3. STATEMENT GUARD — `run_select()` rejects anything that isn't a single
     read-only SELECT, and rejects any query that reaches past the view to the
     base `claim` table.

Layers 1 and 2 are hard guarantees enforced by SQLite. Layer 3 is a string
heuristic (a production system would parse the SQL with e.g. sqlglot); but note
that even if a query slipped past it, the read-only connection means it could
only ever READ, never modify.
"""
import os
import re
import sqlite3
from contextlib import contextmanager

# dashboard.db lives in the backend; allow an env override for other environments.
DB_PATH = os.environ.get("DASHBOARD_DB") or os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "backend", "dashboard.db")
)


def database_available() -> bool:
    """True if the claims database file is reachable from this process.

    NL2SQL reads the backend's SQLite file directly off the filesystem, which only
    works when both run on the same machine. Once the copilot is deployed as its
    own container the file is not there, so callers check this first and report a
    clear message instead of throwing 'unable to open database file'. The real fix
    is moving to a networked database (see ROADMAP.md, Phase 2).
    """
    return os.path.exists(DB_PATH)


# ── Layer 1: the read-only connection ────────────────────────────────────────
def _connect_ro() -> sqlite3.Connection:
    """Open dashboard.db read-only. The `mode=ro` URI makes SQLite refuse every
    write at the engine level — this is a hard guarantee, not a convention."""
    return sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)


# ── Layer 2: the scope predicate (mirrors the backend's scope_claims()) ───────
def _scope_predicate(user_id: int, role: str) -> str:
    """Return the SQL WHERE clause that limits `claim` to what this caller may
    see. Mirrors app/scoping.py::scope_claims exactly. `user_id` is coerced to
    int, so inlining it into SQL is injection-safe (it can only ever be a
    number, never LLM- or user-controlled text)."""
    uid = int(user_id)                 # coerce — never trust it as a string
    r = (role or "").strip().lower()   # accepts 'ADJUSTER' or 'adjuster'

    if r == "admin":
        return "1=1"                                       # sees everything
    if r == "siu_investigator":
        return "fraud_flagged = 1"                         # fraud queue only
    if r == "senior_adjuster":
        # the whole team: any adjuster sharing this lead's team
        return (
            "adjuster_id IN (SELECT id FROM user WHERE team_id = "
            f"(SELECT team_id FROM user WHERE id = {uid}))"
        )
    # default = plain ADJUSTER = least privilege = only their own book
    return f"adjuster_id = {uid}"


# ── Layer 2: per-request scoped connection ───────────────────────────────────
@contextmanager
def scoped_connection(user_id: int, role: str):
    """Yield a read-only connection with a `my_claims` view already scoped to
    the caller. Use it as:  `with scoped_connection(uid, role) as conn: ...`.
    The view is a TEMP object — it exists only for this connection and vanishes
    when it closes, so there is no shared state between requests."""
    conn = _connect_ro()
    try:
        predicate = _scope_predicate(user_id, role)
        conn.execute(f"CREATE TEMP VIEW my_claims AS SELECT * FROM claim WHERE {predicate}")
        yield conn
    finally:
        conn.close()


# ── Layer 3: the statement guard ─────────────────────────────────────────────
_FORBIDDEN = re.compile(
    r"\b(insert|update|delete|drop|alter|create|attach|detach|replace|pragma|vacuum|reindex)\b",
    re.IGNORECASE,
)
# \b...\b matches the standalone word "claim" but NOT "my_claims" or
# "claim_number" (underscore is a word char, so there's no boundary there).
_BASE_TABLE = re.compile(r"\bclaim\b", re.IGNORECASE)


def run_select(conn: sqlite3.Connection, sql: str, limit: int = 200):
    """Validate then execute a single read-only SELECT. Returns
    (column_names, rows). Raises ValueError if the SQL fails any check."""
    stmt = sql.strip().rstrip(";").strip()

    if ";" in stmt:                                   # blocks `SELECT 1; DROP ...`
        raise ValueError("Only a single statement is allowed.")
    low = stmt.lower()
    if not (low.startswith("select") or low.startswith("with")):
        raise ValueError("Only SELECT / WITH queries are allowed.")
    if _FORBIDDEN.search(stmt):
        raise ValueError("Query contains a forbidden (write/DDL) keyword.")
    if _BASE_TABLE.search(stmt):
        raise ValueError("Query must use the my_claims view, not the base claim table.")

    cur = conn.execute(stmt)
    cols = [d[0] for d in cur.description] if cur.description else []
    rows = [list(r) for r in cur.fetchmany(limit)]
    return cols, rows


# ── helper used later by the grounding node (step 3) ─────────────────────────
def distinct_values(conn: sqlite3.Connection, source: str, column: str, limit: int = 25):
    """Distinct values of a free-text column, so the LLM can map user phrasing
    ('Charlotte') to a real stored value. This runs OUR SQL, not the LLM's, so
    it may read `my_claims` (scoped) or a reference table (user/team) directly."""
    rows = conn.execute(
        f'SELECT DISTINCT "{column}" FROM {source} '
        f'WHERE "{column}" IS NOT NULL ORDER BY 1 LIMIT {int(limit)}'
    ).fetchall()
    return [r[0] for r in rows]


# ── runnable smoke test: prove scoping + write-blocking ──────────────────────
if __name__ == "__main__":
    print(f"DB: {DB_PATH}\n")
    # role -> a sample user_id to test with (adjuster 1, senior, siu, admin)
    with _connect_ro() as c:
        total = c.execute("SELECT COUNT(*) FROM claim").fetchone()[0]
    print(f"total claims in DB: {total}\n")

    for role, uid in [("adjuster", 1), ("senior_adjuster", 16), ("siu_investigator", 21), ("admin", 24)]:
        with scoped_connection(uid, role) as conn:
            n = conn.execute("SELECT COUNT(*) FROM my_claims").fetchone()[0]
            print(f"{role:18} (user {uid}): my_claims = {n:3} of {total}")

    print("\nguard checks:")
    with scoped_connection(1, "adjuster") as conn:
        for sql in [
            "SELECT status, COUNT(*) FROM my_claims GROUP BY status",  # ok
            "SELECT * FROM claim",                                     # base table -> blocked
            "SELECT 1; DROP TABLE claim",                             # multi-stmt -> blocked
            "UPDATE my_claims SET status='X'",                        # write -> blocked
        ]:
            try:
                cols, rows = run_select(conn, sql)
                print(f"  OK      {sql[:45]:47} -> {len(rows)} rows")
            except ValueError as e:
                print(f"  BLOCKED {sql[:45]:47} -> {e}")
