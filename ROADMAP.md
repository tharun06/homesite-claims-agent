# Roadmap — from local pilot to a hosted, multi-client MCP service

**Goal:** host everything on Azure, and expose the MCP server *remotely* so external
AI clients (Claude, ChatGPT) and other internal services can use the claims tools
**without logging into the dashboard**. The MCP server stops being a private
subprocess of the copilot and becomes a product in its own right.

Built from research (Oct 2026) + a full read of this repo. Ordered by **dependency**,
not by wish-list. Each phase is independently shippable.

---

## ⚠️ Do these two things before anything else

### 0.1 — Pin the MCP SDK (urgent, 2 minutes)

MCP spec revision **2026-07-28** is the largest change since launch, and Python
**SDK v2 renames `FastMCP` → `MCPServer`**. `from mcp.server.fastmcp import FastMCP`
will break on upgrade.

```
mcp>=1.28,<2
```
Add to a new `adjuster-dashboard/copilot/requirements.txt`. Currently the copilot has
**no dependency manifest at all**, and the root `requirements.txt` omits `mcp`,
`langchain-mcp-adapters`, and `httpx`. Nothing is version-pinned anywhere — builds are
not reproducible.

### 0.2 — Three confirmed security bugs (fix before any public URL)

Verified by reading the code, not inferred:

| # | Bug | Where | Impact |
|---|-----|-------|--------|
| 1 | **HITL bypass** — `/approve` takes `thread_id` from the request body and never checks it belongs to the caller. Frontend uses the guessable `user-{id}`. | `copilot/main.py:64` | Any authenticated user can approve *someone else's* pending write. **Worst issue in the codebase.** |
| 2 | **2 of 3 write tools ungated** — `WRITE_TOOLS = {"update_claim_status"}` only. | `copilot/agent.py:29` | `add_note_to_claim` and `reassign_claim` execute with **no approval**. |
| 3 | **Router only inspects the last tool call** — `tool_calls[-1]`. | `copilot/agent.py` | A parallel batch with a write in a non-final position skips the approval gate entirely. |

Fix: key the checkpointer on `f"{user_id}:{thread_id}"` using the existing
`_resolve_caller(token)`; put all three write tools in `WRITE_TOOLS`; make the router
interrupt if **any** call in the batch is a write.

Also: `JWT_SECRET = "dev-secret-not-for-prod"` is hardcoded and committed
(`backend/app/config.py`), and `/auth/login` needs no password while `/auth/users`
is unauthenticated. Acceptable on localhost; **not** acceptable once anything has a
public URL.

---

## 💰 Cost rules (budget: $5–10 total, trial EXPIRED)

Current actual spend: **June $0.0736, July MTD $0.0838** — ~8¢/month, essentially all
Azure OpenAI tokens. Everything else already bills $0 (AI Search is on the **free**
SKU; both OpenAI resources are S0 pay-per-token; the ACA environment is Consumption).

**The whole deployment can be $0.00/month.** These grants are ALWAYS-FREE and recurring
— they are *not* trial benefits, so the expired trial doesn't affect them:

| Service | Always-free monthly grant |
|---|---|
| Container Apps (Consumption) | 180,000 vCPU-s + 360,000 GiB-s + **2M requests**, per subscription |
| Azure SQL Database (free offer) | 100,000 vCore-s + 32 GB data + 32 GB backup, per DB |
| Log Analytics | 5 GB ingestion, 31-day retention |
| Bandwidth | first 100 GB egress |
| Ingress / FQDN / managed TLS cert | **no meter exists at all** |

### The five rules that keep it at $0

1. **NEVER create an Azure Container Registry.** Basic is **$0.1666/day ≈ $5.07/mo**,
   standing, no free tier, charged with zero pulls — it would eat the entire budget.
   **Use `ghcr.io`** (free, unlimited pulls for public images).
