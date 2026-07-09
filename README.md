# Tradewind

**A deterministic verification and evaluation harness for LLM trading agents.**

Agents propose, the harness disposes. Tradewind wraps an LLM-based trading-agent
framework (demo target: [TradingAgents](https://github.com/TauricResearch/TradingAgents)
by Tauric Research) and gives it three things it does not have on its own:

1. **Determinism** — every LLM call and market-data read is recorded to a
   tamper-evident trace, so a run replays **bit-for-bit with zero live calls**,
   forever.
2. **Invariant enforcement** — a non-LLM engine vetoes any action that breaks a
   formally stated risk rule (cash conservation, position/exposure limits,
   drawdown, order validity, turnover) *before* it can touch the portfolio. No
   model output can bypass this layer.
3. **Auditability** — every trade traces back through the full agent
   deliberation to the market data that informed it, in a diffable report.

The thesis in one line: **no LLM output is trusted; the harness checks execution
economics, not the model's promises.**

---

## 60-second quickstart (no API key)

```bash
git clone --recurse-submodules <your-fork>/tradewind && cd tradewind
uv sync --dev

# 1. Replay the committed example trace — byte-identical, zero network.
uv run tradewind replay examples/traces/tradingagents_demo.jsonl
#   REPLAY OK: 12 events byte-identical, chain hash 22e5e2f4…

# 2. Run a scripted agent over bundled data under the full invariant engine.
uv run tradewind run --data benchmarks/data --symbol AAPL --agent sma --out run.jsonl

# 3. Render the audit report (self-contained HTML + JSON).
uv run tradewind report run.jsonl --out-html report.html --out-json report.json

# 4. Prove the harness catches real failure classes.
uv run tradewind bench          # → ALL: 17/17 caught
```

Everything above runs offline with no secrets. Recording a *live* TradingAgents
run needs three more steps (an editable install of the submodule, the
`tradewind[tradingagents]` extra, and a provider API key) — see
[Running the real TradingAgents](#running-the-real-tradingagents).

---

## Architecture

```
                  ┌─────────────────────────────────────────────┐
   LLM / data ───▶│  BOUNDARIES  LLMBoundary · DataBoundary      │  record  ┌──────────────┐
   (record only)  │              VirtualClock · SeededRand       │─────────▶│  trace.jsonl │
                  └─────────────────────────────────────────────┘  replay  │  append-only │
                          │ (replay: answer from trace, never live)◀────────│  SHA-256     │
                          ▼                                                  │  chain hash  │
   agent  ┌───────────────────────┐   ProposedAction   ┌──────────────────┐ └──────────────┘
   frame- │  ADAPTER (only place  │───────────────────▶│  INVARIANT       │
   work   │  that imports the     │                    │  ENGINE          │  PASS │ WARN │ VETO
   ───────│  agent framework)     │◀── agent_message ──│  6 pure checks   │───────────────┐
          └───────────────────────┘   events           └──────────────────┘               │
                                                                 │ admitted                │ veto
                                                                 ▼                         ▼
                                              ┌──────────────────────────┐        ┌────────────────┐
                                              │  SIM  next-bar-open fill  │        │ violation event│
                                              │  Decimal accounting       │        │ (blocked, why) │
                                              └──────────────────────────┘        └────────────────┘
                                                                 │
                                                                 ▼
                                      ┌────────────────────────────────────────────┐
                                      │  REPORT   equity curve · per-decision audit │
                                      │           · determinism attestation · diff  │
                                      └────────────────────────────────────────────┘
```

The core (`trace`, `invariants`, `sim`, `report`, `bench`) never imports an
agent framework or LLM SDK — only `adapters/*` may. Wall-clock and global
`random` are banned from core code and enforced by a test that greps the source.

| Package | Responsibility |
| --- | --- |
| `tradewind.trace` | Recordable boundaries; append-only JSONL with a SHA-256 chain hash; deterministic replay. |
| `tradewind.invariants` | The six risk checks, the verdict pipeline, and a conservation-enforcing portfolio. |
| `tradewind.sim` | Bar-replay simulator; next-bar-open paper fills with slippage/fees. |
| `tradewind.report` | Self-contained HTML + JSON reports and trace diff. |
| `tradewind.bench` | The seeded-fault benchmark. |
| `tradewind.adapters.tradingagents` | The only module that imports TradingAgents; injects the boundaries at its LLM seam. |
| `tradewind.mcp` | MCP server exposing the harness to any MCP client. |

---

## The invariants

Each is a pure function `check(state, action, ctx) -> Verdict` run *before* any
fill touches the portfolio. A `VETO` blocks the action and emits a fully-evidenced
`violation` event. Money is exact `Decimal`; the accounting path never rounds, so
conservation holds with a **zero** residual.

| # | Invariant | Formal property |
| --- | --- | --- |
| 1 | **Cash conservation** | Applying a fill changes equity-at-fill-price by exactly `−fees`: `cash_delta + price·position_delta == −fees`, exactly. A fill that would create or destroy cash is rejected at application (`InvariantViolation`). |
| 2 | **No negative cash** | `cash + fill.cash_delta ≥ −margin_limit` (default `margin_limit = 0` ⇒ no implicit leverage). |
| 3 | **Position limits** | For the traded symbol `|position_post| ≤ cap`, and gross exposure `Σ|position_i·price_i| ≤ gross_cap` after the fill. |
| 4 | **Drawdown circuit-breaker** | If equity `≤ high_water_mark·(1 − max_drawdown)`, **every** action is vetoed — a halt, not a per-order check. |
| 5 | **Order validity** | Well-formed side/symbol/quantity; `quantity > 0` and finite; symbol in the tradeable universe; fill price within `price_sanity_pct` of the last quote. |
| 6 | **Rate / turnover limit** | ≤ `N` admitted orders per trailing virtual-time window. |
| 7 | **Idempotent replay** | Re-applying a recorded fill sequence yields the identical final state (a pure fold; property-tested). |

A hostile fake agent (`tests/fakes/rogue_agent.py`) firing oversized orders,
negative/zero/NaN quantities, unknown symbols, and cash-blowout buys is proven
**fully contained** — zero fills reach the portfolio.

---

## Benchmark results

`tradewind bench` injects a matrix of faults against the *real* harness and
measures whether each is caught and by what mechanism — nothing is hard-coded to
"detected", so a regression that breaks a guard turns a row red. Full table:
[`benchmarks/results/results.md`](benchmarks/results/results.md).

| Family | Fault class | Caught |
| --- | --- | ---: |
| F1 | Hallucinated instruments (symbols off-universe) | 3 / 3 |
| F2 | Accounting attacks (fills that create/destroy cash) | 3 / 3 |
| F3 | Limit breaches (position, gross, drawdown, turnover) | 4 / 4 |
| F4 | Nondeterminism injection (replay divergence) | 3 / 3 |
| F5 | Trace tampering (edit / delete / reorder / re-encode) | 4 / 4 |
| **All** | | **17 / 17** |

`bench` exits non-zero if any critical family (F1/F2/F3/F5) is not fully caught —
an honest failure, not a green light. CI runs it on every push.

---

## Running the real TradingAgents

The committed example trace is recorded through the adapter's pipeline with a
**deterministic scripted LLM**, because the build environment has no provider
API key and cannot install TradingAgents' full dependency stack. The trace
format and replay guarantees are identical to a live recording — only the
authorship of the words differs. To record a genuine run:

```bash
pip install -e third_party/TradingAgents      # the pinned submodule (read-only)
pip install 'tradewind[tradingagents]'        # langchain-core, langgraph
export OPENAI_API_KEY=...                      # a provider key (recording only)
```

then drive it via `tradewind.adapters.tradingagents.adapter.run_tradingagents`,
which patches TradingAgents' `create_llm_client` seam so every model call flows
through Tradewind's boundary. Once recorded, the trace replays and reports with
no key, like any other.

---

## MCP server

`tradewind.mcp` exposes the harness to any MCP client via five tools —
`run_evaluation`, `replay_trace`, `verify_trace`, `get_report`,
`list_invariants` — as a thin layer over the same library API the CLI uses.
Install the extra and run over stdio:

```bash
pip install 'tradewind[mcp]'
python -m tradewind.mcp.server
```

---

## Limitations

Stated plainly, because overstating fidelity is the classic backtest sin:

- **Synthetic market data.** The bundled OHLCV
  ([`benchmarks/data/`](benchmarks/data/PROVENANCE.md)) is a seeded random walk,
  clearly labelled — not real prices. It exists so the whole suite is
  deterministic and runs in CI with no keys.
- **Bar-level simulation.** Fills execute at the next daily bar's open ± a flat
  slippage in bps, with bps fees. The model does **not** capture intrabar price
  paths, order books or depth, market impact, partial fills, bid/ask spreads
  beyond flat slippage, borrow/short constraints, dividends, or splits.
- **Equity curve is marks-only.** The trace records fills, not per-bar marks, so
  the report's curve marks each position at its most recent *trade* price, not
  every bar. It is labelled as such.
- **F4 is honest about its reach.** The determinism check catches nondeterminism
  that influences a recorded boundary request or the event stream (it surfaces
  as a replay divergence); nondeterminism that never changes a recorded byte is
  invisible by construction. The benchmark models the drift with a
  process-lifetime counter and additionally proves the catch against genuine
  `time`/`random` in `tests/test_bench.py`.
- **Single-threaded v1.** One symbol per scripted run, sequential bar replay; no
  concurrency.
- **TradingAgents is pinned** at v0.3.1 (`01477f9`). The adapter targets that
  version's LLM-construction seam and final-state keys; a different release may
  move them.

---

## Design decisions

Every non-obvious choice — the exact canonical-JSON form, why record returns
what replay returns, the two conservation surfaces, the drawdown circuit
breaker, why the bundled data is synthetic, the F4 counter stand-in, the
TradingAgents injection seam — is logged with its rationale in
[`DECISIONS.md`](DECISIONS.md).

## Development

```bash
uv run ruff check . && uv run ruff format --check .   # lint + format
uv run mypy                                           # strict on src/tradewind
uv run pytest --cov                                   # 165 tests
```

CI (lint, type-check, tests, benchmark, and a replay of the committed trace)
runs on every push with no secrets required.

## License

MIT.
