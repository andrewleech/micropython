"""Operations over nested plain data that keep the nesting on an explicit stack."""
from copy import deepcopy
from json.encoder import c_make_encoder, encode_basestring, encode_basestring_ascii

from .chains import ChainView

_MISSING = object()
_ATOMIC_TYPES = frozenset((type(None), bool, int, float, complex, str, bytes))
# Sequences that compare equal to each other item by item.
_SEQUENCE_TYPES = (list, ChainView)


def _dict_slots(mapping):
    """A dict's items as value, key pairs, in the order copy.deepcopy copies them."""
    for key, value in mapping.items():
        yield value
        yield key


def deep_copy(value):
    """copy.deepcopy of nested dicts, lists, tuples and sets, using an explicit stack.

    The copy, its sharing (an object reachable twice is copied once and shared in the copy) and dict insertion
    order are those of copy.deepcopy, which copies a dict item's value before its key. Immutable atomic leaves
    are returned as they are; a value of any other type is copied by copy.deepcopy with the same memo.
    """
    memo = {}
    # Frame: [original, copy under construction (an item list for a tuple or set), child iterator, copied dict
    # value waiting for its key].
    stack = []

    def enter(item):
        """The finished copy of item, or _MISSING after opening a frame that will produce it."""
        copied = memo.get(id(item), _MISSING)
        if copied is not _MISSING:
            return copied
        kind = type(item)
        if kind in _ATOMIC_TYPES:
            return item
        if kind is dict:
            copied = memo[id(item)] = {}
            stack.append([item, copied, _dict_slots(item), _MISSING])
        elif kind is list:
            copied = memo[id(item)] = []
            stack.append([item, copied, iter(item), _MISSING])
        elif kind is tuple or kind is set:
            stack.append([item, [], iter(item), _MISSING])
        else:
            return deepcopy(item, memo)
        return _MISSING

    copied = enter(value)
    while stack:
        frame = stack[-1]
        original, target, children, held = frame
        if copied is not _MISSING:
            if type(original) is dict:
                if held is _MISSING:
                    frame[3] = copied
                else:
                    target[copied] = held
                    frame[3] = _MISSING
            else:
                target.append(copied)
        child = next(children, _MISSING)
        if child is not _MISSING:
            copied = enter(child)
            continue
        stack.pop()
        kind = type(original)
        if kind is tuple:
            # A tuple reached again while its items were copied already has its copy in the memo.
            copied = memo.get(id(original), _MISSING)
            if copied is _MISSING:
                copied = original if all(a is b for a, b in zip(original, target)) else tuple(target)
                if copied is not original:
                    memo[id(original)] = copied
        elif kind is set:
            copied = memo[id(original)] = set(target)
        else:
            copied = target
    return copied


def deep_equal(left, right):
    """left == right for nested dicts, lists, tuples and ChainViews, using an explicit stack.

    Container items are compared the way the containers' own == compares them: identical items are equal
    without further comparison. A ChainView equals a list or ChainView holding equal items, as its == does.
    Values of any other type, sets included, are compared with ==.
    """
    pending = [(left, right)]
    while pending:
        a, b = pending.pop()
        kind = type(a)
        if kind in _SEQUENCE_TYPES and type(b) in _SEQUENCE_TYPES:
            if kind is not list:
                a = list(a)
            if type(b) is not list:
                b = list(b)
        elif kind is not type(b) or (kind is not dict and kind is not tuple):
            if not a == b:
                return False
            continue
        if len(a) != len(b):
            return False
        if kind is dict:
            for key, value in a.items():
                other = b.get(key, _MISSING)
                if other is _MISSING:
                    return False
                if value is not other:
                    pending.append((value, other))
        else:
            pending.extend((x, y) for x, y in zip(a, b) if x is not y)
    return True


# The C JSON encoder recurses once per nesting level, on the interpreter's recursion limit before
# Python 3.12 and on the C recursion limit from 3.12. A container at most this many levels high
# is handed to it whole: 100 levels stay far below the default limit of 1000 together with the
# frames of any caller, whatever the input.
_C_ENCODER_HEIGHT = 100


def _json_one_call(encoder):
    """A function giving ``encoder.encode(value)`` for a value that holds no ChainView and does not
    contain itself, with one C call when the C encoder is available.

    The circular reference check is left out, as such a value cannot meet itself."""
    if c_make_encoder is None:
        return encoder.encode
    make = c_make_encoder(None, encoder.default,
                          encode_basestring_ascii if encoder.ensure_ascii else encode_basestring,
                          None, encoder.key_separator, encoder.item_separator, encoder.sort_keys,
                          encoder.skipkeys, encoder.allow_nan)
    return lambda value: "".join(make(value, 0))


