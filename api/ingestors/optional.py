"""Optional provider dependencies, resolved at first use rather than at import.

The failure this exists to prevent, in the exact shape it happened:

    api/main.py
      → api/ingestors/__init__.py
        → api/ingestors/tiktok_api.py
          → from TikTokApi import TikTokApi      ← not in requirements.txt
    ModuleNotFoundError: No module named 'TikTokApi'

Uvicorn never finished importing the application. Auth, library, search, Ask and
collections were all fine; one legacy TikTok provider whose dependency the
production image deliberately does not ship took the entire API down with it.

A top-level `import` of an optional dependency is a decision that the whole
process cannot start without it. That is the right decision for `fastapi` and
`sqlalchemy`, and the wrong one for a per-platform extraction backend. So the
optional ones are imported here, on the path that actually needs them, and a
missing one becomes a `ProviderUnavailable` naming the platform and the package
instead of an `ImportError` at module scope.

This is deliberately *not* a way to make missing core dependencies survivable.
Core packages stay imported at module scope so a broken deployment still fails
loudly and immediately; only the packages `requirements.txt` documents as
excluded on purpose are routed through here.
"""
from __future__ import annotations

import importlib
import logging
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

# Where the pinned versions of these packages live, quoted in every error so an
# operator reading a 503 knows what to install without reading this file.
OPTIONAL_REQUIREMENTS = "requirements-optional.txt"


class ProviderUnavailable(RuntimeError):
    """One provider cannot run here. Every other provider still can.

    Carries the platform and the missing package so the API can answer "TikTok
    ingestion is unavailable on this deployment" rather than "500 Internal
    Server Error", which is indistinguishable from Sava being broken.
    """

    def __init__(self, provider: str, dependency: str, reason: str = ""):
        self.provider = provider
        self.dependency = dependency
        self.reason = reason
        detail = f" ({reason})" if reason else ""
        super().__init__(
            f"{provider} ingestion is unavailable on this deployment: the "
            f"optional dependency '{dependency}' is not installed{detail}. "
            f"Install it with `pip install -r {OPTIONAL_REQUIREMENTS}`. "
            f"Every other platform is unaffected."
        )


# module name → the imported module, or None once it is known to be missing.
# Cached both ways: a missing optional package must cost one failed import for
# the life of the process, not one per request.
_modules: Dict[str, Optional[Any]] = {}
_reasons: Dict[str, str] = {}


def _resolve(module: str) -> Optional[Any]:
    if module not in _modules:
        try:
            _modules[module] = importlib.import_module(module)
            logger.info("Optional dependency %r is available", module)
        except Exception as exc:  # not just ImportError: a broken native
            # extension raises whatever it likes, and none of it should be
            # allowed to escape into an unrelated request.
            _modules[module] = None
            _reasons[module] = f"{type(exc).__name__}: {exc}"
            logger.warning("Optional dependency %r is unavailable: %s", module, exc)
    return _modules[module]


def module_available(module: str) -> bool:
    """Is this optional dependency importable? Never raises."""
    return _resolve(module) is not None


def unavailable_reason(module: str) -> Optional[str]:
    """Why the import failed, once it has been attempted."""
    _resolve(module)
    return _reasons.get(module)


def optional_import(module: str, *, provider: str, dependency: Optional[str] = None,
                    attr: Optional[str] = None) -> Any:
    """Import an optional dependency at call time.

    Raises `ProviderUnavailable` — never `ImportError` — so callers up the stack
    handle one provider-shaped failure instead of a bare import error that looks
    like a bug in Sava.
    """
    resolved = _resolve(module)
    package = dependency or module
    if resolved is None:
        raise ProviderUnavailable(provider, package, unavailable_reason(module) or "")
    if attr is None:
        return resolved
    try:
        return getattr(resolved, attr)
    except AttributeError as exc:
        raise ProviderUnavailable(
            provider, package,
            f"{module} is installed but has no {attr!r} — version mismatch",
        ) from exc
