"""System header text is analysed once per distinct content and looked up by name afterwards; the
result is the one the whole text gives, and a run that cannot stand for itself stays in the text."""
import hashlib
import json

import pytest

from mpy_api import frontend
from mpy_api.frontend import clear_run_tables, syntax_adapter


OWN = '/proj/a.c'
SYSTEM = '/usr/include/sys.h'
OTHER = '/usr/include/other.h'

HEADER = ('typedef int sys_t;\n'
          'int sys_fn(int);\n'
          'int own_prototype(int);\n'
          'static inline int sys_inline(int value) { return value; }\n')
SOURCE = ('int own_prototype(int value) { return sys_fn(value) + sys_inline(value); }\n'
          'int cast(int value) { return (sys_t)(value); }\n'
          'int shadow(int value) { typedef long sys_t; return (sys_t)(value); }\n'
          'int unknown(int value) { return missing(value); }\n')


@pytest.fixture(autouse=True)
def fresh_tables():
    clear_run_tables()
    yield
    clear_run_tables()


@pytest.fixture
def analysed(monkeypatch):
    """The texts of the runs analysed for their tables, in order."""
    calls, real = [], frontend._run_tables

    def counting(data, origins):
        calls.append(data)
        return real(data, origins)
    monkeypatch.setattr(frontend, '_run_tables', counting)
    return calls


def unit(*parts):
    """The expanded text and line origins of ``parts``, each a (path, first line, text) in order."""
    code, origins = '', []
    for path, first, text in parts:
        code += text
        origins += [(path, first + line) for line in range(text.count('\n'))]
    return code, origins


def adapt(code, origins, dependencies=(SYSTEM, OTHER), **options):
    record = {'dependencies': {OWN: 'x', **{path: 'x' for path in dependencies}}, 'project_dependencies': [OWN]}
    return syntax_adapter(code, origins, OWN, record, **options)


def same(left, right):
    return json.dumps(left, sort_keys=True, default=list) == json.dumps(right, sort_keys=True, default=list)


def by_name(functions):
    return {function['name']: function for function in functions}


def test_blanked_system_runs_give_the_result_of_the_whole_text(analysed):
    code, origins = unit((SYSTEM, 1, HEADER), (OWN, 1, SOURCE))
    cached, whole = adapt(code, origins), adapt(code, origins, cached_runs=False)
    assert len(analysed) == 1 and same(cached, whole)
    _, functions, errors, frontend_tables = cached
    assert not errors
    functions = by_name(functions)
    # Callee identity and the typedef a cast names come from the system header, at its own lines.
    call = next(call for call in functions['own_prototype']['events']['calls'] if call['expression'] == 'sys_fn ( value )')
    assert call['callee']['kind'] == 'FUNCTION_DECL' and call['callee']['source_span']['source'] == SYSTEM
    assert functions['own_prototype']['declaration_identity']['source_span']['source'] == SYSTEM
    cast = functions['cast']['events']['returns'][0]['expression_node']
    assert cast['kind'] == 'cast_expression' and cast['typedef_authority']['source_span']['source'] == SYSTEM
    # The function's own typedef is the nearer binding.
    local = functions['shadow']['events']['returns'][0]['expression_node']
    assert local['typedef_authority']['source_span']['source'] == OWN
    assert functions['unknown']['events']['calls'][0]['callee'] is None
    assert frontend_tables['syntax_adaptation']['original_sha256'] == hashlib.sha256(code.encode()).hexdigest()


def test_a_run_analysed_once_serves_every_unit_that_holds_it(analysed):
    code, origins = unit((SYSTEM, 1, HEADER), (OWN, 1, SOURCE))
    adapt(code, origins)
    adapt(code, origins)
    other, other_origins = unit((SYSTEM, 1, HEADER), (OWN, 1, 'int another(void) { return sys_fn(1); }\n'))
    result = adapt(other, other_origins)
    assert len(analysed) == 1
    assert by_name(result[1])['another']['events']['calls'][0]['callee']['source_span']['source'] == SYSTEM
    clear_run_tables()
    adapt(code, origins)
    assert len(analysed) == 2


