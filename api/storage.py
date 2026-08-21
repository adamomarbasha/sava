"""Durable object storage.

Sava keeps a small number of things for ever — thumbnails, carousel slides — and
a larger number of things for minutes — downloaded audio and video. Only the
first group belongs here.

Two rules:

  1. **Business logic never names a vendor.** Everything above this module deals
     in an opaque `storage_key` and asks for a URL when it needs one. Whether
     that key lives on a local disk or in an S3-compatible bucket is a
     deployment decision, not a code change.
  2. **No paid provider is configured by default.** The local backend is the
     default and is fully functional; the S3-compatible backend activates only
     when credentials are supplied. Nothing in this file signs Sava up for
     anything.

`boto3` is imported lazily and only by the S3 backend, so it stays an optional
dependency.
"""
from __future__ import annotations

import hashlib
import logging
import mimetypes
import os
import shutil
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Optional

from .config import API_DIR

logger = logging.getLogger(__name__)

_EXTENSIONS = {
    "image/jpeg": ".jpg", "image/jpg": ".jpg", "image/png": ".png",
    "image/webp": ".webp", "image/gif": ".gif", "image/heic": ".heic",
}


def extension_for(content_type: Optional[str], fallback: str = ".jpg") -> str:
    if not content_type:
        return fallback
    return (_EXTENSIONS.get(content_type.lower())
            or mimetypes.guess_extension(content_type)
            or fallback)


def derive_key(namespace: str, source: str, *, content_type: Optional[str] = None) -> str:
    """A stable key for a source URL.

    Deterministic on purpose: storing the same remote image twice must overwrite
    rather than accumulate, and a caller must be able to ask "do I already have
    this?" without a database round trip.
    """
    digest = hashlib.sha256(source.encode("utf-8")).hexdigest()[:24]
    return f"{namespace}/{digest}{extension_for(content_type)}"


class ObjectStorageProvider(ABC):
    """Where durable bytes live."""

    @abstractmethod
    def put(self, key: str, data: bytes, *, content_type: Optional[str] = None) -> str:
        """Store bytes under `key`. Returns the key."""

    @abstractmethod
    def exists(self, key: str) -> bool: ...

    @abstractmethod
    def url(self, key: str) -> str:
        """A URL a client can load. May be relative to the API host."""

    @abstractmethod
    def get(self, key: str) -> Optional[bytes]: ...

    @abstractmethod
    def delete(self, key: str) -> None: ...

    @property
    def name(self) -> str:
        return type(self).__name__


class LocalObjectStorage(ObjectStorageProvider):
    """Filesystem storage served by the API's own `/static` mount.

    The default, and entirely sufficient for a single-host deployment. It stops
    being sufficient the moment there is more than one API host, because host B
    cannot serve a file host A wrote — which is exactly when the S3 backend is
    switched on.
    """

    def __init__(self, root: Optional[Path] = None, public_prefix: str = "/static/objects"):
        self.root = Path(root or (API_DIR / "static" / "objects"))
        self.public_prefix = public_prefix.rstrip("/")
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        # Keys are generated internally, but treat them as untrusted anyway.
        safe = key.replace("..", "").lstrip("/")
        path = (self.root / safe).resolve()
        if not str(path).startswith(str(self.root.resolve())):
            raise ValueError(f"unsafe storage key: {key}")
        return path

    def put(self, key: str, data: bytes, *, content_type: Optional[str] = None) -> str:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return key

    def exists(self, key: str) -> bool:
        try:
            return self._path(key).is_file()
        except ValueError:
            return False

    def url(self, key: str) -> str:
        return f"{self.public_prefix}/{key}"

    def get(self, key: str) -> Optional[bytes]:
        try:
            path = self._path(key)
            return path.read_bytes() if path.is_file() else None
        except ValueError:
            return None

    def delete(self, key: str) -> None:
        try:
            path = self._path(key)
            if path.is_file():
                path.unlink()
        except (ValueError, OSError):
            pass


