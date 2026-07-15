"""Seed claims-index: pull real claims, build a description, embed, and upload directly.

Safe to re-run. Run:  python -m azure_setup.seed_claims
"""
import os
from pathlib import Path

import httpx
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")

DASHBOARD_URL = os.getenv("DASHBOARD_URL", "http://localhost:8100")
ADMIN_EMAIL = "wrightcaleb@example.org"  # Jessica Holmes, Admin — sees every claim

AOAI_ENDPOINT = os.getenv("AZURE_OPENAI_ENDPOINT", "").rstrip("/")
AOAI_KEY = os.getenv("AZURE_OPENAI_KEY", "")
EMBED_DEPLOYMENT = os.getenv("AZURE_EMBEDDING_DEPLOYMENT", "")

SEARCH_ENDPOINT = os.getenv("AZURE_SEARCH_ENDPOINT", "").rstrip("/")
SEARCH_KEY = os.getenv("AZURE_SEARCH_KEY", "")
CLAIMS_INDEX = "claims-index"


def fetch_all_claims() -> list[dict]:
    login = httpx.post(f"{DASHBOARD_URL}/auth/login", data={"email": ADMIN_EMAIL})
    login.raise_for_status()
    token = login.json()["access_token"]

    r = httpx.get(
        f"{DASHBOARD_URL}/claims",
        headers={"Authorization": f"Bearer {token}"},
        params={"limit": 500},
    )
    r.raise_for_status()
    return r.json()["claims"]


def build_content(claim: dict) -> str:
    vehicle = claim.get("vehicle") or {}
    return (
        f"Claim {claim['claim_number']}: {claim.get('peril_type', 'Unknown')} claim "
        f"for a {vehicle.get('year', '')} {vehicle.get('make', '')} {vehicle.get('model', '')}, "
        f"incident in {claim.get('incident_city', 'unknown city')}, {claim.get('incident_state', '')}. "
        f"Status {claim.get('status')}, estimated ${claim.get('estimated_amount')}, "
        f"fraud score {claim.get('fraud_score')}."
    )


def embed(text: str) -> list[float]:
    url = f"{AOAI_ENDPOINT}/openai/deployments/{EMBED_DEPLOYMENT}/embeddings?api-version=2023-05-15"
    r = httpx.post(url, headers={"api-key": AOAI_KEY}, json={"input": text})
    r.raise_for_status()
    return r.json()["data"][0]["embedding"]


def upload_batch(docs: list[dict]) -> None:
    url = f"{SEARCH_ENDPOINT}/indexes/{CLAIMS_INDEX}/docs/index?api-version=2023-11-01"
    r = httpx.post(
        url,
        headers={"api-key": SEARCH_KEY, "Content-Type": "application/json"},
        json={"value": docs},
    )
    r.raise_for_status()


def main():
    claims = fetch_all_claims()
    print(f"fetched {len(claims)} claims")

    docs = []
    for i, c in enumerate(claims, start=1):
        content = build_content(c)
        vector = embed(content)
        docs.append({
            "@search.action": "mergeOrUpload",
            "id": c["claim_number"],
            "claim_number": c["claim_number"],
            "content": content,
            "content_vector": vector,
            "status": c.get("status"),
            "estimated_amount": c.get("estimated_amount"),
        })
        if i % 50 == 0:
            print(f"  embedded {i}/{len(claims)}...")

    upload_batch(docs)
    print(f"\nDONE. uploaded {len(docs)} claims to '{CLAIMS_INDEX}'")


if __name__ == "__main__":
    main()
