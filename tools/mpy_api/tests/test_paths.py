"""PathNames: absolute paths become names, whatever text or structure holds them."""
import hashlib
import re
from pathlib import Path

import pytest

from mpy_api.chains import chain_root
from mpy_api.paths import PathNames

DIGEST = "a" * 64
NAMES = PathNames(
    [("/work/src", "micropython"), ("/work/src/sub", "sub"), ("/work/build dir", "build"),
     ("/usr/include/stdio.h", "system/usr/include/stdio.h")],
    digests=[(DIGEST, "b" * 64)])


@pytest.mark.parametrize("text, expected", [
    ("/work/src/py/obj.c", "micropython/py/obj.c"),
    ("/work/src", "micropython"),
    ("/work/src/sub/x.c:12", "sub/x.c:12"),
    ("/work/src/py/obj.c::object:/work/build dir/py/obj.o::argv:0f::name@/work/src/py/obj.c:7",
     "micropython/py/obj.c::object:build/py/obj.o::argv:0f::name@micropython/py/obj.c:7"),
    ("-I/work/src/py", "-Imicropython/py"),
    ("-Wl,-Map=/work/build dir/fixture.map", "-Wl,-Map=build/fixture.map"),
    ("/work/build\\ dir/fixture.map", "build/fixture.map"),
    ("/usr/include/stdio.h(member.o)", "system/usr/include/stdio.h(member.o)"),
    ("[\"/work/src/a\", \"/work/src/b\"]", "[\"micropython/a\", \"micropython/b\"]"),
])
def test_named_paths_are_replaced(text, expected):
    assert NAMES.text(text) == expected


@pytest.mark.parametrize("text", [
    "/work/src2/py/obj.c",              # not a component of a named directory
    "/work/srcs",
    "/usr/include/stdio.hx",
    "prefix/work/src/py/obj.c",         # the tail of a relative path
    "https://example.org/work/src/x",
    "a / b / c",
    "/rom/lib",                         # data that is not a named path
    "/usr/include",                     # a parent of a named file
])
def test_other_text_is_unchanged(text):
    assert NAMES.text(text) == text


def test_digests_replace_only_whole_strings():
    assert NAMES.text(DIGEST) == "b" * 64
    assert NAMES.text("x" + DIGEST) == "x" + DIGEST


def test_value_rewrites_strings_and_keys_without_touching_the_original():
    original = {"/work/src/a.c": ["/work/src/a.c", ("/work/build dir/o", 3)], "n": 1, "d": DIGEST}
    copy = NAMES.value(original)
    assert copy == {"micropython/a.c": ["micropython/a.c", ["build/o", 3]], "n": 1, "d": "b" * 64}
    assert original["/work/src/a.c"][0] == "/work/src/a.c"


def test_value_keeps_sharing_and_chain_views():
    shared = {"path": "/work/src/x"}
    view = chain_root([], []).extend({"path": "/work/src/y"}).extend({"path": "/work/src/z"})
    copy = NAMES.value({"a": shared, "b": shared, "view": view})
    assert copy["a"] is copy["b"] and copy["a"] == {"path": "micropython/x"}
    assert list(copy["view"]) == [{"path": "micropython/y"}, {"path": "micropython/z"}]
    assert copy["view"].items is not view.items
    assert view.items[-1] == {"path": "/work/src/z"}


def test_value_handles_deep_nesting_without_recursion():
    deep = leaf = {}
    for _ in range(5000):
        leaf["next"] = {}
        leaf = leaf["next"]
    leaf["path"] = "/work/src/x"
    copy = NAMES.value(deep)
    while "next" in copy:
        copy = copy["next"]
    assert copy == {"path": "micropython/x"}


def test_two_keys_published_as_one_name_are_an_error():
    with pytest.raises(ValueError, match="same name"):
        NAMES.value({"/work/src/a": 1, "micropython/a": 2})


