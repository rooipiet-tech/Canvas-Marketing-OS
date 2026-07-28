// Canvas Marketing OS — governance-smoke-test-job.bicep (AC-35)
//
// caj-governance-smoke: a one-shot Container Apps Job that exercises the
// FULL gate cycle end-to-end against the DEPLOYED services, from inside
// the VNet, using the REAL Key Vault signing key:
//
//   step 0  seed the FK chain   -> campaigns -> agent_runs, PLUS an
//                                  already-approved
//                                  governance.approval_inbox row for the
//                                  exact (agent_run_id, function_id,
//                                  content_hash) triple it is about to
//                                  gate-check (see RISK-01 note below)
//   step 1  create a decision   -> POST ca-gatekeeper /gate-check, which
//                                  signs the gate token via
//                                  CryptographyClient against
//                                  kv-cmos-dev (no local test signer:
//                                  ca-gatekeeper runs with
//                                  SIGNER_BACKEND=keyvault)
//   step 2  verify it           -> POST ca-publisher /publish with the
//                                  real asset bytes; the deployed
//                                  verifier checks alg/exp/signature and
//                                  recomputes the content hash
//   step 3  replay the jti      -> POST the IDENTICAL token again
//   step 4  assert rejection    -> the second attempt must come back
//                                  'token_replayed'
//
// The job's exit code encodes pass/fail of all four steps, including the
// replay-rejection assertion. deploy-governance.yml starts it and polls
// the execution to Succeeded.
//
// RETRY/BACKOFF: the first /gate-check is also the first
// CryptographyClient call against a private-endpoint-only vault — cold
// container start, managed-identity token acquisition and private DNS
// resolution all land on that one request. It is wrapped in a BOUNDED
// exponential backoff (5 attempts, 8+16+32+64 = 120s of sleep, ~2min)
// with distinguishable SMOKE-RETRY log lines. Every other step runs once:
// a retry there would mask a genuine governance defect.
//
// RISK-01 — WHY THIS JOB USES A REAL LEVEL-1 FUNCTION_ID:
// This job used to gate-check a dedicated `smoke.governance_cycle`
// function_id pinned at autonomy level 4 in services/gatekeeper/policy/
// autonomy.yaml. /gate-check is agent-native (AC-17) and authenticates no
// caller, so that entry was a standing auto-approve-without-authorization
// path any internal caller could drive. The entry is gone. The job now
// uses the genuine level-1 `publish.social_post` entry and pre-seeds its
// own already-approved approval_inbox row over the SAME Postgres admin
// connection it already uses to seed the FK chain — so it bypasses only
// the human Teams/link click, never the policy's approval gate, and the
// capability requires database write credentials rather than knowledge of
// a magic function_id. The decision reason is asserted to be
// `level_1_approved_by_human`, which makes the smoke test STRICTLY
// stronger than before: it now proves the level-1 approval lookup works
// end to end in addition to sign/verify/replay.
//
// The seeded row is inserted with link_consumed_at already set, so its
// approval link can never be clicked or re-used by anything else.
//
// Consequence, accepted deliberately: an ACTIVE kill switch scoped to
// publish.social_post (or a global one) now fails this job. That is the
// correct loud behaviour — a deploy-time governance smoke test should not
// report green while an operator is holding the emergency brake down.
//
// The smoke script is base64-encoded before becoming a job secret, for
// the same reason migration-job.bicep does it: Container Apps collapses a
// literal "$$" in a secret value to "$" (L-0012), and the base64 alphabet
// contains no "$" at all.

@description('Azure region.')
param location string = resourceGroup().location

@description('Container Apps Job name.')
param jobName string = 'caj-governance-smoke'

@description('Resource id of the Container Apps managed environment (cae-cmos-dev).')
param environmentId string

@description('Internal FQDN of the deployed ca-gatekeeper app.')
param gatekeeperFqdn string

@description('Internal FQDN of the deployed ca-publisher app.')
param publisherFqdn string

@description('Postgres server fully-qualified domain name.')
param postgresFqdn string

@description('Postgres administrator login.')
param administratorLogin string

@secure()
@description('Postgres administrator login password.')
param administratorLoginPassword string

@description('Postgres database name.')
param databaseName string = 'postgres'

var databaseUrl = 'postgresql://${administratorLogin}:${administratorLoginPassword}@${postgresFqdn}:5432/${databaseName}?sslmode=require'

