"""Tie the frozen Python payload of an image to the sources a captured generator consumed.

``tools/makemanifest.py`` writes ``frozen_content.c`` from a manifest. The capture adapter
observes that run while it happens (``firmware_observe``): the files the generator opens, the
processes it starts, and, as ordinary captured actions with a process parent, each ``mpy-cross``
run and the ``mpy-tool.py`` run. This module turns those records into one account per generated
frozen source:

* every ``.mpy`` handed to ``mpy-tool.py`` is tied to the ``mpy-cross`` run that wrote it by path
  and hash. The compiler input is a temporary copy of the source with an optional
  ``__version__`` line appended; it is tied to the source file the generator read by recomputing
  the hash of that source plus the appended line. A ``.mpy`` the manifest froze without compiling
  is bound by its own path;
* strings frozen as text are parsed back out of ``frozen_content.c`` and each one must equal the
  bytes of one file the generator read;
* the standard output of ``mpy-tool.py``, which the generator copies into the end of
  ``frozen_content.c``, is tied to that file by its hash and length;
* the processes the generator started must be exactly the captured child actions.

Anything the evidence does not establish stays ``unbound`` with the reason, and the account is
then ``unaccounted``. The same derivation is run by the receipt validator over the receipt's own
actions and the current files, so a payload record cannot say more than the actions show.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any, Callable, Iterable

BindingsFor = Callable[[str], list[dict[str, str]]]
ReadBytes = Callable[[str], "bytes | None"]

_VERSION_LINE = re.compile(r"\n\n__version__ = [^\n]+\n")
_NAMES = re.compile(rb'^"((?:[^"\\]|\\.)*)\\0" \\$', re.MULTILINE)
_SIZES = re.compile(rb"mp_frozen_str_sizes\[\] = \{ ((?:[0-9]+, )*)0 \};")
_CONTENT = b"const char mp_frozen_str_content[] = {\n"
_MPY_SECTION = b"// Content for MICROPY_MODULE_FROZEN_MPY"
_ESCAPES = {ord("n"): 10, ord("r"): 13, ord('"'): 34, ord("\\"): 92}


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _universal_newlines(data: bytes) -> bytes:
    return data.replace(b"\r\n", b"\n").replace(b"\r", b"\n")


def tagged_input_matches(source: bytes, tie: dict[str, Any], sha256: str) -> bool:
    """Whether the compiler input hash is the source, newline-translated as recorded, plus its tag."""
    appended = tie.get("appended")
    if tie.get("newlines") not in ("preserved", "universal") or not isinstance(appended, str):
        return False
    if appended and not _VERSION_LINE.fullmatch(appended):
        return False
    body = _universal_newlines(source) if tie["newlines"] == "universal" else source
    return _sha256(body + appended.encode()) == sha256


def find_tagged_source(compiled: bytes, candidates: Iterable[tuple[str, str, bytes]]) -> dict[str, Any] | None:
    """The candidate (path, sha256, bytes) whose content the compiler input was made from, if any.

    The generator compiles a copy of the source read in text mode with a version line appended
    when the manifest names a version, so the copy is the source (newlines translated) followed
    by nothing or by exactly that line.
    """
    for path, digest, source in candidates:
        if _sha256(source) != digest:
            continue
        for newlines in ("preserved", "universal"):
            body = _universal_newlines(source) if newlines == "universal" else source
            if not compiled.startswith(body):
                continue
            rest = compiled[len(body) :]
            try:
                appended = rest.decode()
            except UnicodeError:
                continue
            if not appended or _VERSION_LINE.fullmatch(appended):
                return {"path": path, "sha256": digest, "newlines": newlines, "appended": appended}
    return None


def _literals(block: bytes) -> bytes | None:
    """Concatenate the C string literals of a block, decoding the escapes the generator writes."""
    data = bytearray()
    position = 0
    while position < len(block):
        byte = block[position]
        if byte in b" \n":
            position += 1
            continue
        if byte != 34:
            return None
        position += 1
        while True:
            if position >= len(block):
                return None
            byte = block[position]
            if byte == 34:
                position += 1
                break
            if byte != 92:
                data.append(byte)
                position += 1
                continue
            escape = block[position + 1 : position + 2]
            if not escape:
                return None
            if escape[0] in _ESCAPES:
                data.append(_ESCAPES[escape[0]])
                position += 2
            elif escape == b"x" and re.fullmatch(rb"[0-9a-f]{2}", block[position + 2 : position + 4]):
                data.append(int(block[position + 2 : position + 4], 16))
                position += 4
            elif escape == b"0":
                data.append(0)
                position += 2
            else:
                return None
    return bytes(data)


def frozen_strings(content: bytes) -> list[tuple[str, bytes]] | None:
    """The (name, bytes) modules the generated source freezes as text; None if it cannot be parsed."""
    end = content.find(_MPY_SECTION)
    head = content if end < 0 else content[:end]
    start = head.find(b"#define MP_FROZEN_STR_NAMES \\\n")
    sizes_match = _SIZES.search(head)
    body = head.find(_CONTENT)
    if start < 0 or sizes_match is None or body < 0:
        return None
    names = [item.decode("utf-8", errors="replace") for item in _NAMES.findall(head[start : sizes_match.start()])]
    sizes = [int(item) for item in sizes_match.group(1).replace(b" ", b"").split(b",") if item]
    closing = head.rfind(b"\n};")
    if closing < body:
        return None
    blob = _literals(head[body + len(_CONTENT) : closing])
    if blob is None or len(names) != len(sizes) or len(blob) != sum(sizes) + len(sizes) + 1:
        return None
    modules = []
    offset = 0
    for name, size in zip(names, sizes):
        if blob[offset + size : offset + size + 1] != b"\0":
            return None
        modules.append((name, blob[offset : offset + size]))
        offset += size + 1
    return modules


def _option(argv: list[str], *names: str) -> str | None:
    for index, arg in enumerate(argv):
        if arg in names and index + 1 < len(argv):
            return argv[index + 1]
        for name in names:
            if name.startswith("--") and arg.startswith(name + "="):
                return arg.split("=", 1)[1]
    return None


def _resolve(path: str, cwd: str) -> str:
    candidate = Path(path)
    return str((candidate if candidate.is_absolute() else Path(cwd) / candidate).resolve(strict=False))


def _is_tool(action: dict[str, Any]) -> bool:
    argv = action["argv"]
    return len(argv) > 1 and Path(argv[1]).name == "mpy-tool.py"


def _compile_output(action: dict[str, Any]) -> str | None:
    value = _option(action["argv"][1:], "-o")
    return _resolve(value, action["cwd"]) if value and value.endswith(".mpy") else None


def _children(generator: dict[str, Any], actions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Captured actions started by the generator process during its own action."""
    pid = generator["frozen_observation"]["pid"]
    return sorted(
        (
            action
            for action in actions
            if action is not generator
            and (action.get("process") or {}).get("ppid") == pid
            and generator["capture_started_ns"] <= action["capture_started_ns"]
            and action["capture_finished_ns"] <= generator["capture_finished_ns"]
        ),
        key=lambda action: (action["capture_started_ns"], action["id"]),
    )


