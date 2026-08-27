"""Replay a fixture Call for a scenario. Fixture files: fixtures/mock_calls/<scenario_id>[.<variant>].json
Variant selection: scenario['mock_variant'] or env VAPI_VERIFY_MOCK_VARIANT (e.g. 'fail', 'infra')."""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from . import InfraError

DEFAULT_DIR = Path(__file__).resolve().parents[3] / "fixtures" / "mock_calls"


class MockBackend:
    name = "mock"

    def __init__(self, fixtures_dir: Path | str | None = None, variant: str | None = None, **_: Any):
        self.dir = Path(fixtures_dir) if fixtures_dir else DEFAULT_DIR
        self.variant = variant or os.environ.get("VAPI_VERIFY_MOCK_VARIANT") or ""

    def run_scenario(self, scenario: dict[str, Any], *, attempt: int) -> dict[str, Any]:
        variant = scenario.get("mock_variant") or self.variant
        candidates = [self.dir / f"{scenario['id']}.{variant}.json"] if variant else []
        candidates.append(self.dir / f"{scenario['id']}.json")
        for c in candidates:
            if c.exists():
                call = json.loads(c.read_text())
                # An 'infra' fixture simulates a transport failure on attempt 1 and recovers on attempt 2,
                # which is exactly what the retry policy must handle.
                if call.get("_simulate_infra_failure_on_attempt") == attempt:
                    raise InfraError(f"simulated provider failure on attempt {attempt}")
                return call
        raise InfraError(f"no mock fixture for scenario {scenario['id']!r} in {self.dir}")
