"""
OAuth 2.1 protection for the remote MCP server, backed by Microsoft Entra ID.

The MCP server is a RESOURCE SERVER: it never logs anyone in and never issues a
token. It only verifies tokens that Entra issued, which is why swapping identity
providers later means changing this file and nothing else.

The flow (MCP spec 2025-06-18+):

  1. client calls /mcp with no token
  2. server -> 401 + WWW-Authenticate: Bearer resource_metadata="<url>"
  3. client GETs /.well-known/oauth-protected-resource   (RFC 9728, mandatory)
  4. that document names Entra as the authorization server
  5. client does auth-code + PKCE against Entra and gets a token
  6. client retries with Authorization: Bearer <token>
  7. verify_token() below checks it

Validation is deliberately strict about the AUDIENCE. A token minted for some
other API in the same tenant is a perfectly valid Entra token — accepting it
would let any app in the directory call these tools. The audience check is what
makes the token specific to this server.
"""
import os
import time

import httpx
from mcp.server.auth.provider import AccessToken, TokenVerifier

TENANT_ID = os.getenv("ENTRA_TENANT_ID", "")
API_APP_ID = os.getenv("ENTRA_API_APP_ID", "")

# The scope has two spellings and both are needed, for two different jobs.
#
#   SCOPE_NAME  "claims.access"                 — what Entra puts in the token's `scp`
#   SCOPE_URI   "api://<app id>/claims.access"  — what a client must ASK Entra for
#
# Advertise the bare name and Entra cannot tell which API is meant, so it
# defaults the resource to Microsoft Graph, which has no such scope, and sign-in
# dies with AADSTS650053 before this server is ever contacted.
SCOPE_NAME = "claims.access"
SCOPE_URI = f"api://{API_APP_ID}/{SCOPE_NAME}"

# Entra issues v2.0 tokens whose audience is the app id (or api://<app id>).
# Accept both spellings rather than guessing which one the client requested.
VALID_AUDIENCES = {API_APP_ID, f"api://{API_APP_ID}"} if API_APP_ID else set()
ISSUERS = {
    f"https://login.microsoftonline.com/{TENANT_ID}/v2.0",
    f"https://sts.windows.net/{TENANT_ID}/",
}
JWKS_URL = f"https://login.microsoftonline.com/{TENANT_ID}/discovery/v2.0/keys"


def auth_enabled() -> bool:
    """Auth is opt-in: without both ids configured the server stays open, so a
    missing env var can never silently half-protect it.

    (We DO hand-roll the RFC 9728 document — see EntraMetadata for why the SDK's
    generated one cannot work against Entra.)"""
    return bool(TENANT_ID and API_APP_ID)


class _JwksCache:
    """Entra's signing keys, cached. Fetching them per request would add a
    round trip to every call and hammer a shared endpoint; they rotate rarely,
    so an hour is a safe TTL. A cache miss on an unknown kid forces a refetch,
    which is what makes key rotation self-healing."""

    def __init__(self, ttl: int = 3600):
        self._keys: dict = {}
        self._fetched_at = 0.0
        self._ttl = ttl

    def get(self, kid: str, force: bool = False):
        if force or not self._keys or time.time() - self._fetched_at > self._ttl:
            resp = httpx.get(JWKS_URL, timeout=20)
            resp.raise_for_status()
            self._keys = {k["kid"]: k for k in resp.json().get("keys", [])}
            self._fetched_at = time.time()
        key = self._keys.get(kid)
        if key is None and not force:
            return self.get(kid, force=True)   # unknown kid -> keys may have rotated
        return key


_jwks = _JwksCache()


class EntraTokenVerifier(TokenVerifier):
    """Verifies an Entra-issued JWT. Returns None on any failure, which the SDK
    turns into a 401 — never raise, or a malformed token becomes a 500."""

    async def verify_token(self, token: str) -> AccessToken | None:
        try:
            from jose import jwt
            from jose.utils import base64url_decode  # noqa: F401  (import check)

            header = jwt.get_unverified_header(token)
            key = _jwks.get(header.get("kid", ""))
            if key is None:
                return None

            # Verify signature and expiry here, but NOT the audience: jose's
            # audience check compares with `audience not in audience_claims`,
            # which only works for a single string. Passing our two accepted
            # spellings as a list would reject every token. So we check the
            # audience ourselves, immediately below — it is the most important
            # claim and must not be skipped.
            claims = jwt.decode(
                token,
                key,
                algorithms=["RS256"],
                options={"verify_aud": False, "verify_exp": True, "verify_signature": True},
            )

            aud = claims.get("aud")
            aud_set = {aud} if isinstance(aud, str) else set(aud or [])
            if not (aud_set & VALID_AUDIENCES):
                return None          # token was minted for a different API

            if claims.get("iss") not in ISSUERS:
                return None

            # The token says `scp: "claims.access"`, but we advertise — and so
            # require — the api://… spelling. Report both so the SDK's scope
            # check matches whichever one it was told to look for.
            granted = (claims.get("scp") or "").split()
            scopes = granted + [f"api://{API_APP_ID}/{s}" for s in granted]
            return AccessToken(
                token=token,
                client_id=claims.get("azp") or claims.get("appid") or "unknown",
                scopes=scopes,
                expires_at=claims.get("exp"),
                subject=claims.get("oid") or claims.get("sub"),
                claims=claims,
            )
        except Exception:
            # bad signature, expired, wrong audience, malformed — all just "no"
            return None


