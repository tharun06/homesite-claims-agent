import os
import json
from urllib import response
import httpx
from mcp.server.fastmcp import FastMCP


DASHBOARD_URL = os.getenv("DASHBOARD_URL", "http://localhost:8100")

# Two ways this server gets an identity:
#
#  1. ADJUSTER_TOKEN — a token handed in at startup. Used when the copilot spawns
#     this file as a stdio subprocess, and by Claude Desktop's config.
#  2. SERVICE_EMAIL — the server logs in itself and refreshes as needed. Required
#     when running as a long-lived HTTP service, because backend tokens expire
#     after 12 hours and a baked-in one would go stale twice a day.
TOKEN = os.getenv("ADJUSTER_TOKEN", "")
SERVICE_EMAIL = os.getenv("SERVICE_EMAIL", "")
SEARCH_ENDPOINT = os.getenv("AZURE_SEARCH_ENDPOINT", "").rstrip("/")
SEARCH_KEY = os.getenv("AZURE_SEARCH_KEY", "")
SEARCH_INDEX = os.getenv("AZURE_SEARCH_INDEX", "")
CLAIMS_INDEX = os.getenv("AZURE_CLAIMS_INDEX", "claims-index")
AOAI_ENDPOINT = os.getenv("AZURE_OPENAI_ENDPOINT", "").rstrip("/")
AOAI_KEY = os.getenv("AZURE_OPENAI_KEY", "")
EMBED_DEPLOYMENT = os.getenv("AZURE_EMBEDDING_DEPLOYMENT", "")


# The dashboard now runs on a networked Postgres in a different region, so
# list-style endpoints are far slower than they were against a local SQLite
# file (/claims ~7.5s for 58 claims). httpx's default timeout is 5s, which
# made every claims lookup fail. Give backend calls room, and keep Azure
# calls on a shorter leash.
HTTP_TIMEOUT = 60.0

mcp = FastMCP("homesite-claims")

_token_cache: dict[str, str] = {}


def _service_login() -> str:
    """Log in as SERVICE_EMAIL and cache the token."""
    r = httpx.post(f"{DASHBOARD_URL}/auth/login",
                   data={"email": SERVICE_EMAIL}, timeout=HTTP_TIMEOUT)
    r.raise_for_status()
    _token_cache["token"] = r.json()["access_token"]
    return _token_cache["token"]


def _headers() -> dict:
    """Auth header for backend calls. A static ADJUSTER_TOKEN wins when present
    (stdio mode); otherwise use the cached service token, logging in on first
    use."""
    if TOKEN:
        return {"Authorization": f"Bearer {TOKEN}"}
    if not SERVICE_EMAIL:
        return {}
    return {"Authorization": f"Bearer {_token_cache.get('token') or _service_login()}"}


def _request(method: str, path: str, **kw) -> dict:
    """Call the dashboard API, refreshing the service token once on 401.

    Backend tokens expire after 12 hours, so a long-running HTTP server will
    eventually present a stale one. Retrying after a fresh login keeps it alive
    without a restart.
    """
    url = f"{DASHBOARD_URL}{path}"
    response = httpx.request(method, url, headers=_headers(), timeout=HTTP_TIMEOUT, **kw)
    if response.status_code == 401 and SERVICE_EMAIL and not TOKEN:
        _service_login()
        response = httpx.request(method, url, headers=_headers(), timeout=HTTP_TIMEOUT, **kw)
    response.raise_for_status()
    return response.json()


def _get(path: str, params: dict | None = None) -> dict:
    """Get data from the dashboard API."""
    return _request("GET", path, params=params)


def _patch(path: str, data: dict | None = None) -> dict:
    """Patch data on the dashboard API."""
    return _request("PATCH", path, data=data)


def _post(path: str, data: dict | None = None) -> dict:
    """Post data to the dashboard API."""
    return _request("POST", path, data=data)


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
    response = httpx.post(url, headers={"api-key": AOAI_KEY}, json={"input": text}, timeout=HTTP_TIMEOUT)
    response.raise_for_status()
    return response.json()["data"][0]["embedding"]

