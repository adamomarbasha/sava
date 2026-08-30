"""Credentials must never reach a log line.

Found in a real Render deploy log: startup printed

    DATABASE IN USE: postgresql://<user>:<password>@<host>/sava

putting the production Postgres password in plaintext where anyone with
dashboard access could read it, where log history keeps it, and from where any
log drain would forward it.

Two layers are tested here, because one is not enough:

  1. **At the source** — the startup diagnostic logs a sanitised description,
     and every path that logs an exception scrubs it first. A SQLAlchemy
     `OperationalError` quotes the connection URL as a matter of course.
  2. **A global filter** — `SecretScrubbingFilter` sits on the root handler and
     catches whatever the first layer missed: a library logging its own config,
     a future `logger.error(f"...{e}")` written in a hurry.

The second layer is why these tests assert on *log output* rather than on the
helper alone. A helper nobody calls protects nothing.
"""
from __future__ import annotations

import logging

import pytest

from api.config import describe_database_url
from api.observability import (SecretScrubbingFilter, configure_logging,
                               scrub_secrets)

# Not real credentials — shaped like them so the patterns are exercised.
FAKE_PASSWORD = "Sup3rS3cretPassw0rd"
FAKE_USER = "sava_prod_user"
FAKE_PG_URL = (f"postgresql://{FAKE_USER}:{FAKE_PASSWORD}"
               "@dpg-abc123.oregon-postgres.render.com:5432/sava?sslmode=require")


# ─── The sanitised description ───────────────────────────────────────────────

class TestDescribeDatabaseURL:
    def test_the_password_is_gone(self):
        out = describe_database_url(FAKE_PG_URL)
        assert FAKE_PASSWORD not in out

    def test_the_username_is_gone(self):
        assert FAKE_USER not in describe_database_url(FAKE_PG_URL)

    def test_the_query_string_is_gone(self):
        """`?sslmode=` is harmless; `?password=` is not. Neither is kept."""
        out = describe_database_url(FAKE_PG_URL + "&password=another")
        assert "another" not in out and "sslmode" not in out

    def test_it_still_says_something_useful(self):
        """The line exists to catch a silent switch of database. It must stay
        able to do that."""
        out = describe_database_url(FAKE_PG_URL)
        assert "dpg-abc123.oregon-postgres.render.com" in out
        assert "db=sava" in out
        assert out.startswith("postgresql")

    def test_sqlite_keeps_its_path(self):
        """On a laptop, *which file* is the entire point of the diagnostic."""
        out = describe_database_url("sqlite:////tmp/sava/bookmarks.db")
        assert "bookmarks.db" in out

    @pytest.mark.parametrize("value", ["", None, "nonsense", "://", "postgresql://"])
    def test_malformed_input_never_raises(self, value):
        assert isinstance(describe_database_url(value or ""), str)


# ─── The scrubber ────────────────────────────────────────────────────────────

class TestScrubSecrets:
    @pytest.mark.parametrize("text,secret", [
        (FAKE_PG_URL, FAKE_PASSWORD),
        (f"proxy=http://cust-x:{FAKE_PASSWORD}@resi.example.com:8080", FAKE_PASSWORD),
        (f"?password={FAKE_PASSWORD}&sslmode=require", FAKE_PASSWORD),
        ("api_key=AIzaSyDUMMYDUMMYDUMMYDUMMY123456", "AIzaSyDUMMYDUMMYDUMMYDUMMY123456"),
        ("token=abcdefghijklmnopqrs", "abcdefghijklmnopqrs"),
        ("Authorization: Bearer eyJhbGciOi.payload.signature", "eyJhbGciOi.payload.signature"),
        ("Bearer eyJhbGciOi.payload.signature", "eyJhbGciOi.payload.signature"),
    ])
    def test_secrets_are_removed(self, text, secret):
        assert secret not in scrub_secrets(text)

    @pytest.mark.parametrize("text", [
        # Postgres's most useful error. An earlier version matched `password`
        # followed by bare whitespace and destroyed it while redacting nothing.
        'FATAL: password authentication failed for user "sava_prod_user"',
        "could not connect to server at dpg-abc123.render.com port 5432",
        'relation "content_frames" does not exist',
        "token bucket refilled",
        "secret sauce recipe",
    ])
    def test_diagnostics_survive(self, text):
        assert scrub_secrets(text) == text

    def test_the_host_survives_a_credentialed_url(self):
        """Redacting the whole URL would remove the only useful part."""
        out = scrub_secrets(FAKE_PG_URL)
        assert "dpg-abc123.oregon-postgres.render.com" in out

    @pytest.mark.parametrize("value", ["", None])
    def test_empty_input_is_safe(self, value):
        assert scrub_secrets(value) == value

    def test_env_var_values_are_redacted_wherever_they_appear(self, monkeypatch):
        """The backstop that does not depend on recognising a syntax.

        If the literal value of a known secret variable turns up in a message
        by any route, it goes — even in prose no pattern would match.
        """
        monkeypatch.setenv("SECRET_KEY", FAKE_PASSWORD)
        out = scrub_secrets(f"the operator pasted {FAKE_PASSWORD} into a comment")
        assert FAKE_PASSWORD not in out

    def test_short_values_are_not_redacted(self, monkeypatch):
        """A three-character 'secret' would corrupt every unrelated message."""
        monkeypatch.setenv("SECRET_KEY", "abc")
        assert scrub_secrets("abc happens to appear here") == "abc happens to appear here"


