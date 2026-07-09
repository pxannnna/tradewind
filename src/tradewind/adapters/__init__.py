"""Framework adapters.

Only modules under this package may import agent frameworks (TradingAgents,
LangChain, LangGraph). The Tradewind core — ``trace``, ``invariants``, ``sim``,
``report``, ``bench`` — must never depend on anything here.
"""
