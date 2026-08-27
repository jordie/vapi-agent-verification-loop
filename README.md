# vapi-agent-verification-loop

A compact, legible reference implementation of one question: **how does a background coding agent
*verify* a change to a Vapi voice agent — with evidence a reviewer can trust — instead of just claiming it works?**

```
scenario.yaml ──► backend places a call ──► Vapi Call object ──► evidence.json
                  (mock | real Vapi)          (status, artifact,      │
                                               performanceMetrics)     ▼
                                    deterministic assertions ──► verdict ──► run.json (typed status contract)
                                    LLM judge = one signal                └─► report.md (PR-comment style)
```

It is deliberately small. It is **not** a voice platform, not a replacement for Vapi's own
[Test Suites](https://docs.vapi.ai/test/voice-testing) or Evals, and makes no production-scale claims
(see [Limitations](#limitations-read-these)). It exists to show the *lifecycle design*: typed states,
a retry policy that only retries infrastructure failures, evidence kept separate from verdicts, an LLM
judge that can never be the sole gate, and a governed MCP boundary so an agent can drive the loop safely.

## What it validates

Five scenarios against a dental-office booking assistant (swap in your own):

| scenario | what it proves | deterministic gates | judge (signal only) |
|---|---|---|---|
| `happy_path` | books Tuesday 10 AM | ended normally · transcript confirms day+time · `structuredOutputs.appointment.booked == true` · no "transfer you" · `turnLatencyAverage ≤ 1500 ms` | greeting, details, polite close |
| `correction` | caller changes 2 PM → 3 PM | transcript has 3 PM · `appointment.time == "15:00"` | correction acknowledged, not argued |
| `interruption` | barge-in during a long read-back | answers "Saturday" · `numUserInterrupted ≤ 1` · `endpointingLatencyAverage ≤ 800 ms` | stopped the list, answered directly |
| `unsupported_request` | asks for lab results | never fabricates a result (forbidden regex) · declines + offers a channel | safe refusal |
| `escalation` | "I'm in pain, get me a person" | ended via transfer · transfer language present · no medical advice | urgency handled, no diagnosis |

Assertion kinds: `transcript_regex`, `forbidden_phrase`, `structured_output`, `latency_max`,
`max_interruptions`, `ended_reason_in` (all deterministic, critical by default) and `ai_rubric`
(never critical). Field names follow Vapi's OpenAPI schema for `Call` / `Artifact` / `PerformanceMetrics` / `Analysis`.

## What evidence it emits

Every run writes `runs/<run_id>/`:

- `run.json` — the **status contract** (`queued → running → evidence_collected → passed | failed | needs_review`, with `retrying` only for infra failures). Includes every assertion result, `failure_class`, the attempt count, and a transition history with timestamps. Consumers read this; nobody parses logs.
- `evidence.json` — normalized from the Vapi `Call`: transcript + turns, `recordingUrl`, structured outputs, Vapi's own `successEvaluation` (kept as a signal), and **two latency numbers kept apart on purpose**: `client_observed_ms` (this harness's wall clock, honest but coarse) and `provider_reported` (`artifact.performanceMetrics`, verbatim). They are never mixed or summed.
- `raw_call_attempt<N>.json` — the untouched provider payload, for audit.
- `report.md` — PR-comment-style summary. See [`runs/example-report.md`](runs/example-report.md).

## How failures are classified

| class | meaning | retried? | terminal state |
|---|---|---|---|
| `infra` | no usable evidence: transport error, provider 5xx/429, timeout, `endedReason` like `pipeline-error-*`, `assistant-request-failed` | **yes**, bounded (`--max-attempts`, default 2) | `failed` if attempts exhausted |
| `semantic` | evidence exists and a **critical** assertion failed | never | `failed` |
| `judge` | deterministic checks passed; the LLM judge said no or was unavailable | never | `needs_review` |

Rule of thumb encoded in `RunRecord.decide()`: a critical failure beats everything; anything unevaluable
is `needs_review`, never a silent pass.

## Run it

```bash
pip install -e ".[dev]"
pytest -q                                              # 24 tests: contract, evidence, judge, loop, MCP, backend guard

vapi-verify run --scenario scenarios/happy_path.yaml --backend mock --print-report
VAPI_VERIFY_MOCK_VARIANT=fail  vapi-verify run --scenario scenarios/happy_path.yaml   # exit 1, semantic
VAPI_VERIFY_MOCK_VARIANT=infra vapi-verify run --scenario scenarios/happy_path.yaml   # attempt 1 fails, attempt 2 passes
vapi-verify list
```

Exit codes: `0` passed · `1` failed · `2` needs_review · `3` harness error — so CI and agents can branch on them.

### Real calls (opt-in, costs minutes)

```bash
export VAPI_API_KEY=… VAPI_ASSISTANT_ID=… VAPI_PHONE_NUMBER_ID=… VAPI_VERIFY_TEST_NUMBER=+1…
export VAPI_VERIFY_ALLOW_REAL_CALLS=1          # nothing dials without this
export VAPI_VERIFY_JUDGE=claude-cli            # optional: use the local `claude -p` as the rubric judge
vapi-verify run --scenario scenarios/happy_path.yaml --backend vapi
```

The `vapi` backend does exactly two things: `POST /call` (outbound, `assistantId` + `phoneNumberId` +
`customer.number`) and polls `GET /call/{id}` until `status == "ended"`. The *other* side of the call —
the scripted tester — is intentionally out of scope: point `VAPI_VERIFY_TEST_NUMBER` at a Vapi Voice Test
Suite tester, a second assistant, or a human. 4xx responses are treated as *our* bug (not retried); 5xx/429/
transport errors are `infra` (retried once).

### Let an agent drive it (MCP)

`vapi-verify-mcp` is a ~150-line stdio MCP server with zero dependencies, exposing three governed tools:

| tool | does | guard |
|---|---|---|
| `list_verification_runs(limit)` | recent runs from the status contract | read-only |
| `get_run_evidence(run_id)` | evidence + report for one run | `run_id` must be alphanumeric (no path traversal) |
| `request_voice_test(scenario_id, backend)` | run a scenario | scenario **allowlist** (files in `scenarios/`); `backend=vapi` refused unless the server was started with `--allow-real-calls` **and** the env opt-in is set |

```bash
claude mcp add vapi-verify -- vapi-verify-mcp --out runs --scenarios scenarios
```

No shell tool, no arbitrary-URL tool. The point is that MCP is a *policy boundary*, not just an integration format.

## Where this came from

It's a small slice of a larger system I run in production, where coding agents (Claude Code, Codex) pull
scoped tasks from a queue, work in isolated git worktrees, report through a structured status contract
(deterministic heartbeat + progress classifier — no AI on the happy path), pass tests and a policy lint, and
land PRs; green reversible PRs auto-merge, everything else parks for a human. The lessons that shaped this repo:

- **Delivery ≠ liveness.** "The agent is running" is not a result. A run is done when `run.json` says so and `evidence.json` exists.
- **Retry the infrastructure, never the semantics.** Retrying a failed assertion just burns minutes and hides flakiness.
- **The judge is a witness, not the jury.** LLM rubric = one signal; business-critical behavior gets a deterministic assertion.
- **Keep the two latencies apart.** Client-observed wall clock and provider-reported turn metrics answer different questions.

## Limitations (read these)

- **No production-scale or carrier-scale claim.** Fixtures are synthetic; the real backend has been exercised against the API shape, not a fleet.
- **Voice Test Suites are dashboard-driven, and Vapi's testing surface is moving toward Simulations.** The public OpenAPI spec (Aug 2026) has `TestSuite*` schemas but no `/test-suite` path, while `/eval/simulation/*` endpoints do exist. This harness therefore drives the public Call API (`POST /call` → `GET /call/{id}`) plus mock evals **on purpose**, and treats "put Simulation results on the same status contract" as the integration constraint to solve next — not something to paper over.
- **Evaluator nondeterminism remains.** `ai_rubric` results will vary; that's why they can't gate.
- **`client_observed_ms` includes polling granularity** (default 5 s). Use `provider_reported` for turn-level numbers.
- **One assistant, five scenarios.** A real deployment needs scenario generation, flake tracking, and sampled human review of `needs_review` runs.

MIT · Jordan Girmay
