## vapi-verify · `happy_path` · ❌ FAILED

| run | `e19dd2f58443` | attempt 1/2 | backend `mock` | git `c68e86aff7` |
|---|---|---|---|---|

**Outcome:** 5 critical assertion(s) failed  
**Failure class:** `semantic` — call ended normally; agent confirms the booking with day and time; structured output says the appointment was booked; agent never punts to a transfer; average turn latency within budget  

**Call:** `call_mock_hp_fail` · status `ended` · endedReason `assistant-forwarded-call` · [recording](https://example.invalid/recordings/call_mock_hp_fail.wav)

### Assertions

| # | kind | critical | result | detail |
|---|---|---|---|---|
| 1 | `ended_reason_in` — call ended normally | yes | ❌ | endedReason='assistant-forwarded-call' |
| 2 | `transcript_regex` — agent confirms the booking with day and time | yes | ❌ | no match |
| 3 | `structured_output` — structured output says the appointment was booked | yes | ❌ | appointment.booked=False (expected True) |
| 4 | `forbidden_phrase` — agent never punts to a transfer | yes | ❌ | found forbidden: 'let me transfer' |
| 5 | `latency_max` — average turn latency within budget | yes | ❌ | turnLatencyAverage=2210 ms (max 1500) |
| 6 | `ai_rubric` — conversation quality (LLM judge — signal only) | no | ❌ | [claude-cli] r warmly and collected both name and phone number, but it then read back "next Thursday at 10 AM" when the caller had clearly asked for Tuesday — i |

### Latency (two numbers, kept apart on purpose)

- client-observed (this harness, place→ended wall clock): **0 ms**
- provider-reported (`artifact.performanceMetrics`, verbatim): turnLatencyAverage=2210, modelLatencyAverage=540, voiceLatencyAverage=310, transcriberLatencyAverage=190, endpointingLatencyAverage=420, numUserInterrupted=0, numAssistantInterrupted=1

### Transcript

```
assistant: Thanks for calling Bayside Dental, this is Ava. How can I help?
     user: Hi, I'd like to book a cleaning for next Tuesday at 10am.
assistant: Sure. Can I get your name and a phone number?
     user: Sam Rivera, 555-0142.
assistant: Got it. I have you down for a cleaning next Thursday at 10 AM under Sam Rivera. Does that look right?
     user: No, I said Tuesday.
assistant: I'm sorry, let me transfer you to the front desk.
     user: Fine.
```

_Vapi analysis — summary: Agent booked the wrong day and transferred. · successEvaluation: `false` (a signal, not the gate)_

_Evidence: `runs/e19dd2f58443/evidence.json` · schema v1_
