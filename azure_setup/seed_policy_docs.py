"""Seed the policy knowledge base: mirror data/policies -> blob, then reindex.

Uploads every .txt in data/policies to the blob container, deletes any blob no
longer present locally (removes old stubs), clears the search index, and reruns
the indexer so policy-index reflects exactly the local document set.

Safe to re-run. Run:  python azure_setup/seed_policy_docs.py
"""
import os
import time
from pathlib import Path

import httpx
from azure.storage.blob import ContainerClient, ContentSettings
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")

SEARCH_ENDPOINT = os.getenv("AZURE_SEARCH_ENDPOINT", "").rstrip("/")
SEARCH_KEY = os.getenv("AZURE_SEARCH_KEY", "")
SEARCH_INDEX = os.getenv("AZURE_SEARCH_INDEX", "")
BLOB_CONN = os.getenv("AZURE_BLOB_CONNECTION_STRING", "")
BLOB_CONTAINER = os.getenv("AZURE_BLOB_CONTAINER", "")
INDEXER = "policy-indexer"
POLICIES_DIR = ROOT / "data" / "policies"

HDRS = {"api-key": SEARCH_KEY, "Content-Type": "application/json"}
DOC_API = "2023-11-01"   # index/document operations
IDX_API = "2024-07-01"   # indexer operations (matches the setup scripts)
CLIENT = httpx.Client(timeout=httpx.Timeout(60.0))


def sync_blobs():
    cc = ContainerClient.from_connection_string(BLOB_CONN, BLOB_CONTAINER)
    local = {p.name: p for p in POLICIES_DIR.glob("*.txt")}
    existing = {b.name for b in cc.list_blobs()}

    for name, path in sorted(local.items()):
        with open(path, "rb") as fh:
            cc.upload_blob(name, fh, overwrite=True,
                           content_settings=ContentSettings(content_type="text/plain"))
        print(f"  uploaded {name}")

    for name in sorted(existing - set(local)):
        cc.delete_blob(name)
        print(f"  deleted stale blob {name}")

    return sorted(local)


def clear_index():
    idx = CLIENT.get(f"{SEARCH_ENDPOINT}/indexes/{SEARCH_INDEX}?api-version={DOC_API}", headers=HDRS).json()
    key = next(f["name"] for f in idx["fields"] if f.get("key"))
    r = CLIENT.post(f"{SEARCH_ENDPOINT}/indexes/{SEARCH_INDEX}/docs/search?api-version={DOC_API}",
                   headers=HDRS, json={"search": "*", "select": key, "top": 1000})
    keys = [d[key] for d in r.json().get("value", [])]
    if keys:
        CLIENT.post(f"{SEARCH_ENDPOINT}/indexes/{SEARCH_INDEX}/docs/index?api-version={DOC_API}",
                   headers=HDRS, json={"value": [{"@search.action": "delete", key: k} for k in keys]})
    print(f"  cleared {len(keys)} existing index docs (key field: {key})")


def reindex():
    status_url = f"{SEARCH_ENDPOINT}/indexers/{INDEXER}/status?api-version={IDX_API}"
    prev_start = (CLIENT.get(status_url, headers=HDRS).json().get("lastResult") or {}).get("startTime")

    CLIENT.post(f"{SEARCH_ENDPOINT}/indexers/{INDEXER}/reset?api-version={IDX_API}", headers=HDRS)
    CLIENT.post(f"{SEARCH_ENDPOINT}/indexers/{INDEXER}/run?api-version={IDX_API}", headers=HDRS)
    print("  indexer reset + run triggered; polling for the new run...")

    for _ in range(60):
        time.sleep(6)
        last = CLIENT.get(status_url, headers=HDRS).json().get("lastResult") or {}
        if last.get("startTime") == prev_start:
            continue  # old run still showing; wait for the new one to register
        status = last.get("status")
        print(f"    status={status} processed={last.get('itemsProcessed')} failed={last.get('itemsFailed')}")
        if status in ("success", "transientFailure", "error"):
            for e in (last.get("errors") or [])[:3]:
                print("    ERROR:", e.get("errorMessage"))
            break


def main():
    print("1) syncing blobs (data/policies -> container)...")
    docs = sync_blobs()
    print("2) clearing the search index...")
    clear_index()
    print("3) reindexing (chunk + embed via the skillset)...")
    reindex()
    cnt = CLIENT.get(f"{SEARCH_ENDPOINT}/indexes/{SEARCH_INDEX}/docs/$count?api-version={DOC_API}",
                    headers=HDRS).text.strip()
    print(f"\nDONE. local docs: {len(docs)} | index chunk count: {cnt}")


if __name__ == "__main__":
    main()
