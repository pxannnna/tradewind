# TRADEWIND — Deterministic Verification Harness for LLM Trading Agents

Instructions to the coding agent (Claude Code / Fable): This document is the single source of truth for this project. Read it fully before writing any code. Work through the phases in order. Do not skip acceptance criteria. When a decision is not specified here, prefer the simpler, more testable option and record the decision in `DECISIONS.md`.

## 0. One-paragraph summary

Build Tradewind: a verification and evaluation harness that wraps LLM-based trading-agent frameworks (demo target: the open-source TradingAgents multi-agent framework by Tauric Research). Tradewind makes agent runs bit-for-bit reproducible (deterministic replay of all LLM calls and market data), enforces hard risk invariants outside the model (position limits, drawdown caps, cash conservation, order validity), and produces diffable, auditable evaluation reports showing exactly what every agent decided, why, and where it violated a constraint. The core thesis: agents propose, the harness disposes. No LLM output can bypass the invariant layer.

## 1. Goals and non-goals

### Goals

1. Determinism: the same run config + same trace produces byte-identical results, forever, with zero live LLM calls on replay.
2. Invariant enforcement: a non-LLM, deterministic engine that vetoes any agent action violating a formally stated invariant, and logs the violation with full context.
3. Auditability: every trade decision traces back through the full agent decision chain (which agents said what, with which inputs) to source market data.
4. Framework-agnostic core, TradingAgents-specific adapter: the trace/invariant/report core must not import TradingAgents; a thin adapter connects them.
5. Honest evaluation: a seeded-fault benchmark that demonstrates the harness catching real classes of failure, with a results table.

### Non-goals (do NOT build these)

* No live trading, no broker integration, no real money paths. Paper/simulated fills only.
* No new trading strategies or alpha. We evaluate agents; we do not make them profitable.
* No web dashboard in v1 (a static HTML report is fine; see Phase 5).
* No support for streaming/websocket live data in v1. Recorded snapshots only.
* No attempt to fix or patch TradingAgents internals. Wrap, don't fork-modify (vendored copy is read-only).

## 2. Repository setup

Target repo: the user's own GitHub repo (`tradewind` or a name the user confirms). Layout:

```
tradewind/
├── HARNESS_BUILD_SPEC.md      # this file
├── DECISIONS.md               # running log of unspecified decisions made
├── README.md                  # written last, see §10
├── pyproject.toml             # Python ≥3.11, uv-compatible
├── src/tradewind/
│   ├── trace/                 # capture + replay (Phase 1)
│   ├── invariants/            # invariant engine (Phase 2)
│   ├── sim/                   # market replay + paper fills (Phase 3)
│   ├── adapters/
│   │   └── tradingagents/     # the only module allowed to import TradingAgents
│   ├── report/                # eval report generation (Phase 5)
│   ├── mcp/                   # MCP server exposing the harness (Phase 6)
│   └── cli.py                 # `tradewind run|replay|verify|report|bench`
├── benchmarks/                # seeded-fault benchmark (Phase 4)
├── tests/                     # pytest + hypothesis
├── examples/                  # runnable end-to-end demos
└── third_party/TradingAgents/ # git submodule or pinned clone — READ-ONLY
```

Setup steps:

1. Initialise the repo with `pyproject.toml`, ruff + mypy (strict on `src/tradewind/`), pytest, hypothesis, pre-commit.
2. Locate the TradingAgents repository (Tauric Research on GitHub — verify the canonical URL from its paper/README rather than guessing), pin it as a submodule at a specific commit SHA, and record the SHA in `DECISIONS.md`. Read its architecture (analyst/researcher/trader/risk agents, LangGraph graph) before writing the adapter.
3. All new code lives under `src/tradewind/`. Never edit files under `third_party/`.

