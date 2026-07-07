r"""Canonical JSON serialisation and hashing — the determinism linchpin.

Contract: :func:`canonical_json` maps a JSON-compatible Python value to a
unique byte string, so that equal values always hash equally, on any
machine, forever. Every hash in Tradewind (request hashes, the trace chain
hash) is computed over these bytes.

Canonical form, precisely:

* Encoding is UTF-8. Non-ASCII characters are emitted raw, never
  ``\\uXXXX``-escaped (``ensure_ascii=False``).
* Object keys are sorted lexicographically by Unicode code point
  (``sort_keys=True``). Keys must be strings.
* No whitespace: separators are ``,`` and ``:``.
* Integers serialise in base 10 with no leading zeros. Booleans and null
  are ``true``/``false``/``null``.
* Floats serialise via CPython's ``repr`` (shortest string that round-trips
  the IEEE-754 double), which is platform-independent on CPython >= 3.1.
  NaN and infinities are rejected (``allow_nan=False``) — they have no JSON
  representation.
* Values must be JSON-native: ``dict``/``list``/``str``/``int``/``float``/
  ``bool``/``None``. Anything else (including ``Decimal``, ``datetime``,
  tuples of non-JSON values) raises :class:`NotCanonicalisable`; callers
  must convert explicitly (money is carried as decimal *strings* in
  payloads precisely so it never hits float canonicalisation).
"""

import hashlib
import json
from typing import Any

from tradewind.errors import TradewindError


class NotCanonicalisable(TradewindError):
    """A value outside the JSON-native types was passed to canonical_json."""


def _assert_string_keys(value: Any) -> None:
    """Reject non-string object keys, which ``json.dumps`` would silently coerce.

    ``json.dumps({1: "x"})`` emits ``{"1":"x"}``, so ``{1: ...}`` and
    ``{"1": ...}`` would canonicalise identically — a determinism-breaking
    collision. The spec requires string keys, so we enforce it explicitly.
    """
    if isinstance(value, dict):
        for key, child in value.items():
            # Non-str keys (int, bool, float, None) would be silently coerced
            # to strings by json.dumps, so reject them here.
            if not isinstance(key, str):
                raise NotCanonicalisable(
                    f"object keys must be strings, got {type(key).__name__}: {key!r}"
                )
            _assert_string_keys(child)
    elif isinstance(value, list):
        for item in value:
            _assert_string_keys(item)


def canonical_json(value: Any) -> bytes:
    """Serialise ``value`` to canonical JSON bytes (see module docstring)."""
    _assert_string_keys(value)
    try:
        text = json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise NotCanonicalisable(f"value is not canonicalisable to JSON: {exc}") from exc
    return text.encode("utf-8")


def sha256_hex(data: bytes) -> str:
    """Return the lowercase hex SHA-256 digest of ``data``."""
    return hashlib.sha256(data).hexdigest()


def request_hash(request: dict[str, Any]) -> str:
    """Hash a boundary request: ``sha256(canonical_json(request))`` as hex.

    This is the replay lookup key. Two requests replay identically if and
    only if their canonical JSON is byte-identical.
    """
    return sha256_hex(canonical_json(request))


def chain_hash(prev_hash_hex: str, event_body: bytes) -> str:
    """Advance the trace chain: ``sha256(prev_hash_hex_ascii || event_body)``.

    ``prev_hash_hex`` is the previous link's lowercase hex digest (for the
    first event, the header hash); its ASCII bytes are prepended to the
    event's canonical JSON body. Any bit flipped anywhere in the file
    changes every subsequent link, making traces tamper-evident.
    """
    return sha256_hex(prev_hash_hex.encode("ascii") + event_body)
