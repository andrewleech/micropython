"""Two compiler runs per translation unit, with include selection and input content checked by stat and hash.

Pass 1 (-E -fdirectives-only -dI -v -MD) is the only run that resolves includes; pass 2 expands its
output from stdin. Each test edits the tree at a point in that sequence, or after extraction and
before the final verification, and requires the change to be detected."""
import hashlib
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from mpy_api import compiler, inventory


ROOT = Path(__file__).resolve().parents[3]

pytestmark = pytest.mark.skipif(shutil.which('gcc') is None, reason='needs the selected C compiler')


@pytest.fixture(autouse=True)
def fresh_tables():
    for clear in (compiler.clear_resolution_cache, compiler.clear_replay_cache, compiler.clear_input_table):
        clear()
    yield
    for clear in (compiler.clear_resolution_cache, compiler.clear_replay_cache, compiler.clear_input_table):
        clear()


def write(directory, files):
    for name, text in files.items():
        path = directory / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)


def wrapper(directory, after=None):
    """A compiler that logs each argv and, while ``after`` is set, runs it (shell) once after the
    first pass 1 (the first run given -dI). The script is written once: it is a consumed input."""
    hook = directory / 'hook'
    if after is None:
        hook.unlink(missing_ok=True)
    else:
        hook.write_text(after + '\n')
        (directory / 'edited').unlink(missing_ok=True)
    script = directory / 'cc'
    if not script.exists():
        script.write_text(f'#!/bin/sh\nprintf "%s\\n" "$*" >> {directory}/runs.log\n'
                          f'if [ -e {directory}/hook ] && [ ! -e {directory}/edited ]; then for a in "$@"; do '
                          f'if [ "$a" = "-dI" ]; then gcc "$@"; s=$?; touch {directory}/edited; '
                          f'sh {directory}/hook; exit $s; fi; done; fi\nexec gcc "$@"\n')
        script.chmod(0o755)
    return script


def runs(directory):
    path = directory / 'runs.log'
    return path.read_text().splitlines() if path.exists() else []


def extract(directory, arguments=(), *, after=None, source='a.c'):
    """Extract ``source`` through the wrapper, its source recorded first as record selection does."""
    compdb = compiler.authority(ROOT)
    script = wrapper(directory, after)
    entry = {'directory': str(directory), 'arguments': [str(script), *arguments, '-c', source, '-o', 'a.o'], 'file': source}
    snapshot = compiler.recorded(compdb.entry_file(entry))
    compiler.recorded(directory / source)
    record = {**entry, 'physical': snapshot['resolved'], 'identity': snapshot['resolved'], 'object': str(directory / 'a.o'),
              '_source_snapshot': snapshot, '_response_snapshots': {}}
    return inventory._extract(record, compdb, ROOT, full_semantics=True)


def final(*facts):
    """The final verification before publication, over the given translation units."""
    inputs = compiler.reconcile_inputs([(item['translation_unit'], item['input']['input_snapshots']) for item in facts])
    selection = compiler.merge_selection(item['_selection'] for item in facts)
    compiler.verify_collection({'inputs': inputs, 'selection': selection})


def stat_replayed(facts):
    return facts['input']['selection_check'] == {'method': 'include search order replayed by stat'}


def returned(facts, name='f'):
    function = next(item for item in facts['functions'] if item['name'] == name)
    return [event['expression'] for event in function['events']['returns']]


def test_translation_unit_with_recorded_inputs_runs_the_compiler_twice(tmp_path):
    write(tmp_path, {'a.c': '#include <stdio.h>\n#include "h.h"\nint f(void) { return X; }\n',
                     'b.c': '#include <stdio.h>\n#include "h.h"\nint g(void) { return X + 1; }\n',
                     'h.h': '#ifndef H\n#define H\n#define X 1\n#endif\n'})
    first = extract(tmp_path, source='b.c')
    assert stat_replayed(first)
    (tmp_path / 'runs.log').unlink()
    facts = extract(tmp_path)
    compiled = [run for run in runs(tmp_path) if '-dumpmachine' not in run and '--version' not in run]
    assert len(compiled) == 2
    assert '-dI' in compiled[0].split() and '-fpreprocessed' in compiled[1].split()
    assert stat_replayed(facts) and returned(facts) == ['1']
    final(first, facts)