Dependencies (keep minimal, justify anything beyond): `pydantic` v2 (schemas), `hypothesis`, `pytest`, `typer` (CLI), `jinja2` (reports), `mcp` (server), plus whatever TradingAgents itself pins (isolated via the adapter's optional extra: `pip install tradewind[tradingagents]`). Market data: bundled recorded snapshots (see §5) — no paid APIs required to run tests or the benchmark.

## 3. Phase 1 — Trace capture and deterministic replay (the foundation)

Design. Every side-effectful boundary is wrapped in a recordable interface:

* `LLMBoundary`: intercepts every LLM call. In record mode, forwards to the real provider and appends `(request_hash, request, response, latency_ms, token_counts, cost_estimate)` to the trace. In replay mode, looks up `request_hash` and returns the recorded response; a cache miss is a hard error (`ReplayDivergence`), never a silent live call.
* `DataBoundary`: same pattern for market-data reads.
* `ClockBoundary` and `RandBoundary`: virtual clock and seeded RNG injected everywhere; wall-clock and global `random` are forbidden in core code (add a lint/test that greps for them).

Trace format. Append-only JSONL, one event per line, each event a Pydantic-validated record with: monotonic `seq`, `event_type` (`llm_call`, `data_read`, `agent_message`, `proposed_action`, `invariant_check`, `fill`, `violation`), `parent_seq` (causality), payload, and a running SHA-256 chain hash (`hash_n = sha256(hash_{n-1} || canonical_json(event_n))`) so traces are tamper-evident. A trace file header records: config hash, code version, model IDs, submodule SHA, RNG seed.

Request hashing. `request_hash = sha256(canonical_json({model, messages, tools, temperature, ...}))` — canonical JSON means sorted keys, no whitespace, UTF-8. Document the canonicalisation precisely; it is the determinism linchpin.

Acceptance criteria (Phase 1):

* `tradewind replay trace.jsonl` re-executes a recorded run with zero network calls and produces a byte-identical event stream (assert equal chain hash). Prove it in a test.
* A mutated trace (flip one byte) fails chain-hash verification with a clear error.
* Property test (Hypothesis): round-trip serialise/deserialise of arbitrary generated events is lossless.

## 4. Phase 2 — Invariant engine

Design. Invariants are pure functions `check(state: PortfolioState, action: ProposedAction, ctx: MarketContext) -> Verdict` registered in a pipeline that runs before any fill is applied. Verdicts: `PASS`, `VETO(reason, evidence)`, `WARN`. A `VETO` blocks the action, emits a `violation` event with the full evidence chain, and (configurable) either halts the run or continues with the action suppressed. The engine is deterministic, side-effect-free, and imports nothing from any LLM library.

Invariant set v1 (each with a docstring stating the invariant formally):

1. Cash conservation: post-fill `cash + Σ(position_i × price_i)` equals pre-fill value ± fill cost ± fees exactly (Decimal arithmetic, never float, for money).
2. No negative cash (no implicit leverage) unless config explicitly grants a margin limit.
3. Position limits: |position per symbol| ≤ configured cap; gross exposure ≤ cap.
4. Max drawdown circuit-breaker: equity dropping X% from run high-water mark halts trading.
5. Order validity: side/quantity/symbol well-formed; quantity > 0; symbol in tradeable universe; price sanity vs. last quote (reject fills > Y% away).
6. Rate/turnover limit: ≤ N orders per virtual-time window.
7. Idempotent replay: re-applying the recorded fill sequence yields the identical final `PortfolioState` (checked automatically at end of every replay).

Acceptance criteria (Phase 2):

* Each invariant has (a) unit tests for pass and veto cases, (b) at least one Hypothesis property test (e.g. generate random fill sequences; cash conservation must hold or veto).
* A deliberately hostile fake agent (`tests/fakes/rogue_agent.py`) that attempts oversized orders, negative quantities, and unknown symbols is fully contained: zero invalid fills reach the portfolio.

## 5. Phase 3 — Market replay simulator and paper fills

Simple, honest, documented: a bar-replay simulator over bundled historical OHLCV snapshots (a few liquid US tickers, ~6–12 months of daily bars, stored as CSV/Parquet in `benchmarks/data/` with provenance noted). Fills execute at next-bar open with configurable slippage (default: fixed bps) and fees. Explicitly document in the README what this does NOT model (intrabar paths, order books, market impact) — overstating fidelity is the classic backtest sin and reviewers will look for this disclaimer. Virtual clock advances per bar; the agent is invoked per decision point.

Acceptance criteria: golden-file test — a scripted deterministic agent over the bundled data produces an exact known final state; property test that portfolio accounting is path-independent for commutative fill reorderings where applicable (or document why not).

## 6. Phase 4 — Seeded-fault benchmark (the credibility core)

Build `tradewind bench`: runs a matrix of fault injections against the harness and reports catch rates. Fault families (≥3 scenarios each, ≥15 total):

* F1 Hallucinated instruments: agent orders symbols outside the universe.
* F2 Accounting attacks: actions that would create or destroy cash if applied naively.
* F3 Limit breaches: position/exposure/drawdown violations, including slow-bleed paths that only trip the high-water-mark check.
* F4 Nondeterminism injection: an adapter that sneaks a wall-clock read or unseeded RNG into the loop — the harness's determinism checks must flag replay divergence.
* F5 Trace tampering: modified traces must fail verification.

Faults are implemented as misbehaving fake agents / corrupted inputs, driven by recorded traces so the benchmark itself is deterministic and runs in CI with no API keys. Output: a markdown + JSON results table (fault, expected detection, detected?, mechanism, event seq). Target: 100% catch rate on F1–F3/F5 by construction; report honestly whatever F4 achieves. If any fault is not caught, do not weaken the fault — fix the harness or document the limitation explicitly in the README's Limitations section.

## 7. Phase 5 — Evaluation reports

`tradewind report trace.jsonl` renders: run config; equity curve (SVG, no JS dependency); per-decision table linking each fill back through `parent_seq` to the agent messages and LLM calls that produced it; all violations with evidence; token/cost totals per agent role; determinism attestation (chain hash, replay-verified flag). Also `tradewind diff traceA traceB`: aligned diff of two runs (e.g. same config, different model) showing where decisions diverged first. Output: single self-contained HTML file + machine-readable JSON.

## 8. Phase 6 — TradingAgents adapter + MCP server

Adapter: instantiate TradingAgents' LangGraph with Tradewind's boundaries injected (patch its LLM client construction at the adapter seam — document exactly how, after reading the pinned version's code). Capture inter-agent messages (analyst reports, researcher debate, trader decision, risk review) as `agent_message` events with roles, so reports show the full deliberation. Map its final trade decision into a `ProposedAction`. If the pinned version makes injection genuinely impossible somewhere, record the workaround in `DECISIONS.md` rather than modifying the submodule.

MCP server: expose tools `run_evaluation`, `replay_trace`, `verify_trace`, `get_report`, `list_invariants` so any MCP client can drive the harness. Keep it a thin layer over the CLI-level API.

Acceptance criteria: one end-to-end example in `examples/`: record a short TradingAgents run (few decision points, cheap model, key read from env), replay it byte-identically without network, generate the report. Commit the recorded trace so reviewers can replay without any API key.

## 9. Engineering standards (apply throughout)

* Money in `Decimal`; document rounding rules once, in one module.
* Type hints everywhere; `mypy --strict` clean on `src/tradewind/`; ruff clean.
* Every module docstring states its contract; invariants additionally state the formal property they enforce.
* Tests are first-class: aim ≥85% coverage on `trace/`, `invariants/`, `sim/`; CI (GitHub Actions) runs lint + type-check + tests + benchmark on every push, no secrets required.
* Small, conventional commits per logical unit (`feat(trace): ...`); never commit API keys; `.env.example` provided.
* Errors are loud and specific: `ReplayDivergence`, `InvariantViolation`, `TraceIntegrityError` — no bare excepts, no silent fallbacks. In this project a silent fallback is itself a correctness bug.

## 10. README and writeup (do this last, budget real effort)

The README is the product for a portfolio reviewer. Include: the one-paragraph thesis (agents propose, the harness disposes); a 60-second quickstart using the committed example trace (no API key needed); the architecture diagram (ASCII fine); the invariant list with formal statements; the benchmark results table; an explicit Limitations section (bar-level sim fidelity, single-threaded v1, F4 caveats, TradingAgents version pinning); and a short "Design decisions" section linking `DECISIONS.md`. Tone: precise and honest, no marketing superlatives.

## 11. Phase order, checkpoints, and definition of done

Build strictly in order: 1 trace → 2 invariants → 3 sim → 4 benchmark → 5 reports → 6 adapter+MCP → README. Phases 1–4 are the project's substance; if time pressure forces cuts, cut Phase 6's MCP server before anything else. At the end of each phase: run the full test suite, update `DECISIONS.md`, commit, and print a checkpoint summary (what was built, what passed, what's deferred).

Definition of done: CI green; benchmark table generated and committed; the end-to-end TradingAgents example replays byte-identically from the committed trace on a clean clone with no API keys; README complete with Limitations.
