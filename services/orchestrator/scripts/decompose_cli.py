#!/usr/bin/env python3
"""AGENT-NATIVE decomposition CLI (AC-008, AC-031).

Invocable directly from a shell/CLI entrypoint without a running server or
live Service Bus — prints the ordered task batch as a JSON array to
stdout, so an agent can drive loop decomposition programmatically.

Usage:
    python scripts/decompose_cli.py --loop loops/daily-signal-loop.yaml \\
        --heartbeat tests/golden/daily-signal-loop.heartbeat.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from orchestrator import decompose  # noqa: E402
from orchestrator.loop_loader import load_loop  # noqa: E402
from orchestrator.models import HeartbeatEvent  # noqa: E402


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Decompose a loop definition against a heartbeat event and print "
            "the resulting task batch as JSON."
        )
    )
    parser.add_argument(
        "--loop",
        required=True,
        help="Path to a loop-definition YAML file (e.g. loops/daily-signal-loop.yaml).",
    )
    parser.add_argument(
        "--heartbeat",
        required=True,
        help=(
            "Path to a heartbeat-event JSON file matching "
            "contracts/orchestrator/heartbeat-event.schema.json."
        ),
    )
    parser.add_argument(
        "--format",
        choices=["json"],
        default="json",
        help="Output format (only 'json' is currently supported).",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    loop = load_loop(args.loop)
    heartbeat_raw = json.loads(Path(args.heartbeat).read_text(encoding="utf-8"))
    heartbeat = HeartbeatEvent.model_validate(heartbeat_raw)

    tasks = decompose.decompose(loop, heartbeat)
    print(json.dumps(tasks, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
