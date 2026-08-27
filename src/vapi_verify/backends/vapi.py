"""Real backend: place an outbound call via Vapi's REST API and poll it until it ends.

Endpoints and field names follow api.vapi.ai's OpenAPI spec (Aug 2026):
  POST /call            body: {assistantId, phoneNumberId, customer:{number}, name, metadata}
  GET  /call/{id}       -> Call {status, endedReason, startedAt, endedAt, artifact{...}, analysis{...}}
  Auth: Authorization: Bearer <VAPI_API_KEY>

The *other* end of the call (the "tester") is out of scope here on purpose: point
VAPI_VERIFY_TEST_NUMBER at a Vapi Voice Test Suite tester, a second assistant, or a human.
Nothing in this module is invoked unless VAPI_VERIFY_ALLOW_REAL_CALLS=1 — real calls cost minutes.
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from typing import Any

from . import InfraError

BASE = os.environ.get("VAPI_BASE_URL", "https://api.vapi.ai")
TERMINAL_STATUSES = {"ended", "not-found", "deletion-failed"}


class VapiBackend:
    name = "vapi"

    def __init__(self, api_key: str | None = None, assistant_id: str | None = None,
                 phone_number_id: str | None = None, test_number: str | None = None,
                 poll_s: float = 5.0, timeout_s: float = 900.0, **_: Any):
        self.api_key = api_key or os.environ.get("VAPI_API_KEY", "")
        self.assistant_id = assistant_id or os.environ.get("VAPI_ASSISTANT_ID", "")
        self.phone_number_id = phone_number_id or os.environ.get("VAPI_PHONE_NUMBER_ID", "")
        self.test_number = test_number or os.environ.get("VAPI_VERIFY_TEST_NUMBER", "")
        self.poll_s, self.timeout_s = poll_s, timeout_s
        missing = [k for k, v in {"VAPI_API_KEY": self.api_key, "VAPI_ASSISTANT_ID": self.assistant_id,
                                  "VAPI_PHONE_NUMBER_ID": self.phone_number_id, "VAPI_VERIFY_TEST_NUMBER": self.test_number}.items() if not v]
        if missing:
            raise InfraError(f"vapi backend not configured: missing {missing}")
        if os.environ.get("VAPI_VERIFY_ALLOW_REAL_CALLS") != "1":
            raise InfraError("refusing to place a real call: set VAPI_VERIFY_ALLOW_REAL_CALLS=1 explicitly")

    # ---- http --------------------------------------------------------------
    def _req(self, method: str, path: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(f"{BASE}{path}", data=data, method=method,
                                     headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.loads(r.read().decode() or "{}")
        except urllib.error.HTTPError as e:
            detail = e.read().decode(errors="ignore")[:300]
            if e.code >= 500 or e.code == 429:
                raise InfraError(f"vapi {method} {path} -> {e.code}: {detail}") from e
            raise RuntimeError(f"vapi {method} {path} -> {e.code}: {detail}") from e   # 4xx = our bug, not retryable
        except (urllib.error.URLError, TimeoutError) as e:
            raise InfraError(f"vapi {method} {path} transport error: {e}") from e

    # ---- the loop ----------------------------------------------------------
    def run_scenario(self, scenario: dict[str, Any], *, attempt: int) -> dict[str, Any]:
        body = {
            "assistantId": self.assistant_id,
            "phoneNumberId": self.phone_number_id,
            "customer": {"number": self.test_number, "name": f"vapi-verify tester ({scenario['id']})"},
            "name": f"vapi-verify:{scenario['id']}:attempt{attempt}",
            "metadata": {"harness": "vapi-agent-verification-loop", "scenario_id": scenario["id"], "attempt": attempt},
        }
        if scenario.get("assistant_overrides"):
            body["assistantOverrides"] = scenario["assistant_overrides"]
        created = self._req("POST", "/call", body)
        call_id = created.get("id")
        if not call_id:
            raise InfraError(f"POST /call returned no id: {created}")
        t0 = time.monotonic()
        while True:
            call = self._req("GET", f"/call/{call_id}")
            if call.get("status") in TERMINAL_STATUSES:
                call["_client_observed_ms"] = int((time.monotonic() - t0) * 1000)
                return call
            if time.monotonic() - t0 > self.timeout_s:
                raise InfraError(f"call {call_id} did not end within {self.timeout_s}s (status={call.get('status')})")
            time.sleep(self.poll_s)
