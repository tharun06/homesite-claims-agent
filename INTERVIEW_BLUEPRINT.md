# Adjuster Copilot — end-to-end blueprint

Two deliverables sit on one codebase:

1. **The chatbot** — an in-dashboard copilot an adjuster talks to. LangGraph
   orchestrator, RAG, an NL2SQL subgraph, durable conversation state, and a
   human approval gate on writes.
2. **The plugin surface** — the same nine tools exposed over MCP so Claude and
   ChatGPT can drive them, protected by OAuth 2.1 against Entra ID.

The interesting engineering is in the seams: identity crossing three hops,
which tools are safe to expose to a third-party LLM, and what "read-only" has to
mean when a model is writing the SQL.

---

## Saying it out loud

### "Tell me about your recent project" — ~75 seconds

Lead with the problem. Plant hooks. Stop early and let them dig.

> So it's an AI copilot for insurance claims adjusters.
>
> The problem is pretty simple. An adjuster has maybe forty open claims, and
> answering something like *"which of mine are about to breach SLA"* means
> clicking through a dashboard for ten minutes. So we put a chat assistant
> inside the dashboard that just answers it.
>
> Under the hood it's a LangGraph agent with three ways of finding things. Two
> are vector search — one over past claims, one over policy documents. The
> third is natural-language-to-SQL, because a question like *"how many
> fraud-flagged claims are open in the Southeast"* isn't a similarity problem,
> it's a counting problem. Embeddings can't count. That was probably the most
> interesting design call.
>
> The part I'd want to talk about is safety. The agent writes SQL, so I assumed
> that eventually it would write something it shouldn't. The database connection
> is a read-only role — so even if everything above it fails, Postgres just
> refuses. And anything that modifies a claim stops and waits for human approval
> before it runs.
>
> Then the second half. We published the same tools as an MCP server, so Claude
> and ChatGPT can use them too. That one's public on the internet, so it sits
> behind OAuth 2.1 with Entra ID.
>
> That's the shape of it — happy to go deeper anywhere.

### The 20-second version

For a screen, a recruiter, or when they've already heard a lot.

> It's an AI copilot for insurance claims adjusters — a chat assistant inside
> their dashboard that answers questions about their caseload. It's a LangGraph
> agent with vector search and a natural-language-to-SQL path for aggregate
> questions. We also published the same tools as an MCP server behind OAuth, so
> Claude and ChatGPT can use them.

### Delivery notes

- **Lead with the adjuster, not the stack.** Forty claims, ten minutes of
  clicking. Anyone can picture that. Nobody pictures "LangGraph StateGraph."
- **Say "adjuster", "SLA", "peril", "SIU".** Domain vocabulary signals you
  understood the business, not just the ticket.
- **Pause after "embeddings can't count."** It's the best hook you have. Let it
  land.
- **Name-drop at most four technologies.** Listing fifteen is the POC smell —
  it reads as "I followed a tutorial." Four sounds like you chose them.
- **Stop before they stop you.** The goal of an opener is a follow-up question,
  not completeness. Everything else in this document is the follow-up.
- **End with an offer, not a trail-off.** "Happy to go deeper anywhere" beats
  "…yeah, so, that's basically it."

**Hooks you're deliberately planting**, each of which you can answer well:
LangGraph · NL2SQL · embeddings can't count · read-only role · human approval ·
MCP · OAuth 2.1.

### The written version (for a CV or a follow-up email)

> A claims dashboard with an embedded copilot. The copilot is a LangGraph agent
> with three retrieval paths — vector search over past claims, vector search
> over policy documents, and a natural-language-to-SQL subgraph for aggregate
> questions. Conversation state is checkpointed to Postgres so a session
> survives a container restart, and any write pauses for human approval before
> it executes. The same tools are published as an MCP server so Claude and
> ChatGPT can use them, secured as an OAuth 2.1 resource server against Entra
> ID with per-request JWT validation. Deployed to Azure Container Apps for
> about $0/month.

---

## System map

