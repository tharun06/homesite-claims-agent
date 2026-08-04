# Securing the remote MCP server (OAuth 2.1 + Entra ID)

The MCP server is public — anyone on the internet can reach
`https://homesite-mcp.<env>.westus2.azurecontainerapps.io/mcp`. It exposes nine
tools, three of which write (`update_claim_status`, `add_note_to_claim`,
`reassign_claim`). So it is an OAuth 2.1 **resource server**: it never logs
anyone in and never mints a token, it only verifies tokens Entra issued.

That split is the point. Swapping identity providers means changing
`mcp_auth.py` and nothing else — no tool code knows what a token is.

## The handshake

```
1. client POSTs /mcp with no token
2. server  401 + WWW-Authenticate: Bearer resource_metadata="<url>"
3. client  GET /.well-known/oauth-protected-resource        (RFC 9728)
4. that doc names Entra as the authorization server
5. client  auth-code + PKCE against Entra, gets a token
6. client  retries with Authorization: Bearer <token>
7. EntraTokenVerifier.verify_token() checks it
```

Step 7 is strict about the **audience**. A token minted for another API in the
same tenant is a perfectly valid Entra token; accepting it would let any app in
the directory drive these tools. The audience check is what makes a token
specific to *this* server.

## Five things that are not obvious

Each was found by pointing a real client at a standalone hello-world server and
watching it fail. All five now live in `mcp_auth.py` / `mcp_server.py`.

1. **`required_scopes` must be the resource-qualified `api://<app id>/claims.access`.**
   Give Entra the bare name and it cannot tell which API is meant, so it
   defaults the resource to Microsoft Graph — which has no such scope — and
   sign-in dies with `AADSTS650053` before this server is contacted at all.

2. **The token comes back with the *other* spelling.** Entra puts the bare
   `claims.access` in `scp`, but the SDK is checking for the `api://…` form.
   `verify_token` reports both, so the check matches either way. Confirmed
   live: `aud=api://04d3…`, `scp='claims.access'` — mismatched spellings on
   the same request, both handled.

3. **The SDK's protected-resource document cannot work with Entra.** It builds
   the doc from `resource_server_url`, which pydantic pins to `AnyHttpUrl`, so
   `api://…` cannot be expressed. Entra rejects any RFC 8707 `resource` that is
   not an App ID URI with `AADSTS9010010` — at the *authorization* endpoint,
   before the user signs in, so nothing reaches us to debug. Hence
   `EntraMetadata`, which serves a hand-built document.

4. **Clients probe several well-known paths and a 404 on the first one ends the
   handshake.** Observed: `…/oauth-protected-resource` 200,
   `…/oauth-protected-resource/mcp` 404, `…/oauth-authorization-server` 404 →
   client gave up. We serve all five.

5. **The authorization-server document must advertise `S256`.** Entra supports
   PKCE but omits `code_challenge_methods_supported` from its own metadata, so
   a strict client refuses to start.

Debugging any of this needs `RequestLog` (`MCP_DEBUG_LOG=1`) — raw ASGI, *not*
Starlette's `BaseHTTPMiddleware`, which buffers the response and would break the
streaming transport. It answers the only question that matters when a client
"just fails": did it reach us, and what was in the token?

## The wall: identifier URIs need a verified domain

An MCP client derives the RFC 8707 `resource` from **the URL the user typed**,
not from what we advertise. Entra accepts that `resource` only if the exact
string is registered as an Application ID URI:

```bash
az ad app update --id <API_APP_ID> --identifier-uris "api://<API_APP_ID>" "https://<host>/mcp"
```

**That registration now fails for any Azure-provided hostname.**

```
HostNameNotOnVerifiedDomain: Values of identifierUris property must use a
verified domain of the organization or its subdomain
```

Verified against `azurecontainerapps.io`, `azurewebsites.net`, and a third-party
domain — all rejected, on both `az ad app update` and a direct Microsoft Graph
`PATCH`, and on **create** as well as update. Entra tightened this rule: URIs
registered before the change still work (the hello-world server's
`*.use.devtunnels.ms` URI is grandfathered and still completes a full browser
sign-in), but the same string can no longer be added today.

So the browser click-through flow from a Claude/ChatGPT connector needs a domain
you actually own — map it to the Container App, verify it in Entra, register
`https://claims.yourdomain.com/mcp`. That is what production would do anyway;
you would not ship a customer-facing endpoint on a generated Azure hostname.

Everything *except* that last hop is deployed and verified.

## Verifying it (no browser needed)

The Azure CLI is pre-authorized for the scope, so it can mint a real token:

```bash
TOK=$(az account get-access-token --resource "api://$ENTRA_API_APP_ID" --query accessToken -o tsv)
curl -s -X POST "https://$FQDN/mcp" -H "Authorization: Bearer $TOK" \
  -H 'Content-Type: application/json' -H 'Accept: application/json, text/event-stream' \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"curl","version":"1"}}}'
```

Current results against the deployed server:

| request | result |
| --- | --- |
| no token | 401 + `WWW-Authenticate` pointing at the metadata URL |
| garbage string | 401 |
| real Entra token, audience = Microsoft Graph | 401 — correct signature, wrong audience |
| real Entra token, audience = our API | 200, 9 tools, live Postgres data |

The third row is the one worth keeping. It is a genuine, correctly-signed,
unexpired token from the same tenant, and it is refused — that is the audience
check earning its place, and it is the difference between "we check the
signature" and "we check the token was meant for us."
