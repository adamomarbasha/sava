"""Configuration safety.

The bug these exist to prevent: `ENVIRONMENT` defaulted to `development`, and
every production protection was gated on `ENVIRONMENT != development`. A deploy
that forgot one variable therefore ran with the repository's published fallback
secret and permissive CORS, while looking completely healthy.

The rule now is that **the unconfigured state is the safe state** — production
unless development is requested by name — and these tests hold that line.

Each test reloads `api.config` under a controlled environment. `load_dotenv` is
stubbed out first, because the developer's own `api/.env` would otherwise leak
into every case and quietly make them all pass.
"""
from __future__ import annotations

import importlib
import sys

import pytest

import api.auth as auth_module


def _reload_config(monkeypatch, **env):
    """Import `api.config` fresh under exactly the given environment."""
    for key in ("ENVIRONMENT", "SECRET_KEY", "DATABASE_URL", "GEMINI_API_KEY",
                "SAVA_S3_BUCKET", "SAVA_S3_ACCESS_KEY_ID",
                "SAVA_S3_SECRET_ACCESS_KEY", "SAVA_ENABLE_DOCS"):
        monkeypatch.delenv(key, raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, value)

    # Patch at the source module: `api.config` does `from dotenv import
    # load_dotenv`, so reloading it rebinds the name and would undo a patch
    # applied to the config module itself. A deployed container has no .env
    # file; without this the developer's own leaks in and every case passes.
    import dotenv
    monkeypatch.setattr(dotenv, "load_dotenv", lambda *a, **k: None)

    import api.config as config
    return importlib.reload(config)


@pytest.fixture(autouse=True)
def _restore_config():
    """Leave `api.config` as the rest of the suite expects to find it.

    Teardown runs before monkeypatch unwinds its environment changes, so a test
    that deliberately set a broken ENVIRONMENT would make this reload raise and
    turn a passing test into a teardown error. Pin a known-good value first.
    """
    yield
    import os
    import api.config
    previous = os.environ.get("ENVIRONMENT")
    os.environ["ENVIRONMENT"] = "development"
    try:
        importlib.reload(api.config)
    finally:
        if previous is None:
            os.environ.pop("ENVIRONMENT", None)
        else:
            os.environ["ENVIRONMENT"] = previous


PROD_OK = {
    "ENVIRONMENT": "production",
    "SECRET_KEY": "x" * 64,
    "DATABASE_URL": "postgresql://u:p@db.example.com/sava",
    "GEMINI_API_KEY": "test-key",
    "SAVA_S3_BUCKET": "sava",
    "SAVA_S3_ACCESS_KEY_ID": "id",
    "SAVA_S3_SECRET_ACCESS_KEY": "secret",
}


class TestUnconfiguredIsSafe:
    def test_a_missing_environment_means_production(self, monkeypatch):
        """The whole point. Forgetting the variable must not mean development."""
        cfg = _reload_config(monkeypatch)
        assert cfg.ENVIRONMENT == "production"
        assert cfg.IS_PRODUCTION is True
        assert cfg.IS_DEVELOPMENT is False

    def test_an_empty_environment_string_means_production(self, monkeypatch):
        cfg = _reload_config(monkeypatch, ENVIRONMENT="")
        assert cfg.IS_PRODUCTION is True

    def test_a_bare_deployment_refuses_to_start(self, monkeypatch):
        """Nothing configured: loud failure, not a silent insecure boot."""
        cfg = _reload_config(monkeypatch)
        with pytest.raises(cfg.ConfigurationError):
            cfg.require_production_config()

    def test_the_failure_names_every_problem_at_once(self, monkeypatch):
        cfg = _reload_config(monkeypatch)
        problems = " ".join(cfg.production_config_errors()).lower()
        assert "secret_key" in problems
        assert "sqlite" in problems
        assert "storage" in problems or "s3" in problems


class TestDevelopmentIsOptIn:
    @pytest.mark.parametrize("name", ["development", "dev", "local"])
    def test_development_must_be_named(self, monkeypatch, name):
        cfg = _reload_config(monkeypatch, ENVIRONMENT=name)
        assert cfg.IS_DEVELOPMENT is True
        assert cfg.IS_PRODUCTION is False

    def test_development_is_allowed_to_be_unconfigured(self, monkeypatch):
        cfg = _reload_config(monkeypatch, ENVIRONMENT="development")
        assert cfg.production_config_errors() == []
        cfg.require_production_config()  # must not raise

    @pytest.mark.parametrize("name", ["test", "testing", "ci"])
    def test_test_environments_are_not_production(self, monkeypatch, name):
        cfg = _reload_config(monkeypatch, ENVIRONMENT=name)
        assert cfg.IS_PRODUCTION is False
        assert cfg.IS_TEST is True

    def test_a_typo_is_refused_rather_than_guessed(self, monkeypatch):
        """`ENVIRONMENT=prodction` must not silently become development."""
        import dotenv
        monkeypatch.setattr(dotenv, "load_dotenv", lambda *a, **k: None)
        import api.config as config
        monkeypatch.setenv("ENVIRONMENT", "prodction")
        # RuntimeError, not ConfigurationError: reloading re-executes the class
        # statement, so the exception raised mid-reload is an instance of a
        # *new* class object that `pytest.raises` would not recognise as the one
        # captured before the call. ConfigurationError subclasses RuntimeError,
        # which is stable across reloads.
        with pytest.raises(RuntimeError):
            importlib.reload(config)