def test_facts_do_not_depend_on_what_earlier_units_recorded(tmp_path):
    write(tmp_path, {'a.c': '#include <stdio.h>\n#include "h.h"\nint f(void) { return X; }\n',
                     'b.c': '#include <stdio.h>\n#include "h.h"\nint g(void) { return X; }\n', 'h.h': '#define X 1\n'})
    cold = extract(tmp_path)
    assert len([run for run in runs(tmp_path) if '-dI' in run.split()]) == 2
    for clear in (compiler.clear_resolution_cache, compiler.clear_replay_cache, compiler.clear_input_table):
        clear()
    extract(tmp_path, source='b.c')
    (tmp_path / 'runs.log').unlink()
    warm = extract(tmp_path)
    assert len([run for run in runs(tmp_path) if '-dI' in run.split()]) == 1
    # Receipts record the runs the facts come from, so a repeated pass 1 leaves no trace in them.
    cold.pop('_selection')
    warm.pop('_selection')
    assert cold == warm


def test_dependency_and_non_system_sets_come_from_pass_one(tmp_path):
    write(tmp_path, {'a.c': '#include <stdio.h>\n#include <s.h>\n#include "h.h"\nint f(void) { return X + S; }\n',
                     'sys/s.h': '#include "p.h"\n#define S 2\n', 'sys/p.h': '#define P 3\n',
                     'h.h': '#define X 1\n'})
    facts = extract(tmp_path, ['-isystem', 'sys'])
    argv = ['gcc', '-isystem', 'sys', '-c', 'a.c']
    record = {'directory': str(tmp_path), 'expanded_arguments': argv, 'arguments': argv,
              'physical': str(tmp_path / 'a.c'), 'file': 'a.c'}
    compdb = compiler.authority(ROOT)
    every = {compiler.resolve_path(tmp_path, path) for path in compdb.first_rule(compiler.invoke(record, ['-M'])[0])}
    project = {compiler.resolve_path(tmp_path, path) for path in compdb.first_rule(compiler.invoke(record, ['-MM'])[0])}
    assert set(facts['input']['dependencies']) == every
    # A project-relative header included from a system header is excluded, as -MM excludes it.
    assert str(tmp_path / 'sys' / 'p.h') in every and str(tmp_path / 'sys' / 'p.h') not in project
    assert set(facts['coverage']['project_headers']) | {str(tmp_path / 'a.c')} == project


def test_header_edited_after_first_read_is_read_again_after_capture(tmp_path):
    write(tmp_path, {'a.c': '#include "h.h"\nint f(void) { return X; }\n', 'h.h': '#define X 1\n'})
    facts = extract(tmp_path, after=f'printf "#define X 2\\n" > {tmp_path}/h.h')
    passes = [run for run in runs(tmp_path) if '-dI' in run.split()]
    assert len(passes) == 2
    digest = compiler.read_input(tmp_path / 'h.h')[1]['sha256']
    assert facts['input']['dependencies'][str(tmp_path / 'h.h')] == digest
    assert returned(facts) == ['2']
    final(facts)


def test_recorded_header_edited_after_pass_one_fails_final_verification(tmp_path):
    write(tmp_path, {'a.c': '#include "h.h"\nint f(void) { return X; }\n', 'h.h': '#define X 1\n'})
    earlier = extract(tmp_path)
    (tmp_path / 'runs.log').unlink()
    facts = extract(tmp_path, after=f'printf "#define X 2\\n" > {tmp_path}/h.h')
    assert len([run for run in runs(tmp_path) if '-dI' in run.split()]) == 1 and returned(facts) == ['1']
    with pytest.raises(RuntimeError, match='consumed input changed during collection'):
        final(earlier, facts)


@pytest.mark.parametrize('case', ['angled', 'quoted-including-directory', 'include_next', 'guarded-repeat',
                                  'computed', 'omitted-directory', 'directory-symlink', 'duplicate-directory'])
