"""Per-tree node index answers the same questions as a recursive subtree walk; frontend facts are unchanged."""
import hashlib
import json

import pytest
from tree_sitter import Language, Parser
import tree_sitter_c

from mpy_api.frontend import CANONICAL_TOKEN, _tree_index, canonical, syntax_adapter


SOURCES = {
    'enums': """enum { A, B = 5, C };
enum named { X = A + 1, Y, Z = Y << 2 };
typedef enum { P = 0x10, Q = P | 1, R = (Q + 1) * 2 } mode_t;
int use(int value) {
    enum { L1 = 3, L2 };
    enum { L2_again = L1 + 1, L1 };
    if (value) { enum { Inner = 9 }; return value + Inner + A; }
    return L2 + C;
}
int more(int v) { enum { Only = 4 }; return v * Only + B; }
""",
    'function_pointers': """typedef int (*plain_t)(int a, char *b);
typedef void (*arrays_t)(int a[], int b);
typedef int (*nested_t)(int (*callback)(int), int x);
typedef int (*noproto_t)();
typedef int (*qualified_t)(void) __attribute__((noreturn));
int (*table[2])(int);
""",
    'aggregates': """typedef struct _mp_obj_type_t { int base; const char *name; int (*call)(int); } mp_obj_type_t;
typedef union { int a; char b; } mp_arg_val_t;
typedef struct { int x; struct { int y; } inner; } mp_arg_t;
typedef struct _tagged mp_obj_fun_builtin_var_t;
struct _tagged { int count; };
typedef int not_an_aggregate_t;
""",
    'control_and_pointers': """void (*fp)(int);
int proto (int);
int var;
int g(int a) {
    int (*local)(int);
    int *p = &a;
    *fp(a) = 1;
    (*fp)(a);
    local = proto;
    var++;
    --a;
loop:
    if (a) goto loop;
    switch (a) { case 1: break; default: break; }
    for (int i = 0; i < a; ++i) { while (i) { do { a += proto(i); } while (a < 5); } }
    return a ? proto(a) : var;
}
""",
    'syntax_errors': """int f(int a) { return a }
int = ;
int ok(int a) { return a; }
""",
    'comments_and_strings': """const char *s = "a//b"; /* block // comment */ int x = 1; // line # comment
const char t[] = "/* not a comment */";
int divide(int a, int b) { return a / b; /* trailing */ }
char slash = '/';
""",
    'legacy_and_attributes': """int legacy(first, second) int second; int first; { return first + second; }
static inline __attribute__((always_inline)) int attr(int a) { return a; }
int __attribute__((noinline)) pre(int a);
extern int external_decl;
static const int table[3] = {1, 2, 3};
int grid[2][3];
static int initialised = 4;
void variadic(int a, ...) { }
void unnamed(int, char) { }
""",
}


def parse(source):
    return Parser(Language(tree_sitter_c.language())).parse(source.encode())


def walk(node):
    yield node
    for child in node.children:
        yield from walk(child)


TYPE_SETS = [
    ('identifier',), ('enum_specifier',), ('enumerator',), ('declaration',), ('array_declarator',),
    ('pointer_expression',), ('struct_specifier', 'union_specifier'), ('function_declarator',),
    ('type_definition', 'declaration', 'assignment_expression'),
    ('goto_statement', 'labeled_statement', 'switch_statement'),
    ('array_declarator', 'function_declarator'), ('ERROR',), ('not_a_node_type',), ('compound_statement', 'identifier'),
]


@pytest.mark.parametrize('name', sorted(SOURCES))
def test_select_and_has_match_recursive_walk_for_every_subtree(name):
    tree = parse(SOURCES[name])
    index = _tree_index(tree, lambda node: '')
    for root in walk(tree.root_node):
        expected_nodes = list(walk(root))
        for types in TYPE_SETS:
            expected = [node.id for node in expected_nodes if node.type in types]
            assert [node.id for node in index.select(types, root)] == expected
            assert index.has(types, root) is bool(expected)


@pytest.mark.parametrize('name', sorted(SOURCES))
def test_select_all_and_errors_match_recursive_walk(name):
    tree = parse(SOURCES[name])
    index = _tree_index(tree, lambda node: '')
    everything = list(walk(tree.root_node))
    for types in TYPE_SETS:
        assert [node.id for node in index.select_all(types)] == [node.id for node in everything if node.type in types]
    assert [node.id for node in index.errors()] == [
        node.id for node in everything if node.type == 'ERROR' or node.is_missing]
    assert [node.id for node in index.nodes] == [node.id for node in everything]


def test_errors_cover_missing_tokens_and_error_nodes():
    tree = parse(SOURCES['syntax_errors'])
    index = _tree_index(tree, lambda node: '')
    errors = index.errors()
    assert errors
    assert not _tree_index(parse('int ok(void) { return 1; }'), lambda node: '').errors()


def test_extents_nest_like_the_tree():
    tree = parse(SOURCES['control_and_pointers'])
    index = _tree_index(tree, lambda node: '')
    for node in walk(tree.root_node):
        start = index.position[node.id]
        size = len(list(walk(node)))
        assert index.extent[start] - start == size


COMMENTED = [
    '', 'a / b', 'a // comment', 'a /* c */ b', '"//" x', "'/' y", 'x /* open', 'x /* a */ /* b */ y // z',
    '/* only */', '// only', '"/* in string */" z', 'a/b/c', 'int x = 1; // # 5 "inline.h"', 'a\n// multi\nb /* l1\nl2 */ c',
    '"a\\\nb" x', "'\\\n' y", '"abc\\\ndef" + "g"', 'x = "line one \\\n line two";', '"unterminated \\\n', "'a\\\n'",
    'a = "\\\n" / b', '"q\\\n/* not a comment */"',
]


@pytest.mark.parametrize('text', COMMENTED)
def test_canonical_matches_comment_stripping_tokenizer(text):
    expected = ' '.join(match[0] for match in CANONICAL_TOKEN.finditer(text)
                        if not match[0].startswith(('/*', '//')))
    assert canonical(text) == expected


def facts_digest(source):
    physical = '/fixture/index.c'
    origins = [(physical, line) for line in range(1, len(source.splitlines()) + 1)]
    result = syntax_adapter(source, origins, physical, {'dependencies': {physical: 'fixture'}})
    return hashlib.sha256(json.dumps(result, sort_keys=True, default=list).encode()).hexdigest()


# Digests of the frontend facts for these sources.
BASELINE_DIGESTS = {
    'aggregates': '83faeea967c42b232c02aab4b625195529c644dae200736f4f0de5b9a18b20db',
    'comments_and_strings': '4f52bdcb5efb06720cd7739fea40417fd5aef010f991b578aaa2bf340b348abb',
    'control_and_pointers': 'ba1c946ac594ddefdd2cfd7ae3d8ade900e2bb21f9b0bd863513fc19327b8954',
    'enums': '036a42f47bbf8956a617aff47b83f9a78ce32fcecd460434237c26c6397606aa',
    'function_pointers': '63d4a1d66466cbef3ffcf1fb1a18fd06be9006cd330c92ad3e6e8101b27ded18',
    'legacy_and_attributes': '47e61ffc9d285c2ef539c7b960d1d2ba5b22027987dce0dc68b0b1ae6d4b3d4b',
    'syntax_errors': 'd7f08534c29bba877face6d3c93b8c6904a32dd6f65ffcb00b4ddbbdf590e063',
}


@pytest.mark.parametrize('name', sorted(SOURCES))
def test_frontend_facts_match_baseline(name):
    assert facts_digest(SOURCES[name]) == BASELINE_DIGESTS[name]
