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


PASS_ONE = '\n'.join([
    '# 0 "a.c"',
    '# 0 "<built-in>"',
    '# 0 "<command-line>"',
    '# 1 "/usr/include/stdc-predef.h" 1 3 4',
    '# 0 "<command-line>" 2',
    '# 1 "a.c"',
    '#include <s.h>',
    '# 1 "a.c"',
    '# 1 "sys/s.h" 1 3',
    '#include "p.h"',
    '# 1 "sys/s.h" 3',
    '# 1 "sys/p.h" 1 3',
    '# 2 "sys/s.h" 2 3',
    '# 2 "a.c" 2',
    '/* comment */ #include "h.h"',
    '# 2 "a.c"',
    '# 1 "h.h" 1',
    'int h;',
    '# 4 "a.c" 2',
    '  #include "h.h"',
    '/* a comment row',
    '#include "nothing.h"',
    '*/ #define AFTER 1',
    'int a;',
    ''])


def test_scan_of_pass_one_reads_lookups_entries_and_system_flags(tmp_path):
    scan = compiler.scan_dependency_pass(PASS_ONE, 'a.c')
    assert scan['unmodelled'] == []
    assert scan['lookups'] == [
        ['<command-line>', None, 'include', True, 'stdc-predef.h', '/usr/include/stdc-predef.h'],
        ['a.c', None, 'include', True, 's.h', 'sys/s.h'],
        ['sys/s.h', 1, 'include', False, 'p.h', 'sys/p.h'],
        ['a.c', None, 'include', False, 'h.h', 'h.h'],
        ['a.c', None, 'include', False, 'h.h', None],
        # A comment row of the -dI form is read as an include that entered nothing.
        ['a.c', None, 'include', False, 'nothing.h', None]]
    assert scan['entered'] == {'/usr/include/stdc-predef.h': True, 'sys/s.h': True, 'sys/p.h': True, 'h.h': False}
    assert scan['order'] == ['/usr/include/stdc-predef.h', 'sys/s.h', 'sys/p.h', 'h.h']
    rows = scan['source'].split('\n')
    assert '#include' not in scan['source'] and rows[14] == '/* comment */ ' and rows[19] == '  '
    assert len(rows) == len(PASS_ONE.split('\n'))
    (tmp_path / 'h.h').write_text('int h;\n')
    assert compiler.project_dependencies(tmp_path, 'a.c', scan) == ([str(tmp_path / 'a.c'), str(tmp_path / 'h.h')], [])


def test_scan_of_pass_one_reports_what_the_stat_replay_cannot_model():
    renamed = compiler.scan_dependency_pass('# 0 "a.c"\n# 1 "a.c"\n# 7 "other.c"\nint a;\n', 'a.c')
    assert renamed['unmodelled'] == ['presumed file name changed to other.c']
    unpaired = compiler.scan_dependency_pass('# 0 "a.c"\n# 1 "a.c"\n# 1 "x.h" 1\n# 2 "a.c" 2\n', 'a.c')
    assert unpaired['unmodelled'] == ['file entered without an include directive: x.h']


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


def test_input_table_captures_each_path_once_and_rejects_later_content(tmp_path, monkeypatch):
    compiler.clear_input_table()
    path = tmp_path / 'input.h'
    path.write_text('first\n')
    reads = []
    original = compiler.read_input
    monkeypatch.setattr(compiler, 'read_input', lambda target: reads.append(str(target)) or original(target))
    first = compiler.recorded(path)
    assert compiler.recorded(path) is first and compiler.recorded_inputs([path, path]) == {str(path): first}
    assert reads == [str(path)]
    assert compiler.read_recorded(path) == (b'first\n', first)
    path.write_text('second contents\n')
    with pytest.raises(RuntimeError, match='consumed input changed during collection'):
        compiler.read_recorded(path)
    assert compiler.recorded(path) is first
    compiler.clear_input_table()


def test_final_verification_hashes_each_physical_file_once_for_every_spelling(tmp_path, monkeypatch):
    (tmp_path / 'sub').mkdir()
    (tmp_path / 'h.h').write_text('int h;\n')
    spellings = [str(tmp_path / 'h.h'), str(tmp_path / 'sub' / '..' / 'h.h')]
    records = {spelling: compiler.read_input(spelling)[1] for spelling in spellings}
    reads = []
    original = compiler.read_input
    monkeypatch.setattr(compiler, 'read_input', lambda target: reads.append(str(target)) or original(target))
    compiler.verify_inputs(records)
    assert reads == [str(tmp_path / 'h.h')]
    (tmp_path / 'h.h').write_text('int changed;\n')
    for spelling in spellings:
        with pytest.raises(RuntimeError, match='consumed input changed during collection'):
            compiler.verify_inputs({spelling: records[spelling]})


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
