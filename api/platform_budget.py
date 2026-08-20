"""Central control for every external platform request.

One place decides whether Sava is allowed to talk to YouTube, TikTok, or
Instagram right now — instead of scattered `time.sleep()` calls and per-module
retry loops.

Design constraints:
  * In-process and dependency-free. Sava runs one API process and one worker;
    adding Redis purely for a token bucket would be infrastructure for its own
    sake. The seam is `_Backend`, so a shared backend can be swapped in when a
    second worker host appears (see `docs/PLATFORM_LIMITS.md`).
  * Fail *closed* on throttling, never on the user's save. A throttled platform
    parks the job; it does not lose data or surface a 429 to anyone.
  * Every limit is configuration, not a constant buried in ingestion logic.

IMPORTANT — the numeric defaults below are conservative development guesses,
NOT verified platform limits. None of these platforms publishes a documented
rate limit for the access paths Sava uses. They must be tuned from observed
production behaviour. See `docs/PLATFORM_LIMITS.md`.
"""
from __future__ import annotations

import logging
import os
import random
import re
import threading
import time
from collections import deque
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Callable, Deque, Dict, Optional

logger = logging.getLogger(__name__)


# ─── Outcomes ────────────────────────────────────────────────────────────────

class Outcome:
    OK = "ok"
    RATE_LIMITED = "rate_limited"     # 429 / explicit throttle
    FORBIDDEN = "forbidden"           # 403 / bot-check / login wall
    TIMEOUT = "timeout"
    NOT_FOUND = "not_found"           # 404 / deleted / private — not our fault
    ERROR = "error"


class PlatformUnavailable(RuntimeError):
    """Raised when a platform is throttled or circuit-open.

    Carries `retry_after` so the job queue can park the job for exactly that
    long rather than burning an attempt.
    """

    def __init__(self, platform: str, reason: str, retry_after: float):
        super().__init__(f"{platform} unavailable ({reason}); retry in {retry_after:.0f}s")
        self.platform = platform
        self.reason = reason
        self.retry_after = max(1.0, float(retry_after))


# ─── Error classification ────────────────────────────────────────────────────

_RATE_PATTERNS = re.compile(
    r"429|too many requests|rate.?limit|slow down|try again later|"
    r"temporarily blocked|quota exceeded|resource_exhausted",
    re.IGNORECASE,
)
_FORBIDDEN_PATTERNS = re.compile(
    r"403|forbidden|sign in to confirm|not a bot|bot detection|captcha|"
    r"login required|age.?restricted|blocked|consent|unauthorized|"
    r"account.*(?:private|restricted)|ip.*(?:ban|block)",
    re.IGNORECASE,
)
_NOT_FOUND_PATTERNS = re.compile(
    r"404|not found|unavailable|removed|deleted|does not exist|"
    r"private video|no longer available|terminated",
    re.IGNORECASE,
)
_TIMEOUT_PATTERNS = re.compile(
    r"timed? ?out|timeout|read timeout|connection reset|connection aborted|"
    r"temporary failure in name resolution",
    re.IGNORECASE,
)
_RETRY_AFTER = re.compile(r"retry[-_ ]?after[\"':= ]+(\d+)", re.IGNORECASE)


def classify(exc_or_text) -> str:
    """Map an exception or error string onto an Outcome.

    Ordering matters: "not found" is checked before "forbidden" because a
    private video's message often contains both, and a deleted video must not
    trip the circuit breaker — nothing is wrong with the platform.
    """
    text = str(exc_or_text or "")
    if not text:
        return Outcome.ERROR
    if _NOT_FOUND_PATTERNS.search(text):
        return Outcome.NOT_FOUND
    if _RATE_PATTERNS.search(text):
        return Outcome.RATE_LIMITED
    if _FORBIDDEN_PATTERNS.search(text):
        return Outcome.FORBIDDEN
    if _TIMEOUT_PATTERNS.search(text):
        return Outcome.TIMEOUT
    return Outcome.ERROR