def _search_policies(query: str, k: int =3) -> list[dict]:
    """Search the policy docs index for relevant clauses, using Azure AI Search + embeddings."""
    embedding = _embed(query)
    url = f"{SEARCH_ENDPOINT}/indexes/{SEARCH_INDEX}/docs/search?api-version=2024-07-01"
    payload = {
        "vectorQueries": [
            { "kind": "vector", "vector": embedding, "fields": "content_vector", "k": k }
        ],
        "queryType": "semantic",
        "semanticConfiguration": "default",
        "top": 5,
        "select": "content,metadata_storage_name"
    }
    response = httpx.post(url, headers={"api-key": SEARCH_KEY}, json=payload, timeout=HTTP_TIMEOUT)
    response.raise_for_status()
    return response.json().get("value", [])

def _search_similar_claims(query:str, k:int=5) -> list[dict]:
    """ Search the claims index for similar claims, using Azure AI Search + embeddings."""
    # vectorFilterMode=preFilter narrows to the caller's own claims BEFORE ranking,
    # so k only needs to cover how many matches we actually want back — it no longer
    # has to be padded to survive a race against every other adjuster's claims.
    allowed = _get("/claims", params={"limit": 500}).get("claims", [])
    allowed_numbers = {c["claim_number"] for c in allowed}
    if not allowed_numbers:
        return []
    embedding = _embed(query)
    filter_str = "search.in(claim_number, '{}', ',')".format(",".join(allowed_numbers))
    url = f"{SEARCH_ENDPOINT}/indexes/{CLAIMS_INDEX}/docs/search?api-version=2024-07-01"
    payload = {
        "vectorFilterMode": "preFilter",
        "vectorQueries": [
            { "kind": "vector", "vector": embedding, "fields": "content_vector", "k": k }
        ],
        "filter": filter_str,
        "top": 5,
        "select": "claim_number,content,estimated_amount,status"
    }   
    response = httpx.post(url, headers={"api-key": SEARCH_KEY}, json=payload, timeout=HTTP_TIMEOUT)
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

    _post(f"/claims/{claim['id']}/notes", data={"note": note})
    return json.dumps({"claim_number": claim_number, "ok": True})

@mcp.tool()
def reassign_claim(claim_number: str, adjuster_id: int) -> str:
    """Reassign a claim to another adjuster."""
    claim = resolve_claim(claim_number)
    if not claim:
        return json.dumps({"error": "Claim not found"})

    _post(f"/claims/{claim['id']}/reassign", data={"adjuster_id": adjuster_id})

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

@mcp.tool()
def search_similar_claims(query: str) -> str:
    """Search the claims index for similar claims, using semantic search and embeddings.
    Use this for 'have we seen a claim like this before' questions.
    Returns a list of similar claims with their claim_number, content, estimated_amount, and status."""
    hits = _search_similar_claims(query)
    compact = [
        {
            "claim_number": h.get("claim_number"),
            "content": h.get("content"),
            "estimated_amount": h.get("estimated_amount"),
            "status": h.get("status"),
        }
        for h in hits
    ]
    return json.dumps(compact)

if __name__ == "__main__":
    # stdio by default, so the copilot's subprocess and Claude Desktop keep
    # working unchanged. The container sets MCP_TRANSPORT=streamable-http to
    # serve the same tools over HTTP at /mcp instead.
    transport = os.getenv("MCP_TRANSPORT", "stdio")
    if transport == "streamable-http":
        # stateless: a fresh transport per request, no session affinity needed,
        # so the app can scale to zero and back without breaking clients.
        mcp.settings.stateless_http = True
        mcp.settings.host = "0.0.0.0"
        mcp.settings.port = int(os.getenv("PORT", "8000"))

        # The SDK rejects requests whose Host header it does not recognise
        # (DNS-rebinding protection) — behind Azure ingress that means every
        # request gets 421 "Invalid Host header" until the public FQDN is
        # allowed. MCP_ALLOWED_HOSTS is a comma-separated list; "*" allows any.
        allowed = os.getenv("MCP_ALLOWED_HOSTS", "").strip()
        if allowed:
            from mcp.server.transport_security import TransportSecuritySettings
            hosts = [h.strip() for h in allowed.split(",") if h.strip()]
            mcp.settings.transport_security = TransportSecuritySettings(
                allowed_hosts=hosts,
                allowed_origins=hosts,
            )
        mcp.run(transport="streamable-http")
    else:
        mcp.run()