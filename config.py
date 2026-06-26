import os
from dotenv import load_dotenv

# reads the .env file and loads all values into os.environ
load_dotenv()

# ── Azure OpenAI ──────────────────────────────
AZURE_OPENAI_ENDPOINT = os.getenv("AZURE_OPENAI_ENDPOINT")
AZURE_OPENAI_KEY = os.getenv("AZURE_OPENAI_KEY")
AZURE_OPENAI_DEPLOYMENT = os.getenv("AZURE_OPENAI_DEPLOYMENT")
AZURE_EMBEDDING_DEPLOYMENT = os.getenv("AZURE_EMBEDDING_DEPLOYMENT")

# ── Azure AI Search ───────────────────────────
AZURE_SEARCH_ENDPOINT = os.getenv("AZURE_SEARCH_ENDPOINT")
AZURE_SEARCH_KEY = os.getenv("AZURE_SEARCH_KEY")
AZURE_SEARCH_INDEX = os.getenv("AZURE_SEARCH_INDEX")

# ── Azure Blob Storage ────────────────────────
AZURE_BLOB_CONNECTION_STRING = os.getenv("AZURE_BLOB_CONNECTION_STRING")
AZURE_BLOB_CONTAINER = os.getenv("AZURE_BLOB_CONTAINER")

# ── LangSmith ─────────────────────────────────
LANGCHAIN_TRACING_V2 = os.getenv("LANGCHAIN_TRACING_V2")
LANGCHAIN_API_KEY = os.getenv("LANGCHAIN_API_KEY")
LANGCHAIN_PROJECT = os.getenv("LANGCHAIN_PROJECT")

# ── Azure Video Indexer ────────────────────────
AZURE_VIDEO_INDEXER_ACCOUNT_ID   = os.getenv("AZURE_VIDEO_INDEXER_ACCOUNT_ID")
AZURE_VIDEO_INDEXER_LOCATION     = os.getenv("AZURE_VIDEO_INDEXER_LOCATION")
AZURE_VIDEO_INDEXER_ACCESS_TOKEN = os.getenv("AZURE_VIDEO_INDEXER_ACCESS_TOKEN")