"""What counts as short-form.

One rule, one place. The classification has to agree in three separate
contexts — the ingestion pipeline that writes `is_short`, the backfill that
repairs rows saved before this column existed, and the serializer the client
reads — and three copies of "is it vertical?" would drift within a week.

The two platforms behave differently and the rule reflects that rather than
pretending they don't:

  * **TikTok video** is short-form by construction. The feed is vertical, the
    player is vertical, and a nine-minute TikTok is still a TikTok. Duration is
    not the distinguishing property, format is.
  * **YouTube** is mostly long-form, so a Short has to be identified. Two
    signals: the `/shorts/` URL the user actually saved, and the shape of the
    media itself (short *and* taller than it is wide). Either is sufficient —
    the URL because it is YouTube's own declaration, the geometry because a
    Short reached through `watch?v=` still is one.

A TikTok photo post is short-form too: it swipes in the same viewer, it just
pages images instead of frames.
"""
from __future__ import annotations

from typing import Optional
from urllib.parse import urlparse

# YouTube's own ceiling for Shorts is 180s. Using their number rather than
# inventing one means the rule stays right when a 3-minute Short shows up.
SHORT_MAX_SECONDS = 181


def is_shorts_url(url: Optional[str]) -> bool:
    """True for `youtube.com/shorts/<id>` in any host/casing variant.

    Worth capturing at save time: `resolve_identity` deliberately normalizes
    every YouTube URL to `watch?v=<id>` so a Short and a watch link stay one
    canonical row, which means the `/shorts/` evidence is destroyed unless
    something reads it first.
    """
    if not url:
        return False
    try:
        path = (urlparse(url).path or "").lower()
    except Exception:
        return False
    return path.startswith("/shorts/") or "/shorts/" in path


def is_short_form(
    platform: Optional[str],
    *,
    media_kind: Optional[str] = None,
    duration_seconds: Optional[float] = None,
    width: Optional[int] = None,
    height: Optional[int] = None,
    url_hint: Optional[str] = None,
) -> bool:
    """Whether this content belongs in the vertical swipe viewer."""
    platform = (platform or "").lower()
    kind = (media_kind or "").lower()

    if kind in ("article", "unknown") and platform != "tiktok":
        return False

    if platform == "tiktok":
        # Video and carousel both page vertically; only a plain link does not.
        return kind in ("video", "carousel", "image", "")

    if platform == "instagram":
        # Reels are vertical video and carousels page horizontally; both belong
        # in the same viewer TikTok already uses. A screenshot capture never
        # does — there is nothing to play and no post it can claim to be.
        return kind in ("video", "carousel", "image")

    if platform == "youtube":
        if is_shorts_url(url_hint):
            return True
        if not duration_seconds or duration_seconds > SHORT_MAX_SECONDS:
            return False
        # Without dimensions a sub-3-minute YouTube video is far more likely to
        # be a normal short video than a Short, so absence of evidence is not
        # treated as evidence.
        if not width or not height:
            return False
        return height > width

    return False


def derive_for(cc, *, url_hint: Optional[str] = None) -> bool:
    """`is_short_form` applied to a `CanonicalContent` row."""
    return is_short_form(
        cc.platform,
        media_kind=cc.media_kind,
        duration_seconds=cc.duration_seconds,
        width=getattr(cc, "width", None),
        height=getattr(cc, "height", None),
        url_hint=url_hint or cc.canonical_url,
    )
