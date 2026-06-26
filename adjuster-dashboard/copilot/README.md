# Adjuster Copilot — Phase 2 (from scratch)

A blank-slate rebuild of the copilot as a **LangGraph agent**.

We build it hands-on, one milestone at a time:

0. **Seed** — `main.py`, a hello-world FastAPI service on `:8200`. ← you are here
1. **MCP tools** — typed tools over the dashboard API (`:8100`), auth-scoped per adjuster.
2. **LangGraph agent** — a `StateGraph` that chats and calls tools.
3. **Saved history** — a checkpointer + `thread_id` per conversation.
4. **Human-in-the-loop** — pause for approval before any claim is changed.
5. **Polish** — claim context, streaming, tool trace.

Later (Phase 3): RAG over policy docs + similar claims via Azure AI Search.

```
Run:   python3.11 -m uvicorn main:app --port 8200 --reload
Check: curl http://localhost:8200/   ->  {"message":"hello world",...}
```
