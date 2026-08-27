"""Normalize what a backend returns into one evidence document.

Evidence is what a reviewer (human or agent) inspects; the run record only points at it.
Two latency numbers are kept deliberately separate and labeled:
  - client_observed_ms: wall clock around "place call" -> "call ended", measured here. Honest but coarse.
  - provider_reported:  Vapi's artifact.performanceMetrics (turn/model/voice/transcriber/endpointing
                        averages, interruption counts), copied verbatim. Never mixed with the first.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class Turn:
    role: str            # "assistant" | "user" | "system" | "tool"
    text: str
    seconds_from_start: float | None = None


@dataclass
class Evidence:
    run_id: str
    scenario_id: str
    backend: str
    call_id: str = ""
    call_status: str = ""            # Vapi Call.status  (scheduled|queued|ringing|in-progress|forwarding|ended|...)
    ended_reason: str = ""           # Vapi Call.endedReason
    transcript: str = ""             # artifact.transcript (flat text)
    turns: list[Turn] = field(default_factory=list)   # from artifact.messages
    recording_url: str = ""
    structured_outputs: dict[str, Any] = field(default_factory=dict)  # artifact.structuredOutputs + analysis.structuredData
    success_evaluation: str = ""     # analysis.successEvaluation (Vapi's own judge, one signal among others)
    summary: str = ""                # analysis.summary
    client_observed_ms: int | None = None
    provider_reported: dict[str, Any] = field(default_factory=dict)
    raw_ref: str = ""                # path to the raw provider payload, for auditability

    def assistant_text(self) -> str:
        return "\n".join(t.text for t in self.turns if t.role == "assistant") or self.transcript

    def user_text(self) -> str:
        return "\n".join(t.text for t in self.turns if t.role == "user")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def save(self, path: Path) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.to_dict(), indent=2, sort_keys=True))
        return path

    @classmethod
    def load(cls, path: Path) -> "Evidence":
        d = json.loads(path.read_text())
        d["turns"] = [Turn(**t) for t in d.get("turns", [])]
        return cls(**d)


def evidence_from_call(call: dict[str, Any], *, run_id: str, scenario_id: str, backend: str,
                       client_observed_ms: int | None, raw_ref: str = "") -> Evidence:
    """Build Evidence from a Vapi `Call` object (GET /call/{id}). Field names follow Vapi's OpenAPI schema."""
    artifact = call.get("artifact") or {}
    analysis = call.get("analysis") or {}
    turns: list[Turn] = []
    for m in artifact.get("messages") or []:
        role = m.get("role", "")
        text = m.get("message") or m.get("content") or ""
        if role in ("bot", "assistant"):
            role = "assistant"
        if role and text:
            turns.append(Turn(role=role, text=str(text), seconds_from_start=m.get("secondsFromStart")))
    structured: dict[str, Any] = {}
    if isinstance(artifact.get("structuredOutputs"), dict):
        structured.update(artifact["structuredOutputs"])
    if isinstance(analysis.get("structuredData"), dict):
        structured.update(analysis["structuredData"])
    return Evidence(
        run_id=run_id, scenario_id=scenario_id, backend=backend,
        call_id=call.get("id", ""), call_status=call.get("status", ""), ended_reason=call.get("endedReason", "") or "",
        transcript=artifact.get("transcript", "") or "", turns=turns,
        recording_url=artifact.get("recordingUrl", "") or "",
        structured_outputs=structured,
        success_evaluation=str(analysis.get("successEvaluation", "") or ""),
        summary=analysis.get("summary", "") or "",
        client_observed_ms=client_observed_ms,
        provider_reported=dict(artifact.get("performanceMetrics") or {}),
        raw_ref=raw_ref,
    )


def dig(d: dict[str, Any], path: str) -> Any:
    """'a.b.c' lookup that returns None instead of raising."""
    cur: Any = d
    for part in path.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return None
        cur = cur[part]
    return cur
