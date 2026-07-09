"""Pure mapping between TradingAgents' shapes and Tradewind's schemas.

Contract: no imports from TradingAgents, LangChain, or any LLM SDK — these
functions work on plain data so they are unit-testable (and replayable)
without the heavy optional dependencies installed.

* :func:`request_from_messages` — provider-agnostic chat messages →
  :class:`~tradewind.trace.boundaries.LLMRequest`.
* :func:`deliberation_from_final_state` — the graph's final state →
  ordered ``(role, content)`` pairs covering the full deliberation
  (analyst reports, researcher debate, trader plan, risk review, decision).
* :func:`record_deliberation` — append those as ``agent_message`` events.
* :func:`action_from_signal` — the 5-tier rating (``Buy / Overweight /
  Hold / Underweight / Sell``) → a :class:`ProposedAction` (``None`` for
  Hold; Overweight/Underweight scale to half size — see DECISIONS.md).
"""

from collections.abc import Mapping
from decimal import Decimal
from typing import Any

from tradewind.invariants.domain import ProposedAction, Side
from tradewind.trace.boundaries import LLMRequest
from tradewind.trace.events import TraceEvent
from tradewind.trace.writer import TraceWriter

#: Final-state keys → the agent role that authored them, in deliberation order.
_STATE_ROLES: tuple[tuple[str, str], ...] = (
    ("market_report", "market_analyst"),
    ("sentiment_report", "sentiment_analyst"),
    ("news_report", "news_analyst"),
    ("fundamentals_report", "fundamentals_analyst"),
    ("trader_investment_plan", "trader"),
    ("final_trade_decision", "risk_judge"),
)

#: Nested debate-state keys → roles.
_DEBATE_ROLES: tuple[tuple[str, str, str], ...] = (
    ("investment_debate_state", "bull_history", "bull_researcher"),
    ("investment_debate_state", "bear_history", "bear_researcher"),
    ("investment_debate_state", "judge_decision", "research_manager"),
    ("risk_debate_state", "judge_decision", "risk_manager"),
)

#: 5-tier rating → (side, size fraction). Hold maps to no action.
_SIGNAL_MAP: dict[str, tuple[Side, Decimal]] = {
    "buy": (Side.BUY, Decimal(1)),
    "overweight": (Side.BUY, Decimal("0.5")),
    "underweight": (Side.SELL, Decimal("0.5")),
    "sell": (Side.SELL, Decimal(1)),
}


def request_from_messages(
    model: str,
    messages: list[tuple[str, str]],
    temperature: float | None = None,
    params: Mapping[str, Any] | None = None,
) -> LLMRequest:
    """Build an :class:`LLMRequest` from ``(role, content)`` chat messages."""
    return LLMRequest(
        model=model,
        messages=[{"role": role, "content": content} for role, content in messages],
        temperature=temperature,
        params=dict(params or {}),
    )


def deliberation_from_final_state(final_state: Mapping[str, Any]) -> list[tuple[str, str]]:
    """Extract the full deliberation as ordered ``(role, content)`` pairs.

    Missing or empty sections are skipped (an analyst may be deselected), never
    invented. Order follows the graph's own flow: analysts → researcher debate
    → trader → risk review → final decision.
    """
    deliberation: list[tuple[str, str]] = []
    flat = dict(_STATE_ROLES)
    for key, role in _STATE_ROLES[:4]:  # the four analyst reports
        content = final_state.get(key)
        if content:
            deliberation.append((role, str(content)))
    for state_key, inner_key, role in _DEBATE_ROLES[:3]:  # researcher debate
        inner = final_state.get(state_key) or {}
        content = inner.get(inner_key) if isinstance(inner, Mapping) else None
        if content:
            deliberation.append((role, str(content)))
    trader_plan = final_state.get("trader_investment_plan")
    if trader_plan:
        deliberation.append((flat["trader_investment_plan"], str(trader_plan)))
    risk_state = final_state.get("risk_debate_state") or {}
    if isinstance(risk_state, Mapping) and risk_state.get("judge_decision"):
        deliberation.append(("risk_manager", str(risk_state["judge_decision"])))
    decision = final_state.get("final_trade_decision")
    if decision:
        deliberation.append((flat["final_trade_decision"], str(decision)))
    return deliberation


def record_deliberation(
    writer: TraceWriter,
    deliberation: list[tuple[str, str]],
    parent_seq: int | None = None,
) -> list[TraceEvent]:
    """Append the deliberation as chained ``agent_message`` events.

    Each message's ``parent_seq`` points at the previous one (the first at
    ``parent_seq``), so reports can walk the whole conversation causally.
    """
    events: list[TraceEvent] = []
    previous = parent_seq
    for role, content in deliberation:
        event = writer.append(
            "agent_message", {"role": role, "content": content}, parent_seq=previous
        )
        events.append(event)
        previous = event.seq
    return events


def action_from_signal(signal: str, symbol: str, full_quantity: Decimal) -> ProposedAction | None:
    """Map the 5-tier rating to a proposed order (``None`` for Hold).

    ``Buy``/``Sell`` trade ``full_quantity``; ``Overweight``/``Underweight``
    trade half of it (exact Decimal, no rounding). An unrecognised signal
    raises — a malformed decision must never silently become an order.
    """
    normalised = signal.strip().lower()
    if normalised == "hold":
        return None
    if normalised not in _SIGNAL_MAP:
        raise ValueError(
            f"unrecognised trade signal {signal!r}; expected one of "
            "Buy / Overweight / Hold / Underweight / Sell"
        )
    side, fraction = _SIGNAL_MAP[normalised]
    return ProposedAction(symbol=symbol, side=side, quantity=full_quantity * fraction)
