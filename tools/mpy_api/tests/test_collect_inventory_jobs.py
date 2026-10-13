"""collect_inventory raises the same extraction error for every --jobs value."""
import json
import shutil
from pathlib import Path

import pytest

from mpy_api import inventory


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
        inventory.collect_inventory(database=database, root=ROOT, build_dir=build_dir, port='test', target='test',
                                    variant='test', link={}, jobs=jobs)
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
