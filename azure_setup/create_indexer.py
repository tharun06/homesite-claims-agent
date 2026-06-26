import requests
import json
from config import (
    AZURE_SEARCH_ENDPOINT,
    AZURE_SEARCH_KEY,
    AZURE_SEARCH_INDEX
)

INDEXER_NAME = "policy-indexer"
DATASOURCE_NAME = "policy-docs-source"
SKILLSET_NAME = "policy-skillset"


def create_indexer():
    url = f"{AZURE_SEARCH_ENDPOINT}/indexers/{INDEXER_NAME}?api-version=2024-07-01"

    headers = {
        "Content-Type": "application/json",
        "api-key": AZURE_SEARCH_KEY
    }

    indexer = {
        "name": INDEXER_NAME,
        "dataSourceName": DATASOURCE_NAME,
        "skillsetName": SKILLSET_NAME,
        "targetIndexName": AZURE_SEARCH_INDEX,
        "schedule": {
            "interval": "PT2H"
        },

        # maps blob metadata to index fields
        "fieldMappings": [
            {
                "sourceFieldName": "metadata_storage_path",
                "targetFieldName": "id",
                "mappingFunction": {
                    "name": "base64Encode"    # encodes the path as a safe key
                }
            },
            {
                "sourceFieldName": "metadata_storage_path",
                "targetFieldName": "metadata_storage_path"
            },
            {
                "sourceFieldName": "metadata_storage_name",
                "targetFieldName": "metadata_storage_name"
            },
            {
                "sourceFieldName": "content",
                "targetFieldName": "content"
            }
        ],

        # maps skillset outputs to index fields
        "outputFieldMappings": [
            {
                "sourceFieldName": "/document/chunks/*/content_vector",
                "targetFieldName": "content_vector"
            }
        ],

        "parameters": {
            "configuration": {
                "dataToExtract": "contentAndMetadata",
                "parsingMode": "default",
                "imageAction": "none"
            }
        }
    }

    response = requests.put(url, headers=headers, json=indexer)

    if response.status_code in (200, 201, 204):
        print(f"✅ Indexer '{INDEXER_NAME}' created/updated successfully")

        # delete and re-run to clear the failed state
        reset_url = f"{AZURE_SEARCH_ENDPOINT}/indexers/{INDEXER_NAME}/reset?api-version=2024-07-01"
        requests.post(reset_url, headers=headers)

        run_url = f"{AZURE_SEARCH_ENDPOINT}/indexers/{INDEXER_NAME}/run?api-version=2024-07-01"
        run_response = requests.post(run_url, headers=headers)

        if run_response.status_code == 202:
            print(f"✅ Indexer reset and triggered — check portal in 1 minute")
        else:
            print(f"⚠️  Trigger failed: {run_response.status_code}")
            if run_response.text:
                print(run_response.text)
    else:
        print(f"❌ Failed: {response.status_code}")
        if response.text:
            print(json.dumps(response.json(), indent=2))


if __name__ == "__main__":
    create_indexer()