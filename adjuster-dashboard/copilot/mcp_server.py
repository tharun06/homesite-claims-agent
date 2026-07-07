import os
import json
from urllib import response
import httpx
from mcp.server.fastmcp import FastMCP


DASHBOARD_URL = os.getenv("DASHBOARD_URL", "http://localhost:8100")
TOKEN = os.getenv("ADJUSTER_TOKEN", "")
HEADERS = {"Authorization": f"Bearer {TOKEN}"} if TOKEN else {} 
SEARCH_ENDPOINT = os.getenv("AZURE_SEARCH_ENDPOINT", "").rstrip("/")
SEARCH_KEY = os.getenv("AZURE_SEARCH_KEY", "")
SEARCH_INDEX = os.getenv("AZURE_SEARCH_INDEX", "")
AOAI_ENDPOINT = os.getenv("AZURE_OPENAI_ENDPOINT", "").rstrip("/")
AOAI_KEY = os.getenv("AZURE_OPENAI_KEY", "")
EMBED_DEPLOYMENT = os.getenv("AZURE_EMBEDDING_DEPLOYMENT", "")


mcp = FastMCP("homesite-claims")

def _get(path: str, params: dict | None = None) -> dict:
    """Get data from the dashboard API."""
    response = httpx.get(f"{DASHBOARD_URL}{path}", params=params, headers=HEADERS)
    response.raise_for_status()
    return response.json()


def _patch(path: str, data: dict | None = None) -> dict:
    """Patch data on the dashboard API."""
    response = httpx.patch(f"{DASHBOARD_URL}{path}", data=data, headers=HEADERS)
    response.raise_for_status()
    return response.json()


def resolve_claim(claim_number: str) -> dict | None:
    """Resolve a claim number to a claim object."""
    data = _get("/claims", params={"search": claim_number, "limit": 5})
    for claim in data.get("claims", []):
        if claim.get("claim_number") == claim_number:
            return claim
    return None

def _embed(text: str) -> list[float]:
    """Turn text into a vector, using the same embedding model that indexed the policy docs."""
    url = f"{AOAI_ENDPOINT}/openai/deployments/{EMBED_DEPLOYMENT}/embeddings?api-version=2023-05-15"
    response = httpx.post(url, headers={"api-key": AOAI_KEY}, json={"input": text})
    response.raise_for_status()
    return response.json()["data"][0]["embedding"]

def _search_policies(query: str, k: int =3) -> list[dict]:
    """Search the policy docs index for relevant clauses, using Azure AI Search + embeddings."""
    embedding = _embed(query)
    url = f"{SEARCH_ENDPOINT}/indexes/{SEARCH_INDEX}/docs/search?api-version=2023-07-01-Preview"
    payload = {
        "vector": {
            "value": embedding,
            "fields": "content_vector",
            "k": k
        },
        "queryType": "semantic",
        "semanticConfiguration": "default",
        "top": 5,
        "select": "content,metadata_storage_name"
    }
    response = httpx.post(url, headers={"api-key": SEARCH_KEY}, json=payload)
    response.raise_for_status()
    return response.json().get("value", [])

@mcp.tool()
def queue_metrics() -> str:
    """Queue metrics: total/open counts, SLA breaches, fraud count, by-status."""
    return json.dumps(_get("/metrics/queue"))

@mcp.tool()
def list_my_claims(status: str = "", search: str = "", fraud_only: bool = False) -> str:
    """List the adjuster's claims, newest first. Filters (all optional):
    - status: e.g. 'Under Review', 'SIU Flagged'
    - search: matches the claim NUMBER only (e.g. 'CLM-146670'), not free text
    - fraud_only: set True to return only fraud-flagged claims."""
    params = {}
    if status:
        params["status"] = status
    if search:
        params["search"] = search
    data = _get("/claims", params=params)
    claims = data.get("claims", [])
    if fraud_only:
        claims = [c for c in claims if c.get("fraud_flagged")]
    compact = [
        {
            "claim_number": c.get("claim_number"),
            "status": c.get("status"),
            "customer_name": c.get("customer_name"),
            "estimated_amount": c.get("estimated_amount"),
            "fraud_flagged": c.get("fraud_flagged"),
            "fraud_score": c.get("fraud_score"),
        }
        for c in claims
    ]
    return json.dumps(compact)

