"""App Store transaction verification.

The only module allowed to grant the `pro` entitlement.

StoreKit 2 hands the app a `Transaction` whose `jsonRepresentation` is a **JWS**
— a JSON Web Signature that Apple produced with a private key, carrying a
certificate chain up to Apple's own root. That signature is the entire security
model: it means the claims inside (product id, expiry, original transaction id)
came from Apple and not from whoever is holding the phone.

So verification is exactly three questions, in this order:

  1. Does the certificate chain in the JWS header terminate at Apple's root CA?
  2. Does the signature over the payload validate against the leaf certificate?
  3. Do the claims name a product we sell, for the bundle we shipped, and not
     yet expired?

A client that posts `{"is_pro": true}` fails at step 1, which is why the client
never gets to say what plan it is on. It posts evidence; the server decides.

── What is and is not finished ─────────────────────────────────────────────

Steps 2 and 3 are implemented here in full. Step 1 needs Apple's root
certificate and, for the server-to-server refresh path, an App Store Connect API
key — neither of which exists yet. Rather than pretend, the module is explicit:

  * `verify_signed_transaction()` performs the chain check when
    `APPLE_ROOT_CA_PATH` is configured, and **refuses** when it is not and the
    environment is production. There is no configuration in which production
    silently accepts an unverified signature.
  * Outside production, `SAVA_STOREKIT_LOCAL_TESTING=1` accepts a locally-signed
    Xcode StoreKit-configuration transaction so the purchase flow can be
    developed and tested end to end. Those grants are stamped
    `verification="local_testing"` in the database forever, so they can never be
    mistaken for real ones.

`docs/PRICING.md` lists the exact App Store Connect steps that remain.
"""
from __future__ import annotations

import base64
import json
import logging
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from .config import ENVIRONMENT, IS_PRODUCTION

logger = logging.getLogger(__name__)


# ─── Products ────────────────────────────────────────────────────────────────
#
# The bundle identifier is `com.sava.mobile`, so the product identifiers follow
# it. App Store Connect does not require a product id to share the app's prefix,
# but every tool that lists them sorts alphabetically, and an `app.sava.*` id
# under a `com.sava.mobile` app is the sort of inconsistency that costs somebody
# twenty minutes a year forever.
#
# Both products grant the same entitlement. The plan is `pro` either way — the
# only difference is how often Apple charges.

BUNDLE_ID = os.getenv("SAVA_BUNDLE_ID", "com.sava.mobile")

PRO_MONTHLY = os.getenv("SAVA_PRODUCT_PRO_MONTHLY", "com.sava.mobile.pro.monthly")
PRO_ANNUAL = os.getenv("SAVA_PRODUCT_PRO_ANNUAL", "com.sava.mobile.pro.annual")

#: product id -> plan. Anything not in here grants nothing.
PRODUCT_PLANS: Dict[str, str] = {
    PRO_MONTHLY: "pro",
    PRO_ANNUAL: "pro",
}

#: Subscription group. Both products must share one so Apple treats monthly and
#: annual as upgrade/downgrade of the same thing rather than two subscriptions.
SUBSCRIPTION_GROUP = os.getenv("SAVA_SUBSCRIPTION_GROUP", "Sava Pro")

APPLE_ROOT_CA_PATH = os.getenv("SAVA_APPLE_ROOT_CA_PATH")

LOCAL_TESTING = (
    os.getenv("SAVA_STOREKIT_LOCAL_TESTING", "").lower() in ("1", "true", "yes")
    and not IS_PRODUCTION
)


class VerificationError(ValueError):
    """The transaction is not acceptable. The message is safe to return."""


@dataclass(frozen=True)
class VerifiedTransaction:
    """A transaction Apple signed, decoded into what we act on."""

    product_id: str
    plan: str
    original_transaction_id: str
    transaction_id: str
    purchased_at: Optional[datetime]
    expires_at: Optional[datetime]
    environment: str
    revoked: bool
    revocation_reason: Optional[str]
    auto_renew: bool
    #: "apple_jws" for a real verified transaction, "local_testing" otherwise.
    verification: str
    claims: Dict[str, Any]

    @property
    def is_expired(self) -> bool:
        return self.expires_at is not None and self.expires_at <= datetime.now(timezone.utc)