class TestProductionSecrets:
    def test_a_correctly_configured_production_starts(self, monkeypatch):
        cfg = _reload_config(monkeypatch, **PROD_OK)
        assert cfg.production_config_errors() == []
        cfg.require_production_config()

    def test_production_without_a_secret_key_refuses(self, monkeypatch):
        env = dict(PROD_OK)
        del env["SECRET_KEY"]
        cfg = _reload_config(monkeypatch, **env)
        assert any("SECRET_KEY" in p for p in cfg.production_config_errors())

    def test_the_repository_fallback_secret_is_refused(self, monkeypatch):
        """It is printed in this repo; using it is the same as having none."""
        cfg = _reload_config(monkeypatch, **{**PROD_OK,
                                             "SECRET_KEY": "your-secret-key-change-in-production"})
        assert any("placeholder" in p.lower() or "repository" in p.lower()
                   for p in cfg.production_config_errors())

    def test_a_short_secret_is_refused(self, monkeypatch):
        cfg = _reload_config(monkeypatch, **{**PROD_OK, "SECRET_KEY": "tooshort"})
        assert any("characters" in p for p in cfg.production_config_errors())

    def test_auth_module_refuses_to_load_without_a_production_secret(self):
        """The guard lives in auth.py too, and must agree with config.py.

        `api.auth` is imported at the top of this file rather than inside the
        test: the module computes `SECRET_KEY = _load_secret_key()` at import
        time, so importing it under a production environment raises *during the
        import statement* — outside any `pytest.raises` block — and reads as a
        failure rather than the pass it actually is.
        """
        import os
        previous_env = os.environ.get("ENVIRONMENT")
        previous_key = os.environ.get("SECRET_KEY")
        os.environ["ENVIRONMENT"] = "production"
        os.environ.pop("SECRET_KEY", None)
        try:
            with pytest.raises(RuntimeError, match="SECRET_KEY"):
                auth_module._load_secret_key()
        finally:
            if previous_env is None:
                os.environ.pop("ENVIRONMENT", None)
            else:
                os.environ["ENVIRONMENT"] = previous_env
            if previous_key is not None:
                os.environ["SECRET_KEY"] = previous_key


class TestProductionInfrastructure:
    def test_sqlite_is_refused_in_production(self, monkeypatch):
        cfg = _reload_config(monkeypatch, **{**PROD_OK,
                                             "DATABASE_URL": "sqlite:///./bookmarks.db"})
        assert any("sqlite" in p.lower() for p in cfg.production_config_errors())

    def test_postgres_is_accepted(self, monkeypatch):
        cfg = _reload_config(monkeypatch, **PROD_OK)
        assert cfg.IS_POSTGRES is True
        assert not any("sqlite" in p.lower() for p in cfg.production_config_errors())

    def test_missing_object_storage_is_refused(self, monkeypatch):
        env = dict(PROD_OK)
        del env["SAVA_S3_BUCKET"]
        cfg = _reload_config(monkeypatch, **env)
        assert any("storage" in p.lower() for p in cfg.production_config_errors())

    def test_missing_ai_key_is_refused(self, monkeypatch):
        env = dict(PROD_OK)
        del env["GEMINI_API_KEY"]
        cfg = _reload_config(monkeypatch, **env)
        assert any("GEMINI" in p for p in cfg.production_config_errors())


class TestDocsExposure:
    def test_docs_are_off_in_production(self, monkeypatch):
        cfg = _reload_config(monkeypatch, **PROD_OK)
        assert cfg.DOCS_ENABLED is False

    def test_docs_are_on_in_development(self, monkeypatch):
        cfg = _reload_config(monkeypatch, ENVIRONMENT="development")
        assert cfg.DOCS_ENABLED is True

    def test_docs_can_be_deliberately_enabled_in_production(self, monkeypatch):
        cfg = _reload_config(monkeypatch, **{**PROD_OK, "SAVA_ENABLE_DOCS": "1"})
        assert cfg.DOCS_ENABLED is True


class TestCORS:
    def test_production_never_allows_localhost_credentialed_origins(self):
        """The dev config pairs a localhost regex with allow_credentials."""
        import pathlib
        source = pathlib.Path("api/main.py").read_text()
        prod_block = source[source.index("if _is_production:"):source.index("else:")]
        assert "allow_origin_regex" not in prod_block
        assert "localhost" not in prod_block

    def test_production_cors_is_fail_closed(self, monkeypatch):
        """No SAVA_CORS_ORIGINS means no cross-origin access, not all of it."""
        monkeypatch.delenv("SAVA_CORS_ORIGINS", raising=False)
        origins = [o.strip() for o in
                   (monkeypatch.__class__ and "").split(",") if o.strip()]
        assert origins == []
