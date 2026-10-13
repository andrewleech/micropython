"""Git submodule provenance of selected checkouts, read through Git plumbing.

A submodule record states where a fetched dependency was declared to come from (the
``.gitmodules`` URL), which commit the superproject pins (the gitlink in its tree and index),
and what was actually checked out: whether the working tree exists, whether its HEAD is the
pin, which remote it is configured with, and which tracked files differ from the pinned
commit. A pin is a revision identity of the checkout, not a release or version. Records are
evidence about checkouts; they never create components.
"""

from __future__ import annotations

import hashlib
import os
import re
import subprocess
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import urlsplit

GITLINK_MODE = b"160000"
# Paths listed per record; the count and the patch digest cover the rest.
MAX_LISTED_FILES = 100


def _git(directory: Path, *arguments: str) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        ["git", "--no-optional-locks", "-C", str(directory), *arguments],
        capture_output=True,
        check=False,
    )


def _text(directory: Path, *arguments: str) -> str | None:
    result = _git(directory, *arguments)
    return os.fsdecode(result.stdout).rstrip("\n") if result.returncode == 0 else None


def worktree_head(directory: Path) -> tuple[str, str | None]:
    """State of a directory expected to be a checkout root, and its HEAD commit.

    The state is ``absent`` (no such directory), ``not-a-checkout`` (it is not the root of its
    own working tree, including an unreadable ``.git``) or ``present``. A directory inside
    another checkout is not a checkout of its own.
    """
    if not directory.is_dir():
        return "absent", None
    top = _text(directory, "rev-parse", "--show-toplevel")
    if top is None or Path(top).resolve() != directory.resolve():
        return "not-a-checkout", None
    return "present", _text(directory, "rev-parse", "HEAD")


def _normalised_path(path: str) -> str:
    path = path.strip("/")
    return path[: -len(".git")] if path.endswith(".git") else path


def _location(url: str) -> tuple[str, str] | None:
    """Host and repository path of a Git remote, independent of the transport.

    ``ssh://git@host/a/b.git``, ``git@host:a/b`` and ``https://host/a/b`` name the same
    location. A local path has an empty host.
    """
    if re.match(r"[A-Za-z][A-Za-z0-9+.-]*://", url):
        try:
            parts = urlsplit(url)
        except ValueError:
            return None
        host = "" if parts.scheme == "file" else (parts.hostname or "").lower()
        if parts.scheme != "file" and not host:
            return None
        return host, _normalised_path(parts.path)
    scp = re.fullmatch(r"(?:[^@/:]+@)?([^/:@]+):(?!//)(.*)", url)
    if scp and not url.startswith(("/", "./", "../")):
        return scp.group(1).lower(), _normalised_path(scp.group(2))
    return "", _normalised_path(url)


def _resolve_relative(url: str, base: str) -> str | None:
    """Resolve a ``./`` or ``../`` submodule URL against the superproject's remote as Git does."""
    prefix = re.match(r"[A-Za-z][A-Za-z0-9+.-]*://[^/]*/|[^/:@]+@[^/:]+:|/", base)
    head = prefix.group(0) if prefix else ""
    parts = base[len(head) :].rstrip("/").split("/")
    remainder = url
    while True:
        if remainder.startswith("../"):
            if not parts:
                return None
            parts.pop()
            remainder = remainder[3:]
        elif remainder.startswith("./"):
            remainder = remainder[2:]
        else:
            break
    return head + "/".join([*parts, remainder])


def url_relation(declared: str | None, remote: str | None, superproject_remote: str | None) -> str:
    """Compare the declared submodule URL with the remote configured in the checkout.

    ``identical`` is the same text, ``equivalent`` the same host and repository path over a
    different transport or ``.git`` suffix, ``different`` another location, and
    ``unavailable`` when either side is missing or a relative URL cannot be resolved.
    """
    if declared is None or remote is None:
        return "unavailable"
    if declared == remote:
        return "identical"
    if declared.startswith(("./", "../")):
        if superproject_remote is None:
            return "unavailable"
        resolved = _resolve_relative(declared, superproject_remote)
        if resolved is None:
            return "unavailable"
        declared = resolved
        if declared == remote:
            return "identical"
    first, second = _location(declared), _location(remote)
    if first is None or second is None:
        return "unavailable"
    return "equivalent" if first == second else "different"