```
  Browser
     │
     ▼
  React SPA  ──────────────►  FastAPI backend  ──────►  Postgres
  (Static Web Apps)          (Container Apps)          (Flexible Server)
     │                             ▲   ▲
     │ /chat /approve /reject      │   │
     ▼                             │   │
  Copilot service ─────────────────┘   │
  (Container Apps)                     │
     │  LangGraph                      │
     │   ├── agent node (Azure OpenAI) │
     │   ├── action node (tools)       │
     │   └── checkpointer ─────────────┼──► Postgres (state)
     │                                 │
     └── MCP client (stdio) ───────────┘

  Claude / ChatGPT ──── OAuth 2.1 ───►  MCP server  ──► FastAPI backend
                        (Entra ID)     (Container Apps)
```

Note the shape: **the MCP server is a client of the backend**, not a second door
into the database. One authorization implementation, not two.

---

# Part A — The chatbot

## A1. The orchestrator

A LangGraph `StateGraph` with two nodes:

- **`agent`** — calls Azure OpenAI with the tool schemas bound. Either answers
  or emits `tool_calls`.
- **`action`** — executes the requested tools, appends results, loops back.

A conditional edge routes `agent → action` when tool calls are present, and
`agent → END` otherwise. That loop is the whole agent; everything else is tools
and plumbing.

**Why LangGraph over a plain while-loop:** the checkpointer and the interrupt.
Those two features are the entire reason for the dependency, and both are load-
bearing here. Say that — it shows you chose the framework rather than defaulted
to it.

## A2. The three retrieval paths

Different question shapes need different machinery. Picking the wrong one is
the most common RAG mistake.

| question | path | why |
| --- | --- | --- |
| "any claims like this one?" | vector search over claims | fuzzy similarity |
| "what does the policy say about water damage?" | vector search over policy docs | fuzzy similarity |
| "how many fraud-flagged claims are open in the Southeast?" | **NL2SQL** | counting, grouping, filtering |

**Embeddings cannot count.** A vector index returns the *k* nearest neighbours;
it has no notion of "all rows matching X". Aggregate questions are a SQL
problem wearing a natural-language costume. Recognising that split is the point.

### The `description` problem

The original plan was to embed the claim `description` field. It turned out to
be unusable — short, templated, near-duplicate across claims, so every
embedding landed in the same neighbourhood and similarity was noise. Fixing it
meant constructing a richer text representation to embed rather than trusting
the raw column.

**Lesson to state:** the retrieval quality ceiling is set by what you embed, not
by which vector store you pick.

### The top-k prefilter

`search_similar_claims` originally took the top *k* by vector distance alone.
With `k=3` you get the three nearest claims globally — which may all be outside
the adjuster's region, or closed, or irrelevant.

The fix is **filter first, then rank**: narrow by structured predicates
(region, status, peril, date window) and run the vector search over that
candidate set. This is standard practice — the vector index is for ranking
within a relevant set, not for finding the relevant set.

## A3. NL2SQL as a subgraph-as-a-tool

The piece worth talking about most, because it's where "let an LLM write SQL"
meets "don't let an LLM touch the database".

**Structure:** a second, self-contained `StateGraph`, compiled, then wrapped in
an `@tool` function. The orchestrator sees one tool called
`query_claims_data`. It has no idea there's a five-node graph behind it.

```
ground_values → select_tables → generate_sql → execute_sql → ┬─ retry ──┐
                                                             └─ format_answer
                                                                   ▲     │
                                                                   └─────┘
```

- **`ground_values`** — map phrases in the question to real column values
  ("Southeast" → the actual region code). Prevents SQL that is syntactically
  perfect and semantically empty.
- **`select_tables`** — schema linking. Pick only the relevant tables so the
  generation prompt stays small and focused.
- **`generate_sql`** — the LLM call.
- **`execute_sql`** — runs it behind the guardrail (below).
- **retry edge** — on a database error, feed the error text back and regenerate.
  Bounded retries, then give up honestly.
- **`format_answer`** — turn rows into prose.

**Why a subgraph and not just a function:** it gets its own state, its own retry
loop, and its own node-level observability, without any of that leaking into the
orchestrator's state. Composition, not nesting.

### Schema profiles — the part teams get wrong

`schema_profiles.py` holds a semantic description of each table and column. The
industry pattern is a **split**:

- **Mechanical half, generated:** column names, types, nullability, cardinality,
  sample values — reflected from the database and data-profiled.
- **Semantic half, hand-written:** what the column *means*, which values are
  legal, what the gotchas are.

