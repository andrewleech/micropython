"""Canonical JSON is compact, key-sorted, ASCII-only, encoded in bounded pieces and streamed with its identity."""
import hashlib
import io
import json
import json.encoder
import random
import tracemalloc

import pytest

from mpy_api import cli
from mpy_api.structure import JSONStream


def reference(value):
    return (json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False) + "\n").encode("ascii")


def streamed(value):
    stream = io.BytesIO()
    identity = cli.write_canonical(value, stream)
    return stream.getvalue(), identity


def random_value(rng, depth):
    kind = rng.random()
    if depth > 7 or kind < 0.3:
        return rng.choice([None, True, False, 0, -17, 3.5, 1e300, -0.0, 5e-324, 2 ** 80, -(10 ** 40), "", "plain",
                           "line\nbreak\r\n\ttab", 'quote " and \\ slash', "uni \u00e9\u2028\u2029 \U0001f600",
                           "\x00\x1f\x7f", "lone \ud800 surrogate", "key: value, {braces} [brackets]"])
    if kind < 0.65:
        return {rng.choice(["a", "b", "z", "A", "\u00e9", "with space", "k\nnewline", "", "10", "9", "\U0001f600"]) + str(rng.randrange(4)):
                random_value(rng, depth + 1) for _ in range(rng.randrange(0, 5))}
    return [random_value(rng, depth + 1) for _ in range(rng.randrange(0, 5))]


EDGE_VALUES = [
    {}, [], None, 5, "text", "", 0, 1.5, True,
    {"empty": {}, "list": [], "nested": {"deep": {"deeper": {"deepest": [1, {"x": []}]}}}},
    [[], {}, [[]], [{}]], {"unsorted": 1, "Zebra": 2, "alpha": 3, "": 4, "\u00e9": 5, "z": 6},
    {"mixed": {1: "int key", 2: "other"}, "after": [3, 2, 1]},
    {1: "a", 10: "b", 2: "c"}, {None: 1}, {True: 1, 2.5: 2}, {"a": {1: {"b": [1]}}},
    {"a": [{"b": [{"c": [{"d": "past the split depth"}]}]}]},
    {"text": "line one\nline two", "list": ["x\ny", {"k\nk": "v\nv"}]},
    [{"translation": {"functions": [{"events": {"calls": [1, 2, {"expr": "a\nb"}]}}]}}] * 3,
    {"tuple": (1, (2, 3), []), "big": 2 ** 200, "floats": [0.1, 1e-7, 1e22, 123456789.123456789]},
    ("top", "level", "tuple"),
]


@pytest.mark.parametrize("depth", [0, 1, 2, 4, 9])
@pytest.mark.parametrize("index", range(len(EDGE_VALUES)))
def test_edge_values_match_the_c_encoder_at_any_split_depth(index, depth, monkeypatch):
    monkeypatch.setattr(cli, "_SPLIT_DEPTH", depth)
    value = EDGE_VALUES[index]
    expected = reference(value)
    assert cli.canonical_bytes(value) == expected
    assert "".join(cli.canonical_chunks(value)) + "\n" == expected.decode("ascii")
    assert streamed(value)[0] == expected


