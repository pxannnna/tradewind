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
