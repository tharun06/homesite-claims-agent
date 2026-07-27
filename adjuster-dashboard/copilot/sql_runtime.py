"""
Safe SQL runtime for the NL2SQL subgraph.

This is the boundary that makes it acceptable to let an LLM write SQL against the
real claims database. Works against PostgreSQL (deployed) or SQLite (local dev),
chosen by NL2SQL_DATABASE_URL.

Three layers of defense, strongest first:

  1. READ-ONLY ACCESS.
     - Postgres: a dedicated `copilot_ro` role holding only SELECT + TEMPORARY.
       Writes fail with InsufficientPrivilege, enforced by the database's own
       privilege system rather than a client flag. The session is additionally
       set READ ONLY after setup.
     - SQLite: a `mode=ro` connection, which SQLite itself refuses to write to.
  2. SCOPED VIEW — per request we build a `my_claims` TEMP VIEW containing only
     the rows this caller may see. The LLM queries `my_claims` and never names
     the base table. Temp views are connection-local, so requests are isolated.
  3. STATEMENT GUARD — run_select() rejects anything that is not a single
     read-only SELECT, and rejects any attempt to reach past the view to the
     base `claim` table.

Note on `app_users`: in PostgreSQL `user` is a RESERVED word, and an unquoted
`FROM user` silently resolves to the CURRENT_USER function instead of the table —
returning a wrong answer with no error. We expose the view `app_users` so the LLM
cannot get this wrong.
"""
import os
import re
from contextlib import contextmanager

# NL2SQL_DATABASE_URL should be the READ-ONLY Postgres DSN, e.g.
#   postgresql://copilot_ro:pass@host:5432/claims?sslmode=require
# If unset we fall back to the local SQLite file for offline development.
NL2SQL_URL = os.getenv("NL2SQL_DATABASE_URL", "")

DB_PATH = os.environ.get("DASHBOARD_DB") or os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "backend", "dashboard.db")
)

IS_POSTGRES = NL2SQL_URL.startswith("postgres")

# The table the LLM must never touch directly, and the name it must use instead.
BASE_TABLE = "claim"
SCOPED_VIEW = "my_claims"
USERS_RELATION = "app_users" if IS_POSTGRES else "user"


def database_available() -> bool:
    """True if the claims database is reachable from this process."""
    if IS_POSTGRES:
        return bool(NL2SQL_URL)
    return os.path.exists(DB_PATH)


# ── Layer 2: the scope predicate (mirrors the backend's scope_claims()) ───────
def _scope_predicate(user_id: int, role: str) -> str:
    """SQL WHERE clause limiting `claim` to what this caller may see. Mirrors
    app/scoping.py::scope_claims. user_id is coerced to int, so inlining it is
    injection-safe — it can only ever be a number, never LLM-controlled text."""
    uid = int(user_id)
    r = (role or "").strip().lower()
    users = f'"{"user"}"' if IS_POSTGRES else "user"   # `user` is reserved in PG

    if r == "admin":
        return "1=1"
    if r == "siu_investigator":
        return "fraud_flagged = true" if IS_POSTGRES else "fraud_flagged = 1"
    if r == "senior_adjuster":
        return (
            f"adjuster_id IN (SELECT id FROM {users} WHERE team_id = "
            f"(SELECT team_id FROM {users} WHERE id = {uid}))"
        )
    return f"adjuster_id = {uid}"


