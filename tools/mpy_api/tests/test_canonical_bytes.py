"""Parallel canonical serialization is byte-identical to the single-process encoder."""
import json
import random

import pytest

from mpy_api import cli


def reference(value):
    return (json.dumps(value, sort_keys=True, indent=2, ensure_ascii=False, allow_nan=False) + "\n").encode("utf-8")


def random_value(rng, depth):
    kind = rng.random()
    if depth > 7 or kind < 0.3:
        return rng.choice([None, True, False, 0, -17, 3.5, 1e300, "", "plain", "line\nbreak\r\n\ttab", 'quote " and \\ slash',
                           "uni \u00e9\u2028\u2029 \U0001f600", "\x00\x1f", "key: value, {braces} [brackets]"])
    if kind < 0.65:
        return {rng.choice(["a", "b", "z", "A", "\u00e9", "with space", "k\nnewline", "", "10", "9"]) + str(rng.randrange(4)):
                random_value(rng, depth + 1) for _ in range(rng.randrange(0, 5))}
    return [random_value(rng, depth + 1) for _ in range(rng.randrange(0, 5))]


EDGE_VALUES = [
    {}, [], None, 5, "text", {"empty": {}, "list": [], "nested": {"deep": {"deeper": {"deepest": [1, {"x": []}]}}}},
    [[], {}, [[]], [{}]], {"unsorted": 1, "Zebra": 2, "alpha": 3, "": 4},
    {"mixed": {1: "int key", 2: "other"}, "after": [3, 2, 1]},
    {"a": [{"b": [{"c": [{"d": "past the split depth"}]}]}]},
    {"text": "line one\nline two", "list": ["x\ny", {"k\nk": "v\nv"}]},
    [{"translation": {"functions": [{"events": {"calls": [1, 2, {"expr": "a\nb"}]}}]}}] * 3,
]


@pytest.mark.parametrize("jobs", [2, 3, 8])
@pytest.mark.parametrize("index", range(len(EDGE_VALUES)))
def test_edge_values_match_serial(index, jobs):
    value = EDGE_VALUES[index]
    assert cli.canonical_bytes(value, jobs) == reference(value) == cli.canonical_bytes(value)


@pytest.mark.parametrize("seed", range(12))
def test_random_values_match_serial(seed):
    rng = random.Random(seed)
    value = {"schema_version": 1, **{f"section{index}": random_value(rng, 0) for index in range(6)},
             "list": [random_value(rng, 1) for _ in range(30)]}
    assert cli.canonical_bytes(value, 4) == reference(value)


def test_serial_default_is_unchanged_encoder():
    value = {"b": [1, 2], "a": {"x": None}}
    assert cli.canonical_bytes(value) == reference(value)
    assert cli.canonical_bytes(value, 1) == reference(value)


def test_non_finite_numbers_are_rejected_in_both_modes():
    value = {"ok": [1, {"bad": float("nan")}]}
    with pytest.raises(ValueError, match="not JSON compliant"):
        cli.canonical_bytes(value)
    with pytest.raises(ValueError, match="not JSON compliant"):
        cli.canonical_bytes(value, 3)


def test_unsortable_keys_raise_like_the_serial_encoder():
    value = {"mixed": {1: "a", "two": "b"}}
    with pytest.raises(TypeError):
        cli.canonical_bytes(value)
    with pytest.raises(TypeError):
        cli.canonical_bytes(value, 3)


def test_leaf_state_is_released(tmp_path):
    cli.canonical_bytes({"a": [1, 2, 3]}, 2)
    assert cli._LEAVES is None