# ─── JWS handling ────────────────────────────────────────────────────────────

def _b64url(segment: str) -> bytes:
    """Decode a base64url segment, restoring the padding JWS omits."""
    return base64.urlsafe_b64decode(segment + "=" * (-len(segment) % 4))


def _split_jws(jws: str) -> tuple:
    parts = (jws or "").strip().split(".")
    if len(parts) != 3:
        raise VerificationError("Malformed transaction: expected a JWS with three parts.")
    return parts[0], parts[1], parts[2]


def decode_claims(jws: str) -> Dict[str, Any]:
    """The payload, **without** checking the signature.

    Never sufficient on its own. Split out because the claims are needed both by
    the verified path and by diagnostics, and because keeping the unsafe decode
    in one clearly-named function makes its call sites auditable.
    """
    _, payload, _ = _split_jws(jws)
    try:
        return json.loads(_b64url(payload))
    except Exception as e:
        raise VerificationError(f"Transaction payload is not readable: {e}")


def _certificate_chain(header_segment: str) -> List[bytes]:
    try:
        header = json.loads(_b64url(header_segment))
    except Exception as e:
        raise VerificationError(f"Transaction header is not readable: {e}")
    chain = header.get("x5c") or []
    if not chain:
        raise VerificationError("Transaction carries no certificate chain.")
    return [base64.b64decode(c) for c in chain]


def _verify_signature(jws: str) -> Dict[str, Any]:
    """Full cryptographic verification against Apple's root.

    Raises `VerificationError` for anything that does not check out, including
    the root certificate not being configured — in production, an unverifiable
    transaction and an unconfigured server are the same answer: no entitlement.
    """
    header_segment, payload_segment, signature_segment = _split_jws(jws)

    if not APPLE_ROOT_CA_PATH:
        raise VerificationError(
            "App Store verification is not configured on this server "
            "(SAVA_APPLE_ROOT_CA_PATH). Refusing to grant Pro.")

    try:
        from cryptography import x509
        from cryptography.hazmat.primitives.asymmetric import ec
        from cryptography.hazmat.primitives.asymmetric.utils import (
            decode_dss_signature, encode_dss_signature)
        from cryptography.hazmat.primitives import hashes
    except ImportError as e:  # pragma: no cover - dependency is declared
        raise VerificationError(f"Verification support is unavailable: {e}")

    chain = _certificate_chain(header_segment)
    certs = [x509.load_der_x509_certificate(der) for der in chain]
    leaf = certs[0]

    # ── 1. Chain terminates at Apple's root ──────────────────────────────────
    try:
        with open(APPLE_ROOT_CA_PATH, "rb") as fh:
            root_bytes = fh.read()
        root = (x509.load_pem_x509_certificate(root_bytes)
                if b"-----BEGIN" in root_bytes
                else x509.load_der_x509_certificate(root_bytes))
    except Exception as e:
        raise VerificationError(f"Apple root certificate could not be loaded: {e}")

    if certs[-1].fingerprint(hashes.SHA256()) != root.fingerprint(hashes.SHA256()):
        raise VerificationError("Transaction chain does not terminate at Apple's root.")

    now = datetime.now(timezone.utc)
    for index in range(len(certs) - 1):
        child, parent = certs[index], certs[index + 1]
        if not (child.not_valid_before_utc <= now <= child.not_valid_after_utc):
            raise VerificationError("A certificate in the chain is not currently valid.")
        try:
            parent.public_key().verify(
                child.signature, child.tbs_certificate_bytes,
                ec.ECDSA(child.signature_hash_algorithm))
        except Exception:
            raise VerificationError("Transaction certificate chain is not intact.")

    # ── 2. Signature over the payload ────────────────────────────────────────
    signing_input = f"{header_segment}.{payload_segment}".encode("ascii")
    raw_signature = _b64url(signature_segment)
    # JWS ES256 signatures are raw r||s; `cryptography` wants DER.
    half = len(raw_signature) // 2
    der_signature = encode_dss_signature(
        int.from_bytes(raw_signature[:half], "big"),
        int.from_bytes(raw_signature[half:], "big"))
    try:
        leaf.public_key().verify(der_signature, signing_input, ec.ECDSA(hashes.SHA256()))
    except Exception:
        raise VerificationError("Transaction signature is not valid.")

    return json.loads(_b64url(payload_segment))