def _reads(observation: dict[str, Any], build_root: Path) -> tuple[dict[str, str], dict[str, str]]:
    """The files the generator read outside the build directory, and those whose bytes changed."""
    files: dict[str, str] = {}
    changed: dict[str, str] = {}
    for item in observation["reads"]:
        path = item["path"]
        if Path(path).is_relative_to(build_root):
            continue
        if files.setdefault(path, item["sha256"]) != item["sha256"]:
            changed[path] = files[path]
    return files, changed


def _bound(element: dict[str, Any], bindings: list[dict[str, str]], outside: str) -> dict[str, Any]:
    if bindings:
        element["source_bindings"] = list(bindings)
        element["disposition"] = "bound"
    else:
        element["disposition"] = "unbound"
        element["reason"] = outside
    return element


def _compiled_element(
    compile_action: dict[str, Any],
    mpy: dict[str, str],
    reads: dict[str, str],
    bindings_for: BindingsFor,
    read_bytes: ReadBytes,
) -> dict[str, Any]:
    argv = compile_action["argv"][1:]
    element: dict[str, Any] = {
        "kind": "mpy-compiled",
        "target": _option(argv, "-s"),
        "mpy": mpy,
        "compile_action": compile_action["id"],
    }
    source = next((item for item in compile_action["inputs"] if item.get("role") == "ephemeral-input"), None)
    tie = (source or {}).get("derived_from")
    if not isinstance(tie, dict):
        element["disposition"] = "unbound"
        element["reason"] = "the compiler input is not tied to a source file the generator read"
        return element
    element["source"] = {"path": tie["path"], "sha256": tie["sha256"]}
    content = read_bytes(tie["path"])
    if reads.get(tie["path"]) != tie["sha256"]:
        element["disposition"] = "unbound"
        element["reason"] = "the generator did not read this source with these bytes"
    elif content is None or _sha256(content) != tie["sha256"]:
        element["disposition"] = "unbound"
        element["reason"] = "the source file no longer has the bytes the generator read"
    elif not tagged_input_matches(content, tie, source["sha256"]):
        element["disposition"] = "unbound"
        element["reason"] = "the compiler input is not this source plus the manifest version line"
    else:
        return _bound(element, bindings_for(tie["path"]), "the frozen source lies outside the declared source roots")
    return element