2. **NEVER run `az containerapp up --source .` or `--repo`.** Both *silently create an
   ACR*. Use `az containerapp create --image ghcr.io/...` instead.
3. **Always pass `--min-replicas 0 --max-replicas 1`.** Scaled to zero = literally zero
   compute charge. The dangerous platform default is **`maxReplicas: 10`** — a crash-loop
   at 10 replicas burns 2.4× the entire monthly grant (~$8.57) in 24 hours.
4. **Never add a Dedicated workload profile, a private endpoint, or planned maintenance**
   — each triggers the **Dedicated Plan Management** meter at $0.10/hr ≈ **$73/mo**,
   even on a Consumption environment.
5. **Don't point an uptime monitor / cron pinger at the apps** — that defeats
   scale-to-zero and converts a $0 deployment into a paid one.

Already applied: Log Analytics daily quota capped at **0.2 GB** (was uncapped), and a
**$5 monthly budget** with email alerts at 50/80/100%.

⚠️ Free-grant usage is **invisible on the bill** until exceeded — you get no gradual
warning. The budget alert is the only early signal.

---

## Phase 1 — Containerize and deploy, one app at a time

**Target: Azure Container Apps**, into the `homesite-backend-env` environment that
already exists in `rg-homesite-claims`. Decided, not defaulted:

- App Service is **blocked** — this subscription has 0 dedicated vCPU quota (proven).
- Azure Functions: the MCP-extension path means rewriting all 9 tools into the
  trigger/binding model; the self-hosted path is preview + Flex-Consumption-only.
  Also a **230s HTTP timeout**, which is hostile to long agent turns.
- API Management as MCP gateway: GA, but **no Consumption tier support** → ~$50–210/mo
  floor. Disqualified on cost for a pilot.
- Container Apps consumption is the one option **already proven to provision here**,
  and Microsoft's own Python MCP tutorial uses this exact stack.

Order (each one deployable and verifiable on its own):

1. **Backend** (`:8100`) — everything depends on it.
2. **Frontend** (`:5180`) — static build; proves end-to-end reachability.
3. **Copilot** (`:8200`) — depends on backend.
4. **MCP server** — the new public product (Phase 3).

Per-service work: a `Dockerfile` (+ `.dockerignore`), `--host 0.0.0.0 --port $PORT`,
non-root user, config via env vars, and a `/healthz`.

Known blockers to clear as you go:
- Frontend hardcodes all three service URLs as `localhost` literals.
- `allow_origins=["*"]` in **both** `backend/app/main.py` and `copilot/main.py`.
- A fresh container comes up with an **empty DB** — nothing seeds on startup, and
  login is "pick a seeded user", so an empty DB = an unusable app.
- `/ws?token=<jwt>` puts a bearer token in a **query string**, which ingress and CDN
  layers log. Move it to a header or a short-lived ticket.

---

## ⏸️ Deferred — continuous deployment for containers (the `:latest` gap)

**Known gap, deliberately parked.** The backend workflow builds a new image on
every push, but **Azure never picks it up**. Container Apps pulled `ghcr.io/...:latest`
once at creation and pinned that revision to the image it got; re-pushing the same
tag notifies nothing. The workflow goes green, the image is in the registry, and the
running app keeps serving old code — a silent no-op.

(The frontend does *not* have this problem: the Static Web Apps action uploads files
straight to Azure, so it genuinely auto-deploys.)

**The fix, when we come back to it:**
1. Deploy by **immutable SHA tag**, never `:latest` — `:latest` is the root cause.
2. Add a deploy step after the build: `az containerapp update --image ghcr.io/...:${{ github.sha }}`.
3. Authenticate GitHub → Azure with **OIDC federated credentials** (no stored secret):
   app registration + service principal + a federated credential scoped to this repo,
   plus an RBAC role assignment on the resource group.
   ✅ Verified: app registration **is** permitted in this tenant.
