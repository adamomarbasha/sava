# External platform limits — what is known, what is not

**None of the numbers in `api/platform_budget.py` are verified platform limits.**
They are conservative development defaults chosen to be obviously safe, and they
must be replaced with values measured in production.

## Verified (measured 18 Aug 2026, on this machine)

| Claim | Evidence |
|---|---|
| YouTube blocks datacenter IP ranges by ASN | `youtube-transcript-api` returns `RequestBlocked` citing cloud-provider IPs; documented upstream in jdepoix/youtube-transcript-api#593 |
| YouTube bot-checks residential IPs too | `yt-dlp` returned `Sign in to confirm you're not a bot` from this residential connection |
| Browser cookies bypass the bot check locally | `cookiesfrombrowser=("chrome",)` succeeded where the anonymous request failed |
| TikTok thumbnail URLs are signed and expire | 10/10 stored TikTok thumbnails return HTTP 403; their `x-expires` timestamps are in the past |
| One `extract_info` call yields metadata **and** caption tracks | 286 caption segments + full metadata returned from a single call |

## NOT verified — do not treat as fact

- **Any requests-per-minute ceiling, for any platform.** None of YouTube,
  TikTok, or Instagram publishes a rate limit for the access paths Sava uses.
  Every `*_RPM` default is a guess.
- **Whether a residential proxy fully resolves the bot check**, or merely
  reduces its frequency. Untested — no proxy provider is configured.
- **Instagram behaviour at any volume.** The ingestor currently runs
  unauthenticated and logs a warning saying so. Treat all Instagram numbers as
  unknown.
- **Whether cookie auth survives at scale.** One account's cookies used at
  volume is a plausible ban vector. It is a local development convenience, not
  a production strategy.

## Configuration

Every value is env-overridable; defaults live in `DEFAULT_POLICIES`.

```
SAVA_YOUTUBE_MAX_CONCURRENCY=3     SAVA_YOUTUBE_RPM=30    SAVA_YOUTUBE_MIN_INTERVAL_S=0.5
SAVA_TIKTOK_MAX_CONCURRENCY=2      SAVA_TIKTOK_RPM=12     SAVA_TIKTOK_MIN_INTERVAL_S=2.0
SAVA_INSTAGRAM_MAX_CONCURRENCY=1   SAVA_INSTAGRAM_RPM=6   SAVA_INSTAGRAM_MIN_INTERVAL_S=5.0

SAVA_<PLATFORM>_FAILURE_THRESHOLD   consecutive hard failures before the circuit opens
SAVA_<PLATFORM>_OPEN_SECONDS        initial open duration (doubles per consecutive trip)
SAVA_<PLATFORM>_MAX_OPEN_SECONDS    ceiling on the open duration

SAVA_PROXY_URL                      rotating residential proxy (required in production)
SAVA_YTDLP_COOKIES_FROM_BROWSER     local development only
SAVA_ASYNC_SAVE=1                   non-blocking save path (leave on)
```

## Tuning from production

1. Watch `GET /api/ops/platforms` — specifically `rate_limited_rate` and
   `forbidden_rate`.
2. Raise `*_RPM` by ~25% only while both stay at zero across a full day.
3. Stop the moment either goes non-zero, then step back one increment.
4. Never tune upward while draining a backlog — that is not representative
   traffic.

`trips` climbing on any platform means the limit is already too high.

## Known architectural limit

Budget state is **per-process**. The API process and the worker share nothing,
so running N workers multiplies the effective request rate by N. Until a shared
backend exists (the seam is `_PlatformState`), either run a single worker or
divide the configured limits by the worker count.
