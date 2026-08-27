"""Assertions over evidence. Deterministic checks are the gate; the LLM judge is a signal.

Assertion kinds (scenario YAML `assertions:` entries)
  transcript_regex     pattern must match assistant text (or user text with role: user)
  forbidden_phrase     pattern must NOT match assistant text
  structured_output    dotted path in structured outputs equals a value
  latency_max          provider-reported metric (e.g. turnLatencyAverage) <= max_ms
  max_interruptions    provider-reported numUserInterrupted <= max
  ended_reason_in      Call.endedReason must be one of the listed values
  ai_rubric            an LLM judge answers PASS/FAIL against a rubric — never critical

`critical` defaults to True for deterministic kinds and is forced False for ai_rubric.
A critical failure => run failed. A non-critical failure or an unevaluable check => needs_review.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
from typing import Any, Callable, Protocol

from .evidence import Evidence, dig
from .status_contract import AssertionResult

DETERMINISTIC = {"transcript_regex", "forbidden_phrase", "structured_output", "latency_max",
                 "max_interruptions", "ended_reason_in"}


class AIJudge(Protocol):
    name: str
    def __call__(self, rubric: str, evidence: Evidence) -> tuple[bool | None, str]: ...


class NullJudge:
    """No LLM configured: returns None so the run lands in needs_review instead of a fake pass."""
    name = "none"
    def __call__(self, rubric: str, evidence: Evidence) -> tuple[bool | None, str]:
        return None, "no AI judge configured (set VAPI_VERIFY_JUDGE=claude-cli or pass a judge)"


class ClaudeCliJudge:
    """Uses the local `claude` CLI (`claude -p`) — a subscription-backed judge with no API key.
    Output contract: the model must end with a line `VERDICT: PASS` or `VERDICT: FAIL`."""
    name = "claude-cli"

    def __init__(self, binary: str = "claude", timeout_s: int = 120):
        self.binary, self.timeout_s = binary, timeout_s

    def __call__(self, rubric: str, evidence: Evidence) -> tuple[bool | None, str]:
        if not shutil.which(self.binary):
            return None, f"{self.binary} not on PATH"
        prompt = (
            "You are grading a phone conversation transcript between an AI assistant and a caller.\n"
            f"RUBRIC:\n{rubric}\n\nTRANSCRIPT:\n{evidence.transcript or evidence.assistant_text()}\n\n"
            "Answer with one short paragraph of reasoning, then a final line exactly `VERDICT: PASS` or `VERDICT: FAIL`."
        )
        try:
            out = subprocess.run([self.binary, "-p", prompt], capture_output=True, text=True, timeout=self.timeout_s)
        except (subprocess.TimeoutExpired, OSError) as e:
            return None, f"judge error: {e}"
        text = (out.stdout or "").strip()
        m = re.search(r"VERDICT:\s*(PASS|FAIL)", text, re.I)
        if not m:
            return None, f"judge gave no verdict: {text[-200:]}"
        return m.group(1).upper() == "PASS", text[-600:]


class StaticJudge:
    """Test double: fixed verdict."""
    name = "static"
    def __init__(self, verdict: bool | None, detail: str = "static"):
        self.verdict, self.detail = verdict, detail
    def __call__(self, rubric: str, evidence: Evidence) -> tuple[bool | None, str]:
        return self.verdict, self.detail


def default_judge() -> AIJudge:
    return ClaudeCliJudge() if os.environ.get("VAPI_VERIFY_JUDGE") == "claude-cli" else NullJudge()


def evaluate(assertions: list[dict[str, Any]], evidence: Evidence, judge: AIJudge | None = None) -> list[AssertionResult]:
    judge = judge or default_judge()
    results: list[AssertionResult] = []
    for a in assertions:
        kind = a.get("type", "")
        critical = bool(a.get("critical", True)) if kind in DETERMINISTIC else False
        desc = a.get("description") or kind
        try:
            passed, detail = _check(kind, a, evidence, judge)
        except Exception as e:  # a broken assertion must not crash the run; it becomes unevaluable
            passed, detail = None, f"assertion error: {e}"
        results.append(AssertionResult(kind=kind, description=desc, passed=passed, critical=critical, detail=detail))
    return results


def _check(kind: str, a: dict[str, Any], ev: Evidence, judge: AIJudge) -> tuple[bool | None, str]:
    if kind == "transcript_regex":
        text = ev.user_text() if a.get("role") == "user" else ev.assistant_text()
        m = re.search(a["pattern"], text, re.I | re.S)
        return bool(m), (f"matched: {m.group(0)[:80]!r}" if m else "no match")
    if kind == "forbidden_phrase":
        m = re.search(a["pattern"], ev.assistant_text(), re.I | re.S)
        return not m, (f"found forbidden: {m.group(0)[:80]!r}" if m else "absent")
    if kind == "structured_output":
        val = dig(ev.structured_outputs, a["path"])
        return val == a.get("equals"), f"{a['path']}={val!r} (expected {a.get('equals')!r})"
    if kind == "latency_max":
        metric = a.get("metric", "turnLatencyAverage")
        val = ev.provider_reported.get(metric)
        if val is None:
            return None, f"provider did not report {metric}"
        return float(val) <= float(a["max_ms"]), f"{metric}={val} ms (max {a['max_ms']})"
    if kind == "max_interruptions":
        val = ev.provider_reported.get("numUserInterrupted")
        if val is None:
            return None, "provider did not report numUserInterrupted"
        return int(val) <= int(a["max"]), f"numUserInterrupted={val} (max {a['max']})"
    if kind == "ended_reason_in":
        ok = ev.ended_reason in a["values"]
        return ok, f"endedReason={ev.ended_reason!r}"
    if kind == "ai_rubric":
        verdict, detail = judge(a["rubric"], ev)
        return verdict, f"[{judge.name}] {detail}"
    raise ValueError(f"unknown assertion type {kind!r}")
