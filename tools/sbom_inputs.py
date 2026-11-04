"""Read explicit native and frozen inputs for one image."""

import hashlib
import json
import shlex
import subprocess
from pathlib import Path


def _load(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValueError(f"cannot read input {path}: {exc}") from exc


def _resolve(path, directory):
    path = Path(path)
    return (path if path.is_absolute() else directory / path).resolve()


def _within(path, root):
    return path == root or root in path.parents

def _command_outputs(entry, directory, source):
    """Find the object and explicitly requested dependency file."""
    arguments = entry.get("arguments")
    if not isinstance(arguments, list) or not all(isinstance(arg, str) for arg in arguments):
        try:
            arguments = shlex.split(entry.get("command", ""))
        except (ValueError, TypeError):
            arguments = []
    output = entry.get("output") if isinstance(entry.get("output"), str) else None
    depfile = None
    index = 0
    while index < len(arguments):
        arg = arguments[index]
        if arg in ("-o", "-MF"):
            if index + 1 < len(arguments):
                index += 1
                if arg == "-o" and output is None:
                    output = arguments[index]
                elif arg == "-MF":
                    depfile = arguments[index]
        elif arg.startswith("-MF") and len(arg) > 3:
            depfile = arg[3:]
        elif arg.startswith("-o") and len(arg) > 2 and output is None:
            output = arg[2:]
        index += 1
    if output is None:
        output = source.with_suffix(".o").name
    return _resolve(output, directory), _resolve(depfile, directory) if depfile else None


def _make_prerequisites(text):
    """Parse the prerequisites of the first make rule, ignoring later phony rules."""
    text = text.replace("\\\r\n", " ").replace("\\\n", " ")
    for line in text.splitlines():
        escaped = False
        separator = None
        for index, char in enumerate(line):
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == "#":
                break
            elif char == ":":
                separator = index
                break
        if separator is None:
            continue
        words = []
        word = []
        index = separator + 1
        while index < len(line):
            char = line[index]
            if char == "#":
                break
            if char.isspace():
                if word:
                    words.append("".join(word))
                    word = []
            elif char == "\\" and index + 1 < len(line):
                following = line[index + 1]
                if following.isspace() or following in "\\#:":
                    word.append(following)
                    index += 1
                else:
                    word.append(char)
            elif char == "$" and index + 1 < len(line) and line[index + 1] == "$":
                word.append("$")
                index += 1
            else:
                word.append(char)
            index += 1
        if word:
            words.append("".join(word))
        return words
    raise ValueError("no make rule found")


def _ninja_dependencies(root):
    """Read Ninja's dependency log without running build commands."""
    try:
        result = subprocess.run(
            ["ninja", "-C", str(root), "-t", "deps"],
            capture_output=True, text=True, encoding="utf-8", check=False,
        )
    except (OSError, UnicodeError) as exc:
        return None, f"cannot read Ninja dependency log: {exc}"
    if result.returncode:
        detail = result.stderr.strip() or result.stdout.strip()
        return None, f"ninja -t deps failed ({result.returncode}): {detail}"
    objects = {}
    current = None
    for line in result.stdout.splitlines():
        if not line:
            current = None
        elif line.startswith("    ") and current is not None:
            objects[current][1].append(line[4:])
        elif ": #deps " in line:
            name, _, description = line.rpartition(": #deps ")
            current = _resolve(name, root)
            if current in objects:
                return None, f"duplicate object in Ninja dependency log: {name}"
            objects[current] = (description, [])
        elif line.startswith("ninja: Entering directory "):
            continue
        else:
            return None, f"unrecognised Ninja dependency log output: {line}"
    return objects, None


def _dependency_sources(entry, directory, source, excludes, database_dir, ninja_cache, firmware_dir):
    obj, explicit = _command_outputs(entry, directory, source)
    candidates = []
    if explicit:
        candidates.extend((explicit, explicit.with_suffix(".P")))
    candidates.extend((obj.with_suffix(".P"), obj.with_suffix(".d"),
                       obj.with_name(obj.name + ".d")))
    candidates = list(dict.fromkeys(candidates))
    evidence = {
        "source": source, "object": obj, "depfile": explicit,
        "status": "missing" if explicit else "unspecified",
    }
    for depfile in candidates:
        try:
            text = depfile.read_text(encoding="utf-8")
        except FileNotFoundError:
            continue
        except (OSError, UnicodeError) as exc:
            evidence.update(depfile=depfile, status="unreadable", error=str(exc))
            return evidence, []
        try:
            prerequisites = _make_prerequisites(text)
        except ValueError as exc:
            evidence.update(depfile=depfile, status="unreadable", error=str(exc))
            return evidence, []
        evidence.update(depfile=depfile, status="read")
        break
    else:
        root = next((root for root in (database_dir, directory)
                     if (root / ".ninja_deps").is_file()), None)
        if root is None:
            return evidence, []
        if root not in ninja_cache:
            ninja_cache[root] = _ninja_dependencies(root)
        objects, error = ninja_cache[root]
        if error:
            evidence["error"] = error
            return evidence, []
        item = objects.get(obj)
        if item is None:
            evidence["error"] = f"object not found in Ninja dependency log: {obj}"
            return evidence, []
        description, prerequisites = item
        if "(STALE)" in description:
            evidence["error"] = f"stale Ninja dependency log entry for {obj}"
            return evidence, []
        if source not in {_resolve(path, root) for path in prerequisites}:
            evidence["error"] = f"source not found in Ninja dependency log entry for {obj}"
            return evidence, []
        directory = root
        evidence.update(depfile=root / ".ninja_deps", status="read")
    sources = []
    seen = set()
    missing = {"depfile": [], "firmware": []}
    for prerequisite in prerequisites:
        path = _resolve(prerequisite, directory)
        firmware = (path.parent == firmware_dir and
                    (path.match("w*_combined.h") or path.match("cyw43_btfw_*.h")))
        if ((path.suffix != ".c" and not firmware) or path == source or path in seen
                or any(_within(path, exclude) for exclude in excludes)):
            continue
        seen.add(path)
        kind = "firmware" if firmware else "depfile"
        if not path.is_file():
            missing[kind].append(path)
            continue
        sources.append((path, kind))
    errors = []
    for kind, label in (("depfile", "C"), ("firmware", "firmware")):
        if missing[kind]:
            errors.append(f"missing {label} dependencies: "
                          + ", ".join(str(path) for path in missing[kind]))
    if errors:
        evidence.update(status="incomplete", error="; ".join(errors))
    return evidence, sources


def read_inputs(repo, databases, excludes, frozen, depfile_evidence=None):
    """Return selected inputs and optionally collect evidence for each depfile."""
    records = []
    seen = set()
    frozen_commands = []
    exclude_paths = [_resolve(p, repo) for p in excludes]
    firmware_dir = (Path(repo) / "lib/cyw43-driver/firmware").resolve()
    ninja_cache = {}
    for database in databases:
        database = Path(database).resolve()
        entries = _load(database)
        if not isinstance(entries, list):
            raise ValueError(f"{database}: compilation database must be an array")
        for entry in entries:
            if not isinstance(entry, dict) or not isinstance(entry.get("file"), str) or not isinstance(entry.get("directory"), str):
                raise ValueError(f"{database}: each command needs string directory and file")
            directory = _resolve(entry["directory"], database.parent)
            source = _resolve(entry["file"], directory)
            if any(_within(source, p) or _within(directory, p) for p in exclude_paths):
                if source.name == "frozen_content.c":
                    raise ValueError(f"{database}: excluded frozen_content.c compilation command: {source}")
                continue
            evidence, dependencies = _dependency_sources(
                entry, directory, source, exclude_paths, database.parent, ninja_cache, firmware_dir)
            if depfile_evidence is not None:
                depfile_evidence.append(evidence)
            for dependency, kind in dependencies:
                key = ("native-" + kind, dependency, source, evidence["object"])
                if key not in seen:
                    seen.add(key)
                    records.append({
                        "source": dependency, "selection": "native", "input_kind": kind,
                        "originating_source": source, "object": evidence["object"],
                        "depfile": evidence["depfile"],
                    })
            if source.name == "frozen_content.c":
                command = (source, directory, entry.get("output"), entry.get("command"),
                           tuple(entry["arguments"]) if isinstance(entry.get("arguments"), list) else None)
                if command not in frozen_commands:
                    frozen_commands.append(command)
                continue
            key = ("native", str(source))
            if key not in seen:
                seen.add(key)
                records.append({"source": source, "selection": "native"})
    if len(frozen_commands) > 1:
        raise ValueError("compilation databases contain multiple frozen_content.c targets or commands")
    if frozen_commands and not frozen:
        raise ValueError("generated frozen_content.c requires --frozen input")
    if frozen and not frozen_commands:
        raise ValueError("--frozen input requires a frozen_content.c compilation command")
    if len(frozen) > 1:
        raise ValueError("multiple --frozen inputs are ambiguous for one image")
    for path in frozen:
        data = _load(path)
        if not isinstance(data, dict) or not isinstance(data.get("files"), list):
            raise ValueError(f"{path}: frozen input must contain a files array")
        identity = data.get("frozen_content")
        if (not isinstance(identity, dict)
            or not isinstance(identity.get("path"), str)
            or not Path(identity["path"]).is_absolute()
            or not isinstance(identity.get("build_dir"), str)
            or not Path(identity["build_dir"]).is_absolute()
            or not isinstance(identity.get("sha256"), str)
            or len(identity["sha256"]) != 64
            or any(c not in "0123456789abcdef" for c in identity["sha256"])):
            raise ValueError(f"{path}: invalid frozen_content identity")
        source = frozen_commands[0][0]
        if source != Path(identity["path"]).resolve() or not _within(source, Path(identity["build_dir"]).resolve()):
            raise ValueError(f"{path}: frozen_content identity does not match compilation database")
        try:
            with source.open("rb") as f:
                digest = hashlib.sha256()
                for chunk in iter(lambda: f.read(1024 * 1024), b""):
                    digest.update(chunk)
                digest = digest.hexdigest()
        except OSError as exc:
            raise ValueError(f"{path}: cannot read {source}: {exc}") from exc
        if digest != identity["sha256"]:
            raise ValueError(f"{path}: frozen_content.c digest does not match compilation database")
        for entry in data["files"]:
            if (not isinstance(entry, dict) or not isinstance(entry.get("source"), str)
                or not Path(entry["source"]).is_absolute() or not isinstance(entry.get("target"), str)
                or entry.get("kind") not in ("freeze_as_str", "freeze_as_mpy", "freeze_mpy")
                or (entry.get("package") is not None and not isinstance(entry["package"], str))
                or (entry.get("version") is not None and not isinstance(entry["version"], str))
                or (entry.get("license") is not None and not isinstance(entry["license"], str))
                or (entry.get("manifest") is not None and
                    (not isinstance(entry["manifest"], str) or not Path(entry["manifest"]).is_absolute()))):
                raise ValueError(f"{path}: invalid frozen input entry")
            source = Path(entry["source"]).resolve()
            manifest = Path(entry["manifest"]).resolve() if entry.get("manifest") else None
            key = ("frozen", str(source), entry["target"], entry["kind"], entry.get("package"),
                   str(manifest), entry.get("version"), entry.get("license"))
            if key not in seen:
                seen.add(key)
                records.append({"source": source, "selection": "frozen", "target": entry["target"],
                                "kind": entry["kind"], "package": entry.get("package"),
                                "manifest": manifest, "version": entry.get("version"),
                                "license": entry.get("license")})
    return records
