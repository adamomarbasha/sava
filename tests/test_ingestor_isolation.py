"""Provider isolation: one platform's dependency must not be the API's.

The production incident this file exists to prevent, exactly:

    ModuleNotFoundError: No module named 'TikTokApi'
      api/main.py → api/ingestors/__init__.py → api/ingestors/tiktok_api.py

Uvicorn never finished importing the app. Auth, library, search, Ask and
collections were all perfectly functional code that could not run, because one
legacy TikTok backend imported a package the production image deliberately does
not ship.

The tests below run in a process where `TikTokApi` and `playwright` are *hidden
even if they happen to be installed locally*, which is the whole point: a
developer machine that has them cannot tell you whether the container will boot.
"""
from __future__ import annotations

import builtins
import importlib
import sys

import pytest


# The two packages requirements.txt deliberately excludes, plus everything that
# imports them — those submodules must be evicted too, or a cached import makes
# the simulation a no-op.
OPTIONAL_PACKAGES = ("TikTokApi", "playwright")


@pytest.fixture
def without_optional_packages(monkeypatch):
    """A process that looks exactly like the production container.

    `TikTokApi` and `playwright` raise ImportError on import, and every Sava
    module that touches them is reloaded from scratch inside that world.
    """
    real_import = builtins.__import__

    def blocked_import(name, *args, **kwargs):
        root = name.split(".")[0]
        if root in OPTIONAL_PACKAGES:
            raise ModuleNotFoundError(f"No module named {root!r}")
        return real_import(name, *args, **kwargs)

    for name in list(sys.modules):
        root = name.split(".")[0]
        if root in OPTIONAL_PACKAGES or name.startswith("api.ingestors"):
            monkeypatch.delitem(sys.modules, name, raising=False)

    monkeypatch.setattr(builtins, "__import__", blocked_import)
    yield
    # Leave the interpreter as we found it for the rest of the suite: the
    # modules were removed with monkeypatch.delitem, which restores them.
    for name in list(sys.modules):
        if name.startswith("api.ingestors"):
            del sys.modules[name]


class TestTheAPIBootsWithoutOptionalProviders:
    """The production failure, reproduced and then required not to happen."""

    def test_the_ingestors_package_imports(self, without_optional_packages):
        """This is the exact import that raised ModuleNotFoundError on Render."""
        ingestors = importlib.import_module("api.ingestors")
        assert callable(ingestors.add_bookmark)
        assert callable(ingestors.refresh_bookmark)

    def test_the_tiktok_provider_module_itself_imports(self, without_optional_packages):
        """Importing the provider is safe; only *using* it needs the package."""
        module = importlib.import_module("api.ingestors.tiktok_api")
        assert module.TikTokApiIngestor.dependencies_available() is False

    def test_the_playwright_provider_module_itself_imports(self, without_optional_packages):
        module = importlib.import_module("api.ingestors.tiktok")
        assert module.TikTokIngestor.dependencies_available() is False

    def test_the_fastapi_app_imports(self, without_optional_packages):
        """The end of the chain: uvicorn can import `api.main:app`."""
        for name in list(sys.modules):
            if name == "api.main":
                del sys.modules[name]
        main = importlib.import_module("api.main")
        assert main.app is not None


class TestOnlyTikTokIsAffected:
    def test_every_other_platform_still_resolves(self, without_optional_packages):
        """A TikTok-shaped failure must be TikTok-shaped."""
        registry = importlib.import_module("api.ingestors.registry")
        cases = {
            "https://www.youtube.com/watch?v=dQw4w9WgXcQ": "youtube",
            "https://www.instagram.com/p/ABC123/": "instagram",
            "https://twitter.com/someone/status/1": "twitter",
            "https://www.reddit.com/r/python/comments/abc/": "reddit",
            "https://www.linkedin.com/posts/abc": "linkedin",
            "https://www.pinterest.com/pin/1234/": "pinterest",
            "https://www.facebook.com/some/post": "facebook",
        }
        for url, platform in cases.items():
            ingestor = registry.get_ingestor(url)
            assert ingestor is not None, f"{platform} lost its ingestor"
            assert ingestor.platform == platform

    def test_the_unavailable_provider_is_simply_absent(self, without_optional_packages):
        registry = importlib.import_module("api.ingestors.registry")
        assert registry.get_tiktok_ingestors("https://www.tiktok.com/@a/video/1") == []
        # ...and nothing else went missing with it.
        assert len(registry.available_ingestors()) == 8

    def test_status_names_the_platform_and_the_package(self, without_optional_packages):
        registry = importlib.import_module("api.ingestors.registry")
        status = registry.provider_status()
        assert status["TikTokApiIngestor"] == {
            "platform": "tiktok", "package": "TikTokApi",
            "available": False, "reason": "TikTokApi is not installed",
        }
        assert status["TikTokIngestor"]["package"] == "playwright"
        assert status["TikTokIngestor"]["available"] is False


