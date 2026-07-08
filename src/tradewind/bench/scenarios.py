"""Fault scenarios: each one injects a fault and observes the harness's response.

Contract: nothing here asserts detection — every scenario runs the fault
against the real engine/portfolio/trace code and *measures* whether it was
caught, so a regression that breaks a guard turns the corresponding row red.
The module is deterministic and offline (no wall clock, no RNG, no network);
F4 models a drifting value with a process-lifetime counter, documented below.
"""

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory

from tradewind.bench.report import FaultResult
from tradewind.errors import InvariantViolation, ReplayDivergence, TraceIntegrityError
from tradewind.invariants.checks import default_invariants
from tradewind.invariants.domain import (
    Fill,
    MarketContext,
    MarketModel,
    ProposedAction,
    RiskConfig,
    Side,
)
from tradewind.invariants.engine import EvaluationResult, InvariantEngine, record_evaluation
from tradewind.invariants.portfolio import PortfolioState, check_fill_conservation
from tradewind.sim import SimConfig, Simulator, load_price_data
from tradewind.sim.simulator import Agent, DecisionContext
from tradewind.trace.boundaries import (
    DataBoundary,
    DataRequest,
    DataResponse,
    LLMBoundary,
    LLMRequest,
    LLMResponse,
    Mode,
    ReplayIndex,
)
from tradewind.trace.events import TraceHeader
from tradewind.trace.reader import read_trace, verify_trace
from tradewind.trace.wallclock import WallTimer
from tradewind.trace.writer import TraceWriter

_DECLINE_DATA = Path(__file__).resolve().parents[3] / "benchmarks" / "data" / "bench"
_NOW = datetime(2024, 1, 2, tzinfo=UTC)
_UNIVERSE = frozenset({"AAPL"})


def _short(message: str, limit: int = 90) -> str:
    single_line = " ".join(message.split())
    return single_line if len(single_line) <= limit else single_line[: limit - 1] + "…"


def _bench_header() -> TraceHeader:
    return TraceHeader(config_hash="b" * 64, code_version="0.1.0", model_ids=["bench"], rng_seed=0)


class _ConstTimer(WallTimer):
    """Constant-latency timer so bench recordings never touch the wall clock."""

    def start(self) -> float:
        return 0.0

    def elapsed_ms(self, start_marker: float) -> int:
        return 0


class _GenerousRisk:
    """Risk config presets that isolate one invariant at a time."""

    @staticmethod
    def base(**overrides: object) -> RiskConfig:
        fields: dict[str, object] = {
            "max_position_per_symbol": Decimal("1000000"),
            "max_gross_exposure": Decimal("1000000000"),
            "max_drawdown": Decimal("0.99"),
            "price_sanity_pct": Decimal("0.50"),
            "max_orders_per_window": 1000,
            "rate_window_seconds": 86400,
            "margin_limit": Decimal("0"),
        }
        fields.update(overrides)
        return RiskConfig(**fields)  # type: ignore[arg-type]


def _market(**overrides: object) -> MarketContext:
    fields: dict[str, object] = {
        "now": _NOW,
        "last_price": {"AAPL": Decimal("100")},
        "tradeable_universe": _UNIVERSE,
        "model": MarketModel(),
    }
    fields.update(overrides)
    return MarketContext(**fields)  # type: ignore[arg-type]


def _seq_for_result(result: EvaluationResult) -> int | None:
    """Record a result to a throwaway trace; return the first violation's seq."""
    if not result.vetoes:
        return None
    with TemporaryDirectory(prefix="tw-bench-") as tmp:
        with TraceWriter(Path(tmp) / "t.jsonl", _bench_header()) as writer:
            action_event = writer.append("proposed_action", {"stub": "bench"})
            events = record_evaluation(writer, result, parent_seq=action_event.seq)
        return events[1].seq