def test_distinct_contents_are_never_conflated(analysed):
    first, first_origins = unit((SYSTEM, 1, 'int foo(int);\n'), (OWN, 1, 'int use(void) { return foo(1) + bar(1); }\n'))
    second, second_origins = unit((SYSTEM, 1, 'int bar(int);\n'), (OWN, 1, 'int use(void) { return foo(1) + bar(1); }\n'))
    for _ in range(2):
        calls = {call['expression']: call['callee'] for call in by_name(adapt(first, first_origins)[1])['use']['events']['calls']}
        assert calls['foo ( 1 )']['kind'] == 'FUNCTION_DECL' and calls['bar ( 1 )'] is None
        calls = {call['expression']: call['callee'] for call in by_name(adapt(second, second_origins)[1])['use']['events']['calls']}
        assert calls['foo ( 1 )'] is None and calls['bar ( 1 )']['kind'] == 'FUNCTION_DECL'
    assert len(analysed) == 2


def test_the_same_text_from_other_lines_or_another_file_is_a_distinct_run(analysed):
    use = 'int use(void) { return foo(1); }\n'
    spans = []
    for path, first in ((SYSTEM, 1), (SYSTEM, 40), (OTHER, 1)):
        code, origins = unit((path, first, 'int foo(int);\n'), (OWN, 1, use))
        result = adapt(code, origins)
        assert same(result, adapt(code, origins, cached_runs=False))
        spans.append(by_name(result[1])['use']['events']['calls'][0]['callee']['source_span'])
    assert [(span['source'], span['start_line']) for span in spans] == [(SYSTEM, 1), (SYSTEM, 40), (OTHER, 1)]
    assert len(analysed) == 3


def test_system_runs_between_own_text_each_have_tables(analysed):
    code, origins = unit((OWN, 1, 'int own_first(void) { return 0; }\n'), (SYSTEM, 1, 'typedef int first_t;\n'),
                         (OWN, 2, 'int middle(int v) { return (first_t)(v) + (second_t)(v); }\n'),
                         (OTHER, 1, 'typedef int second_t;\nint second_fn(void);\n'),
                         (OWN, 3, 'int last(int v) { return second_fn() + (second_t)(v); }\n'))
    cached = adapt(code, origins)
    assert len(analysed) == 2 and same(cached, adapt(code, origins, cached_runs=False))
    assert not cached[2]
    functions = by_name(cached[1])
    middle = functions['middle']['events']['returns'][0]['expression_node']
    assert middle['left']['typedef_authority']['source_span']['source'] == SYSTEM
    # second_t is declared after this function, so it is not a type name here.
    assert 'typedef_authority' not in middle['right']
    last = functions['last']['events']['returns'][0]['expression_node']
    assert last['right']['typedef_authority']['source_span']['source'] == OTHER
    assert functions['last']['events']['calls'][0]['callee']['source_span']['source'] == OTHER


def test_text_without_a_system_dependency_is_not_split(analysed):
    code, origins = unit((OWN, 1, HEADER + SOURCE))
    assert same(adapt(code, origins), adapt(code, origins, cached_runs=False)) and not analysed


def test_lines_outside_the_captured_dependencies_stay_in_the_text(analysed):
    code, origins = unit(('<built-in>', 1, 'typedef int builtin_t;\n'), (SYSTEM, 1, HEADER), (OWN, 1, SOURCE))
    cached = adapt(code, origins)
    assert same(cached, adapt(code, origins, cached_runs=False))
    gaps = cached[3]['unassessed_source_declarations']
    assert [gap['source_span']['source'] for gap in gaps] == ['<built-in>']
    assert len(analysed) == 1


@pytest.mark.parametrize('header', ['int x = = 3;\n', 'int ok;\nint y y;\nint after;\n'])
def test_a_syntax_error_in_a_system_run_still_fails_the_unit(analysed, header):
    code, origins = unit((SYSTEM, 1, header), (OWN, 1, SOURCE))
    for _ in range(2):
        result = adapt(code, origins)
        assert result[2] and same(result, adapt(code, origins, cached_runs=False))
    # The run that cannot stand for itself is analysed once and stays in every unit's text.
    assert len(analysed) == 1


@pytest.mark.parametrize('header, message', [
    ('int broken(int ap) { return __builtin_va_arg(ap); }\n', '__builtin_va_arg'),
    ('int broken( { ;\n', 'unclosed C delimiter'),
])
def test_an_adaptation_failure_in_a_system_run_still_aborts(analysed, header, message):
    code, origins = unit((SYSTEM, 1, header), (OWN, 1, SOURCE))
    for _ in range(2):
        with pytest.raises(ValueError, match=message):
            adapt(code, origins)
    with pytest.raises(ValueError, match=message):
        adapt(code, origins, cached_runs=False)


