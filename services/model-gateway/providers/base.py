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