def _pipeline_scenario(
    fault_id: str,
    scenario: str,
    description: str,
    expected: str,
    action: ProposedAction,
    state: PortfolioState,
    ctx: MarketContext,
    risk: RiskConfig,
    expected_invariant: str,
) -> FaultResult:
    """Run one action through the pipeline and report which invariant vetoed."""
    result = InvariantEngine(default_invariants(risk)).evaluate(state, action, ctx)
    matched = [v for v in result.vetoes if v.invariant == expected_invariant]
    detected = result.blocked and bool(matched)
    mechanism = (
        _short(f"VETO {matched[0].invariant}: {matched[0].reason}")
        if matched
        else ("blocked by other invariant" if result.blocked else "NOT DETECTED")
    )
    return FaultResult(
        fault_id, scenario, description, expected, detected, mechanism, _seq_for_result(result)
    )


# --- F1 Hallucinated instruments -------------------------------------------


def f1_scenarios() -> list[FaultResult]:
    """F1: orders for symbols outside the tradeable universe."""
    state = PortfolioState.initial(Decimal("100000"))
    hallucinations = [
        ("unknown ticker TSLA", "AAPL-only universe; agent orders TSLA", "TSLA"),
        ("meme instrument", "agent orders a symbol that never existed", "DOGE-9000"),
        ("lookalike symbol", "near-miss of a real symbol, still off-universe", "AAPL.US"),
    ]
    return [
        _pipeline_scenario(
            "F1",
            name,
            desc,
            "VETO order_validity",
            ProposedAction(symbol, Side.BUY, Decimal("1")),
            state,
            _market(),
            _GenerousRisk.base(),
            "order_validity",
        )
        for (name, desc, symbol) in hallucinations
    ]


# --- F2 Accounting attacks -------------------------------------------------


def _fill_attack(scenario: str, description: str, tampered: Fill) -> FaultResult:
    """Run a fabricated, non-balancing fill and confirm the harness rejects it."""
    verdict = check_fill_conservation(tampered)
    raised = False
    try:
        PortfolioState.initial(Decimal("100000")).apply_fill(tampered)
    except InvariantViolation:
        raised = True
    detected = verdict.is_veto and raised
    seq = _seq_for_result(EvaluationResult((verdict,))) if verdict.is_veto else None
    mechanism = (
        _short(f"VETO {verdict.invariant} + apply_fill raised") if detected else "NOT DETECTED"
    )
    return FaultResult(
        "F2", scenario, description, "VETO cash_conservation", detected, mechanism, seq
    )


def f2_scenarios() -> list[FaultResult]:
    """F2: fills/actions that would create or destroy cash if applied naively."""
    # A "buy" that credits cash instead of debiting it (conjures money).
    conjure = Fill(
        symbol="AAPL",
        side=Side.BUY,
        quantity=Decimal("10"),
        price=Decimal("100"),
        fees=Decimal("0"),
        cash_delta=Decimal("1000"),
        position_delta=Decimal("10"),
    )
    # A fill claiming more shares than its price/qty imply.
    phantom_shares = Fill(
        symbol="AAPL",
        side=Side.BUY,
        quantity=Decimal("10"),
        price=Decimal("100"),
        fees=Decimal("0"),
        cash_delta=Decimal("-1000"),
        position_delta=Decimal("1000"),
    )
    overdraw = _pipeline_scenario(
        "F2",
        "overdraw cash",
        "buy far beyond available cash (would go negative)",
        "VETO no_negative_cash",
        ProposedAction("AAPL", Side.BUY, Decimal("100")),
        PortfolioState.initial(Decimal("1000")),
        _market(),
        _GenerousRisk.base(),
        "no_negative_cash",
    )
    return [
        _fill_attack("conjured cash", "fabricated buy that credits cash", conjure),
        _fill_attack("phantom shares", "fill books more shares than it pays for", phantom_shares),
        overdraw,
    ]


# --- F3 Limit breaches -----------------------------------------------------


