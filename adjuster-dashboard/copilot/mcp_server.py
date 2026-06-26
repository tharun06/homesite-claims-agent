import os
import json
import httpx
from mcp.server.fastmcp import FastMCP


DASHBOARD_URL = os.getenv("DASHBOARD_URL", "http://localhost:8100")
TOKEN = os.getenv("ADJUSTER_TOKEN", "")
HEADERS = {"Authorization": f"Bearer {TOKEN}"} if TOKEN else {} 

mcp = FastMCP("homesite-claims")

def _get(path: str, params: dict | None) -> dict:
    """Get data from the dashboard API."""
    response = httpx.get(f"{DASHBOARD_URL}{path}", params=params, headers=HEADERS)
    response.raise_for_status()
    return response.json()

def resolve_claim(claim_number: str) -> dict | None:
    """Resolve a claim number to a claim object."""
    data = _get("/claims", params={"search": claim_number, "limit": 5})
    for claim in data.get("claims", []):
        if claim.get("claim_number") == claim_number:
            return claim
    return None

@mcp.tool()
def queue_metrics() -> str:
    """Queue metrics: total/open counts, SLA breaches, fraud count, by-status."""
    return json.dumps(_get("/metrics/queue"))

@mcp.tool()
def list_my_claims(status: str = "", search: str ="") -> str:
    """List the adjuster's claims, newest first. Optionally filter by status
    (e.g. 'Under Review') or a search term matching the claim number."""
    params = {}
    if status:
        params["status"] = status
    if search:
        params["search"] = search
    data =_get("/claims", params=params)
    compact = [
        {
            "claim_number": c.get("claim_number"),
            "status": c.get("status"),
            "customer_name": c.get("customer_name"),
            "estimate:": c.get("estimate"),
            "fraud_flagged": c.get("fraud_flagged"),
        }
        for c in data.get("claims", [])
    ]
    return json.dumps(compact)