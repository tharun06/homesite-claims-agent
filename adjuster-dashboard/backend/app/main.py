"""
Adjuster Dashboard API.

Run:  uvicorn app.main:api --reload --port 8100
Docs: http://localhost:8100/docs
"""
import asyncio

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Query
from fastapi.middleware.cors import CORSMiddleware
from jose import jwt, JWTError
from sqlmodel import Session

from app.config import JWT_SECRET, JWT_ALGORITHM
from app.database import init_db, engine
from app.models import User
from app.realtime.hub import hub
from app.realtime.simulator import run_simulator, stop_simulator
from app.api import routes_auth, routes_claims, routes_work, routes_directory

api = FastAPI(title="HomeSite Adjuster Dashboard API")

api.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],          # dev only
    allow_methods=["*"],
    allow_headers=["*"],
)

api.include_router(routes_auth.router, tags=["auth"])
api.include_router(routes_claims.router, tags=["claims"])
api.include_router(routes_work.router, tags=["work"])
api.include_router(routes_directory.router, tags=["directory"])


@api.on_event("startup")
async def startup():
    init_db()
    asyncio.create_task(run_simulator())


@api.on_event("shutdown")
def shutdown():
    stop_simulator()


@api.get("/health")
def health():
    return {"ok": True, "ws_connections": hub.connection_count}


@api.websocket("/ws")
async def websocket_endpoint(ws: WebSocket, token: str = Query(...)):
    """Live event stream. Client connects with ?token=<jwt>. Role-scoped delivery."""
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
        user_id = int(payload.get("sub"))
    except (JWTError, TypeError, ValueError):
        await ws.close(code=1008)
        return

    with Session(engine) as s:
        user = s.get(User, user_id)
    if not user:
        await ws.close(code=1008)
        return

    await hub.connect(ws, user)
    try:
        while True:
            await ws.receive_text()   # keepalive; client may ping
    except WebSocketDisconnect:
        await hub.disconnect(ws)
