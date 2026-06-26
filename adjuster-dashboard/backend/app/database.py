"""SQLite engine + session helpers. One file: dashboard.db."""
import os
from sqlmodel import SQLModel, create_engine, Session

DB_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "dashboard.db")
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
