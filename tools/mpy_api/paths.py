# SPDX-License-Identifier: MIT
"""Host-independent names for the absolute paths of a generation.

Collection works with absolute host paths: identities, input records and evidence all carry them.
Everything published is written with ``PathNames`` instead, so the output depends on the content of
the inputs and on the names given to their roots, never on where a checkout or build lives.

A rule maps an absolute path (a directory or a single file) to a name. A path under a rule's path
is published as the name followed by the rest of the path; the longest rule wins.
"""

from __future__ import annotations

import os
import re

from .chains import ChainView

# Host paths that stay as they are in anything published: installed toolchains and libraries are named
# ``system/<path>`` where the build record uses them, and the kernel's own trees are never named.
SYSTEM_PREFIXES = ("/usr/", "/opt/", "/lib/", "/lib32/", "/lib64/", "/libx32/", "/bin/", "/sbin/", "/etc/", "/nix/")
FIXED_PREFIXES = ("/dev/", "/proc/", "/sys/")

# Compiler options whose value is joined to the flag.
_PATH_OPTIONS = ("-I", "-isystem", "-iquote", "-idirafter", "-L", "-B", "-include", "-imacros", "--sysroot=")


def argument_paths(arguments):
    """The absolute paths a compiler command line names: operands and option values, and the directory or
    file of an option that joins it to its flag. A macro definition is a value, not a path."""
    for argument in arguments:
        if argument.startswith("/"):
            yield argument
        else:
            for option in _PATH_OPTIONS:
                if argument.startswith(option) and argument[len(option):].startswith("/"):
                    yield argument[len(option):]
                    break


def is_host_path(path):
    """Whether ``path`` is one of a particular machine that a published name must stand for, rather than a
    system location or a kernel tree."""
    return not path.startswith(SYSTEM_PREFIXES + FIXED_PREFIXES)

_TOKEN = r"/[\w.+~-]+(?:/[\w.+~-]+)*"
_REGULAR = re.compile(_TOKEN + r"\Z")


def _name_character(character):
    return character == "_" or character.isalnum() or character in ".+~-"


class PathNames:
    """Rewrites the absolute paths in strings, dict keys and nested values."""

    def __init__(self, rules=(), digests=()):
        table = {}
        rules = list(rules)
        # Make-style dependency text writes a space as a backslash and a space.
        rules += [(path.replace(" ", "\\ "), name.replace(" ", "\\ ")) for path, name in rules if " " in path]
        for path, name in rules:
            if not isinstance(path, str) or not path.startswith("/") or len(path) < 2:
                raise ValueError(f"a named path must be absolute and not the file system root: {path!r}")
            path = path.rstrip("/")
            if not name or name.startswith("/") or "\x00" in name:
                raise ValueError(f"invalid published path name: {name!r}")
            if table.setdefault(path, name) != name:
                raise ValueError(f"path {path} is given two names: {table[path]} and {name}")
        self._table = table
        self._digests = dict(digests)
        # A path inside a longer text is absolute and made of components of name characters. A named path
        # with other characters (a space) is looked for as a whole.
        irregular = sorted((path for path in table if not _REGULAR.match(path)), key=len, reverse=True)
        alternatives = "".join(f"(?P<irregular>{'|'.join(map(re.escape, irregular))})(?![\\w.+~-])|" if irregular else "")
        self._pattern = re.compile(alternatives + _TOKEN)

    @staticmethod
    def require(paths, where):
        """Raise ``ValueError`` when ``paths`` (host paths no name covers) is not empty."""
        paths = sorted(set(paths))
        if paths:
            shown = ", ".join(paths[:8]) + (f" and {len(paths) - 8} more" if len(paths) > 8 else "")
            raise ValueError(
                f"{where} names host paths that no root covers: {shown}; name the directory of each with "
                "--source-root NAME=DIR (or pass the file it comes from by the option that names it)")

    def extended(self, rules=(), digests=()):
        """These names and the further ``rules`` (absolute path, name) and ``digests`` as one set of names."""
        return PathNames([*self._table.items(), *rules], [*self._digests.items(), *digests])

    def _name(self, token):
        table = self._table
        cut = len(token)
        while cut > 0:
            name = table.get(token[:cut])
            if name is not None:
                return name + token[cut:]
            cut = token.rfind("/", 0, cut)
        return None

    @staticmethod
    def _standalone(match):
        """Whether the token of ``match`` is an absolute path, not the tail of a relative one. The name
        characters before it may be an option such as -I or -L."""
        start, string = match.start(), match.string
        if start and _name_character(string[start - 1]):
            first = start - 1
            while first > 0 and _name_character(string[first - 1]):
                first -= 1
            return re.fullmatch(r"-[A-Za-z]+", string[first:start]) is not None
        return True

    def unnamed(self, text, *, existing=False):
        """The absolute host paths in ``text`` that no name covers. With ``existing`` only those that exist
        on this host count, for text such as a linker map where other words look like paths."""
        found = []
        for match in self._pattern.finditer(text):
            token = match.group()
            if (self._pattern.groupindex and match.group("irregular")) or not self._standalone(match):
                continue
            if self._name(token) is None and is_host_path(token) and (not existing or os.path.lexists(token)):
                found.append(token)
        return found

    def _replace(self, match):
        token = match.group()
        irregular = match.group("irregular") if self._pattern.groupindex else None
        if not self._standalone(match):
            return token
        if irregular is not None:
            return self._table[token]
        name = self._name(token)
        return token if name is None else name

    def text(self, value):
        """``value`` with every named absolute path replaced by its name, or its published digest when it is
        the digest of a file whose content names host paths."""
        digest = self._digests.get(value)
        if digest is not None:
            return digest
        if "/" not in value or not self._table:
            return value
        return self._pattern.sub(self._replace, value)

    def value(self, root):
        """A copy of the nested dicts, lists and tuples of ``root`` with every string and dict key rewritten
        by ``text``, walking with an explicit stack. Sharing within ``root`` is kept in the copy; a ChainView
        keeps its table layout. Values of other types are shared."""
        if not self._table and not self._digests:
            return root
        text = self.text
        memo = {}
        stack = []

        def enter(item):
            kind = type(item)
            if kind is str:
                return text(item)
            if kind is ChainView:
                return ChainView(item.parents, enter(item.items), item.position, item.length)
            if kind is dict:
                copy = memo.get(id(item))
                if copy is None:
                    copy = memo[id(item)] = {}
                    stack.append((item, copy))
                return copy
            if kind is list or kind is tuple:
                copy = memo.get(id(item))
                if copy is None:
                    copy = memo[id(item)] = []
                    stack.append((item, copy))
                return copy
            return item

        result = enter(root)
        while stack:
            source, copy = stack.pop()
            if type(source) is dict:
                for key, item in source.items():
                    if type(key) is str:
                        key = text(key)
                        if key in copy:
                            raise ValueError(f"distinct paths are published under the same name: {key}")
                    copy[key] = enter(item)
            else:
                copy.extend([enter(item) for item in source])
        return result
