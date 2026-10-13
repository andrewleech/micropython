"""Capture successful MicroPython build actions through tool wrappers."""

from __future__ import annotations

import argparse
import functools
import hashlib
import json
import os
import re
import shlex
import shutil
import stat
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Sequence

from .argv_tokens import flatten as _flatten
from .argv_tokens import resolve_arg as _resolve_arg
from .compdb import DepfileError, depfile_prerequisites
from .firmware_capture_receipt import receipt_from_journal
from .firmware_frozen import find_tagged_source
from .firmware_observe import LOG_VARIABLE, WRAPPER_DIR_VARIABLE, is_generator

_PROBES = {"-dumpmachine", "-print-file-name=libgcc.a", "-print-prog-name=ld"}
_OUTPUT_OPTIONS = {"-o", "-MF", "-MT", "-MQ", "-Map"}
_PATH_OPTIONS = {"-include", "-imacros", "-T", "-L", "-I", "-isystem", "-iquote"}
_SOURCE_SUFFIXES = (".c", ".cc", ".cpp", ".cxx", ".s", ".sx")
_WRAPPER_DIR = WRAPPER_DIR_VARIABLE


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


class _DepfileFailure(Exception):
    """The compiler's dependency output could not be read as complete evidence."""


def _depfile_request(argv: list[str], cwd: Path) -> dict | None:
    """Where a GCC-style command writes its dependency file, or None when the command names none.

    ``-MD`` and ``-MMD`` ask for a file: ``-MF`` names it, otherwise it is the output name with a
    ``.d`` suffix (or the input stem when there is no ``-o``). ``-MF`` alone does not make the
    compiler write anything, so it is a request only when the file exists afterwards.
    """
    tokens = _flatten(argv, cwd)
    explicit = output = system_headers = None
    for index, token in enumerate(tokens):
        following = tokens[index + 1] if index + 1 < len(tokens) else None
        if token in ("-MD", "-MMD"):
            system_headers = "omitted" if token == "-MMD" else "included"
        elif token == "-MF" and following is not None:
            explicit = following
        elif token.startswith("-MF") and len(token) > 3:
            explicit = token[3:]
        elif token == "-o" and following is not None:
            output = following
        elif token.startswith("-o") and len(token) > 2:
            output = token[2:]
    if explicit is None and system_headers is None:
        return None
    if explicit is not None:
        path = Path(explicit)
    elif output is not None:
        path = Path(output).with_suffix(".d")
    else:
        sources = [
            Path(token)
            for token in tokens
            if Path(token).suffix.lower() in _SOURCE_SUFFIXES and _resolve_arg(token, cwd) is not None
        ]
        if len(sources) != 1:
            return {"requested": True, "path": None, "system_headers": system_headers}
        path = Path(sources[0].stem + ".d")
    return {
        "requested": system_headers is not None,
        "path": path if path.is_absolute() else cwd / path,
        "system_headers": system_headers or "included",
    }


def _dependency_record(word: str, cwd: Path) -> dict[str, str]:
    """The canonical path and hash of one depfile prerequisite; the path as written when it differs."""
    written = Path(os.path.normpath(word if os.path.isabs(word) else cwd / word))
    record = _file_record(written, "compiler-dependency")
    if record is None:
        raise _DepfileFailure(f"dependency is not a readable file: {word}")
    entry = {"path": record["path"], "sha256": record["sha256"]}
    if str(written) != record["path"]:
        entry["written_path"] = str(written)
    return entry


