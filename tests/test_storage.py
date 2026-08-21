"""Object storage configuration.

The bug: `get_storage()` fell back to local disk whenever S3 was missing *or
threw*, logged an error, and carried on. On a host with an ephemeral filesystem
that means every mirrored thumbnail and collection cover is written to a
directory that is deleted on the next deploy — and nothing fails at the time, so
the loss surfaces days later as missing images with no event to point at.

Production must fail closed. Development must stay convenient.
"""
from __future__ import annotations

import importlib

import pytest

from api import storage as storage_module


@pytest.fixture(autouse=True)
def _reset():
    storage_module.reset_storage()
    yield
    storage_module.reset_storage()
    import api.config
    importlib.reload(api.config)


def _as_production(monkeypatch):
    import api.config
    monkeypatch.setattr(api.config, "IS_PRODUCTION", True)


def _as_development(monkeypatch):
    import api.config
    monkeypatch.setattr(api.config, "IS_PRODUCTION", False)


class TestProductionFailsClosed:
    def test_unconfigured_production_refuses(self, monkeypatch):
        _as_production(monkeypatch)
        for key in ("SAVA_S3_BUCKET", "SAVA_S3_ACCESS_KEY_ID",
                    "SAVA_S3_SECRET_ACCESS_KEY"):
            monkeypatch.delenv(key, raising=False)
        with pytest.raises(storage_module.StorageUnavailable):
            storage_module.get_storage()

    def test_broken_s3_in_production_refuses_rather_than_degrading(self, monkeypatch):
        """The important one: configured but unusable must not become local disk."""
        _as_production(monkeypatch)
        monkeypatch.setenv("SAVA_S3_BUCKET", "sava")
        monkeypatch.setenv("SAVA_S3_ACCESS_KEY_ID", "id")
        monkeypatch.setenv("SAVA_S3_SECRET_ACCESS_KEY", "secret")

        def _explode(*args, **kwargs):
            raise RuntimeError("endpoint unreachable")

        monkeypatch.setattr(storage_module, "S3CompatibleStorage", _explode)
        with pytest.raises(storage_module.StorageUnavailable):
            storage_module.get_storage()

    def test_the_error_explains_why_falling_back_is_unacceptable(self, monkeypatch):
        _as_production(monkeypatch)
        monkeypatch.delenv("SAVA_S3_BUCKET", raising=False)
        with pytest.raises(storage_module.StorageUnavailable) as e:
            storage_module.get_storage()
        assert "local disk" in str(e.value).lower()


class TestDevelopmentStaysConvenient:
    def test_development_uses_local_disk(self, monkeypatch):
        _as_development(monkeypatch)
        for key in ("SAVA_S3_BUCKET", "SAVA_S3_ACCESS_KEY_ID",
                    "SAVA_S3_SECRET_ACCESS_KEY"):
            monkeypatch.delenv(key, raising=False)
        assert isinstance(storage_module.get_storage(),
                          storage_module.LocalObjectStorage)

    def test_development_tolerates_broken_s3(self, monkeypatch):
        _as_development(monkeypatch)
        monkeypatch.setenv("SAVA_S3_BUCKET", "sava")
        monkeypatch.setenv("SAVA_S3_ACCESS_KEY_ID", "id")
        monkeypatch.setenv("SAVA_S3_SECRET_ACCESS_KEY", "secret")
        monkeypatch.setattr(storage_module, "S3CompatibleStorage",
                            lambda *a, **k: (_ for _ in ()).throw(RuntimeError("nope")))
        assert isinstance(storage_module.get_storage(),
                          storage_module.LocalObjectStorage)


class TestUserMediaIsNotPermanentlyPublic:
    def _stub_s3(self, monkeypatch, public_base=None):
        class _FakeClient:
            def generate_presigned_url(self, op, Params, ExpiresIn):
                return f"https://signed.example/{Params['Key']}?exp={ExpiresIn}"

        s3 = storage_module.S3CompatibleStorage.__new__(
            storage_module.S3CompatibleStorage)
        s3.bucket = "sava"
        s3.public_base_url = public_base
        s3._client = _FakeClient()
        return s3

    def test_private_keys_get_an_expiring_url(self, monkeypatch):
        s3 = self._stub_s3(monkeypatch, public_base="https://cdn.example")
        url = s3.url("thumbnails/abc123.jpg")
        assert url.startswith("https://signed.example/")
        assert "exp=" in url

    def test_only_the_public_namespace_uses_the_public_base(self, monkeypatch):
        s3 = self._stub_s3(monkeypatch, public_base="https://cdn.example")
        assert s3.url("public/logo.png") == "https://cdn.example/public/logo.png"

    def test_without_a_public_base_everything_is_signed(self, monkeypatch):
        s3 = self._stub_s3(monkeypatch, public_base=None)
        assert s3.url("public/logo.png").startswith("https://signed.example/")

    def test_presign_lifetime_is_bounded(self):
        assert 0 < storage_module.S3CompatibleStorage.PRESIGN_SECONDS <= 7 * 24 * 3600


class TestKeysAreNotGuessableFromAPostId:
    def test_keys_are_derived_by_digest(self):
        key = storage_module.derive_key("thumbnails",
                                        "https://cdn.instagram.com/p/ABC123/x.jpg",
                                        content_type="image/jpeg")
        assert "ABC123" not in key
        assert key.startswith("thumbnails/")

    def test_the_same_source_yields_the_same_key(self):
        a = storage_module.derive_key("thumbnails", "https://x/y.jpg")
        b = storage_module.derive_key("thumbnails", "https://x/y.jpg")
        assert a == b