_DECLINE_SYMBOL = "DECLINE"


def _bleed_agent(ctx: DecisionContext) -> Sequence[ProposedAction]:
    """Open a large position, then keep nibbling — trades into a drawdown halt."""
    if ctx.index == 0:
        return [
            ProposedAction(_DECLINE_SYMBOL, Side.BUY, Decimal("90")),
            ProposedAction(_DECLINE_SYMBOL, Side.BUY, Decimal("1")),
        ]
    return [ProposedAction(_DECLINE_SYMBOL, Side.BUY, Decimal("1"))]


def _sim_first_violation(
    config: SimConfig, agent: Agent, invariant_name: str
) -> tuple[bool, int | None, str]:
    prices = load_price_data(_DECLINE_DATA, config.universe)
    with TemporaryDirectory(prefix="tw-bench-") as tmp:
        path = Path(tmp) / "sim.jsonl"
        with TraceWriter(path, _bench_header()) as writer:
            Simulator(prices, config).run(agent, writer=writer)
        _, events = read_trace(path)
        for event in events:
            if event.event_type == "violation" and event.payload.get("invariant") == invariant_name:
                return True, event.seq, str(event.payload.get("reason", ""))
    return False, None, ""


def f3_scenarios() -> list[FaultResult]:
    """F3: position, gross-exposure, turnover, and slow-bleed drawdown breaches."""
    oversized = _pipeline_scenario(
        "F3",
        "oversized position",
        "single order over the per-symbol cap",
        "VETO position_limit",
        ProposedAction("AAPL", Side.BUY, Decimal("10")),
        PortfolioState.initial(Decimal("1000000")),
        _market(),
        _GenerousRisk.base(max_position_per_symbol=Decimal("5")),
        "position_limit",
    )
    gross = _pipeline_scenario(
        "F3",
        "gross exposure",
        "order within per-symbol cap but over gross cap",
        "VETO position_limit",
        ProposedAction("AAPL", Side.BUY, Decimal("10")),
        PortfolioState.initial(Decimal("1000000")),
        _market(),
        _GenerousRisk.base(max_gross_exposure=Decimal("500")),
        "position_limit",
    )
    rate = _pipeline_scenario(
        "F3",
        "turnover rate",
        "order exceeding the per-window turnover cap",
        "VETO rate_limit",
        ProposedAction("AAPL", Side.BUY, Decimal("1")),
        PortfolioState(
            cash=Decimal("1000000"),
            positions={},
            recent_order_times=(_NOW, _NOW - timedelta(seconds=10), _NOW - timedelta(seconds=20)),
        ),
        _market(),
        _GenerousRisk.base(max_orders_per_window=3, rate_window_seconds=3600),
        "rate_limit",
    )
    # Slow-bleed: equity drifts below the high-water mark until the breaker halts.
    bleed_config = SimConfig(
        initial_cash=Decimal("10000"),
        universe=frozenset({_DECLINE_SYMBOL}),
        risk=_GenerousRisk.base(max_drawdown=Decimal("0.20")),
        model=MarketModel(),
    )
    detected, seq, reason = _sim_first_violation(bleed_config, _bleed_agent, "drawdown_breaker")
    slow_bleed = FaultResult(
        "F3",
        "slow-bleed drawdown",
        "position bleeds value until the drawdown circuit-breaker halts trading",
        "VETO drawdown_breaker (circuit breaker)",
        detected,
        _short(f"VETO drawdown_breaker: {reason}") if detected else "NOT DETECTED",
        seq,
    )
    return [oversized, gross, rate, slow_bleed]


# --- F4 Nondeterminism injection -------------------------------------------