@pytest.mark.parametrize("seed", range(40))
def test_random_values_match_the_c_encoder_and_round_trip(seed, monkeypatch):
    rng = random.Random(seed)
    monkeypatch.setattr(cli, "_SPLIT_DEPTH", rng.choice([1, 2, 3, 4, 6]))
    value = {"schema_version": 1, **{f"section{index}": random_value(rng, 0) for index in range(6)},
             "list": [random_value(rng, 1) for _ in range(30)]}
    data, identity = streamed(value)
    assert data == reference(value) == cli.canonical_bytes(value)
    assert json.loads(data) == value
    assert identity == {"bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}


def test_non_string_keys_encode_like_the_json_module():
    value = {"a": {2: "two", 10: "ten", 1: "one"}, "b": [{3: [{4: 5}]}]}
    data, _ = streamed(value)
    assert data == reference(value)
    assert json.loads(data) == {"a": {"1": "one", "2": "two", "10": "ten"}, "b": [{"3": [{"4": 5}]}]}


def test_output_is_one_ascii_line_without_whitespace_padding():
    value = {"b": [1, {"d": "x y", "c": None}], "a": "\u00e9\u2028 \U0001f600 \ud800"}
    data, _ = streamed(value)
    assert data == b'{"a":"\\u00e9\\u2028 \\ud83d\\ude00 \\ud800","b":[1,{"c":null,"d":"x y"}]}\n'
    data.decode("utf-8")
    assert json.loads(data) == value


def test_keys_are_sorted_by_code_point():
    value = {"\U0001f600": 1, "\uffff": 2, "b": 3, "B": 4, "": 5}
    assert json.loads(streamed(value)[0], object_pairs_hook=lambda pairs: [key for key, _ in pairs]) == sorted(value)


@pytest.mark.parametrize("block", [1, 2, 7, 64, 1 << 20])
def test_block_boundaries_do_not_change_bytes_or_identity(block, monkeypatch):
    monkeypatch.setattr(cli, "_BLOCK_CHARS", block)
    rng = random.Random(block)
    value = {"x": [random_value(rng, 1) for _ in range(40)], "y": {"k": "v" * 100}}
    stream = io.BytesIO()
    identity = cli.write_canonical(value, stream)
    assert stream.getvalue() == reference(value)
    assert identity == {"bytes": len(stream.getvalue()), "sha256": hashlib.sha256(reference(value)).hexdigest()}


def test_non_finite_numbers_are_rejected():
    value = {"ok": [1, {"bad": float("nan")}]}
    with pytest.raises(ValueError, match="not JSON compliant"):
        cli.canonical_bytes(value)
    with pytest.raises(ValueError, match="not JSON compliant"):
        streamed(value)
    with pytest.raises(ValueError, match="not JSON compliant"):
        streamed({"bad": float("inf")})


def test_unsortable_keys_raise_like_the_json_module():
    value = {"mixed": {1: "a", "two": "b"}}
    with pytest.raises(TypeError):
        cli.canonical_bytes(value)
    with pytest.raises(TypeError):
        streamed(value)


def test_unserializable_values_raise():
    with pytest.raises(TypeError):
        streamed({"a": [object()]})


def test_circular_references_raise():
    value = {"a": []}
    value["a"].append(value)
    with pytest.raises((ValueError, RecursionError)):
        streamed(value)


@pytest.mark.skipif(json.encoder.c_make_encoder is None, reason="no C encoder in this interpreter")
def test_pure_python_encoder_is_never_used(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("pure Python encoder in use")
    monkeypatch.setattr(json.encoder, "_make_iterencode", forbidden)
    value = {"a": [{"b": list(range(5)), "c": {"d": "e"}}], "f": {"g": {"h": {"i": {"j": [1, 2]}}}}}
    assert streamed(value)[0] == reference(value)
    assert cli.canonical_bytes(value) == reference(value)


def test_pieces_are_bounded_by_the_values_below_the_split_depth(monkeypatch):
    monkeypatch.setattr(cli, "_SPLIT_DEPTH", 2)
    value = {"items": [{"payload": "p" * 1000, "more": ["m" * 500] * 4} for _ in range(50)]}
    sizes = [len(piece) for piece in cli.canonical_chunks(value)]
    assert max(sizes) < 3100 < len(reference(value))


def test_blocks_written_to_the_stream_are_bounded(monkeypatch):
    monkeypatch.setattr(cli, "_BLOCK_CHARS", 4096)

    class Recorder:
        def __init__(self):
            self.sizes = []

        def write(self, data):
            self.sizes.append(len(data))

    value = {"items": [{"id": index, "text": "t" * 300} for index in range(500)]}
    recorder = Recorder()
    identity = cli.write_canonical(value, recorder)
    assert sum(recorder.sizes) == identity["bytes"] == len(reference(value))
    assert max(recorder.sizes) < 4096 + 400
    assert len(recorder.sizes) > 10


def test_streaming_does_not_hold_a_copy_of_the_document():
    value = {"items": [{"id": index, "text": "abc" * 1000} for index in range(8000)]}

    class Discard:
        def write(self, data):
            pass

    document = len(reference(value))
    tracemalloc.start()
    try:
        cli.write_canonical(value, Discard())
        peak = tracemalloc.get_traced_memory()[1]
    finally:
        tracemalloc.stop()
    assert document > 24_000_000
    assert peak < document // 4


def stream_of(items):
    return JSONStream(iter(items))


@pytest.mark.parametrize("depth", [0, 1, 2, 4, 9])
@pytest.mark.parametrize("seed", range(10))
def test_streamed_arrays_write_the_bytes_of_their_items_as_a_list(seed, depth, monkeypatch):
    monkeypatch.setattr(cli, "_SPLIT_DEPTH", depth)
    rng = random.Random(seed)
    items = [random_value(rng, 0) for _ in range(rng.randrange(0, 6))]
    nested = [random_value(rng, 0) for _ in range(3)]
    expected = reference({"a": items, "b": {"c": nested, "d": []}, "e": [items]})
    value = {"a": stream_of(items), "b": {"c": stream_of(nested), "d": stream_of([])}, "e": [stream_of(items)]}
    assert streamed(value)[0] == expected


def test_streamed_items_are_produced_while_written_and_released():
    produced = []

    def items():
        for index in range(200):
            produced.append(index)
            yield {"index": index, "text": "abc" * 100000}

    class Discard:
        def write(self, data):
            pass

    tracemalloc.start()
    try:
        cli.write_canonical({"units": JSONStream(items())}, Discard())
        peak = tracemalloc.get_traced_memory()[1]
    finally:
        tracemalloc.stop()
    assert produced == list(range(200))
    assert peak < 200 * 300000 // 8
