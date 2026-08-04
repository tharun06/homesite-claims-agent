# Plan: finish the MCP OAuth flow using `homes-site.com`

Goal: make `https://claims.homes-site.com/mcp` a URL that Claude and ChatGPT can
add as a connector, sign into with Entra, and call the nine claims tools.

Everything except the browser sign-in already works. The one broken step is
Entra refusing to register the server's URL as an Application ID URI, because
`azurecontainerapps.io` is not a domain you own. `homes-site.com` is.

Target hostname: **`claims.homes-site.com`** — a subdomain, not the apex. Entra
accepts "a verified domain **or its subdomain**", so verifying `homes-site.com`
once covers this and anything else you add later.

---

## Step 0 — check the domain before doing anything else

The domain cost $0.00, and free registrars sometimes hand out a name without
full DNS control. Everything below depends on being able to create records.

- [ ] Log into the registrar's DNS panel
- [ ] Confirm you can add a **CNAME** on a subdomain
- [ ] Confirm you can add a **TXT** record on the apex (`homes-site.com`)
- [ ] Confirm you can add a **TXT** record on a subdomain (`asuid.claims`)
- [ ] Note the DNS TTL — set it low (300s) if you can, so mistakes are cheap

**If TXT on the apex is not possible, stop.** Entra domain verification cannot
happen and the rest of the plan is dead. Get a different registrar first.

---

## The two ownership proofs

This is the part that confuses people, so it's worth naming up front: you prove
you own the domain **twice**, to two systems that don't talk to each other.

| proof | to whom | so that |
| --- | --- | --- |
| A | Azure Container Apps | it will serve traffic for that hostname and issue a TLS cert |
| B | Entra ID | it will issue tokens naming that hostname |

They're independent. Do them in either order, or in parallel.

---

## Step 1 — proof A: hostname on the Container App

- [ ] Get the app's verification id
      (`az containerapp show … --query properties.customDomainVerificationId`)
- [ ] At the registrar, add:
      - `TXT` on `asuid.claims` → that verification id
      - `CNAME` on `claims` → `homesite-mcp.jollycoast-c20a8d3a.westus2.azurecontainerapps.io`
- [ ] Wait for DNS to propagate — check with `nslookup claims.homes-site.com`
- [ ] `az containerapp hostname add`, then `az containerapp hostname bind`
      with a **managed certificate** (free, auto-renewing)

**Verify:** `curl -I https://claims.homes-site.com/mcp` returns 401 — not a
cert error, not a 404. A 401 means TLS is good and your server is answering.

**Cost:** managed certificates on Container Apps are free. Still $0.

---

## Step 2 — proof B: verify the domain in Entra

- [ ] Add `homes-site.com` as a custom domain on the tenant
- [ ] Entra gives you a TXT value like `MS=ms12345678`
- [ ] Add that TXT at the **apex** `homes-site.com` at the registrar
- [ ] Wait for propagation, then tell Entra to verify

**Verify:** the domain shows as Verified in the tenant's domain list.

**Watch out:** a domain can only be verified in **one** Entra tenant, ever.
If you later want it in a different tenant you must remove it from this one
first.

---

## Step 3 — register the identifier URI

This is the step that has been failing all along. With the domain verified it
should now be accepted.

- [ ] `az ad app update --id 04d34987-… --identifier-uris` with **both**:
      - `api://04d34987-…` (keep it — removing it breaks the working token path)
      - `https://claims.homes-site.com/mcp` (no trailing slash — Entra rejects one)

**Verify:** `az ad app show --id 04d34987-… --query identifierUris` lists both.

**If it still fails** with `HostNameNotOnVerifiedDomain`, Step 2 didn't actually
complete — go back, don't try to work around it.

---

## Step 4 — fix the token audience *before* testing

Do not skip this. It is the single most likely thing to waste an afternoon.

The claims API app has `requestedAccessTokenVersion = null` (v1 tokens), which
means the token's `aud` is **whatever URI the client asked for**. Once Claude
starts asking for `resource=https://claims.homes-site.com/mcp`, the token comes
back with that string as its audience — and `mcp_auth.py` only accepts the two
`api://` spellings. Every request would 401, with nothing in the logs to explain
why.

- [ ] Set `requestedAccessTokenVersion = 2` on the API app.
      Then `aud` is always the plain app-id GUID, whichever URI was requested.
- [ ] Belt and braces: also add the resource URI to `VALID_AUDIENCES` in
      `mcp_auth.py`, so either behaviour is handled.

**Verify:** mint a token with the Azure CLI and decode it — `aud` should be
`04d34987-…` and `ver` should be `2`.

---

## Step 5 — point the server at its new name

- [ ] `MCP_PUBLIC_URL=https://claims.homes-site.com`
      (this is what the RFC 9728 document advertises as `resource`, and it must
      match the identifier URI from Step 3 **exactly**)
- [ ] `MCP_ALLOWED_HOSTS` — add `claims.homes-site.com`, or the MCP SDK's
      DNS-rebinding protection returns **421 Invalid Host header**
- [ ] Restart the revision

**Verify:** `curl https://claims.homes-site.com/.well-known/oauth-protected-resource`
shows `"resource": "https://claims.homes-site.com/mcp"`.

---

## Step 6 — let Claude actually connect

Entra does **not** support Dynamic Client Registration, which is how most MCP
clients would normally get a client id. So you supply one by hand.

- [ ] On the client app (`e2eea669-…`), add Claude's redirect URI.
      Copy it from the hello-world client app — that registration is proven to
      work, so don't guess it.
- [ ] In Claude → Settings → Connectors → Add custom connector:
      - URL: `https://claims.homes-site.com/mcp`
      - Client ID: `e2eea669-…`
      - Client secret: from `.env`
- [ ] Click through the Entra sign-in and consent screen

**Verify:** ask Claude "how many fraud-flagged claims do I have?" and confirm it
returns real numbers. Cross-check against the dashboard.

**Turn on `MCP_DEBUG_LOG=1` before this step.** `RequestLog` prints one line per
request including the decoded token, and it is the only way to tell "the client
never reached us" apart from "the client reached us and we rejected its token."
Those two failures look identical from the Claude side.

---

## Step 7 — repeat for ChatGPT

Same connector setup, different redirect URI. Warm the container first — a cold
start has previously caused ChatGPT to give up with `ClientDisconnect`.

---

## Risks, in rough order of likelihood

1. **The free domain has crippled DNS.** Caught by Step 0. Blocks everything.
2. **Forgetting Step 4.** Everything looks configured and every call 401s.
3. **DNS propagation.** Verification fails, you change something that was
   already correct, and now two things are wrong. Wait and re-check instead.
4. **Trailing slash on the identifier URI.** Entra rejects it outright.
5. **`MCP_ALLOWED_HOSTS`.** Symptom is 421, not 401 — easy to misdiagnose as auth.
6. **Editing container env vars via YAML.** Has previously wiped every variable
   on the app. Use `az containerapp update --set-env-vars`, which merges.

## What does not change

- The tools, the backend, the database — untouched
- `api://04d34987-…` keeps working, so the Azure CLI test path still works
- The existing `azurecontainerapps.io` URL keeps working for token-based calls
- Cost stays $0

## Definition of done

Claude, in a fresh conversation, signs in through Entra and answers a question
using live claims data — and an unauthenticated `curl` to the same URL gets 401.