def _payload(
    generator: dict[str, Any],
    output: dict[str, str],
    actions: list[dict[str, Any]],
    build_root: Path,
    bindings_for: BindingsFor,
    read_bytes: ReadBytes,
) -> dict[str, Any]:
    observation = generator["frozen_observation"]
    unresolved: list[dict[str, str]] = []
    children = _children(generator, actions)
    spawns = observation["spawns"]
    if len(spawns) != len(children) or any(
        spawn[1:] != child["argv"][1:] for spawn, child in zip(spawns, children)
    ):
        unresolved.append(
            {"reason": f"the generator started {len(spawns)} process(es) and {len(children)} were captured as its children"}
        )
    tools = [child for child in children if _is_tool(child)]
    compiles = {path: child for child in children if (path := _compile_output(child)) is not None}
    if len(tools) > 1 or len(tools) + len(compiles) != len(children):
        unresolved.append({"reason": "the generator started a process that is neither mpy-cross nor mpy-tool"})
    tool = tools[0] if len(tools) == 1 else None
    content = read_bytes(output["path"])
    reads, changed = _reads(observation, build_root)

    elements: list[dict[str, Any]] = []
    frozen_mpy: list[str] = []
    if tool is not None:
        for argument in tool["argv"][2:]:
            if not argument.endswith(".mpy"):
                continue
            path = _resolve(argument, tool["cwd"])
            frozen_mpy.append(path)
            recorded = next((item for item in tool["inputs"] if item["path"] == path), None)
            if recorded is None:
                elements.append(
                    {
                        "kind": "mpy-prebuilt",
                        "mpy": {"path": path},
                        "disposition": "unbound",
                        "reason": "the frozen .mpy is not a captured input of the mpy-tool run",
                    }
                )
                continue
            mpy = {"path": path, "sha256": recorded["sha256"]}
            produced = compiles.get(path)
            if produced is not None:
                written = next((item for item in produced["outputs"] if item["path"] == path), None)
                if written is None or written["sha256"] != mpy["sha256"]:
                    elements.append(
                        {
                            "kind": "mpy-compiled",
                            "mpy": mpy,
                            "compile_action": produced["id"],
                            "disposition": "unbound",
                            "reason": "the .mpy changed between the compile and the mpy-tool run",
                        }
                    )
                else:
                    elements.append(_compiled_element(produced, mpy, reads, bindings_for, read_bytes))
            elif Path(path).is_relative_to(build_root):
                elements.append(
                    {
                        "kind": "mpy-compiled",
                        "mpy": mpy,
                        "disposition": "unbound",
                        "reason": "no captured mpy-cross run wrote this .mpy",
                    }
                )
            else:
                elements.append(
                    _bound(
                        {"kind": "mpy-prebuilt", "mpy": mpy, "source": dict(mpy)},
                        bindings_for(path),
                        "the frozen .mpy lies outside the declared source roots",
                    )
                )
    for path in sorted(set(compiles) - set(frozen_mpy)):
        unresolved.append({"path": path, "reason": "a compiled .mpy was not passed to the mpy-tool run"})

    if tool is not None:
        stdout = tool.get("stdout")
        if (
            not isinstance(stdout, dict)
            or content is None
            or not stdout.get("size")
            or len(content) < stdout["size"]
            or _sha256(content[-stdout["size"] :]) != stdout["sha256"]
        ):
            unresolved.append({"reason": "frozen_content.c does not end with the captured mpy-tool output"})

    modules = frozen_strings(content) if content is not None else None
    if modules is None:
        unresolved.append({"reason": "the frozen string section of frozen_content.c cannot be parsed"})
        modules = []
    for name, data in modules:
        digest = _sha256(data)
        paths = sorted(path for path, value in reads.items() if value == digest)
        element: dict[str, Any] = {"kind": "str", "target": name}
        if len(paths) == 1 and _unchanged(paths[0], digest, read_bytes):
            element["source"] = {"path": paths[0], "sha256": digest}
            elements.append(
                _bound(element, bindings_for(paths[0]), "the frozen source lies outside the declared source roots")
            )
            continue
        element["disposition"] = "unbound"
        element["reason"] = (
            "no file the generator read has these bytes"
            if not paths
            else "several files the generator read have these bytes"
            if len(paths) > 1
            else "the source file no longer has the bytes frozen"
        )
        elements.append(element)

    inputs = []
    for path, digest in sorted(reads.items()):
        row: dict[str, Any] = {"path": path, "sha256": digest}
        bindings = bindings_for(path)
        if path in changed:
            row["disposition"] = "unbound"
            row["reason"] = "the file changed while the generator ran"
        elif not _unchanged(path, digest, read_bytes):
            row["disposition"] = "unbound"
            row["reason"] = "the file no longer has the bytes the generator read"
        else:
            _bound(row, bindings, "the generator read a file outside the declared source roots")
        inputs.append(row)
    accounted = (
        not unresolved
        and all(item["disposition"] == "bound" for item in elements + inputs)
    )
    return {
        "generated": output,
        "generator": generator["id"],
        "mpy_tool": {"action": tool["id"], "stdout": tool.get("stdout")} if tool is not None else None,
        "elements": elements,
        "inputs": inputs,
        "unresolved": unresolved,
        "status": "accounted" if accounted else "unaccounted",
    }