def parse_retry_after(exc_or_text) -> Optional[float]:
    """Honour an explicit Retry-After when the platform gives us one."""
    m = _RETRY_AFTER.search(str(exc_or_text or ""))
    if m:
        try:
            return max(1.0, float(m.group(1)))
        except ValueError:
            return None
    return None


# ─── Policy ──────────────────────────────────────────────────────────────────

def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, default))
    except (TypeError, ValueError):
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, default))
    except (TypeError, ValueError):
        return default


@dataclass
class PlatformPolicy:
    """Per-platform operating limits. Every value is env-overridable."""
    name: str
    max_concurrency: int
    requests_per_minute: float
    min_interval_s: float                 # spacing between requests
    failure_threshold: int                # consecutive hard failures -> open
    open_seconds: float                   # how long the breaker stays open
    max_open_seconds: float               # ceiling after repeated trips
    backoff_base_s: float = 30.0
    backoff_max_s: float = 1800.0

    @classmethod
    def from_env(cls, name: str, *, concurrency: int, rpm: float,
                 min_interval: float, failures: int, open_s: float) -> "PlatformPolicy":
        up = name.upper()
        return cls(
            name=name,
            max_concurrency=_env_int(f"SAVA_{up}_MAX_CONCURRENCY", concurrency),
            requests_per_minute=_env_float(f"SAVA_{up}_RPM", rpm),
            min_interval_s=_env_float(f"SAVA_{up}_MIN_INTERVAL_S", min_interval),
            failure_threshold=_env_int(f"SAVA_{up}_FAILURE_THRESHOLD", failures),
            open_seconds=_env_float(f"SAVA_{up}_OPEN_SECONDS", open_s),
            max_open_seconds=_env_float(f"SAVA_{up}_MAX_OPEN_SECONDS", 3600.0),
        )


# Conservative development defaults. NOT verified platform limits.
#
# YouTube    most permissive of the three for metadata/captions, but it is the
#            platform that actively bot-checks datacenter IPs.
# TikTok     no public API for this access path; signed URLs and aggressive
#            anti-automation. Kept slow deliberately.
# Instagram  highest-risk dependency by a distance — see the launch assessment.
#            Deliberately the most restrictive.
DEFAULT_POLICIES: Dict[str, PlatformPolicy] = {
    "youtube": PlatformPolicy.from_env(
        "youtube", concurrency=3, rpm=30, min_interval=0.5, failures=5, open_s=120),
    "tiktok": PlatformPolicy.from_env(
        "tiktok", concurrency=2, rpm=12, min_interval=2.0, failures=4, open_s=300),
    "instagram": PlatformPolicy.from_env(
        "instagram", concurrency=1, rpm=6, min_interval=5.0, failures=3, open_s=600),
    "other": PlatformPolicy.from_env(
        "other", concurrency=2, rpm=30, min_interval=0.5, failures=5, open_s=120),

    # Not a platform — a *capacity*. Transcription is the one stage that can
    # saturate a host regardless of which platform the media came from, so it
    # gets its own ceiling. Without this, a queue full of TikToks would happily
    # start as many concurrent transcriptions as there are workers.
    "asr": PlatformPolicy.from_env(
        "asr", concurrency=2, rpm=120, min_interval=0.0, failures=6, open_s=60),

    # Also a capacity, and deliberately not the `tiktok` bucket. Background
    # acquisition is a crawl and is throttled like one (12 rpm, 2s apart);
    # resolving a stream so a human can watch it is a keypress, and putting it
    # behind the crawl budget would make every third swipe wait two seconds.
    # Separate buckets also mean a playback outage cannot open the circuit that
    # ingestion depends on, or the reverse.
    "playback": PlatformPolicy.from_env(
        "playback", concurrency=4, rpm=60, min_interval=0.2, failures=6, open_s=90),
}


# ─── Per-platform state ──────────────────────────────────────────────────────

