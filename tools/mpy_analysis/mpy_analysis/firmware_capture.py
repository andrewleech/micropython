"""Capture successful MicroPython build actions through tool wrappers."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Sequence

from .firmware_capture_receipt import receipt_from_journal

_PROBES = {"-dumpmachine", "-print-file-name=libgcc.a", "-print-prog-name=ld"}
_OUTPUT_OPTIONS = {"-o", "-MF", "-MT", "-MQ", "-Map"}
_PATH_OPTIONS = {"-include", "-imacros", "-T", "-L", "-I", "-isystem", "-iquote"}
_DEPFILE_CONTINUATION = re.compile(r"\\\r?\n")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _file_record(path: Path, role: str) -> dict[str, str] | None:
    try:
        resolved = path.resolve(strict=True)
        if not resolved.is_file():
            return None
        return {"path": str(resolved), "sha256": _sha256(resolved), "role": role}
    except OSError:
        return None


def _resolve_arg(value: str, cwd: Path) -> Path | None:
    if not value or value.startswith("-") or value.startswith("="):
        return None
    path = Path(value)
    candidate = path if path.is_absolute() else cwd / path
    return candidate if candidate.is_file() else None


def _depfile_inputs(path: Path, cwd: Path) -> list[Path]:
    try:
        text = path.read_text(encoding="utf-8", errors="strict")
    except (OSError, UnicodeError):
        return []
    text = _DEPFILE_CONTINUATION.sub(" ", text)
    colon = text.find(":")
    if colon < 0:
        return []
    try:
        words = shlex.split(text[colon + 1 :], posix=True)
    except ValueError:
        return []
    return [candidate for word in words if (candidate := _resolve_arg(word, cwd)) is not None]

def _depfile_evidence(argv: list[str], cwd: Path) -> list[dict]:
    evidence = []
    for index, arg in enumerate(argv[:-1]):
        if arg != "-MF":
            continue
        depfile = _resolve_arg(argv[index + 1], cwd)
        if depfile is None:
            continue
        record = _file_record(depfile, "dependency-file")
        if record is None:
            continue
        dependencies = []
        for path in _depfile_inputs(depfile, cwd):
            dependency = _file_record(path, "compiler-dependency")
            if dependency is not None:
                dependencies.append({"path": dependency["path"], "sha256": dependency["sha256"]})
        evidence.append(
            {
                "path": record["path"],
                "sha256": record["sha256"],
                "captured_ns": time.time_ns(),
                "dependencies": sorted(dependencies, key=lambda item: item["path"]),
            }
        )
    return evidence



def _kind(role: str, argv: list[str]) -> str | None:
    if any(arg in _PROBES or arg.startswith("-print-file-name=") for arg in argv):
        return None
    if role in ("cc", "cxx"):
        if "-E" in argv:
            return "generate"
        return "compile" if "-c" in argv else "link"
    if role == "as":
        return "assemble"
    if role == "ld":
        return "link"
    if role == "ar":
        return "archive"
    if role in ("objcopy", "strip"):
        return "transform"
    if role == "python":
        return "generate"
    return None


def _action_inputs(role: str, kind: str, argv: list[str], cwd: Path) -> list[dict[str, str]]:
    candidates: dict[str, tuple[Path, str]] = {}
    output_values: set[str] = set()
    option = None
    for arg in argv:
        if option in _OUTPUT_OPTIONS:
            output_values.add(arg)
            option = None
            continue
        if option in _PATH_OPTIONS:
            candidate = _resolve_arg(arg, cwd)
            if candidate is not None:
                candidates[str(candidate.resolve())] = (candidate, "link-operand" if option == "-T" else "tool-input")
            option = None
            continue
        if arg in _OUTPUT_OPTIONS:
            option = arg
            continue
        if arg in _PATH_OPTIONS:
            option = arg
            continue
        if arg.startswith("-Map="):
            output_values.add(arg.split("=", 1)[1])
            continue
        if arg.startswith("-Wl,"):
            for field in arg[4:].split(","):
                if field.startswith("-T") and len(field) > 2:
                    candidate = _resolve_arg(field[2:], cwd)
                    if candidate is not None:
                        candidates[str(candidate.resolve())] = (candidate, "link-operand")
                elif field.startswith("-Map="):
                    output_values.add(field.split("=", 1)[1])
            continue
        if arg.startswith("@"):
            candidate = _resolve_arg(arg[1:], cwd)
            if candidate is not None:
                candidates[str(candidate.resolve())] = (candidate, "response-file")
            continue
        candidate = _resolve_arg(arg, cwd)
        if candidate is not None:
            suffix = candidate.suffix.lower()
            if kind in ("compile", "assemble") and suffix in (".c", ".cc", ".cpp", ".cxx", ".s", ".sx"):
                input_role = "source"
            elif kind == "link" and suffix in (".o", ".obj", ".a", ".lib", ".so", ".dylib", ".elf"):
                input_role = "link-operand"
            elif kind == "archive" and suffix in (".o", ".obj"):
                input_role = "archive-member"
            else:
                input_role = "tool-input"
            candidates[str(candidate.resolve())] = (candidate, input_role)

    if role in ("cc", "cxx") and kind == "compile":
        for index, arg in enumerate(argv[:-1]):
            if arg == "-MF":
                depfile = _resolve_arg(argv[index + 1], cwd)
                if depfile is not None:
                    for dependency in _depfile_inputs(depfile, cwd):
                        candidates.setdefault(str(dependency.resolve()), (dependency, "compiler-dependency"))

    return [
        record
        for path, (candidate, input_role) in sorted(candidates.items())
        if path not in {str((cwd / item).resolve()) for item in output_values}
        and (record := _file_record(candidate, input_role)) is not None
    ]


def _action_outputs(role: str, kind: str, argv: list[str], cwd: Path) -> list[dict[str, str]]:
    paths: set[Path] = set()
    option = None
    for index, arg in enumerate(argv):
        if option in _OUTPUT_OPTIONS:
            if option in ("-o", "-Map"):
                candidate = Path(arg)
                paths.add(candidate if candidate.is_absolute() else cwd / candidate)
            option = None
            continue
        if arg in _OUTPUT_OPTIONS:
            option = arg
            continue
        if arg.startswith("-Map="):
            candidate = Path(arg.split("=", 1)[1])
            paths.add(candidate if candidate.is_absolute() else cwd / candidate)
            continue
        if arg.startswith("-Wl,"):
            for field in arg[4:].split(","):
                if field.startswith("-Map="):
                    candidate = Path(field.split("=", 1)[1])
                    paths.add(candidate if candidate.is_absolute() else cwd / candidate)
                elif field == "-Map" and index + 1 < len(argv):
                    candidate = Path(argv[index + 1])
                    paths.add(candidate if candidate.is_absolute() else cwd / candidate)
            continue
    if role == "ar":
        for arg in argv:
            candidate = _resolve_arg(arg, cwd)
            if candidate is not None and candidate.suffix.lower() in (".a", ".lib"):
                paths.add(candidate)
                break
    if role in ("objcopy", "strip"):
        candidates = [_resolve_arg(arg, cwd) for arg in argv]
        files = [path for path in candidates if path is not None]
        if files:
            paths.add(files[-1])
    if role == "python":
        for index, arg in enumerate(argv[:-1]):
            if arg.startswith("--output-"):
                candidate = Path(argv[index + 1])
                paths.add(candidate if candidate.is_absolute() else cwd / candidate)
        generated = [
            path
            for arg in argv
            if (path := _resolve_arg(arg, cwd)) is not None
            and path.suffix.lower() in (".dfu", ".pack")
        ]
        if generated:
            paths.add(generated[-1])
    return [record for path in sorted(paths) if (record := _file_record(path, "output")) is not None]


def _append_event(path: Path, event: dict) -> None:
    payload = (json.dumps(event, sort_keys=True, separators=(",", ":")) + "\n").encode()
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    try:
        os.write(descriptor, payload)
    finally:
        os.close(descriptor)


def run_tool(role: str, executable: Path, argv: list[str], *, journal: Path, image_id: str, build_id: str) -> int:
    cwd = Path.cwd().resolve()
    kind = _kind(role, argv)
    if kind is None:
        return subprocess.run([str(executable), *argv]).returncode
    executable_record = _file_record(executable, "tool")
    if executable_record is None:
        raise RuntimeError(f"capture tool executable is not a readable file: {executable}")
    inputs = _action_inputs(role, kind, argv, cwd)
    before = {item["path"]: item["sha256"] for item in inputs}
    started = time.time_ns()
    result = subprocess.run([str(executable), *argv])
    after_inputs = _action_inputs(role, kind, argv, cwd)
    after = {item["path"]: item["sha256"] for item in after_inputs}
    outputs = _action_outputs(role, kind, argv, cwd) if result.returncode == 0 else []
    in_place = {item["path"] for item in outputs} if kind == "transform" else set()
    changed_inputs = sorted(
        path for path, digest in before.items() if after.get(path) != digest and path not in in_place
    )
    captured_paths = set(before)
    post_action_dependencies = []
    for item in after_inputs:
        if item["path"] not in captured_paths:
            inputs.append(item)
            post_action_dependencies.append(item["path"])
            captured_paths.add(item["path"])
    dependency_evidence = _depfile_evidence(argv, cwd) if result.returncode == 0 else []
    _append_event(
        journal,
        {
            "schema": "mpy-firmware-action-event/v1",
            "build_id": build_id,
            "image_id": image_id,
            "kind": kind,
            "role": role,
            "argv": [str(executable), *argv],
            "cwd": str(cwd),
            "started_ns": started,
            "finished_ns": time.time_ns(),
            "exit_code": result.returncode,
            "executable": executable_record,
            "inputs": inputs,
            "outputs": outputs,
            "changed_inputs_during_action": changed_inputs,
            "dependencies_discovered_after_action": post_action_dependencies,
            "dependency_evidence": dependency_evidence,
        },
    )
    return result.returncode


def _make_tool_wrapper(path: Path, role: str, executable: Path, journal: Path, image_id: str, build_id: str) -> None:
    command = [
        sys.executable,
        "-m",
        "mpy_analysis.firmware_capture",
        "_tool",
        "--role",
        role,
        "--executable",
        str(executable),
        "--journal",
        str(journal),
        "--image-id",
        image_id,
        "--build-id",
        build_id,
        "--",
        '"$@"',
    ]
    path.write_text("#!/bin/sh\nexec " + shlex.join(command[:-1]) + " \"$@\"\n", encoding="utf-8")
    path.chmod(0o755)


def capture_make(
    *,
    micropython_root: Path,
    make_dir: Path,
    build_dir: Path,
    journal: Path,
    receipt_path: Path,
    elf: Path,
    map_file: Path,
    image_id: str,
    image_role: str,
    build_id: str,
    port: str,
    target: str,
    variant: str,
    source_roots: dict[str, Path],
    cross_compile: str,
    variables: list[str],
    targets: list[str],
) -> int:
    root = micropython_root.resolve(strict=True)
    make_directory = (root / make_dir).resolve(strict=True) if not make_dir.is_absolute() else make_dir.resolve(strict=True)
    build = build_dir.resolve()
    journal = journal.resolve()
    receipt_path = receipt_path.resolve()
    elf = elf.resolve()
    map_file = map_file.resolve()
    if len({receipt_path, journal, elf, map_file}) != 4:
        raise ValueError("receipt, journal, ELF and map outputs must be distinct paths")
    if journal.exists():
        raise ValueError(f"action journal already exists: {journal}")
    if receipt_path.exists():
        raise ValueError(f"receipt output already exists: {receipt_path}")
    if build.exists() and any(build.iterdir()):
        raise ValueError(f"capture requires an empty build directory: {build}")
    build.mkdir(parents=True, exist_ok=True)
    cross = cross_compile
    tools = {
        "CC": ("cc", shutil.which(cross + "gcc")),
        "CXX": ("cxx", shutil.which(cross + "g++")),
        "AS": ("as", shutil.which(cross + "as")),
        "LD": ("ld", shutil.which(cross + "ld")),
        "AR": ("ar", shutil.which(cross + "ar")),
        "OBJCOPY": ("objcopy", shutil.which(cross + "objcopy")),
        "STRIP": ("strip", shutil.which(cross + "strip")),
        "PYTHON": ("python", shutil.which("python3")),
    }
    missing = [name for name, (_, executable) in tools.items() if not executable]
    if missing:
        raise ValueError(f"missing toolchain executables for make variables: {', '.join(missing)}")

    with tempfile.TemporaryDirectory(prefix="mpy-firmware-capture-") as wrapper_dir:
        wrappers = Path(wrapper_dir)
        make_vars = [f"BUILD={build}"]
        for name, (role, executable) in tools.items():
            wrapper = wrappers / name.lower()
            _make_tool_wrapper(wrapper, role, Path(executable), journal, image_id, build_id)
            make_vars.append(f"{name}={wrapper}")
        environment = os.environ.copy()
        package_root = str(Path(__file__).resolve().parents[1])
        environment["PYTHONPATH"] = package_root + os.pathsep + environment.get("PYTHONPATH", "")
        result = subprocess.run(
            ["make", "-j1", "-C", str(make_directory), *make_vars, *variables, *(targets or ["all"])],
            env=environment,
        )
    if result.returncode:
        return result.returncode
    if not journal.is_file():
        raise ValueError("build succeeded without any captured tool actions")
    build_variables = {name: value for name, value in (item.split("=", 1) for item in variables)}
    build_variables["BUILD"] = str(build)
    if cross_compile:
        build_variables["CROSS_COMPILE"] = cross_compile
    receipt = receipt_from_journal(
        journal,
        build_id=build_id,
        image_id=image_id,
        image_role=image_role,
        port=port,
        target=target,
        variant=variant,
        variables=build_variables,
        source_roots=source_roots,
        build_dir=build,
        elf=elf,
        map_file=map_file,
    )
    receipt_path.parent.mkdir(parents=True, exist_ok=True)
    receipt_path.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"Captured firmware receipt: {receipt_path} ({receipt['status']})")
    return 0


def _tool_main(argv: Sequence[str]) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--role", required=True)
    parser.add_argument("--executable", type=Path, required=True)
    parser.add_argument("--journal", type=Path, required=True)
    parser.add_argument("--image-id", required=True)
    parser.add_argument("--build-id", required=True)
    parser.add_argument("args", nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    arguments = args.args[1:] if args.args[:1] == ["--"] else args.args
    return run_tool(
        args.role,
        args.executable,
        arguments,
        journal=args.journal,
        image_id=args.image_id,
        build_id=args.build_id,
    )


def main(argv: Sequence[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if arguments and arguments[0] == "_tool":
        return _tool_main(arguments[1:])
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--micropython-root", type=Path, required=True)
    parser.add_argument("--make-dir", type=Path, required=True)
    parser.add_argument("--build-dir", type=Path, required=True)
    parser.add_argument("--journal", type=Path, required=True)
    parser.add_argument("--receipt", type=Path, required=True)
    parser.add_argument("--elf", type=Path, required=True)
    parser.add_argument("--map", dest="map_file", type=Path, required=True)
    parser.add_argument("--image-id", required=True)
    parser.add_argument("--image-role", default="application")
    parser.add_argument("--build-id", required=True)
    parser.add_argument("--port", required=True)
    parser.add_argument("--target", required=True)
    parser.add_argument("--variant", required=True)
    parser.add_argument("--source-root", action="append", default=[], metavar="NAME=PATH")
    parser.add_argument("--cross-compile", default="")
    parser.add_argument("--variable", action="append", default=[], metavar="NAME=VALUE")
    parser.add_argument("--make-target", action="append", default=[])
    args = parser.parse_args(arguments)
    try:
        root = args.micropython_root.resolve(strict=True)
        source_roots = {"micropython": root}
        root_paths = {root}
        for item in args.source_root:
            name, separator, path = item.partition("=")
            if not separator or not name or not path or name in source_roots:
                raise ValueError(f"invalid or duplicate source root: {item!r}")
            source_path = Path(path).resolve(strict=True)
            if source_path in root_paths:
                raise ValueError(f"source root {item!r} duplicates an existing physical root")
            source_roots[name] = source_path
            root_paths.add(source_path)
        variable_names = set()
        for item in args.variable:
            name, separator, _ = item.partition("=")
            if not separator or not name or name == "BUILD" or name in variable_names:
                raise ValueError(f"invalid, reserved or duplicate make variable: {item!r}")
            variable_names.add(name)
        return capture_make(
            micropython_root=root,
            make_dir=args.make_dir,
            build_dir=args.build_dir,
            journal=args.journal,
            receipt_path=args.receipt,
            elf=args.elf,
            map_file=args.map_file,
            image_id=args.image_id,
            image_role=args.image_role,
            build_id=args.build_id,
            port=args.port,
            target=args.target,
            variant=args.variant,
            source_roots=source_roots,
            cross_compile=args.cross_compile,
            variables=args.variable,
            targets=args.make_target,
        )
    except (OSError, ValueError, RuntimeError) as error:
        print(f"firmware-capture: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