# ─── Claim interpretation ────────────────────────────────────────────────────

def _millis(value: Any) -> Optional[datetime]:
    """Apple sends timestamps as milliseconds since the epoch."""
    if value in (None, "", 0):
        return None
    try:
        return datetime.fromtimestamp(int(value) / 1000.0, tz=timezone.utc)
    except (TypeError, ValueError, OSError):
        return None


def _interpret(claims: Dict[str, Any], *, verification: str) -> VerifiedTransaction:
    product_id = (claims.get("productId") or "").strip()
    plan = PRODUCT_PLANS.get(product_id)
    if plan is None:
        raise VerificationError(
            f"“{product_id or 'unknown'}” is not a Sava product.")

    bundle = (claims.get("bundleId") or "").strip()
    if bundle and bundle != BUNDLE_ID:
        raise VerificationError(
            f"Transaction is for {bundle}, not this app.")

    original = str(claims.get("originalTransactionId") or "").strip()
    if not original:
        raise VerificationError("Transaction has no original transaction id.")

    environment = (claims.get("environment")
                   or ("LocalTesting" if verification == "local_testing" else "Production"))

    # A Sandbox transaction must never grant Pro in production. StoreKit
    # sandbox accounts are freely creatable, so accepting one would make the
    # subscription optional for anybody willing to read a blog post.
    if IS_PRODUCTION and environment != "Production":
        raise VerificationError(
            f"This is a {environment} purchase and cannot be used here.")

    revocation = _millis(claims.get("revocationDate"))
    return VerifiedTransaction(
        product_id=product_id,
        plan=plan,
        original_transaction_id=original,
        transaction_id=str(claims.get("transactionId") or original),
        purchased_at=_millis(claims.get("purchaseDate")
                             or claims.get("originalPurchaseDate")),
        expires_at=_millis(claims.get("expiresDate")),
        environment=environment,
        revoked=revocation is not None,
        revocation_reason=(str(claims.get("revocationReason"))
                           if claims.get("revocationReason") is not None else None),
        # StoreKit's Transaction does not carry the renewal flag; it lives on
        # the renewal info. Absent means "we do not know", and the honest
        # default for an unknown renewal is False — the client refreshes it from
        # `Product.SubscriptionInfo.RenewalState` anyway.
        auto_renew=bool(claims.get("autoRenewStatus", 0)),
        verification=verification,
        claims=claims,
    )


def verify_signed_transaction(jws: str) -> VerifiedTransaction:
    """Verify a StoreKit 2 signed transaction and return what it entitles.

    This is the single door. Everything about a user's plan enters the system
    through it.
    """
    if not (jws or "").strip():
        raise VerificationError("No transaction was supplied.")

    if LOCAL_TESTING:
        # Xcode's StoreKit configuration signs with a local test certificate
        # that does not chain to Apple's root, so the chain check cannot pass
        # and is not meant to. Claims are still parsed and validated — a local
        # test of a product we do not sell still fails.
        logger.warning(
            "StoreKit LOCAL TESTING is enabled (ENVIRONMENT=%s): accepting an "
            "unverified transaction. This is refused in production.", ENVIRONMENT)
        return _interpret(decode_claims(jws), verification="local_testing")

    return _interpret(_verify_signature(jws), verification="apple_jws")


def describe_configuration() -> dict:
    """What this server can currently do. Read by `/health` and the ops path."""
    return {
        "bundle_id": BUNDLE_ID,
        "products": sorted(PRODUCT_PLANS),
        "subscription_group": SUBSCRIPTION_GROUP,
        "apple_root_configured": bool(APPLE_ROOT_CA_PATH),
        "local_testing_enabled": LOCAL_TESTING,
        "can_verify_production_purchases": bool(APPLE_ROOT_CA_PATH),
    }
