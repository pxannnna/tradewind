# DECISIONS

Running log of decisions the spec left open, per its instruction to prefer
the simpler, more testable option and record it here. Newest at the bottom.

## Phase 0 — Setup

- **TradingAgents pin.** Canonical repository confirmed as
  `https://github.com/TauricResearch/TradingAgents` (the org/repo named in the
  project's paper, arXiv:2412.20138). Pinned as a git submodule at commit
  `01477f9afb7a47b849ed4c9259d3a9a4738d9fda`, which is the tagged release
  `v0.3.1` (verified via `git ls-remote` on 2026-07-07). Its architecture
  matches the spec's description — `tradingagents/agents/{analysts,
  researchers,trader,risk_mgmt,managers}` over a LangGraph — which the Phase 6
  adapter will wrap. The submodule is read-only and is not needed for any
  phase before 6; CI checks out with `submodules: false`.
- **Build tooling.** `uv` + `hatchling`, dev dependencies in a PEP 735
  `[dependency-groups]` table. `uv.lock` is gitignored for now (library-style
  project); revisit if CI flakiness ever warrants a lockfile.

## Phase 1 — Trace capture and deterministic replay

- **Canonical JSON floats.** Finite floats are allowed and serialise via
  CPython's shortest-round-trip `repr`; NaN/Infinity are rejected. Money never
  travels as a float: all monetary quantities in payloads are decimal
  *strings* (rule restated where Decimal arithmetic lands in Phase 2).
- **Chain hash concretely.** `hash_0 = sha256(canonical_json(header))`;
  `hash_n = sha256(ascii(hex(hash_{n-1})) || canonical_json(event_n minus its
  chain_hash field))`. Each event line stores its own `chain_hash`;
  verification recomputes the whole chain and additionally checks each line's
  bytes equal the canonical re-serialisation of the parsed record, so
  re-encodings (added whitespace, `\uXXXX` escapes) are rejected even though
  they parse to the same value.
- **No timestamps in trace records.** The header contains no created-at and
  events carry no wall time, otherwise replay could not be byte-identical.
  Wall time appears in exactly one place: the `latency_ms` measured for a
  *live* provider call in record mode, stored in the event payload and
  replayed verbatim. The only module allowed to import `time` is
  `trace/wallclock.py`, enforced by `tests/test_no_wallclock.py`.
- **Zero-network replay is structural, not behavioural.** A boundary in
  REPLAY mode refuses at construction time to hold a live provider
  (`BoundaryConfigError`), so there is no code path from replay to the
  network; a cache miss raises `ReplayDivergence`.
- **Repeated identical requests.** Replay lookups are per-`request_hash` FIFO
  queues, so a run that issues the same request twice gets both recorded
  responses in their original order; consuming more than recorded raises
  `ReplayDivergence("exhausted …")`.
- **`tradewind replay` semantics in Phase 1.** Until the simulator (Phase 3)
  and adapter (Phase 6) provide runnable programs, the CLI replay re-executes
  the recorded *event stream* through the real writer path (re-validate,
  re-canonicalise, re-hash every event) and asserts byte-identity with the
  recording. Driver-based replay — re-running an actual agent program against
  recorded boundary responses — exists today at the library level and is
  proven in `tests/test_record_replay.py::test_driver_replay_is_byte_identical`
  and `examples/record_replay_demo.py`; the CLI will grow a `--driver` path
  when Phase 3 introduces runnable configurations.
- **Truncation caveat.** The chain hash makes any in-place edit, reorder, or
  middle deletion detectable, but chopping whole lines off the *tail* of a
  trace leaves a shorter, internally-consistent prefix. Detecting tail
  truncation therefore requires comparing the final chain hash against an
  externally stored expectation — `tradewind verify` prints it for exactly
  that purpose, and Phase 5 reports will embed it as the determinism
  attestation.
- **Event `seq` starts at 1** and the header is not an event (it is the chain
  anchor). `parent_seq` must reference a strictly earlier event.
- **Virtual time is timezone-aware UTC** and monotonic (no backwards jumps);
  the default epoch is 2000-01-01T00:00:00Z.

## Phase 2 — Invariant engine

- **One check signature, exactly as specified.** Every invariant is a frozen,
  configured object with `check(state, action, ctx) -> Verdict`. Risk
  thresholds live in the invariant instances (`RiskConfig` builds the
  pipeline), market *facts* in `MarketContext`, microstructure (fees,
  slippage) in `MarketModel`, and the portfolio in `PortfolioState`. This
  keeps each argument a single clear concern.
- **Projection is total, not raising.** `MarketContext.project_fill` returns
  `Fill | None`; an unprojectable action (unknown symbol, no quote, qty ≤ 0 or
  non-finite) yields `None`. Economic invariants return `PASS` on `None` and
  let `OrderValidity` own that veto, so a single bad action produces one clear
  violation rather than a pile-up of redundant ones.
- **Money never rounds in the accounting path.** All amounts are exact
  `Decimal`; `to_decimal` rejects `float` and `bool`. The conservation identity
  `cash_delta + price·position_delta == -fees` therefore holds with a zero
  residual, proven by a Hypothesis property over random fills. The one place
  rounding is *defined* is `tradewind.money` (`quantize_cents`/`quantize_qty`),
  used only at I/O boundaries.
- **Two conservation surfaces, one law.** Cash conservation appears in the
  pipeline (`CashConservation`, always `PASS` for an honest projection) *and*
  is hard-enforced at fill application: `PortfolioState.apply_fill` calls
  `verify_fill_consistency`, which raises `InvariantViolation` on a tampered
  fill. The Verdict form (`check_fill_conservation`) gives the F2 benchmark a
  reachable veto without applying the fill.
- **Drawdown is a circuit breaker.** Once equity ≤ `high_water_mark ×
  (1 − max_drawdown)`, `DrawdownBreaker` vetoes *every* action regardless of
  its content — a halt, not a per-order check. The high-water mark is raised
  only by `PortfolioState.mark`, called per bar in Phase 3.
- **Idempotent replay = a pure fold.** `replay_fills` is referentially
  transparent (invariant 7); re-applying a fill sequence yields the identical
  final state, checked in `test_portfolio.py`.
- **Removed a dead `isinstance(side, Side)` guard.** `ProposedAction.side` is a
  `Side` enum by construction (`from_payload` raises on an unknown side before
  an action can exist), so re-validating it is unreachable under the type
  system; `OrderValidity` covers the real malformed-order classes instead.
## Phase 3 — Market replay simulator and paper fills

- **Bundled data is SYNTHETIC, and says so.** With no reliable free market-data
  source available offline (and paid APIs disallowed), the bundled OHLCV is a
  seeded geometric random walk generated by `benchmarks/data/generate.py`,
  clearly labelled in `benchmarks/data/PROVENANCE.md`. Shipping honest
  synthetic data beats bundling real prices of dubious licence and implying a
  fidelity the daily-bar model does not have. The committed CSVs are the source
  of truth; the generator reproduces them byte-for-byte from a fixed seed.
- **Propose at close, vet-and-fill at next open.** Agents decide at a bar's
  close using only data through that bar (`DecisionContext` accessors clamp to
  the current index — no lookahead). Admitted orders execute at the *next*
  bar's open. Crucially, the invariant pipeline runs at *execution* time with
  `MarketContext.last_price` set to the actual open fill price, so the
  invariants vet real execution economics and the resulting portfolio can never
  violate an invariant. Orders proposed on the final bar are dropped (no next
  open) — documented, not silent.
- **Sim traces carry decisions, not bars.** The simulator emits
  `proposed_action → invariant_check → (violation | fill)` events with causal
  `parent_seq` links. It deliberately does *not* emit `data_read` events (those
  carry a `request_hash` for the boundary/replay path and belong to the Phase 6
  adapter); per-bar market data is described by the run config. This keeps
  `ReplayIndex` unambiguous and the decision trace focused.
- **Commutativity scope.** The pure accounting fold (`replay_fills`) is
  permutation-invariant because Decimal addition commutes (property-tested).
  The *gated pipeline* is intentionally order-dependent — the drawdown circuit
  breaker and rate limiter must see orders in sequence — so admission is not
  commutative, and we assert commutativity only for the accounting fold.
- **`tradewind run` is now live.** It drives a built-in scripted agent
  (`buy_and_hold` or `sma`) over bundled bars, offline and deterministic, and
  with `--out` writes a chain-hashed decision trace that it verifies. Real
  LLM-agent runs arrive with the Phase 6 adapter; the scripted path exists so
  the sim is exercisable end-to-end today.
- **Fixed intraday stamps.** Events get virtual timestamps from
  `datetime.combine(day, OPEN_TIME|CLOSE_TIME)` at fixed UTC times; no wall
  clock is read (still enforced by `test_no_wallclock.py`).

## Phase 4 — Seeded-fault benchmark

- **Scenarios observe, they don't assert.** Every one of the 17 scenarios runs
  its fault against the *real* engine/portfolio/trace code and measures whether
  it was caught (and by which mechanism / event seq). Nothing is hard-coded to
  "detected", so a regression that breaks a guard turns that row red. 17
  scenarios span F1(3)/F2(3)/F3(4)/F4(3)/F5(4) — ≥3 per family, ≥15 total.
- **F4 uses a counter stand-in in `src`, real nondeterminism in `tests`.** The
  no-wallclock lint forbids `time`/`random` anywhere in `src/tradewind`, so the
  benchmark models a drifting value with a process-lifetime counter that is not
  reset between the record and replay runs — faithfully reproducing how an
  unseeded RNG or wall-clock read makes a reissued request hash differ and trip
  `ReplayDivergence`. `tests/test_bench.py` additionally proves the identical
  catch against genuine `random.Random()` and `time.perf_counter_ns`. This
  keeps the benchmark deterministic and CI-safe while still demonstrating the
  real mechanism.
- **F4's honest limit, stated in the report and README.** The determinism check
  can only catch nondeterminism that influences a recorded boundary request or
  the event stream; nondeterminism that never changes a recorded byte is
  invisible by construction. The three F4 scenarios (drift in an LLM request,
  drift in a data request, an extra unrecorded call) all manifest that way, so
  all three are caught — but the caveat is documented, not hidden.
- **Committed, reproducible results.** `tradewind bench --out benchmarks/results`
  writes `results.md` + `results.json`, byte-identical across runs (volatile
  temp-dir paths are stripped from evidence strings). `bench` exits 1 if any
  critical family (F1/F2/F3/F5) is not fully caught — an honest failure, not a
  green light. Current result: **17/17 caught**.
- **Slow-bleed fixture.** `benchmarks/data/bench/DECLINE.csv` is a hand-authored
  steady decline (symbol `DECLINE`, 100 → 25) so a buy-and-bleed agent trips the
  drawdown circuit breaker at a known bar; the scenario cites the first
  `drawdown_breaker` violation's event seq from the recorded trace.

## Phase 5 — Evaluation reports

- **Reports are reconstructed from the trace, not from run state.**
  `build_report_model` verifies the trace, then rebuilds the decision chain
  purely from events and their `parent_seq` links, and attempts a byte-identical
  replay to set the determinism-attestation flag. It invents nothing the trace
  does not contain — so the report is an audit, not a re-narration.
- **Equity curve is honest about being marks-only.** The trace records fills,
  not per-bar marks, so the curve is cumulative portfolio value *at fill
  prices* (each position marked at its most recent trade). With `--initial-cash`
  it is absolute equity; without, it is P&L from zero. The report labels it
  "marks at fill prices" so the gaps between trades are never oversold, and no
  initial-capital figure has to be smuggled into the trace.
- **Self-contained HTML via autoescaping Jinja2.** Output is one file: inline
  CSS, an inline dependency-free SVG chart, no `<script>`, no external URLs — it
  opens offline anywhere. Autoescaping is on, and a test feeds a
  `<img onerror=…>` symbol through to prove hostile trace text renders as inert
  text, not markup (LLM output is untrusted).
- **Optional `role` tag on LLM calls.** `LLMBoundary.complete(..., role=...)`
  writes a `role` key into the `llm_call` payload when given (default `None`
  writes nothing, so existing traces and their hashes are unchanged). The report
  totals token/cost by that role, falling back to the model id, then
  "unattributed". The Phase 6 adapter will tag each call with its agent role.
- **`diff` aligns by seq on `(event_type, canonical payload)`** and reports the
  first divergent seq — enough to show where two runs (e.g. same config, two
  models) first parted ways. Both `report` and `diff` emit HTML + JSON.
- **render.py is E501-exempt.** The HTML/CSS templates are long inline strings
  that cannot be line-wrapped without corrupting output; ruff's line-length rule
  is disabled for that one file only.

- **Tooling scope.** ruff excludes `third_party/` (`extend-exclude`) and mypy's
  `files` targets only `src/tradewind`, so neither ever touches the read-only
  submodule. CI checks out without submodules, so lint/type/test/benchmark all
  run with no TradingAgents dependency and no secrets.
- **String-only object keys.** `canonical_json` rejects non-string dict keys
  (`NotCanonicalisable`) because `json.dumps` would otherwise coerce e.g.
  `{1: x}` to `{"1": x}`, letting two distinct inputs canonicalise identically
  — a determinism-breaking hash collision.
- **Record returns what replay returns.** In RECORD mode each boundary passes
  its freshly-built payload through the same canonical-JSON round-trip that
  REPLAY uses to reconstruct it, so a driver consuming a response (e.g.
  formatting a data dict into a prompt) sees byte-identical inputs in both
  modes. Without this, nested-dict key ordering could differ between record
  and replay and silently change a request hash.
