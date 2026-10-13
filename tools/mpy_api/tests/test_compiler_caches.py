"""Process-level caches in compiler replay keep the results of uncached filesystem and compiler queries."""
import os
import re
import stat
from pathlib import Path

import pytest

from mpy_api import compiler


@pytest.fixture(autouse=True)
def fresh_caches():
    compiler.clear_resolution_cache()
    compiler.clear_replay_cache()
    yield
    compiler.clear_resolution_cache()
    compiler.clear_replay_cache()


def reference_unmark(text, directory):
    rows, origins, source, line = [], [], '<unknown>', 0
    for row in text.splitlines():
        marker = re.match(r'\s*#\s+(\d+)\s+"([^"]+)"', row)
        if marker:
            line = int(marker[1])
            name = marker[2]
            source = name if name.startswith('<') else str((Path(directory) / name).resolve())
            continue
        rows.append(row if not row.lstrip().startswith('#') else '')
        origins.append((source, line))
        line += 1
    return ('\n'.join(rows) + '\n' if rows else ''), origins


def layout(tmp_path):
    (tmp_path / 'real' / 'sub').mkdir(parents=True)
    (tmp_path / 'real' / 'sub' / 'a.h').write_text('int a;\n')
    (tmp_path / 'link').symlink_to(tmp_path / 'real', target_is_directory=True)
    (tmp_path / 'build').mkdir()
    return tmp_path / 'build'


EXPANSION = '\n'.join([
    '# 1 "../link/sub/a.h"',
    'int declared_in_header;',
    '   # pragma once',
    '#  7 "../real/sub/../sub/a.h" 1 3',
    'int second;',
    '',
    '# 3 "<built-in>"',
    'int builtin;',
    '#define NOT_A_MARKER 1',
    '\t#\tdefine ALSO_BLANKED',
    '# 12 "missing/not-created.h"',
    'int last = 1; // # 5 "inline.h"',
    ''])


def test_unmark_matches_uncached_reference_and_resolves_symlinks(tmp_path):
    build = layout(tmp_path)
    expected = reference_unmark(EXPANSION, build)
    assert compiler.unmark(EXPANSION, build) == expected
    assert compiler.unmark(EXPANSION, build) == expected
    sources = {source for source, _ in expected[1]}
    assert str(tmp_path / 'real' / 'sub' / 'a.h') in sources
    assert '<built-in>' in sources
    assert str(build / 'missing' / 'not-created.h') in sources


def test_unmark_without_markers_or_hashes_is_unchanged():
    text = 'int a;\n\nint b;'
    assert compiler.unmark(text, '/nowhere') == reference_unmark(text, '/nowhere')
    assert compiler.unmark('', '/nowhere') == reference_unmark('', '/nowhere')


def test_resolve_path_matches_pathlib_and_is_memoized(tmp_path):
    build = layout(tmp_path)
    name = '../link/sub/../sub/a.h'
    expected = str((build / name).resolve())
    assert compiler.resolve_path(build, name) == expected
    # The memoized answer survives a layout change within one run; clearing restores live behaviour.
    (tmp_path / 'link').unlink()
    other = tmp_path / 'other'
    (other / 'sub').mkdir(parents=True)
    (other / 'sub' / 'a.h').write_text('int a;\n')
    (tmp_path / 'link').symlink_to(other, target_is_directory=True)
    assert compiler.resolve_path(build, name) == expected
    compiler.clear_resolution_cache()
    assert compiler.resolve_path(build, name) == str((other / 'sub' / 'a.h').resolve())


def test_resolve_path_keys_on_directory_and_name(tmp_path):
    build = layout(tmp_path)
    assert compiler.resolve_path(build, 'a.h') != compiler.resolve_path(tmp_path / 'real' / 'sub', 'a.h')
    assert compiler.resolve_path(build, '/abs/x.h') == '/abs/x.h'


def test_validate_include_markers_uses_resolved_dependencies(tmp_path):
    build = layout(tmp_path)
    header = str(tmp_path / 'real' / 'sub' / 'a.h')
    record = {'directory': str(build), 'dependencies': {header: 'digest'}}
    compiler.validate_include_markers('# 1 "../link/sub/a.h" 1\nint a;\n# 2 "<built-in>" 1 3\n', record)
    compiler.validate_include_markers('# 1 "../link/sub/a.h"\n# 4 "unlisted.h" 2\nint a;\n', record)
    with pytest.raises(RuntimeError, match='uncaptured include dependency'):
        compiler.validate_include_markers('# 1 "unlisted.h" 1\n', record)
    # Repeated validation of the same text keeps the same verdicts after memoization.
    with pytest.raises(RuntimeError, match='uncaptured include dependency'):
        compiler.validate_include_markers('# 1 "unlisted.h" 1\n', record)


def test_read_input_reports_current_content_despite_cached_resolution(tmp_path):
    path = tmp_path / 'input.h'
    path.write_text('first\n')
    content, first = compiler.read_input(path)
    assert content == b'first\n'
    path.write_text('second contents\n')
    content, second = compiler.read_input(path)
    assert content == b'second contents\n'
    assert second['resolved'] == first['resolved']
    assert second['sha256'] != first['sha256'] and second['_stat'] != first['_stat']
    with pytest.raises(RuntimeError, match='consumed input changed'):
        compiler.verify_inputs({str(path): first})


