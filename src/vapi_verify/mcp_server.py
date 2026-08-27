"""A minimal MCP (Model Context Protocol) server over stdio, exposing the verification loop
to a coding agent (Claude Code, Codex, etc.) as three governed tools — no shell, no arbitrary URLs:

  list_verification_runs(limit)          -> recent run records (status contract, not logs)
  get_run_evidence(run_id)               -> the evidence document for one run
  request_voice_test(scenario_id, backend)-> run a scenario; `vapi` backend only if explicitly allowed by env

Protocol: JSON-RPC 2.0, newline-delimited, methods initialize / notifications/initialized / ping /
tools/list / tools/call (MCP 2024-11-05 shape). Zero dependencies so the boundary is inspectable.
Register in Claude Code:  claude mcp add vapi-verify -- vapi-verify-mcp --out runs --scenarios scenarios
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from .run import execute, load_scenario

PROTOCOL_VERSION = "2024-11-05"


class Server:
    def __init__(self, out_dir: Path, scenarios_dir: Path, allow_real_calls: bool = False):
        self.out, self.scenarios, self.allow_real = out_dir, scenarios_dir, allow_real_calls

    # ---- tool implementations ---------------------------------------------
    def _scenario_ids(self) -> dict[str, Path]:
        return {p.stem: p for p in sorted(self.scenarios.glob("*.yaml"))}

    def list_verification_runs(self, limit: int = 20) -> list[dict[str, Any]]:
        runs = [json.loads(p.read_text()) for p in self.out.glob("*/run.json")]
        runs.sort(key=lambda d: d.get("created_at", ""), reverse=True)
        keep = ("run_id", "scenario_id", "backend", "status", "attempt", "outcome", "failure_class", "failure_reason", "created_at", "ended_at")
        return [{k: d.get(k) for k in keep} for d in runs[: max(1, min(int(limit), 100))]]

    def get_run_evidence(self, run_id: str) -> dict[str, Any]:
        if not run_id.isalnum():
            raise ValueError("run_id must be alphanumeric")   # no path games
        p = self.out / run_id / "evidence.json"
        if not p.exists():
            raise FileNotFoundError(f"no evidence for run {run_id}")
        d = json.loads(p.read_text())
        rep = self.out / run_id / "report.md"
        d["report_markdown"] = rep.read_text() if rep.exists() else ""
        return d

    def request_voice_test(self, scenario_id: str, backend: str = "mock") -> dict[str, Any]:
        ids = self._scenario_ids()
        if scenario_id not in ids:
            raise ValueError(f"unknown scenario {scenario_id!r}; allowed: {sorted(ids)}")   # allowlist
        if backend not in ("mock", "vapi"):
            raise ValueError("backend must be 'mock' or 'vapi'")
        if backend == "vapi" and not self.allow_real:
            raise PermissionError("real calls are disabled for this server (start with --allow-real-calls)")
        run = execute(load_scenario(ids[scenario_id]), backend_name=backend, out_dir=self.out, task_id="mcp")
        return {"run_id": run.run_id, "status": run.status, "outcome": run.outcome, "failure_class": run.failure_class,
                "attempt": run.attempt, "report": str(self.out / run.run_id / "report.md")}

    TOOLS = [
        {"name": "list_verification_runs", "description": "List recent verification runs (typed status contract).",
         "inputSchema": {"type": "object", "properties": {"limit": {"type": "integer", "default": 20}}}},
        {"name": "get_run_evidence", "description": "Fetch the evidence document + Markdown report for a run id.",
         "inputSchema": {"type": "object", "properties": {"run_id": {"type": "string"}}, "required": ["run_id"]}},
        {"name": "request_voice_test", "description": "Run an allowlisted scenario through a backend and return the verdict.",
         "inputSchema": {"type": "object", "properties": {"scenario_id": {"type": "string"}, "backend": {"type": "string", "enum": ["mock", "vapi"], "default": "mock"}},
                         "required": ["scenario_id"]}},
    ]

    # ---- JSON-RPC ------------------------------------------------------------
    def handle(self, msg: dict[str, Any]) -> dict[str, Any] | None:
        mid, method, params = msg.get("id"), msg.get("method", ""), msg.get("params") or {}
        if method == "initialize":
            return self._ok(mid, {"protocolVersion": PROTOCOL_VERSION, "capabilities": {"tools": {}},
                                  "serverInfo": {"name": "vapi-verify", "version": "0.1.0"}})
        if method == "notifications/initialized":
            return None
        if method == "ping":
            return self._ok(mid, {})
        if method == "tools/list":
            return self._ok(mid, {"tools": self.TOOLS})
        if method == "tools/call":
            name, args = params.get("name"), params.get("arguments") or {}
            fn = {"list_verification_runs": self.list_verification_runs, "get_run_evidence": self.get_run_evidence,
                  "request_voice_test": self.request_voice_test}.get(name)
            if not fn:
                return self._err(mid, -32601, f"unknown tool {name!r}")
            try:
                result = fn(**args)
                return self._ok(mid, {"content": [{"type": "text", "text": json.dumps(result, indent=2)}], "isError": False})
            except Exception as e:  # noqa: BLE001 — tool errors are returned to the agent, never crash the server
                return self._ok(mid, {"content": [{"type": "text", "text": f"{type(e).__name__}: {e}"}], "isError": True})
        if mid is None:
            return None
        return self._err(mid, -32601, f"method not found: {method}")

    @staticmethod
    def _ok(mid: Any, result: Any) -> dict[str, Any]:
        return {"jsonrpc": "2.0", "id": mid, "result": result}

    @staticmethod
    def _err(mid: Any, code: int, message: str) -> dict[str, Any]:
        return {"jsonrpc": "2.0", "id": mid, "error": {"code": code, "message": message}}

    def serve(self, inp=sys.stdin, out=sys.stdout) -> None:
        for line in inp:
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                out.write(json.dumps(self._err(None, -32700, "parse error")) + "\n"); out.flush(); continue
            resp = self.handle(msg)
            if resp is not None:
                out.write(json.dumps(resp) + "\n"); out.flush()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default="runs"); ap.add_argument("--scenarios", default="scenarios")
    ap.add_argument("--allow-real-calls", action="store_true", help="permit backend='vapi' (still needs VAPI_VERIFY_ALLOW_REAL_CALLS=1)")
    a = ap.parse_args(argv)
    Server(Path(a.out), Path(a.scenarios), a.allow_real_calls).serve()
    return 0


if __name__ == "__main__":
    sys.exit(main())
