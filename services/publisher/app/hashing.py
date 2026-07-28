"""Content hashing (AC-09).

Publisher INDEPENDENTLY RECOMPUTES the content hash over the raw asset
bytes it is about to publish. It never trusts:

  * a caller-supplied hash in the publish request, nor
  * assets.content_hash — which the frozen schema declares as nullable
    `text` with no format constraint or CHECK, so it may be absent,
    stale or written by an entirely different code path (C-6).

The only hash Publisher compares against is the one bound into the gate
token's canonical-JSON `resource` claim at approval time. One changed
byte anywhere in the asset changes the recomputed digest and the publish
is refused with `content_hash_mismatch`.
"""

from __future__ import annotations

import hashlib

HASH_ALGORITHM = "sha256"


def sha256_hex(data: bytes) -> str:
    """Lowercase hex SHA-256 of the exact bytes supplied."""
    if not isinstance(data, (bytes, bytearray, memoryview)):
        raise TypeError("content hashing operates on raw bytes, not text")
    return hashlib.sha256(bytes(data)).hexdigest()


def recompute_content_hash(asset_bytes: bytes) -> str:
    """The one function Publisher uses to derive a content hash."""
    return sha256_hex(asset_bytes)


def hashes_match(expected: str | None, actual: str) -> bool:
    """Constant-time-ish, case-insensitive comparison of two hex digests."""
    if not expected:
        return False
    import hmac

    return hmac.compare_digest(expected.strip().lower(), actual.strip().lower())