def _raw_changes(directory: Path, base: str) -> list[dict[str, str]] | None:
    result = _git(
        directory,
        "diff",
        "--raw",
        "-z",
        "--no-renames",
        "--ignore-submodules=all",
        "--no-ext-diff",
        "--no-textconv",
        base,
    )
    if result.returncode:
        return None
    fields = result.stdout.split(b"\0")
    changes = []
    for index in range(0, len(fields) - 1, 2):
        status = fields[index].split()[-1].decode()
        changes.append({"status": status, "path": os.fsdecode(fields[index + 1])})
    return changes


def _patch_digest(directory: Path, base: str) -> str | None:
    """SHA-256 of the binary-safe tracked-file patch of the working tree against ``base``."""
    process = subprocess.Popen(
        [
            "git",
            "--no-optional-locks",
            "-C",
            str(directory),
            "diff",
            "--binary",
            "--no-renames",
            "--ignore-submodules=all",
            "--no-ext-diff",
            "--no-textconv",
            base,
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    )
    digest = hashlib.sha256()
    assert process.stdout is not None
    for chunk in iter(lambda: process.stdout.read(1 << 16), b""):
        digest.update(chunk)
    return digest.hexdigest() if process.wait() == 0 else None


class SubmoduleInspector:
    """Reads submodule evidence for one SBOM run; results are cached for its duration."""

    def __init__(self) -> None:
        self._toplevels: dict[Path, Path | None] = {}
        self._gitlinks: dict[Path, dict[str, dict[str, Any]]] = {}
        self._modules: dict[Path, dict[str, dict[str, Any]]] = {}
        self._records: dict[tuple[Path, str], dict[str, Any]] = {}

    def toplevel(self, directory: Path) -> Path | None:
        """The working-tree root Git reports for ``directory``, or None outside a usable checkout."""
        if directory not in self._toplevels:
            value = _text(directory, "rev-parse", "--show-toplevel") if directory.is_dir() else None
            self._toplevels[directory] = Path(value).resolve() if value else None
        return self._toplevels[directory]

    def gitlinks(self, checkout: Path) -> dict[str, dict[str, Any]]:
        """Gitlink paths of a checkout with the commit in its index and in its HEAD tree."""
        if checkout not in self._gitlinks:
            links: dict[str, dict[str, Any]] = {}
            index = _git(checkout, "ls-files", "--stage", "-z")
            for entry in index.stdout.split(b"\0") if index.returncode == 0 else ():
                if not entry.startswith(GITLINK_MODE + b" "):
                    continue
                meta, _, path = entry.partition(b"\t")
                _, sha, stage = meta.split()
                link = links.setdefault(
                    os.fsdecode(path), {"index": None, "committed": None, "unmerged": False}
                )
                if stage == b"0":
                    link["index"] = sha.decode()
                else:
                    link["unmerged"] = True
            tree = _git(checkout, "ls-tree", "-r", "-z", "HEAD")
            for entry in tree.stdout.split(b"\0") if tree.returncode == 0 else ():
                if not entry.startswith(GITLINK_MODE + b" "):
                    continue
                meta, _, path = entry.partition(b"\t")
                link = links.setdefault(
                    os.fsdecode(path), {"index": None, "committed": None, "unmerged": False}
                )
                link["committed"] = meta.split()[2].decode()
            self._gitlinks[checkout] = links
        return self._gitlinks[checkout]

    def gitmodules(self, checkout: Path) -> dict[str, dict[str, Any]]:
        """``.gitmodules`` entries of a checkout keyed by their declared path."""
        if checkout not in self._modules:
            entries: dict[str, dict[str, str]] = {}
            result = (
                _git(checkout, "config", "--file", ".gitmodules", "--null", "--list")
                if (checkout / ".gitmodules").is_file()
                else None
            )
            for item in result.stdout.split(b"\0") if result and result.returncode == 0 else ():
                key, _, value = os.fsdecode(item).partition("\n")
                if not key.startswith("submodule.") or "." not in key[len("submodule.") :]:
                    continue
                name, _, variable = key[len("submodule.") :].rpartition(".")
                entries.setdefault(name, {})[variable] = value
            modules: dict[str, dict[str, Any]] = {}
            for name, values in entries.items():
                path = values.get("path")
                if path is None:
                    continue
                entry = {"name": name, **values}
                if path in modules:
                    entry["ambiguous"] = modules[path]["ambiguous"] = True
                modules[path] = entry
            self._modules[checkout] = modules
        return self._modules[checkout]

    def record(self, superproject: Path, path: str, depth: int) -> dict[str, Any]:
        """Evidence for the submodule recorded at ``path`` in ``superproject``."""
        key = (superproject, path)
        if key not in self._records:
            self._records[key] = self._build(superproject, path, depth)
        return self._records[key]

    def _build(self, superproject: Path, path: str, depth: int) -> dict[str, Any]:
        directory = superproject / path
        link = self.gitlinks(superproject).get(path)
        entry = self.gitmodules(superproject).get(path)
        findings: set[str] = set()
        pinned = link["committed"] if link else None
        index = link["index"] if link else None
        record: dict[str, Any] = {
            "kind": "git-submodule",
            "path": path,
            "superproject": str(superproject),
            "directory": str(directory),
            "depth": depth,
            "name": entry["name"] if entry else None,
            "url": entry.get("url") if entry else None,
            "pinned_commit": pinned,
        }
        if entry and entry.get("branch"):
            record["declared_branch"] = entry["branch"]
        if link is None:
            findings.add("no-gitlink")
        else:
            if index is not None and index != pinned:
                record["index_commit"] = index
                findings.add("gitlink-not-committed" if pinned is None else "index-pin-differs-from-head-pin")
            if link["unmerged"]:
                findings.add("unmerged-gitlink")
        if entry is None or not entry.get("url"):
            findings.add("not-registered-in-gitmodules")
        if entry and entry.get("ambiguous"):
            findings.add("ambiguous-gitmodules-entry")
        if link is None:
            record["checkout"] = "absent"
            record["findings"] = sorted(findings)
            return record
        state, reason = self._checkout_state(directory)
        record["checkout"] = state
        if reason:
            record["checkout_reason"] = reason
        if state == "not-initialized":
            findings.add("not-initialized")
        elif state == "unusable":
            findings.add("unusable-checkout")
        else:
            self._inspect_checkout(record, superproject, directory, pinned, index, findings)
        record["findings"] = sorted(findings)
        return record

    def _checkout_state(self, directory: Path) -> tuple[str, str | None]:
        if not directory.is_dir():
            return "not-initialized", "the submodule directory does not exist"
        if self.toplevel(directory) == directory.resolve():
            return "present", None
        if (directory / ".git").exists() or (directory / ".git").is_symlink():
            return "unusable", "the checkout metadata is unavailable"
        if not any(directory.iterdir()):
            return "not-initialized", "the submodule directory is empty"
        return "unusable", "the directory has content but is not a Git checkout"

    def _inspect_checkout(
        self,
        record: dict[str, Any],
        superproject: Path,
        directory: Path,
        pinned: str | None,
        index: str | None,
        findings: set[str],
    ) -> None:
        head = _text(directory, "rev-parse", "HEAD")
        record["head"] = head
        expected = pinned if pinned is not None else index
        record["head_matches_pin"] = None if expected is None else head == expected
        if (pinned is not None and head != pinned) or (index is not None and head != index):
            findings.add("head-differs-from-pin")
        remote = _text(directory, "remote", "get-url", "origin")
        record["remote_url"] = remote
        # `remote get-url` applies url.<base>.insteadOf rewrites; the declared URL gets the same.
        declared = record["url"]
        if declared:
            declared = _text(superproject, "ls-remote", "--get-url", declared) or declared
        relation = url_relation(
            declared, remote, _text(superproject, "remote", "get-url", "origin")
        )
        record["url_relation"] = relation
        if relation == "different":
            findings.add("local-override")
        elif relation == "unavailable" and remote is None:
            findings.add("remote-unavailable")
        status = _text(
            directory, "status", "--porcelain", "--untracked-files=no", "--ignore-submodules=all"
        )
        record["dirty"] = bool(status) if status is not None else None
        if status:
            findings.add("dirty-tracked-files")
        if record["head_matches_pin"] is True and not status:
            return
        base = pinned if pinned is not None else index
        if base is not None and _git(directory, "cat-file", "-e", base + "^{commit}").returncode:
            findings.add("pin-object-unavailable")
            base = None
        base_name = "pin"
        if base is None:
            if not status or head is None:
                return
            base, base_name = head, "head"
        changes = _raw_changes(directory, base)
        if changes:
            record["diff_from_pin" if base_name == "pin" else "diff_from_head"] = {
                "base": base,
                "count": len(changes),
                "files": changes[:MAX_LISTED_FILES],
                "files_truncated": len(changes) > MAX_LISTED_FILES,
                "patch_sha256": _patch_digest(directory, base),
            }

    def tree(
        self, checkout: Path, depth: int = 0, seen: set[Path] | None = None
    ) -> list[dict[str, Any]]:
        """Records of every submodule below ``checkout``, to any depth through initialised ones."""
        seen = {checkout} if seen is None else seen
        records = []
        links = self.gitlinks(checkout)
        for path in sorted(links):
            record = self.record(checkout, path, depth)
            records.append(record)
            if record["checkout"] == "present":
                real = Path(record["directory"]).resolve()
                if real not in seen:
                    seen.add(real)
                    records.extend(self.tree(real, depth + 1, seen))
        for path in sorted(set(self.gitmodules(checkout)) - set(links)):
            records.append(self.record(checkout, path, depth))
        return records

    def chain(self, directory: Path) -> list[dict[str, Any]]:
        """Submodule records enclosing ``directory``, outermost first, innermost last.

        The innermost record is the submodule whose checkout contains the directory. A directory
        below a gitlink path that has no usable checkout of its own ends in that gitlink's
        record, with ``checkout`` stating why there is none. A directory in a repository that is
        not a submodule has an empty chain.
        """
        directory = directory.resolve()
        top = self.toplevel(directory)
        if top is None:
            top = next(
                (found for found in map(self.toplevel, directory.parents) if found is not None),
                None,
            )
            if top is None:
                return []
        relative = directory.relative_to(top)
        for path in sorted(self.gitlinks(top)):
            link = PurePosixPath(path)
            if relative == Path(link) or Path(link) in relative.parents:
                prefix = self._checkout_chain(top)
                return [*prefix, self.record(top, path, len(prefix))]
        return self._checkout_chain(top)

    def _checkout_chain(self, checkout: Path) -> list[dict[str, Any]]:
        parent = self.toplevel(checkout.parent)
        if parent is None or parent == checkout:
            return []
        relative = checkout.relative_to(parent).as_posix()
        if relative not in self.gitlinks(parent):
            return []
        prefix = self._checkout_chain(parent)
        return [*prefix, self.record(parent, relative, len(prefix))]

    def checkout_record(self, checkout: Path) -> dict[str, Any] | None:
        """The record of ``checkout`` as a submodule of its superproject, or None."""
        chain = self.chain(checkout)
        if chain and chain[-1]["directory"] == str(checkout.resolve()):
            return chain[-1]
        return None