class _DriftSource:
    """A process-lifetime counter standing in for a wall-clock/RNG read.

    Its value is not a function of any request, and it is NOT reset between the
    record and replay runs — so an identical request gets a different value the
    second time, exactly as an unseeded RNG or a wall-clock read would. This
    models F4 without importing the time or random modules into core code (which
    the no-wallclock lint forbids); ``tests/test_bench.py`` proves the same catch
    against genuine wall-clock and RNG sources.
    """

    def __init__(self) -> None:
        self._n = 0

    def next(self) -> str:
        self._n += 1
        return str(self._n)


class _ConstLLM:
    def complete(self, request: LLMRequest) -> LLMResponse:
        return LLMResponse(content="ok")


class _ConstData:
    def read(self, request: DataRequest) -> DataResponse:
        return DataResponse(data={"ok": True})


def _drifting_llm_request(value: str) -> LLMRequest:
    return LLMRequest(model="m", messages=[{"role": "user", "content": f"noise={value}"}])


def _f4_llm_drift() -> FaultResult:
    drift = _DriftSource()
    header = _bench_header()
    with TemporaryDirectory(prefix="tw-bench-") as tmp:
        recorded = Path(tmp) / "rec.jsonl"
        with TraceWriter(recorded, header) as writer:
            llm = LLMBoundary(Mode.RECORD, writer, provider=_ConstLLM(), wall_timer=_ConstTimer())
            llm.complete(_drifting_llm_request(drift.next()))
        _, events = read_trace(recorded)
        index = ReplayIndex(events)
        detected, mechanism = False, "NOT DETECTED"
        with TraceWriter(Path(tmp) / "rep.jsonl", header) as writer:
            llm = LLMBoundary(Mode.REPLAY, writer, replay_index=index)
            try:
                llm.complete(_drifting_llm_request(drift.next()))
            except ReplayDivergence as exc:
                detected, mechanism = True, _short(f"ReplayDivergence: {exc}")
    return FaultResult(
        "F4",
        "wall-clock/RNG in LLM request",
        "a value not derived from inputs leaks into an LLM request",
        "ReplayDivergence on replay",
        detected,
        mechanism,
        None,
    )


def _f4_data_drift() -> FaultResult:
    drift = _DriftSource()
    header = _bench_header()
    with TemporaryDirectory(prefix="tw-bench-") as tmp:
        recorded = Path(tmp) / "rec.jsonl"
        with TraceWriter(recorded, header) as writer:
            data = DataBoundary(Mode.RECORD, writer, provider=_ConstData())
            data.read(DataRequest(source="bars", params={"noise": drift.next()}))
        _, events = read_trace(recorded)
        index = ReplayIndex(events)
        detected, mechanism = False, "NOT DETECTED"
        with TraceWriter(Path(tmp) / "rep.jsonl", header) as writer:
            data = DataBoundary(Mode.REPLAY, writer, replay_index=index)
            try:
                data.read(DataRequest(source="bars", params={"noise": drift.next()}))
            except ReplayDivergence as exc:
                detected, mechanism = True, _short(f"ReplayDivergence: {exc}")
    return FaultResult(
        "F4",
        "wall-clock/RNG in data request",
        "a drifting value leaks into a market-data request",
        "ReplayDivergence on replay",
        detected,
        mechanism,
        None,
    )


def _f4_extra_call() -> FaultResult:
    """Structural nondeterminism: replay issues more calls than were recorded."""
    header = _bench_header()
    fixed = _drifting_llm_request("fixed")
    with TemporaryDirectory(prefix="tw-bench-") as tmp:
        recorded = Path(tmp) / "rec.jsonl"
        with TraceWriter(recorded, header) as writer:
            llm = LLMBoundary(Mode.RECORD, writer, provider=_ConstLLM(), wall_timer=_ConstTimer())
            llm.complete(fixed)  # exactly one call recorded
        _, events = read_trace(recorded)
        index = ReplayIndex(events)
        detected, mechanism = False, "NOT DETECTED"
        with TraceWriter(Path(tmp) / "rep.jsonl", header) as writer:
            llm = LLMBoundary(Mode.REPLAY, writer, replay_index=index)
            llm.complete(fixed)  # consumes the one recording
            try:
                llm.complete(fixed)  # a second, unrecorded call
            except ReplayDivergence as exc:
                detected, mechanism = True, _short(f"ReplayDivergence: {exc}")
    return FaultResult(
        "F4",
        "extra call on replay",
        "the loop issues an LLM call that the recording never made",
        "ReplayDivergence on replay",
        detected,
        mechanism,
        None,
    )


