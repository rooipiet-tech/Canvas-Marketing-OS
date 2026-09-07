"""TD-14 v2 contract window — Publisher's verifier must accept a v2 gate
token (contracts/gate-token/v2/schema.json: function_id/content_hash as
first-class top-level claims, no resource claim) IN ADDITION TO v1's
resource-packed form. See app/verifier.py's module docstring for why both
must be accepted: Gatekeeper and Publisher are independent Container Apps
with independent deploy pipelines, so either could roll out first.

v1 acceptance itself stays covered by test_verify_alg_pinning.py and the
DB-backed tests using conftest's make_token fixture, unchanged — this file
is additive, exercising the new v2 and dual-support paths only.

Pure-unit: no database needed, mirrors test_verify_alg_pinning.py's own
local-keypair approach rather than conftest's DB-backed fixtures.
"""

from __future__ import annotations

import json
import time
import uuid

import jwt
import pytest
from app.verifier import REASON_TOKEN_INVALID, GateTokenVerifier, VerificationError
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

ISSUER = "cmos-gatekeeper"
AUDIENCE = "cmos-publisher"
CONTENT_HASH = "c" * 64
FUNCTION_ID = "publish.social_post"


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


def _v2_claims(**overrides) -> dict:
    now = int(time.time())
    claims = {
        "iss": ISSUER,
        "sub": str(uuid.uuid4()),
        "aud": AUDIENCE,
        "iat": now,
        "exp": now + 900,
        "jti": str(uuid.uuid4()),
        "gate_decision_id": str(uuid.uuid4()),
        "function_id": FUNCTION_ID,
        "content_hash": CONTENT_HASH,
    }
    claims.update(overrides)
    return claims


@pytest.fixture
def keypair():
    return _keypair()


@pytest.fixture
def verifier(keypair):
    _private_pem, public_pem = keypair
    return GateTokenVerifier(public_key_pem=public_pem, issuer=ISSUER, audience=AUDIENCE)


def test_accepts_a_v2_token_with_top_level_claims(keypair, verifier) -> None:
    private_pem, _ = keypair
    token = jwt.encode(_v2_claims(), private_pem, algorithm="RS256")
    claims = verifier.verify(token)
    assert "resource" not in claims
    assert verifier.bound_content_hash(claims) == CONTENT_HASH
    assert verifier.bound_function_id(claims) == FUNCTION_ID


def test_accepts_empty_content_hash_for_non_content_actions(keypair, verifier) -> None:
    """Matches app/tokens.py's content_hash=request.content_hash or ""."""
    private_pem, _ = keypair
    token = jwt.encode(_v2_claims(content_hash=""), private_pem, algorithm="RS256")
    claims = verifier.verify(token)
    assert verifier.bound_content_hash(claims) == ""


def test_rejects_a_token_with_only_one_of_the_v2_claims(keypair, verifier) -> None:
    """function_id present without content_hash (or vice versa) is not a
    valid shape under EITHER contract version — must not silently fall
    back to treating it as a v1 token.
    """
    private_pem, _ = keypair
    claims = _v2_claims()
    del claims["content_hash"]
    token = jwt.encode(claims, private_pem, algorithm="RS256")
    with pytest.raises(VerificationError) as excinfo:
        verifier.verify(token)
    assert excinfo.value.reason == REASON_TOKEN_INVALID


def test_rejects_a_token_with_neither_v1_nor_v2_binding(keypair, verifier) -> None:
    private_pem, _ = keypair
    claims = _v2_claims()
    del claims["function_id"]
    del claims["content_hash"]
    token = jwt.encode(claims, private_pem, algorithm="RS256")
    with pytest.raises(VerificationError) as excinfo:
        verifier.verify(token)
    assert excinfo.value.reason == REASON_TOKEN_INVALID


def test_v2_top_level_claims_take_precedence_over_a_stray_resource_claim(
    keypair, verifier
) -> None:
    """A token should never carry both in practice (v2 issuers don't build
    a resource claim at all), but if one somehow did, the top-level v2
    claims must win — never the packed string.
    """
    private_pem, _ = keypair
    claims = _v2_claims(
        resource=json.dumps(
            {"content_hash": "f" * 64, "function_id": "other.function"},
            sort_keys=True,
            separators=(",", ":"),
        )
    )
    token = jwt.encode(claims, private_pem, algorithm="RS256")
    verified = verifier.verify(token)
    assert verifier.bound_content_hash(verified) == CONTENT_HASH
    assert verifier.bound_function_id(verified) == FUNCTION_ID


def test_v2_token_publishes_successfully_end_to_end(
    client, conn, agent_run, gate_decision, make_v2_token, fake_vault_posts
) -> None:
    """Full real-HTTP proof, not just an isolated GateTokenVerifier call:
    a v2-shaped token (as Gatekeeper now actually issues, per
    services/gatekeeper/app/tokens.py) flows through the real /publish
    endpoint exactly the way test_publish_exactly_once.py already proves
    for a v1 token — same outcome, same exactly-once behaviour.
    """
    import base64

    from app.hashing import recompute_content_hash

    asset_bytes = b"a perfectly valid v2-token asset payload"
    content_hash = recompute_content_hash(asset_bytes)
    token, claims = make_v2_token(gate_decision_id=gate_decision, content_hash=content_hash)
    assert "resource" not in claims

    response = client.post(
        "/publish",
        json={
            "agent_run_id": str(agent_run),
            "function_id": "publish.social_post",
            "asset_bytes_b64": base64.b64encode(asset_bytes).decode(),
            "gate_token": token,
        },
    )
    assert response.status_code == 200

    body = response.json()
    assert body["outcome"] == "published"
    assert body["content_hash"] == content_hash
    assert body["jti"] == claims["jti"]

    rows = conn.execute(
        "SELECT outcome, content_hash, jti FROM governance.publish_attempts"
    ).fetchall()
    assert len(rows) == 1
    assert rows[0]["outcome"] == "published"
    assert rows[0]["content_hash"] == content_hash
    assert len(fake_vault_posts) == 1
