# Deploying Sava

Everything here has been verified locally against PostgreSQL 16 + pgvector by
running the exact commands the container runs. What has **not** been verified is
a live deployment — that needs an account, a payment method and a DNS record,
none of which exist yet.

## What runs where

```
  iPhone (TestFlight)
     │  HTTPS
     ▼
  api.sava.app                          Render web service, Docker, 2 uvicorn workers
     ├── FastAPI          ENVIRONMENT=production, fails closed on missing config
     │
     ├── PostgreSQL 16 + pgvector       Render managed database
     │      Sava's job queue lives here — no broker, no Redis
     │
     ├── sava-worker                    Render worker service, SAME image
     │      python -m api.worker --concurrency 2
     │
     ├── Cloudflare R2                  private bucket, presigned URLs
     │
     ├── Gemini                         understanding + embeddings
     └── Sentry                         API and worker
```

Two processes, one image, one database, one bucket. No Kubernetes, no Redis, no
Celery — Sava's queue is already Postgres-backed with `FOR UPDATE SKIP LOCKED`,
leases and bounded retries, and the concurrency behaviour is covered by tests
that run against a real Postgres.

## Why this stack

I compared these against what Sava actually needs: two long-running processes
from one repository, Postgres **with pgvector**, and cheap image egress.

| Option | Fit | Why not chosen |
| --- | --- | --- |
| **Render** | ✅ chosen for compute + database | — |
| Railway | Good | Equivalent capability; configuration lives in the dashboard rather than a file in the repo, so the deployment is less reviewable |
| Fly.io | Good, more control | Needs a separately managed Postgres and more per-service configuration for no benefit at this size |
| Neon | Excellent Postgres | Adds a third vendor. Worth switching to if you want database branching or outgrow Render's plans |
| Supabase | Postgres + storage + auth | Sava has its own auth; adopting theirs would be a rewrite, and using only their Postgres is Neon with more surface |
| **Cloudflare R2** | ✅ chosen for object storage | — |

**Render** because a blueprint (`render.yaml`) declares the web service, the
worker and the database in one file that lives in the repo and gets reviewed
like code, and because `worker` is a first-class service type rather than a cron
job pretending to be one.

**R2** because Sava serves a thumbnail for every item on every library screen,
and R2 charges nothing for egress. On S3 that same traffic is the largest line
in the bill at any real scale.

> Vendor capabilities change and I cannot verify them from this repository.
> Confirm pgvector availability on your chosen plan before committing. Sava
> fails loudly rather than silently if it is absent: `ensure_extensions()` runs
> before `create_all` and raises a named error, so a provider without pgvector
> breaks the first deploy instead of producing a subtly broken search.

## Environment variables

`api/config.py::production_config_errors()` is the authority. It reports **every**
problem at once at startup, so you fix them in one pass rather than one redeploy
at a time.

| Variable | Required | Notes |
| --- | --- | --- |
| `ENVIRONMENT` | effectively | Defaults to `production`. Set explicitly anyway so intent is visible |
| `SECRET_KEY` | **yes** | ≥32 chars. Generate: `python -c 'import secrets; print(secrets.token_urlsafe(64))'`. Rotating signs everyone out |
| `DATABASE_URL` | **yes** | Must be PostgreSQL; SQLite is refused in production |
| `GEMINI_API_KEY` | **yes** | Without it saves are stored but never understood |
| `SAVA_S3_BUCKET` | **yes** | |
| `SAVA_S3_ACCESS_KEY_ID` | **yes** | |
| `SAVA_S3_SECRET_ACCESS_KEY` | **yes** | |
| `SAVA_S3_ENDPOINT_URL` | for R2 | `https://<account>.r2.cloudflarestorage.com` |
| `SAVA_S3_REGION` | no | `auto` for R2 |
| `SAVA_S3_PUBLIC_BASE_URL` | no | Only applies to keys under `public/`. User media is always presigned |
| `SAVA_TRUST_PROXY` | **yes on Render** | Without it every request appears to come from the load balancer and the per-address login limit protects nobody |
| `SENTRY_DSN` | recommended | Absent, error reporting no-ops silently |
| `SAVA_ENABLE_DOCS` | no | Leave unset. `/docs`, `/redoc`, `/openapi.json` are 404 in production |
| `WEB_CONCURRENCY` | no | uvicorn workers, default 2 |
| `SAVA_MAX_SAVES_PER_DAY` | no | Default 200. See `api/quota.py` |
| `SAVA_QUEUE_STALL_SECONDS` | no | Default 900. Drives `/health` and your alert |

Never set `SAVA_PROXY_URL` or `SAVA_YTDLP_COOKIES_FROM_BROWSER` in production.
They belong to the frozen third-party ingestion path and are development-only.

## Deploying

```bash
# 1. Create the bucket (Cloudflare R2), note the endpoint and keys.
# 2. In Render: New → Blueprint → point at this repository.
#    render.yaml declares sava-api, sava-worker and sava-postgres.
# 3. Fill the `sync: false` variables in the dashboard.
# 4. Deploy. SECRET_KEY is generated by Render and shared with the worker.
```

Schema creation and migrations run automatically on first boot
(`api/main.py::on_startup` → `ensure_extensions` → `create_all` →
`run_migrations`), so there is no separate migration step for the first deploy.

## Verifying a deployment from outside

```bash
curl -s https://api.sava.app/health | jq            # expect ok: true
curl -s -o /dev/null -w '%{http_code}\n' https://api.sava.app/docs    # expect 404
curl -sD- -o /dev/null https://api.sava.app/livez | grep -i x-request-id
```

`/health` returns **503** when the database, storage or queue is broken, so an
uptime monitor needs no body parsing. Point one at it and alert on non-200.

The single most useful field is `checks.queue.oldest_queued_age_seconds`. A dead
worker leaves the API perfectly healthy and nothing draining; queue age is the
only number that moves in every version of that failure. Alert above
`SAVA_QUEUE_STALL_SECONDS`.

## After deploying, point the app at it

`ios/Info-Release.plist` ships `SAVA_API_BASE_URL = REPLACE_WITH_PRODUCTION_API_URL`
and `ios/Scripts/validate-api-config.sh` fails the Release build until it is a
real HTTPS origin. Replace it with `https://api.sava.app` — not an IP, not
http — and the build will pass.

## Local development is unchanged

```bash
ENVIRONMENT=development uvicorn api.main:app --reload
python -m api.worker
```

SQLite and local disk still work, because development is now something you ask
for by name rather than something you get by forgetting.
