"""
HomeSite Adjuster Copilot — Phase 2, rebuilt from scratch.

This is the seed. We grow it, milestone by milestone, into a LangGraph agent:
  MCP tools over the dashboard API -> a StateGraph -> a checkpointer for saved
  chat history -> human-in-the-loop approval before any claim is changed.

Run:   python3.11 -m uvicorn main:app --port 8200 --reload
Check: curl http://localhost:8200/
"""
from fastapi import FastAPI

app = FastAPI(title="Adjuster Copilot")


@app.get("/")
def hello():
    return {"message": "hello world", "service": "adjuster-copilot", "phase": 2}
