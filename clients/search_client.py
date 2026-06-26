from azure.search.documents import SearchClient
from azure.core.credentials import AzureKeyCredential
from config import (
    AZURE_SEARCH_ENDPOINT,
    AZURE_SEARCH_KEY,
    AZURE_SEARCH_INDEX
)


def search_repair_costs(query: str, top_k: int = 3) -> list:
    """
    Searches the repair cost reference guide in Azure AI Search.
    Returns relevant cost range chunks for the vehicle and damage type.
    """
    print(f"  [Azure Search] searching repair costs: {query[:60]}...")

    client = SearchClient(
        endpoint=AZURE_SEARCH_ENDPOINT,
        index_name=AZURE_SEARCH_INDEX,
        credential=AzureKeyCredential(AZURE_SEARCH_KEY.strip("'"))
    )

    results = client.search(
        search_text=query,
        top=top_k,
        select=["content", "metadata_storage_name"],
        filter="metadata_storage_name eq 'REPAIR-COSTS-001.txt'"
    )

    chunks = [r.get("content", "") for r in results]
    print(f"  [Azure Search] found {len(chunks)} repair cost chunks")
    return chunks


def search_policy_docs(query: str, policy_id: str = None, top_k: int = 3) -> list:
    """
    REAL Azure AI Search call.
    """
    print(f"  [Azure Search] searching for: {query[:60]}...")

    if not AZURE_SEARCH_KEY or "your_azure" in AZURE_SEARCH_KEY:
        raise ValueError("Azure Search is not configured. Mock response disabled for testing.")

    client = SearchClient(
        endpoint=AZURE_SEARCH_ENDPOINT,
        index_name=AZURE_SEARCH_INDEX,
        credential=AzureKeyCredential(AZURE_SEARCH_KEY)
    )

    results = client.search(
        search_text=query,
        top=top_k,
        select=["content", "metadata_storage_name", "metadata_storage_path"]
    )

    clauses = []

    for i, result in enumerate(results, start=1):
        content = result.get("content", "")
        file_name = result.get("metadata_storage_name", "unknown file")
        file_path = result.get("metadata_storage_path", "unknown path")

        print(f"\n  [Azure Search RESULT {i}]")
        print(f"  File: {file_name}")
        print(f"  Path: {file_path}")
        print(f"  Content preview: {content[:300]}")

        clauses.append(content)

    print(f"\n  [Azure Search] found {len(clauses)} clauses")
    return clauses