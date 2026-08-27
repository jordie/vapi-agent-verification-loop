"""The typed status contract for one verification run.

Why a contract instead of logs: a background agent's *liveness* ("it's running") is not
*delivery* ("the evidence exists and a verdict was reached"). Every consumer of a run —
the CLI, CI, the MCP server, a reviewer — reads this one schema and never parses output.

State machine
    queued -> running -> evidence_collected -> passed | failed | needs_review
    running -> retrying -> running          (only for RETRYABLE infra failures)
    running -> failed                       (semantic failures are never retried)

Failure classes
    infra     the call/eval never produced evidence (transport, provider error, timeout) -> retryable
    semantic  evidence exists and a deterministic assertion failed                       -> terminal
    judge     only the LLM judge disagreed / was unavailable                             -> needs_review
"""
from __future__ import annotations

import json
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal

SCHEMA_VERSION = "1"

Status = Literal["queued", "running", "retrying", "evidence_collected", "passed", "failed", "needs_review"]
FailureClass = Literal["none", "infra", "semantic", "judge"]

TRANSITIONS: dict[str, set[str]] = {
    "queued": {"running"},
    "running": {"evidence_collected", "retrying", "failed"},
    "retrying": {"running", "failed"},
    "evidence_collected": {"passed", "failed", "needs_review"},
    "passed": set(),
    "failed": set(),
    "needs_review": set(),
}
TERMINAL = {"passed", "failed", "needs_review"}

# endedReason values (from Vapi's Call schema) that mean "no usable evidence was produced".
# Anything not matching these prefixes is treated as a real, semantic outcome.
INFRA_ENDED_REASON_PREFIXES = (
    "call-start-error",
    "assistant-request-failed",
    "assistant-request-returned",
    "pipeline-error",
    "twilio-failed",
    "vonage-failed",
    "vapifault",
    "phone-call-provider",
    "worker-shutdown",
    "unknown-error",
    "assistant-not-found",
    "silence-timed-out",
    "call.in-progress.error",
)


class InvalidTransition(RuntimeError):
    pass


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


@dataclass
class AssertionResult:
    kind: str
    description: str
    passed: bool | None          # None = could not evaluate (e.g. judge unavailable)
    critical: bool
    detail: str = ""


@dataclass
class RunRecord:
    scenario_id: str
    backend: str
    task_id: str = "adhoc"
    git_sha: str = "unknown"
    run_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    schema_version: str = SCHEMA_VERSION
    status: str = "queued"
    attempt: int = 1
    max_attempts: int = 2
    created_at: str = field(default_factory=now_iso)
    started_at: str | None = None
    ended_at: str | None = None
    outcome: str = ""                       # human summary of the verdict
    failure_class: str = "none"
    failure_reason: str = ""
    evidence_ref: str = ""                  # path to the evidence JSON
    call_id: str = ""
    assertions: list[AssertionResult] = field(default_factory=list)
    latency_client_observed_ms: int | None = None   # wall clock around place->ended, measured by THIS harness
    latency_provider_reported: dict[str, Any] = field(default_factory=dict)  # artifact.performanceMetrics, verbatim
    history: list[dict[str, str]] = field(default_factory=list)

    # ---- transitions -------------------------------------------------------
    def transition(self, to: str, note: str = "") -> None:
        if to not in TRANSITIONS.get(self.status, set()):
            raise InvalidTransition(f"{self.status} -> {to} is not allowed")
        self.history.append({"at": now_iso(), "from": self.status, "to": to, "note": note})
        self.status = to
        if to == "running" and self.started_at is None:
            self.started_at = now_iso()
        if to in TERMINAL:
            self.ended_at = now_iso()

    def fail(self, failure_class: str, reason: str) -> None:
        self.failure_class = failure_class
        self.failure_reason = reason
        self.transition("failed", reason)

    # ---- retry policy ------------------------------------------------------
    def can_retry(self, failure_class: str) -> bool:
        """Only infra failures are retryable, and only while attempts remain."""
        return failure_class == "infra" and self.attempt < self.max_attempts

    def begin_retry(self, reason: str) -> None:
        self.transition("retrying", reason)
        self.attempt += 1
        self.transition("running", f"attempt {self.attempt}")

    # ---- verdict -----------------------------------------------------------
    def decide(self) -> str:
        """Deterministic verdict from assertion results. Rules:
        - any CRITICAL assertion failed        -> failed (semantic)
        - any assertion could not be evaluated -> needs_review (judge)
        - any non-critical assertion failed    -> needs_review (judge)
        - otherwise                            -> passed
        """
        if self.status != "evidence_collected":
            raise InvalidTransition("decide() requires status=evidence_collected")
        crit_failed = [a for a in self.assertions if a.critical and a.passed is False]
        unknown = [a for a in self.assertions if a.passed is None]
        soft_failed = [a for a in self.assertions if not a.critical and a.passed is False]
        if crit_failed:
            self.failure_class = "semantic"
            self.failure_reason = "; ".join(a.description for a in crit_failed)
            self.outcome = f"{len(crit_failed)} critical assertion(s) failed"
            self.transition("failed", self.failure_reason)
        elif unknown or soft_failed:
            self.failure_class = "judge"
            self.failure_reason = "; ".join(a.description for a in unknown + soft_failed)
            self.outcome = "deterministic checks passed; judge signal missing or negative"
            self.transition("needs_review", self.failure_reason)
        else:
            self.outcome = f"all {len(self.assertions)} assertion(s) passed"
            self.transition("passed", self.outcome)
        return self.status

    # ---- serialization -----------------------------------------------------
    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2, sort_keys=True)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "RunRecord":
        d = dict(d)
        d["assertions"] = [AssertionResult(**a) for a in d.get("assertions", [])]
        return cls(**d)


def classify_ended_reason(ended_reason: str | None) -> str:
    """Map Vapi's Call.endedReason to a failure class. Unknown/None -> infra (no evidence)."""
    if not ended_reason:
        return "infra"
    if ended_reason.startswith(INFRA_ENDED_REASON_PREFIXES):
        return "infra"
    return "none"
