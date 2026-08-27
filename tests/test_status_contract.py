import pytest
from vapi_verify.status_contract import AssertionResult, InvalidTransition, RunRecord, classify_ended_reason


def test_happy_transitions_and_terminal_timestamps():
    r = RunRecord(scenario_id="s", backend="mock")
    r.transition("running"); r.transition("evidence_collected")
    r.assertions = [AssertionResult("transcript_regex", "x", True, True)]
    assert r.decide() == "passed" and r.ended_at and r.started_at
    assert [h["to"] for h in r.history] == ["running", "evidence_collected", "passed"]


def test_invalid_transition_raises():
    r = RunRecord(scenario_id="s", backend="mock")
    with pytest.raises(InvalidTransition):
        r.transition("passed")           # queued -> passed is not allowed
    r.transition("running"); r.transition("evidence_collected")
    r.assertions = [AssertionResult("k", "d", True, True)]; r.decide()
    with pytest.raises(InvalidTransition):
        r.transition("running")          # terminal states are frozen


def test_critical_failure_beats_everything():
    r = RunRecord(scenario_id="s", backend="mock"); r.transition("running"); r.transition("evidence_collected")
    r.assertions = [AssertionResult("a", "crit", False, True), AssertionResult("ai_rubric", "judge", None, False)]
    assert r.decide() == "failed" and r.failure_class == "semantic" and "crit" in r.failure_reason


def test_unevaluable_or_soft_failure_needs_review_not_pass():
    r = RunRecord(scenario_id="s", backend="mock"); r.transition("running"); r.transition("evidence_collected")
    r.assertions = [AssertionResult("a", "ok", True, True), AssertionResult("ai_rubric", "judge", None, False)]
    assert r.decide() == "needs_review" and r.failure_class == "judge"
    r2 = RunRecord(scenario_id="s", backend="mock"); r2.transition("running"); r2.transition("evidence_collected")
    r2.assertions = [AssertionResult("a", "ok", True, True), AssertionResult("ai_rubric", "judge said no", False, False)]
    assert r2.decide() == "needs_review"


def test_retry_policy_only_infra_and_bounded():
    r = RunRecord(scenario_id="s", backend="mock", max_attempts=2); r.transition("running")
    assert r.can_retry("infra") and not r.can_retry("semantic") and not r.can_retry("judge")
    r.begin_retry("provider 503")
    assert r.attempt == 2 and r.status == "running"
    assert not r.can_retry("infra")      # attempts exhausted
    r.fail("infra", "still down")
    assert r.status == "failed" and r.failure_class == "infra"


def test_ended_reason_classification():
    assert classify_ended_reason("pipeline-error-openai-llm-failed") == "infra"
    assert classify_ended_reason("assistant-request-failed") == "infra"
    assert classify_ended_reason(None) == "infra"
    assert classify_ended_reason("customer-ended-call") == "none"
    assert classify_ended_reason("assistant-forwarded-call") == "none"


def test_roundtrip_serialization():
    r = RunRecord(scenario_id="s", backend="mock"); r.transition("running")
    r.assertions = [AssertionResult("k", "d", True, True, "det")]
    r2 = RunRecord.from_dict(__import__("json").loads(r.to_json()))
    assert r2.run_id == r.run_id and r2.assertions[0].detail == "det" and r2.status == "running"
