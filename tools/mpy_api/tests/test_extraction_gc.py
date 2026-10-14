"""Extraction runs without automatic cyclic collection and leaves no cyclic garbage to collect."""
import gc

import pytest

from mpy_api import inventory
from mpy_api.facts import FactStore
from mpy_api.frontend import syntax_adapter
from mpy_api.semantics import semantics


SOURCE = """typedef struct { int qst; int flags; int defval; } mp_arg_t;
int helper(int value);
int table[2][2] = { { 1, 2 }, { 3, 4 } };
static int probe(int value, int mode) {
    static const mp_arg_t allowed[] = { { .qst = 1, .flags = 2, .defval = 3 } };
    int total = value;
    for (int i = 0; i < mode; i++) { total = total + helper(i); }
    if (mode == 1) { return helper(total); } else if (mode == 2) { total = (total + 1) * 2; } else { return 0; }
    while (total > 3) { if (total & 1) { break; } total--; }
    return total;
}
"""


@pytest.fixture(autouse=True)
def restore_gc():
    enabled = gc.isenabled()
    yield
    gc.enable() if enabled else gc.disable()


def test_gc_paused_disables_inside_and_restores_enabled_state():
    gc.enable()
    seen = []
    inventory._gc_paused(lambda: seen.append(gc.isenabled()))()
    assert seen == [False] and gc.isenabled()


def test_gc_paused_keeps_a_disabled_collector_disabled():
    gc.disable()
    inventory._gc_paused(lambda: None)()
    assert not gc.isenabled()


def test_gc_paused_restores_state_when_the_function_raises():
    gc.enable()

    def failing():
        raise ValueError('boom')
    with pytest.raises(ValueError, match='boom'):
        inventory._gc_paused(failing)()
    assert gc.isenabled()


def test_gc_paused_returns_result_and_passes_arguments():
    assert inventory._gc_paused(lambda *args, **kwargs: (args, kwargs))(1, key=2) == ((1,), {'key': 2})


def test_serial_extraction_preserves_order_and_results(monkeypatch, tmp_path):
    calls = []

    def fake(record, compdb, root, path, level):
        calls.append(record['index'])
        return {'index': record['index'], 'nested': [record['index']] * 3, 'root': root, 'path': path}
    monkeypatch.setattr(inventory, '_extract_unit', fake)
    records = [{'index': index} for index in range(7)]
    store = FactStore(tmp_path)
    results = []
    inventory._gc_paused(lambda: inventory._extract_serial(records, None, '/root', store, results.append))()
    assert calls == list(range(7))
    assert results == [{'index': index, 'nested': [index] * 3, 'root': '/root', 'path': store.path(index)}
                       for index in range(7)]


def test_worker_runs_extraction_with_collection_disabled(monkeypatch, tmp_path):
    monkeypatch.setattr(inventory, '_extract_unit',
                        lambda record, compdb, root, path, level: (record['index'], gc.isenabled()))
    monkeypatch.setattr(inventory, '_EXTRACTION', ([{'index': 0}, {'index': 1}], None, None, FactStore(tmp_path)))
    gc.enable()
    assert inventory._extract_worker(1) == (True, (1, False), None)
    assert not gc.isenabled()


def analyse():
    physical = '/fixture/gc.c'
    origins = [(physical, line) for line in range(1, len(SOURCE.splitlines()) + 1)]
    declarations, functions, errors, frontend = syntax_adapter(
        SOURCE, origins, physical, {'dependencies': {physical: 'fixture'}, 'project_dependencies': [physical]})
    assert not errors
    by_name = {}
    for function in functions:
        by_name.setdefault(function['name'], []).append(function)
    summaries = [semantics(function, by_name, errors, {}) for function in functions]
    return declarations, functions, frontend, summaries


def test_frontend_and_semantics_leave_no_cyclic_garbage():
    gc.disable()
    analyse()  # warm module-level caches (compiled patterns, parser language)
    gc.collect()
    declarations, functions, frontend, summaries = analyse()
    assert functions and summaries
    del declarations, functions, frontend, summaries
    assert gc.collect() == 0
