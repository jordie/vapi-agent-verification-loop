## vapi-verify · `happy_path` · ✅ PASSED

| run | `6c2aa4c01f3c` | attempt 1/2 | backend `mock` | git `unknown` |
|---|---|---|---|---|

**Outcome:** all 6 assertion(s) passed  

**Call:** `call_mock_hp_ok` · status `ended` · endedReason `customer-ended-call` · [recording](https://example.invalid/recordings/call_mock_hp_ok.wav)

### Assertions

| # | kind | critical | result | detail |
|---|---|---|---|---|
| 1 | `ended_reason_in` — call ended normally | yes | ✅ | endedReason='customer-ended-call' |
| 2 | `transcript_regex` — agent confirms the booking with day and time | yes | ✅ | matched: 'Tuesday at 10 AM' |
| 3 | `structured_output` — structured output says the appointment was booked | yes | ✅ | appointment.booked=True (expected True) |
| 4 | `forbidden_phrase` — agent never punts to a transfer | yes | ✅ | absent |
| 5 | `latency_max` — average turn latency within budget | yes | ✅ | turnLatencyAverage=1120 ms (max 1500) |
| 6 | `ai_rubric` — conversation quality (LLM judge — signal only) | no | ✅ | [claude-cli] mber (555-0142), and explicitly read back the date and time ("next Tuesday at 10 AM") for the caller to confirm, which they did. It used the caller |

### Latency (two numbers, kept apart on purpose)

- client-observed (this harness, place→ended wall clock): **0 ms**
- provider-reported (`artifact.performanceMetrics`, verbatim): turnLatencyAverage=1120, modelLatencyAverage=540, voiceLatencyAverage=310, transcriberLatencyAverage=190, endpointingLatencyAverage=420, numUserInterrupted=0, numAssistantInterrupted=1

### Transcript

```
assistant: Thanks for calling Bayside Dental, this is Ava. How can I help?
     user: Hi, I'd like to book a cleaning for next Tuesday at 10am.
assistant: Sure. Can I get your name and a phone number?
     user: Sam Rivera, 555-0142.
assistant: Got it. I have you down for a cleaning next Tuesday at 10 AM under Sam Rivera, 555-0142. Does that look right?
     user: Yes, perfect.
assistant: You're all set. We'll send a reminder the day before. Anything else?
     user: No, thanks. Bye.
assistant: Goodbye!
```

_Vapi analysis — summary: Caller booked a cleaning for Tuesday 10 AM. · successEvaluation: `true` (a signal, not the gate)_

_Evidence: `runs/6c2aa4c01f3c/evidence.json` · schema v1_