var smokeScript = '''
import base64
import hashlib
import os
import sys
import time
import uuid

import httpx
import psycopg

GATEKEEPER_URL = os.environ['GATEKEEPER_URL'].rstrip('/')
PUBLISHER_URL = os.environ['PUBLISHER_URL'].rstrip('/')
DATABASE_URL = os.environ['DATABASE_URL']

FUNCTION_ID = 'publish.social_post'
ACTION_CLASS = 'publish'
AUTONOMY_LEVEL = 1
EXPECTED_REASON = 'level_1_approved_by_human'
PRESEED_DECIDED_BY = 'caj-governance-smoke:preseeded-approval'

MAX_ATTEMPTS = 5
BASE_BACKOFF_SECONDS = 8
REQUEST_TIMEOUT_SECONDS = 30.0


def log(line):
    print(line, flush=True)


def fail(step, detail):
    log('SMOKE-FAIL step=' + str(step) + ' detail=' + str(detail))
    sys.exit(1)


def seed_fk_chain_and_approval(content_hash):
    """Seed campaigns -> agent_runs, plus a pre-APPROVED inbox row.

    The pre-seeded row stands in for the human Teams/link click ONLY. The
    autonomy policy still evaluates publish.social_post at level 1 and
    still refuses to issue a token without a matching approved row, so no
    standing auto-approve entry has to exist in autonomy.yaml (RISK-01).
    """
    with psycopg.connect(DATABASE_URL, autocommit=True) as conn:
        campaign_id = conn.execute(
            'INSERT INTO campaigns (name) VALUES (%s) RETURNING id',
            ('governance-smoke-' + uuid.uuid4().hex[:8],),
        ).fetchone()[0]
        agent_run_id = conn.execute(
            'INSERT INTO agent_runs (campaign_id, agent_name) VALUES (%s, %s) RETURNING id',
            (campaign_id, 'caj-governance-smoke'),
        ).fetchone()[0]
        conn.execute(
            'INSERT INTO governance.approval_inbox ('
            ' agent_run_id, function_id, action_class, level, content_hash,'
            ' preview_title, preview_reference, evidence_summary, status,'
            ' link_token, link_consumed_at, decided_by, decided_at, expires_at'
            ') VALUES ('
            ' %s, %s, %s, %s, %s,'
            ' %s, %s, %s, %s,'
            ' %s, now(), %s, now(), now() + interval '
            "'30 minutes'"
            ')',
            (
                agent_run_id,
                FUNCTION_ID,
                ACTION_CLASS,
                AUTONOMY_LEVEL,
                content_hash,
                'governance smoke test',
                'smoke://governance-cycle',
                'Deploy-time smoke test: approval pre-seeded in place of the human click.',
                'approved',
                'smoke-preseed-' + uuid.uuid4().hex,
                PRESEED_DECIDED_BY,
            ),
        )
    return str(agent_run_id)


def gate_check_with_retry(agent_run_id, content_hash):
    payload = {
        'agent_run_id': agent_run_id,
        'function_id': FUNCTION_ID,
        'action_class': ACTION_CLASS,
        'content_hash': content_hash,
        'preview_title': 'governance smoke test',
        'preview_reference': 'smoke://governance-cycle',
        'evidence_summary': 'Deploy-time smoke test of the full gate cycle.',
    }
    last = 'no attempt made'
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            response = httpx.post(
                GATEKEEPER_URL + '/gate-check',
                json=payload,
                timeout=REQUEST_TIMEOUT_SECONDS,
            )
            if response.status_code == 200:
                log('SMOKE-STEP-1-OK gate-check succeeded on attempt ' + str(attempt))
                return response.json()
            last = 'status=' + str(response.status_code) + ' body=' + response.text[:400]
        except Exception as exc:
            last = repr(exc)
        if attempt < MAX_ATTEMPTS:
            delay = BASE_BACKOFF_SECONDS * (2 ** (attempt - 1))
            log(
                'SMOKE-RETRY step=gate-check attempt=' + str(attempt) + '/'
                + str(MAX_ATTEMPTS) + ' sleeping=' + str(delay) + 's last=' + str(last)
            )
            time.sleep(delay)
    fail('gate-check', last)


def publish(agent_run_id, asset_b64, token):
    return httpx.post(
        PUBLISHER_URL + '/publish',
        json={
            'agent_run_id': agent_run_id,
            'function_id': FUNCTION_ID,
            'asset_bytes_b64': asset_b64,
            'gate_token': token,
        },
        timeout=REQUEST_TIMEOUT_SECONDS,
    )


def main():
    log('SMOKE-BEGIN governance gate cycle against ' + GATEKEEPER_URL + ' and ' + PUBLISHER_URL)

    asset_bytes = ('governance-smoke-' + uuid.uuid4().hex).encode('utf-8')
    asset_b64 = base64.b64encode(asset_bytes).decode('ascii')
    content_hash = hashlib.sha256(asset_bytes).hexdigest()

    agent_run_id = seed_fk_chain_and_approval(content_hash)
    log('SMOKE-STEP-0-OK seeded agent_run ' + agent_run_id + ' and its approved inbox row')

    # Step 1 - create a decision and sign a gate token with the REAL
    # Key Vault key (the deployed Gatekeeper runs SIGNER_BACKEND=keyvault).
    # FUNCTION_ID is a genuine level-1 (approval-required) policy entry:
    # the token is issued ONLY because the pre-seeded approval row above
    # satisfies the policy's own approval gate (RISK-01).
    decision = gate_check_with_retry(agent_run_id, content_hash)
    if decision.get('outcome') != 'approved':
        fail('gate-check-outcome', decision)
    if decision.get('level') != AUTONOMY_LEVEL:
        fail('gate-check-level', decision)
    if decision.get('reason') != EXPECTED_REASON:
        fail('gate-check-reason', decision)
    token = decision.get('gate_token')
    if not token:
        fail('gate-token-missing', decision)
    log('SMOKE-STEP-1-OK decision ' + str(decision.get('decision_id')) + ' issued a gate token')

    # Step 2 - verify it via the deployed Publisher's real verifier.
    first = publish(agent_run_id, asset_b64, token)
    if first.status_code != 200:
        fail('publish', 'status=' + str(first.status_code) + ' body=' + first.text[:400])
    first_body = first.json()
    if first_body.get('outcome') != 'published':
        fail('publish-outcome', first_body)
    log('SMOKE-STEP-2-OK publish attempt ' + str(first_body.get('attempt_id')) + ' published')

    # Step 3 + 4 - replay the identical jti and assert the rejection.
    second = publish(agent_run_id, asset_b64, token)
    log('SMOKE-STEP-3-OK replayed the identical gate token')
    if second.status_code != 403:
        fail('replay-status', 'expected 403, got ' + str(second.status_code))
    detail = second.json().get('detail', {})
    if detail.get('reason') != 'token_replayed':
        fail('replay-reason', detail)
    log('SMOKE-STEP-4-OK replay rejected with reason token_replayed')

    log('SMOKE-PASS full gate cycle verified end to end')
    return 0


if __name__ == '__main__':
    sys.exit(main())
'''

