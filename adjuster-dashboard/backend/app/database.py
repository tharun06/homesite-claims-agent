"""Database engine + session helpers.

Runs on SQLite locally (zero setup) and PostgreSQL in Azure. Set DATABASE_URL to
switch, e.g.
    postgresql+psycopg://user:pass@host:5432/claims?sslmode=require

Why Postgres in deployment: container filesystems are ephemeral, and a file-based
DB cannot be shared between the backend and copilot containers. We tried an Azure
Files share first — it fails, because SQLite needs POSIX advisory locks and Azure
Files is SMB, which does not provide them ("database is locked").
"""
import os
from sqlmodel import SQLModel, create_engine, Session

# DASHBOARD_DB still works for pointing SQLite at another path (used by the
# copilot's NL2SQL layer in local dev).
DB_PATH = os.getenv("DASHBOARD_DB") or os.path.join(
    os.path.dirname(os.path.dirname(__file__)), "dashboard.db"
)

DB_URL = os.getenv("DATABASE_URL") or f"sqlite:///{DB_PATH}"

if DB_URL.startswith("sqlite"):
    # check_same_thread=False so FastAPI's threadpool can share the connection
    connect_args = {"check_same_thread": False}
    engine_kwargs = {}
else:
    connect_args = {}
    # A Burstable B1ms allows few connections, and Container Apps scales to zero
    # and back. Keep the pool tiny and recycle before the server drops idle
    # connections, or the first request after an idle period fails.
    engine_kwargs = {"pool_size": 5, "max_overflow": 2, "pool_pre_ping": True,
                     "pool_recycle": 900}

engine = create_engine(DB_URL, echo=False, connect_args=connect_args, **engine_kwargs)


def init_db():
    """Create all tables. Safe to call repeatedly."""
    SQLModel.metadata.create_all(engine)


def get_session():
    """FastAPI dependency — yields a session per request."""
    with Session(engine) as session:
        yield session