def _depfile_evidence(role: str, kind: str, argv: list[str], cwd: Path) -> tuple[list[dict], dict | None]:
    """Dependency evidence of a finished action and, for a compile, whether it is complete.

    A compile that asked for no dependency file, or whose file is missing, unreadable or does not
    parse as one make rule, is reported with the reason; it never yields partial evidence.
    """
    needed = role in ("cc", "cxx") and kind == "compile"
    request = _depfile_request(argv, cwd)
    if request is None:
        reason = "the compile command requests no dependency file (-MD, -MMD)"
        return [], {"status": "not-requested", "reason": reason} if needed else None
    depfile = request["path"]
    try:
        if depfile is None:
            raise _DepfileFailure("the dependency file name cannot be derived from the command")
        try:
            text = depfile.read_text(encoding="utf-8", errors="strict")
        except FileNotFoundError:
            if not request["requested"]:
                return [], {"status": "not-requested", "reason": "-MF names a file but no -MD or -MMD requests one"} if needed else None
            raise _DepfileFailure(f"dependency file was not written: {depfile}") from None
        except (OSError, UnicodeError) as error:
            raise _DepfileFailure(f"dependency file is unreadable: {error}") from error
        try:
            words = depfile_prerequisites(text)
        except DepfileError as error:
            raise _DepfileFailure(f"dependency file is not a make rule: {error}") from error
        dependencies = [_dependency_record(word, cwd) for word in words]
        record = _file_record(depfile, "dependency-file")
        if record is None:
            raise _DepfileFailure(f"dependency file is not a regular file: {depfile}")
    except _DepfileFailure as failure:
        return [], {"status": "failed", "reason": str(failure)} if needed else None
    if request["system_headers"] == "omitted" and "-isystem" in argv:
        capture = {
            "status": "failed",
            "reason": "-MMD omits headers from system directories while -isystem directories are in use",
        }
    else:
        capture = {"status": "collected", "system_headers": request["system_headers"]}
    evidence = [
        {
            "path": record["path"],
            "sha256": record["sha256"],
            "captured_ns": time.time_ns(),
            "dependencies": sorted(
                {item["path"]: item for item in dependencies}.values(), key=lambda item: item["path"]
            ),
        }
    ]
    return evidence, capture if needed else None


def _objcopy_reads_binary(argv: list[str]) -> bool:
    """Whether objcopy is told to read its input as a raw binary (``-I binary``)."""
    for index, arg in enumerate(argv):
        following = argv[index + 1] if index + 1 < len(argv) else None
        if arg in ("-I", "--input-target") and following == "binary":
            return True
        if arg in ("-Ibinary", "--input-target=binary"):
            return True
    return False

def _shell_command(argv: list[str]) -> str | None:
    """The recipe text of a ``sh -c`` invocation, or None for another shell command line."""
    if len(argv) >= 2 and re.fullmatch(r"-[a-z]*c", argv[-2]):
        return argv[-1]
    return None


def _shell_paths(argv: list[str], cwd: Path) -> list[Path]:
    """The words of a recipe line, resolved against the working directory.

    The line is split with POSIX shell quoting and redirection operators are dropped; a word is
    kept as a path candidate whatever it names, since the caller tests it against the file system.
    """
    command = _shell_command(argv)
    if command is None:
        return []
    lexer = shlex.shlex(command, posix=True, punctuation_chars=True)
    lexer.whitespace_split = True
    lexer.commenters = ""
    try:
        words = [word for word in lexer if word and not set(word) <= set("();<>|&")]
    except ValueError:
        return []
    paths: list[Path] = []
    for word in words:
        if word.startswith("-") or "\0" in word:
            continue
        path = Path(os.path.normpath(word if os.path.isabs(word) else cwd / word))
        if path not in paths:
            paths.append(path)
    return paths


def _file_state(path: Path) -> tuple[int, int, int] | None:
    try:
        info = path.lstat()
    except OSError:
        return None
    return (info.st_size, info.st_mtime_ns, info.st_ino) if stat.S_ISREG(info.st_mode) else None


def _kind(role: str, argv: list[str]) -> str | None:
    if any(arg in _PROBES or arg.startswith("-print-file-name=") for arg in argv):
        return None
    if role == "shell":
        return "generate" if _shell_command(argv) is not None else None
    if role == "mpy-cross":
        return None if "--version" in argv else "generate"
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


def _observed_reads() -> list[dict]:
    """The read events the observed frozen-content generator has logged so far, oldest first."""
    log = os.environ.get(LOG_VARIABLE)
    events = []
    try:
        lines = Path(log).read_text(encoding="utf-8").splitlines() if log else []
    except OSError:
        return []
    for line in lines:
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if event.get("event") == "read":
            events.append(event)
    return events


