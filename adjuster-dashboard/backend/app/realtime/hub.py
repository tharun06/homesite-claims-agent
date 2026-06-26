"""
WebSocket hub with role-scoped delivery.

A claim event only reaches a connection if that user's role allows seeing
the claim — the same visibility rules as the REST API, enforced server-side.
"""
import asyncio
from dataclasses import dataclass, field
from typing import Optional

from fastapi import WebSocket
from sqlmodel import Session

from app.database import engine
from app.models import Claim, User, Role


@dataclass
class Connection:
    ws: WebSocket
    user_id: int
    role: str
    team_id: Optional[int]


class Hub:
    def __init__(self):
        self._conns: list[Connection] = []
        self._lock = asyncio.Lock()

    async def connect(self, ws: WebSocket, user: User):
        await ws.accept()
        async with self._lock:
            self._conns.append(Connection(ws, user.id, user.role.value, user.team_id))

    async def disconnect(self, ws: WebSocket):
        async with self._lock:
            self._conns = [c for c in self._conns if c.ws is not ws]

    # ── delivery rules — mirror scoping.py ──
    @staticmethod
    def _can_see(conn: Connection, vis: dict) -> bool:
        if conn.role == Role.ADMIN.value:
            return True
        if conn.role == Role.SIU_INVESTIGATOR.value:
            return vis["fraud_flagged"]
        if conn.role == Role.SENIOR_ADJUSTER.value:
            return vis["adjuster_team_id"] == conn.team_id
        return vis["adjuster_id"] == conn.user_id   # adjuster

    def _visibility(self, claim_id: int) -> Optional[dict]:
        with Session(engine) as s:
            claim = s.get(Claim, claim_id)
            if not claim:
                return None
            adj = s.get(User, claim.adjuster_id)
            return {"adjuster_id": claim.adjuster_id,
                    "adjuster_team_id": adj.team_id if adj else None,
                    "fraud_flagged": claim.fraud_flagged}

    async def broadcast_claim_event(self, claim_id: int, event_type: str, payload: dict):
        vis = self._visibility(claim_id)
        if vis is None:
            return
        msg = {"type": event_type, "claim_id": claim_id, "payload": payload}
        await self._send_filtered(msg, vis)

    async def broadcast_metrics_tick(self):
        """Tell every client to refresh its (scoped) metrics."""
        await self._send_all({"type": "metrics_tick"})

    async def _send_filtered(self, msg: dict, vis: dict):
        dead = []
        for c in list(self._conns):
            if self._can_see(c, vis):
                try:
                    await c.ws.send_json(msg)
                except Exception:
                    dead.append(c.ws)
        for ws in dead:
            await self.disconnect(ws)

    async def _send_all(self, msg: dict):
        dead = []
        for c in list(self._conns):
            try:
                await c.ws.send_json(msg)
            except Exception:
                dead.append(c.ws)
        for ws in dead:
            await self.disconnect(ws)

    @property
    def connection_count(self) -> int:
        return len(self._conns)


hub = Hub()
