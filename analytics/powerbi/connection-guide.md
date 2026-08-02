# Power BI connection guide — Canvas Marketing OS analytics

This guide documents how a human operator connects Power BI to the
nightly Fabric export produced by `analytics-ingest`. **Out of scope for
this build**: provisioning or live-refreshing an actual Power BI
workspace/dataset. `analytics-dataset.json` in this directory is a
starter dataset *definition* only — its table/column names are a subset
of `analytics/contracts/fabric-nightly-export.schema.json`'s fields, kept
internally consistent, but no live workspace has been created or
refreshed by this build.

## What gets exported

Every night, `caj-analytics-nightly-ingest`
(`infra/modules/analytics/nightly-ingest-job.bicep`) runs
`python -m analytics_ingest.cli nightly --day <yesterday>`, which ends by
uploading one JSON blob — `<day>.json`, validated against
`analytics/contracts/fabric-nightly-export.schema.json` — to the
`analytics-fabric-export` blob container on the shared storage account
(`infra/modules/analytics/blob-container.bicep`).

## Connecting Power BI via a Fabric shortcut

1. In Microsoft Fabric, create a **OneLake shortcut** targeting the
   `analytics-fabric-export` blob container on the shared storage account
   (Azure Blob Storage source type). This mounts the container's blobs
   into OneLake without copying data.
2. In Power BI Desktop or the Fabric workspace, create a new dataset that
   reads through the shortcut — either via a Lakehouse table built on top
   of the shortcut (recommended: run a simple Fabric notebook/dataflow
   that explodes each night's JSON into the 4 arrays this export always
   contains) or directly via Power Query's JSON connector pointed at the
   shortcut path.
3. Model the dataset using `analytics-dataset.json`'s 4 tables
   (`EngagementByArchetype`, `PublishingReliability`,
   `CostPerAcceptedAsset`, `VaultUtilisation`) as the starting schema —
   each table's columns map 1:1 onto the corresponding array's items in
   `fabric-nightly-export.schema.json`.
4. Set the dataset's scheduled refresh to run after
   `caj-analytics-nightly-ingest`'s nightly window (01:00 UTC / 03:00
   SAST — see `docs/analytics-nightly-scheduling.md`), leaving a buffer
   for the job's `replicaTimeout` (1800s) plus blob-propagation lag.

## Verifying the export exists before connecting

```
az storage blob list \
  --account-name <storageAccountName> \
  --container-name analytics-fabric-export \
  --auth-mode login \
  --query "[].name" -o tsv
```

Each successful nightly run adds one `<YYYY-MM-DD>.json` blob. If the
expected day's blob is missing, see
`docs/analytics-nightly-scheduling.md`'s polling-caveat section before
assuming the job failed outright — a "Running" job status lags real
completion/crash.