4. Until then, deploying backend/copilot changes is a **manual** `az containerapp update`.

---

## Phase 2 — Get off SQLite (blocks everything multi-container)

`database.py` writes `dashboard.db` to local disk. Container Apps storage is
**ephemeral** — every restart, revision, or scale-to-zero wipes all claims and users.
It also cannot be shared between the backend and MCP containers.

→ **Azure SQL Database, Free Offer.** ~~PostgreSQL Flexible Server~~ — corrected after
verification: the Postgres 750-hour B1ms free tier is a **12-month Free Account trial
benefit only**, which this subscription no longer has. It would cost real money.

Azure SQL's free offer *is* **always-free** ("free forever, with monthly limits…
available regardless of your Azure subscription type"): **100,000 vCore-seconds +
32 GB data + 32 GB backup, per database, per month**, up to 10 DBs.

Non-negotiable settings:
- **"Behavior when free limit reached" → "Auto-pause until next month."** This is a
  hard $0 stop. The alternative ("continue for additional charges") is **irreversible**.
- **Create it in `westus2`** to match the Container Apps environment. The first free
  database **permanently locks the region** for every future free DB in the subscription.
- Tame the SQLAlchemy pool (`poolclass=NullPool` or short recycle) — auto-pause needs
  open sessions to reach zero, and the default `QueuePool` holds connections open forever.
- Add connection retry: a paused DB returns **error 40613** on first connect while it
  resumes (~1 min).

⚠️ **Open decision — the LangGraph checkpointer.** Official savers are InMemory,
SQLite, and Postgres only; there is **no official MSSQL checkpointer**. So the copilot's
`copilot.db` can't simply move to Azure SQL. Options: keep SQLite on a mounted Azure
Files share, accept InMemory (conversation history and pending approvals die on
restart), or use a free external Postgres. Decide before deploying the copilot.

Two related items:
- `AsyncSqliteSaver.from_conn_string("copilot.db")` — a **relative** path, so
  conversation history dies on restart and can't be shared across replicas.
- `sql_runtime.py` opens `../backend/dashboard.db` **directly off the filesystem**.
  In separate containers that path doesn't exist. The NL2SQL tool cannot move to the
  MCP server until this is a network DB.

---

## Phase 3 — Make the MCP server remote (the hinge)

### 3.1 The identity refactor — do this FIRST, while still on stdio

```python
TOKEN   = os.getenv("ADJUSTER_TOKEN", "")            # mcp_server.py:9-10
HEADERS = {"Authorization": f"Bearer {TOKEN}"}       # evaluated ONCE at import
```

**One process = one hardcoded user.** Fine for a spawned subprocess; structurally
broken for a shared server. Flip the transport without fixing this and *every*
external client silently acts as whichever user's token was in the environment at
container start. `_search_similar_claims` builds its Azure Search pre-filter from
that identity — so this is a direct cross-adjuster data-exposure path.

Note the naive fix (`global TOKEN` per call) is **worse**: a module global isn't
concurrency-safe, so two in-flight calls race. That turns a guaranteed leak into an
intermittent one, which passes testing. Identity must live in a **contextvar**,
per-request. Do this refactor on stdio, where it cannot leak anything.

### 3.2 Flip the transport

Only two transports exist in the current spec: **stdio** and **Streamable HTTP**.
The old HTTP+SSE two-endpoint shape is deprecated — don't build on it.

```python
mcp = FastMCP("homesite-claims", stateless_http=True, streamable_http_path="/mcp",
              host="0.0.0.0")
mcp.run(transport="streamable-http")
```

- Use **`stateless_http=True`** — a fresh transport per request, no session tracking,
  so you can scale replicas behind plain round-robin with no sticky sessions. It also
  puts you on the right side of the 2026-07-28 spec, which removes protocol-level
  sessions entirely.
