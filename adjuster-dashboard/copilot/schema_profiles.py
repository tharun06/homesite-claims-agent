"""
Semantic profiles — the LLM's ENTIRE map of the database for NL2SQL.

The mechanical half (tables, columns, types, FKs, enum values) was reflected
from models.py + profiled from dashboard.db by gen_schema_profiles.py. The
descriptions below are the curated half: they encode business meaning the raw
schema can't state (what an SLA breach is, which column is the auth boundary,
what each enum value means).

Scope is deliberately the claim -> user -> team join chain only. Enum values
are the strings ACTUALLY stored in SQLite (e.g. 'UNDER_REVIEW', not
'Under Review'), so generated SQL matches the data.
"""

SEMANTIC_PROFILES = {
    "team": {
        "description": "An adjuster team, organized by US region. Every adjuster belongs to one team; SIU investigators and admins have none.",
        "columns": {
            "id":     {"type": "int", "description": "Primary key."},
            "name":   {"type": "str", "description": "Display name, e.g. 'Team Southeast'. Prefer filtering/grouping by region instead."},
            "region": {"type": "str", "description": "US region the team covers. One of: Northeast, Southeast, Midwest, West, Southwest."},
        },
        "relations": [
            "app_users.team_id -> team.id (a team has many users)",
        ],
    },
    "app_users": {
        "description": "A staff member — adjuster, senior adjuster, SIU investigator, or admin. Claims are assigned to users via claim.adjuster_id. NOTE: query this as `app_users` — the underlying table name is a reserved word.",
        "columns": {
            "id":      {"type": "int",  "description": "Primary key. Join target for claim.adjuster_id."},
            "name":    {"type": "str",  "description": "Full name of the staff member. FREE TEXT — ground before filtering."},
            "email":   {"type": "str",  "description": "Login email. FREE TEXT; rarely used for filtering."},
            "role":    {"type": "str",  "description": "Staff role, stored as: ADJUSTER, SENIOR_ADJUSTER, SIU_INVESTIGATOR, ADMIN."},
            "team_id": {"type": "int",  "description": "FK to team.id — the user's team. NULL for SIU investigators and admins."},
            "active":  {"type": "bool", "description": "Whether the user is currently active (almost always true)."},
        },
        "relations": [
            "app_users.team_id -> team.id",
            "claim.adjuster_id -> app_users.id (a user owns many claims)",
        ],
    },
    "claim": {
        "description": "The core fact table: one auto-insurance claim. Money fields are USD; dates drive SLA and lifecycle logic.",
        "columns": {
            "id":               {"type": "int",   "description": "Primary key."},
            "claim_number":     {"type": "str",   "description": "Human-facing claim ID, e.g. 'CLM-100569'. FREE TEXT — ground before filtering."},
            "adjuster_id":      {"type": "int",   "description": "FK to app_users.id — the adjuster who owns this claim. MANDATORY SCOPE FILTER: a plain adjuster may only see rows where adjuster_id = their own id."},
            "status":           {"type": "str",   "description": "Lifecycle stage, stored as these exact strings (note UNDER_SCORES, not spaces): FNOL, UNDER_REVIEW, INVESTIGATION, APPRAISAL, PENDING_APPROVAL, APPROVED, DENIED, CLOSED, SIU_FLAGGED. A claim is 'resolved' when status IN ('CLOSED','DENIED')."},
            "peril_type":       {"type": "str",   "description": "Cause of loss, stored as: COLLISION, COMPREHENSIVE, THEFT, VANDALISM, WEATHER, GLASS."},
            "loss_date":        {"type": "date",  "description": "Date the incident/loss occurred."},
            "reported_date":    {"type": "date",  "description": "Date the claim was first reported (FNOL); on or after loss_date."},
            "incident_city":    {"type": "str",   "description": "City where the loss occurred (North Carolina only). FREE TEXT — ground before filtering."},
            "incident_state":   {"type": "str",   "description": "State of the loss — always 'NC' in this dataset."},
            "estimated_amount": {"type": "float", "description": "Estimated repair/replacement cost, USD."},
            "reserve_amount":   {"type": "float", "description": "Money reserved against the claim, USD. 0 once the claim is resolved (CLOSED/DENIED)."},
            "approved_amount":  {"type": "float", "description": "Final approved payout, USD. NULL until the claim reaches APPROVED or CLOSED."},
            "deductible":       {"type": "float", "description": "Customer's out-of-pocket amount, USD."},
            "fraud_score":      {"type": "float", "description": "Model-assigned fraud risk, 0.0 (low) to 1.0 (high)."},
            "fraud_flagged":    {"type": "bool",  "description": "True (1) if flagged for fraud / SIU review, else False (0)."},
            "sla_due_date":     {"type": "datetime", "description": "Deadline to act on the claim. An SLA BREACH = sla_due_date < now AND status NOT IN ('CLOSED','DENIED')."},
            "created_at":       {"type": "datetime", "description": "When the claim row was created (approximately the reported_date)."},
            "updated_at":       {"type": "datetime", "description": "When the claim was last updated."},
        },
        "relations": [
            "claim.adjuster_id -> app_users.id",
            # claim.policy_id -> policy.id and claim.vehicle_id -> vehicle.id exist in
            # the DB but are intentionally omitted — those tables aren't profiled.
        ],
    },
}
