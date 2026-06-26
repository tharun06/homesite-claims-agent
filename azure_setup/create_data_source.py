import requests
import json
from config import (
    AZURE_SEARCH_ENDPOINT,
    AZURE_SEARCH_KEY,
    AZURE_BLOB_CONNECTION_STRING,
    AZURE_BLOB_CONTAINER
)

DATASOURCE_NAME = "policy-docs-source"


def create_data_source():
    """
    Creates the data source — tells Azure AI Search where
    to find the documents (our Blob Storage container).
    The indexer uses this to pull documents automatically.
    """

    url = f"{AZURE_SEARCH_ENDPOINT}/datasources/{DATASOURCE_NAME}?api-version=2023-11-01"

    headers = {
        "Content-Type": "application/json",
        "api-key": AZURE_SEARCH_KEY
    }

    data_source = {
        "name": DATASOURCE_NAME,
        "type": "azureblob",
        "credentials": {
            "connectionString": AZURE_BLOB_CONNECTION_STRING
        },
        "container": {
            "name": AZURE_BLOB_CONTAINER
        }
    }

    response = requests.put(url, headers=headers, json=data_source)

    if response.status_code in (200, 201, 204):
        print(f"✅ Data source '{DATASOURCE_NAME}' created successfully")
    else:
        print(f"❌ Failed to create data source: {response.status_code}")
        if response.text: 
            print(json.dumps(response.json(), indent=2))
        


if __name__ == "__main__":
    create_data_source()