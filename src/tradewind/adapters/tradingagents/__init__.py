"""TradingAgents adapter: run Tauric Research's TradingAgents under Tradewind.

The injection seam (documented after reading the pinned v0.3.1 source): the
graph constructs its two LLMs in ``TradingAgentsGraph.__init__`` via
``create_llm_client(provider, model, ...)``, imported into
``tradingagents.graph.trading_graph`` from ``tradingagents.llm_clients``.
:func:`~tradewind.adapters.tradingagents.adapter.patched_llm_clients` replaces
that binding (and the factory's own) with a client whose ``get_llm()`` returns
a LangChain-compatible chat model routed through Tradewind's
:class:`~tradewind.trace.boundaries.LLMBoundary` — so every LLM call the graph
makes is recorded to (or replayed from) the trace, and no call can bypass it.

Layering (heavy imports are strictly quarantined):

* :mod:`.mapping` — pure functions, no TradingAgents/LangChain imports:
  message ↔ request conversion, final-state → ``agent_message`` deliberation
  extraction, 5-tier signal → :class:`ProposedAction`.
* :mod:`.boundary_model` — builds the LangChain ``BaseChatModel`` wrapper;
  imports ``langchain_core`` lazily (requires the ``tradingagents`` extra).
* :mod:`.adapter` — patches the seam and drives ``TradingAgentsGraph``;
  imports ``tradingagents`` itself lazily.

Requires ``pip install tradewind[tradingagents]`` plus an editable install of
the pinned submodule (``pip install -e third_party/TradingAgents``) and a
provider API key — only for *recording*. Replay of a committed trace needs
none of that, by construction.
"""

from tradewind.adapters.tradingagents.mapping import (
    action_from_signal,
    deliberation_from_final_state,
    record_deliberation,
    request_from_messages,
)

__all__ = [
    "action_from_signal",
    "deliberation_from_final_state",
    "record_deliberation",
    "request_from_messages",
]
