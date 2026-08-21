"""Ownership and privilege checks, in one place.

Authorization was previously written per-route, and two copies of
`_owned_bookmark` had already drifted apart in their error text. That pattern is
how a route eventually gets written without the check at all — which is exactly
what happened to the comments endpoints, and to every `/api/ops/*` route.

Everything here answers one of two questions:

  * **Does this user own this row?** — `owned_bookmark`, `owned_collection`.
  * **Is this user allowed to see the whole installation?** — `require_admin`.

Both fail with **404, not 403**. A 403 confirms that the id exists and belongs to
somebody, which is a membership oracle: an attacker can walk the id space and
learn exactly how many saves the service holds and which ids are live. 404 is
indistinguishable from "no such row" and gives them nothing.
"""
from __future__ import annotations

import os
from typing import Optional, Set

from fastapi import Depends, HTTPException
from sqlalchemy.orm import Session

from .auth import get_current_user
from .db import get_db
from .models import Bookmark, Collection

_NOT_FOUND = "Not found"


def owned_bookmark(db: Session, bookmark_id: int, user_id: int) -> Bookmark:
    """The bookmark, if this user owns it. 404 otherwise."""
    bookmark = (db.query(Bookmark)
                .filter(Bookmark.id == bookmark_id, Bookmark.user_id == user_id)
                .first())
    if bookmark is None:
        raise HTTPException(status_code=404, detail=_NOT_FOUND)
    return bookmark


def owned_collection(db: Session, collection_id: int, user_id: int) -> Collection:
    """The collection, if this user owns it. 404 otherwise."""
    collection = (db.query(Collection)
                  .filter(Collection.id == collection_id,
                          Collection.user_id == user_id)
                  .first())
    if collection is None:
        raise HTTPException(status_code=404, detail=_NOT_FOUND)
    return collection


# ─── Administration ──────────────────────────────────────────────────────────
#
# There is no admin column on `User` and inventing one would mean a migration
# plus a way to set it, which is more moving parts than this needs today. An
# environment allowlist is deployment configuration: it cannot be granted by
# anything an attacker can reach over HTTP, and it is trivially auditable.
#
# It fails closed. Unset means *nobody* is an administrator — including in
# development — so an operations endpoint left unguarded by mistake is
# unreachable rather than open to every registered user.

_ADMIN_ENV = "SAVA_ADMIN_EMAILS"


def admin_emails() -> Set[str]:
    raw = os.getenv(_ADMIN_ENV, "") or ""
    return {part.strip().lower() for part in raw.split(",") if part.strip()}


def is_admin(email: Optional[str]) -> bool:
    if not email:
        return False
    return email.strip().lower() in admin_emails()


def require_admin(current_user: dict = Depends(get_current_user)) -> dict:
    """Gate an installation-wide endpoint.

    404 rather than 403 for the same reason as above, and one more: an endpoint
    that answers 403 has told an ordinary user that a privileged surface exists
    at that path and is worth attacking. To a non-admin these routes simply are
    not there.
    """
    if not is_admin(current_user.get("email")):
        raise HTTPException(status_code=404, detail=_NOT_FOUND)
    return current_user


__all__ = ["owned_bookmark", "owned_collection", "require_admin",
           "is_admin", "admin_emails", "get_db", "get_current_user"]
