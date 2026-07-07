"""Tradewind's exception hierarchy.

Contract: errors are loud and specific. A silent fallback is a correctness
bug in this project, so every failure mode gets a dedicated exception type
carrying enough context to diagnose it from the message alone. No code in
this repository may swallow these exceptions.
"""


class TradewindError(Exception):
    """Base class for all Tradewind errors."""


class TraceIntegrityError(TradewindError):
    """A trace file failed structural or chain-hash verification.

    Raised when a trace line cannot be parsed, the ``seq`` numbering is not
    strictly monotonic, a ``parent_seq`` points forward, or a recomputed
    chain hash does not match the stored one (i.e. the file was tampered
    with or truncated).
    """


class ReplayDivergence(TradewindError):
    """A replayed run requested something the recorded trace never produced.

    Raised on any boundary cache miss during replay: an LLM or data request
    whose ``request_hash`` is absent from (or exhausted in) the recording.
    Replay must never fall back to a live call.
    """
