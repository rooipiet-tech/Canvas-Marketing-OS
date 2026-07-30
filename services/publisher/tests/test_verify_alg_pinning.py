"""AC-21 — Publisher pins RS256 and rejects alg:none / algorithm confusion.

Pure-unit: no database needed.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
import uuid

import jwt
import pytest
from app.verifier import (
    REASON_INVALID_ALG,
    REASON_TOKEN_EXPIRED,
    GateTokenVerifier,
    VerificationError,
)
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

ISSUER = "cmos-gatekeeper"
AUDIENCE = "cmos-publisher"


def _keypair() -> tuple[str, str]:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode()
    public_pem = (
        key.public_key()
        .public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        .decode()
    )
    return private_pem, public_pem


def _claims(**overrides) -> dict:
    now = int(time.time())
    claims = {
        "iss": ISSUER,
        "sub": str(uuid.uuid4()),
        "aud": AUDIENCE,
        "iat": now,
        "exp": now + 900,
        "jti": str(uuid.uuid4()),
        "gate_decision_id": str(uuid.uuid4()),
        "resource": json.dumps(
            {"content_hash": "a" * 64, "function_id": "publish.social_post"},
            sort_keys=True,
            separators=(",", ":"),
        ),
    }
    claims.update(overrides)
    return claims


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


@pytest.fixture
def keypair():
    return _keypair()


@pytest.fixture
def verifier(keypair):
    _private_pem, public_pem = keypair
    return GateTokenVerifier(public_key_pem=public_pem, issuer=ISSUER, audience=AUDIENCE)


def test_accepts_a_genuine_rs256_token(keypair, verifier) -> None:
    private_pem, _ = keypair
    token = jwt.encode(_claims(), private_pem, algorithm="RS256")
    claims = verifier.verify(token)
    assert claims["iss"] == ISSUER
    assert verifier.allowed_algorithms == ("RS256",)
    assert verifier.bound_content_hash(claims) == "a" * 64
    assert verifier.bound_function_id(claims) == "publish.social_post"


def test_rejects_alg_none(verifier) -> None:
    header = _b64url(json.dumps({"alg": "none", "typ": "JWT"}).encode())
    payload = _b64url(json.dumps(_claims()).encode())
    unsigned = f"{header}.{payload}."

    with pytest.raises(VerificationError) as excinfo:
        verifier.verify(unsigned)
    assert excinfo.value.reason == REASON_INVALID_ALG

    # Upper/mixed-case spellings must not slip past either.
    for spelling in ("NONE", "None", "nOnE"):
        header = _b64url(json.dumps({"alg": spelling, "typ": "JWT"}).encode())
        with pytest.raises(VerificationError) as excinfo:
            verifier.verify(f"{header}.{payload}.")
        assert excinfo.value.reason == REASON_INVALID_ALG


def test_rejects_alg_confusion_hs256(keypair, verifier) -> None:
    """Classic confusion: sign HS256 using the RSA PUBLIC PEM as the secret.

    Assembled by hand because PyJWT refuses to *encode* an HMAC token with
    a PEM key — an attacker has no such scruples, so the verifier, not the
    encoder, has to be the thing that says no.
    """
    _private_pem, public_pem = keypair
    header = _b64url(json.dumps({"alg": "HS256", "typ": "JWT"}).encode())
    payload = _b64url(json.dumps(_claims()).encode())
    signing_input = f"{header}.{payload}".encode("ascii")
    signature = hmac.new(public_pem.encode(), signing_input, hashlib.sha256).digest()
    forged = f"{header}.{payload}.{_b64url(signature)}"

    with pytest.raises(VerificationError) as excinfo:
        verifier.verify(forged)
    assert excinfo.value.reason == REASON_INVALID_ALG

    # The reason is distinct from all four GOAL refusal reasons.
    assert excinfo.value.reason not in {
        "token_absent",
        "token_expired",
        "content_hash_mismatch",
        "token_replayed",
    }


def test_rejects_expired_and_wrong_key(keypair, verifier) -> None:
    private_pem, _ = keypair
    now = int(time.time())
    expired = jwt.encode(
        _claims(iat=now - 7200, exp=now - 3600), private_pem, algorithm="RS256"
    )
    with pytest.raises(VerificationError) as excinfo:
        verifier.verify(expired)
    assert excinfo.value.reason == REASON_TOKEN_EXPIRED

    other_private, _ = _keypair()
    foreign = jwt.encode(_claims(), other_private, algorithm="RS256")
    with pytest.raises(VerificationError) as excinfo:
        verifier.verify(foreign)
    assert excinfo.value.reason == "token_invalid"