def f4_scenarios() -> list[FaultResult]:
    """F4: nondeterminism that manifests as a replay divergence."""
    return [_f4_llm_drift(), _f4_data_drift(), _f4_extra_call()]


# --- F5 Trace tampering ----------------------------------------------------


def _clean_trace(path: Path) -> None:
    with TraceWriter(path, _bench_header()) as writer:
        writer.append("agent_message", {"role": "trader", "content": "hold AAPL"})
        action = writer.append(
            "proposed_action", {"symbol": "AAPL", "side": "buy", "quantity": "10"}
        )
        writer.append("fill", {"symbol": "AAPL", "quantity": "10"}, parent_seq=action.seq)


def _expect_integrity_error(tmp: Path, raw: bytes, tag: str) -> tuple[bool, str]:
    path = tmp / f"tampered-{tag}.jsonl"
    path.write_bytes(raw)
    try:
        verify_trace(path)
    except TraceIntegrityError as exc:
        # Strip the volatile temp-dir prefix so the committed report is stable.
        message = str(exc).replace(str(tmp) + "/", "")
        return True, _short(f"TraceIntegrityError: {message}")
    return False, "NOT DETECTED"


def f5_scenarios() -> list[FaultResult]:
    """F5: tampered traces (edited, deleted, reordered, re-encoded) must fail verification."""
    results: list[FaultResult] = []
    with TemporaryDirectory(prefix="tw-bench-") as tmp_str:
        tmp = Path(tmp_str)
        clean = tmp / "clean.jsonl"
        _clean_trace(clean)
        raw = clean.read_bytes()
        lines = raw.splitlines(keepends=True)

        flipped = raw.replace(b'"quantity":"10"', b'"quantity":"99"', 1)
        d1, m1 = _expect_integrity_error(tmp, flipped, "flip")
        results.append(
            FaultResult(
                "F5",
                "flipped payload byte",
                "one value edited inside an event payload",
                "TraceIntegrityError (chain hash)",
                d1,
                m1,
                None,
            )
        )

        dropped = b"".join(lines[:2] + lines[3:])  # remove one event line
        d2, m2 = _expect_integrity_error(tmp, dropped, "drop")
        results.append(
            FaultResult(
                "F5",
                "deleted event line",
                "a middle event removed from the trace",
                "TraceIntegrityError (seq order)",
                d2,
                m2,
                None,
            )
        )

        reordered = b"".join([lines[0], lines[2], lines[1], *lines[3:]])
        d3, m3 = _expect_integrity_error(tmp, reordered, "reorder")
        results.append(
            FaultResult(
                "F5",
                "reordered events",
                "two event lines swapped",
                "TraceIntegrityError",
                d3,
                m3,
                None,
            )
        )

        noncanon = lines[0] + lines[1].replace(b'"record":', b'"record": ', 1) + b"".join(lines[2:])
        d4, m4 = _expect_integrity_error(tmp, noncanon, "noncanon")
        results.append(
            FaultResult(
                "F5",
                "non-canonical re-encoding",
                "same JSON value, extra whitespace",
                "TraceIntegrityError (not canonical)",
                d4,
                m4,
                None,
            )
        )
    return results


def all_results() -> list[FaultResult]:
    """Every fault scenario, in canonical family order."""
    return [
        *f1_scenarios(),
        *f2_scenarios(),
        *f3_scenarios(),
        *f4_scenarios(),
        *f5_scenarios(),
    ]
