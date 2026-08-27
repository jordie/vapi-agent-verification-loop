"""Render a run + its evidence as a PR-comment-style Markdown report."""
from __future__ import annotations

from .evidence import Evidence
from .status_contract import RunRecord

BADGE = {"passed": "✅ PASSED", "failed": "❌ FAILED", "needs_review": "🟡 NEEDS REVIEW"}


def render(run: RunRecord, ev: Evidence | None) -> str:
    lines = [f"## vapi-verify · `{run.scenario_id}` · {BADGE.get(run.status, run.status)}", "",
             f"| run | `{run.run_id}` | attempt {run.attempt}/{run.max_attempts} | backend `{run.backend}` | git `{run.git_sha[:10]}` |",
             "|---|---|---|---|---|", ""]
    lines.append(f"**Outcome:** {run.outcome or run.failure_reason or '—'}  ")
    if run.failure_class != "none":
        lines.append(f"**Failure class:** `{run.failure_class}` — {run.failure_reason}  ")
    if ev:
        lines += ["", f"**Call:** `{ev.call_id or 'n/a'}` · status `{ev.call_status}` · endedReason `{ev.ended_reason}`"
                      + (f" · [recording]({ev.recording_url})" if ev.recording_url else "")]
    lines += ["", "### Assertions", "", "| # | kind | critical | result | detail |", "|---|---|---|---|---|"]
    for i, a in enumerate(run.assertions, 1):
        res = "✅" if a.passed else ("❌" if a.passed is False else "❔")
        lines.append(f"| {i} | `{a.kind}` — {a.description} | {'yes' if a.critical else 'no'} | {res} | {a.detail.replace('|', '/')[:160]} |")
    lines += ["", "### Latency (two numbers, kept apart on purpose)", ""]
    lines.append(f"- client-observed (this harness, place→ended wall clock): **{run.latency_client_observed_ms if run.latency_client_observed_ms is not None else 'n/a'} ms**")
    pr = run.latency_provider_reported or {}
    if pr:
        keys = ["turnLatencyAverage", "modelLatencyAverage", "voiceLatencyAverage", "transcriberLatencyAverage",
                "endpointingLatencyAverage", "numUserInterrupted", "numAssistantInterrupted"]
        lines.append("- provider-reported (`artifact.performanceMetrics`, verbatim): " +
                     ", ".join(f"{k}={pr[k]}" for k in keys if k in pr))
    else:
        lines.append("- provider-reported: none in artifact")
    if ev and ev.turns:
        lines += ["", "### Transcript", "", "```"]
        lines += [f"{t.role:>9}: {t.text}" for t in ev.turns]
        lines += ["```"]
    elif ev and ev.transcript:
        lines += ["", "### Transcript", "", "```", ev.transcript, "```"]
    if ev and (ev.summary or ev.success_evaluation):
        lines += ["", f"_Vapi analysis — summary: {ev.summary or '—'} · successEvaluation: `{ev.success_evaluation or '—'}` (a signal, not the gate)_"]
    lines += ["", f"_Evidence: `{run.evidence_ref}` · schema v{run.schema_version}_", ""]
    return "\n".join(lines)
