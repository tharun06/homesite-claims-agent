import requests
import json
from config import AZURE_SEARCH_ENDPOINT, AZURE_SEARCH_KEY, AZURE_SEARCH_INDEX


def create_index():
    # delete existing index first so we can recreate cleanly
    delete_url = f"{AZURE_SEARCH_ENDPOINT}/indexes/{AZURE_SEARCH_INDEX}?api-version=2024-07-01"
    headers = {
        "Content-Type": "application/json",
        "api-key": AZURE_SEARCH_KEY
    }
    requests.delete(delete_url, headers=headers)
    print(f"  Deleted old index (if existed)")

    url = f"{AZURE_SEARCH_ENDPOINT}/indexes/{AZURE_SEARCH_INDEX}?api-version=2024-07-01"

    index = {
        "name": AZURE_SEARCH_INDEX,
        "fields": [
            {
                "name": "id",
                "type": "Edm.String",
                "key": True,
                "filterable": True,
                "retrievable": True
            },
            {
                "name": "content",
                "type": "Edm.String",
                "searchable": True,
                "retrievable": True
            },
            {
                "name": "metadata_storage_name",
                "type": "Edm.String",
                "filterable": True,
                "retrievable": True
            },
            {
                "name": "metadata_storage_path",
                "type": "Edm.String",
                "retrievable": True
            },
            {
                "name": "content_vector",
                "type": "Collection(Edm.Single)",
                "searchable": True,
                "retrievable": True,
                "dimensions": 1536,
                "vectorSearchProfile": "hnsw-profile"
            }
        ],
        "vectorSearch": {
            "algorithms": [
                {
                    "name": "hnsw-config",
                    "kind": "hnsw",
                    "hnswParameters": {
                        "metric": "cosine",
                        "m": 4,
                        "efConstruction": 400,
                        "efSearch": 500
                    }
                }
            ],
            "profiles": [
                {
                    "name": "hnsw-profile",
                    "algorithm": "hnsw-config"
                }
            ]
        }
    }

    response = requests.put(url, headers=headers, json=index)

    if response.status_code in (200, 201, 204):
        print(f"✅ Index '{AZURE_SEARCH_INDEX}' created successfully")
    else:
        print(f"❌ Failed: {response.status_code}")
        if response.text:
            print(json.dumps(response.json(), indent=2))


if __name__ == "__main__":
    create_index()