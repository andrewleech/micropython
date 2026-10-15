"""collect_inventory raises the same extraction error for every --jobs value."""
import json
import shutil
import tempfile
from pathlib import Path

import pytest

from mpy_api import inventory
from mpy_api.facts import FactStore
from mpy_api.paths import PathNames


ROOT = Path(__file__).resolve().parents[3]

pytestmark = pytest.mark.skipif(shutil.which('gcc') is None, reason='needs the selected C compiler')

VALID = 'int value;\nstatic int helper(int input) { return input + 1; }\nint other(int input) { return helper(input); }\n'
BROKEN_FIRST = 'int first_broken( { ;\n'
BROKEN_SECOND = 'int second_broken( { ;\n'


def build(tmp_path, sources):
    build_dir = tmp_path / 'build'
    build_dir.mkdir()
    entries = []
    for name, text in sources.items():
        source = tmp_path / name
        source.write_text(text)
        obj = build_dir / (source.stem + '.o')
        obj.write_bytes(b'')
        entries.append({'directory': str(build_dir), 'arguments': ['gcc', '-c', '-o', str(obj), str(source)],
                        'file': str(source), 'output': str(obj)})
    database = tmp_path / 'compile_commands.json'
    database.write_text(json.dumps(entries))
    return database, build_dir


def failure(project, jobs):
    database, build_dir = project
    with pytest.raises(RuntimeError) as raised:
        selection = inventory.select_records(database=database, root=ROOT, build_dir=build_dir, names=PathNames([(str(database.parent.resolve()), 'project')]))
        store = FactStore(tempfile.mkdtemp(dir=database.parent))
        inventory.collect_inventory(selection, port='test', target='test', variant='test', link={},
                                    store=store, jobs=jobs)
    return str(raised.value)


@pytest.fixture
def ownership_fails(monkeypatch):
    def fail(records, link):
        raise RuntimeError('ownership failed')
    monkeypatch.setattr(inventory, 'collect_ownership', fail)


def test_first_failing_record_in_order_has_the_serial_message(tmp_path, ownership_fails):
    project = build(tmp_path, {'a_valid.c': VALID, 'b_broken.c': BROKEN_FIRST, 'c_valid.c': VALID,
                               'd_broken.c': BROKEN_SECOND})
    serial = failure(project, 1)
    assert 'API extraction failed for compiled TU' in serial and 'b_broken.c' in serial
    assert 'd_broken.c' not in serial and 'ownership failed' not in serial
    for jobs in (2, 4):
        assert failure(project, jobs) == serial


def test_ownership_failure_surfaces_when_every_record_extracts(tmp_path, ownership_fails):
    project = build(tmp_path, {'a_valid.c': VALID, 'b_valid.c': VALID})
    for jobs in (1, 3):
        assert failure(project, jobs) == 'ownership failed'


def snapshot(digest):
    return {'/x.h': {'path': '/x.h', 'resolved': '/x.h', 'sha256': digest, '_stat': (1,)}}


def planned_unit(plan):
    """A stand-in for one unit's extraction whose include constraints and input snapshots follow ``plan``."""
    def extract(record, compdb, root, path, level, full_semantics):
        step = plan[int(record['identity'][2:])]
        if step.get('fail'):
            raise RuntimeError('extraction failure ' + record['identity'])
        location = {'path': str(path), 'bytes': 0, 'sha256': '', 'compression': level, 'sections': []}
        return ({'translation_unit': record['identity'], 'functions': []}, [], location,
                snapshot(step['snapshot']), step['selection'])
    return extract


def planned_failure(monkeypatch, tmp_path, jobs, plan):
    monkeypatch.setattr(inventory, '_extract_unit', planned_unit(plan))
    monkeypatch.setattr(inventory, 'collect_ownership', lambda records, link: {'input_snapshots': {}})
    records = [{'identity': f'tu{index}', 'physical': f'/tu{index}.c'} for index in range(len(plan))]
    selection = {'root': tmp_path, 'build_dir': tmp_path, 'database': tmp_path / 'db', 'compdb': None,
                 'records': records, 'completeness': {}, 'inputs': {}, 'names': PathNames()}
    with pytest.raises(RuntimeError) as raised:
        inventory.collect_inventory(selection, port='p', target='t', variant='v', link={},
                                    store=FactStore(tempfile.mkdtemp(dir=tmp_path)), jobs=jobs)
    return str(raised.value)


BOTH_CONFLICTS = [{'selection': [('is', '/a.h', 'A')], 'snapshot': '1'},
                  {'selection': [('is', '/a.h', 'B')], 'snapshot': '2'}]


@pytest.mark.parametrize('jobs', [1, 2])
def test_selection_conflict_is_raised_before_an_input_conflict(monkeypatch, tmp_path, jobs):
    assert (planned_failure(monkeypatch, tmp_path, jobs, BOTH_CONFLICTS)
            == 'compiler include selection changed during collection: /a.h')


@pytest.mark.parametrize('jobs', [1, 2])
def test_input_conflict_alone_names_both_units(monkeypatch, tmp_path, jobs):
    plan = [{'selection': [], 'snapshot': '1'}, {'selection': [], 'snapshot': '2'}]
    assert (planned_failure(monkeypatch, tmp_path, jobs, plan)
            == 'conflicting consumed input snapshots for /x.h: tu0 and tu1')


@pytest.mark.parametrize('jobs', [1, 2])
def test_a_later_extraction_failure_wins_over_earlier_conflicts(monkeypatch, tmp_path, jobs):
    plan = [*BOTH_CONFLICTS, {'selection': [], 'snapshot': '1', 'fail': True}]
    assert planned_failure(monkeypatch, tmp_path, jobs, plan) == 'extraction failure tu2'


def test_parallel_extraction_refuses_a_runs_directory_that_is_not_new(tmp_path):
    project = build(tmp_path, {'a_valid.c': VALID, 'b_valid.c': VALID})
    database, build_dir = project
    selection = inventory.select_records(database=database, root=ROOT, build_dir=build_dir, names=PathNames([(str(database.parent.resolve()), 'project')]))
    store = FactStore(tempfile.mkdtemp(dir=database.parent))
    (Path(store.directory) / 'runs').mkdir()
    with pytest.raises(FileExistsError):
        inventory.collect_inventory(selection, port='test', target='test', variant='test', link={},
                                    store=store, jobs=2)