Nobody hand-writes 200 column types, and no generator knows that
`SIU Flagged` means Special Investigations Unit.

### The three-layer SQL guardrail (`sql_runtime.py`)

Assume the model will eventually emit something hostile — through a bug, a
jailbreak, or injected content in a retrieved document.

1. **Statement inspection** — reject anything that isn't a single `SELECT`.
   Blocks multi-statement (`; DROP …`), DDL, DML.
2. **Database-enforced read-only** — a Postgres role granted only `SELECT` and
   `TEMPORARY`. If layer 1 is bypassed, the *database* refuses. This is the one
   that matters: it doesn't depend on your parser being correct.
3. **Row scoping** — the query runs against TEMP VIEWs, not base tables, and
   `_scope_predicate()` bakes the caller's authorization into those views. An
   adjuster cannot read another region's claims *even with a correct query*.

Layer 3 deliberately mirrors the backend's `scope_claims()` — same rule, so a
question answered via SQL and the same question answered via the REST API return
the same rows.

**The line to use:** "I didn't try to make the LLM safe. I made the blast radius
small enough that it doesn't need to be."

## A4. The checkpointer — why the bot remembers

`StateGraph.compile(checkpointer=saver)`.

After **every node transition**, LangGraph serialises the graph state and writes
it under a `thread_id`. On the next message with the same `thread_id`, it loads
that state and continues.

- **What's stored:** the whole state object — the message list, and any other
  channels. Not "what the LLM said" — the full state at each step.
- **Why per-node and not per-turn:** that's what makes resume-from-interrupt
  possible. You can stop between the agent node and the action node and pick up
  exactly there.
- **`thread_id`** is the conversation key and the isolation boundary.

**Progression, and why:** `AsyncSqliteSaver` locally → `AsyncPostgresSaver` once
deployed. SQLite died on a shared Azure Files volume (see war stories), and a
scale-to-zero container loses in-process state on every idle timeout. Durable
state has to live outside the container.

## A5. Human-in-the-loop on writes

Three tools mutate: `update_claim_status`, `add_note_to_claim`, `reassign_claim`.

`interrupt_before=["action"]` stops the graph before the tool node runs. The
pending call is surfaced to the adjuster. On approve, `ainvoke(None, config)`
resumes from the checkpoint — the `None` means "no new input, continue from
saved state." Reject discards.

This is exactly why the checkpointer is not optional: the interrupt is only
useful if the paused state survives long enough for a human to look at it.

### The three bugs found here — good material

Found by reading the code rather than by a failure:

1. **`/approve` trusted a client-supplied `thread_id` with no ownership check.**
   Anyone could approve anyone's pending write. A complete bypass of the gate.
2. **Only one of three write tools was in `WRITE_TOOLS`.** Two mutations sailed
   past the interrupt.
3. **The router only inspected `tool_calls[-1]`.** A batch with a write anywhere
   but last went unchecked.

**The lesson:** a security control that is *present* is not a security control
that *works*. All three would have passed a demo.

## A6. Streaming

Token streaming via `astream_events`, filtered on
`event["metadata"]["langgraph_node"] != "agent"` so intermediate node chatter
doesn't reach the user — only the final answer streams.

## A7. Identity and authorization

`_resolve_caller()` establishes who's asking; `scope_claims()` filters rows by
role and region. Enforcement is **server-side in the backend**, not in the agent
prompt. An LLM instruction is not an access control.

---

# Part B — The plugin surface (MCP + OAuth 2.1)

## B1. What MCP is, in one line

A standard protocol for exposing tools to an LLM client, so you write the tool
once instead of once per vendor. The same nine tools serve the in-house copilot,
Claude, and ChatGPT.

## B2. Transports

- **stdio** — the client spawns your server as a subprocess and talks over
  stdin/stdout. Local only. This is Claude Desktop's config file path.
- **Streamable HTTP** — a real HTTP endpoint at `/mcp`. Required for anything
  remote.

`stateless_http=True` because Container Apps can scale to multiple replicas and
there's no sticky routing — any replica must be able to serve any request.

## B3. Identity across the hop

The MCP server calls the backend, so it needs a credential:

- **stdio:** `ADJUSTER_TOKEN` from the environment — a specific adjuster.
- **remote:** `SERVICE_EMAIL` self-login, with a **401-triggered re-login and
  retry**. Tokens expire; a long-lived server that doesn't handle that dies
  quietly after an hour.

