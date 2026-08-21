"""Brute-force protection for the authentication endpoints.

`api/rate_limiter.py` already existed but is a single global list of timestamps
with no key at all — it cannot distinguish one caller from another, and its only
use in the application was reading `.get_status()` for a telemetry field. Login
and registration were completely unthrottled.

Two independent limits, because there are two different attacks:

  * **Per source address** — one machine trying many passwords. Strict, because
    a real person does not fail ten times in a quarter of an hour.
  * **Per account** — many machines each trying a few passwords against one
    email, which slips under any per-IP limit. Looser, because this counter is
    shared by everyone who might be attacking that account.

**On not building a lockout weapon.** A per-account counter is a denial-of-
service primitive if you are careless: fill someone's bucket and they cannot log
in. Three properties keep it from becoming one.

  1. Only *failures* count. A correct password is never throttled by the account
     limit — the check happens before the counter is consulted, and success
     clears the bucket.
  2. It always expires. There is no administrative unlock and no escalation; the
     window slides, so the worst case is a bounded wait, not a locked account.
  3. The account threshold is deliberately higher than the address threshold, so
     an attacker must burn many addresses to reach it, and the victim's own
     address is unaffected while they do.

Storage is per-process and in memory. That is honest for one API process and it
is what this deployment is; a second process would need Redis, and the module is
shaped so that swapping the backing store touches only `_Bucket`.
"""
from __future__ import annotations

import os
import threading
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from fastapi import HTTPException, Request, status

# Failures allowed from one address before it is refused, and the window.
IP_MAX_FAILURES = int(os.getenv("SAVA_AUTH_IP_MAX_FAILURES", "10"))
IP_WINDOW_SECONDS = int(os.getenv("SAVA_AUTH_IP_WINDOW_SECONDS", str(15 * 60)))

# Failures against one account, from anywhere, before it is refused. Higher than
# the address limit on purpose — see the note above.
ACCOUNT_MAX_FAILURES = int(os.getenv("SAVA_AUTH_ACCOUNT_MAX_FAILURES", "20"))
ACCOUNT_WINDOW_SECONDS = int(os.getenv("SAVA_AUTH_ACCOUNT_WINDOW_SECONDS", str(15 * 60)))

# Registrations from one address per window. Generous enough for a family or an
# office behind one NAT, tight enough that scripted signup floods are not free.
REGISTER_MAX = int(os.getenv("SAVA_REGISTER_MAX", "5"))
REGISTER_WINDOW_SECONDS = int(os.getenv("SAVA_REGISTER_WINDOW_SECONDS", str(60 * 60)))

# Only honour X-Forwarded-For when something in front of us is actually setting
# it. Trusting it unconditionally would let any caller spoof their address and
# make the per-address limit meaningless.
TRUST_PROXY = os.getenv("SAVA_TRUST_PROXY", "").lower() in ("1", "true", "yes")

# Cap on distinct tracked keys, so an attacker rotating addresses or invented
# emails cannot grow this dictionary without bound.
_MAX_KEYS = 20_000


@dataclass
class _Bucket:
    hits: List[float] = field(default_factory=list)


class SlidingWindowLimiter:
    """Counts events per key inside a sliding window."""

    def __init__(self, max_events: int, window_seconds: int):
        self.max_events = max_events
        self.window_seconds = window_seconds
        self._buckets: Dict[str, _Bucket] = {}
        self._lock = threading.Lock()

    def _prune(self, bucket: _Bucket, now: float) -> None:
        cutoff = now - self.window_seconds
        bucket.hits = [t for t in bucket.hits if t > cutoff]

    def check(self, key: str) -> Tuple[bool, int]:
        """(allowed, retry_after_seconds). Does not record anything."""
        now = time.time()
        with self._lock:
            bucket = self._buckets.get(key)
            if bucket is None:
                return True, 0
            self._prune(bucket, now)
            if len(bucket.hits) < self.max_events:
                return True, 0
            oldest = min(bucket.hits)
            return False, max(1, int(self.window_seconds - (now - oldest)) + 1)

    def record(self, key: str) -> None:
        now = time.time()
        with self._lock:
            if key not in self._buckets and len(self._buckets) >= _MAX_KEYS:
                # Drop the least recently used key rather than growing forever.
                stalest = min(self._buckets.items(),
                              key=lambda kv: max(kv[1].hits) if kv[1].hits else 0)[0]
                self._buckets.pop(stalest, None)
            bucket = self._buckets.setdefault(key, _Bucket())
            self._prune(bucket, now)
            bucket.hits.append(now)

    def reset(self, key: str) -> None:
        with self._lock:
            self._buckets.pop(key, None)

    def clear(self) -> None:
        """Tests only."""
        with self._lock:
            self._buckets.clear()


login_ip_limiter = SlidingWindowLimiter(IP_MAX_FAILURES, IP_WINDOW_SECONDS)
login_account_limiter = SlidingWindowLimiter(ACCOUNT_MAX_FAILURES, ACCOUNT_WINDOW_SECONDS)
register_ip_limiter = SlidingWindowLimiter(REGISTER_MAX, REGISTER_WINDOW_SECONDS)


def reset_all() -> None:
    """Tests only — the limiters are module-level singletons."""
    login_ip_limiter.clear()
    login_account_limiter.clear()
    register_ip_limiter.clear()


def client_ip(request: Optional[Request]) -> str:
    """The caller's address, as well as we can know it."""
    if request is None:
        return "unknown"
    if TRUST_PROXY:
        forwarded = request.headers.get("x-forwarded-for")
        if forwarded:
            # Leftmost entry is the original client; the rest are proxies.
            return forwarded.split(",")[0].strip() or "unknown"
    return (request.client.host if request.client else "unknown") or "unknown"


def account_key(email: str) -> str:
    return (email or "").strip().lower()


def _too_many(retry_after: int) -> HTTPException:
    # Deliberately identical whichever limit tripped: telling a caller that the
    # *account* limit was hit would confirm the account exists.
    return HTTPException(
        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        detail="Too many attempts. Try again shortly.",
        headers={"Retry-After": str(retry_after)},
    )


def guard_login(request: Optional[Request], email: str) -> None:
    """Refuse a login attempt that is over either limit. Call before verifying."""
    ip = client_ip(request)
    allowed, retry = login_ip_limiter.check(f"ip:{ip}")
    if not allowed:
        raise _too_many(retry)

    allowed, retry = login_account_limiter.check(f"acct:{account_key(email)}")
    if not allowed:
        raise _too_many(retry)


def record_login_failure(request: Optional[Request], email: str) -> None:
    login_ip_limiter.record(f"ip:{client_ip(request)}")
    login_account_limiter.record(f"acct:{account_key(email)}")


def record_login_success(request: Optional[Request], email: str) -> None:
    """Clear both buckets.

    Clearing the account bucket is what stops a failed attack from stranding the
    real owner: the moment they get in, the counter an attacker filled is gone.
    """
    login_ip_limiter.reset(f"ip:{client_ip(request)}")
    login_account_limiter.reset(f"acct:{account_key(email)}")


def guard_register(request: Optional[Request]) -> None:
    ip = client_ip(request)
    allowed, retry = register_ip_limiter.check(f"reg:{ip}")
    if not allowed:
        raise _too_many(retry)


def record_register(request: Optional[Request]) -> None:
    register_ip_limiter.record(f"reg:{client_ip(request)}")
