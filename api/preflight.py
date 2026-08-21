"""Deployment readiness check.

    python -m api.preflight

Answers one question honestly: if this process were serving real users right
now, what would be broken? Every check corresponds to something that fails
silently rather than loudly — a server that boots, answers health checks, and
loses every uploaded thumbnail on the next deploy looks perfectly healthy from
the outside.

Exit code is non-zero when a blocker is present, so it can gate a deploy.
"""
from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from typing import List


@dataclass
class Check:
    name: str
    ok: bool
    blocking: bool
    detail: str


def run_checks() -> List[Check]:
    from .config import DATABASE_URL, ENVIRONMENT, GEMINI_API_KEY, OPENAI_API_KEY

    is_production = ENVIRONMENT.lower() not in ("development", "dev", "test", "testing")
    checks: List[Check] = []

    def add(name, ok, blocking, detail):
        checks.append(Check(name, ok, blocking and is_production, detail))

    # ── Secrets ──────────────────────────────────────────────────────────────
    secret = os.getenv("SECRET_KEY", "")
    if not secret:
        secret_detail = "unset — the server will refuse to start in production"
    elif secret == "your-secret-key-change-in-production":
        secret_detail = "still the development default — every token is forgeable"
    elif len(secret) < 32:
        secret_detail = f"only {len(secret)} chars; use 32+ of real randomness"
    else:
        secret_detail = "configured"
    add("JWT secret", secret_detail == "configured", True, secret_detail)

    # ── Database ─────────────────────────────────────────────────────────────
    is_sqlite = DATABASE_URL.startswith("sqlite")
    add("Database",
        not is_sqlite, True,
        "SQLite cannot back a deployment: one writer, wiped on redeploy, no "
        "vector index. Set DATABASE_URL to Postgres."
        if is_sqlite else f"{DATABASE_URL.split('://')[0]}")

    if not is_sqlite:
        try:
            from sqlalchemy import text

            from .db import engine
            with engine.connect() as conn:
                has_vector = conn.execute(text(
                    "SELECT 1 FROM pg_extension WHERE extname='vector'")).first()
            add("pgvector extension", bool(has_vector), True,
                "run: CREATE EXTENSION vector;" if not has_vector else "installed")
        except Exception as e:
            add("Database reachable", False, True, f"{type(e).__name__}: {e}"[:110])

    # ── Object storage ───────────────────────────────────────────────────────
    from .storage import LocalObjectStorage, get_storage
    storage = get_storage()
    # `name` is the class name, not a slug — compare against the type rather
    # than a string that never matches. This check silently passed before,
    # which is exactly the class of failure this module exists to catch.
    is_local = isinstance(storage, LocalObjectStorage)
    add("Object storage",
        not is_local, True,
        "local disk — thumbnails and collection covers are lost on every "
        "redeploy. Set SAVA_S3_* to an S3-compatible bucket."
        if is_local else storage.name)

    # ── AI ───────────────────────────────────────────────────────────────────
    add("AI provider", bool(GEMINI_API_KEY or OPENAI_API_KEY), True,
        "no GEMINI_API_KEY — summaries, Ask Sava and cover ranking are disabled"
        if not (GEMINI_API_KEY or OPENAI_API_KEY) else "configured")

    # ── Non-blocking, but they change what the product can do ────────────────
    add("Residential proxy", bool(os.getenv("SAVA_PROXY_URL")), False,
        "unset — YouTube commonly blocks datacenter IPs, so extraction from a "
        "cloud host may fail" if not os.getenv("SAVA_PROXY_URL") else "configured")

    add("CORS origins", True, False,
        os.getenv("SAVA_CORS_ORIGINS") or "none set (fine — iOS does not use CORS)")

    from .asr import get_asr
    asr = get_asr()
    add("Speech-to-text", True, False,
        f"{asr.name}" + ("" if asr.available else
                         " — content without captions gets no transcript"))

    return checks


def main() -> int:
    from .config import ENVIRONMENT

    checks = run_checks()
    print(f"Sava preflight — ENVIRONMENT={ENVIRONMENT}\n")

    width = max(len(c.name) for c in checks)
    blockers = 0
    for check in checks:
        if check.ok:
            mark = "ok  "
        elif check.blocking:
            mark = "FAIL"
            blockers += 1
        else:
            mark = "warn"
        print(f"  [{mark}] {check.name.ljust(width)}  {check.detail}")

    print()
    if blockers:
        print(f"{blockers} blocking issue(s). Not ready to serve real users.")
        return 1
    print("No blocking issues.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
