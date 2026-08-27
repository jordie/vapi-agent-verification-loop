"""CLI: run one scenario through a backend, collect evidence, decide, write run + report.

    vapi-verify run --scenario scenarios/happy_path.yaml [--backend mock|vapi] [--task-id T] [--out runs/]
    vapi-verify list [--out runs/]
    vapi-verify show <run_id> [--out runs/]

Exit code: 0 passed · 1 failed · 2 needs_review · 3 harness error.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import yaml

from .backends import InfraError, get_backend
from .evidence import evidence_from_call
from .judge import evaluate
from .report import render
from .status_contract import RunRecord, classify_ended_reason

EXIT = {"passed": 0, "failed": 1, "needs_review": 2}


def git_sha() -> str:
    try:
        return subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True, timeout=5).stdout.strip() or "unknown"
    except Exception:
        return "unknown"


def load_scenario(path: Path) -> dict[str, Any]:
    sc = yaml.safe_load(path.read_text())
    for k in ("id", "assertions"):
        if k not in sc:
            raise ValueError(f"scenario {path} missing {k!r}")
    return sc


def execute(scenario: dict[str, Any], *, backend_name: str, out_dir: Path, task_id: str = "adhoc",
            judge=None, backend=None, max_attempts: int = 2) -> RunRecord:
    """The loop. Every state change goes through RunRecord.transition so it is auditable."""
    run = RunRecord(scenario_id=scenario["id"], backend=backend_name, task_id=task_id, git_sha=git_sha(), max_attempts=max_attempts)
    run_dir = out_dir / run.run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    run.transition("running", "start")
    backend = backend or get_backend(backend_name)
    while True:
        t0 = time.monotonic()
        try:
            call = backend.run_scenario(scenario, attempt=run.attempt)
        except InfraError as e:
            if run.can_retry("infra"):
                run.begin_retry(str(e)); continue
            run.fail("infra", str(e)); break
        client_ms = call.pop("_client_observed_ms", None) or int((time.monotonic() - t0) * 1000)
        raw_path = run_dir / f"raw_call_attempt{run.attempt}.json"
        raw_path.write_text(json.dumps(call, indent=2, sort_keys=True))
        fclass = classify_ended_reason(call.get("endedReason"))
        if fclass == "infra":   # includes calls that *ended* because the provider pipeline failed — no usable evidence
            reason = f"no evidence: status={call.get('status')} endedReason={call.get('endedReason')}"
            if run.can_retry("infra"):
                run.begin_retry(reason); continue
            run.fail("infra", reason); break
        ev = evidence_from_call(call, run_id=run.run_id, scenario_id=scenario["id"], backend=backend_name,
                                client_observed_ms=client_ms, raw_ref=str(raw_path))
        ev_path = ev.save(run_dir / "evidence.json")
        run.evidence_ref, run.call_id = str(ev_path), ev.call_id
        run.latency_client_observed_ms, run.latency_provider_reported = client_ms, ev.provider_reported
        run.transition("evidence_collected", f"evidence at {ev_path.name}")
        run.assertions = evaluate(scenario["assertions"], ev, judge)
        run.decide()
        (run_dir / "report.md").write_text(render(run, ev))
        break
    (run_dir / "run.json").write_text(run.to_json())
    return run


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="vapi-verify", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run"); r.add_argument("--scenario", required=True); r.add_argument("--backend", default="mock")
    r.add_argument("--task-id", default="adhoc"); r.add_argument("--out", default="runs"); r.add_argument("--max-attempts", type=int, default=2)
    r.add_argument("--print-report", action="store_true")
    l = sub.add_parser("list"); l.add_argument("--out", default="runs")
    s = sub.add_parser("show"); s.add_argument("run_id"); s.add_argument("--out", default="runs")
    a = ap.parse_args(argv)
    out = Path(a.out)
    if a.cmd == "run":
        try:
            run = execute(load_scenario(Path(a.scenario)), backend_name=a.backend, out_dir=out, task_id=a.task_id, max_attempts=a.max_attempts)
        except Exception as e:  # noqa: BLE001
            print(json.dumps({"ok": False, "error": f"{type(e).__name__}: {e}"})); return 3
        print(json.dumps({"run_id": run.run_id, "status": run.status, "outcome": run.outcome, "failure_class": run.failure_class,
                          "attempt": run.attempt, "report": str(out / run.run_id / "report.md")}))
        if a.print_report and (out / run.run_id / "report.md").exists():
            print((out / run.run_id / "report.md").read_text())
        return EXIT.get(run.status, 1)
    if a.cmd == "list":
        for p in sorted(out.glob("*/run.json")):
            d = json.loads(p.read_text()); print(f"{d['run_id']}  {d['status']:<14} {d['scenario_id']:<22} {d['backend']:<5} attempt {d['attempt']}  {d.get('outcome') or d.get('failure_reason','')}")
        return 0
    if a.cmd == "show":
        p = out / a.run_id / "report.md"
        if not p.exists():
            print(f"no run {a.run_id}"); return 3
        print(p.read_text()); return 0
    return 3


if __name__ == "__main__":
    sys.exit(main())
