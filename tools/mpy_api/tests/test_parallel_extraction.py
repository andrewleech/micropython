"""Forked extraction workers pass the serial results, in record order, to the parent with serial error semantics."""
import os
from pathlib import Path
import pickle
import time

import pytest

from mpy_api import inventory
from mpy_api.facts import FactStore


def records(count):
    return [{'identity': f'tu-{index}', 'physical': f'/src/{index}.c', 'index': index} for index in range(count)]


def fake_unit(record, compdb, root, path, level, full_semantics):
    """Deterministic stand-in for one unit's extraction result, with shared references and tuples."""
    index = record['index']
    if index in compdb['fail']:
        raise compdb['fail'][index]
    shared = [index, index + 1]
    return {'translation_unit': record['identity'], 'root': root, 'pid_is_set': bool(os.getpid()),
            'path': os.path.basename(path), 'level': level,
            'tuple': (index, 'x'), 'first': shared, 'second': shared}


@pytest.fixture
def patched(monkeypatch):
    monkeypatch.setattr(inventory, '_extract_unit', fake_unit)


@pytest.fixture
def store(tmp_path):
    return FactStore(tmp_path, compression=3)


def serial(items, compdb, root, store):
    results = []
    inventory._extract_serial(items, compdb, root, store, results.append, full_semantics=False)
    return results


def run(items, compdb, jobs, store, concurrent=None):
    results = []
    side = inventory._extract_parallel(items, compdb, '/root', jobs, store, results.append, concurrent,
                                       full_semantics=False)
    return results, side


@pytest.mark.parametrize('jobs', [2, 3, 8, 64])
def test_results_match_serial_in_record_order(patched, store, jobs):
    items = records(17)
    compdb = {'fail': {}}
    parallel, side = run(items, compdb, jobs, store)
    assert parallel == serial(items, compdb, '/root', store)
    assert side is None
    assert [facts['translation_unit'] for facts in parallel] == [record['identity'] for record in items]
    assert [facts['path'] for facts in parallel] == [store.path(index).name for index in range(17)]
    assert {facts['level'] for facts in parallel} == {3}


def test_pickled_results_keep_types_and_intra_result_sharing(patched, store):
    parallel, _ = run(records(4), {'fail': {}}, 2, store)
    for facts in parallel:
        assert isinstance(facts['tuple'], tuple)
        assert facts['first'] is facts['second']
    # Results are independent objects, as pickle round-tripped values.
    assert parallel[0]['first'] is not parallel[1]['first']
    assert pickle.loads(pickle.dumps(parallel)) == parallel


def test_single_record_and_more_workers_than_records(patched, store):
    items = records(1)
    assert run(items, {'fail': {}}, 8, store)[0] == serial(items, {'fail': {}}, '/root', store)


def gated_unit(record, compdb, root, path, level, full_semantics):
    """A unit that, when its index is in ``compdb['gated']``, waits for the file ``compdb['gate']``
    (up to a minute, so a defect fails the test instead of hanging it), then leaves a marker file
    at ``path``."""
    if record['index'] in compdb['gated']:
        deadline = time.monotonic() + 60
        while not compdb['gate'].exists() and time.monotonic() < deadline:
            time.sleep(0.01)
    Path(path).write_text('done')
    return record['identity']


def test_each_result_is_consumed_before_later_records_complete(monkeypatch, store, tmp_path):
    monkeypatch.setattr(inventory, '_extract_unit', gated_unit)
    items = records(8)
    gate = tmp_path / 'gate'
    seen = []

    def consume(result):
        seen.append((result, sum(store.path(index).exists() for index in range(8))))
        gate.touch()
    inventory._extract_parallel(items, {'gated': range(4, 8), 'gate': gate}, '/root', 2, store, consume,
                                full_semantics=False)
    assert [result for result, _ in seen] == [f'tu-{index}' for index in range(8)]
    # Records 4 to 7 cannot finish before the first result is consumed.
    assert seen[0][1] <= 4


def test_a_consume_failure_cancels_the_records_not_yet_started(monkeypatch, store, tmp_path):
    monkeypatch.setattr(inventory, '_extract_unit', gated_unit)
    gate = tmp_path / 'gate'

    def consume(result):
        gate.touch()
        raise RuntimeError('repeated fact object for translation unit ' + result)
    with pytest.raises(RuntimeError, match='repeated fact object for translation unit tu-0'):
        inventory._extract_parallel(records(40), {'gated': range(1, 40), 'gate': gate}, '/root', 2, store, consume,
                                    full_semantics=False)
    assert inventory._EXTRACTION is None
    # Only records already handed to a worker when tu-0 was consumed can finish.
    assert sum(store.path(index).exists() for index in range(40)) < 10


def test_first_failing_record_in_order_raises_with_its_own_message(patched, store):
    items = records(12)
    compdb = {'fail': {9: RuntimeError('API extraction failed for tu-9'),
                       4: RuntimeError('API extraction failed for tu-4'),
                       6: ValueError('later failure')}}
    with pytest.raises(RuntimeError, match='tu-4') as raised:
        run(items, compdb, 4, store)
    assert type(raised.value) is RuntimeError
    assert str(raised.value) == 'API extraction failed for tu-4'
    assert isinstance(raised.value.__cause__, inventory._WorkerTraceback)
    assert 'fake_unit' in str(raised.value.__cause__)
    with pytest.raises(RuntimeError, match='tu-4') as serial_raised:
        serial(items, compdb, '/root', store)
    assert str(serial_raised.value) == str(raised.value)


@pytest.mark.parametrize('error', [OSError('disk'), KeyError('missing'), ValueError('bad value')])
def test_exception_types_propagate_unchanged(patched, store, error):
    with pytest.raises(type(error)) as raised:
        run(records(5), {'fail': {2: error}}, 3, store)
    assert raised.value.args == error.args


def test_worker_state_is_released_after_success_and_failure(patched, store):
    run(records(3), {'fail': {}}, 2, store)
    assert inventory._EXTRACTION is None
    with pytest.raises(ValueError):
        run(records(3), {'fail': {1: ValueError('x')}}, 2, store)
    assert inventory._EXTRACTION is None


def test_workers_are_separate_processes(monkeypatch, store):
    parent = os.getpid()
    monkeypatch.setattr(inventory, '_extract_unit', lambda record, compdb, root, path, level, full_semantics: os.getpid())
    pids, _ = run(records(8), {}, 4, store)
    assert parent not in pids and 1 <= len(set(pids)) <= 4


def test_concurrent_callable_runs_in_this_process_and_returns_its_result(patched, store):
    parent = os.getpid()
    items = records(6)
    results, side = run(items, {'fail': {}}, 3, store, lambda: ('side', os.getpid()))
    assert side == ('side', parent)
    assert results == serial(items, {'fail': {}}, '/root', store)


def test_concurrent_failure_is_raised_after_extraction_and_after_extraction_failures(patched, store):
    def broken():
        raise RuntimeError('ownership failed')
    with pytest.raises(RuntimeError, match='ownership failed'):
        run(records(4), {'fail': {}}, 2, store, broken)
    with pytest.raises(ValueError, match='extraction wins'):
        run(records(4), {'fail': {1: ValueError('extraction wins')}}, 2, store, broken)