def _is_temporary(path: Path) -> bool:
    return path.resolve().is_relative_to(Path(tempfile.gettempdir()).resolve())


def _mpy_cross_inputs(argv: list[str], cwd: Path) -> list[dict]:
    """The source files of an mpy-cross command line.

    The frozen-content generator compiles a temporary copy of each source. That copy is recorded
    as an ephemeral input, with the hash that ties it to the file the generator read when the
    bytes show the copy was made from it.
    """
    inputs = []
    index = 0
    while index < len(argv):
        arg = argv[index]
        index += 1
        if arg in ("-o", "-s", "-X"):
            index += 1
        elif not arg.startswith("-") and (path := _resolve_arg(arg, cwd)) is not None:
            ephemeral = _is_temporary(path) and LOG_VARIABLE in os.environ
            record = _file_record(path, "ephemeral-input" if ephemeral else "tool-input")
            if record is None:
                continue
            if ephemeral:
                reads = reversed(_observed_reads()[-64:])
                candidates = (
                    (event["path"], event["sha256"], Path(event["path"]).read_bytes())
                    for event in reads
                    if Path(event["path"]).is_file()
                )
                tie = find_tagged_source(path.read_bytes(), candidates)
                if tie is not None:
                    record["derived_from"] = tie
            inputs.append(record)
    return inputs


def _action_inputs(role: str, kind: str, argv: list[str], cwd: Path) -> list[dict[str, str]]:
    if role == "shell":
        return [
            record
            for path in _shell_paths(argv, cwd)
            if path.is_file() and (record := _file_record(path, "tool-input")) is not None
        ]
    if role == "mpy-cross":
        return _mpy_cross_inputs(argv, cwd)
    candidates: dict[str, tuple[Path, str]] = {}
    output_values: set[str] = set()
    option = None
    binary = role == "objcopy" and _objcopy_reads_binary(argv)
    format_option = False
    for arg in _flatten(argv, cwd):
        if format_option:
            binary = arg == "binary"
            format_option = False
            continue
        if kind == "link" and arg in ("-b", "--format", "-format"):
            format_option = True
            continue
        if kind == "link" and arg.startswith(("--format=", "-format=")):
            binary = arg.split("=", 1)[1] == "binary"
            continue
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
        if arg.startswith("-T") and len(arg) > 2:
            candidate = _resolve_arg(arg[2:], cwd)
            if candidate is not None:
                candidates[str(candidate.resolve())] = (candidate, "link-operand")
            continue
        if arg.startswith("@"):
            candidate = _resolve_arg(arg[1:], cwd)
            if candidate is not None:
                candidates[str(candidate.resolve())] = (candidate, "response-file")
            continue
        candidate = _resolve_arg(arg, cwd)
        if candidate is not None:
            suffix = candidate.suffix.lower()
            if binary:
                input_role = "embedded-input"
            elif kind in ("compile", "assemble") and suffix in _SOURCE_SUFFIXES:
                input_role = "source"
            elif kind == "link" and suffix in (".o", ".obj", ".a", ".lib", ".so", ".dylib", ".elf"):
                input_role = "link-operand"
            elif kind == "archive" and suffix in (".o", ".obj"):
                input_role = "archive-member"
            else:
                input_role = "tool-input"
            candidates[str(candidate.resolve())] = (candidate, input_role)

    return [
        record
        for path, (candidate, input_role) in sorted(candidates.items())
        if path not in {str((cwd / item).resolve()) for item in output_values}
        and (record := _file_record(candidate, input_role)) is not None
    ]


def _action_outputs(role: str, kind: str, argv: list[str], cwd: Path) -> list[dict[str, str]]:
    paths: set[Path] = set()
    option = None
    for arg in _flatten(argv, cwd):
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