def test_rules_must_be_absolute_named_and_consistent():
    for rules in ([("relative", "x")], [("/", "x")], [("/a", "")], [("/a", "x"), ("/a", "y")]):
        with pytest.raises(ValueError):
            PathNames(rules)
    extended = NAMES.extended([("/other", "other")])
    assert extended.text("/other/f") == "other/f" and extended.text("/work/src/f") == "micropython/f"
    with pytest.raises(ValueError):
        NAMES.extended([("/work/src", "other")])


def test_no_rules_leave_everything_unchanged():
    value = {"/work/src/a": "/work/src/b"}
    assert PathNames().value(value) is value


def host_surface(*consumed):
    return {
        "build": {"consumed_inputs": {path: {"path": path, "resolved": path, "sha256": "0"} for path in consumed},
                  "source_inputs": {"/usr/include/b.h": "0"}, "compilers": [["/opt/tc/bin/gcc", "0"]],
                  "link": {"linker": "/opt/tc/bin/ld", "nm": "/opt/tc/bin/nm", "objdump": "/opt/tc/bin/objdump",
                           "link_inputs": {"/b/build/x.o": "0"},
                           "argv": ["gcc", "-L/opt/tc/lib", "-Wl,-Map=/b/build/m.map", "-lc"],
                           "expanded_argv": [], "linker_arguments": []}},
        "coverage": {"unassessed_link_objects": ["/opt/tc/lib/libgcc.a(_x.o)"]}}


def test_system_paths_get_names_and_other_host_paths_are_errors():
    from mpy_api.cli import host_names
    roots = PathNames([("/b/build", "build")])
    names = host_names(host_surface("/opt/tc/include/a.h"), roots, ["gcc", "-I/usr/include", "-o", "/dev/null"])
    assert names.text("/opt/tc/include/a.h") == "system/opt/tc/include/a.h"
    assert names.text("-L/opt/tc/lib -I/usr/include") == "-Lsystem/opt/tc/lib -Isystem/usr/include"
    assert names.text("/opt/tc/lib/libgcc.a(_x.o)") == "system/opt/tc/lib/libgcc.a(_x.o)"
    assert names.text("/dev/null") == "/dev/null" and names.text("/b/build/m.map") == "build/m.map"
    with pytest.raises(ValueError, match="/data/ext/src/x.c.*--source-root"):
        host_names(host_surface("/data/ext/src/x.c"), roots, [])


def test_unnamed_finds_uncovered_host_paths_only():
    names = PathNames([("/work/src", "micropython")])
    text = "-I/work/src/py -I/data/inc /opt/tc/x /dev/null /DISCARD/ build/relative/path /rom/lib"
    assert names.unnamed(text) == ["/data/inc", "/DISCARD", "/rom/lib"]
    assert names.unnamed(text, existing=True) == []


def test_named_text_digests_are_published_under_named_keys(tmp_path):
    from mpy_api.cli import named_identities, require_covered
    names = PathNames([(str(tmp_path), "t")])
    record = {"compdb": {"path": str(tmp_path / "c.json")}}
    result = named_identities([("compdb", f"{tmp_path}/a.c")], record, names)
    assert result == {"compdb": {"path": str(tmp_path / "c.json"), "named_bytes": 5,
                                 "named_sha256": hashlib.sha256(b"t/a.c").hexdigest()}}
    with pytest.raises(ValueError, match="compdb .*/root"):
        require_covered([("compdb", "/root")], record, names)


def test_argument_paths_are_operands_and_path_options_not_macro_values():
    from mpy_api.cli import argument_paths
    arguments = ["gcc", "-I/a", "-DX=/b", "-c", "/c.c", "-o", "/d.o", "-isystem/e", "--sysroot=/f", "-Wl,-Map=/g", "-O2"]
    assert list(argument_paths(arguments)) == ["/a", "/c.c", "/d.o", "/e", "/f"]


def test_a_relative_path_joined_to_an_option_is_not_absolute():
    names = PathNames([("/work/src", "micropython")])
    assert names.unnamed("-I../.. -I. -I/data/inc") == ["/data/inc"]
    assert names.text("-I../../work/src") == "-I../../work/src"
