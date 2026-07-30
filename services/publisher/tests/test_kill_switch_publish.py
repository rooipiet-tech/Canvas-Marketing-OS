"""AC-12, AC-13 — kill switches block publishes, including pre-issued tokens."""

from __future__ import annotations

import base64
import time

ASSET_BYTES = b"asset subject to a kill switch"


def _publish(client, agent_run, token, function_id="publish.social_post"):
    return client.post(
        "/publish",
        json={
            "agent_run_id": str(agent_run),
            "function_id": function_id,
            "asset_bytes_b64": base64.b64encode(ASSET_BYTES).decode(),
            "gate_token": token,
        },
    )


def test_global_kill_switch_blocks_within_5s(
    client, conn, agent_run, gate_decision, make_token
) -> None:
    from app.hashing import recompute_content_hash
    from app.vault_adapter import get_vault_adapter

    content_hash = recompute_content_hash(ASSET_BYTES)

    # Baseline: publishing works before the switch is flipped.
    warm_token, _ = make_token(gate_decision_id=gate_decision, content_hash=content_hash)
    assert _publish(client, agent_run, warm_token).status_code == 200
    assert get_vault_adapter().call_count == 1

    # A token issued BEFORE the flip — still perfectly valid and unused.
    pre_issued_token, claims = make_token(
        gate_decision_id=gate_decision, content_hash=content_hash
    )

    flipped_at = time.monotonic()
    conn.execute(
        "INSERT INTO governance.kill_switches (scope, reason) VALUES ('global', 'incident 42')"
    )

    response = _publish(client, agent_run, pre_issued_token)
    elapsed = time.monotonic() - flipped_at

    assert elapsed < 5.0, f"kill switch took {elapsed:.2f}s to take effect"
    assert response.status_code == 403
    body = response.json()["detail"]
    assert body["outcome"] == "rejected"
    assert "kill_switch_active:global" in body["reason"]
    assert "incident 42" in body["reason"]

    # No extra publish happened, and the refusal is audited.
    assert get_vault_adapter().call_count == 1
    rows = conn.execute(
        "SELECT outcome, reason FROM governance.publish_attempts ORDER BY created_at, id"
    ).fetchall()
    assert rows[-1]["outcome"] == "rejected"
    assert "kill_switch_active" in rows[-1]["reason"]

    # The blocked token was not burned — the switch, not the token, is the
    # thing that expired.
    assert (
        conn.execute(
            "SELECT count(*) AS n FROM governance.jti_ledger WHERE jti = %s", (claims["jti"],)
        ).fetchone()["n"]
        == 0
    )

    # Clearing the switch restores publishing, again within 5s.
    cleared_at = time.monotonic()
    conn.execute("UPDATE governance.kill_switches SET active = false WHERE scope = 'global'")
    restored = _publish(client, agent_run, pre_issued_token)
    assert time.monotonic() - cleared_at < 5.0
    assert restored.status_code == 200


def test_per_function_kill_switch_scopes_correctly(
    client, conn, agent_run, gate_decision, make_token
) -> None:
    from app.hashing import recompute_content_hash

    content_hash = recompute_content_hash(ASSET_BYTES)

    conn.execute(
        """
        INSERT INTO governance.kill_switches (scope, function_id, reason)
        VALUES ('function', 'publish.social_post', 'brand review')
        """
    )

    blocked_token, _ = make_token(
        gate_decision_id=gate_decision,
        content_hash=content_hash,
        function_id="publish.social_post",
    )
    blocked = _publish(client, agent_run, blocked_token, "publish.social_post")
    assert blocked.status_code == 403
    assert "kill_switch_active:function:publish.social_post" in blocked.json()["detail"]["reason"]

    # A DIFFERENT function_id is entirely unaffected.
    other_token, _ = make_token(
        gate_decision_id=gate_decision,
        content_hash=content_hash,
        function_id="publish.blog_article",
    )
    allowed = _publish(client, agent_run, other_token, "publish.blog_article")
    assert allowed.status_code == 200
    assert allowed.json()["outcome"] == "published"

    # The scoping follows the function_id BOUND IN THE TOKEN, not the one
    # the caller put in the request body.
    spoof_token, _ = make_token(
        gate_decision_id=gate_decision,
        content_hash=content_hash,
        function_id="publish.social_post",
    )
    spoofed = _publish(client, agent_run, spoof_token, "publish.blog_article")
    assert spoofed.status_code == 403
    assert "kill_switch_active:function:publish.social_post" in spoofed.json()["detail"]["reason"]