@dataclass
class _PlatformState:
    policy: PlatformPolicy
    lock: threading.Lock = field(default_factory=threading.Lock)
    semaphore: threading.Semaphore = field(init=False)
    recent: Deque[float] = field(default_factory=deque)
    last_request_at: float = 0.0

    consecutive_failures: int = 0
    trips: int = 0
    open_until: float = 0.0
    throttled_until: float = 0.0
    probing: bool = False

    # counters (cheap in-process; the durable record is usage_events)
    total: int = 0
    ok: int = 0
    rate_limited: int = 0
    forbidden: int = 0
    timeouts: int = 0
    not_found: int = 0
    errors: int = 0
    retries: int = 0
    rejected: int = 0
    wait_ms_total: float = 0.0
    latency_ms: Deque[float] = field(default_factory=lambda: deque(maxlen=500))

    def __post_init__(self):
        self.semaphore = threading.Semaphore(max(1, self.policy.max_concurrency))

    @property
    def blocked_until(self) -> float:
        return max(self.open_until, self.throttled_until)

    def snapshot(self) -> dict:
        now = time.time()
        lat = sorted(self.latency_ms)

        def pct(p: float) -> float:
            if not lat:
                return 0.0
            return round(lat[min(len(lat) - 1, int(len(lat) * p))], 1)

        blocked = self.blocked_until
        return {
            "platform": self.policy.name,
            "state": ("open" if self.open_until > now
                      else "throttled" if self.throttled_until > now
                      else "closed"),
            "blocked_for_s": round(max(0.0, blocked - now), 1),
            "consecutive_failures": self.consecutive_failures,
            "trips": self.trips,
            "requests": self.total,
            "ok": self.ok,
            "rate_limited": self.rate_limited,
            "forbidden": self.forbidden,
            "timeouts": self.timeouts,
            "not_found": self.not_found,
            "errors": self.errors,
            "retries": self.retries,
            "rejected": self.rejected,
            "success_rate": round(self.ok / self.total, 4) if self.total else None,
            "rate_limited_rate": round(self.rate_limited / self.total, 4) if self.total else None,
            "forbidden_rate": round(self.forbidden / self.total, 4) if self.total else None,
            "timeout_rate": round(self.timeouts / self.total, 4) if self.total else None,
            "p50_latency_ms": pct(0.50),
            "p95_latency_ms": pct(0.95),
            "p99_latency_ms": pct(0.99),
            "avg_wait_ms": round(self.wait_ms_total / self.total, 1) if self.total else 0.0,
            "in_flight": max(0, self.policy.max_concurrency - self.semaphore._value),
            "policy": {
                "max_concurrency": self.policy.max_concurrency,
                "requests_per_minute": self.policy.requests_per_minute,
                "min_interval_s": self.policy.min_interval_s,
                "failure_threshold": self.policy.failure_threshold,
            },
        }