def test_a_run_with_a_projected_form_stays_in_the_text(analysed):
    header = 'struct s { int a; };\nunsigned long offset = __builtin_offsetof(struct s, a);\n'
    code, origins = unit((SYSTEM, 1, header), (OWN, 1, SOURCE))
    cached = adapt(code, origins)
    assert same(cached, adapt(code, origins, cached_runs=False))
    replacements = cached[3]['syntax_adaptation']['replacements']
    assert [replacement['source_span']['source'] for replacement in replacements] == [SYSTEM]


@pytest.mark.parametrize('parts', [
    # The own text is completed by the run: blanked, it is an error.
    ((OWN, 1, 'typedef\n'), (SYSTEM, 1, 'int t;\n'), (OWN, 2, 'int use(t v) { return v; }\n')),
    # The run follows a bare struct specifier, which the run's declarator would continue: blanked,
    # the specifier stands alone and the declaration of y is lost.
    ((OWN, 1, 'struct s { int a; }\n'), (SYSTEM, 1, 'y;\n'), (OWN, 2, ';\nint after(void) { return 0; }\n')),
    ((OWN, 1, 'struct s { int a; }\n'), (SYSTEM, 1, 'int y;\n'), (OWN, 2, ';\nint after(void) { return 0; }\n')),
    # The run follows a declaration's missing keyword.
    ((OWN, 1, 'static\n'), (SYSTEM, 1, 'int h;\n'), (OWN, 2, ';\n')),
], ids=['completes-own-text', 'declarator-continues-a-specifier', 'follows-a-struct-specifier',
        'follows-an-incomplete-declaration'])
def test_a_run_not_isolated_from_the_text_around_it_is_processed_whole(monkeypatch, analysed, parts):
    verdicts, real = [], frontend._isolated
    monkeypatch.setattr(frontend, '_isolated', lambda *arguments: verdicts.append(real(*arguments)) or verdicts[-1])
    code, origins = unit(*parts)
    cached = adapt(code, origins)
    assert len(analysed) == 1 and verdicts == [False]
    assert same(cached, adapt(code, origins, cached_runs=False))


def test_the_declarator_a_run_gives_a_bare_struct_specifier_is_kept(analysed):
    code, origins = unit((OWN, 1, 'struct s { int a; }\n'), (SYSTEM, 1, 'y;\n'), (OWN, 2, ';\nint after(void) { return 0; }\n'))
    cached = adapt(code, origins)
    assert same(cached, adapt(code, origins, cached_runs=False))
    assert [declaration['name'] for declaration in cached[0]] == ['y']


@pytest.mark.parametrize('before', [';\n', 'int own_first(void) { return 0; }\n', 'typedef int own_t;\n'])
def test_a_run_after_a_complete_top_level_node_is_blanked(monkeypatch, analysed, before):
    verdicts, real = [], frontend._isolated
    monkeypatch.setattr(frontend, '_isolated', lambda *arguments: verdicts.append(real(*arguments)) or verdicts[-1])
    code, origins = unit((OWN, 1, before), (SYSTEM, 1, 'int h;\n'), (OWN, 2, 'int after(void) { return h; }\n'))
    cached = adapt(code, origins)
    assert len(analysed) == 1 and verdicts == [True]
    assert same(cached, adapt(code, origins, cached_runs=False))


def test_a_top_level_node_spanning_a_run_has_the_unit_processed_whole(monkeypatch, analysed):
    verdicts, real = [], frontend._isolated
    monkeypatch.setattr(frontend, '_isolated', lambda *arguments: verdicts.append(real(*arguments)) or verdicts[-1])
    code, origins = unit((OWN, 1, 'int f(void) {\n'), (SYSTEM, 1, 'int h;\n'), (OWN, 2, 'return h;\n}\n'))
    cached = adapt(code, origins)
    assert len(analysed) == 1 and verdicts == [False]
    assert same(cached, adapt(code, origins, cached_runs=False))


