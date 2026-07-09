"""End-to-end example: record → replay byte-identically → report. No API key.

Run:  python examples/tradingagents_e2e.py

This drives the exact pipeline the TradingAgents adapter uses — role-tagged
LLM calls through `LLMBoundary`, the deliberation captured as `agent_message`
events via the adapter's mapping functions, the 5-tier signal mapped to a
`ProposedAction`, and the invariant engine ruling on it before a paper fill —
then proves the committed trace replays byte-identically with zero network
access and renders the evaluation report from it.

The LLM here is a deterministic scripted stand-in, so the committed trace is
reproducible and reviewable without any key. Recording a run of the *real*
TradingAgentsGraph uses `tradewind.adapters.tradingagents.adapter.run_tradingagents`
and additionally requires `pip install -e third_party/TradingAgents`,
`pip install 'tradewind[tradingagents]'`, and a provider API key — the trace
format and every step below are identical either way.
"""

import sys
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from tradewind.adapters.tradingagents.mapping import (
    action_from_signal,
    deliberation_from_final_state,
    record_deliberation,
    request_from_messages,
)
from tradewind.invariants.checks import default_invariants
from tradewind.invariants.domain import MarketContext, RiskConfig
from tradewind.invariants.engine import InvariantEngine, record_evaluation
from tradewind.invariants.portfolio import PortfolioState
from tradewind.report.model import build_report_model
from tradewind.report.render import render_report_html
from tradewind.trace.boundaries import (
    LLMBoundary,
    LLMRequest,
    LLMResponse,
    Mode,
    ReplayIndex,
)
from tradewind.trace.events import TraceHeader
from tradewind.trace.reader import read_trace, verify_trace
from tradewind.trace.writer import TraceWriter

HERE = Path(__file__).resolve().parent
TRACE = HERE / "traces" / "tradingagents_demo.jsonl"
SYMBOL = "AAPL"
TRADE_DATE = "2024-01-02"

#: (role, prompt topic) for each deliberation step, in graph order.
ROLES = ["market_analyst", "news_analyst", "trader", "risk_judge"]


class ScriptedLLM:
    """Deterministic stand-in provider: response is a pure function of input."""

    def complete(self, request: LLMRequest) -> LLMResponse:
        role = request.messages[0]["content"].split(":", 1)[0]
        return LLMResponse(
            content=f"[{role}] scripted analysis of {SYMBOL} for {TRADE_DATE} "
            f"(request {request.hash()[:8]})",
            prompt_tokens=120,
            completion_tokens=45,
            cost_estimate="0.0012",
        )


class ConstTimer:
    """Constant latency so the committed recording is byte-reproducible."""

    def start(self) -> float:
        return 0.0

    def elapsed_ms(self, start_marker: float) -> int:
        return 3


def drive(writer: TraceWriter, llm: LLMBoundary) -> None:
    """One decision point, exactly as the adapter shapes it."""
    responses = {}
    for role in ROLES:
        request = request_from_messages(
            "scripted-model",
            [("user", f"{role}: assess {SYMBOL} as of {TRADE_DATE}")],
            temperature=0.0,
        )
        response, _ = llm.complete(request, role=role)
        responses[role] = response.content

    final_state = {
        "market_report": responses["market_analyst"],
        "news_report": responses["news_analyst"],
        "trader_investment_plan": responses["trader"],
        "final_trade_decision": f"Buy — {responses['risk_judge']}",
    }
    events = record_deliberation(writer, deliberation_from_final_state(final_state))
    action = action_from_signal("Buy", SYMBOL, Decimal("10"))
    assert action is not None
    action_event = writer.append("proposed_action", action.to_payload(), parent_seq=events[-1].seq)

    # The harness disposes: full invariant pipeline before any fill.
    risk = RiskConfig(
        max_position_per_symbol=Decimal("100"),
        max_gross_exposure=Decimal("100000"),
        max_drawdown=Decimal("0.25"),
        price_sanity_pct=Decimal("0.10"),
        max_orders_per_window=10,
        rate_window_seconds=3600,
    )
    ctx = MarketContext(
        now=datetime(2024, 1, 3, 14, 30, tzinfo=UTC),
        last_price={SYMBOL: Decimal("185.50")},
        tradeable_universe=frozenset({SYMBOL}),
    )
    state = PortfolioState.initial(Decimal("10000"))
    result = InvariantEngine(default_invariants(risk)).evaluate(state, action, ctx)
    check_events = record_evaluation(writer, result, parent_seq=action_event.seq)
    if not result.blocked:
        fill = ctx.project_fill(action)
        assert fill is not None
        state.apply_fill(fill)  # conservation-checked
        writer.append("fill", fill.to_payload(), parent_seq=check_events[0].seq)


def header() -> TraceHeader:
    return TraceHeader(
        config_hash="a" * 64,
        code_version="0.1.0",
        model_ids=["scripted-model"],
        submodule_sha="01477f9afb7a47b849ed4c9259d3a9a4738d9fda",
        rng_seed=0,
    )


def main() -> None:
    if not TRACE.exists():
        TRACE.parent.mkdir(parents=True, exist_ok=True)
        with TraceWriter(TRACE, header()) as writer:
            llm = LLMBoundary(Mode.RECORD, writer, provider=ScriptedLLM(), wall_timer=ConstTimer())
            drive(writer, llm)
        print(f"recorded {TRACE}")

    print(f"verify   : {verify_trace(TRACE).final_chain_hash}")

    # Replay the same driver against the recording — no provider attachable.
    _, events = read_trace(TRACE)
    index = ReplayIndex(events)
    replayed = TRACE.with_name("replayed.tmp.jsonl")
    replayed.unlink(missing_ok=True)
    with TraceWriter(replayed, header()) as writer:
        drive(writer, LLMBoundary(Mode.REPLAY, writer, replay_index=index))
    identical = TRACE.read_bytes() == replayed.read_bytes()
    replayed.unlink()
    print(f"replay   : byte-identical = {identical}")
    if not identical:
        raise SystemExit(1)

    report_path = TRACE.with_name("tradingagents_demo_report.html")
    model = build_report_model(TRACE, initial_equity=Decimal("10000"))
    report_path.write_text(render_report_html(model), encoding="utf-8")
    print(
        f"report   : {report_path.name} — {model.proposed} proposed, "
        f"{model.admitted} admitted, replay_verified={model.replay_verified}"
    )


if __name__ == "__main__":
    main()
    sys.exit(0)