class PlatformRequestManager:
    """Concurrency, rate budget, and circuit breaking for external platforms."""

    def __init__(self, policies: Optional[Dict[str, PlatformPolicy]] = None):
        self._states: Dict[str, _PlatformState] = {
            name: _PlatformState(policy=p)
            for name, p in (policies or DEFAULT_POLICIES).items()
        }
        self._guard = threading.Lock()

    def _state(self, platform: str) -> _PlatformState:
        key = (platform or "other").lower()
        st = self._states.get(key)
        if st is not None:
            return st
        with self._guard:
            st = self._states.get(key)
            if st is None:
                st = _PlatformState(policy=DEFAULT_POLICIES["other"])
                self._states[key] = st
            return st

    # ── Admission ───────────────────────────────────────────────────────────

    def availability(self, platform: str, *, consume_probe: bool = False) -> tuple:
        """(is_available, seconds_until_available, reason). Never blocks.

        Read-only by default so callers can inspect state freely — the queue
        polls this on every claim, and monitoring reads it too. Only
        `acquire()` passes `consume_probe=True`, which is what actually spends
        the single probe allowed through a half-open circuit. Making this
        read-only matters: a stray introspection call must never eat the probe
        and leave the breaker stuck open.
        """
        st = self._state(platform)
        now = time.time()
        with st.lock:
            if st.open_until > now:
                # One probe is allowed near the end of the open window, so
                # recovery is detected without a flood of retries.
                if not st.probing and (st.open_until - now) < 15.0:
                    if consume_probe:
                        st.probing = True
                    return True, 0.0, "probe"
                return False, st.open_until - now, "circuit_open"
            if st.throttled_until > now:
                return False, st.throttled_until - now, "throttled"
        return True, 0.0, "ok"

    @contextmanager
    def acquire(self, platform: str, operation: str = "request",
                *, timeout: float = 30.0):
        """Reserve a slot. Yields a handle the caller reports the outcome to.

            with manager.acquire("youtube", "metadata") as slot:
                try:
                    ...
                    slot.ok()
                except Exception as e:
                    slot.failed(e)
                    raise
        """
        st = self._state(platform)
        available, wait_s, reason = self.availability(platform, consume_probe=True)
        if not available:
            with st.lock:
                st.rejected += 1
            raise PlatformUnavailable(platform, reason, wait_s)

        waited_start = time.monotonic()
        if not st.semaphore.acquire(timeout=timeout):
            with st.lock:
                st.rejected += 1
            raise PlatformUnavailable(platform, "concurrency_saturated", 30.0)

        try:
            self._respect_rate_budget(st)
            wait_ms = (time.monotonic() - waited_start) * 1000
            slot = _Slot(self, st, platform, operation, wait_ms)
            try:
                yield slot
            finally:
                slot._finalize()
        finally:
            st.semaphore.release()

    def _respect_rate_budget(self, st: _PlatformState) -> None:
        """Sleep just enough to honour rpm and min-interval.

        This is the ONLY sleep in the request path, and it is bounded by the
        policy — not an arbitrary constant sprinkled through ingestion code.
        """
        while True:
            with st.lock:
                now = time.time()
                window_start = now - 60.0
                while st.recent and st.recent[0] < window_start:
                    st.recent.popleft()

                sleep_for = 0.0
                if st.policy.requests_per_minute > 0 and \
                        len(st.recent) >= st.policy.requests_per_minute:
                    sleep_for = max(sleep_for, st.recent[0] + 60.0 - now)
                gap = now - st.last_request_at
                if gap < st.policy.min_interval_s:
                    sleep_for = max(sleep_for, st.policy.min_interval_s - gap)

                if sleep_for <= 0:
                    st.recent.append(now)
                    st.last_request_at = now
                    return

            # Jitter prevents worker threads synchronising into a thundering herd.
            time.sleep(min(sleep_for, 30.0) + random.uniform(0, 0.25))

    # ── Outcome bookkeeping ─────────────────────────────────────────────────

    def _record(self, st: _PlatformState, outcome: str, latency_ms: float,
                wait_ms: float, retry_after: Optional[float]) -> None:
        with st.lock:
            st.total += 1
            st.wait_ms_total += wait_ms
            st.latency_ms.append(latency_ms)

            if outcome == Outcome.OK:
                st.ok += 1
                st.consecutive_failures = 0
                if st.probing:
                    # Probe succeeded — close the breaker.
                    st.probing = False
                    st.open_until = 0.0
                    st.trips = 0
                    logger.info("platform %s recovered; circuit closed", st.policy.name)
                return

            if outcome == Outcome.NOT_FOUND:
                # Content-level problem, not a platform problem. Never trips
                # the breaker — a wave of deleted videos must not look like
                # an outage.
                st.not_found += 1
                return

            if outcome == Outcome.RATE_LIMITED:
                st.rate_limited += 1
            elif outcome == Outcome.FORBIDDEN:
                st.forbidden += 1
            elif outcome == Outcome.TIMEOUT:
                st.timeouts += 1
            else:
                st.errors += 1

            st.consecutive_failures += 1

            if outcome == Outcome.RATE_LIMITED:
                cool = retry_after or min(
                    st.policy.backoff_base_s * (2 ** min(st.trips, 6)),
                    st.policy.backoff_max_s,
                )
                st.throttled_until = max(st.throttled_until, time.time() + cool)
                logger.warning("platform %s rate limited; pausing %.0fs",
                               st.policy.name, cool)

            if st.consecutive_failures >= st.policy.failure_threshold:
                st.trips += 1
                st.probing = False
                window = min(st.policy.open_seconds * (2 ** min(st.trips - 1, 4)),
                             st.policy.max_open_seconds)
                st.open_until = time.time() + window
                st.consecutive_failures = 0
                logger.error(
                    "platform %s circuit OPEN for %.0fs after %d consecutive failures "
                    "(trip #%d)", st.policy.name, window, st.policy.failure_threshold,
                    st.trips)

    # ── Introspection ───────────────────────────────────────────────────────

    def snapshot(self) -> dict:
        return {name: st.snapshot() for name, st in self._states.items()}

    def reset(self) -> None:
        """Test helper — clears all state."""
        with self._guard:
            for name, st in list(self._states.items()):
                self._states[name] = _PlatformState(policy=st.policy)