def test_shadow_created_before_publication_fails_final_verification(tmp_path, case):
    files = {'inc1/.keep': '', 'inc2/h.h': '#ifndef H\n#define H\n#define X 2\n#endif\n'}
    arguments, shadow = ['-Iinc1', '-Iinc2'], 'inc1/h.h'
    source = '#include <h.h>\n'
    if case == 'quoted-including-directory':
        files['sub/b.h'] = '#include "h.h"\n'
        source, shadow = '#include "sub/b.h"\n', 'sub/h.h'
    elif case == 'include_next':
        files.update({'inc0/h.h': '#include_next <h.h>\n'})
        arguments = ['-Iinc0', '-Iinc1', '-Iinc2']
    elif case == 'guarded-repeat':
        files['sub/b.h'] = '#include "h.h"\n'
        source, arguments, shadow = '#include <h.h>\n#include "sub/b.h"\n', ['-Iinc2'], 'sub/h.h'
    elif case == 'computed':
        source = '#define HEADER <h.h>\n#include HEADER\n'
    elif case == 'omitted-directory':
        arguments, shadow = ['-Imissing', '-Iinc2'], 'missing/h.h'
    elif case in ('directory-symlink', 'duplicate-directory'):
        files['empty/.keep'] = ''
        files['full/h.h'] = '#define X 1\n'
        arguments, shadow = ['-Ilink', '-Iinc2'], None
        if case == 'duplicate-directory':
            # link duplicates inc1, so the compiler leaves it out of the search order.
            arguments = ['-Iinc1', '-Ilink', '-Iinc2']
    write(tmp_path, {**files, 'a.c': source + 'int f(void) { return X; }\n'})
    if case == 'directory-symlink':
        (tmp_path / 'link').symlink_to('empty')
    elif case == 'duplicate-directory':
        (tmp_path / 'link').symlink_to('inc1')
    facts = extract(tmp_path, arguments)
    assert stat_replayed(facts)
    final(facts)
    if shadow is None:
        (tmp_path / 'link').unlink()
        (tmp_path / 'link').symlink_to('full')
    else:
        write(tmp_path, {shadow: '#define X 1\n'})
    with pytest.raises(RuntimeError, match='compiler include (selection|search directory) changed during collection'):
        final(facts)


@pytest.mark.parametrize('known', [False, True])
def test_has_include_probe_changing_after_pass_one_is_detected(tmp_path, known):
    write(tmp_path, {'a.c': '#include "q.h"\nint f(void) { return X; }\n',
                     'q.h': '#if __has_include("p.h")\n#define X 1\n#else\n#define X 2\n#endif\n'})
    earlier = []
    if known:
        # An earlier translation unit recorded q.h and observed the paths its probe tests.
        earlier.append(extract(tmp_path))
        (tmp_path / 'runs.log').unlink()
    facts = extract(tmp_path, after=f'touch {tmp_path}/p.h')
    passes = [run for run in runs(tmp_path) if '-dI' in run.split()]
    if known:
        assert len(passes) == 1 and returned(facts) == ['2']
    else:
        # First observed after the edit: pass 1 ran again, and its expansion took the probe as true.
        assert len(passes) == 2 and returned(facts) == ['1'] and stat_replayed(facts)
        final(*earlier, facts)
        (tmp_path / 'p.h').unlink()
    with pytest.raises(RuntimeError, match='compiler include probe changed during collection'):
        final(*earlier, facts)


def test_unmodelled_lookups_repeat_pass_one_after_expansion(tmp_path):
    write(tmp_path, {'pre.h': '#define P 1\n', 'a.c': 'int f(void) { return P; }\n',
                     'q.h': '#if __has_include(NAME)\n#endif\n', 'b.c': '#define NAME "x.h"\n#include "q.h"\nint g(void) { return 1; }\n'})
    facts = extract(tmp_path, ['-include', 'pre.h'])
    assert facts['input']['selection_check'] == {'method': 'compiler dependency pass repeated',
                                                 'reason': 'command-line include option'}
    assert facts['_selection'] == []
    (tmp_path / 'runs.log').unlink()
    (tmp_path / 'edited').unlink(missing_ok=True)
    facts = extract(tmp_path, source='b.c')
    assert facts['input']['selection_check']['reason'].startswith('__has_include without a literal operand')


