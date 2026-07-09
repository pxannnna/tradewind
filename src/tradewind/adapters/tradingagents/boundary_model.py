"""LangChain-compatible chat model that routes through an ``LLMBoundary``.

Contract: :func:`make_boundary_chat_model` returns an object satisfying
LangChain's ``BaseChatModel`` interface whose every generation goes through
Tradewind's :class:`~tradewind.trace.boundaries.LLMBoundary` — recorded in
RECORD mode, answered from the trace in REPLAY mode, never bypassing it.

``langchain_core`` is imported lazily inside the factory so this module is
importable (and the rest of Tradewind fully usable) without the
``tradingagents`` extra installed; calling the factory without it raises a
clear error naming the missing dependency.
"""

from typing import Any

from tradewind.errors import TradewindError
from tradewind.trace.boundaries import LLMBoundary, LLMRequest


class AdapterDependencyMissing(TradewindError):
    """The tradingagents extra (langchain-core / TradingAgents) is not installed."""


def _lc_role(message: Any) -> str:
    """Map a LangChain message to a chat role string."""
    kind = getattr(message, "type", "human")
    return {"human": "user", "ai": "assistant", "system": "system", "tool": "tool"}.get(
        str(kind), str(kind)
    )


def make_boundary_chat_model(
    boundary: LLMBoundary, model_name: str, role: str | None = None
) -> Any:
    """Build a ``BaseChatModel`` whose generations go through ``boundary``.

    ``role`` tags every ``llm_call`` event for per-role cost reporting.
    """
    try:
        from langchain_core.language_models.chat_models import BaseChatModel
        from langchain_core.messages import AIMessage, BaseMessage
        from langchain_core.outputs import ChatGeneration, ChatResult
    except ImportError as exc:  # pragma: no cover - exercised only without the extra
        raise AdapterDependencyMissing(
            "langchain-core is required for the TradingAgents adapter; "
            "install with: pip install 'tradewind[tradingagents]'"
        ) from exc

    class BoundaryChatModel(BaseChatModel):
        """Chat model backed by a Tradewind LLM boundary (no direct SDK access)."""

        tw_model_name: str = model_name
        tw_role: str | None = role

        @property
        def _llm_type(self) -> str:
            return "tradewind-boundary"

        def _generate(
            self,
            messages: list[BaseMessage],
            stop: list[str] | None = None,
            run_manager: Any = None,
            **kwargs: Any,
        ) -> ChatResult:
            request = LLMRequest(
                model=self.tw_model_name,
                messages=[{"role": _lc_role(m), "content": str(m.content)} for m in messages],
                params={"stop": stop} if stop else {},
            )
            response, _ = boundary.complete(request, role=self.tw_role)
            return ChatResult(
                generations=[ChatGeneration(message=AIMessage(content=response.content))]
            )

    return BoundaryChatModel()


class BoundaryLLMClient:
    """Drop-in for TradingAgents' ``BaseLLMClient``: ``get_llm()`` returns the wrapper.

    Instances of this class are what the patched ``create_llm_client`` returns,
    so ``TradingAgentsGraph.__init__`` receives boundary-backed models without
    knowing anything changed.
    """

    def __init__(self, boundary: LLMBoundary, model: str, role: str | None = None) -> None:
        self._boundary = boundary
        self._model = model
        self._role = role

    def get_llm(self) -> Any:
        """Return the LangChain-compatible boundary-backed chat model."""
        return make_boundary_chat_model(self._boundary, self._model, self._role)