# ── observability: see what a client actually sent ───────────────────────────
def peek(token: str) -> str:
    """Read a JWT's claims WITHOUT verifying it. Never use this to make a
    security decision — it exists so the log can show what the client sent even
    when (especially when) the token is one we reject."""
    import base64
    import json
    try:
        p = token.split(".")[1]
        c = json.loads(base64.urlsafe_b64decode(p + "=" * (-len(p) % 4)))
        return (f"aud={c.get('aud')} iss={c.get('iss')} "
                f"scp={c.get('scp')!r} appid={c.get('appid') or c.get('azp')} "
                f"upn={c.get('upn') or c.get('preferred_username')}")
    except Exception as e:
        return f"<undecodable: {type(e).__name__}>"


class EntraMetadata:
    """Serve our own RFC 9728 document, because the SDK's is unusable with Entra.

    A client reads `resource` here and replays it as the RFC 8707 `resource`
    parameter when it talks to the authorization server — the MCP spec requires
    that. Entra only accepts a `resource` equal to the API's App ID URI; hand it
    an https:// URL and it answers

        AADSTS9010010: The resource parameter provided in the request
                       doesn't match with the requested scopes.

    …at the *authorization* endpoint, before the user even signs in. So the whole
    handshake dies at Entra and this server never sees a thing.

    The SDK builds the document from `resource_server_url`, which pydantic pins
    to AnyHttpUrl, so `api://…` cannot be expressed there. Hence this override.

    Clients also probe several *paths*, and a 404 on the one they happen to try
    first can end the handshake. Observed from a real client:

        GET /.well-known/oauth-protected-resource      -> 200
        GET /.well-known/oauth-protected-resource/mcp  -> 404
        GET /.well-known/oauth-authorization-server    -> 404   <- gave up here

    RFC 9728 appends the resource's path, so a server mounted at /mcp is asked
    for `…/oauth-protected-resource/mcp`. Clients also look for the authorization
    server's metadata on the resource server before going upstream. We serve all
    of them, and the AS document advertises `S256` — which Entra supports but
    does not advertise, so a strict client would otherwise refuse to start.
    """

    def __init__(self, app, documents: dict):
        import json
        self.app = app
        self.routes = {p: json.dumps(d).encode() for p, d in documents.items()}

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            body = self.routes.get(scope.get("path", "").rstrip("/"))
            if body is not None:
                await send({"type": "http.response.start", "status": 200,
                            "headers": [(b"content-type", b"application/json"),
                                        (b"cache-control", b"no-store")]})
                await send({"type": "http.response.body", "body": body})
                return
        await self.app(scope, receive, send)


class RequestLog:
    """One line per request. Raw ASGI deliberately: Starlette's
    BaseHTTPMiddleware buffers the response and would break streaming, which is
    the whole transport here.

    This answers the only question that matters when a client "just fails":
    did it reach us at all, and what was in the token it sent?"""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)      # lifespan etc.

        seen: dict = {}

        async def send_wrapper(message):
            if message["type"] == "http.response.start":
                seen["status"] = message["status"]
                seen["hdrs"] = message.get("headers", [])
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            hdrs = {k.decode().lower(): v.decode() for k, v in scope.get("headers", [])}
            auth = hdrs.get("authorization", "")
            bits = [
                f"{scope['method']} {scope.get('path')}",
                f"-> {seen.get('status', '???')}",
                f"host={hdrs.get('host')}",
                f"ua={(hdrs.get('user-agent') or '')[:40]}",
                ("TOKEN " + peek(auth[7:])) if auth.lower().startswith("bearer ")
                else "NO TOKEN",
            ]
            for k, v in seen.get("hdrs", []):
                if k.decode().lower() == "www-authenticate":
                    bits.append(f"reply={v.decode()[:90]}")
            print("[req]", "  ".join(bits), flush=True)


def metadata_documents(public_url: str) -> dict:
    """The well-known documents to serve, keyed by path.

    `resource` is the canonical URI of this server and is what a client replays
    to Entra as the RFC 8707 `resource`. Entra accepts it only if the exact same
    string is an Application ID URI on the API registration:

        az ad app update --id <API_APP_ID> \
            --identifier-uris "api://<API_APP_ID>" "<this value>"

    Entra rejects a trailing slash in an identifier URI, so this must not end
    in one.
    """
    base = f"https://login.microsoftonline.com/{TENANT_ID}"
    resource_uri = public_url.rstrip("/") + "/mcp"
    prm = {
        "resource": resource_uri,
        "authorization_servers": [f"{base}/v2.0"],
        "scopes_supported": [SCOPE_URI],
        "bearer_methods_supported": ["header"],
    }
    asm = {
        "issuer": f"{base}/v2.0",
        "authorization_endpoint": f"{base}/oauth2/v2.0/authorize",
        "token_endpoint": f"{base}/oauth2/v2.0/token",
        "jwks_uri": f"{base}/discovery/v2.0/keys",
        "response_types_supported": ["code"],
        "response_modes_supported": ["query", "fragment", "form_post"],
        "grant_types_supported": ["authorization_code", "refresh_token"],
        "token_endpoint_auth_methods_supported": [
            "client_secret_post", "client_secret_basic"],
        # Entra supports S256 but omits it from its own metadata.
        "code_challenge_methods_supported": ["S256"],
        "scopes_supported": [SCOPE_URI, "openid", "profile", "offline_access"],
    }
    return {
        "/.well-known/oauth-protected-resource": prm,
        "/.well-known/oauth-protected-resource/mcp": prm,
        "/.well-known/oauth-authorization-server": asm,
        "/.well-known/oauth-authorization-server/mcp": asm,
        "/.well-known/openid-configuration": asm,
    }, resource_uri