- Leave `json_response=False` (turning it on silently swallows progress notifications).
- Alternative: mount `mcp.streamable_http_app()` into FastAPI to share a process —
  but then you **must** wire `mcp.session_manager.run()` into the parent app's
  lifespan, or the first request fails with *"Task group is not initialized"*.

### 3.3 Split the tool surface by trust

Do **not** expose all 9 tools publicly. Suggested split:

- **External, read-only:** `queue_metrics`, `get_my_pending_tasks`, `get_claim_status`,
  `search_policy_docs`.
- **Conditional:** `list_my_claims`, `search_similar_claims` — only behind a redaction
  layer. `claim_summary` returns customer **name, phone, email, VIN, policy number**;
  that PII would cross to a third-party LLM provider.
- **Internal only:** the three write tools. The HITL approval gate lives in the
  *copilot's* LangGraph — Claude and ChatGPT would bypass it completely.

### 3.4 Point the copilot at the remote server

Replace `StdioServerParameters`/`stdio_client` with `streamablehttp_client`. This also
fixes a real bug: `build_graph()` currently spawns a **new MCP subprocess and a new
SQLite saver on every `/chat`, `/approve`, and `/reject` call** — harmless locally, a
process explosion in a container.

---

## Phase 4 — Authentication

MCP authorization is OAuth 2.1-based and applies **only** to HTTP transports (stdio
servers are explicitly told to use environment credentials instead). The server is a
pure **OAuth resource server**; RFC 9728 Protected Resource Metadata is mandatory.

Two deliverables — don't conflate them:

**4a. Key-based (ship this).** A bearer/API-key header verified by a `TokenVerifier`.
Enough for internal service-to-service use and for proving the remote server works.

**4b. OAuth 2.1 via Entra ID (the real learning project).** App registration →
Application ID URI → exposed scopes → auth-code + PKCE for users, client credentials
for services. Redirect URI for Claude is `https://claude.ai/api/mcp/auth_callback`.

⚠️ Check first: **many tenants disable user-initiated app registration.** Verify you
can create one before planning around it.

**The hard part** (easy to underestimate): token audience. The backend only accepts
its own HS256 JWT, but external clients present an **Entra** token. The MCP server can
verify Entra tokens, but must then present something the backend accepts. Decide
explicitly: teach the backend to validate Entra tokens, or exchange tokens at the MCP
layer. Also read the spec's **"no token passthrough"** rule — passing a client's token
straight through to a downstream API is a named anti-pattern.

---

## Phase 5 — Connect Claude and ChatGPT

Both require a **publicly reachable HTTPS endpoint speaking Streamable HTTP** —
which ACA external ingress gives you free with a managed certificate.

- **Claude:** custom connectors are GA on all plans (Free is capped at 1), across
  web/Desktop/mobile/Claude Code. **No Anthropic review needed** for private use —
  verification and the directory only affect discoverability. Note Claude connects
  from Anthropic's **cloud egress range**, not your machine → localhost and private
  VNets won't work; a dev tunnel is needed for local testing.
- **ChatGPT:** connectors / developer mode / Apps SDK. Deep research imposes extra
  requirements (notably `search` and `fetch` tools).

**OpenAPI:** FastAPI *already publishes* `/openapi.json` — that's a free win. But
routes lack `operation_id`/`response_model`, so the generated spec has ugly operation
IDs, which matters if you import to API Management or build a Copilot Studio /
Power Platform connector (those consume **Swagger 2.0**, not OpenAPI 3.1 — a real
conversion gotcha).

---

## Phase 6 — One-click deploy (IaC)

**azd + Bicep.** `azure.yaml` maps source folders → Azure services; `infra/*.bicep`
defines the resources; `azd up` provisions + builds + deploys everything in one shot.
Bicep over ARM (same engine, far less verbose) and over Terraform (no state file,
Azure-only is fine here, and azd defaults to Bicep).

