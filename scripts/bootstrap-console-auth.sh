#!/usr/bin/env bash
# Canvas Marketing OS — bootstrap-console-auth.sh
#
# One-time, human-run ops runbook script (Phase 2 of console-app.bicep's
# three-phase bootstrap — see that file's header comment). This is bash,
# not Python, unlike scripts/validate_contracts.py — deliberately, because
# this is a one-time human-run CLI runbook, not application code (the
# repo's only other scripts/ file is Python because it IS application/CI
# logic; this script is closer in spirit to a documented manual procedure,
# just made runnable and self-verifying instead of copy-pasted prose).
#
# WHAT THIS SCRIPT DOES:
#   (a) reads the console's managed identity's principalId/clientId —
#       READ-ONLY (`az identity show`), never mutates anything;
#   (b) prints the exact Entra Portal steps a human with directory admin
#       rights must perform manually (App Registration creation, redirect
#       URI, Federated Identity Credential) with REAL values interpolated
#       — READ-ONLY (`az containerapp env show`), never mutates anything;
#   (c) prints the exact `gh secret set CONSOLE_ENTRA_CLIENT_ID --env
#       cmos-dev` command template for the human to run themselves.
#
# This script NEVER creates the App Registration, NEVER adds the
# Federated Identity Credential, and NEVER sets the GitHub secret itself —
# every mutating action is a human's own manual step, printed here as a
# copy-pasteable command/instruction. Per this session's build rules, this
# script is authored and syntax-checked (`bash -n`) only — it is never
# executed by the builder/orchestrator.
#
# Prerequisite: the console-identity Bicep module (infra/modules/console/
# console-identity.bicep) must have deployed at least once via
# deploy-infra.yml, so `id-console-cmos-dev` exists to read from.

set -euo pipefail

RESOURCE_GROUP="cmos-dev"
IDENTITY_NAME="id-console-cmos-dev"
CONTAINER_APPS_ENVIRONMENT="cae-cmos-dev"
CONSOLE_APP_NAME="ca-console"
TENANT_ID="012ad0f2-8372-4425-82e4-c5e25967c3c9"

echo "=== Canvas Marketing OS — console Entra ID bootstrap ==="
echo

echo "Step (a): reading the console managed identity (read-only)..."
IDENTITY_JSON=$(az identity show -g "$RESOURCE_GROUP" -n "$IDENTITY_NAME" \
  --query "{principalId:principalId, clientId:clientId}" -o json) || {
  echo
  echo "ERROR: could not read managed identity '$IDENTITY_NAME' in resource group '$RESOURCE_GROUP'."
  echo "This identity is created by infra/modules/console/console-identity.bicep on the FIRST"
  echo "successful run of deploy-infra.yml's 'deploy' job (Phase 1 of the three-phase bootstrap —"
  echo "see infra/modules/console/console-app.bicep's header comment)."
  echo "Trigger or wait for that workflow run to complete, then re-run this script."
  exit 1
}

PRINCIPAL_ID=$(echo "$IDENTITY_JSON" | python3 -c 'import json,sys; print(json.load(sys.stdin)["principalId"])' 2>/dev/null \
  || echo "$IDENTITY_JSON" | grep -oE '"principalId": *"[^"]+"' | cut -d'"' -f4)
CLIENT_ID=$(echo "$IDENTITY_JSON" | python3 -c 'import json,sys; print(json.load(sys.stdin)["clientId"])' 2>/dev/null \
  || echo "$IDENTITY_JSON" | grep -oE '"clientId": *"[^"]+"' | cut -d'"' -f4)

echo "  principalId = $PRINCIPAL_ID"
echo "  clientId    = $CLIENT_ID (this identity's own public client id — never a secret)"
echo

echo "Step (b): resolving the Container Apps environment's default domain (read-only)..."
DEFAULT_DOMAIN=$(az containerapp env show -g "$RESOURCE_GROUP" -n "$CONTAINER_APPS_ENVIRONMENT" \
  --query "properties.defaultDomain" -o tsv) || {
  echo "ERROR: could not read Container Apps environment '$CONTAINER_APPS_ENVIRONMENT'."
  exit 1
}
REDIRECT_URI="https://${CONSOLE_APP_NAME}.${DEFAULT_DOMAIN}/.auth/login/aad/callback"
ISSUER="https://login.microsoftonline.com/${TENANT_ID}/v2.0"
FIC_AUDIENCE="api://AzureADTokenExchange"

cat <<EOF

=== Manual Entra Portal steps (perform these yourself — a human with
    directory admin rights in tenant $TENANT_ID) ===

1. In the Azure Portal, go to Microsoft Entra ID > App registrations >
   New registration.
     - Name: "Canvas Marketing OS Console"
     - Supported account types: Accounts in this organizational directory only
     - Redirect URI (Web): $REDIRECT_URI

2. Note the new App Registration's "Application (client) ID" — this is
   the value you will set as CONSOLE_ENTRA_CLIENT_ID below.

3. In the App Registration, go to Certificates & secrets >
   Federated credentials > Add credential.
     - Federated credential scenario: "Other issuer"
     - Issuer:   $ISSUER
     - Subject identifier: $PRINCIPAL_ID
     - Audience: $FIC_AUDIENCE
     - Name: fic-console-managed-identity

   This links the App Registration to the console's OWN managed identity
   (id-console-cmos-dev) — NO client secret is ever created (L-0013).

4. Set the GitHub Actions secret so the NEXT deploy-infra.yml run picks up
   the real client id (Phase 3 of the three-phase bootstrap):

     gh secret set CONSOLE_ENTRA_CLIENT_ID --env cmos-dev --body "<App Registration client id from step 2>"

5. Re-run (or wait for the next push-triggered run of) deploy-infra.yml.
   Its 'deploy' job passes this secret as the consoleClientId Bicep
   parameter, replacing Phase 1's fail-closed placeholder GUID with the
   real value — an ordinary idempotent ARM incremental update, no
   identity recreation.

EOF

echo "Bootstrap instructions printed. This script performed no mutating action."