def test_dependency_rule_disagreeing_with_the_expansion_is_rejected(tmp_path):
    write(tmp_path, {'a.c': '#include "h.h"\nint f(void) { return X; }\n', 'h.h': '#define X 1\n'})
    # A compiler whose dependency rule (on stderr) loses h.h, which its expansion still enters.
    script = tmp_path / 'cc'
    script.write_text('#!/bin/sh\nfor a in "$@"; do if [ "$a" = "-dI" ]; then gcc "$@" 2> err; s=$?; '
                      'sed "s/ h\\.h//" err >&2; exit $s; fi; done\nexec gcc "$@"\n')
    script.chmod(0o755)
    entry = {'directory': str(tmp_path), 'arguments': [str(script), '-c', 'a.c', '-o', 'a.o'], 'file': 'a.c'}
    snapshot = compiler.recorded(tmp_path / 'a.c')
    record = {**entry, 'physical': snapshot['resolved'], 'identity': snapshot['resolved'],
              'object': str(tmp_path / 'a.o'), '_source_snapshot': snapshot, '_response_snapshots': {}}
    with pytest.raises(RuntimeError, match='compiler dependency rule disagrees with the files its expansion entered'):
        inventory._extract(record, compiler.authority(ROOT), ROOT, full_semantics=True)


def test_pass_two_reads_no_include(tmp_path):
    write(tmp_path, {'a.c': 'int f(void) { return 1; }\n', 'h.h': 'int h;\n'})
    record = {'directory': str(tmp_path), 'expanded_arguments': ['gcc', '-c', 'a.c'], 'arguments': ['gcc', '-c', 'a.c'],
              'physical': str(tmp_path / 'a.c'), 'file': 'a.c'}
    text, _ = compiler.invoke(record, compiler._PASS_ONE)
    scan = compiler.scan_dependency_pass(text, 'a.c')
    record = {**record, '_pass_one': text, '_consumed': set()}
    assert 'int f(void)' in compiler.expand({**record, '_scan': scan}, compiler.authority(ROOT))[1]
    # An include directive left in pass 2's input is entered by pass 2 and rejected.
    stray = {**scan, 'source': scan['source'] + '\n#include "h.h"\n'}
    with pytest.raises(RuntimeError, match='compiler expansion entered files outside its dependency pass'):
        compiler.expand({**record, '_scan': stray}, compiler.authority(ROOT))


def gcc_mm(directory, arguments, source='a.c'):
    out = subprocess.run(['gcc', *arguments, '-MM', source], cwd=directory, capture_output=True, text=True, check=True).stdout
    return {str((Path(directory) / path).resolve()) for path in out.split(':', 1)[1].replace('\\\n', ' ').split()}


@pytest.mark.parametrize('case', ['listed', 'not-listed'])
def test_file_entered_in_and_outside_system_headers_takes_the_compiler_mm_set(tmp_path, case):
    if case == 'listed':
        # S/x.h is first stacked from S/a.h (system) as "x.h", then as "S/x.h" from a.c: a second
        # file entry, first stacked outside a system header, so -MM lists it.
        files = {'S/a.h': '#include "x.h"\n', 'S/x.h': '#ifndef XH\n#define XH\nint xv;\n#endif\n',
                 'a.c': '#include <a.h>\n#include "S/x.h"\nint f(void) { return 1; }\n'}
        arguments = ['-isystem', 'S']
    else:
        # P/x.h is first stacked from S/a.h (system) and stacked again as the same file entry from
        # a.c, outside a system header: -MM does not list it.
        files = {'S/a.h': '#include <x.h>\n', 'P/x.h': 'int xv;\n',
                 'a.c': '#include <a.h>\n#include <x.h>\nint f(void) { return 1; }\n'}
        arguments = ['-IP', '-isystem', 'S']
    write(tmp_path, files)
    facts = extract(tmp_path, arguments)
    header = str(tmp_path / ('S/x.h' if case == 'listed' else 'P/x.h'))
    assert (header in facts['coverage']['project_headers']) == (case == 'listed')
    assert set(facts['coverage']['project_headers']) | {str(tmp_path / 'a.c')} == gcc_mm(tmp_path, arguments)
    assert any('-MM' in receipt['argv'] for receipt in facts['input']['receipts'])