def _unchanged(path: str, digest: str, read_bytes: ReadBytes) -> bool:
    content = read_bytes(path)
    return content is not None and _sha256(content) == digest


def frozen_accounting(
    actions: list[dict[str, Any]],
    *,
    generated_sources: dict[str, str],
    build_root: Path,
    bindings_for: BindingsFor,
    read_bytes: ReadBytes,
) -> dict[str, Any]:
    """The frozen payload accounts of the generated sources an image compiles in.

    ``generated_sources`` maps the source paths of the image's contributing compiles to the hash
    each compile consumed; a generator whose ``-o`` output is one of them produced frozen content
    of that image.
    """
    payloads = []
    for action in actions:
        observation = action.get("frozen_observation")
        if observation is None or observation.get("mode") != "freeze":
            continue
        written = _option(observation.get("argv", [])[1:], "-o", "--output")
        if written is None:
            continue
        path = _resolve(written, action["cwd"])
        output = next((item for item in action["outputs"] if item["path"] == path), None)
        if path not in generated_sources or output is None:
            continue
        if observation.get("status") != "observed":
            payloads.append(
                {
                    "generated": {"path": path, "sha256": generated_sources[path]},
                    "generator": action["id"],
                    "mpy_tool": None,
                    "elements": [],
                    "inputs": [],
                    "unresolved": [{"reason": "the generator run was not observed: " + str(observation.get("reason"))}],
                    "status": "unaccounted",
                }
            )
            continue
        payloads.append(
            _payload(
                action,
                {"path": path, "sha256": generated_sources[path]},
                actions,
                build_root,
                bindings_for,
                read_bytes,
            )
        )
    status = "none" if not payloads else "accounted" if all(item["status"] == "accounted" for item in payloads) else "unaccounted"
    return {"status": status, "payloads": payloads}
