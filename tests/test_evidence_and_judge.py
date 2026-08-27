import json
from pathlib import Path

from vapi_verify.evidence import Evidence, evidence_from_call
from vapi_verify.judge import StaticJudge, evaluate

FIX = Path(__file__).resolve().parents[1] / "fixtures" / "mock_calls"


def _ev(name="happy_path.json"):
    call = json.loads((FIX / name).read_text())
    return evidence_from_call(call, run_id="r1", scenario_id="happy_path", backend="mock", client_observed_ms=87000)


def test_evidence_maps_vapi_call_fields():
    ev = _ev()
    assert ev.call_id == "call_mock_hp_ok" and ev.call_status == "ended" and ev.ended_reason == "customer-ended-call"
    assert ev.turns[0].role == "assistant" and "Bayside" in ev.turns[0].text      # 'bot' normalized to 'assistant'
    assert ev.structured_outputs["appointment"]["booked"] is True
    assert ev.provider_reported["turnLatencyAverage"] == 1120 and ev.client_observed_ms == 87000
    assert ev.recording_url.endswith(".wav") and ev.success_evaluation == "true"


def test_evidence_roundtrip(tmp_path):
    p = _ev().save(tmp_path / "e.json")
    ev2 = Evidence.load(p)
    assert ev2.turns[1].role == "user" and ev2.structured_outputs == _ev().structured_outputs


def test_deterministic_assertions_pass_on_good_call():
    res = evaluate([
        {"type": "transcript_regex", "pattern": r"tuesday.{0,40}10\s?am"},
        {"type": "structured_output", "path": "appointment.booked", "equals": True},
        {"type": "forbidden_phrase", "pattern": "transfer you"},
        {"type": "latency_max", "metric": "turnLatencyAverage", "max_ms": 1500},
        {"type": "max_interruptions", "max": 1},
        {"type": "ended_reason_in", "values": ["customer-ended-call"]},
    ], _ev(), StaticJudge(True))
    assert all(r.passed is True for r in res) and all(r.critical for r in res)


def test_deterministic_assertions_fail_on_bad_call():
    res = evaluate([
        {"type": "transcript_regex", "pattern": r"tuesday.{0,40}10\s?am"},
        {"type": "structured_output", "path": "appointment.booked", "equals": True},
        {"type": "forbidden_phrase", "pattern": "transfer you"},
        {"type": "latency_max", "metric": "turnLatencyAverage", "max_ms": 1500},
    ], _ev("happy_path.fail.json"), StaticJudge(True))
    assert [r.passed for r in res] == [False, False, False, False]


def test_ai_rubric_is_never_critical_and_none_means_unevaluable():
    res = evaluate([{"type": "ai_rubric", "rubric": "x", "critical": True}], _ev(), StaticJudge(None, "no judge"))
    assert res[0].critical is False and res[0].passed is None and "no judge" in res[0].detail


def test_missing_metric_is_unevaluable_not_pass():
    ev = _ev(); ev.provider_reported = {}
    res = evaluate([{"type": "latency_max", "metric": "turnLatencyAverage", "max_ms": 1500}], ev, StaticJudge(True))
    assert res[0].passed is None


def test_unknown_assertion_type_does_not_crash():
    res = evaluate([{"type": "bogus"}], _ev(), StaticJudge(True))
    assert res[0].passed is None and "assertion error" in res[0].detail