def test_mm_run_disagreeing_with_pass_one_is_rejected(tmp_path):
    write(tmp_path, {'S/a.h': '#include "x.h"\n', 'S/x.h': '#ifndef XH\n#define XH\nint xv;\n#endif\n',
                     'h.h': 'int h;\n', 'a.c': '#include <a.h>\n#include "S/x.h"\n#include "h.h"\nint f(void) { return 1; }\n'})
    script = wrapper(tmp_path)
    script.write_text('#!/bin/sh\nfor a in "$@"; do if [ "$a" = "-MM" ]; then gcc "$@" | sed "s| h\\.h||"; exit 0; fi; done\n'
                      'exec gcc "$@"\n')
    with pytest.raises(RuntimeError, match='compiler dependency selection changed during collection'):
        extract(tmp_path, ['-isystem', 'S'])


def tokens(text):
    return re.findall(r'"(?:\\.|[^"\\\n])*"|\'(?:\\.|[^\'\\\n])*\'|\w+|\S', text)


def expansion(directory, source, arguments=(), files=None):
    """Prepare and expand a.c, with the C tokens of the expansion and of plain gcc -E."""
    write(directory, {'a.c': source, **(files or {})})
    compdb = compiler.authority(ROOT)
    script = wrapper(directory)
    entry = {'directory': str(directory), 'arguments': [str(script), *arguments, '-c', 'a.c', '-o', 'a.o'], 'file': 'a.c'}
    snapshot = compiler.recorded(directory / 'a.c')
    record = {**entry, 'physical': snapshot['resolved'], 'identity': snapshot['resolved'], 'object': str(directory / 'a.o'),
              '_source_snapshot': snapshot, '_response_snapshots': {}}
    prepared = compiler.prepare(record, compdb)
    _, code, _, contracts, receipts, method = compiler.expand(prepared, compdb)
    plain = subprocess.run(['gcc', *arguments, '-E', 'a.c'], cwd=directory, capture_output=True, text=True, check=True).stdout
    return tokens(code), tokens(compiler.unmark(plain, str(directory))[0]), method, receipts