## B4. OAuth 2.1 — the three players

| role | who | job |
| --- | --- | --- |
| Resource server | the MCP server | owns the data, validates tokens, **never logs anyone in** |
| Authorization server | Entra ID | knows the users, issues tokens |
| Client | Claude / ChatGPT | wants to call tools on a user's behalf |

The server **never sees a password** and **never phones Entra to validate**. It
verifies the signature offline against Entra's published JWKS. A token is a
signed statement: *"Entra says user U granted app C the scope `claims.access`
on API A, until time T."*

**Why this split matters:** changing identity provider means changing one file.
No tool code knows what a token is.

## B5. The handshake

```
 1. client → POST /mcp                          (no token)
 2. server → 401 + WWW-Authenticate: resource_metadata="…"
 3. client → GET /.well-known/oauth-protected-resource     (RFC 9728)
 4. server → { resource: <this server>, authorization_servers: [Entra] }
 5. client → Entra /authorize: client id, scope, resource, PKCE challenge
 6. Entra  → is that resource a registered App ID URI?
 7. Entra  → sign-in + consent screen
 8. user   → approves
 9. Entra  → redirect with one-time code
10. client → trades code + PKCE verifier for a JWT
11. client → POST /mcp with Authorization: Bearer <jwt>
12. server → verify signature / audience / issuer / expiry
13. server → 200, runs the tool
```

## B6. Validation, and why the audience check is the load-bearing one

Checked per request: **signature** (JWKS, cached with TTL, refetch on unknown
`kid` so key rotation self-heals), **audience**, **issuer**, **expiry**.

A token minted for a different API in the same tenant is a *perfectly valid
Entra token*. Accepting it would let any app in the directory drive these tools.
The audience check is what makes a token specific to this server.

**Proven, not asserted:**

| request | result |
| --- | --- |
| no token | 401 + `WWW-Authenticate` |
| garbage string | 401 |
| real Entra token, audience = Microsoft Graph | **401** |
| real Entra token, audience = our API | 200, 9 tools, live data |

Row 3 is the answer to "how do you know it's actually secure" — a genuine,
correctly-signed, unexpired token from the right tenant, refused.

## B7. Five non-obvious failures

Each found by pointing a real client at a hello-world server and watching it
fail. Worth knowing because they're the difference between reading the spec and
having shipped it.

1. **`required_scopes` must be the resource-qualified `api://<app id>/claims.access`.**
   The bare name leaves Entra unable to tell which API is meant, so it defaults
   the resource to Microsoft Graph and sign-in dies with `AADSTS650053` before
   your server is contacted at all.
2. **The token comes back with the *other* spelling** — `scp` holds the bare
   name while the SDK checks for the URI form. Report both.
3. **The SDK's protected-resource document cannot work with Entra.** It's built
   from a field pydantic pins to `AnyHttpUrl`, so `api://…` can't be expressed —
   and Entra rejects any RFC 8707 `resource` that isn't an App ID URI
   (`AADSTS9010010`), at the *authorization* endpoint, before sign-in, so
   nothing reaches you to debug.
4. **Clients probe several well-known paths; a 404 on the first one ends the
   handshake.** Serve all five.
5. **The metadata must advertise `S256`.** Entra supports PKCE but omits it from
   its own metadata, so a strict client refuses to start.

Debugging any of this needed a raw-ASGI request logger — deliberately *not*
Starlette's `BaseHTTPMiddleware`, which buffers responses and would break the
streaming transport.

## B8. The wall: identifier URIs need a verified domain

A client derives the RFC 8707 `resource` from **the URL the user typed**, not
from what you advertise. Entra accepts it only if that exact string is a
registered Application ID URI — and it now refuses any unverified domain:

```
HostNameNotOnVerifiedDomain
```

Tested against `azurecontainerapps.io`, `azurewebsites.net`, and a third-party
domain; on `az ad app update`, on **create**, and via a direct Microsoft Graph
PATCH. All rejected. Pre-existing registrations are grandfathered — which is why
a devtunnel URI registered before the change still completes a full sign-in.