def _json_height(root, heights):
    """The height of container ``root`` (1 when it holds no non-empty container), or None when the
    height exceeds ``_C_ENCODER_HEIGHT``, when ``root`` holds a ChainView or when it contains itself.

    One post-order pass over an explicit stack fills ``heights`` (by container id) for ``root`` and
    every container below it that it reaches."""
    known = heights.get(id(root), _MISSING)
    if known is not _MISSING:
        return known
    on_path = {id(root)}
    # Frame: [container, its remaining non-scalar items, greatest item height, whether every item is encodable].
    frames = [[root, _json_nested(root), 0, True]]
    while frames:
        frame = frames[-1]
        for item in frame[1]:
            if type(item) is ChainView:
                frame[3] = False
            elif isinstance(item, (dict, list, tuple)):
                if not item:
                    if frame[2] < 1:
                        frame[2] = 1
                    continue
                identity = id(item)
                known = heights.get(identity, _MISSING)
                if known is _MISSING:
                    if identity in on_path:
                        frame[3] = False
                        continue
                    on_path.add(identity)
                    frames.append([item, _json_nested(item), 0, True])
                    break
                if known is None:
                    frame[3] = False
                elif known > frame[2]:
                    frame[2] = known
        else:
            frames.pop()
            container = frame[0]
            on_path.discard(id(container))
            height = frame[2] + 1 if frame[3] and frame[2] < _C_ENCODER_HEIGHT else None
            heights[id(container)] = height
            if frames:
                parent = frames[-1]
                if height is None:
                    parent[3] = False
                elif height > parent[2]:
                    parent[2] = height
    return heights[id(root)]


_JSON_SCALARS = frozenset((str, int, float, bool, type(None)))


def _json_nested(container):
    """An iterator over the items of ``container`` (values of a dict) that are not plain scalars."""
    return iter([item for item in (container.values() if isinstance(container, dict) else container)
                 if type(item) not in _JSON_SCALARS])


def _json_key_text(key, encode_one):
    """The string the C encoder writes for dict key ``key``."""
    if isinstance(key, str):
        return key
    if isinstance(key, (float, int)) or key is None:
        return encode_one(key)
    raise TypeError(f"keys must be str, int, float, bool or None, not {type(key).__name__}")


def _json_entries(value, encode_string, encode_one):
    """Yield ``(text, item)`` for container ``value``: the text written before each item, and the item."""
    if isinstance(value, dict):
        if all(isinstance(key, str) for key in value):
            pairs = ((key, value[key]) for key in sorted(value))
        else:
            pairs = ((_json_key_text(key, encode_one), item) for key, item in sorted(value.items()))
        separator = "{"
        for key, item in pairs:
            yield separator + encode_string(key) + ":", item
            separator = ","
    else:
        separator = "["
        for item in value:
            yield separator, item
            separator = ","


def json_pieces(value, encoder, split_depth):
    """Yield the JSON text of ``value`` in pieces whose concatenation is ``encoder.encode(value)``, with a
    ChainView written as the array of its items.

    ``encoder`` is a ``json.JSONEncoder`` with ``sort_keys``, no ``indent`` and compact separators, so it runs
    its C implementation. A container at depth ``split_depth`` or deeper that is at most ``_C_ENCODER_HEIGHT``
    levels high, holds no ChainView and does not contain itself, and every other value, is encoded whole by
    the C encoder. Every other container is walked here with an explicit stack, dicts in sorted key order,
    so nesting deeper than ``_C_ENCODER_HEIGHT`` never reaches the C encoder's recursion."""
    encode_string = encode_basestring_ascii if encoder.ensure_ascii else encode_basestring
    encode_one = _json_one_call(encoder)
    heights = {}
    walking = set()
    # One frame per container being written: its id, its closing text and its remaining entries. The
    # bottom frame holds ``value`` itself; the items of the top frame lie at depth ``len(frames) - 1``.
    frames = [(None, "", iter((("", value),)))]
    while frames:
        identity, closer, entries = frames[-1]
        for text, item in entries:
            if text:
                yield text
            if isinstance(item, str):
                yield encode_string(item)
                continue
            if type(item) is ChainView:
                walk = bool(item)
            else:
                walk = (isinstance(item, (dict, list, tuple)) and item
                        and (len(frames) <= split_depth or _json_height(item, heights) is None))
            if walk:
                if id(item) in walking:
                    raise ValueError("Circular reference detected")
                walking.add(id(item))
                frames.append((id(item), "}" if isinstance(item, dict) else "]",
                               _json_entries(item, encode_string, encode_one)))
                break
            yield encode_one([] if type(item) is ChainView else item)
        else:
            frames.pop()
            if closer:
                walking.remove(identity)
                yield closer