ROUTED = {
    'push-pop-defined': '#define A 1\n#pragma push_macro("A")\n#undef A\n#define A 2\n#pragma pop_macro("A")\nint f = A;\n',
    'push-pop-kept': '#define A 1\n#pragma push_macro("A")\nint q;\n#pragma pop_macro("A")\nint f = A;\n',
    'push-pop-defined-commented-undef': ('#define A 1\n/*\n#undef A\n*/\n#pragma push_macro("A")\n#undef A\n#define A 2\n'
                                         '#pragma pop_macro("A")\nint f = A;\n'),
    'pragma-operator-push-pop': ('#define A 1\n_Pragma("push_macro(\\"A\\")")\n#undef A\n_Pragma("pop_macro(\\"A\\")")\n'
                                 '#if A\nint yes;\n#else\nint no;\n#endif\n'),
    'pragma-operator-pasted-push-pop': ('#define S(x) _Pragma(#x)\n#define P(a, b) S(a##b)\n#define A 1\n'
                                        'P(push_, macro("A"))\n#undef A\nS(pop_macro("A"))\n#if A\nint yes;\n#else\nint no;\n#endif\n'),
    'pragma-operator-system-header': '#include "psh.h"\nint m = PSH;\n',
    'base-file': 'const char *b = __BASE_FILE__;\n',
    'base-file-in-header': '#include "lvl.h"\nint x;\n',
    'base-file-pasted': '#define CAT(a, b) a##b\nconst char *b = CAT(__BASE, _FILE__);\n',
    'timestamp': 'const char *t = __TIMESTAMP__;\n',
    'timestamp-pasted': '#define CAT(a, b) a##b\nconst char *t = CAT(__TIME, STAMP__);\n',
}
UNCHANGED = {
    'file': 'const char *f = __FILE__; const char *n = __FILE_NAME__;\n#include "lvl2.h"\n',
    'line': 'int a = __LINE__;\n#line 100\nint b = __LINE__;\n#define L __LINE__\nint c = L;\n',
    'line-file': '#line 7 "other.c"\nconst char *f = __FILE__; int c = __LINE__;\n',
    'include-level': '#include "lvl2.h"\nint l0 = __INCLUDE_LEVEL__;\n#if __INCLUDE_LEVEL__ == 0\nint top;\n#endif\n',
    'counter-in-text': '#define C __COUNTER__\nint a = C; int b = __COUNTER__;\n',
    'push-pop-undefined': ('#pragma push_macro("signed")\n#define signed +\n#if signed 1 == 1\nint one;\n#endif\n'
                           '#undef signed\n#pragma pop_macro("signed")\nsigned int s;\n'),
    'push-pop-after-undef': ('#define unsigned signed\ntypedef unsigned long sl;\n#undef unsigned\n#pragma push_macro("unsigned")\n'
                             '#define unsigned +\n#if unsigned 1 == 1\nint u;\n#endif\n#undef unsigned\n'
                             '#pragma pop_macro("unsigned")\nunsigned int x;\n'),
    'pragma-operator-other': '#define P(x) _Pragma(#x)\nP(GCC diagnostic push)\nint p;\nP(GCC diagnostic pop)\n',
    'system-header': '#include "sh.h"\nint m = SH;\n',
    'dependency': '#pragma GCC dependency "sh.h"\nint d;\n',
    'poison': 'int x;\n#pragma GCC poison unused_name\nint z;\n',
    'once': '#include "po.h"\n#include "po.h"\nint q = PO;\n',
    'ident-assert': '#ident "hello"\n#assert machine(foo)\n#if #machine(foo)\nint yes;\n#endif\n',
    'feature-tests': 'int a = __has_attribute(packed); int b = __has_builtin(__builtin_expect);\n',
    'va-opt': '#define V(...) __VA_OPT__(x) __VA_ARGS__\nint V(a) = 1;\n',
    'stdc': 'int s = __STDC__; long v = __STDC_VERSION__; int h = __STDC_HOSTED__;\n',
    'continued-row': '#include "x.h"\nint y = 1 \\\n#include "x.h"\n;\n',
}
HEADERS = {'lvl.h': 'const char *hb = __BASE_FILE__;\n', 'lvl2.h': 'int inc_level = __INCLUDE_LEVEL__;\n',
           'sh.h': '#pragma GCC system_header\n#define SH 3\nint s;\n', 'x.h': '#ifndef XG\n#define XG\n#endif\n', 'po.h': '#pragma once\n#define PO 4\nint po;\n',
           'psh.h': '#define S(x) _Pragma(#x)\nS(GCC system_header)\n#define PSH 5\nint p;\n'}


@pytest.mark.parametrize('case', sorted(ROUTED))
def test_construct_pass_two_cannot_reproduce_routes_the_unit_to_plain_expansion(tmp_path, case):
    ours, plain, method, receipts = expansion(tmp_path, ROUTED[case], files=HEADERS)
    assert method['method'] == 'plain -E' and method['contracts'].startswith('unassessed')
    assert ours == plain
    # Pass 2 runs only where nothing in pass 1's output names the construct: a pasted builtin and a
    # _Pragma operand reach pass 2 and fail it.
    assert any('-fpreprocessed' in receipt['argv'] for receipt in receipts) == case.startswith(('pragma-operator', 'base-file-pasted', 'timestamp-pasted'))


def test_command_line_pragma_operator_routes_the_unit(tmp_path):
    arguments = ['-DPUSH=_Pragma("push_macro(\\"A\\")")', '-DPOP=_Pragma("pop_macro(\\"A\\")")']
    ours, plain, method, _ = expansion(tmp_path, '#define A 1\nPUSH\n#undef A\n#define A 2\nPOP\n#if A == 1\nint one;\n#else\nint two;\n#endif\n',
                                       arguments)
    assert method == {'method': 'plain -E', 'reason': 'pass 2 used push_macro',
                      'contracts': 'unassessed: the contract probe expands only from pass 1 output'}
    assert ours == plain and 'one' in ours