**The fix is a domain you own**, mapped to the app and verified in Entra. That's
what production does anyway — you wouldn't ship a customer endpoint on a
generated cloud hostname. Everything short of that hop is deployed and verified.

**Two adjacent facts worth knowing:**

- **`requestedAccessTokenVersion`.** On v1 (`null`), the token's `aud` is
  *whatever URI the client requested* — so with a custom domain it'd be the
  https URL, and a verifier expecting `api://…` would 401 everything with
  nothing in the logs. Setting it to `2` makes `aud` always the app-id GUID.
- **Entra does not support Dynamic Client Registration.** Most MCP clients
  expect DCR; with Entra you paste a pre-registered client id and secret into
  the connector dialog by hand.

## B9. What should not be exposed — and why

Currently all nine tools are public behind OAuth. Two real problems:

- **The write tools bypass the HITL gate.** The approval interrupt lives in the
  *copilot's* LangGraph. Claude calls the MCP server directly, so a write from
  Claude skips human approval entirely. Authentication answers *who*; it does
  not answer *what they may do*.
- **PII crosses a tenant boundary.** `claim_summary` returns customer name,
  phone, email, VIN and policy number — which would land on a third-party LLM
  provider's servers on every call.

The intended split: read-only tools external, PII-bearing tools only behind
redaction, write tools internal-only.

Also outstanding: **tool annotations** (`readOnlyHint` / `destructiveHint`) are
missing, which is why ChatGPT labels all nine tools DESTRUCTIVE.

**Say this unprompted.** Knowing the remaining hole is stronger than claiming
there isn't one.

---

# Part C — Infrastructure

## C1. Hosting

- **Frontend:** Azure Static Web Apps. Chosen over a container running nginx —
  no cold start, a separate free allowance, and no image to rebuild for a CSS
  change.
- **Backend, copilot, MCP server:** Azure Container Apps, Consumption plan.
  Scale-to-zero, and the monthly free grant (vCPU-seconds + requests) is
  always-free, not trial-only. `max-replicas` capped as a blast-radius limit —
  a runaway loop can't scale into a bill.
- **Database:** PostgreSQL Flexible Server.

## C2. Registry

**ghcr.io, not Azure Container Registry.** ACR Basic is ~$5.07/month *standing* —
charged whether or not you push. For public images ghcr.io is free. On a
$5–10 total budget that single choice is most of the budget.

## C3. CI/CD, honestly

GitHub Actions builds on push and pushes SHA-tagged images to ghcr.io.
Deployment is a manual `az containerapp update` to the new tag.

**Known gap:** there's no trigger that rolls a new image out automatically.
Fixing it properly means SHA-pinned tags plus OIDC federation from Actions to
Azure instead of a stored credential. Deliberately deferred — say so; "we tag by
SHA and deploy explicitly" is a defensible position, and `:latest` with no
rollout trigger is a real gap worth naming.

## C4. Cost control

Current spend: **$0.00**. The rules: no standing-charge resources, scale to
zero, capped replicas, free-tier registry, and checking the portal rather than
trusting a script's exit code — which is how a second, billable Postgres server
got caught mid-provision and deleted.

---

# Part D — War stories

The most useful interview material in the whole project. Each one is a real
failure with a transferable lesson.

**SQLite on an Azure Files share → `database is locked`.**
SMB doesn't provide POSIX advisory locks, so SQLite's locking silently doesn't
work. Killed the shared-volume plan and forced the move to Postgres.
*Lesson: "it's a filesystem" is not the same as "it's a filesystem with the
semantics your library assumes."*

**Postgres reserved word `user`.**
`SELECT … FROM user` returned **1 row** — unquoted `user` resolves to the
`CURRENT_USER` function. `FROM "user"` returned **25**. No error, just wrong
data. Fixed with an `app_users` view.
*Lesson: the dangerous bugs don't raise.*

**`default_transaction_read_only` blocked `CREATE TEMP VIEW`.**
The read-only setting fought the row-scoping mechanism. Resolved by relying on
role grants and setting the session read-only *after* view creation.
*Lesson: two correct-looking safety layers can conflict.*

**`mcp.settings.token_verifier` silently does nothing.**
FastMCP reads `mcp._token_verifier`. Assigning to `settings` leaves the server
**wide open while looking configured**. Caught by testing an unauthenticated
call rather than reading the config.
*Lesson: verify security controls from the outside.*

