import requests
import json
from config import (
    AZURE_SEARCH_ENDPOINT,
    AZURE_SEARCH_KEY,
    AZURE_OPENAI_ENDPOINT,
    AZURE_OPENAI_KEY,
    AZURE_EMBEDDING_DEPLOYMENT
)

SKILLSET_NAME = "policy-skillset"


def create_skillset():
    # use newer API version that supports all skill properties
    url = f"{AZURE_SEARCH_ENDPOINT}/skillsets/{SKILLSET_NAME}?api-version=2024-07-01"

    headers = {
        "Content-Type": "application/json",
        "api-key": AZURE_SEARCH_KEY
    }

    skillset = {
        "name": SKILLSET_NAME,
        "description": "Split and embed policy documents for RAG",
        "skills": [

            # SKILL 1: SPLIT into chunks
            {
                "@odata.type": "#Microsoft.Skills.Text.SplitSkill",
                "name": "split-skill",
                "description": "Split policy docs into chunks",
                "context": "/document",
                "defaultLanguageCode": "en",
                "textSplitMode": "pages",
                "maximumPageLength": 512,
                "inputs": [
                    {
                        "name": "text",
                        "source": "/document/content"
                    }
                ],
                "outputs": [
                    {
                        "name": "textItems",
                        "targetName": "chunks"
                    }
                ]
            },

            # SKILL 2: EMBED each chunk into a vector
            {
                "@odata.type": "#Microsoft.Skills.Text.AzureOpenAIEmbeddingSkill",
                "name": "embedding-skill",
                "description": "Embed chunks using Azure OpenAI",
                "context": "/document/chunks/*",
                "resourceUri": AZURE_OPENAI_ENDPOINT,
                "apiKey": AZURE_OPENAI_KEY,
                "deploymentId": AZURE_EMBEDDING_DEPLOYMENT,
                "modelName": "text-embedding-3-small",
                "dimensions": 1536,
                "inputs": [
                    {
                        "name": "text",
                        "source": "/document/chunks/*"
                    }
                ],
                "outputs": [
                    {
                        "name": "embedding",
                        "targetName": "content_vector"
                    }
                ]
            }
        ]
    }

    response = requests.put(url, headers=headers, json=skillset)

    # CORRECT — includes 204, checks for body before printing
    if response.status_code in (200, 201, 204):
        print(f"✅ Skillset '{SKILLSET_NAME}' created successfully")
    else:
        print(f"❌ Failed to create skillset: {response.status_code}")
        
    if response.text:
        print(json.dumps(response.json(), indent=2))


if __name__ == "__main__":
    create_skillset()