def test_pragma_operator_once_fails_rather_than_repeat_the_header(tmp_path):
    write(tmp_path, {'o.h': '_Pragma("once")\nint g(void) { return 7; }\n', 'a.c': '#include "o.h"\n#include "o.h"\n'})
    with pytest.raises(RuntimeError, match='compiler expansion entered files outside its dependency pass'):
        extract(tmp_path)


@pytest.mark.parametrize('case', sorted(UNCHANGED))
def test_construct_pass_two_reproduces_keeps_two_runs(tmp_path, case):
    ours, plain, method, receipts = expansion(tmp_path, UNCHANGED[case], files=HEADERS)
    assert method == {'method': 'pass 1 output expanded from stdin'}
    assert ours == plain
    assert [receipt['argv'].count('-fpreprocessed') for receipt in receipts] == [1]


def test_counter_expanded_in_a_directive_fails_pass_one(tmp_path):
    write(tmp_path, {'a.c': 'int a = __COUNTER__;\n#if __COUNTER__ == 1\nint one;\n#endif\n'})
    with pytest.raises(RuntimeError, match='__COUNTER__ expanded inside directive'):
        extract(tmp_path)


def test_routed_unit_facts_follow_plain_expansion(tmp_path):
    write(tmp_path, {'a.c': '#define A 1\n#pragma push_macro("A")\n#undef A\n#define A 2\n#pragma pop_macro("A")\n'
                            'int f(void) { return A; }\nconst char *g(void) { return __BASE_FILE__; }\n'})
    facts = extract(tmp_path)
    assert returned(facts) == ['1'] and returned(facts, 'g') == ['"a.c"']
    assert facts['input']['expansion']['method'] == 'plain -E' and facts['contracts'] == {}
    final(facts)


def test_directives_hash_identifies_the_text_definitions_are_read_from(tmp_path):
    write(tmp_path, {'a.c': '#include "h.h"\nint f(void) { return X; }\n', 'h.h': '#define X 1\n'})
    facts = extract(tmp_path)
    record = {'directory': str(tmp_path), 'expanded_arguments': ['gcc', '-c', 'a.c'], 'arguments': ['gcc', '-c', 'a.c'],
              'physical': str(tmp_path / 'a.c'), 'file': 'a.c'}
    text, _ = compiler.invoke(record, compiler._PASS_ONE)
    source = compiler.scan_dependency_pass(text, 'a.c')['source']
    assert '#include' not in source
    assert facts['input']['compiler_directives_sha256'] == hashlib.sha256(source.encode()).hexdigest()


def test_has_include_in_a_macro_is_constrained_in_the_evaluating_file_directory(tmp_path):
    write(tmp_path, {'inc/a.h': '#define HAS_CFG __has_include("cfg.h")\n',
                     'a.c': '#include <a.h>\n#if HAS_CFG\n#define X 1\n#else\n#define X 2\n#endif\nint f(void) { return X; }\n'})
    facts = extract(tmp_path, ['-Iinc'])
    assert stat_replayed(facts) and returned(facts) == ['2']
    final(facts)
    write(tmp_path, {'cfg.h': ''})
    with pytest.raises(RuntimeError, match='compiler include probe changed during collection'):
        final(facts)


@pytest.mark.parametrize('response', [False, True])
def test_has_include_in_a_command_line_macro_is_constrained(tmp_path, response):
    definition = '-DHAS_CFG=__has_include("cfg.h")'
    if response:
        write(tmp_path, {'defs.rsp': definition.replace('"', '\\"') + '\n'})
    write(tmp_path, {'a.c': '#if HAS_CFG\n#define X 1\n#else\n#define X 2\n#endif\nint f(void) { return X; }\n'})
    facts = extract(tmp_path, ['@defs.rsp' if response else definition])
    assert stat_replayed(facts) and returned(facts) == ['2']
    final(facts)
    write(tmp_path, {'cfg.h': ''})
    with pytest.raises(RuntimeError, match='compiler include probe changed during collection'):
        final(facts)


