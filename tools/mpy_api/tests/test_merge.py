"""Merge of generated declarations into a template."""
import ast

import pytest

from mpy_api.merge import MergeError, class_table, merge_template

GENERATED_HEAD = "from typing import Any as _mpy_Any\n"


def names(text):
    return {node.name for node in ast.parse(text).body if isinstance(node, (ast.ClassDef, ast.FunctionDef))}


def members(text, cls):
    node = next(n for n in ast.parse(text).body if isinstance(n, ast.ClassDef) and n.name == cls)
    return {n.name for n in node.body if isinstance(n, (ast.ClassDef, ast.FunctionDef))}


BUILTINS = frozenset("int str bytes list dict tuple object bool type".split())


def merged(generated, template, **kwargs):
    """A module merge against pack builtins that have ``BUILTINS``; ``builtin_names=None`` merges builtins itself."""
    kwargs.setdefault("builtin_names", BUILTINS)
    return merge_template(GENERATED_HEAD + generated, template, what="fixture", **kwargs)


def test_names_the_build_lacks_are_dropped_and_helpers_stay():
    template = '''
import sys
from typing import Generic, TypeVar
_T = TypeVar("_T")
class ellipsis: ...
class thing(Generic[_T]):
    def keep(self) -> int: ...
    def drop(self) -> int: ...
    def __len__(self) -> int: ...
class gone: ...
def func(x: int) -> int: ...
def other() -> None: ...
'''
    result = merged("class thing:\n    def keep(self) -> _mpy_Any: ...\ndef func(arg0: _mpy_Any, /) -> _mpy_Any: ...\n", template,
                    keep=["ellipsis"])
    assert names(result.text) == {"ellipsis", "thing", "func"}
    assert members(result.text, "thing") == {"keep", "__len__"}
    assert "class thing(Generic[_T]):" in result.text and '_T = TypeVar("_T")' in result.text
    assert "gone" in result.report["dropped"] and "thing.drop" in result.report["dropped_members"]


def test_open_class_keeps_every_template_member_and_closed_class_does_not():
    template = "class a:\n    def m(self) -> int: ...\n    def n(self) -> int: ...\nclass b:\n    def m(self) -> int: ...\n    def n(self) -> int: ...\n"
    generated = ("class a:\n    def __getattr__(self, name: _mpy_Any) -> _mpy_Any: ...\n"
                 "class b:\n    def m(self) -> _mpy_Any: ...\n")
    result = merged(generated, template)
    assert members(result.text, "a") == {"m", "n", "__getattr__"}
    assert members(result.text, "b") == {"m"}
    assert result.report["open_classes"] == ["a"]


def test_module_without_catch_all_stays_closed():
    result = merged("def only() -> int: ...\n", "def only() -> int: ...\ndef extra() -> int: ...\n")
    assert names(result.text) == {"only"} and "__getattr__" not in result.text


def test_fully_proven_generated_signature_wins_and_refinements_do_not():
    template = ('from typing import overload\n@overload\ndef f(x: int) -> int: ...\n@overload\ndef f(x: str) -> str: ...\n'
                "def g(x: int) -> list[bytes]: ...\ndef h(x: int) -> str: ...\n")
    generated = "def f() -> int: ...\ndef g() -> list: ...\ndef h(arg0: _mpy_Any, /) -> int: ...\n"
    result = merged(generated, template)
    assert "def f() -> int" in result.text and "@overload" not in result.text
    assert "def g(x: int) -> list[bytes]" in result.text
    assert "def h(x: int) -> int" in result.text
    assert result.report["generated_wins"] == ["f"] and result.report["return_overrides"] == ["h"]


def test_names_only_the_build_has_are_appended_unless_a_base_provides_them():
    typing = class_table(ast.parse("class Sequence:\n    def index(self) -> int: ...\n"))
    template = "from typing import Sequence\nclass seq(Sequence[int]):\n    def __len__(self) -> int: ...\n"
    generated = "class seq:\n    def index(self) -> _mpy_Any: ...\n    def extra(self) -> _mpy_Any: ...\nclass novel: ...\n"
    result = merged(generated, template, typing=typing)
    assert members(result.text, "seq") == {"__len__", "extra"}
    assert "novel" in names(result.text)
    assert result.report["base_provided_members"] == ["seq.index"]


def test_class_whose_abstract_members_are_gone_is_rebased():
    typing = class_table(ast.parse(
        "from abc import abstractmethod\nclass Sequence:\n    def index(self) -> int: ...\n"
        "class MutableSequence(Sequence):\n    @abstractmethod\n    def insert(self) -> None: ...\n"))
    template = "from typing import MutableSequence\nclass buf(MutableSequence[int]):\n    def insert(self) -> None: ...\n"
    result = merged("class buf: ...\n", template, typing=typing)
    assert "class buf(Sequence[int])" in result.text
    assert result.report["abstract_demoted"] == ["buf: MutableSequence[int] -> Sequence[int]"]


def test_all_is_dropped_and_unused_imports_are_pruned_but_reexports_and_stars_stay():
    template = ('import sys\nimport os\nfrom typing import Any, Final\nfrom a import b as b\nfrom c import *\n'
                '__all__ = ["x"]\nif sys.version_info >= (3, 9):\n    __all__ += ["y"]\nx: int\n')
    result = merged("x: int\n", template)
    tree = ast.parse(result.text)
    imported = {alias.asname or alias.name for node in tree.body if isinstance(node, (ast.Import, ast.ImportFrom)) for alias in node.names}
    assert "os" not in imported and "Final" not in imported and {"b", "*"} <= imported
    assert "__all__" not in result.text
    assert "pruned_imports" in result.report


