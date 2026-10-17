"""``deep_copy`` equals a frame-per-item reference implementation and ``copy.deepcopy``: the same values, the
same sharing and the same dict order."""
import copy
import random

import pytest

from mpy_api.chains import chain_root
from mpy_api.structure import _ATOMIC_TYPES, _MISSING, _dict_slots, deep_copy


def reference_deep_copy(value):
    """deep_copy with every dict key and value, and every item, entered through its own frame step."""
    memo = {}
    stack = []

    def enter(item):
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
            return copy.deepcopy(item, memo)
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


def signature(value):
    """The value as nested tuples with each container numbered when first met and referred to by number after,
    so that two values have one signature when they hold the same data shared in the same places."""
    numbers, order = {}, []

    def walk(item):
        kind = type(item)
        if kind in _ATOMIC_TYPES:
            return ('atom', kind.__name__, repr(item))
        if id(item) in numbers:
            return ('ref', numbers[id(item)])
        numbers[id(item)] = len(numbers)
        order.append(item)
        if kind is dict:
            return ('dict', numbers[id(item)], [(walk(k), walk(v)) for k, v in item.items()])
        if kind in (list, tuple):
            return (kind.__name__, numbers[id(item)], [walk(x) for x in item])
        if kind is set:
            return ('set', numbers[id(item)], sorted(repr(x) for x in item))
        return ('other', numbers[id(item)], repr(item))
    return walk(value)


ATOMS = [None, True, False, 0, 1, -7, 2 ** 70, 1.5, 0.0, 1 + 2j, '', 'a', 'key', 'caf\u00e9', b'', b'x']


def random_value(rng, depth, pool):
    """A nested value that reuses earlier containers of ``pool`` and sometimes contains itself."""
    roll = rng.random()
    if depth == 0 or roll < 0.3:
        return rng.choice(ATOMS)
    if pool and roll < 0.45:
        return rng.choice(pool)
    kind = rng.choice(['dict', 'dict', 'list', 'list', 'tuple', 'set', 'chain'])
    if kind == 'dict':
        result = {}
        pool.append(result)
        for _ in range(rng.randint(0, 5)):
            key = rng.choice(['a', 'b', 'c', 'd', 1, 2, (1, 'x'), 'e', 'f'])
            result[key] = random_value(rng, depth - 1, pool)
    elif kind == 'list':
        result = []
        pool.append(result)
        result.extend(random_value(rng, depth - 1, pool) for _ in range(rng.randint(0, 5)))
    elif kind == 'tuple':
        result = tuple(random_value(rng, depth - 1, pool) for _ in range(rng.randint(0, 4)))
        pool.append(result)
    elif kind == 'set':
        result = {rng.choice([1, 2, 'x', (1, 2), None]) for _ in range(rng.randint(0, 4))}
    else:
        result = chain_root([], []).extend(rng.choice(ATOMS)).extend(rng.choice(ATOMS))
    return result


@pytest.mark.parametrize('seed', range(60))
def test_copies_have_the_values_sharing_and_order_of_the_reference_and_of_deepcopy(seed):
    rng = random.Random(seed)
    pool = []
    original = random_value(rng, 6, pool)
    if pool and rng.random() < 0.5:
        # A cycle through a dict or list.
        target = rng.choice([item for item in pool if type(item) in (dict, list)] or [None])
        if type(target) is dict:
            target['cycle'] = original
        elif type(target) is list:
            target.append(original)
    before = signature(original)
    new, reference, standard = deep_copy(original), reference_deep_copy(original), copy.deepcopy(original)
    assert signature(new) == signature(reference) == signature(standard) == before
    assert signature(original) == before
    # Mutable containers are new objects; ChainViews are immutable and returned as they are.
    if type(original) in (dict, list, set):
        assert new is not original


def test_atoms_tuples_of_atoms_and_chain_views_are_returned_as_they_are():
    view = chain_root([], []).extend('x')
    for value in (None, 3, 'text', b'raw', 1.5, (1, 'a', (2, 3)), view):
        assert deep_copy(value) is value
    nested = {'view': view, 'items': [view, view]}
    copied = deep_copy(nested)
    assert copied['view'] is view and copied['items'][0] is view and copied['items'][1] is view


def test_a_pair_of_equal_atoms_in_a_dict_keeps_order_and_non_atomic_keys_and_values():
    key = (1, [2])
    shared = {'a': 1}
    original = {'b': 1, 'c': shared, key[0]: [shared, shared], 'd': 'x', ('t', 'u'): None, 'e': {'f': {'g': [1, 2, {'h': 3}]}}}
    copied = deep_copy(original)
    assert list(copied) == list(original) == list(reference_deep_copy(original))
    assert copied['c'] is copied[1][0] is copied[1][1] and copied['c'] is not shared
    assert copied == original


def test_a_deep_chain_of_nested_values_needs_no_recursion():
    original = leaf = {}
    for _ in range(5000):
        leaf['next'] = {'depth': 1}
        leaf = leaf['next']
    copied = deep_copy(original)
    depth = 0
    while 'next' in copied:
        copied = copied['next']
        depth += 1
    assert depth == 5000
