"""
Full Azure AI Search setup — index + skillset + indexer in one script.
Run this once to get the search pipeline working end-to-end.
"""
import os, requests, json, time
from dotenv import load_dotenv
load_dotenv()

ENDPOINT  = os.getenv("AZURE_SEARCH_ENDPOINT")
KEY       = os.getenv("AZURE_SEARCH_KEY").strip("'")
OAI_EP    = os.getenv("AZURE_OPENAI_ENDPOINT").rstrip("/")
OAI_KEY   = os.getenv("AZURE_OPENAI_KEY")
EMBED_DEP = os.getenv("AZURE_EMBEDDING_DEPLOYMENT")   # text-embedding-3-small
BLOB_CONN = os.getenv("AZURE_BLOB_CONNECTION_STRING")
CONTAINER = os.getenv("AZURE_BLOB_CONTAINER")          # policy-documents

HEADERS   = {"api-key": KEY, "Content-Type": "application/json"}
API       = "2024-07-01"

INDEX     = "policy-index"
SKILLSET  = "policy-skillset"
DATASRC   = "policy-docs-source"
INDEXER   = "policy-indexer"

def call(method, path, body=None):
    url = f"{ENDPOINT}{path}?api-version={API}"
    r = getattr(requests, method)(url, headers=HEADERS, json=body)
    return r

def ok(r, label):
    if r.status_code in (200, 201, 204):
        print(f"  ✅ {label}")
        return True
    print(f"  ❌ {label} — {r.status_code}")
    if r.text:
        try: print(json.dumps(r.json(), indent=2))
        except: print(r.text[:500])
    return False

# ── STEP 1: delete existing resources ────────────────────
print("\n[1] Cleaning up old resources...")
call("delete", f"/indexers/{INDEXER}")
call("delete", f"/skillsets/{SKILLSET}")
call("delete", f"/indexes/{INDEX}")
print("  ✅ Cleaned up")

# ── STEP 2: create index ──────────────────────────────────
print("\n[2] Creating index...")
index_def = {
    "name": INDEX,
    "fields": [
        # chunk-level key — unique per chunk (parent path + chunk number)
        {"name": "chunk_id",              "type": "Edm.String",  "key": True,  "filterable": True, "retrievable": True, "analyzer": "keyword"},
        # the actual chunk text
        {"name": "content",               "type": "Edm.String",  "searchable": True, "retrievable": True},
        # vector of the chunk
        {
            "name": "content_vector",
            "type": "Collection(Edm.Single)",
            "searchable": True,
            "retrievable": True,
            "dimensions": 1536,
            "vectorSearchProfile": "hnsw-profile"
        },
        # parent document metadata — useful for filtering by policy file
        {"name": "metadata_storage_name", "type": "Edm.String",  "retrievable": True, "filterable": True},
        {"name": "metadata_storage_path", "type": "Edm.String",  "retrievable": True, "filterable": True},
    ],
    "vectorSearch": {
        "algorithms": [{"name": "hnsw-algo", "kind": "hnsw"}],
        "profiles":   [{"name": "hnsw-profile", "algorithm": "hnsw-algo"}]
    }
}
ok(call("put", f"/indexes/{INDEX}", index_def), "Index created")

# ── STEP 3: create data source ────────────────────────────
print("\n[3] Creating data source...")
ds_def = {
    "name": DATASRC,
    "type": "azureblob",
    "credentials": {"connectionString": BLOB_CONN},
    "container":   {"name": CONTAINER}
}
ok(call("put", f"/datasources/{DATASRC}", ds_def), "Data source created")

# ── STEP 4: create skillset (split + embed each chunk) ────
print("\n[4] Creating skillset with split + embed...")
skillset_def = {
    "name": SKILLSET,
    "description": "Split policy docs into chunks then embed each chunk",
    "skills": [
        # SKILL 1: split document into 512-token chunks
        {
            "@odata.type":     "#Microsoft.Skills.Text.SplitSkill",
            "name":            "split-skill",
            "context":         "/document",
            "defaultLanguageCode": "en",
            "textSplitMode":   "pages",
            "maximumPageLength": 512,
            "pageOverlapLength": 64,
            "inputs":  [{"name": "text",      "source": "/document/content"}],
            "outputs": [{"name": "textItems", "targetName": "chunks"}]
        },
        # SKILL 2: embed each chunk — context is per-chunk
        {
            "@odata.type":  "#Microsoft.Skills.Text.AzureOpenAIEmbeddingSkill",
            "name":         "embedding-skill",
            "context":      "/document/chunks/*",
            "resourceUri":  OAI_EP,
            "apiKey":       OAI_KEY,
            "deploymentId": EMBED_DEP,
            "modelName":    "text-embedding-3-small",
            "dimensions":   1536,
            "inputs":  [{"name": "text",      "source": "/document/chunks/*"}],
            "outputs": [{"name": "embedding", "targetName": "content_vector"}]
        }
    ],
    # indexProjections: one index document per chunk (not per blob file)
    "indexProjections": {
        "selectors": [
            {
                "targetIndexName": INDEX,
                "parentKeyFieldName": "metadata_storage_path",
                "sourceContext": "/document/chunks/*",
                "mappings": [
                    {"name": "content",               "source": "/document/chunks/*"},
                    {"name": "content_vector",        "source": "/document/chunks/*/content_vector"},
                    {"name": "metadata_storage_name", "source": "/document/metadata_storage_name"}
                ]
            }
        ],
        "parameters": {"projectionMode": "skipIndexingParentDocuments"}
    }
}
ok(call("put", f"/skillsets/{SKILLSET}", skillset_def), "Skillset created")

# ── STEP 5: create indexer ────────────────────────────────
print("\n[5] Creating indexer...")
indexer_def = {
    "name":            INDEXER,
    "dataSourceName":  DATASRC,
    "skillsetName":    SKILLSET,
    "targetIndexName": INDEX,
    # fieldMappings and outputFieldMappings not needed —
    # indexProjections in the skillset handles all field routing
    "parameters": {"configuration": {"dataToExtract": "contentAndMetadata"}}
}
ok(call("put", f"/indexers/{INDEXER}", indexer_def), "Indexer created")

# ── STEP 6: run & poll ────────────────────────────────────
print("\n[6] Running indexer...")
ok(call("post", f"/indexers/{INDEXER}/run"), "Indexer started")

print("\n[7] Polling indexer status...")
for i in range(12):
    time.sleep(5)
    r = call("get", f"/indexers/{INDEXER}/status").json()
    last = r.get("lastResult", {})
    status     = last.get("status", "unknown")
    processed  = last.get("itemsProcessed", 0)
    failed     = last.get("itemsFailed", 0)
    print(f"  [{i*5}s] {status}  processed={processed}  failed={failed}")

    if status in ("success", "transientFailure", "persistentFailure"):
        errors = last.get("errors", [])
        if errors:
            print("\n  ERRORS:")
            for e in errors[:5]:
                print(f"    message: {e.get('errorMessage')}")
                print(f"    details: {e.get('details','')}")
        else:
            print(f"\n  ✅ Done — {processed} document(s) indexed successfully")
        break
