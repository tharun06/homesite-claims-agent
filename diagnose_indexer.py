import os, requests, json
from dotenv import load_dotenv
load_dotenv()

ENDPOINT = os.getenv("AZURE_SEARCH_ENDPOINT")
KEY      = os.getenv("AZURE_SEARCH_KEY").strip("'")
HEADERS  = {"api-key": KEY, "Content-Type": "application/json"}
API      = "2024-07-01"

def get(path):
    r = requests.get(f"{ENDPOINT}{path}?api-version={API}", headers=HEADERS)
    return r.json()

# ── 1. List all indexes ───────────────────────────────────
print("\n=== INDEXES ===")
data = get("/indexes")
for idx in data.get("value", []):
    print(f"  • {idx['name']}")
    for f in idx.get("fields", []):
        vconf = f.get("vectorSearchProfile") or ""
        dims  = f.get("dimensions", "")
        print(f"      field: {f['name']}  type: {f['type']}  dims: {dims}  vector: {vconf}")

# ── 2. List all indexers + status ────────────────────────
print("\n=== INDEXERS ===")
data = get("/indexers")
for ix in data.get("value", []):
    name = ix["name"]
    print(f"  • {name}")
    status = get(f"/indexers/{name}/status")
    last = status.get("lastResult", {})
    print(f"    status:    {last.get('status')}")
    print(f"    succeeded: {last.get('itemsProcessed')}")
    print(f"    failed:    {last.get('itemsFailed')}")
    errors = last.get("errors", [])
    warnings = last.get("warnings", [])
    if errors:
        print(f"    ERRORS ({len(errors)}):")
        for e in errors[:5]:
            print(f"      key:     {e.get('key', 'n/a')[:60]}")
            print(f"      message: {e.get('errorMessage', 'n/a')}")
            print(f"      details: {e.get('details', 'n/a')}")
            print()
    if warnings:
        print(f"    WARNINGS ({len(warnings)}):")
        for w in warnings[:3]:
            print(f"      {w.get('message', 'n/a')}")

# ── 3. List all skillsets ─────────────────────────────────
print("\n=== SKILLSETS ===")
data = get("/skillsets")
for s in data.get("value", []):
    print(f"  • {s['name']}")
    for sk in s.get("skills", []):
        print(f"      skill: {sk.get('name')}  type: {sk.get('@odata.type','').split('.')[-1]}")
        if "dimensions" in sk:
            print(f"        dimensions: {sk['dimensions']}")
        if "deploymentId" in sk:
            print(f"        deployment: {sk['deploymentId']}")

# ── 4. List all data sources ──────────────────────────────
print("\n=== DATA SOURCES ===")
data = get("/datasources")
for ds in data.get("value", []):
    print(f"  • {ds['name']}  type: {ds.get('type')}")
    print(f"    container: {ds.get('container', {}).get('name')}")
