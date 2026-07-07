"""Canonical JSON: the exact byte-form contract, plus determinism properties."""

import json
from decimal import Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st

from tradewind.trace.canonical import (
    NotCanonicalisable,
    canonical_json,
    chain_hash,
    request_hash,
    sha256_hex,
)

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


def test_exact_byte_form() -> None:
    value = {"b": [1, 2.5, None, True], "a": "héllo", "c": {"y": 1, "x": ""}}
    assert (
        canonical_json(value) == '{"a":"héllo","b":[1,2.5,null,true],"c":{"x":"","y":1}}'.encode()
    )


def test_key_order_is_irrelevant() -> None:
    assert canonical_json({"a": 1, "b": 2}) == canonical_json({"b": 2, "a": 1})


def test_non_ascii_is_raw_utf8_not_escaped() -> None:
    assert canonical_json("héllo") == '"héllo"'.encode()
    assert b"\\u" not in canonical_json("héllo")


@pytest.mark.parametrize(
    "bad", [Decimal("1.5"), {1: "int key"}, float("nan"), float("inf"), object()]
)
def test_non_json_native_values_rejected(bad: object) -> None:
    with pytest.raises(NotCanonicalisable):
        canonical_json(bad)


def test_hash_shapes() -> None:
    assert sha256_hex(b"") == "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
    assert len(request_hash({"model": "m"})) == 64
    assert chain_hash("00" * 32, b"x") != chain_hash("11" * 32, b"x")


@given(json_values)
def test_canonicalisation_is_a_fixed_point(value: object) -> None:
    once = canonical_json(value)
    assert canonical_json(json.loads(once.decode("utf-8"))) == once


@given(json_values)
def test_equal_values_hash_equally(value: object) -> None:
    reparsed = json.loads(canonical_json(value).decode("utf-8"))
    assert sha256_hex(canonical_json(value)) == sha256_hex(canonical_json(reparsed))
