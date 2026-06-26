import os
from dotenv import load_dotenv
load_dotenv()

print("=" * 55)
print("HOMESITE CLAIMS — CONNECTION TESTS")
print("=" * 55)

# ── TEST 1: Azure OpenAI (chat) ──────────────────────────
print("\n[1] Azure OpenAI — Chat (gpt-4.1-mini)...")
try:
    from openai import AzureOpenAI
    client = AzureOpenAI(
        azure_endpoint=os.getenv("AZURE_OPENAI_ENDPOINT"),
        api_key=os.getenv("AZURE_OPENAI_KEY"),
        api_version="2024-02-01"
    )
    resp = client.chat.completions.create(
        model=os.getenv("AZURE_OPENAI_DEPLOYMENT"),
        messages=[{"role": "user", "content": "Reply with OK only."}],
        max_tokens=5
    )
    print(f"  ✅ Connected — response: {resp.choices[0].message.content.strip()}")
except Exception as e:
    print(f"  ❌ Failed: {e}")

# ── TEST 2: Azure OpenAI (embeddings) ───────────────────
print("\n[2] Azure OpenAI — Embeddings (text-embedding-3-small)...")
try:
    resp = client.embeddings.create(
        model=os.getenv("AZURE_EMBEDDING_DEPLOYMENT"),
        input="test embedding"
    )
    dims = len(resp.data[0].embedding)
    print(f"  ✅ Connected — vector dimensions: {dims}")
except Exception as e:
    print(f"  ❌ Failed: {e}")

# ── TEST 3: Azure AI Search ──────────────────────────────
print("\n[3] Azure AI Search (policy-index)...")
try:
    from azure.search.documents import SearchClient
    from azure.core.credentials import AzureKeyCredential
    search_client = SearchClient(
        endpoint=os.getenv("AZURE_SEARCH_ENDPOINT"),
        index_name=os.getenv("AZURE_SEARCH_INDEX"),
        credential=AzureKeyCredential(os.getenv("AZURE_SEARCH_KEY").strip("'"))
    )
    results = list(search_client.search("collision", top=1))
    print(f"  ✅ Connected — got {len(results)} result(s) from index")
    for r in results[:3]:
        print(f"     • {list(r.keys())}")
except Exception as e:
    print(f"  ❌ Failed: {e}")

# ── TEST 4: Azure Blob Storage ───────────────────────────
print("\n[4] Azure Blob Storage (policy-documents)...")
try:
    from azure.storage.blob import BlobServiceClient
    blob_client = BlobServiceClient.from_connection_string(
        os.getenv("AZURE_BLOB_CONNECTION_STRING")
    )
    container = blob_client.get_container_client(os.getenv("AZURE_BLOB_CONTAINER"))
    blobs = list(container.list_blobs())
    print(f"  ✅ Connected — {len(blobs)} file(s) in container")
    for b in blobs[:5]:
        print(f"     • {b.name}")
except Exception as e:
    print(f"  ❌ Failed: {e}")

print("\n" + "=" * 55)
