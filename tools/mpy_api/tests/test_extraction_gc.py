"""Extraction pauses automatic cyclic collection and reclaims each record's garbage explicitly."""
import gc
import weakref

import pytest

from mpy_api import inventory


class Cycle:
    """An object that is only reclaimable by the cyclic collector."""
    def __init__(self):
        self.me = self


@pytest.fixture(autouse=True)
def restore_gc():
    enabled, frozen = gc.isenabled(), gc.get_freeze_count()
    yield
    gc.unfreeze()
    gc.enable() if enabled else gc.disable()
    assert gc.isenabled() == enabled and frozen == 0


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


def test_gc_paused_unfreezes_what_the_function_froze_but_not_earlier_freezes():
    kept = [object()]
    inventory._gc_paused(lambda: gc.freeze())()
    assert gc.get_freeze_count() == 0
    gc.freeze()
    before = gc.get_freeze_count()
    assert before > 0
    inventory._gc_paused(lambda: gc.freeze())()
    assert gc.get_freeze_count() >= before
    gc.unfreeze()
    assert kept


def test_serial_extraction_preserves_order_and_results(monkeypatch):
    calls = []

    def fake(record, compdb, root):
        calls.append(record['index'])
        return {'index': record['index'], 'nested': [record['index']] * 3, 'root': root}
    monkeypatch.setattr(inventory, '_extract', fake)
    records = [{'index': index} for index in range(7)]
    results = inventory._gc_paused(lambda: inventory._extract_serial(records, None, '/root'))()
    assert calls == list(range(7))
    assert results == [{'index': index, 'nested': [index] * 3, 'root': '/root'} for index in range(7)]
    assert gc.get_freeze_count() == 0


def test_serial_extraction_reclaims_cyclic_garbage_without_automatic_collection(monkeypatch):
    references = []

    def fake(record, compdb, root):
        garbage = Cycle()
        references.append(weakref.ref(garbage))
        return {'index': record['index']}
    monkeypatch.setattr(inventory, '_extract', fake)

    def run():
        records = [{'index': index} for index in range(5)]
        results = inventory._extract_serial(records, None, None)
        assert not gc.isenabled()
        return results
    inventory._gc_paused(run)()
    assert len(references) == 5 and all(reference() is None for reference in references)


def test_serial_extraction_keeps_live_results_reachable_after_freezing(monkeypatch):
    monkeypatch.setattr(inventory, '_extract', lambda record, compdb, root: {'items': [Cycle(), Cycle()], 'index': record['index']})

    def run():
        results = inventory._extract_serial([{'index': 0}, {'index': 1}], None, None)
        gc.collect()
        return results
    results = inventory._gc_paused(run)()
    gc.collect()
    assert [result['index'] for result in results] == [0, 1]
    assert all(item.me is item for result in results for item in result['items'])


def test_worker_disables_collection_and_collects_before_each_record(monkeypatch):
    references = []

    def fake(record, compdb, root):
        garbage = Cycle()
        references.append(weakref.ref(garbage))
        return gc.isenabled()
    monkeypatch.setattr(inventory, '_extract', fake)
    monkeypatch.setattr(inventory, '_EXTRACTION', ([{'index': 0}, {'index': 1}], None, None))
    gc.enable()
    first = inventory._extract_worker(0)
    assert first == (True, False, None) and not gc.isenabled()
    assert references[0]() is not None
    inventory._extract_worker(1)
    assert references[0]() is None
