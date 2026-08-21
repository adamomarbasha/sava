"""Playback routes for the short-form viewer.

Two endpoints with deliberately different auth models, because two different
HTTP clients call them:

  * `/api/bookmarks/{id}/playback` is called by the app, carries the bearer
    token like everything else, and answers "how does this play".
  * `/api/playback/{canonical_id}/stream` is called by `AVPlayer`, which issues
    its own requests and will not attach the app's Authorization header. It
    authenticates with the short-lived signed token minted by the first
    endpoint, scoped to one item and one user.

The stream route is the only place in Sava that proxies media bytes. It is
kept narrow on purpose: it will only serve a canonical row the requesting user
has actually saved, only for TikTok, and only for as long as the token lives.
"""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, StreamingResponse
from sqlalchemy.orm import Session

from .auth import get_current_user
from .db import get_db
from .models import Bookmark, CanonicalContent
from .authz import owned_bookmark
from .platform_budget import PlatformUnavailable
from .services import playback as playback_svc

logger = logging.getLogger(__name__)

router = APIRouter(tags=["playback"])


# See `api/authz.py` — one definition, used everywhere.
_owned_bookmark = owned_bookmark


def _base_url(request: Request) -> str:
    """The origin the client reached us on.

    Built from the request rather than from config because Sava is developed
    against a LAN IP and run in the simulator against localhost; a hardcoded
    base would hand the device a URL only the server can resolve.
    """
    return str(request.base_url).rstrip("/")


@router.get("/api/bookmarks/{bookmark_id}/playback")
def bookmark_playback(
    bookmark_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user=Depends(get_current_user),
):
    """How this save plays: video, embed, gallery, or an honest refusal."""
    bookmark = _owned_bookmark(db, bookmark_id, user["id"])
    cc = (db.query(CanonicalContent)
          .filter(CanonicalContent.id == bookmark.canonical_content_id)
          .first()) if bookmark.canonical_content_id else None

    if cc is None:
        # Saved before canonical linking, or still resolving. Not an error —
        # the item simply has nothing to play yet.
        return {
            "bookmark_id": bookmark_id,
            **playback_svc.PlaybackDescriptor(
                kind="unavailable", poster=bookmark.thumbnail_url,
                reason="This save hasn't finished processing yet.").as_dict(),
        }

    descriptor = playback_svc.descriptor_for(
        db, cc, user_id=user["id"], base_url=_base_url(request))
    return {"bookmark_id": bookmark_id, "canonical_id": cc.id, **descriptor.as_dict()}


@router.get("/api/playback/{canonical_id}/embed", response_class=HTMLResponse)
def playback_embed(
    canonical_id: int,
    request: Request,
    t: str = Query(..., description="signed playback token"),
    db: Session = Depends(get_db),
):
    """The host page for a YouTube embed, served with a real origin.

    Same token model as the stream route and for the same reason: a
    `WKWebView` issues its own requests and carries no bearer token. It exists
    server-side rather than in the app because the page has to declare an
    origin YouTube will accept, and a page the web view assembles from a string
    has none — which YouTube rejects as "video unavailable".
    """
    user_id = playback_svc.verify_token(t, canonical_id)
    if user_id is None:
        raise HTTPException(status_code=403, detail="Invalid or expired playback token")

    cc = (db.query(CanonicalContent)
          .filter(CanonicalContent.id == canonical_id).first())
    platform = (cc.platform or "").lower() if cc else ""
    if cc is None or platform not in ("youtube", "instagram") or not cc.platform_content_id:
        raise HTTPException(status_code=404, detail="Not found")

    owns = (db.query(Bookmark.id)
            .filter(Bookmark.user_id == user_id,
                    Bookmark.canonical_content_id == canonical_id)
            .first())
    if owns is None:
        raise HTTPException(status_code=404, detail="Not found")

    # Two platforms, one route: both need a page served from a real origin, and
    # both are gated by the same token and the same ownership check. The only
    # difference is which iframe goes inside.
    if platform == "instagram":
        html = playback_svc.instagram_embed_page(cc.platform_content_id,
                                                 _base_url(request))
    else:
        html = playback_svc.embed_page(cc.platform_content_id, _base_url(request))
    # No bytes of media pass through here, but the page embeds a token; keep it
    # out of shared caches.
    return HTMLResponse(html, headers={"Cache-Control": "private, no-store"})


@router.get("/api/playback/{canonical_id}/stream")
def playback_stream(
    canonical_id: int,
    t: str = Query(..., description="signed playback token"),
    range: Optional[str] = Header(default=None),
    db: Session = Depends(get_db),
):
    """Proxy the platform's media, passing `Range` through in both directions.

    `AVPlayer` opens with a small range request, reads the moov atom, then
    seeks — so forwarding `Range` and returning 206 with `Content-Range` is
    what separates real scrubbing from a progressive download that must buffer
    from zero every time the user drags the timeline.
    """
    user_id = playback_svc.verify_token(t, canonical_id)
    if user_id is None:
        raise HTTPException(status_code=403, detail="Invalid or expired playback token")

    cc = (db.query(CanonicalContent)
          .filter(CanonicalContent.id == canonical_id).first())
    if cc is None:
        raise HTTPException(status_code=404, detail="Not found")

    # The token proves who minted it; this proves they still have the save. A
    # user who deletes an item should stop being able to stream it.
    owns = (db.query(Bookmark.id)
            .filter(Bookmark.user_id == user_id,
                    Bookmark.canonical_content_id == canonical_id)
            .first())
    if owns is None:
        raise HTTPException(status_code=404, detail="Not found")

    if (cc.platform or "").lower() != "tiktok":
        raise HTTPException(status_code=400,
                            detail="This platform is not proxied")

    try:
        status, headers, body = playback_svc.stream_upstream(
            db, cc, range_header=range, user_id=user_id)
    except PlatformUnavailable as e:
        # The circuit breaker is open or the platform budget is spent. That is a
        # temporary, expected condition with a known retry time — not a server
        # fault. It was surfacing as an unhandled 500, which told the client
        # nothing and logged a traceback for a working safety mechanism.
        raise HTTPException(
            status_code=503,
            detail="This video can't be loaded right now. Try again shortly.",
            headers={"Retry-After": str(int(getattr(e, "retry_after", 60) or 60))})

    if body is None:
        raise HTTPException(status_code=502,
                            detail="Couldn't reach this video right now")

    return StreamingResponse(body, status_code=status, headers=headers,
                             media_type=headers.get("Content-Type", "video/mp4"))
