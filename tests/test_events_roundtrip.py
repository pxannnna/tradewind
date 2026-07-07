"""Acceptance: round-trip serialise/deserialise of arbitrary events is lossless."""

import json

from hypothesis import given
from hypothesis import strategies as st

from tradewind.trace.events import EVENT_TYPES, TraceEvent, TraceHeader

json_values = st.recursive(
    st.none()
    | st.booleans()
    | st.integers(min_value=-(2**53), max_value=2**53)
    | st.floats(allow_nan=False, allow_infinity=False)
    | st.text(max_size=20),
    lambda children: (
        st.lists(children, max_size=4) | st.dictionaries(st.text(max_size=10), children, max_size=4)
    ),
    max_leaves=25,
)

hex_hashes = st.text(alphabet="0123456789abcdef", min_size=64, max_size=64)


@st.composite
def events(draw: st.DrawFn) -> TraceEvent:
    seq = draw(st.integers(min_value=2, max_value=10**9))
    parent = draw(st.none() | st.integers(min_value=1, max_value=seq - 1))
    return TraceEvent(
        seq=seq,
        event_type=draw(st.sampled_from(EVENT_TYPES)),
        parent_seq=parent,
        payload=draw(st.dictionaries(st.text(max_size=10), json_values, max_size=5)),
        chain_hash=draw(hex_hashes),
    )


@given(events())
def test_event_roundtrip_is_lossless(event: TraceEvent) -> None:
    line = event.to_line()
    restored = TraceEvent.model_validate(json.loads(line.decode("utf-8")))
    assert restored == event
    assert restored.to_line() == line  # byte-stable, not just value-equal


@given(
    config_hash=hex_hashes,
    code_version=st.text(max_size=20),
    model_ids=st.lists(st.text(max_size=20), max_size=3),
    submodule_sha=st.none() | hex_hashes,
    rng_seed=st.integers(min_value=0, max_value=2**63),
)
def test_header_roundtrip_is_lossless(
    config_hash: str,
    code_version: str,
    model_ids: list[str],
    submodule_sha: str | None,
    rng_seed: int,
) -> None:
    from tradewind.trace.canonical import canonical_json

    header = TraceHeader(
        config_hash=config_hash,
        code_version=code_version,
        model_ids=model_ids,
        submodule_sha=submodule_sha,
        rng_seed=rng_seed,
    )
    line = canonical_json(header.model_dump(mode="json"))
    restored = TraceHeader.model_validate(json.loads(line.decode("utf-8")))
    assert restored == header
    assert restored.genesis_hash() == header.genesis_hash()
