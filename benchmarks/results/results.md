# Tradewind seeded-fault benchmark

Every row runs an injected fault against the real harness and records whether it was caught and how. F1–F3 and F5 are caught by construction; F4 is reported honestly (see the caveat below).

## Catch rates

| Family | Fault class | Caught | Total |
| --- | --- | ---: | ---: |
| F1 | Hallucinated instruments | 3 | 3 |
| F2 | Accounting attacks | 3 | 3 |
| F3 | Limit breaches | 4 | 4 |
| F4 | Nondeterminism injection | 3 | 3 |
| F5 | Trace tampering | 4 | 4 |
| **All** | | **17** | **17** |

## Scenarios

| Fault | Scenario | Expected detection | Detected? | Mechanism | Seq |
| --- | --- | --- | :---: | --- | ---: |
| F1 | unknown ticker TSLA | VETO order_validity | ✅ | VETO order_validity: symbol 'TSLA' is not in the tradeable universe | 3 |
| F1 | meme instrument | VETO order_validity | ✅ | VETO order_validity: symbol 'DOGE-9000' is not in the tradeable universe | 3 |
| F1 | lookalike symbol | VETO order_validity | ✅ | VETO order_validity: symbol 'AAPL.US' is not in the tradeable universe | 3 |
| F2 | conjured cash | VETO cash_conservation | ✅ | VETO cash_conservation + apply_fill raised | 3 |
| F2 | phantom shares | VETO cash_conservation | ✅ | VETO cash_conservation + apply_fill raised | 3 |
| F2 | overdraw cash | VETO no_negative_cash | ✅ | VETO no_negative_cash: post-fill cash -9000 would breach the margin floor 0 | 3 |
| F3 | oversized position | VETO position_limit | ✅ | VETO position_limit: position in AAPL would reach 10, over the 5 cap | 3 |
| F3 | gross exposure | VETO position_limit | ✅ | VETO position_limit: gross exposure would reach 1000, over the 500 cap | 3 |
| F3 | turnover rate | VETO rate_limit | ✅ | VETO rate_limit: 3 orders already in the 1:00:00 window, at the 3 cap | 3 |
| F3 | slow-bleed drawdown | VETO drawdown_breaker (circuit breaker) | ✅ | VETO drawdown_breaker: equity 7675.00 is at/below the drawdown threshold 8000.00 (0.20 of… | 21 |
| F4 | wall-clock/RNG in LLM request | ReplayDivergence on replay | ✅ | ReplayDivergence: replay miss: no recorded llm_call with request_hash 04b4047fc698291b…; … | - |
| F4 | wall-clock/RNG in data request | ReplayDivergence on replay | ✅ | ReplayDivergence: replay miss: no recorded data_read with request_hash 8967070a886e5f63…;… | - |
| F4 | extra call on replay | ReplayDivergence on replay | ✅ | ReplayDivergence: replay exhausted: every recorded llm_call with request_hash 2ff0acc85ea… | - |
| F5 | flipped payload byte | TraceIntegrityError (chain hash) | ✅ | TraceIntegrityError: tampered-flip.jsonl: chain-hash mismatch at seq 2 (line 3): stored 6… | - |
| F5 | deleted event line | TraceIntegrityError (seq order) | ✅ | TraceIntegrityError: tampered-drop.jsonl:3: seq 3 out of order, expected 2 | - |
| F5 | reordered events | TraceIntegrityError | ✅ | TraceIntegrityError: tampered-reorder.jsonl:2: seq 2 out of order, expected 1 | - |
| F5 | non-canonical re-encoding | TraceIntegrityError (not canonical) | ✅ | TraceIntegrityError: tampered-noncanon.jsonl:2: line bytes are not canonical JSON for the… | - |

## F4 caveat

The determinism check catches nondeterminism that influences a recorded boundary request or the event stream — on replay the reissued request hashes no longer match the recording, so the lookup misses and raises `ReplayDivergence`. Nondeterminism that never influences any recorded byte is invisible by construction; the harness cannot flag what it never observes. The benchmark models the drifting value with a process-lifetime counter (a deterministic, CI-safe stand-in for a wall-clock read or unseeded RNG); `tests/test_bench.py` additionally proves the same catch against genuine `time`/`random` sources.
