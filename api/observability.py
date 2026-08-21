"""Request correlation and error reporting.

Two small things that together answer "what happened to this user's save?".

**Request IDs.** Every request gets one, it is echoed in the response header, and
it is attached to every log line emitted while handling that request. Without it,
a production log is a single interleaved stream from many concurrent requests and
several worker threads, and reconstructing one user's failure means guessing
which lines belong together.

**Sentry.** Optional and configured by environment. Absent `SENTRY_DSN` this
module does nothing at all, which keeps development quiet and makes enabling it
in production a matter of setting one variable rather than changing code.

Deliberately not included: metrics, tracing, dashboards. Those are worth having
later and worth nothing now — the gap this closes is that a failure currently
leaves no durable record anywhere.
"""
from __future__ import annotations

import contextvars
import logging
import os
import uuid
from typing import Optional

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import ASGIApp

logger = logging.getLogger(__name__)

REQUEST_ID_HEADER = "X-Request-ID"

# Readable by log filters on any thread handling this request.
_request_id: contextvars.ContextVar[str] = contextvars.ContextVar(
    "sava_request_id", default="-")


def current_request_id() -> str:
    return _request_id.get()


class RequestIDFilter(logging.Filter):
    """Puts the current request id on every record, so formatters can use it."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = current_request_id()
        return True


class RequestContextMiddleware(BaseHTTPMiddleware):
    """Assigns a request id and logs one line per completed request."""

    def __init__(self, app: ASGIApp) -> None:
        super().__init__(app)

    async def dispatch(self, request, call_next):
        # Honour an inbound id so a mobile client or proxy can correlate too, but
        # bound its length — this value ends up in every log line.
        inbound = (request.headers.get(REQUEST_ID_HEADER) or "")[:64].strip()
        request_id = inbound or uuid.uuid4().hex[:16]
        token = _request_id.set(request_id)
        try:
            response = await call_next(request)
        except Exception:
            # Logged with the id attached, then re-raised so FastAPI's handler
            # (and Sentry, if enabled) still see it.
            logger.exception("unhandled error on %s %s",
                             request.method, request.url.path)
            raise
        finally:
            _request_id.reset(token)

        response.headers[REQUEST_ID_HEADER] = request_id
        # Path only, never the query string: search terms and saved URLs are user
        # content and do not belong in an access log.
        if response.status_code >= 400:
            logger.info("%s %s -> %s", request.method,
                        request.url.path, response.status_code)
        return response


def configure_logging() -> None:
    """Attach the request id to the root formatter, once."""
    root = logging.getLogger()
    for handler in root.handlers:
        handler.addFilter(RequestIDFilter())
        handler.setFormatter(logging.Formatter(
            "%(asctime)s %(levelname)s [%(request_id)s] %(name)s: %(message)s"))


def init_sentry(component: str = "api") -> bool:
    """Start Sentry if a DSN is configured. Returns whether it was enabled.

    `send_default_pii=False` is the important argument: Sava's request bodies
    contain saved URLs and Ask questions, which are the user's private content.
    An error report should say what broke, not what they were reading.
    """
    dsn = os.getenv("SENTRY_DSN")
    if not dsn:
        return False
    try:
        import sentry_sdk
    except ImportError:
        logger.warning("SENTRY_DSN is set but sentry-sdk is not installed")
        return False

    from .config import ENVIRONMENT

    sentry_sdk.init(
        dsn=dsn,
        environment=ENVIRONMENT,
        send_default_pii=False,
        traces_sample_rate=float(os.getenv("SENTRY_TRACES_SAMPLE_RATE", "0.0")),
        release=os.getenv("SAVA_RELEASE"),
    )
    sentry_sdk.set_tag("component", component)
    logger.info("Sentry enabled for %s (%s)", component, ENVIRONMENT)
    return True
