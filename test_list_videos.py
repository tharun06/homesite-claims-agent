import os
import requests
from dotenv import load_dotenv

load_dotenv()

ACCOUNT_ID = os.getenv("AZURE_VIDEO_INDEXER_ACCOUNT_ID")
LOCATION = os.getenv("AZURE_VIDEO_INDEXER_LOCATION")
ACCESS_TOKEN = os.getenv("AZURE_VIDEO_INDEXER_ACCESS_TOKEN")

url = f"https://api.videoindexer.ai/{LOCATION}/Accounts/{ACCOUNT_ID}/Videos"

params = {
    "accessToken": ACCESS_TOKEN
}

response = requests.get(url, params=params)

print("Status:", response.status_code)

if response.status_code >= 400:
    print(response.text)
    response.raise_for_status()

videos = response.json().get("results", [])

print(f"\nFound {len(videos)} videos\n")

for video in videos:
    print("Name:", video.get("name"))
    print("ID:", video.get("id"))
    print("State:", video.get("state"))
    print("-" * 40)