# Architecture

Everything below is what is actually deployed, verified against the resource
group and the code — not a target state.

**One line:** a claims dashboard with an embedded LangGraph copilot, whose nine
tools are also published as an OAuth-protected MCP server so external LLM
clients can drive them.

The shape worth remembering: **the MCP server is a client of the backend, not a
second door into the database.** One authorization implementation, reached three
different ways.

---

## System

```
 CLIENTS         ┌──────────────┐   ┌───────────────┐   ┌──────────────────┐
                 │  Browser     │   │    Claude     │   │     ChatGPT      │
                 │  (adjuster)  │   │  (connector)  │   │   (connector)    │
                 └──────┬───────┘   └───────┬───────┘   └────────┬─────────┘
                        │                   └──────────┬─────────┘
                        │                              │ OAuth 2.1 + Entra
                        │                              │ Streamable HTTP
 ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─│─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ │─ ─ ─ ─ ─ ─ ─ ─ ─ ─
 EDGE                   │                              │
        ┌───────────────▼────────────┐                 │
        │  Azure Static Web Apps     │   managed TLS + CDN. No gateway,
        │  React + Vite  (Free)      │   no Front Door — SWA and Container
        └───────────────┬────────────┘   Apps ingress each front themselves.
                        │
                        │  REST + WebSocket        ┌───────────────────┐
                        │  NDJSON stream           │                   │
 ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─│─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ │─ ─ ─ ─ ─ ─ ─ ─ ─ ─
 SERVICES   Azure Container Apps · Consumption · scale-to-zero
                        │                          │
        ┌───────────────▼──────────┐   ┌───────────▼──────────────┐
        │   homesite-copilot       │   │      homesite-mcp        │
        │   FastAPI + LangGraph    │   │      FastMCP             │
        │                          │   │  Streamable HTTP  /mcp   │
        │  agent ⇄ action nodes    │   │  9 tools                 │
        │  NL2SQL subgraph         │   │  Entra JWT verification  │
        │  checkpointer            │   └───────────┬──────────────┘
        └──┬──────────┬────────────┘               │
           │          │ spawns MCP over stdio      │ HTTP + bearer
           │          └────────────────────────────┤
           │                          ┌────────────▼─────────────┐
           │                          │    homesite-backend      │
           │                          │    FastAPI + SQLModel    │
           │                          │  JWT auth · scope_claims │
           │                          │  WebSocket hub           │
           │                          └────────────┬─────────────┘
           │                                       │
 ─ ─ ─ ─ ─ │─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─│─ ─ ─ ─ ─ ─ ─ ─ ─ ─
 DATA      │                                       │
           │  read-only role                       │  read-write
           │  (copilot_ro)                         │
           └───────────────┬───────────────────────┘
                           │
              ┌────────────▼─────────────┐      ┌────────────────────────┐
              │  PostgreSQL Flexible     │      │   Azure AI Search      │
              │  · claims, users, teams  │      │   (Free tier)          │
              │  · LangGraph checkpoints │      │   policy-index         │
              └──────────────────────────┘      │   claims-index         │
                                                │   HNSW + semantic      │
              ┌──────────────────────────┐      └───────────▲────────────┘
              │  Azure OpenAI            │                  │ indexer
              │  gpt-4.1-mini      (100) │      ┌───────────┴────────────┐
              │  text-embed-3-small(500) │      │  Blob Storage          │
              └──────────────────────────┘      │  policy-documents      │
                                                └────────────────────────┘
 ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─
 PLATFORM   GitHub Actions → ghcr.io (SHA-tagged) → manual az containerapp
            update · Entra ID app registrations · Log Analytics
```

---

## The four request paths

### 1. Dashboard read — no LLM involved

```
React ──JWT──► backend ──scope_claims()──► Postgres
```

`get_current_user` validates the token and loads the **User row**; `scope_claims`
turns the caller's role into a WHERE clause. This is the single authorization
implementation the other paths reuse.

### 2. Copilot chat

```
React ──POST /chat──► copilot
                        │
                        ├─ agent node ──► Azure OpenAI (tool schemas bound)
                        │
                        ├─ action node ──► MCP (stdio subprocess)
                        │                    └──► backend ──► Postgres
                        │
                        ├─ query_claims_data ──► NL2SQL subgraph
                        │                          └──► Postgres (copilot_ro)
                        │
                        └─ checkpointer ──► Postgres
                        
       ◄──NDJSON stream── {"status"} {"delta"} … {"done"}
```

