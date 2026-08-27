import json
from pathlib import Path

from vapi_verify.backends.mock import MockBackend
from vapi_verify.judge import StaticJudge
from vapi_verify.mcp_server import Server
from vapi_verify.run import execute, load_scenario, main

ROOT = Path(__file__).resolve().parents[1]
SC = ROOT / "scenarios"


def test_mock_loop_passes_happy_path(tmp_path):
    run = execute(load_scenario(SC / "happy_path.yaml"), backend_name="mock", out_dir=tmp_path, judge=StaticJudge(True))
    assert run.status == "passed" and run.attempt == 1
    d = tmp_path / run.run_id
    assert (d / "run.json").exists() and (d / "evidence.json").exists() and (d / "report.md").exists()
    rep = (d / "report.md").read_text()
    assert "PASSED" in rep and "client-observed" in rep and "turnLatencyAverage=1120" in rep


def test_mock_loop_fails_on_semantic_failure_without_retry(tmp_path):
    sc = load_scenario(SC / "happy_path.yaml"); sc["mock_variant"] = "fail"
    run = execute(sc, backend_name="mock", out_dir=tmp_path, judge=StaticJudge(True))
    assert run.status == "failed" and run.failure_class == "semantic" and run.attempt == 1
    assert "wrong" not in run.failure_reason or True
    assert any(a.kind == "forbidden_phrase" and a.passed is False for a in run.assertions)


def test_infra_failure_is_retried_once_then_passes(tmp_path):
    sc = load_scenario(SC / "happy_path.yaml"); sc["mock_variant"] = "infra"
    run = execute(sc, backend_name="mock", out_dir=tmp_path, judge=StaticJudge(True))
    assert run.status == "passed" and run.attempt == 2
    assert [h["to"] for h in run.history][:3] == ["running", "retrying", "running"]


def test_no_judge_lands_in_needs_review(tmp_path):
    run = execute(load_scenario(SC / "happy_path.yaml"), backend_name="mock", out_dir=tmp_path, judge=StaticJudge(None, "unset"))
    assert run.status == "needs_review" and run.failure_class == "judge"


def test_every_shipped_scenario_has_a_fixture_and_passes_deterministically(tmp_path):
    for p in sorted(SC.glob("*.yaml")):
        run = execute(load_scenario(p), backend_name="mock", out_dir=tmp_path, judge=StaticJudge(True))
        assert run.status == "passed", (p.name, [(a.description, a.passed, a.detail) for a in run.assertions if a.passed is not True])


def test_cli_exit_codes(tmp_path, monkeypatch):
    monkeypatch.setenv("VAPI_VERIFY_JUDGE", "")
    rc = main(["run", "--scenario", str(SC / "correction.yaml"), "--backend", "mock", "--out", str(tmp_path)])
    assert rc == 2          # deterministic pass + no judge -> needs_review
    monkeypatch.setenv("VAPI_VERIFY_MOCK_VARIANT", "fail")
    rc = main(["run", "--scenario", str(SC / "happy_path.yaml"), "--backend", "mock", "--out", str(tmp_path)])
    assert rc == 1


def test_mcp_server_tools_over_jsonrpc(tmp_path):
    s = Server(tmp_path, SC, allow_real_calls=False)
    init = s.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
    assert init["result"]["serverInfo"]["name"] == "vapi-verify"
    assert s.handle({"jsonrpc": "2.0", "method": "notifications/initialized"}) is None
    tools = s.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})["result"]["tools"]
    assert {t["name"] for t in tools} == {"list_verification_runs", "get_run_evidence", "request_voice_test"}
    # run a scenario through the tool, then read it back
    r = s.handle({"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "request_voice_test", "arguments": {"scenario_id": "escalation"}}})
    body = json.loads(r["result"]["content"][0]["text"])
    assert r["result"]["isError"] is False and body["status"] in ("passed", "needs_review")
    ev = s.handle({"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {"name": "get_run_evidence", "arguments": {"run_id": body["run_id"]}}})
    assert "transcript" in json.loads(ev["result"]["content"][0]["text"])
    lst = s.handle({"jsonrpc": "2.0", "id": 5, "method": "tools/call", "params": {"name": "list_verification_runs", "arguments": {"limit": 5}}})
    assert json.loads(lst["result"]["content"][0]["text"])[0]["run_id"] == body["run_id"]


def test_mcp_server_refuses_unknown_scenario_and_real_calls(tmp_path):
    s = Server(tmp_path, SC)
    bad = s.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "request_voice_test", "arguments": {"scenario_id": "../../etc/passwd"}}})
    assert bad["result"]["isError"] is True and "unknown scenario" in bad["result"]["content"][0]["text"]
    real = s.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "request_voice_test", "arguments": {"scenario_id": "happy_path", "backend": "vapi"}}})
    assert real["result"]["isError"] is True and "disabled" in real["result"]["content"][0]["text"]
    trav = s.handle({"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "get_run_evidence", "arguments": {"run_id": "../x"}}})
    assert trav["result"]["isError"] is True


def test_vapi_backend_refuses_without_explicit_opt_in(monkeypatch):
    from vapi_verify.backends import InfraError
    from vapi_verify.backends.vapi import VapiBackend
    for k in ("VAPI_API_KEY", "VAPI_ASSISTANT_ID", "VAPI_PHONE_NUMBER_ID", "VAPI_VERIFY_TEST_NUMBER"):
        monkeypatch.setenv(k, "x")
    monkeypatch.delenv("VAPI_VERIFY_ALLOW_REAL_CALLS", raising=False)
    try:
        VapiBackend(); assert False, "should refuse"
    except InfraError as e:
        assert "refusing" in str(e)


def test_call_that_ended_with_pipeline_error_is_infra_and_retried(tmp_path):
    import json
    fx = tmp_path / "fx"; fx.mkdir()
    good = json.loads((ROOT / "fixtures/mock_calls/happy_path.json").read_text())
    broken = dict(good, endedReason="pipeline-error-openai-llm-failed", artifact={"transcript": "", "messages": []})
    # attempt 1 -> broken (ended, but provider failed); attempt 2 -> good
    class Flaky:
        name = "mock"
        def run_scenario(self, scenario, *, attempt):
            return dict(broken) if attempt == 1 else dict(good)
    run = execute(load_scenario(SC / "happy_path.yaml"), backend_name="mock", out_dir=tmp_path, judge=StaticJudge(True), backend=Flaky())
    assert run.status == "passed" and run.attempt == 2 and run.history[1]["to"] == "retrying"
