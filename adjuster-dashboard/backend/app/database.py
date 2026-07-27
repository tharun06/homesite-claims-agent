"""SQLite engine + session helpers. One file: dashboard.db."""
import os
from sqlmodel import SQLModel, create_engine, Session

# DASHBOARD_DB lets the deployment put the file on a mounted Azure Files share
# (/data/dashboard.db) instead of ephemeral container disk. That makes the data
# survive restarts AND lets the copilot container read the same file for NL2SQL.
# sql_runtime.py reads the same variable. Defaults to the local path for dev.
DB_PATH = os.getenv("DASHBOARD_DB") or os.path.join(
    os.path.dirname(os.path.dirname(__file__)), "dashboard.db"
)
DB_URL  = f"sqlite:///{DB_PATH}"

# check_same_thread=False so FastAPI's threadpool can share the connection
engine = create_engine(DB_URL, echo=False, connect_args={"check_same_thread": False})


def init_db():
    """Create all tables. Safe to call repeatedly."""
    SQLModel.metadata.create_all(engine)


def get_session():
    """FastAPI dependency — yields a session per request."""
    with Session(engine) as session:
        yield session
