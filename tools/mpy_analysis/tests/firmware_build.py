"""Real GNU Make builds captured through capture_make, with the SBOM generated from their receipt."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

import pytest
from mpy_analysis import sbom, scope
from mpy_analysis.firmware_capture import capture_make
from mpy_analysis.sbom_spdx import to_spdx

MPY_ROOT = Path(__file__).resolve().parents[3]
TOOLS = ("make", "gcc", "g++", "as", "ld", "ar", "objcopy", "strip", "python3")
requires_gnu_build = pytest.mark.skipif(
    not all(shutil.which(tool) for tool in TOOLS), reason="native GNU build tools are unavailable"
)

PREAMBLE = """ROOT := $(abspath ..)
.PHONY: all
all: $(BUILD)/firmware.elf
$(BUILD):
\tmkdir -p $@
$(BUILD)/%.o: $(ROOT)/src/%.c | $(BUILD)
\t$(CC) $(CFLAGS) -MD -MF $(@:.o=.d) -c $< -o $@
"""
LINK = "$(BUILD)/firmware.elf: {objects}\n\t$(CC) -nostdlib -Wl,-e,main -Wl,-Map=$(BUILD)/firmware.map -o $@ $^\n"


def run_git(root: Path, *arguments: str) -> None:
    subprocess.run(["git", "-C", str(root), *arguments], check=True, capture_output=True)


@dataclass
class Build:
    root: Path
    build: Path
    receipt_path: Path
    journal: Path
    roots: dict[str, Path]
    exit_code: int
    name: str
    variant: str = "controls"
    extra: dict = field(default_factory=dict)

    @property
    def receipt(self) -> dict:
        return json.loads(self.receipt_path.read_text())

    @property
    def image(self) -> dict:
        return self.receipt["images"][0]

    def headers(self) -> dict[str, dict]:
        return {Path(item["path"]).name: item for item in self.image["header_accounting"]}


def capture(
    tmp_path: Path,
    *,
    files: dict[str, str | bytes],
    makefile: str,
    roots: dict[str, str] | None = None,
    name: str = "fixture",
    cross_compile: str = "",
    variables: tuple[str, ...] = (),
    build_id: str = "a" * 64,
    symlinks: dict[str, str] | None = None,
) -> Build:
    """Commit the files (relative to the repository root) and capture the Makefile's build.

    ``roots`` maps a source root name to its directory relative to the repository root; the
    default is the single root ``fixture`` at ``src``. Files named ``src/manifest.py`` and
    ``src/main.py`` are added unless given, so a selection can always be made.
    """
    root = tmp_path / name
    (root / "make").mkdir(parents=True)
    (root / "make" / "Makefile").write_text(makefile)
    contents = {"src/manifest.py": 'module("main.py")\n', "src/main.py": "main = True\n", **files}
    for relative, content in contents.items():
        (root / relative).parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, bytes):
            (root / relative).write_bytes(content)
        else:
            (root / relative).write_text(content)
    for relative, target in (symlinks or {}).items():
        (root / relative).parent.mkdir(parents=True, exist_ok=True)
        (root / relative).symlink_to(target)
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    run_git(root, "config", "user.email", "sbom-test@example.invalid")
    run_git(root, "config", "user.name", "SBOM Test")
    run_git(root, "add", "-A")
    run_git(root, "commit", "-qm", "fixture")
    build = tmp_path / f"{name}-build"
    receipt_path = tmp_path / f"{name}-receipt.json"
    journal = tmp_path / f"{name}-actions.jsonl"
    absolute = {key: root / relative for key, relative in (roots or {"fixture": "src"}).items()}
    code = capture_make(
        micropython_root=root,
        make_dir=root / "make",
        build_dir=build,
        journal=journal,
        receipt_path=receipt_path,
        elf=build / "firmware.elf",
        map_file=build / "firmware.map",
        image_id=name,
        image_role="application",
        build_id=build_id,
        port="native",
        target="native",
        variant="controls",
        source_roots=absolute,
        cross_compile=cross_compile,
        variables=list(variables),
        targets=["all"],
    )
    return Build(root, build, receipt_path, journal, absolute, code, name)


def scope_of(built: Build) -> dict:
    """The scope inventory of a captured build; the selection is its compiled sources."""
    events = [json.loads(line) for line in built.journal.read_text().splitlines()]
    compdb = built.root.parent / f"{built.name}-compile_commands.json"
    compdb.write_text(
        json.dumps(
            [
                {
                    "directory": event["cwd"],
                    "file": next(item["path"] for item in event["inputs"] if item["role"] == "source"),
                    "arguments": event["argv"],
                    "output": next(item["path"] for item in event["outputs"] if item["path"].endswith(".o")),
                }
                for event in events
                if event["kind"] == "compile"
                and any(
                    Path(item["path"]).is_relative_to(root)
                    for item in event["inputs"]
                    if item["role"] == "source"
                    for root in built.roots.values()
                )
            ]
        )
    )
    inventory = scope.collect_manifest_scope(
        MPY_ROOT,
        [built.roots["fixture"] / "manifest.py"],
        roots={key: str(path) for key, path in built.roots.items()},
        path_vars={},
        owners={key: "application" for key in built.roots},
        target="native",
        variant=built.variant,
        port="native",
    )
    inventory["groups"]["native"] = scope.collect_compdb_sources(
        compdb, roots=inventory["roots"], owners={key: "application" for key in built.roots}
    )
    return inventory


def generate(built: Build, *, receipt=None, **options):
    """The SBOM model and summary for a captured build; `options` go to generate_sbom."""
    return sbom.generate_sbom(
        scope_of(built),
        micropython_root=MPY_ROOT,
        build_receipt=receipt if receipt is not None else built.receipt_path,
        **options,
    )


def components(model, **properties):
    """Model components whose properties include all of the given name/value pairs."""
    return [
        item
        for item in model.components.values()
        if all(item.properties.get(key) == value for key, value in properties.items())
    ]


def fake_toolchain(tmp_path, monkeypatch, gcc_tail="", gcc_head=""):
    """A toolchain prefix `fake-` whose gcc runs the head lines, the real gcc, then the tail lines."""
    directory = tmp_path / "fake-bin"
    directory.mkdir()
    gcc = directory / "fake-gcc"
    gcc.write_text(f'#!/bin/sh\n{gcc_head}\n{shutil.which("gcc")} "$@" || exit $?\n{gcc_tail}\n')
    gcc.chmod(0o755)
    for tool in ("g++", "as", "ld", "ar", "objcopy", "strip"):
        (directory / f"fake-{tool}").symlink_to(shutil.which(tool))
    monkeypatch.setenv("PATH", f"{directory}:{os.environ['PATH']}")
    return "fake-"


def spdx(model):
    return to_spdx(model)


def gap_kinds(summary) -> set[str]:
    return {gap["kind"] for gap in summary["gaps"]}


__all__ = [
    "LINK",
    "PREAMBLE",
    "Build",
    "capture",
    "components",
    "gap_kinds",
    "generate",
    "run_git",
    "scope_of",
    "requires_gnu_build",
    "spdx",
]
