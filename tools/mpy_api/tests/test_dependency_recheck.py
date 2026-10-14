"""Dependency selection and input resolution still detect inputs changing during collection."""
import os
import shutil
from pathlib import Path

import pytest

from mpy_api import compiler, inventory


ROOT = Path(__file__).resolve().parents[3]

needs_gcc = pytest.mark.skipif(shutil.which('gcc') is None, reason='needs the selected C compiler')


@pytest.fixture(autouse=True)
def fresh_caches():
    compiler.clear_resolution_cache()
    compiler.clear_replay_cache()
    compiler.clear_input_table()
    yield
    compiler.clear_resolution_cache()
    compiler.clear_replay_cache()
    compiler.clear_input_table()


def extract(directory, wrapper_body, arguments):
    """Run extraction of a.c with a compiler wrapper that can edit the tree between compiler runs."""
    compdb = compiler.authority(ROOT)
    wrapper = directory / 'cc'
    wrapper.write_text('#!/bin/sh\n' + wrapper_body)
    wrapper.chmod(0o755)
    entry = {'directory': str(directory), 'arguments': [str(wrapper), *arguments, '-c', 'a.c', '-o', 'a.o'], 'file': 'a.c'}
    _, snapshot = compiler.read_input(directory / 'a.c')
    record = {**entry, 'physical': str(directory / 'a.c'), 'identity': str(directory / 'a.c'),
              'object': str(directory / 'a.o'), '_source_snapshot': snapshot, '_response_snapshots': {}}
    return inventory._extract(record, compdb, ROOT, full_semantics=True)


@needs_gcc
def test_header_losing_an_include_after_discovery_is_rejected(tmp_path):
    (tmp_path / 'a.c').write_text('#include "h.h"\nint f(void) { return X; }\n')
    (tmp_path / 'h.h').write_text('#include "old.h"\n')
    (tmp_path / 'old.h').write_text('#define X 1\n')
    # The first pass 1 reads h.h before it is captured, then a concurrent edit drops the include.
    body = (f'for a in "$@"; do if [ "$a" = "-dI" ] && [ ! -e {tmp_path}/edited ]; then gcc "$@"; s=$?;'
            f' printf "#define X 1\\n" > {tmp_path}/h.h; touch {tmp_path}/edited; exit $s; fi; done\nexec gcc "$@"\n')
    with pytest.raises(RuntimeError, match='compiler dependency selection changed during collection'):
        extract(tmp_path, body, [])


@needs_gcc
def test_shadowing_header_created_after_expansion_is_rejected(tmp_path):
    (tmp_path / 'inc1').mkdir()
    (tmp_path / 'inc2').mkdir()
    (tmp_path / 'a.c').write_text('#include <h.h>\nint f(void) { return X; }\n')
    (tmp_path / 'inc2' / 'h.h').write_text('#define X 2\n')
    # Pass 2 (-E -fpreprocessed -x c, from stdin) is the last compiler run before the selection check.
    body = (f'for a in "$@"; do if [ "$a" = "-x" ]; then gcc "$@"; s=$?;'
            f' printf "#define X 1\\n" > {tmp_path}/inc1/h.h; exit $s; fi; done\nexec gcc "$@"\n')
    with pytest.raises(RuntimeError, match='compiler dependency selection changed after expansion'):
        extract(tmp_path, body, ['-Iinc1', '-Iinc2'])


@needs_gcc
def test_unchanged_tree_extracts_through_the_same_wrapper(tmp_path):
    (tmp_path / 'a.c').write_text('#include "h.h"\nint f(void) { return X; }\n')
    (tmp_path / 'h.h').write_text('#define X 1\n')
    facts = extract(tmp_path, 'exec gcc "$@"\n', [])
    assert facts['coverage']['status'] == 'processed'
    assert {'a.c', 'h.h'} <= {Path(path).name for path in facts['input']['dependencies']}


def test_symlink_retargeted_to_a_hard_link_of_the_same_inode_is_detected(tmp_path):
    (tmp_path / 'X.h').write_text('int x;\n')
    os.link(tmp_path / 'X.h', tmp_path / 'Y.h')
    (tmp_path / 'P.h').symlink_to('X.h')
    _, first = compiler.read_input(tmp_path / 'P.h')
    os.unlink(tmp_path / 'P.h')
    (tmp_path / 'P.h').symlink_to('Y.h')
    _, second = compiler.read_input(tmp_path / 'P.h')
    assert Path(first['resolved']).name == 'X.h' and Path(second['resolved']).name == 'Y.h'
    with pytest.raises(RuntimeError, match='consumed input changed'):
        compiler.verify_inputs({str(tmp_path / 'P.h'): first})
    with pytest.raises(RuntimeError, match='conflicting consumed input snapshots'):
        compiler.reconcile_inputs([('first read', {str(tmp_path / 'P.h'): first}),
                                   ('second read', {str(tmp_path / 'P.h'): second})])


def test_retargeted_directory_symlink_component_is_detected(tmp_path):
    for name in ('one', 'two'):
        (tmp_path / name).mkdir()
        (tmp_path / name / 'h.h').write_text('int x;\n')
    (tmp_path / 'link').symlink_to('one')
    _, first = compiler.read_input(tmp_path / 'link' / 'h.h')
    os.unlink(tmp_path / 'link')
    (tmp_path / 'link').symlink_to('two')
    (tmp_path / 'two' / 'h.h').unlink()
    os.link(tmp_path / 'one' / 'h.h', tmp_path / 'two' / 'h.h')
    _, second = compiler.read_input(tmp_path / 'link' / 'h.h')
    assert first['resolved'] != second['resolved']
    with pytest.raises(RuntimeError, match='consumed input changed'):
        compiler.verify_inputs({str(tmp_path / 'link' / 'h.h'): first})


def test_link_free_paths_are_resolved_once_and_linked_paths_every_time(tmp_path, monkeypatch):
    tmp_path = tmp_path.resolve()
    (tmp_path / 'plain.h').write_text('int x;\n')
    (tmp_path / 'target.h').write_text('int y;\n')
    (tmp_path / 'linked.h').symlink_to('target.h')
    calls = []
    original = Path.resolve

    def counting(self, strict=False):
        calls.append(str(self))
        return original(self, strict=strict)
    monkeypatch.setattr(Path, 'resolve', counting)
    for _ in range(3):
        compiler.read_input(tmp_path / 'plain.h')
    assert calls == [str(tmp_path / 'plain.h')]
    calls.clear()
    for _ in range(3):
        compiler.read_input(tmp_path / 'linked.h')
    assert calls == [str(tmp_path / 'linked.h')] * 6


def test_dotdot_after_a_symlink_counts_as_linked(tmp_path):
    tmp_path = tmp_path.resolve()
    (tmp_path / 'real' / 'sub').mkdir(parents=True)
    (tmp_path / 'sub').mkdir()
    (tmp_path / 'real' / 'h.h').write_text('int x;\n')
    (tmp_path / 'sub' / 'h.h').write_text('int y;\n')
    (tmp_path / 'l').symlink_to('real/sub')
    path = tmp_path / 'l' / '..' / 'h.h'
    assert compiler._has_symlink(str(path))
    assert not compiler._has_symlink(str(tmp_path / 'sub' / '..' / 'sub' / 'h.h'))
    _, snapshot = compiler.read_input(path)
    assert snapshot['resolved'] == str(tmp_path / 'real' / 'h.h')