```
azure.yaml              # 4 services
infra/
  main.bicep
  core/                 # acr, aca-env, storage, keyvault
  apps/                 # backend, copilot, mcp, web
```

CI/CD: GitHub Actions with **OIDC federated credentials** (no long-lived secrets).
A "Deploy to Azure" button is a README nicety on top of the same Bicep.

---

## Phase 7 — Kill the API keys (managed identity)

Highest value-to-effort ratio in this whole document (~1–2 hours).

One **user-assigned managed identity** attached to every container app, plus RBAC:
- `Cognitive Services OpenAI User` — chat + embeddings
- `Search Index Data Reader` — the copilot's vector queries
- `Search Service Contributor` — **only** for the seeding scripts, never the runtime app

Then replace `api_key=`/`AzureKeyCredential` with `DefaultAzureCredential`. This
removes `AZURE_OPENAI_KEY` and `AZURE_SEARCH_KEY` from the environment entirely —
which matters more once the MCP server is public, because a compromised server
currently yields an Azure Search **admin** key (index-wide write).

---

## Phase 8 — Guardrails

You already have two genuinely good ones: the **three-layer SQL guardrail** in
`sql_runtime.py` and **HITL approval**. What's missing, in priority order:

1. Fix the three P0 bugs above (they *are* your guardrails, currently broken).
2. Move authority enforcement **server-side** — the backend should reject status
   transitions exceeding the caller's role/amount authority, not rely on the agent.
3. **Indirect prompt injection** — retrieved policy docs and DB rows are untrusted
   content that reaches the model. Azure AI Content Safety **Prompt Shields**
   (document attacks) is the Azure-native mitigation.
4. **Rate limiting** on the embedding-backed tools once public.
5. **Audit trail** — `ClaimEvent.actor` records only `user.name`. Once dashboard,
   Claude, ChatGPT, and internal services can all act as the same human, you can't
   answer "which client made this change". Add the calling `client_id`.

Note: Foundry's tool-call/tool-response guardrail interception only works for agents
built in **Foundry Agent Service** — a LangGraph agent can't use those hooks, so
these have to be implemented in your own code.

---

## Honest "skip in this pilot" list

Resume-driven development is a real failure mode. These are worth *understanding*,
not necessarily *building* here:

| Topic | Verdict |
|---|---|
| **Microsoft Graph API** | **Skip.** Genuinely irrelevant to a claims system — it's for users/groups/mail/Teams. Worth 10 minutes of reading so you can say what it is. Also note "Graph API" is ambiguous: **Microsoft Graph** ≠ **Azure Resource Graph** (resource inventory via KQL). Knowing the difference is itself a good interview answer. |
| **Bot connectors** | **Skip / clarify.** This most likely means **Azure Bot Service** channel connectors (Teams, Slack, Web Chat) — a completely different stack from Claude/ChatGPT "custom connectors". Only worth it if you actually want a Teams-facing bot. |
| **Azure Functions** | **Skip as a host.** Evaluated and rejected above (230s timeout, preview status, tool rewrite). Worth knowing *why* you rejected it — that's the better interview answer anyway. |
| **API Management** | **Skip on cost.** ~$50–210/mo floor. Revisit only if you need a real API gateway. |
| **VNet / private endpoints** | **Actively counterproductive** — Claude connectors need public reachability. Don't lock down what must stay reachable. |

---

## Suggested order of attack

```
0. Pin mcp<2  ·  fix the 3 security bugs                    ← today
1. Dockerfile + deploy BACKEND to Container Apps            ← start here
2. Postgres (unblocks everything multi-container)
3. Deploy frontend, then copilot
4. MCP identity refactor (contextvar) — still on stdio
5. Flip MCP to Streamable HTTP + deploy as its own app
6. Key-based auth  →  connect Claude  →  connect ChatGPT
7. Entra OAuth 2.1 (the real learning project)
8. azd + Bicep one-click  ·  managed identity  ·  guardrails
```