def test_read_input_missing_file_is_not_cached(tmp_path):
    path = tmp_path / 'later.h'
    with pytest.raises(FileNotFoundError):
        compiler.read_input(path)
    path.write_text('x\n')
    assert compiler.read_input(path)[0] == b'x\n'


def counting_compiler(tmp_path):
    """A compiler stand-in that appends one line to a log per run and echoes its arguments."""
    log = tmp_path / 'runs.log'
    script = tmp_path / 'fakecc'
    script.write_text('#!/bin/sh\necho "$@" >> "%s"\nif [ "$1" = "-fail" ]; then echo broken >&2; exit 3; fi\n'
                      'echo "out: $@"\n' % log)
    script.chmod(script.stat().st_mode | stat.S_IXUSR)
    return script, log


def fake_record(tmp_path, script, source='x.c', flags=('-DONE',)):
    return {'compiler': str(script), 'expanded_arguments': [str(script), *flags, '-c', source],
            'arguments': [str(script), *flags, '-c', source], 'directory': str(tmp_path),
            'file': source, 'physical': str(tmp_path / source)}


def runs(log):
    return log.read_text().splitlines() if log.exists() else []


def test_invoke_without_reuse_runs_every_time(tmp_path):
    script, log = counting_compiler(tmp_path)
    record = fake_record(tmp_path, script)
    compiler.invoke(record, ['-M'])
    compiler.invoke(record, ['-M'])
    assert len(runs(log)) == 2


def test_invoke_reuse_replays_identical_command_and_keeps_receipt_shape(tmp_path):
    script, log = counting_compiler(tmp_path)
    record = fake_record(tmp_path, script)
    first_out, first = compiler.invoke(record, ['-M'], reuse=True)
    second_out, second = compiler.invoke(record, ['-M'], reuse=True)
    plain_out, plain = compiler.invoke(record, ['-M'])
    assert len(runs(log)) == 2
    assert first_out == second_out == plain_out == 'out: -DONE -M x.c\n'
    assert first == second == plain
    assert second['argv'] is not first['argv']
    assert second['argv'] == [str(script), '-DONE', '-M', 'x.c'] and second['exit'] == 0


def test_invoke_reuse_distinguishes_option_flags_directory_and_source(tmp_path):
    script, log = counting_compiler(tmp_path)
    base = fake_record(tmp_path, script)
    compiler.invoke(base, ['-M'], reuse=True)
    compiler.invoke(base, ['-MM'], reuse=True)
    compiler.invoke(fake_record(tmp_path, script, source='y.c'), ['-M'], reuse=True)
    compiler.invoke(fake_record(tmp_path, script, flags=('-DTWO',)), ['-M'], reuse=True)
    (tmp_path / 'other').mkdir()
    compiler.invoke({**base, 'directory': str(tmp_path / 'other')}, ['-M'], reuse=True)
    assert len(runs(log)) == 5
    compiler.invoke(base, ['-M'], reuse=True)
    assert len(runs(log)) == 5


def test_identity_queries_are_shared_across_sources_with_identical_flags(tmp_path):
    script, log = counting_compiler(tmp_path)
    one = fake_record(tmp_path, script, source='x.c')
    two = fake_record(tmp_path, script, source='y.c')
    other_flags = fake_record(tmp_path, script, source='z.c', flags=('-m32',))
    out_one, receipt_one = compiler.invoke(one, ['-dumpmachine'], reuse=True)
    out_two, receipt_two = compiler.invoke(two, ['-dumpmachine'], reuse=True)
    compiler.invoke(other_flags, ['-dumpmachine'], reuse=True)
    compiler.invoke(one, ['--version'], reuse=True)
    compiler.invoke(two, ['--version'], reuse=True)
    assert len(runs(log)) == 3
    assert out_one == out_two
    assert receipt_one['argv'][-1] == 'x.c' and receipt_two['argv'][-1] == 'y.c'
    assert receipt_two['argv'] == [str(script), '-DONE', '-dumpmachine', 'y.c']
    assert receipt_one['stdout_sha256'] == receipt_two['stdout_sha256']


def test_invoke_reuse_does_not_cache_failures_or_stdin_runs(tmp_path):
    script, log = counting_compiler(tmp_path)
    record = fake_record(tmp_path, script)
    for _ in range(2):
        with pytest.raises(RuntimeError, match='compiler replay failed'):
            compiler.invoke({**record, 'expanded_arguments': [str(script), '-fail', '-c', 'x.c']}, ['-M'], reuse=True)
    assert len(runs(log)) == 2
    compiler.invoke(record, ['-E', '-x', 'c'], 'int a;\n', reuse=True)
    compiler.invoke(record, ['-E', '-x', 'c'], 'int a;\n', reuse=True)
    assert len(runs(log)) == 4


def test_clear_replay_cache_forces_a_new_run(tmp_path):
    script, log = counting_compiler(tmp_path)
    record = fake_record(tmp_path, script)
    compiler.invoke(record, ['-M'], reuse=True)
    compiler.clear_replay_cache()
    compiler.invoke(record, ['-M'], reuse=True)
    assert len(runs(log)) == 2
