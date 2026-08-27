"""Backends produce a Vapi-shaped `Call` dict for a scenario. Two are shipped:
  mock  — replays a fixture (CI-safe, no credentials, no minutes)
  vapi  — places a real outbound call through api.vapi.ai and polls it to `ended`
The harness only ever sees the Call dict; that is the contract.
"""
from __future__ import annotations

from typing import Any, Protocol


class InfraError(RuntimeError):
    """The backend could not produce evidence (transport/provider/timeout). Retryable."""


class Backend(Protocol):
    name: str
    def run_scenario(self, scenario: dict[str, Any], *, attempt: int) -> dict[str, Any]: ...


def get_backend(name: str, **kw: Any) -> Backend:
    if name == "mock":
        from .mock import MockBackend
        return MockBackend(**kw)
    if name == "vapi":
        from .vapi import VapiBackend
        return VapiBackend(**kw)
    raise ValueError(f"unknown backend {name!r}")