@mcp.tool()
def get_claim_status(claim_number: str) -> str:
    """Return the current status and summary of one claim."""
    claim = resolve_claim(claim_number)
    if not claim:
        return json.dumps({"error": "Claim not found"})

    result = {
        "claim_number": claim.get("claim_number"),
        "status": claim.get("status"),
        "fraud_flagged": claim.get("fraud_flagged"),
        "estimated_amount": claim.get("estimated_amount"),
    }
    return json.dumps(result)

@mcp.tool()
def get_my_pending_tasks() -> str:
    """Return pending, in-progress, and overdue tasks assigned to the adjuster."""
    data = _get("/me/tasks", params={"days": 30})
    compact = [
        {
            "id": t.get("id"),
            "claim_id": t.get("claim_id"),
            "task_type": t.get("task_type"),
            "description": t.get("description"),
            "status": t.get("status"),
            "due_date": t.get("due_date"),
        }
        for t in data
    ]
    return json.dumps(compact)


VALID_STATUSES = {
    "FNOL", "Under Review", "Investigation", "Appraisal",
    "Pending Approval", "Approved", "Denied", "Closed", "SIU Flagged"
}

@mcp.tool()
def update_claim_status(claim_number: str, new_status: str) -> str:
    """Update the status of a claim by claim number."""
    if new_status not in VALID_STATUSES:
        return json.dumps({"error": f"Invalid status '{new_status}'. Must be one of: {sorted(VALID_STATUSES)}"})

    claim = resolve_claim(claim_number)
    if not claim:
        return json.dumps({"error": "Claim not found"})

    result = _patch(
        f"/claims/{claim.get('id')}/status",
        data={"new_status": new_status},
    )
    return json.dumps({
        "claim_number": claim_number,
        "updated": result,
    })

@mcp.tool()
def add_note_to_claim(claim_number: str, note: str) -> str:
    """Add a note to a claim."""
    claim = resolve_claim(claim_number)
    if not claim:
        return json.dumps({"error": "Claim not found"})

    result = httpx.post(
        f"{DASHBOARD_URL}/claims/{claim['id']}/notes",
        data={"note": note},
        headers=HEADERS,
    )
    result.raise_for_status()
    return json.dumps({"claim_number": claim_number, "ok": True})

@mcp.tool()
def reassign_claim(claim_number: str, adjuster_id: int) -> str:
    """Reassign a claim to another adjuster."""
    claim = resolve_claim(claim_number)
    if not claim:
        return json.dumps({"error": "Claim not found"})

    result = httpx.post(
        f"{DASHBOARD_URL}/claims/{claim['id']}/reassign",
        data={"adjuster_id": adjuster_id},
        headers=HEADERS,
    )
    result.raise_for_status()

    return json.dumps({
        "claim_number": claim_number,
        "adjuster_id": adjuster_id,
        "ok": True,
    })

@mcp.tool()
def search_policy_docs(query: str) -> str:
    """Search HomeSite's policy, claims-handling, fraud/SIU, and authority
    documents for guidance on rules and procedures — e.g. coverage limits,
    deductibles, escalation rules, fraud thresholds, SLA timelines. Use this
    for 'what is the rule / am I allowed to / how do I' questions.
    Do NOT use this to look up a specific claim's own data — use
    list_my_claims or get_claim_status for that instead.
    You MUST name the source document explicitly in your answer, e.g.
    "per adjuster-authority-matrix.txt". If an excerpt contains a numeric
    limit or threshold, compare it directly against the user's figures and
    state plainly whether they are within or over it — do not hedge."""
    hits = _search_policies(query)
    compact = [
        {"source": h.get("metadata_storage_name"), "excerpt": h.get("content")}
        for h in hits
    ]
    return json.dumps(compact)

if __name__ == "__main__":
    mcp.run()