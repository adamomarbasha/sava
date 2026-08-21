"""Deprecated Instagram ingestor.

Replaced by `api/services/instagram.py`, which puts extraction behind a
provider interface. This shim remains only so that older imports fail loudly
and specifically rather than with an ImportError somewhere unrelated.

Why the previous implementation had to go, concretely:

  * It drove **instaloader against a single operator account** — one username,
    one password, one session file on disk. That is the exact dependency the
    production architecture is not allowed to have: it does not survive 100k
    users, and the account it depends on is one rate-limit away from taking the
    whole platform down for everybody.
  * It **fabricated metadata**. Every failure path returned
    `{"title": "Instagram Post", ...}`, which is indistinguishable from a
    successful extraction to every caller. Missing metadata is acceptable;
    metadata that claims to be real and is not corrupts the library.
  * It **wrote thumbnails to `static/thumbnails/` as relative paths**, bypassing
    the object-storage abstraction. Those references cannot be mirrored, cannot
    be served from a CDN, and are the reason legacy Instagram rows still have
    thumbnails that no storage backend knows about.
  * It had **no carousel support** — `post.url` only — so a multi-image post
    was silently reduced to its first frame.
"""
from __future__ import annotations


class InstagramApiIngestor:  # pragma: no cover - deprecated shim
    """Removed. Use `api.services.instagram.extract_metadata`."""

    def __init__(self, *args, **kwargs):
        raise RuntimeError(
            "InstagramApiIngestor has been removed. Instagram extraction now "
            "runs through api.services.instagram.extract_metadata(), behind the "
            "InstagramMetadataProvider interface."
        )
