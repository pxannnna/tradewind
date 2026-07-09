"""TradingAgents adapter: pure mapping layer (no heavy deps required)."""

from decimal import Decimal
from pathlib import Path

import pytest

from tests.helpers import make_header
from tradewind.adapters.tradingagents.mapping import (
    action_from_signal,
    deliberation_from_final_state,
    record_deliberation,
    request_from_messages,
)
from tradewind.invariants.domain import Side
from tradewind.trace.writer import TraceWriter


def test_request_from_messages_shapes_llmrequest() -> None:
    request = request_from_messages(
        "gpt-4o-mini", [("system", "be brief"), ("user", "assess AAPL")], temperature=0.2
    )
    assert request.model == "gpt-4o-mini"
    assert request.messages == [
        {"role": "system", "content": "be brief"},
        {"role": "user", "content": "assess AAPL"},
    ]
    assert request.temperature == 0.2
    assert len(request.hash()) == 64


def test_deliberation_extraction_order_and_roles() -> None:
    final_state = {
        "market_report": "M",
        "sentiment_report": "S",
        "news_report": "N",
        "fundamentals_report": "F",
        "investment_debate_state": {
            "bull_history": "BULL",
            "bear_history": "BEAR",
            "judge_decision": "JUDGE",
        },
        "trader_investment_plan": "PLAN",
        "risk_debate_state": {"judge_decision": "RISK"},
        "final_trade_decision": "Buy — sized conservatively",
    }
    deliberation = deliberation_from_final_state(final_state)
    assert [role for role, _ in deliberation] == [
        "market_analyst",
        "sentiment_analyst",
        "news_analyst",
        "fundamentals_analyst",
        "bull_researcher",
        "bear_researcher",
        "research_manager",
        "trader",
        "risk_manager",
        "risk_judge",
    ]
    assert deliberation[-1][1] == "Buy — sized conservatively"


def test_deliberation_skips_missing_sections() -> None:
    # Deselected analysts / absent debates are skipped, never invented.
    deliberation = deliberation_from_final_state(
        {"market_report": "M", "final_trade_decision": "Hold"}
    )
    assert deliberation == [("market_analyst", "M"), ("risk_judge", "Hold")]


def test_record_deliberation_chains_parent_seq(tmp_path: Path) -> None:
    with TraceWriter(tmp_path / "t.jsonl", make_header()) as writer:
        events = record_deliberation(
            writer, [("market_analyst", "M"), ("trader", "PLAN")], parent_seq=None
        )
    assert [e.event_type for e in events] == ["agent_message", "agent_message"]
    assert events[0].parent_seq is None
    assert events[1].parent_seq == events[0].seq  # causal chain
    assert events[1].payload["role"] == "trader"


@pytest.mark.parametrize(
    ("signal", "side", "quantity"),
    [
        ("Buy", Side.BUY, Decimal("10")),
        ("buy", Side.BUY, Decimal("10")),
        ("Overweight", Side.BUY, Decimal("5")),
        ("Underweight", Side.SELL, Decimal("5")),
        ("Sell", Side.SELL, Decimal("10")),
        (" SELL ", Side.SELL, Decimal("10")),
    ],
)
def test_action_from_signal_five_tiers(signal: str, side: Side, quantity: Decimal) -> None:
    action = action_from_signal(signal, "AAPL", Decimal("10"))
    assert action is not None
    assert action.side is side
    assert action.quantity == quantity
    assert action.symbol == "AAPL"


def test_action_from_signal_hold_is_no_action() -> None:
    assert action_from_signal("Hold", "AAPL", Decimal("10")) is None


def test_action_from_signal_rejects_garbage() -> None:
    with pytest.raises(ValueError, match="unrecognised trade signal"):
        action_from_signal("YOLO", "AAPL", Decimal("10"))
