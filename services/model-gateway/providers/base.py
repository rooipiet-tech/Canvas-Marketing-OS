"""The gateway's single provider extension point.

Adding an upstream model vendor means adding one module in this package and
registering it by name (see providers/registry.py) — no router, dispatcher,
or request-handling module changes. The interface is dependency-injectable,
so tests substitute a stub implementation without any network call.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass
class ProviderResult:
    """Normalized completion result, independent of any vendor wire format."""

    content: str
    input_tokens: int
    output_tokens: int
    # Additive, optional (F-EMPTY-COMPLETION-VISIBILITY, 7 Aug 2026, round
    # 24) -- the vendor's own reason the response ended (e.g. Anthropic's
    # "end_turn"/"max_tokens"/"stop_sequence"/"tool_use"/"refusal"). Every
    # adapter should populate it when the vendor's response carries one; it
    # defaults to None so a stub Provider in an existing test (or a future
    # adapter that genuinely has no such concept) doesn't have to set it.
    # Threaded through to completion.py's response body and its structured
    # log line specifically so an empty `content` (previously indistinguishable
    # from any other empty response -- see completion.py's own note) carries
    # a reason instead of being a dead end for whoever has to debug it next.
    stop_reason: str | None = None


@runtime_checkable
class Provider(Protocol):
    """One method. That is the whole contract an adapter must satisfy."""

    async def complete(
        self,
        *,
        provider_model: str,
        messages: list[dict],
        max_tokens: int,
        temperature: float,
        tools: list[dict] | None,
    ) -> ProviderResult: ...