var smokeScriptBase64 = base64(smokeScript)

resource governanceSmokeTestJob 'Microsoft.App/jobs@2024-03-01' = {
  name: jobName
  location: location
  tags: {
    purpose: 'one-shot live governance gate-cycle smoke test'
  }
  properties: {
    environmentId: environmentId
    configuration: {
      triggerType: 'Manual'
      replicaTimeout: 900
      // Zero retries on purpose: the retry budget lives inside the script
      // (bounded backoff around the first CryptographyClient call). A
      // job-level retry would re-run the replay assertion with a fresh
      // token and could mask a genuine governance defect.
      replicaRetryLimit: 0
      manualTriggerConfig: {
        replicaCompletionCount: 1
        parallelism: 1
      }
      secrets: [
        {
          name: 'db-connection-string'
          value: databaseUrl
        }
        {
          name: 'smoke-script-b64'
          value: smokeScriptBase64
        }
      ]
    }
    template: {
      containers: [
        {
          name: 'governance-smoke'
          image: 'python:3.12-slim'
          command: [
            'sh'
            '-c'
            'set -eu; printf "%s" "$SMOKE_SCRIPT_B64" | base64 -d > /tmp/smoke.py; pip install --no-cache-dir --disable-pip-version-check httpx "psycopg[binary]"; python /tmp/smoke.py'
          ]
          env: [
            {
              name: 'SMOKE_SCRIPT_B64'
              secretRef: 'smoke-script-b64'
            }
            {
              name: 'DATABASE_URL'
              secretRef: 'db-connection-string'
            }
            {
              name: 'GATEKEEPER_URL'
              value: 'https://${gatekeeperFqdn}'
            }
            {
              name: 'PUBLISHER_URL'
              value: 'https://${publisherFqdn}'
            }
          ]
          resources: {
            cpu: json('0.5')
            memory: '1Gi'
          }
        }
      ]
    }
  }
}

output jobName string = governanceSmokeTestJob.name
output jobId string = governanceSmokeTestJob.id