def test_future_imports_stay_first():
    result = merged("def extra(arg0: _mpy_Any) -> _mpy_Any: ...\n", "from __future__ import annotations\nx: int\n")
    assert result.text.splitlines()[1:3] == ["from __future__ import annotations", "from typing import Any as _mpy_Any"]


def test_private_helper_reading_a_dropped_name_is_dropped_with_its_readers():
    template = ("from typing import TypeAlias\nclass gone: ...\n_Alias: TypeAlias = gone | int\n_Second = list[_Alias]\n"
                "def f(x: int) -> int: ...\n")
    result = merged("def f() -> int: ...\n", template)
    assert "_Alias" not in result.text and "_Second" not in result.text
    assert result.report["dropped_readers"] == ["_Alias", "_Second"]


def test_generated_imports_supply_names_the_appended_declarations_read():
    generated = "from typing import Callable\ndef hook(cb: Callable[[int], int]) -> None: ...\n"
    assert "from typing import Callable" in merged(generated, "x: int\n").text


def test_merge_is_deterministic():
    template = "class a:\n    def m(self) -> int: ...\ndef f(x: int) -> int: ...\n"
    generated = "class a:\n    def m(self) -> _mpy_Any: ...\ndef f(arg0: _mpy_Any, /) -> _mpy_Any: ...\n"
    assert merged(generated, template).text == merged(generated, template).text


def test_a_statement_reading_a_class_the_build_lacks_is_dropped_not_passed_through_the_host_builtins():
    template = "class float: ...\nclass int:\n    def __truediv__(self, other: int) -> float: ...\n    def __neg__(self) -> int: ...\n"
    result = merged("class int: ...\n", template, builtin_names=None)
    assert "float" not in result.text and "def __neg__" in result.text
    assert result.report["dropped_readers"] == ["int.__truediv__"] and result.report["dangling"] == []


def test_dropping_a_reader_keeps_the_generated_declaration_of_the_same_name():
    template = "class gone: ...\nclass keep:\n    def m(self) -> gone: ...\n"
    generated = "class keep:\n    def m(self) -> _mpy_Any: ...\n"
    result = merged(generated, template)
    assert result.report["dropped_readers"] == ["keep.m"] and "def m(self) -> _mpy_Any" in result.text
    assert "gone" not in result.text


def test_one_overload_reading_a_missing_class_is_dropped_and_the_others_stay():
    template = ("from typing import overload\nclass float: ...\n@overload\ndef f(x: int) -> int: ...\n"
                "@overload\ndef f(x: float) -> float: ...\n")
    result = merged("def f(arg0: _mpy_Any, /) -> _mpy_Any: ...\n", template, builtin_names={"int"})
    assert result.text.count("def f(") == 1 and "def f(x: int) -> int" in result.text


def test_a_generated_declaration_reading_a_name_nothing_defines_fails_the_merge():
    template = "def g(x: int) -> int: ...\n"
    generated = "def g(x: gone) -> int: ...\n"
    with pytest.raises(MergeError, match="gone"):
        merged(generated, template)
    assert merged(generated, template, strict=False).report["dangling"] == ["gone"]


def test_a_statement_dropped_for_a_missing_name_takes_its_readers_with_it():
    template = "class float: ...\nclass int: ...\n_Number = int | float\n_Pair = tuple[_Number, int]\ndef f(x: _Pair) -> int: ...\n"
    result = merged("class int: ...\ndef f(arg0: _mpy_Any, /) -> _mpy_Any: ...\n", template, builtin_names=None)
    assert "_Number" not in result.text and "_Pair" not in result.text
    assert result.report["dropped_readers"] == ["_Number", "_Pair", "f"]


def test_module_reading_a_class_the_pack_builtins_lack_loses_that_statement():
    template = "def f(x: float) -> int: ...\n"
    generated = "def f(arg0: _mpy_Any, /) -> _mpy_Any: ...\n"
    without = merged(generated, template, builtin_names={"int", "str"})
    assert "float" not in without.text and "def f(arg0" in without.text
    assert "def f(x: float)" in merged(generated, template, builtin_names={"int", "float"}).text


def test_bare_class_keeps_header_and_no_members_when_the_build_lacks_it():
    template = ("from typing import Generic, TypeVar\n_T = TypeVar(\"_T\")\n"
                "class slice(Generic[_T]):\n    def indices(self, n: int) -> tuple[int, int, int]: ...\n"
                "class int:\n    def __getitem__(self, s: slice[int]) -> int: ...\n")
    result = merged("class int: ...\n", template, bare=["slice"], builtin_names=None)
    assert "class slice(Generic[_T]):\n    ..." in result.text and "indices" not in result.text
    assert result.report["bare"] == ["slice"] and result.report["dangling"] == []
    absent = merged("class int: ...\n", template, builtin_names=None, strict=False)
    assert "class slice" not in absent.text


def test_bare_does_not_apply_when_the_build_has_the_class():
    template = "class slice:\n    def indices(self) -> int: ...\n"
    result = merged("class slice:\n    def indices(self) -> _mpy_Any: ...\n", template, bare=["slice"])
    assert "indices" in result.text and result.report["bare"] == []


def test_bare_class_the_template_lacks_is_an_empty_class():
    result = merged("class int: ...\n", "class int: ...\n", bare=["complex"], builtin_names=None)
    assert "class complex: ..." in result.text and result.report["bare"] == ["complex"]
