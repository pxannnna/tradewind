"""Invariant engine (Phase 2): deterministic risk checks outside the model.

Agents propose; this package disposes. A pipeline of pure invariants runs
before any fill touches the portfolio, and a ``VETO`` from any of them blocks
the action with a fully-evidenced ``violation`` event. Nothing here imports an
LLM library, reads the wall clock, or uses global RNG.

Public surface:

* Domain — :class:`Side`, :class:`ProposedAction`, :class:`Fill`,
  :class:`MarketModel`, :class:`MarketContext`, :class:`RiskConfig`.
* Portfolio — :class:`PortfolioState`, :func:`replay_fills`,
  :func:`verify_fill_consistency`.
* Checks — the six invariants and :func:`default_invariants`.
* Engine — :class:`InvariantEngine`, :class:`EvaluationResult`,
  :class:`VetoPolicy`, :func:`record_evaluation`.
* Verdict — :class:`Verdict`, :class:`Decision`.
"""

from tradewind.invariants.checks import (
    CashConservation,
    DrawdownBreaker,
    Invariant,
    NoNegativeCash,
    OrderValidity,
    PositionLimit,
    RateLimit,
    default_invariants,
)
from tradewind.invariants.domain import (
    Fill,
    MarketContext,
    MarketModel,
    ProposedAction,
    RiskConfig,
    Side,
)
from tradewind.invariants.engine import (
    EvaluationResult,
    InvariantEngine,
    VetoPolicy,
    record_evaluation,
)
from tradewind.invariants.portfolio import (
    PortfolioState,
    check_fill_conservation,
    replay_fills,
    verify_fill_consistency,
)
from tradewind.invariants.verdict import Decision, Verdict

__all__ = [
    "CashConservation",
    "Decision",
    "DrawdownBreaker",
    "EvaluationResult",
    "Fill",
    "Invariant",
    "InvariantEngine",
    "MarketContext",
    "MarketModel",
    "NoNegativeCash",
    "OrderValidity",
    "PortfolioState",
    "PositionLimit",
    "ProposedAction",
    "RateLimit",
    "RiskConfig",
    "Side",
    "Verdict",
    "VetoPolicy",
    "check_fill_conservation",
    "default_invariants",
    "record_evaluation",
    "replay_fills",
    "verify_fill_consistency",
]
