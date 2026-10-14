import re
import subprocess
from pathlib import Path

import pytest
from mpy_analysis import mpy_tree

REPO = Path(__file__).resolve().parents[3]

# Libraries carried in-tree that declare no usable single version; each needs a
# reason, and a new library must either get a VERSION_SOURCES entry or be added here.
NO_SINGLE_VERSION = {
    "lib/CMSIS_5": "versioned by git tag only",
    "lib/CMSIS_6": "versioned by git tag only",
    "lib/alif-security-toolkit": "releases are tagged only on a separate branch",
    "lib/arduino-lib": "no version file",
    "lib/axtls": "no axTLS version declared",
    "lib/btstack": "no version marker",
    "lib/crypto-algorithms": "no version marker",
    "lib/libm": "no version marker",
    "lib/micropython-lib": "package repository, versions are per package",
    "lib/mbedtls_errors": "MicroPython code generation tooling",
    "lib/nrfx": "no version marker",
    "lib/psoc-edge": "collection of independently versioned trees",
    "lib/re1.5": "no version marker",
    "lib/uzlib": "no version marker",
    "lib/wiznet5k": "no version marker",
}


SOURCES = {*mpy_tree.VERSION_SOURCES, *mpy_tree.FAMILY_SOURCES}


def populated(relative):
    path = REPO / relative
    return path.is_dir() and any(path.iterdir())


def submodule_paths():
    out = subprocess.run(
        ["git", "config", "--file", ".gitmodules", "--get-regexp", r"submodule\..*\.path"],
        cwd=REPO,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return [line.split()[1] for line in out.splitlines()]


def test_license_tree_nests_by_indent_and_ignores_prose():
    text = (
        "intro\n\n/ (MIT)\n    /lib\n        /lwip (BSD-3-clause)\n"
        "        /deep (X)\n            /inner (Y)\n    /ports\n        /stm32\n"
        "            /usbd*.c (Z)\n"
    )
    assert mpy_tree.license_tree(text) == [
        ((), "MIT"),
        (("lib", "lwip"), "BSD-3-clause"),
        (("lib", "deep"), "X"),
        (("lib", "deep", "inner"), "Y"),
        (("ports", "stm32", "usbd*.c"), "Z"),
    ]


def test_release_matches_the_header_and_marks_prereleases():
    major, minor, micro, pre = (
        re.search(
            rf"^#define MICROPY_VERSION_{n} (\d+)", (REPO / "py/mpconfig.h").read_text(), re.M
        ).group(1)
        for n in ("MAJOR", "MINOR", "MICRO", "PRERELEASE")
    )
    expected = f"{major}.{minor}.{micro}" + ("-preview" if int(pre) else "")
    assert mpy_tree.release(REPO)[0] == expected


def test_root_identity_is_micropython_with_its_declared_licence():
    found = mpy_tree.identity(REPO)
    assert found["name"][0] == "micropython"
    assert found["license"][0] == "MIT"
    assert found["version"][0] == mpy_tree.release(REPO)[0]


def test_listed_library_names_tolerate_punctuation_differences():
    # LICENSE lists "berkeley-db-1xx" for the directory lib/berkeley-db-1.xx.
    assert mpy_tree.listed_licence(REPO, ("lib", "berkeley-db-1.xx"))[0] == "BSD-4-clause"
    assert mpy_tree.listed_licence(REPO, ("lib", "re1.5"))[0] == "BSD-3-clause"


def test_unlisted_directories_have_no_declared_identity():
    assert mpy_tree.identity(REPO / "extmod") is None


def test_every_carried_library_has_a_version_source_or_a_documented_reason():
    carried = {
        f"lib/{p.name}" for p in (REPO / "lib").iterdir() if p.is_dir() and p.name != "__pycache__"
    }
    carried |= {
        path for path in submodule_paths() if path.count("/") == 1 and path.startswith("lib/")
    }
    undecided = sorted(
        library
        for library in carried
        if library not in SOURCES and library not in NO_SINGLE_VERSION
    )
    assert undecided == [], "add a VERSION_SOURCES entry or a documented NO_SINGLE_VERSION reason"
    stale = sorted(set(SOURCES) - carried)
    assert stale == []


@pytest.mark.parametrize("library", sorted(mpy_tree.VERSION_SOURCES))
def test_checked_out_library_version_parses(library):
    if not populated(library):
        pytest.skip(f"{library} is not checked out")
    version, path = mpy_tree.library_version(REPO, library)
    assert re.search(r"\d", version) and path.is_file()


def test_library_whose_marker_moved_is_an_error(tmp_path):
    (tmp_path / "lib/lwip/src/include/lwip").mkdir(parents=True)
    (tmp_path / "lib/lwip/src/include/lwip/init.h").write_text("/* no version here */\n")
    with pytest.raises(ValueError, match="LWIP_VERSION_MAJOR"):
        mpy_tree.library_version(tmp_path, "lib/lwip")


def test_family_collection_version_comes_from_the_one_selected_family():
    if not populated("lib/stm32lib"):
        pytest.skip("lib/stm32lib is not checked out")
    one = tuple((REPO / "lib/stm32lib").glob("CMSIS/STM32H5xx/Include/*.h"))[:1]
    version, path = mpy_tree.library_version(REPO, "lib/stm32lib", one)
    assert re.fullmatch(r"\d+\.\d+\.\d+", version) and path.name == "stm32h5xx.h"
    both = one + tuple((REPO / "lib/stm32lib").glob("CMSIS/STM32F4xx/Include/*.h"))[:1]
    with pytest.raises(ValueError, match="one family"):
        mpy_tree.library_version(REPO, "lib/stm32lib", both)


def test_littlefs_generations_are_versioned_and_split_into_boundaries(tmp_path):
    from mpy_analysis.boundaries import _split_generations

    lfs = REPO / "lib/littlefs"
    if not populated("lib/littlefs"):
        pytest.skip("lib/littlefs is not carried")
    assert mpy_tree.library_version(REPO, "lib/littlefs", (lfs / "lfs1.c",))[0] == "1.7"
    assert mpy_tree.library_version(REPO, "lib/littlefs", (lfs / "lfs2_util.c",))[0] == "2.11"
    assert mpy_tree.identity(lfs, (lfs / "lfs2.c",))["name"][0] == "littlefs-lfs2"
    with pytest.raises(ValueError, match="one family"):
        mpy_tree.library_version(REPO, "lib/littlefs", (lfs / "lfs1.c", lfs / "lfs2.c"))
    sources = [("native", {"path": f"lib/littlefs/{n}"}) for n in ("lfs1.c", "lfs2.c", "other.c")]
    boundary = {
        "id": "micropython/lib/littlefs",
        "directory": str(lfs),
        "sources": sources,
        "issues": [],
        "candidates": [],
        "fetch_provenance": [],
    }
    split = {b["id"]: [s[1]["path"] for s in b["sources"]] for b in _split_generations([boundary])}
    assert split == {
        "micropython/lib/littlefs": ["lib/littlefs/other.c"],
        "micropython/lib/littlefs#lfs1": ["lib/littlefs/lfs1.c"],
        "micropython/lib/littlefs#lfs2": ["lib/littlefs/lfs2.c"],
    }
