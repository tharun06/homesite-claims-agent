"""
Codegen: reflect the DB schema + profile the data, then emit a
schema_profiles.py SKELETON for the NL2SQL subgraph.

This does the mechanical half automatically (names, types, PK/FK, and — by
querying the data — which columns are enums vs. free text). It deliberately
leaves every `description` blank: that's the human/curated half.

Run from the backend dir:
    python gen_schema_profiles.py
Writes to ../copilot/schema_profiles.py
"""
import os
import sqlite3
import enum
import datetime as dt

from app.database import DB_PATH
from app.models import Team, User, Claim

# the 3-table join chain we expose to the LLM (claim -> user -> team)
TARGETS = {"team": Team, "user": User, "claim": Claim}

# claim columns we intentionally DON'T expose to the LLM
SKIP_COLUMNS = {"incident_lat", "incident_lng", "description"}

ENUM_MAX = 12  # <= this many distinct values => treat as an enum and list them


def friendly_type(col) -> str:
    """Map a SQLAlchemy column type to a simple label. Order matters:
    bool is a subclass of int, datetime is a subclass of date."""
    try:
        pt = col.type.python_type
    except Exception:
        return str(col.type).lower()
    if isinstance(pt, type) and issubclass(pt, enum.Enum):
        return "str"          # str-enums are stored as text
    if pt is bool:
        return "bool"
    if pt is dt.datetime:
        return "datetime"
    if pt is dt.date:
        return "date"
    return {int: "int", float: "float", str: "str"}.get(pt, pt.__name__)


def profile(conn, table, colname, ftype):
    """Look at the real data: enum-like (few distinct values) or free text?"""
    if ftype in ("int", "float", "date", "datetime"):
        return None
    n = conn.execute(f'SELECT COUNT(DISTINCT "{colname}") FROM "{table}"').fetchone()[0]
    if n <= ENUM_MAX:
        vals = [r[0] for r in conn.execute(
            f'SELECT DISTINCT "{colname}" FROM "{table}" '
            f'WHERE "{colname}" IS NOT NULL ORDER BY 1')]
        return ("enum", n, vals)
    vals = [r[0] for r in conn.execute(
        f'SELECT DISTINCT "{colname}" FROM "{table}" '
        f'WHERE "{colname}" IS NOT NULL LIMIT 3')]
    return ("freetext", n, vals)


def main():
    conn = sqlite3.connect(DB_PATH)
    lines = [
        "# AUTO-GENERATED SKELETON — reflected from models.py + profiled from dashboard.db.",
        "# The mechanical facts (type, PK, FK, enum values, free-text flags) are filled in.",
        "# YOUR JOB: write every `description`. That's the part a machine can't do.",
        "# Guidance is in the trailing comments on each line.",
        "",
        "SEMANTIC_PROFILES = {",
    ]
    report = []

    for tname, model in TARGETS.items():
        cols = list(model.__table__.columns)
        report.append(f"\n=== {tname} ({len(cols)} columns) ===")
        lines.append(f'    "{tname}": {{')
        lines.append('        "description": "",  # TODO: one sentence — what is this table?')
        lines.append('        "columns": {')

        for col in cols:
            if col.name in SKIP_COLUMNS:
                report.append(f"  {col.name:18} SKIPPED (not exposed to the LLM)")
                continue
            ftype = friendly_type(col)
            hints = []
            if col.primary_key:
                hints.append("PK")
            fks = [fk.target_fullname for fk in col.foreign_keys]
            for t in fks:
                hints.append(f"FK -> {t}")
            if col.nullable and not col.primary_key:
                hints.append("nullable")

            prof = profile(conn, tname, col.name, ftype)
            if prof:
                kind, n, vals = prof
                if kind == "enum":
                    hints.append("ENUM: " + ", ".join(str(v) for v in vals))
                else:
                    sample = ", ".join(str(v) for v in vals)
                    hints.append(f"FREE TEXT ({n} distinct, e.g. {sample}) -> ground before filtering")

            comment = " · ".join(hints)
            lines.append(f'            "{col.name}": {{"type": "{ftype}", "description": ""}},'
                         + (f'  # {comment}' if comment else ""))
            report.append(f"  {col.name:18} {ftype:9} {comment}")

        # relations (outgoing FKs on this table)
        rels = []
        for col in cols:
            for fk in col.foreign_keys:
                rels.append(f"{tname}.{col.name} -> {fk.target_fullname}")
        lines.append("        },")
        lines.append('        "relations": [')
        for r in rels:
            lines.append(f'            "{r}",')
        lines.append("        ],")
        lines.append("    },")

    lines.append("}")
    conn.close()

    out_path = os.path.join(os.path.dirname(__file__), "..", "copilot", "schema_profiles.py")
    out_path = os.path.abspath(out_path)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")

    print("\n".join(report))
    print(f"\nSkeleton written to: {out_path}")
    print("Next: open it and fill in every empty description.")


if __name__ == "__main__":
    main()
