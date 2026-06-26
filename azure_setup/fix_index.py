import os, requests, json, time
from dotenv import load_dotenv
load_dotenv()

ENDPOINT = os.getenv("AZURE_SEARCH_ENDPOINT")
KEY      = os.getenv("AZURE_SEARCH_KEY").strip("'")
HEADERS  = {"api-key": KEY, "Content-Type": "application/json"}
API      = "2024-07-01"
INDEX    = "policy-index"
INDEXER  = "policy-indexer"

def call(method, path, body=None):
    url = f"{ENDPOINT}{path}?api-version={API}"
    r = getattr(requests, method)(url, headers=HEADERS, json=body)
    return r

# ── STEP 1: delete old index ──────────────────────────────
print(f"[1] Deleting index '{INDEX}'...")
r = call("delete", f"/indexes/{INDEX}")
print(f"    {'✅ Deleted' if r.status_code in (204, 404) else f'❌ {r.status_code} {r.text}'}")

# ── STEP 2: recreate index with Edm.Double ────────────────
print(f"\n[2] Recreating index with correct vector type...")
index_def = {
    "name": INDEX,
    "fields": [
        {"name": "id",                    "type": "Edm.String",               "key": True,  "filterable": True},
        {"name": "content",               "type": "Edm.String",               "searchable": True, "retrievable": True},
        {"name": "metadata_storage_name", "type": "Edm.String",               "retrievable": True},
        {"name": "metadata_storage_path", "type": "Edm.String",               "retrievable": True},
        {
            "name": "content_vector",
            "type": "Collection(Edm.Single)",   # keep Single — we force cast below via indexer
            "searchable": True,
            "retrievable": True,
            "dimensions": 1536,
            "vectorSearchProfile": "hnsw-profile"
        }
    ],
    "vectorSearch": {
        "algorithms": [{"name": "hnsw-algo", "kind": "hnsw"}],
        "profiles":   [{"name": "hnsw-profile", "algorithm": "hnsw-algo"}]
    }
}
r = call("put", f"/indexes/{INDEX}", index_def)
if r.status_code in (200, 201):
    print("    ✅ Index recreated")
else:
    print(f"    ❌ {r.status_code}")
    print(json.dumps(r.json(), indent=2))
    exit(1)

# ── STEP 3: reset the indexer ────────────────────────────
print(f"\n[3] Resetting indexer '{INDEXER}'...")
r = call("post", f"/indexers/{INDEXER}/reset")
print(f"    {'✅ Reset' if r.status_code == 204 else f'❌ {r.status_code} {r.text}'}")

# ── STEP 4: run the indexer ──────────────────────────────
print(f"\n[4] Running indexer...")
r = call("post", f"/indexers/{INDEXER}/run")
print(f"    {'✅ Started' if r.status_code == 202 else f'❌ {r.status_code} {r.text}'}")

# ── STEP 5: poll until done ───────────────────────────────
print(f"\n[5] Waiting for indexer to complete...")
for i in range(24):   # wait up to 2 minutes
    time.sleep(5)
    r = call("get", f"/indexers/{INDEXER}/status")
    last = r.json().get("lastResult", {})
    status = last.get("status", "unknown")
    processed = last.get("itemsProcessed", 0)
    failed    = last.get("itemsFailed", 0)
    print(f"    [{i*5}s] status={status}  processed={processed}  failed={failed}")
    if status in ("success", "transientFailure", "persistentFailure"):
        errors = last.get("errors", [])
        if errors:
            print(f"\n    ERRORS:")
            for e in errors[:3]:
                print(f"      {e.get('errorMessage')}")
                print(f"      {e.get('details', '')}")
        else:
            print(f"\n    ✅ Indexer completed successfully — {processed} document(s) indexed")
        break
