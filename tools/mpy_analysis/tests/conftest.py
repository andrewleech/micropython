"""Helpers that write compiledb-shaped entries and the dependency files GCC writes with them."""

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def write(root, rel, text=""):
    path = Path(root) / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


def entry(root, source, deps, directory=None, obj=None, write_dep=True):
    """A compiledb-shaped entry for source, with its dependency file written as GCC writes it."""
    directory = Path(directory or root)
    stem = os.path.relpath(Path(directory, source), root)
    stem = os.path.splitext(stem)[0].replace(os.sep, "_")
    obj = obj or f"{root}/build/{stem}.o"
    dep = os.path.splitext(obj)[0] + ".d"
    if write_dep:
        write(directory, dep, f"{obj}: {source} " + " ".join(str(Path(root) / d) for d in deps) + "\n")
    return {"directory": str(directory), "file": str(source),
            "arguments": ["gcc", "-c", "-MD", "-MF", dep, "-o", obj, str(source)]}


def write_db(path, entries):
    Path(path).write_text(json.dumps(entries, indent=1))
    return path