The agent⇄action loop runs until the model stops emitting tool calls. Writes stop
at `interrupt_before=["action"]` and wait for `/approve`.

### 3. External LLM client

```
Claude ──OAuth 2.1──► Entra ──JWT──► homesite-mcp ──bearer──► backend ──► Postgres
```

Same tools, same backend, same `scope_claims`. Only the identity layer differs.

### 4. Document ingestion

```
data/policies/*.txt ──► Blob (policy-documents)
                          └──► indexer (scheduled, incremental)
                                 └──► skillset: split 512/64 → embed
                                        └──► index projections (1 doc per chunk)
                                               └──► policy-index
```

No chunking or embedding code runs in the application.

---

## Trust boundaries

The part worth talking about. Four boundaries, each enforced somewhere the layer
above it cannot reach.

| boundary | mechanism | what it stops |
| --- | --- | --- |
| browser → backend | HS256 JWT, `sub` → live User row lookup | role comes from the DB at request time, not the token |
| external LLM → MCP | Entra RS256, JWKS, **audience** + issuer + expiry | a valid token minted for another API in the tenant |
| any caller → claims | `scope_claims()` as a WHERE clause | reading another adjuster's book |
| LLM-written SQL → DB | `copilot_ro` role: SELECT + TEMPORARY only | writes, DDL — enforced by Postgres, not by a parser |

Two properties fall out of this:

**Two token systems, validated differently.** Internal tokens are first-party
HS256 — signature and expiry, no audience check, because there is no third party
to distinguish. Entra tokens are RS256 with audience and issuer checked, because
there is.

**Two database identities.** The backend connects read-write. The NL2SQL subgraph
connects as `copilot_ro` through a separate DSN, and queries a per-request TEMP
VIEW rather than the base table. A model-authored query cannot write, and cannot
name `claim`.

---

## The whiteboard version

Sixty seconds, five boxes. Draw this, not the full diagram:

```
   Browser ──► React (Static Web Apps)
                  │
                  ▼
             Copilot ──► LangGraph ──► Azure OpenAI
             (FastAPI)      │
                            ├──► MCP tools ──► Backend ──► Postgres
                            ├──► NL2SQL ─────────────────► Postgres (read-only)
                            └──► checkpointer ───────────► Postgres

   Claude / ChatGPT ──OAuth──► same MCP tools ──► Backend
```

Then say the sentence that makes it click:

> "The MCP server is a client of the backend, not a second door to the database.
> So the copilot, Claude and the dashboard all go through one authorization
> implementation."

---

## Deliberately absent

Naming these prevents "why didn't you use X" from sounding like an oversight.

| not there | why |
| --- | --- |
| Application Gateway / Front Door | SWA and Container Apps ingress each provide managed TLS and routing. A gateway would be a layer with no job until there is a WAF or one domain across services. |
| Redis / cache layer | No hot path warrants it yet. The nearest real candidate is caching embeddings for repeated policy questions. |
| API Management | ~$50–210/month floor for a gateway not yet needed. |
| VNet / private endpoints | Actively wrong here — connectors require public reachability. |
| Message queue | Every path is synchronous request/response. A queue would add a failure mode without removing one. |
| Separate vector database | Azure AI Search provides hybrid and reranking natively. pgvector would simplify relational filtering and lose both. |

---

## Deployment facts

| | |
| --- | --- |
| resource group | `rg-homesite-claims` (apps) · `homesite-claims` (AI services) |
| container env | `homesite-env`, Consumption, scale-to-zero |
| replicas | **min 0 / max 1 on all three apps** — a cost guard that is also a hard concurrency ceiling |
| registry | ghcr.io, SHA-tagged. ACR Basic would be ~$5/month standing. |
| deploy | GitHub Actions builds; `az containerapp update` is manual |
| quotas | gpt-4.1-mini 100k TPM · text-embedding-3-small 500k TPM, set on the deployment, **not in the repo** |
| IaC | none — the single biggest gap |
| spend | $0.00 |
