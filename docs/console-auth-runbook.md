# Console Entra ID auth — bootstrap runbook

The console sits behind Azure Container Apps built-in authentication
(Easy Auth) with a Microsoft Entra ID identity provider, made fully
secretless via a Federated Identity Credential (FIC) bound to the
console's own managed identity (`id-console-cmos-dev`) — see `L-0013` in
`.compound/learnings/security/`.

Creating the Entra App Registration and its FIC requires directory admin
rights that this repo's CI identity (`cmos-github-actions`) does not have
and should not be granted (see `.loop/domain.md`'s `C-4` finding: zero
Graph API permissions, zero directory role membership). This is therefore
a one-time **manual** step, not a pipeline step (`AUTH-003`).

The App Registration and its Federated Identity Credential must be
created in this platform's own Entra tenant,
`012ad0f2-8372-4425-82e4-c5e25967c3c9` (the same tenant id
`scripts/bootstrap-console-auth.sh` resolves and prints) — not any other
directory the operator running this runbook may have access to.

## Run the script

```
./scripts/bootstrap-console-auth.sh
```

The script reads the console's managed identity (read-only `az identity
show`), reads the Container Apps environment's default domain (read-only
`az containerapp env show`), and prints the exact Entra Portal steps with
real values already interpolated — no placeholders to hand-substitute.
It performs no mutating action itself; every Azure/Entra change it
describes is a command or Portal action **you** run yourself.

## The three-phase bootstrap

1. **Phase 1** — the first `deploy-infra.yml` run creates
   `id-console-cmos-dev` (a genuine Bicep resource, never a lookup — see
   `infra/modules/console/console-identity.bicep`) and the console's
   `authConfigs` resource with a fail-closed placeholder `consoleClientId`
   (an invalid GUID) until the real secret is set. Logins fail closed
   against the placeholder — strictly *more* restrictive than no auth at
   all, so unauthenticated-rejection holds trivially during this window.
2. **Phase 2** — run `scripts/bootstrap-console-auth.sh`, follow its
   printed Entra Portal steps (App Registration + redirect URI + FIC), and
   run the `gh secret set CONSOLE_ENTRA_CLIENT_ID --env cmos-dev` command
   it prints.
3. **Phase 3** — the next `deploy-infra.yml` run passes the real secret as
   the `consoleClientId` Bicep parameter — an ordinary idempotent ARM
   incremental update, no identity recreation, no downtime beyond a normal
   revision update.

See `infra/modules/console/console-app.bicep`'s header comment for the
full technical detail (schema verification sources, the
`override-use-mi-fic-assertion-client-id` sentinel mechanism) behind this
design.