# ─── The global filter, on real log output ───────────────────────────────────

class TestNothingLeaksThroughLogging:
    """Asserts on what a handler would actually emit."""

    def _capture(self, emit) -> str:
        """Run `emit()` against a handler wired exactly as production is."""
        import io
        stream = io.StringIO()
        handler = logging.StreamHandler(stream)
        handler.setFormatter(logging.Formatter("%(message)s"))
        handler.addFilter(SecretScrubbingFilter())
        logger = logging.getLogger("sava.redaction.test")
        logger.handlers = [handler]
        logger.propagate = False
        logger.setLevel(logging.DEBUG)
        try:
            emit(logger)
        finally:
            logger.handlers = []
        return stream.getvalue()

    def test_a_raw_url_logged_as_an_argument_is_scrubbed(self):
        out = self._capture(lambda log: log.info("DATABASE IN USE: %s", FAKE_PG_URL))
        assert FAKE_PASSWORD not in out
        assert FAKE_USER not in out

    def test_a_raw_url_logged_in_an_fstring_is_scrubbed(self):
        out = self._capture(lambda log: log.error(f"boom: {FAKE_PG_URL}"))
        assert FAKE_PASSWORD not in out

    def test_a_sqlalchemy_style_error_is_scrubbed(self):
        """The realistic shape: a driver error quoting the whole DSN."""
        message = (f'(psycopg.OperationalError) connection failed: {FAKE_PG_URL}\n'
                   f'[SQL: SELECT 1]')
        out = self._capture(lambda log: log.error("init failed: %s", message))
        assert FAKE_PASSWORD not in out
        assert "SELECT 1" in out, "the useful part must survive"

    def test_an_exception_traceback_message_is_scrubbed(self):
        def emit(log):
            try:
                raise RuntimeError(f"cannot reach {FAKE_PG_URL}")
            except RuntimeError as e:
                log.error("startup failed: %s", e)
        assert FAKE_PASSWORD not in self._capture(emit)

    def test_env_secrets_do_not_leak_through_logging(self, monkeypatch):
        monkeypatch.setenv("GEMINI_API_KEY", "AIzaSyDUMMYDUMMYDUMMYDUMMY99")
        out = self._capture(
            lambda log: log.warning("provider rejected AIzaSyDUMMYDUMMYDUMMYDUMMY99"))
        assert "AIzaSyDUMMYDUMMYDUMMYDUMMY99" not in out

    def test_the_filter_is_installed_by_configure_logging(self):
        """A filter that exists but is never attached protects nothing."""
        root = logging.getLogger()
        handler = logging.StreamHandler()
        root.addHandler(handler)
        try:
            configure_logging()
            assert any(isinstance(f, SecretScrubbingFilter)
                       for f in handler.filters), "scrubber not attached"
        finally:
            root.removeHandler(handler)


# ─── No call site logs a raw connection string ───────────────────────────────

class TestNoRawDatabaseURLInSource:
    """A grep-level guard, so the fix cannot be undone by accident.

    Cheap, and it catches the exact mistake that caused this: someone writing
    `logger.info(..., DATABASE_URL)` because it was convenient.
    """

    def test_no_module_logs_database_url_directly(self):
        import pathlib
        import re

        root = pathlib.Path(__file__).resolve().parent.parent / "api"
        offenders = []
        # A logging call whose arguments mention DATABASE_URL, unless it is
        # going through the sanitiser.
        pattern = re.compile(
            r"(logger|logging)\.\w+\([^)]*\bDATABASE_URL\b[^)]*\)", re.S)
        for path in root.rglob("*.py"):
            text = path.read_text(encoding="utf-8", errors="replace")
            for match in pattern.finditer(text):
                snippet = match.group(0)
                if "describe_database_url" in snippet or "scrub_secrets" in snippet:
                    continue
                offenders.append(f"{path.relative_to(root.parent)}: {snippet[:70]}")
        assert not offenders, "raw DATABASE_URL reaches a log call:\n" + "\n".join(offenders)