def test_a_unit_whose_blanked_text_fails_to_adapt_is_processed_whole(monkeypatch, analysed):
    real = frontend.adapt_source

    def failing(code, origins):
        if 'int f' in code and 'int h;' not in code:
            raise ValueError('blanked text refused')
        return real(code, origins)
    monkeypatch.setattr(frontend, 'adapt_source', failing)
    code, origins = unit((OWN, 1, 'int f(void) { return 0; }\n'), (SYSTEM, 1, 'int h;\n'), (OWN, 2, 'int g(void) { return 1; }\n'))
    cached = adapt(code, origins)
    assert len(analysed) == 1 and same(cached, adapt(code, origins, cached_runs=False))
    assert [function['name'] for function in cached[1]] == ['f', 'g']


def shared_directory(tmp_path):
    directory = tmp_path / 'runs'
    directory.mkdir()
    frontend.share_run_tables(str(directory))
    return directory


def another_process_starts():
    """A worker begins with no tables in memory, as a forked worker does."""
    frontend._RUN_TABLES.clear()


def test_a_run_analysed_by_one_process_is_taken_from_the_directory_by_the_next(tmp_path, analysed):
    directory = shared_directory(tmp_path)
    code, origins = unit((SYSTEM, 1, HEADER), (OWN, 1, SOURCE))
    first = adapt(code, origins)
    assert len(analysed) == 1 and len(list(directory.iterdir())) == 1
    another_process_starts()
    second = adapt(code, origins)
    assert len(analysed) == 1 and same(first, second) and same(second, adapt(code, origins, cached_runs=False))


def test_a_run_that_stays_in_the_text_is_shared_too(tmp_path, analysed):
    shared_directory(tmp_path)
    code, origins = unit((SYSTEM, 1, 'int x = = 3;\n'), (OWN, 1, SOURCE))
    first = adapt(code, origins)
    another_process_starts()
    second = adapt(code, origins)
    assert len(analysed) == 1 and first[2] and same(first, second)


def test_the_directory_keeps_distinct_runs_apart(tmp_path, analysed):
    directory = shared_directory(tmp_path)
    for header, path, first_line in (('int foo(int);\n', SYSTEM, 1), ('int bar(int);\n', SYSTEM, 1),
                                     ('int foo(int);\n', SYSTEM, 7), ('int foo(int);\n', OTHER, 1)):
        code, origins = unit((path, first_line, header), (OWN, 1, 'int use(void) { return foo(1) + bar(1); }\n'))
        adapt(code, origins)
        another_process_starts()
    assert len(analysed) == 4 and len(list(directory.iterdir())) == 4


@pytest.mark.parametrize('damage', [lambda blob: blob[:-1], lambda blob: blob[:40], lambda blob: bytes([blob[0] ^ 1]) + blob[1:],
                                    lambda blob: blob[:-1] + bytes([blob[-1] ^ 1]), lambda blob: b''])
def test_a_shared_table_that_changed_is_refused(tmp_path, analysed, damage):
    directory = shared_directory(tmp_path)
    code, origins = unit((SYSTEM, 1, HEADER), (OWN, 1, SOURCE))
    adapt(code, origins)
    (path,) = directory.iterdir()
    path.write_bytes(damage(path.read_bytes()))
    another_process_starts()
    with pytest.raises(RuntimeError, match='run tables changed during generation'):
        adapt(code, origins)


def adapt_in_a_worker(label):
    code, origins = unit((SYSTEM, 1, HEADER), (OWN, 1, SOURCE))
    return json.dumps(adapt(code, origins), sort_keys=True, default=list)


def test_forked_workers_racing_for_a_run_agree(tmp_path):
    import multiprocessing
    from concurrent.futures import ProcessPoolExecutor
    directory = shared_directory(tmp_path)
    with ProcessPoolExecutor(max_workers=4, mp_context=multiprocessing.get_context('fork')) as pool:
        results = list(pool.map(adapt_in_a_worker, range(8)))
    code, origins = unit((SYSTEM, 1, HEADER), (OWN, 1, SOURCE))
    assert set(results) == {json.dumps(adapt(code, origins, cached_runs=False), sort_keys=True, default=list)}
    assert len(list(directory.iterdir())) == 1


def test_clearing_the_tables_stops_sharing(tmp_path, analysed):
    directory = shared_directory(tmp_path)
    clear_run_tables()
    code, origins = unit((SYSTEM, 1, HEADER), (OWN, 1, SOURCE))
    adapt(code, origins)
    assert not list(directory.iterdir())