def _tree_state(root: Path | None) -> dict[str, tuple[int, int, int]]:
    """Size, modification time and inode of every regular file under root."""
    state: dict[str, tuple[int, int, int]] = {}
    if root is None:
        return state
    for directory, _, names in os.walk(root):
        for name in names:
            path = os.path.join(directory, name)
            try:
                info = os.lstat(path)
            except OSError:
                continue
            if stat.S_ISREG(info.st_mode):
                state[path] = (info.st_size, info.st_mtime_ns, info.st_ino)
    return state


def _outside_wrappers(items: list[dict[str, str]]) -> list[dict[str, str]]:
    """Drop the capture's own tool wrappers, which a generator may name on its command line."""
    directory = os.environ.get(_WRAPPER_DIR)
    if not directory:
        return items
    root = Path(directory).resolve()
    return [item for item in items if not Path(item["path"]).is_relative_to(root)]


@functools.lru_cache(maxsize=None)
def _banner(executable: str) -> str:
    """The lower-case first line of the tool's ``--version`` output, empty when it has none."""
    try:
        result = subprocess.run([executable, "--version"], capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return ""
    lines = (result.stdout or result.stderr).splitlines()
    return lines[0].lower() if lines else ""


def _assembler_depfile_option(role: str, argv: list[str]) -> str | None:
    """The file a command already asks GNU as to write its dependencies to (``--MD``), if any."""
    for index, arg in enumerate(argv):
        if role == "as":
            if arg in ("--MD", "-MD") and index + 1 < len(argv):
                return argv[index + 1]
        elif arg.startswith("-Wa,"):
            parts = arg[4:].split(",")
            for position, part in enumerate(parts[:-1]):
                if part in ("--MD", "-MD"):
                    return parts[position + 1]
    return None


def _assembler_plan(role: str, kind: str, executable: Path, argv: list[str]) -> dict | None:
    """How the files GNU as reads are observed for a compile or assemble, None for other actions.

    GNU as writes every file it opened (``.incbin`` data, ``.include`` files) to the file named
    by ``--MD``; gcc forwards it as ``-Wa,--MD,file``. The capture adds that option to the command
    it runs, unless the command already has one, and records the original command. A tool that is
    not GNU cannot report this, which is recorded instead of assumed empty.
    """
    if not ((role in ("cc", "cxx") and kind == "compile") or (role == "as" and kind == "assemble")):
        return None
    banner = _banner(str(executable))
    if role == "as":
        gnu = "gnu assembler" in banner
    else:
        gnu = "clang" not in banner and ("gcc" in banner or "g++" in banner)
    if not gnu:
        return {"unavailable": f"{Path(executable).name} is not a GNU tool and reports no assembler dependencies"}
    return {"requested": _assembler_depfile_option(role, argv)}


def _is_compiler_temporary(path: Path) -> bool:
    """gcc's own assembly temporary, which the assembler lists as its input and gcc then removes."""
    return path.parent == Path(tempfile.gettempdir()).resolve() and re.fullmatch(r"cc\w{6}\.[sS]", path.name) is not None


def _names_known_file(word: str, known: set[str]) -> bool:
    """A relative file name the assembler lists from a ``.file`` directive for a file already recorded."""
    name = os.path.normpath(word)
    return not os.path.isabs(name) and any(path.endswith(os.sep + name) for path in known)


def _assembler_evidence(depfile: Path, known: set[str], cwd: Path) -> dict:
    """Files the assembler read besides the action's sources and compiler dependencies."""
    try:
        text = depfile.read_text(encoding="utf-8", errors="strict")
    except (OSError, UnicodeError) as error:
        return {"status": "failed", "reason": f"assembler dependency file is unreadable: {error}"}
    if not text.strip():
        return {"status": "failed", "reason": "the assembler wrote no dependency output (it did not run)"}
    try:
        words = depfile_prerequisites(text)
    except DepfileError as error:
        return {"status": "failed", "reason": f"assembler dependency file is not a make rule: {error}"}
    dependencies: dict[str, dict[str, str]] = {}
    for word in words:
        written = Path(os.path.normpath(word if os.path.isabs(word) else cwd / word))
        record = _file_record(written, "assembler-dependency")
        if record is None:
            if _is_compiler_temporary(written) or _names_known_file(word, known):
                continue
            return {"status": "failed", "reason": f"assembler dependency is not a readable file: {word}"}
        if record["path"] in known:
            continue
        entry = {"path": record["path"], "sha256": record["sha256"]}
        if str(written) != record["path"]:
            entry["written_path"] = str(written)
        dependencies.setdefault(record["path"], entry)
    return {"status": "collected", "dependencies": [dependencies[path] for path in sorted(dependencies)]}


def _frozen_observation(log: Path) -> dict:
    """What the frozen-content generator logged about itself: its pid, argv, reads and spawns."""
    start = None
    reads: list[dict] = []
    spawns: list[list[str]] = []
    seen: set[tuple[str, str]] = set()
    for line in log.read_text(encoding="utf-8").splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if event.get("event") == "start" and start is None:
            start = event
        elif event.get("event") == "read" and (event["path"], event["sha256"]) not in seen:
            seen.add((event["path"], event["sha256"]))
            reads.append({"path": event["path"], "sha256": event["sha256"]})
        elif event.get("event") == "spawn":
            spawns.append(event["argv"])
    if start is None:
        return {"status": "unavailable", "reason": "the generator process did not start the observer"}
    return {
        "status": "observed",
        "mode": "list-c-modules" if "--list-c-modules" in start["argv"] else "freeze",
        "pid": start["pid"],
        "argv": start["argv"],
        "reads": reads,
        "spawns": spawns,
    }


def run_tool(
    role: str,
    executable: Path,
    argv: list[str],
    *,
    journal: Path,
    image_id: str,
    build_id: str,
    build_dir: Path | None = None,
) -> int:
    cwd = Path.cwd().resolve()
    kind = _kind(role, argv)
    if kind is None:
        return subprocess.run([str(executable), *argv]).returncode
    executable_record = _file_record(executable, "tool")
    if executable_record is None:
        raise RuntimeError(f"capture tool executable is not a readable file: {executable}")
    inputs = _outside_wrappers(_action_inputs(role, kind, argv, cwd))
    before = {item["path"]: item["sha256"] for item in inputs}
    watched = build_dir if role == "python" and kind == "generate" else None
    tree_before = _tree_state(watched)
    build_root = build_dir.resolve() if build_dir is not None else None
    shell_paths = [
        path for path in _shell_paths(argv, cwd) if build_root is not None and path.is_relative_to(build_root)
    ] if role == "shell" else []
    shell_before = {path: _file_state(path) for path in shell_paths}
    plan = _assembler_plan(role, kind, executable, argv)
    run_argv = list(argv)
    assembler_file = None
    if plan is not None and "unavailable" not in plan:
        if plan["requested"] is not None:
            assembler_file = Path(plan["requested"])
            assembler_file = assembler_file if assembler_file.is_absolute() else cwd / assembler_file
        else:
            descriptor, name = tempfile.mkstemp(prefix="mpy-asm-deps-", suffix=".d")
            os.close(descriptor)
            assembler_file = Path(name)
            run_argv += [f"-Wa,--MD,{name}"] if role in ("cc", "cxx") else ["--MD", name]
    wrapper_dir = os.environ.get(_WRAPPER_DIR)
    observing = role == "python" and kind == "generate" and bool(wrapper_dir) and is_generator(argv)
    observed_child = LOG_VARIABLE in os.environ
    teeing = observed_child and role == "python" and kind == "generate"
    observation_log = None
    bytecode_cache = None
    environment = None
    if observing:
        descriptor, name = tempfile.mkstemp(prefix="mpy-frozen-observe-", suffix=".jsonl")
        os.close(descriptor)
        observation_log = Path(name)
        # An empty bytecode cache makes the generator read the source of every module it imports.
        bytecode_cache = tempfile.mkdtemp(prefix="mpy-frozen-pycache-")
        environment = {
            **os.environ,
            LOG_VARIABLE: name,
            "PYTHONPATH": wrapper_dir + os.pathsep + os.environ.get("PYTHONPATH", ""),
            "PYTHONPYCACHEPREFIX": bytecode_cache,
        }
    started = time.time_ns()
    stdout_record = None
    frozen_observation = None
    try:
        if teeing:
            result = subprocess.run([str(executable), *run_argv], stdout=subprocess.PIPE)
            sys.stdout.buffer.write(result.stdout)
            sys.stdout.buffer.flush()
            stdout_record = {"sha256": hashlib.sha256(result.stdout).hexdigest(), "size": len(result.stdout)}
        else:
            result = subprocess.run([str(executable), *run_argv], env=environment)
        if observation_log is not None:
            frozen_observation = _frozen_observation(observation_log)
        after_inputs = _outside_wrappers(_action_inputs(role, kind, argv, cwd))
        after = {item["path"]: item["sha256"] for item in after_inputs}
        outputs = _action_outputs(role, kind, argv, cwd) if result.returncode == 0 else []
        written: set[str] = set()
        if result.returncode == 0 and watched is not None:
            # A generator may write its result to standard output redirected by the shell, so the
            # files it created or rewrote under the build directory are its outputs.
            journal_path = str(Path(journal).resolve())
            known_outputs = {item["path"] for item in outputs}
            for path, state in sorted(_tree_state(watched).items()):
                if tree_before.get(path) != state and path != journal_path:
                    record = _file_record(Path(path), "output")
                    if record is not None and record["path"] not in known_outputs:
                        outputs.append(record)
                        written.add(record["path"])
        if result.returncode == 0 and role == "shell":
            # A recipe line such as ``echo text > file`` runs no wrapped tool: the files it names
            # that were created or rewritten under the build directory are its outputs.
            known_outputs = {item["path"] for item in outputs}
            for path in shell_paths:
                if _file_state(path) != shell_before[path]:
                    record = _file_record(path, "output")
                    if record is not None and record["path"] not in known_outputs:
                        outputs.append(record)
                        written.add(record["path"])
        if role == "shell" and not outputs:
            return result.returncode
        in_place = {item["path"] for item in outputs} if kind == "transform" else set()
        in_place |= written & set(before)
        changed_inputs = sorted(
            path for path, digest in before.items() if after.get(path) != digest and path not in in_place
        )
        created = {item["path"] for item in outputs} - set(before)
        captured_paths = set(before)
        post_action_dependencies = []
        for item in after_inputs:
            if item["path"] not in captured_paths and item["path"] not in created:
                inputs.append(item)
                post_action_dependencies.append(item["path"])
                captured_paths.add(item["path"])
        dependency_evidence, dependency_capture = (
            _depfile_evidence(role, kind, argv, cwd) if result.returncode == 0 else ([], None)
        )
        for evidence in dependency_evidence:
            for dependency in evidence["dependencies"]:
                if dependency["path"] not in captured_paths:
                    inputs.append({**{key: dependency[key] for key in ("path", "sha256")}, "role": "compiler-dependency"})
                    post_action_dependencies.append(dependency["path"])
                    captured_paths.add(dependency["path"])
        assembler_dependencies = None
        if plan is not None and result.returncode == 0:
            assembler_dependencies = (
                {"status": "unavailable", "reason": plan["unavailable"]}
                if "unavailable" in plan
                else _assembler_evidence(assembler_file, captured_paths, cwd)
            )
            for dependency in (assembler_dependencies or {}).get("dependencies", []):
                inputs.append({**{key: dependency[key] for key in ("path", "sha256")}, "role": "assembler-dependency"})
                post_action_dependencies.append(dependency["path"])
                captured_paths.add(dependency["path"])
    finally:
        if assembler_file is not None and plan["requested"] is None:
            assembler_file.unlink(missing_ok=True)
        if observation_log is not None:
            observation_log.unlink(missing_ok=True)
        if bytecode_cache is not None:
            shutil.rmtree(bytecode_cache, ignore_errors=True)
    _append_event(
        journal,
        {
            "schema": "mpy-firmware-action-event/v1",
            "build_id": build_id,
            "image_id": image_id,
            "kind": kind,
            "role": role,
            **({"derivation": "shell-command"} if role == "shell" else {}),
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
            **({"dependency_capture": dependency_capture} if dependency_capture else {}),
            **({"assembler_dependencies": assembler_dependencies} if assembler_dependencies else {}),
            "process": {"pid": os.getpid(), "ppid": os.getppid()},
            **({"in_observed_generator": True} if observed_child else {}),
            **({"frozen_observation": frozen_observation} if frozen_observation is not None else {}),
            **({"stdout": stdout_record} if stdout_record is not None else {}),
        },
    )
    return result.returncode


def _make_tool_wrapper(
    path: Path, role: str, executable: Path, journal: Path, image_id: str, build_id: str, build_dir: Path
) -> None:
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
        "--build-dir",
        str(build_dir),
        "--",
        '"$@"',
    ]
    script = "#!/bin/sh\nexec " + shlex.join(command[:-1]) + " \"$@\"\n"
    if role == "shell":
        # A recipe line that starts one of the capture's tool wrappers is recorded by that
        # wrapper; only the other lines are handed to the recording shell.
        script = (
            f'#!/bin/sh\ncase "$2" in\n{shlex.quote(str(path.parent))}/*) exec {shlex.quote(str(executable))} "$@" ;;\nesac\n'
            + script.split("\n", 1)[1]
        )
    path.write_text(script, encoding="utf-8")
    path.chmod(0o755)


