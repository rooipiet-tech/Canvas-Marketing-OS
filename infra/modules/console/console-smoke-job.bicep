// Canvas Marketing OS — infra/modules/console/console-smoke-job.bicep
//
// caj-console-smoke: the in-VNet reachability check for ca-console.
// ca-console's ingress is external but the check runs INSIDE the
// environment, so it proves the app answers on its own network rather
// than only through whatever the runner can reach.
//
// WHY THIS MODULE EXISTS AT ALL (F-CONSOLE-SMOKE-CLI-ARGS).
// deploy-console.yml used to create this job inline on the CLI:
//
//     az containerapp job create ... --command /bin/sh --args '-c' "$SMOKE_CMD"
//
// az's argparse reads a value beginning with '-' as an option, so '-c'
// was never passed as an argument -- every invocation died with
// `ERROR: unrecognized arguments: -c ...`, on BOTH the create and the
// `job update` fallback. deploy-console has therefore FAILED all eight of
// its runs since 31 July. The image build and `az containerapp update`
// succeeded each time, so ca-console did deploy; only this verification
// step was broken, which is why a permanently red workflow went
// unexamined for a month.
//
// deploy-console.yml was the ONLY workflow in this repo passing
// `--command`/`--args` on the CLI -- the sole violator of a rule the repo
// had already written down. deploy-governance.yml states it directly:
// "Bare `job start`, no CLI container-argument override (`--yaml`,
// `--env-vars`, `--command`, `--args`) -- the job's real command/env is
// baked into its Bicep-persisted template at deploy time... This
// sidesteps the CLI override bug class entirely (L-0022: any
// container-argument flag replaces the whole container spec; L-0023:
// `--yaml` overrides can be silently ignored)."
//
// So the fix is not to out-argue argparse. It is to do what the eight
// working smoke jobs already do: declare the command as a Bicep array,
// where no shell and no argument parser sits between the intent and the
// container spec, and let the workflow issue a bare `job start`.
//
// The URL arrives as an env var rather than being interpolated into the
// command string, so the command is a fixed literal and the only thing
// that varies per environment is data.

@description('Azure region.')
param location string = resourceGroup().location

@description('Container Apps Job name.')
param jobName string = 'caj-console-smoke'

@description('Resource id of the Container Apps managed environment (cae-cmos-dev).')
param environmentId string

@description('ca-console\'s live ingress FQDN (consoleApp.outputs.fqdn) — never a hardcoded hostname (L-0025).')
param consoleFqdn string

resource consoleSmokeJob 'Microsoft.App/jobs@2024-03-01' = {
  name: jobName
  location: location
  tags: {
    purpose: 'in-VNet reachability check for ca-console'
  }
  properties: {
    environmentId: environmentId
    configuration: {
      triggerType: 'Manual'
      replicaTimeout: 120
      replicaRetryLimit: 1
      manualTriggerConfig: {
        replicaCompletionCount: 1
        parallelism: 1
      }
    }
    template: {
      containers: [
        {
          name: 'console-smoke'
          image: 'curlimages/curl:latest'
          // 401 or 302 is the PASS condition: ca-console sits behind Entra
          // auth, so an unauthenticated request being challenged or
          // redirected is proof the app is up and enforcing auth. A 200
          // here would mean the console was answering anonymously, which
          // is a finding rather than a success.
          command: [
            'sh'
            '-c'
            'STATUS=$(curl -s -o /dev/null -w "%{http_code}" "$CONSOLE_URL"); echo "HTTP_STATUS=$STATUS"; [ "$STATUS" = "401" ] || [ "$STATUS" = "302" ] || exit 1'
          ]
          env: [
            {
              name: 'CONSOLE_URL'
              value: 'https://${consoleFqdn}/'
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

output jobName string = consoleSmokeJob.name