class S3CompatibleStorage(ObjectStorageProvider):
    """Any S3-compatible bucket — AWS S3, Cloudflare R2, MinIO, Backblaze B2.

    Written against the S3 API rather than a specific vendor, so choosing one is
    a matter of endpoint and credentials. **Not active unless configured**; no
    account is created or billed by this code existing.
    """

    def __init__(self, *, bucket: str, endpoint_url: Optional[str],
                 access_key: str, secret_key: str,
                 region: str = "auto", public_base_url: Optional[str] = None):
        import boto3  # imported lazily: optional dependency

        self.bucket = bucket
        self.public_base_url = (public_base_url or "").rstrip("/")
        self._client = boto3.client(
            "s3", endpoint_url=endpoint_url or None,
            aws_access_key_id=access_key, aws_secret_access_key=secret_key,
            region_name=region,
        )

    def put(self, key: str, data: bytes, *, content_type: Optional[str] = None) -> str:
        self._client.put_object(
            Bucket=self.bucket, Key=key, Body=data,
            ContentType=content_type or "application/octet-stream",
            CacheControl="public, max-age=31536000, immutable",
        )
        return key

    def exists(self, key: str) -> bool:
        try:
            self._client.head_object(Bucket=self.bucket, Key=key)
            return True
        except Exception:
            return False

    # How long a presigned URL stays valid. Long enough that a client which
    # fetched a library page an hour ago can still load its images; short enough
    # that a URL copied out of a proxy log stops working the same day.
    PRESIGN_SECONDS = int(os.getenv("SAVA_S3_PRESIGN_SECONDS", str(6 * 3600)))

    def url(self, key: str) -> str:
        """A URL for this object. Presigned and expiring unless told otherwise.

        `SAVA_S3_PUBLIC_BASE_URL` makes objects permanently and anonymously
        readable by anyone who learns the key, which is the correct behaviour for
        a CDN of our *own* assets and the wrong behaviour for mirrored copies of
        other people's posts. It is deliberately opt-in and namespaced: the
        public base is only honoured for keys under `public/`.
        """
        if self.public_base_url and key.startswith("public/"):
            return f"{self.public_base_url}/{key}"
        return self._client.generate_presigned_url(
            "get_object", Params={"Bucket": self.bucket, "Key": key},
            ExpiresIn=self.PRESIGN_SECONDS,
        )

    def get(self, key: str) -> Optional[bytes]:
        try:
            return self._client.get_object(Bucket=self.bucket, Key=key)["Body"].read()
        except Exception:
            return None

    def delete(self, key: str) -> None:
        try:
            self._client.delete_object(Bucket=self.bucket, Key=key)
        except Exception as e:
            logger.warning("object delete failed for %s: %s", key, e)


_provider: Optional[ObjectStorageProvider] = None


class StorageUnavailable(RuntimeError):
    """Production storage is required but not usable."""


def get_storage() -> ObjectStorageProvider:
    """The configured backend.

    Local disk in development; S3-compatible in production, with **no fallback**.

    The fallback used to exist and was the wrong kindness. If S3 was misconfigured
    or briefly unreachable at import time, this logged an error and quietly
    returned local disk — so the process kept serving, kept accepting saves, and
    wrote every mirrored thumbnail and collection cover to a container filesystem
    that is discarded on the next deploy. The failure surfaced days later as
    missing images with no event to point at.

    In production a storage problem is now a startup problem: loud, immediate,
    and attributable.
    """
    global _provider
    if _provider is not None:
        return _provider

    bucket = os.getenv("SAVA_S3_BUCKET")
    access = os.getenv("SAVA_S3_ACCESS_KEY_ID")
    secret = os.getenv("SAVA_S3_SECRET_ACCESS_KEY")
    configured = bool(bucket and access and secret)

    from .config import IS_PRODUCTION

    if configured:
        try:
            _provider = S3CompatibleStorage(
                bucket=bucket,
                endpoint_url=os.getenv("SAVA_S3_ENDPOINT_URL"),
                access_key=access, secret_key=secret,
                region=os.getenv("SAVA_S3_REGION", "auto"),
                public_base_url=os.getenv("SAVA_S3_PUBLIC_BASE_URL"),
            )
            logger.info("object storage: S3-compatible bucket %s", bucket)
            return _provider
        except Exception as e:
            if IS_PRODUCTION:
                raise StorageUnavailable(
                    f"Object storage is configured but unusable: {e}. Refusing to "
                    "fall back to local disk in production — the filesystem is "
                    "ephemeral, so every stored object would be lost silently on "
                    "the next deploy.") from e
            logger.warning("S3 configured but unusable (%s); using local disk "
                           "because this is not production", e)

    if IS_PRODUCTION:
        raise StorageUnavailable(
            "Object storage is not configured (SAVA_S3_BUCKET, "
            "SAVA_S3_ACCESS_KEY_ID, SAVA_S3_SECRET_ACCESS_KEY) and production "
            "must not write user media to local disk.")

    _provider = LocalObjectStorage()
    logger.info("object storage: local filesystem at %s", _provider.root)
    return _provider


def reset_storage() -> None:
    """Tests only."""
    global _provider
    _provider = None


def purge_temp(path: Optional[str]) -> None:
    """Delete a working directory. Source media is never retained after ingest."""
    if not path:
        return
    p = Path(path)
    target = p.parent if p.is_file() else p
    try:
        if target.exists() and "sava_" in target.name:
            shutil.rmtree(target, ignore_errors=True)
    except Exception:
        pass
