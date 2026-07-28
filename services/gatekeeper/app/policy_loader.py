"""Load and validate the autonomy policy (policy/autonomy.yaml).

Levels 0..4 are documented in policy/README.md. A malformed entry raises
at load time — the service must never start with a half-understood policy
and then fail open on the first request.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

from app.config import policy_path

MIN_LEVEL = 0
MAX_LEVEL = 4

# Fail-closed: an unmapped (function_id, action_class) is blocked.
FALLBACK_DEFAULT_LEVEL = 0

REQUIRED_ENTRY_KEYS = ("function_id", "action_class", "level")

LEVEL_DESCRIPTIONS: dict[int, str] = {
    0: "blocked always — no approval can unblock it",
    1: "approval-required (single approver)",
    2: "approval-required (elevated; same single-approver mechanism this session)",
    3: "auto-approved-and-audited (no human in the loop)",
    4: "fully-autonomous passthrough (no human in the loop, logged)",
}


class PolicyError(ValueError):
    """Raised when autonomy.yaml is missing, malformed or self-contradictory."""


@dataclass(frozen=True)
class PolicyEntry:
    function_id: str
    action_class: str
    level: int
    description: str | None = None


@dataclass(frozen=True)
class AutonomyPolicy:
    version: int
    default_level: int
    entries: tuple[PolicyEntry, ...]

    def level_for(self, function_id: str, action_class: str) -> int:
        for entry in self.entries:
            if entry.function_id == function_id and entry.action_class == action_class:
                return entry.level
        return self.default_level

    def entry_for(self, function_id: str, action_class: str) -> PolicyEntry | None:
        for entry in self.entries:
            if entry.function_id == function_id and entry.action_class == action_class:
                return entry
        return None


def _validate_level(raw_level: object, where: str) -> int:
    if isinstance(raw_level, bool) or not isinstance(raw_level, int):
        raise PolicyError(f"{where}: level must be an integer 0-4, got {raw_level!r}")
    if raw_level < MIN_LEVEL or raw_level > MAX_LEVEL:
        raise PolicyError(f"{where}: level must be between 0 and 4, got {raw_level!r}")
    return raw_level


def load_policy(path: str | Path | None = None) -> AutonomyPolicy:
    resolved = Path(path) if path is not None else policy_path()
    if not resolved.exists():
        raise PolicyError(f"autonomy policy not found at {resolved}")

    document = yaml.safe_load(resolved.read_text(encoding="utf-8"))
    if not isinstance(document, dict):
        raise PolicyError(f"{resolved}: top level must be a mapping")

    if "version" not in document:
        raise PolicyError(f"{resolved}: missing required top-level key 'version'")
    version = document["version"]
    if isinstance(version, bool) or not isinstance(version, int):
        raise PolicyError(f"{resolved}: 'version' must be an integer, got {version!r}")

    default_level = _validate_level(
        document.get("default_level", FALLBACK_DEFAULT_LEVEL),
        f"{resolved}: default_level",
    )

    raw_entries = document.get("entries")
    if not isinstance(raw_entries, list) or not raw_entries:
        raise PolicyError(f"{resolved}: 'entries' must be a non-empty list")

    entries: list[PolicyEntry] = []
    seen: set[tuple[str, str]] = set()
    for index, raw in enumerate(raw_entries):
        where = f"{resolved}: entries[{index}]"
        if not isinstance(raw, dict):
            raise PolicyError(f"{where}: must be a mapping, got {type(raw).__name__}")

        missing = [key for key in REQUIRED_ENTRY_KEYS if key not in raw]
        if missing:
            raise PolicyError(f"{where}: missing required key(s) {', '.join(sorted(missing))}")

        function_id = raw["function_id"]
        action_class = raw["action_class"]
        if not isinstance(function_id, str) or not function_id:
            raise PolicyError(f"{where}: function_id must be a non-empty string")
        if not isinstance(action_class, str) or not action_class:
            raise PolicyError(f"{where}: action_class must be a non-empty string")

        level = _validate_level(raw["level"], where)

        key = (function_id, action_class)
        if key in seen:
            raise PolicyError(f"{where}: duplicate entry for {function_id}/{action_class}")
        seen.add(key)

        description = raw.get("description")
        if description is not None and not isinstance(description, str):
            raise PolicyError(f"{where}: description must be a string when present")

        unknown = set(raw) - set(REQUIRED_ENTRY_KEYS) - {"description"}
        if unknown:
            raise PolicyError(f"{where}: unknown key(s) {', '.join(sorted(unknown))}")

        entries.append(
            PolicyEntry(
                function_id=function_id,
                action_class=action_class,
                level=level,
                description=description,
            )
        )

    return AutonomyPolicy(version=version, default_level=default_level, entries=tuple(entries))


_CACHED_POLICY: AutonomyPolicy | None = None


def get_policy(*, reload: bool = False) -> AutonomyPolicy:
    """Startup-loaded policy.

    Cached deliberately: unlike kill switches (which must be an uncached
    live read on every decision), the autonomy policy is a build-time
    artefact shipped in the bundle and only changes on redeploy.
    """
    global _CACHED_POLICY
    if reload or _CACHED_POLICY is None:
        _CACHED_POLICY = load_policy()
    return _CACHED_POLICY
