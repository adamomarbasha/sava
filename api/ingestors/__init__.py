"""Ingestion providers.

Importing this package used to import every provider module, which meant
importing every provider's dependencies — including the two that the production
image deliberately does not install. `api/main.py` imports this package, so a
single absent optional package (`TikTokApi`) stopped Uvicorn from importing the
FastAPI application at all: no auth, no library, no search, no Ask, no
collections, on a deployment where only TikTok was actually affected.

Two changes keep that from recurring:

  * The provider modules themselves import their optional dependencies at use
    time (see `optional.py`), so importing one is always safe.
  * This module resolves provider classes lazily, via PEP 562 `__getattr__`, so
    `from .ingestors import add_bookmark` pulls in the registry and nothing
    else. A provider is imported when something asks for it, and a provider
    that cannot be imported is a `ProviderUnavailable` for that platform rather
    than an `ImportError` at startup.

The public names are unchanged.
"""
from .base import BaseIngestor
from .optional import ProviderUnavailable
from .registry import (add_bookmark, available_ingestors, get_ingestor,
                       provider_status, refresh_bookmark)

# name → module it lives in. Resolved on first attribute access.
_LAZY = {
    'YouTubeIngestor': '.youtube',
    'TikTokApiIngestor': '.tiktok_api',
    # Note: the dependency-free social shim, not the Playwright ingestor in
    # `.tiktok`. This mapping preserves the name each import previously got.
    'TikTokIngestor': '.social',
    'InstagramIngestor': '.social',
    'TwitterIngestor': '.social',
    'LinkedInIngestor': '.social',
    'RedditIngestor': '.social',
    'PinterestIngestor': '.social',
    'SnapchatIngestor': '.social',
    'FacebookIngestor': '.social',
}


def __getattr__(name):
    """Import a provider class on first use (PEP 562)."""
    module = _LAZY.get(name)
    if module is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    from importlib import import_module
    value = getattr(import_module(module, __name__), name)
    globals()[name] = value  # resolved once; subsequent access is a plain load
    return value


def __dir__():
    return sorted(set(globals()) | set(_LAZY))


__all__ = [
    'BaseIngestor',
    'YouTubeIngestor',
    'TikTokApiIngestor',
    'TikTokIngestor',
    'InstagramIngestor',
    'TwitterIngestor',
    'LinkedInIngestor',
    'RedditIngestor',
    'PinterestIngestor',
    'SnapchatIngestor',
    'FacebookIngestor',
    'ProviderUnavailable',
    'get_ingestor',
    'available_ingestors',
    'provider_status',
    'add_bookmark',
    'refresh_bookmark'
]
