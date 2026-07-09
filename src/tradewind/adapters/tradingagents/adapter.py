"""Drive a TradingAgentsGraph run under Tradewind's boundaries.

Contract: :func:`patched_llm_clients` is the documented injection seam — it
replaces ``create_llm_client`` both where the factory defines it
(``tradingagents.llm_clients.factory``) and where the graph module binds it
(``tradingagents.graph.trading_graph``), so a ``TradingAgentsGraph``
constructed inside the context receives boundary-backed chat models for both
its deep- and quick-thinking LLMs. No TradingAgents code is modified on disk;
the submodule stays read-only.

:func:`run_tradingagents` then propagates one decision, records the full
deliberation as ``agent_message`` events, maps the 5-tier signal to a
:class:`ProposedAction`, and emits it as a ``proposed_action`` event.

``tradingagents`` (and its heavy dependency stack) is imported lazily; without
it, calling these functions raises :class:`AdapterDependencyMissing` with the
install instructions. Recording additionally needs a provider API key in the
environment; replaying a committed trace needs neither.
"""

from collections.abc import Iterator
from contextlib import contextmanager
from decimal import Decimal
from typing import Any

from tradewind.adapters.tradingagents.boundary_model import (
    AdapterDependencyMissing,
    BoundaryLLMClient,
)
from tradewind.adapters.tradingagents.mapping import (
    action_from_signal,
    deliberation_from_final_state,
    record_deliberation,
)
from tradewind.invariants.domain import ProposedAction
from tradewind.trace.boundaries import LLMBoundary
from tradewind.trace.events import TraceEvent
from tradewind.trace.writer import TraceWriter


@contextmanager
def patched_llm_clients(boundary: LLMBoundary) -> Iterator[None]:
    """Patch TradingAgents' LLM factory to return boundary-backed clients.

    Both the factory module and the name imported into
    ``tradingagents.graph.trading_graph`` are replaced for the duration of the
    context, then restored — the vendored code is never edited.
    """
    try:
        from tradingagents.graph import trading_graph as tg_module
        from tradingagents.llm_clients import factory as factory_module
    except ImportError as exc:  # pragma: no cover - exercised only without the extra
        raise AdapterDependencyMissing(
            "TradingAgents is required for the adapter; install the pinned "
            "submodule with: pip install -e third_party/TradingAgents "
            "and the extra: pip install 'tradewind[tradingagents]'"
        ) from exc

    def boundary_factory(
        provider: str, model: str, base_url: str | None = None, **kwargs: Any
    ) -> BoundaryLLMClient:
        del provider, base_url, kwargs  # boundary decides everything observable
        return BoundaryLLMClient(boundary, model)

    original_factory = factory_module.create_llm_client
    original_bound = tg_module.create_llm_client
    factory_module.create_llm_client = boundary_factory
    tg_module.create_llm_client = boundary_factory
    try:
        yield
    finally:
        factory_module.create_llm_client = original_factory
        tg_module.create_llm_client = original_bound


def run_tradingagents(
    writer: TraceWriter,
    boundary: LLMBoundary,
    symbol: str,
    trade_date: str,
    quantity: Decimal,
    config: dict[str, Any] | None = None,
    selected_analysts: tuple[str, ...] = ("market", "news"),
) -> tuple[ProposedAction | None, list[TraceEvent]]:
    """Propagate one TradingAgents decision under the harness.

    Returns the mapped :class:`ProposedAction` (``None`` for Hold) and the
    events written. The graph's own LLM construction happens inside
    :func:`patched_llm_clients`, so every model call flows through
    ``boundary`` — recorded or replayed, never live-by-accident.
    """
    with patched_llm_clients(boundary):
        from tradingagents.graph.trading_graph import TradingAgentsGraph

        graph = TradingAgentsGraph(
            selected_analysts=list(selected_analysts), debug=False, config=config
        )
        final_state, signal = graph.propagate(symbol, trade_date)

    deliberation = deliberation_from_final_state(final_state)
    events = record_deliberation(writer, deliberation)
    action = action_from_signal(signal, symbol, quantity)
    parent = events[-1].seq if events else None
    if action is not None:
        events.append(writer.append("proposed_action", action.to_payload(), parent_seq=parent))
    else:
        events.append(
            writer.append(
                "proposed_action",
                {"symbol": symbol, "side": None, "quantity": "0", "signal": "Hold"},
                parent_seq=parent,
            )
        )
    return action, events
