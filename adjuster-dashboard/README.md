# HomeSite Adjuster Dashboard

Phase 1 of the adjuster experience: an end-to-end dashboard over mock claim
data with role-based visibility and a live realtime feed. Built API-first so
Phase 2 (the Copilot) wraps the same endpoints as MCP tools.

## Architecture

```
adjuster-dashboard/
├── backend/   FastAPI + SQLite + WebSocket   (port 8100)
│   └── app/
│       ├── models.py        data model (typed SQLModel)
│       ├── seed.py          generates the mock book of business
│       ├── auth.py          mock JWT + identity
│       ├── scoping.py       role-based "who sees what"  ← reused by MCP later
│       ├── geo.py           haversine proximity
│       ├── realtime/        WebSocket hub + event simulator
│       └── api/             REST routers  ← these become MCP tools in Phase 2
└── frontend/  Vite + React                   (port 5173)
```

## Roles (Standard 4)

| Role | Sees | Can do |
|------|------|--------|
| Adjuster | own assigned claims | view, note, change status |
| Senior Adjuster | whole team's claims | + reassign |
| SIU Investigator | fraud-flagged queue | view, note |
| Admin / Manager | everything + metrics | full |

Visibility is enforced in `scoping.py` — one source of truth for both the
REST API and the realtime hub.

## Run it

**1. Backend** (terminal 1):
```bash
cd adjuster-dashboard/backend
pip3.11 install -r requirements.txt
python3.11 -m app.seed          # build/refresh the database (once)
python3.11 -m uvicorn app.main:api --reload --port 8100
```
API docs: http://localhost:8100/docs

**2. Frontend** (terminal 2):
```bash
cd adjuster-dashboard/frontend
npm install
npm run dev
```
Open http://localhost:5173 and pick any user to sign in.

## Mock data volumes

5 teams · 25 users · 200 customers/policies/vehicles · 400 claims ·
~2,200 conversation messages · ~600 tasks · 40 geo-located repair shops.
Re-run `python3.11 -m app.seed` to regenerate. Change the `N_*` constants in
`seed.py` to scale.

## Realtime

The event simulator (`realtime/simulator.py`) stands in for telephony +
workflow. Every few seconds it persists a real event and pushes it over the
WebSocket — a voice→text message lands, a status advances, a claim is
reassigned, metrics tick. Delivery is role-scoped server-side.

## Phase 2 — where the AI plugs in

Nothing here gets thrown away. The Copilot reuses:

| Phase 1 asset | Phase 2 use |
|---------------|-------------|
| REST endpoints in `api/` | wrapped 1:1 as **MCP tools** |
| `auth.py` token + `scoping.py` | per-caller tool authorization (Copilot inherits visibility) |
| `geo.py` `repair-shops` endpoint | `find_repairs_near` tool; swap haversine for Azure Maps + spatial index |
| `models.py` typed schemas | tool input/output contracts |

Example Copilot questions and the tools they map to:
- "latest status of claim X" → `GET /claims/{id}`
- "status of VIN …" → `GET /vehicles/{vin}`
- "my work last week" → `GET /me/assignments`
- "pending items" → `GET /me/tasks`
- "repairs near this claim" → `GET /repair-shops?claim_id=…`
