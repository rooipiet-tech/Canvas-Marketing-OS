"""AC-18 — the starter Power BI dataset's declared columns are a subset of the Fabric export schema contract's fields."""

from __future__ import annotations

import json

from analytics_ingest.config import contracts_dir

_ANALYTICS_DIR = contracts_dir().parent


def test_dataset_columns_subset_of_export_schema():
    dataset_path = _ANALYTICS_DIR / "powerbi" / "analytics-dataset.json"
    schema_path = _ANALYTICS_DIR / "contracts" / "fabric-nightly-export.schema.json"

    dataset = json.loads(dataset_path.read_text(encoding="utf-8"))
    schema = json.loads(schema_path.read_text(encoding="utf-8"))

    all_schema_field_names: set[str] = set()
    for array_name in (
        "engagement_by_archetype",
        "publishing_reliability",
        "cost_per_accepted_asset",
        "vault_utilisation",
    ):
        item_schema = schema["properties"][array_name]["items"]
        all_schema_field_names.update(item_schema["properties"].keys())

    for table in dataset["tables"]:
        for column in table["columns"]:
            assert column["name"] in all_schema_field_names, (
                f"{table['name']}.{column['name']} is not a field in any "
                "fabric-nightly-export.schema.json item schema"
            )
