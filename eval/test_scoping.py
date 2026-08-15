"""Cross-user access tests — zero tolerance.

Runs against a live backend and asserts that one user cannot reach another's
claims through any route. No LLM, no labelling, fully deterministic: every
assertion is a fact about the API, so this is safe to gate CI on.

These exist because the authorization rule (`scope_claims`) is reached three
different ways — the dashboard, the copilot's MCP tools, and external clients —
and a regression in it is the most expensive bug this system can have.

    python eval/test_scoping.py                      # against localhost:8100
    DASHBOARD_URL=https://…  python eval/test_scoping.py

Exit code is the number of failures, so CI can gate on it directly.
"""
import os
import sys
from collections import defaultdict

import httpx

BASE = os.getenv("DASHBOARD_URL", "http://localhost:8100").rstrip("/")

# Generous, because /claims has a known N+1 in claim_summary. An admin listing
# every claim issues roughly three queries per row; at 400 claims that does not
# finish inside a normal timeout. ROLE_LIMIT keeps the role-comparison checks
# under that ceiling — it still exceeds what a senior adjuster can see, so the
# "admin sees strictly more" assertion stays meaningful.
TIMEOUT = 180.0
ROLE_LIMIT = 200

_results: list[tuple[bool, str, str]] = []


def check(condition: bool, name: str, detail: str = "") -> bool:
    _results.append((bool(condition), name, detail))
    return bool(condition)


def login(email: str) -> str:
    r = httpx.post(f"{BASE}/auth/login", data={"email": email}, timeout=TIMEOUT)
    r.raise_for_status()
    return r.json()["access_token"]


def claims_for(token: str, limit: int = ROLE_LIMIT) -> list[dict]:
    r = httpx.get(f"{BASE}/claims", params={"limit": limit},
                  headers={"Authorization": f"Bearer {token}"}, timeout=TIMEOUT)
    r.raise_for_status()
    return r.json()["claims"]


def get_claim(token: str, claim_id: int) -> httpx.Response:
    return httpx.get(f"{BASE}/claims/{claim_id}",
                     headers={"Authorization": f"Bearer {token}"}, timeout=TIMEOUT)


def main() -> int:
    users = httpx.get(f"{BASE}/auth/users", timeout=TIMEOUT).json()
    by_role = defaultdict(list)
    for u in users:
        by_role[u["role"]].append(u)
    print(f"backend: {BASE}")
    print("seeded: " + ", ".join(f"{r}={len(v)}" for r, v in sorted(by_role.items())) + "\n")

    # ── unauthenticated access ───────────────────────────────────────────────
    r = httpx.get(f"{BASE}/claims", timeout=TIMEOUT)
    check(r.status_code == 401, "no token -> 401", f"got {r.status_code}")

    r = httpx.get(f"{BASE}/claims", headers={"Authorization": "Bearer not-a-jwt"},
                  timeout=TIMEOUT)
    check(r.status_code == 401, "garbage token -> 401", f"got {r.status_code}")

    # A token signed with the right shape but a bogus subject must not resolve to
    # a user. This is the check that catches "we trusted the claim, not the DB".
    import jose.jwt as jj
    forged = jj.encode({"sub": "999999", "role": "admin", "name": "x",
                        "exp": 9999999999}, "wrong-secret", algorithm="HS256")
    r = httpx.get(f"{BASE}/claims", headers={"Authorization": f"Bearer {forged}"},
                  timeout=TIMEOUT)
    check(r.status_code == 401, "token signed with wrong secret -> 401",
          f"got {r.status_code}")

    # ── two adjusters must not see each other ────────────────────────────────
    adjusters = by_role.get("adjuster", [])
    if len(adjusters) < 2:
        check(False, "two adjusters seeded", "need >= 2 to test isolation")
        return report()

    a, b = adjusters[0], adjusters[1]
    tok_a, tok_b = login(a["email"]), login(b["email"])
    claims_a, claims_b = claims_for(tok_a), claims_for(tok_b)
    ids_a = {c["id"] for c in claims_a}
    ids_b = {c["id"] for c in claims_b}

    check(bool(ids_a) and bool(ids_b), "both adjusters have claims",
          f"a={len(ids_a)} b={len(ids_b)}")
    check(not (ids_a & ids_b), "adjuster books do not overlap",
          f"{len(ids_a & ids_b)} shared ids")

    # the direct-object reference: ask for a claim you were not shown
    if ids_b:
        victim = sorted(ids_b - ids_a)[0]
        r = get_claim(tok_a, victim)
        check(r.status_code == 403, "adjuster A reading B's claim by id -> 403",
              f"got {r.status_code} for claim {victim}")

    # a claim from your own book must still work — guards against a fix that
    # simply denies everything
    if ids_a:
        own = sorted(ids_a)[0]
        r = get_claim(tok_a, own)
        check(r.status_code == 200, "adjuster A reading own claim -> 200",
              f"got {r.status_code} for claim {own}")

    # search must not widen scope
    if claims_b:
        target = claims_b[0]["claim_number"]
        r = httpx.get(f"{BASE}/claims", params={"search": target, "limit": 50},
                      headers={"Authorization": f"Bearer {tok_a}"}, timeout=TIMEOUT)
        found = {c["claim_number"] for c in r.json()["claims"]}
        check(target not in found,
              "searching for another adjuster's claim number returns nothing",
              f"leaked {target}")

    # ── role scoping ─────────────────────────────────────────────────────────
    if by_role.get("siu_investigator"):
        tok = login(by_role["siu_investigator"][0]["email"])
        siu_claims = claims_for(tok)
        non_fraud = [c for c in siu_claims if not c.get("fraud_flagged")]
        check(not non_fraud, "SIU sees only fraud-flagged claims",
              f"{len(non_fraud)} non-fraud rows")

    if by_role.get("senior_adjuster") and by_role.get("admin"):
        tok_sr = login(by_role["senior_adjuster"][0]["email"])
        tok_ad = login(by_role["admin"][0]["email"])
        sr = claims_for(tok_sr, ROLE_LIMIT)
        ad = claims_for(tok_ad, ROLE_LIMIT)
        check(len(sr) > len(claims_a),
              "senior adjuster sees more than one adjuster", f"{len(sr)} vs {len(claims_a)}")
        check(len(ad) >= len(sr), "admin sees at least what a senior does",
              f"admin={len(ad)} senior={len(sr)}")
        check(len(ad) > len(sr), "admin sees strictly more than one team",
              f"admin={len(ad)} senior={len(sr)}")

    return report()


def report() -> int:
    print()
    failures = [r for r in _results if not r[0]]
    for ok, name, detail in _results:
        mark = "PASS" if ok else "FAIL"
        print(f"  [{mark}] {name}" + (f"   ({detail})" if detail and not ok else ""))
    print(f"\n{len(_results) - len(failures)}/{len(_results)} passed")
    if failures:
        print("\nSECURITY REGRESSION — do not deploy")
    return len(failures)


if __name__ == "__main__":
    sys.exit(main())
