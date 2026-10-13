"""Forked extraction workers return the serial results, in record order, with serial error semantics."""
import os
import pickle

import pytest

from mpy_api import inventory


def records(count):
    return [{'identity': f'tu-{index}', 'physical': f'/src/{index}.c', 'index': index} for index in range(count)]


def fake_extract(record, compdb, root):
    """Deterministic stand-in with shared references and tuples."""
    index = record['index']
    if index in compdb['fail']:
        raise compdb['fail'][index]
    shared = [index, index + 1]
    return {'translation_unit': record['identity'], 'root': root, 'pid_is_set': bool(os.getpid()),
            'tuple': (index, 'x'), 'first': shared, 'second': shared}


@pytest.fixture
def patched(monkeypatch):
    monkeypatch.setattr(inventory, '_extract', fake_extract)


def serial(items, compdb, root):
    return [fake_extract(record, compdb, root) for record in items]


def run(items, compdb, jobs, concurrent=None):
    return inventory._extract_parallel(items, compdb, '/root', jobs, concurrent)


@pytest.mark.parametrize('jobs', [2, 3, 8, 64])
def test_results_match_serial_in_record_order(patched, jobs):
    items = records(17)
    compdb = {'fail': {}}
    parallel, side = run(items, compdb, jobs)
    assert parallel == serial(items, compdb, '/root')
    assert side is None
    assert [facts['translation_unit'] for facts in parallel] == [record['identity'] for record in items]


def test_pickled_results_keep_types_and_intra_result_sharing(patched):
    parallel, _ = run(records(4), {'fail': {}}, 2)
    for facts in parallel:
        assert isinstance(facts['tuple'], tuple)
        assert facts['first'] is facts['second']
    # Results are independent objects, as pickle round-tripped values.
    assert parallel[0]['first'] is not parallel[1]['first']
    assert pickle.loads(pickle.dumps(parallel)) == parallel


def test_single_record_and_more_workers_than_records(patched):
    items = records(1)
    assert run(items, {'fail': {}}, 8)[0] == serial(items, {'fail': {}}, '/root')


def test_first_failing_record_in_order_raises_with_its_own_message(patched):
    items = records(12)
    compdb = {'fail': {9: RuntimeError('API extraction failed for tu-9'),
                       4: RuntimeError('API extraction failed for tu-4'),
                       6: ValueError('later failure')}}
    with pytest.raises(RuntimeError, match='tu-4') as raised:
        run(items, compdb, 4)
    assert type(raised.value) is RuntimeError
    assert str(raised.value) == 'API extraction failed for tu-4'
    assert isinstance(raised.value.__cause__, inventory._WorkerTraceback)
    assert 'fake_extract' in str(raised.value.__cause__)
    with pytest.raises(RuntimeError, match='tu-4') as serial_raised:
        serial(items, compdb, '/root')
    assert str(serial_raised.value) == str(raised.value)


@pytest.mark.parametrize('error', [OSError('disk'), KeyError('missing'), ValueError('bad value')])
def test_exception_types_propagate_unchanged(patched, error):
    with pytest.raises(type(error)) as raised:
        run(records(5), {'fail': {2: error}}, 3)
    assert raised.value.args == error.args


def test_worker_state_is_released_after_success_and_failure(patched):
    run(records(3), {'fail': {}}, 2)
    assert inventory._EXTRACTION is None
    with pytest.raises(ValueError):
        run(records(3), {'fail': {1: ValueError('x')}}, 2)
    assert inventory._EXTRACTION is None


def test_workers_are_separate_processes(monkeypatch):
    parent = os.getpid()
    monkeypatch.setattr(inventory, '_extract', lambda record, compdb, root: os.getpid())
    pids, _ = run(records(8), {}, 4)
    assert parent not in pids and 1 <= len(set(pids)) <= 4


def test_concurrent_callable_runs_in_this_process_and_returns_its_result(patched):
    parent = os.getpid()
    items = records(6)
    results, side = run(items, {'fail': {}}, 3, lambda: ('side', os.getpid()))
    assert side == ('side', parent)
    assert results == serial(items, {'fail': {}}, '/root')


def test_concurrent_failure_is_raised_after_extraction_and_after_extraction_failures(patched):
    def broken():
        raise RuntimeError('ownership failed')
    with pytest.raises(RuntimeError, match='ownership failed'):
        run(records(4), {'fail': {}}, 2, broken)
    with pytest.raises(ValueError, match='extraction wins'):
        run(records(4), {'fail': {1: ValueError('extraction wins')}}, 2, broken)