@contextmanager
def scoped_connection(user_id: int, role: str):
    """Yield a read-only connection with a `my_claims` view already scoped to the
    caller. The view is TEMP — it exists only for this connection and vanishes
    when it closes, so there is no shared state between requests."""
    predicate = _scope_predicate(user_id, role)

    if IS_POSTGRES:
        import psycopg
        conn = psycopg.connect(NL2SQL_URL, autocommit=True, connect_timeout=20)
        try:
            conn.execute(
                f"CREATE TEMP VIEW {SCOPED_VIEW} AS SELECT * FROM {BASE_TABLE} WHERE {predicate}"
            )
            # Belt and braces: the role already lacks write grants, but make the
            # session explicitly read-only now that setup is done. (This must come
            # AFTER the temp view — CREATE VIEW is itself blocked in a read-only
            # transaction.)
            conn.execute("SET SESSION CHARACTERISTICS AS TRANSACTION READ ONLY")
            yield conn
        finally:
            conn.close()
    else:
        import sqlite3
        conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
        try:
            conn.execute(
                f"CREATE TEMP VIEW {SCOPED_VIEW} AS SELECT * FROM {BASE_TABLE} WHERE {predicate}"
            )
            yield conn
        finally:
            conn.close()


# ── Layer 3: the statement guard ─────────────────────────────────────────────
_FORBIDDEN = re.compile(
    r"\b(insert|update|delete|drop|alter|create|attach|detach|replace|truncate|grant|revoke|copy|pragma|vacuum)\b",
    re.IGNORECASE,
)
# \bclaim\b matches the standalone word "claim" but NOT "my_claims" or
# "claim_number" — underscore is a word character, so there is no boundary there.
_BASE_TABLE = re.compile(rf"\b{BASE_TABLE}\b", re.IGNORECASE)


def run_select(conn, sql: str, limit: int = 200):
    """Validate then execute a single read-only SELECT.
    Returns (column_names, rows). Raises ValueError if the SQL fails any check."""
    stmt = sql.strip().rstrip(";").strip()

    if ";" in stmt:
        raise ValueError("Only a single statement is allowed.")
    low = stmt.lower()
    if not (low.startswith("select") or low.startswith("with")):
        raise ValueError("Only SELECT / WITH queries are allowed.")
    if _FORBIDDEN.search(stmt):
        raise ValueError("Query contains a forbidden (write/DDL) keyword.")
    if _BASE_TABLE.search(stmt):
        raise ValueError(f"Query must use the {SCOPED_VIEW} view, not the base {BASE_TABLE} table.")

    cur = conn.execute(stmt)
    cols = [d[0] for d in cur.description] if cur.description else []
    rows = [list(r) for r in cur.fetchmany(limit)]
    return cols, rows


def distinct_values(conn, source: str, column: str, limit: int = 25):
    """Distinct values of a free-text column, so the LLM can map user phrasing
    ('Charlotte') to a real stored value. This runs OUR SQL, not the LLM's."""
    cur = conn.execute(
        f'SELECT DISTINCT "{column}" FROM {source} '
        f'WHERE "{column}" IS NOT NULL ORDER BY 1 LIMIT {int(limit)}'
    )
    return [r[0] for r in cur.fetchall()]


# ── runnable smoke test ──────────────────────────────────────────────────────
if __name__ == "__main__":
    print(f"backend: {'PostgreSQL' if IS_POSTGRES else 'SQLite'}")
    print(f"available: {database_available()}\n")

    for role, uid in [("adjuster", 1), ("senior_adjuster", 16),
                      ("siu_investigator", 21), ("admin", 24)]:
        with scoped_connection(uid, role) as conn:
            n = conn.execute(f"SELECT COUNT(*) FROM {SCOPED_VIEW}").fetchone()[0]
            print(f"  {role:18} (user {uid}): {SCOPED_VIEW} = {n}")

    print("\nguard checks:")
    with scoped_connection(1, "adjuster") as conn:
        for sql in [
            f"SELECT status, COUNT(*) FROM {SCOPED_VIEW} GROUP BY status",   # ok
            f"SELECT * FROM {BASE_TABLE}",                                    # base table
            "SELECT 1; DROP TABLE claim",                                     # multi-stmt
            f"UPDATE {SCOPED_VIEW} SET status='X'",                           # write
        ]:
            try:
                cols, rows = run_select(conn, sql)
                print(f"  OK      {sql[:44]:46} -> {len(rows)} rows")
            except ValueError as e:
                print(f"  BLOCKED {sql[:44]:46} -> {e}")