class _Slot:
    """Handle for one in-flight external request."""

    def __init__(self, manager: PlatformRequestManager, state: _PlatformState,
                 platform: str, operation: str, wait_ms: float):
        self._manager = manager
        self._state = state
        self.platform = platform
        self.operation = operation
        self.wait_ms = wait_ms
        self._started = time.monotonic()
        self._outcome: Optional[str] = None
        self._retry_after: Optional[float] = None
        self.bytes_moved = 0

    @property
    def latency_ms(self) -> float:
        return (time.monotonic() - self._started) * 1000

    def ok(self, *, bytes_moved: int = 0) -> None:
        self._outcome = Outcome.OK
        self.bytes_moved = bytes_moved

    def failed(self, exc_or_text, *, outcome: Optional[str] = None) -> str:
        self._outcome = outcome or classify(exc_or_text)
        self._retry_after = parse_retry_after(exc_or_text)
        return self._outcome

    def _finalize(self) -> None:
        if self._outcome is None:
            # Caller neither confirmed nor reported — treat as an error so a
            # silent bug cannot look like a healthy platform.
            self._outcome = Outcome.ERROR
        self._manager._record(self._state, self._outcome, self.latency_ms,
                              self.wait_ms, self._retry_after)


# ─── Module singleton ────────────────────────────────────────────────────────

_manager: Optional[PlatformRequestManager] = None
_manager_lock = threading.Lock()


def get_manager() -> PlatformRequestManager:
    global _manager
    if _manager is None:
        with _manager_lock:
            if _manager is None:
                _manager = PlatformRequestManager()
    return _manager


def guarded(platform: str, operation: str, fn: Callable, *args,
            db=None, canonical_content_id=None, user_id=None, **kwargs):
    """Run `fn` under platform control and record the outcome.

    `fn` must return an object exposing `.ok`, `.error`, and `.bytes_moved`
    (i.e. an `AcquisitionResult`), which is how every acquisition path already
    reports itself.
    """
    from .ai import telemetry

    manager = get_manager()
    with manager.acquire(platform, operation) as slot:
        try:
            result = fn(*args, **kwargs)
        except Exception as e:
            outcome = slot.failed(e)
            if db is not None:
                telemetry.record(
                    db, operation=f"platform.{operation}", platform=platform,
                    canonical_content_id=canonical_content_id, user_id=user_id,
                    wall_ms=int(slot.latency_ms), success=False,
                    error=f"[{outcome}] {e}"[:400],
                )
            raise

        ok = bool(getattr(result, "ok", False))
        nbytes = int(getattr(result, "bytes_moved", 0) or 0)
        if ok:
            slot.ok(bytes_moved=nbytes)
        else:
            slot.failed(getattr(result, "error", "") or "unknown failure")

        if db is not None:
            telemetry.record(
                db, operation=f"platform.{operation}", platform=platform,
                canonical_content_id=canonical_content_id, user_id=user_id,
                proxy_bytes=nbytes, wall_ms=int(slot.latency_ms),
                estimated_usd=telemetry.proxy_cost(nbytes),
                success=ok, error=(None if ok else str(getattr(result, "error", ""))[:400]),
            )
        return result