@pytest.mark.parametrize('source', ['#define HI __has_include\n#if HI("cfg.h")\n#define X 1\n#else\n#define X 2\n#endif\n',
                                    '#define HN __has_include_next\n#define X 2\n'])
def test_has_include_without_a_literal_call_repeats_pass_one(tmp_path, source):
    write(tmp_path, {'a.c': source + 'int f(void) { return X; }\n'})
    facts = extract(tmp_path)
    assert facts['input']['selection_check']['method'] == 'compiler dependency pass repeated'
    assert facts['input']['selection_check']['reason'].startswith('__has_include without a literal operand')
    assert returned(facts) == ['2']


def test_has_include_probe_classification(tmp_path):
    path = tmp_path / 'p.h'
    path.write_text('/* __has_include argument (GCC PR 80005) */\n#ifdef __has_include\n'
                    '# if __has_include ("linux/stat.h") && defined(__has_include)\n# endif\n#endif\n'
                    '#ifndef __has_include\n#define __has_include(x) 0\n#endif\n'
                    '#if __has_include_next(<n.h>) // __has_include alone in a comment\n#endif\n')
    compiler.recorded(path)
    assert compiler._PROBES[str(path)] == ([(False, False, 'linux/stat.h'), (True, True, 'n.h')], None)


def test_not_a_directory_include_entry_becoming_a_directory_fails_final_verification(tmp_path):
    write(tmp_path, {'inc2/h.h': '#define X 2\n', 'a.c': '#include <h.h>\nint f(void) { return X; }\n', 'nd': ''})
    facts = extract(tmp_path, ['-Ind', '-Iinc2'])
    assert stat_replayed(facts)
    final(facts)
    (tmp_path / 'nd').unlink()
    write(tmp_path, {'nd/h.h': '#define X 1\n'})
    with pytest.raises(RuntimeError, match='compiler include search directory changed during collection'):
        final(facts)


def test_warnings_suppressed_repeats_pass_one(tmp_path):
    write(tmp_path, {'a.c': 'int f(void) { return 1; }\n'})
    facts = extract(tmp_path, ['-w'])
    assert facts['input']['selection_check']['reason'].startswith('warnings suppressed (-w)')


def test_left_out_directory_gaining_a_link_to_the_same_file_fails_final_verification(tmp_path):
    write(tmp_path, {'sys/s.h': '#define S 1\n', 'a.c': '#include <s.h>\nint f(void) { return S; }\n'})
    arguments = ['-Imissing', '-isystem', 'sys']
    facts = extract(tmp_path, arguments)
    assert stat_replayed(facts) and facts['coverage']['project_headers'] == []
    final(facts)
    (tmp_path / 'missing').mkdir()
    (tmp_path / 'missing' / 's.h').symlink_to('../sys/s.h')
    # The same file, now found through missing/ as a project header.
    listed = subprocess.run(['gcc', *arguments, '-MM', 'a.c'], cwd=tmp_path, capture_output=True, text=True, check=True).stdout
    assert 'missing/s.h' in listed
    with pytest.raises(RuntimeError, match='compiler include search directory changed during collection'):
        final(facts)


def test_chain_directories_becoming_duplicates_fails_final_verification(tmp_path):
    write(tmp_path, {'A/p.h': '#define P 1\n', 'B/.keep': '', 'a.c': '#include <p.h>\nint f(void) { return P; }\n'})
    arguments = ['-IA', '-isystem', 'B']
    facts = extract(tmp_path, arguments)
    assert str(tmp_path / 'A/p.h') in facts['coverage']['project_headers']
    final(facts)
    (tmp_path / 'B').rename(tmp_path / 'B.old')
    (tmp_path / 'B').symlink_to('A')
    assert str(tmp_path / 'A' / 'p.h') not in gcc_mm(tmp_path, arguments)
    with pytest.raises(RuntimeError, match='compiler include search directory changed during collection'):
        final(facts)