def _mpy_cross_binary(root: Path, variables: list[str]) -> Path | None:
    """The mpy-cross executable the build runs, when it can be located before the build starts.

    The build takes it from ``MICROPY_MPYCROSS`` (a make variable or the environment) and
    otherwise from ``mpy-cross/build/mpy-cross`` in the checkout. A build that has to build
    mpy-cross itself first is not wrapped; its frozen payload then stays unbound.
    """
    explicit = [item.split("=", 1)[1] for item in variables if item.startswith("MICROPY_MPYCROSS=")]
    candidate = Path(
        (explicit[-1] if explicit else "") or os.environ.get("MICROPY_MPYCROSS") or root / "mpy-cross" / "build" / "mpy-cross"
    )
    candidate = candidate if candidate.is_absolute() else Path.cwd() / candidate
    return candidate.resolve() if candidate.is_file() and os.access(candidate, os.X_OK) else None


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
        "SHELL": ("shell", shutil.which("sh")),
    }
    mpy_cross = _mpy_cross_binary(root, variables)
    if mpy_cross is not None:
        tools["MICROPY_MPYCROSS"] = ("mpy-cross", str(mpy_cross))
    make_variables = [item for item in variables if not item.startswith("MICROPY_MPYCROSS=")]
    missing = [name for name, (_, executable) in tools.items() if not executable]
    if missing:
        raise ValueError(f"missing toolchain executables for make variables: {', '.join(missing)}")

    with tempfile.TemporaryDirectory(prefix="mpy-firmware-capture-") as wrapper_dir:
        wrappers = Path(wrapper_dir)
        make_vars = [f"BUILD={build}"]
        for name, (role, executable) in tools.items():
            wrapper = wrappers / name.lower()
            _make_tool_wrapper(wrapper, role, Path(executable), journal, image_id, build_id, build)
            make_vars.append(f"{name}={wrapper}")
        (wrappers / "sitecustomize.py").write_text(
            "from mpy_analysis.firmware_observe import install\ninstall()\n", encoding="utf-8"
        )
        environment = os.environ.copy()
        environment[_WRAPPER_DIR] = str(wrappers)
        package_root = str(Path(__file__).resolve().parents[1])
        environment["PYTHONPATH"] = package_root + os.pathsep + environment.get("PYTHONPATH", "")
        result = subprocess.run(
            ["make", "-j1", "-C", str(make_directory), *make_vars, *make_variables, *(targets or ["all"])],
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
    parser.add_argument("--build-dir", type=Path, default=None)
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
        build_dir=args.build_dir,
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