**`jose` audience check with a list.**
Its internal check is `audience not in audience_claims`, which only works for a
single string — passing a list would have rejected every token. Now the audience
is checked by hand.

**Version skew in the langgraph family.** Container died at import on a
`langgraph.runtime` import. Fixed by pinning the whole family together, not one
package.

**App Service quota of 0 vCPU** on the subscription → pivoted to Container Apps.
**`ManagedClusterSuspended`** on an 11-day-idle environment → rebuilt fresh.

**httpx's 5-second default timeout** was killing calls to an endpoint that takes
~7.5s. Raised to 60s.

**ChatGPT `ClientDisconnect`** turned out to be a scale-to-zero cold start, not
an auth failure.

**The realtime simulator was corrupting the data** — draining statuses to
APPROVED and randomly reassigning claims, flattening every distribution. Gated
behind an env var, default off.
*Lesson: demo scaffolding that writes to the database is a data-integrity risk.*

**Known and deliberately unfixed:** an N+1 in `claim_summary` — 174 queries,
~7.5s. An identity-map preload was attempted, didn't work, and was reverted
rather than left half-done.
*Lesson: knowing the cost and choosing not to pay it is engineering; not knowing
is not.*

---

# Part E — Questions you should expect

**On the agent**
- Why LangGraph and not a plain loop, or LangChain agents? → checkpointer +
  interrupt; both load-bearing.
- What's actually stored in the checkpointer, and when? → full graph state,
  after every node transition, keyed by `thread_id`.
- How do you resume? → `ainvoke(None, config)`; `None` means continue from saved
  state.
- What happens on a container restart mid-conversation? → state is in Postgres,
  next message resumes.

**On RAG**
- Why not just embed everything? → aggregates aren't a similarity problem;
  embeddings can't count.
- How do you stop top-k returning irrelevant results? → filter to a candidate
  set first, rank within it.
- How do you evaluate retrieval quality? → *honest answer: no formal eval
  harness. Name it as the gap and say what you'd build.*

**On NL2SQL**
- What if the model writes `DROP TABLE`? → three layers, and layer 2 is the
  database itself.
- What if it writes valid SQL for the wrong rows? → row scoping via TEMP VIEWs,
  mirroring the API's rule.
- What about SQL that's syntactically valid but semantically wrong? → value
  grounding, plus the retry loop on execution error. Not fully solved — an eval
  set is the real answer.

**On MCP and OAuth**
- You exposed tools publicly — how did you secure them? → resource server,
  OAuth 2.1, per-request JWT validation, **audience-scoped**; then immediately
  raise the tool-surface split, because auth alone isn't the answer.
- Why validate offline instead of introspecting? → no round trip per request;
  JWKS cached with rotation handling.
- What stops a token for another API in the tenant? → the audience check —
  and here's the test that proves it.
- Would you give an external LLM write access? → no. The approval gate lives in
  the copilot's graph and an external client bypasses it. Writes stay internal.
- What about PII? → currently a real gap; redaction layer before exposure.

**On infrastructure**
- Why Container Apps over App Service / Functions / AKS? → quota, scale-to-zero
  economics, and Functions' 230s cap versus a streaming transport.
- How does a code change reach production? → Actions builds SHA-tagged images to
  ghcr.io, deploy is explicit. Name the automation gap.
- How do you control cost? → no standing charges, scale to zero, capped
  replicas, free registry; $0 to date.

**The ones that separate candidates**
- What's still broken? → tool-surface split, PII redaction, no retrieval eval,
  no auto-rollout, the N+1, the identifier-URI wall.
- What would you do differently? → IaC from day one; the manual Azure setup is
  not reproducible.
- What did you skip on purpose? → Graph API, Bot Service, API Management
  (~$50–210/mo floor), VNet/private endpoints — actively wrong here, since
  connectors require public reachability.

---

# Part F — Three things to lead with

1. **"The database enforces read-only, not the prompt."** Shows you assume the
   model is compromised.
2. **"A valid Entra token from my own tenant gets a 401 — here's the test."**
   Shows the difference between configuring auth and verifying it.
3. **"Authentication answers who, not what. The write tools shouldn't be on the
   public surface at all, because the approval gate can't reach there."** Shows
   you can find the hole in your own design.