class TestTheErrorIsProviderSpecific:
    @pytest.mark.asyncio
    async def test_saving_a_tiktok_says_tiktok(self, without_optional_packages):
        """Not a 500, and not a silently empty bookmark."""
        registry = importlib.import_module("api.ingestors.registry")
        with pytest.raises(registry.ProviderUnavailable) as exc:
            await registry.add_bookmark("https://www.tiktok.com/@a/video/123", 1)

        assert exc.value.provider == "tiktok"
        message = str(exc.value)
        assert "tiktok" in message.lower()
        assert "TikTokApi" in message
        assert "requirements-optional.txt" in message
        # The reassurance an operator reading a 503 needs.
        assert "Every other platform is unaffected" in message

    @pytest.mark.asyncio
    async def test_it_does_not_write_a_metadata_less_bookmark(self, without_optional_packages, db):
        """Failing loudly beats a library full of untitled TikToks."""
        from api.models import Bookmark
        registry = importlib.import_module("api.ingestors.registry")
        url = "https://www.tiktok.com/@a/video/456"
        with pytest.raises(registry.ProviderUnavailable):
            await registry.add_bookmark(url, 1, db)
        assert db.query(Bookmark).filter(Bookmark.url == url).first() is None

    def test_using_the_provider_directly_raises_the_same_error(self, without_optional_packages):
        module = importlib.import_module("api.ingestors.tiktok_api")
        optional = importlib.import_module("api.ingestors.optional")
        with pytest.raises(optional.ProviderUnavailable):
            optional.optional_import("TikTokApi", provider="tiktok", attr="TikTokApi")


class TestCoreDependenciesStillFailLoudly:
    """Isolation is for optional providers only — it must not hide real faults."""

    def test_a_missing_core_package_is_not_swallowed(self, monkeypatch):
        real_import = builtins.__import__

        def blocked_import(name, *args, **kwargs):
            if name.split(".")[0] == "yt_dlp":
                raise ModuleNotFoundError("No module named 'yt_dlp'")
            return real_import(name, *args, **kwargs)

        for name in list(sys.modules):
            if name.startswith("api.ingestors") or name.split(".")[0] == "yt_dlp":
                monkeypatch.delitem(sys.modules, name, raising=False)
        monkeypatch.setattr(builtins, "__import__", blocked_import)

        # yt-dlp is in requirements.txt. Its absence is a broken deployment and
        # has to surface at startup, not become a quietly degraded YouTube.
        with pytest.raises(ModuleNotFoundError):
            importlib.import_module("api.ingestors.registry")

        for name in list(sys.modules):
            if name.startswith("api.ingestors"):
                del sys.modules[name]


class TestHealthReportsProviderAvailability:
    def test_optional_providers_do_not_decide_health(self, db, without_optional_packages):
        """A provider that was never installed is not an outage."""
        import api.health as health
        report = health.health_report(db)
        providers = report["optional_providers"]
        assert providers["TikTokApiIngestor"]["available"] is False
        # `ok` reflects database, storage and queue — nothing else.
        assert set(report["checks"]) == {"database", "storage", "queue"}


class TestTikTokWorksWhereItsDependenciesExist:
    """Isolation must not be removal. Where the packages are installed —
    development machines, and any image built with requirements-optional.txt —
    the providers have to come back on their own, with no code change and no
    flag."""

    @pytest.mark.skipif(
        importlib.util.find_spec("TikTokApi") is None,
        reason="TikTokApi not installed here (as in the production image)",
    )
    def test_the_tiktokapi_provider_is_offered_when_installed(self):
        for name in list(sys.modules):
            if name.startswith("api.ingestors"):
                del sys.modules[name]
        registry = importlib.import_module("api.ingestors.registry")
        try:
            names = [type(i).__name__ for i in registry.get_tiktok_ingestors(
                "https://www.tiktok.com/@a/video/1")]
            assert "TikTokApiIngestor" in names
            assert registry.provider_status()["TikTokApiIngestor"]["available"]
            # ...and it is preferred over the Playwright fallback, as before.
            assert names[0] == "TikTokApiIngestor"
        finally:
            for name in list(sys.modules):
                if name.startswith("api.ingestors"):
                    del sys.modules[name]
