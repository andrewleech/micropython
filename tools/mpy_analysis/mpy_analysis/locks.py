"""Fetch and lock manifests, recognised by file name and strictly validated by format.

``west.yml`` (west manifest), ESP-IDF ``dependencies.lock`` and ESP-IDF ``idf_component.yml``
are read statically, never executed, and only into candidate evidence: the entries say what a
manifest or lock declares was fetched, not what a build consumed, and no entry becomes a
component. A file that is not in a supported shape (an unknown key, an unsupported lock format
version, a manifest import that would hide further projects, YAML features such as anchors or
tags, a duplicate key, a wrongly typed field) is reported as ``unsupported`` with the reason,
and nothing is read from it.
"""

from __future__ import annotations

import configparser
import hashlib
import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any

from .submodules import worktree_head

try:
    import yaml
    from yaml.resolver import Resolver
except ModuleNotFoundError:  # pragma: no cover - exercised only without PyYAML
    yaml = None  # type: ignore[assignment]

LOCK_FILES = {
    "west.yml": "west-manifest",
    "dependencies.lock": "esp-idf-dependencies-lock",
    "idf_component.yml": "esp-idf-component-manifest",
}
MAX_BYTES = 1 << 20
IDF_LOCK_VERSION = "1.0.0"
_HEX64 = re.compile(r"[0-9a-f]{64}")
_COMMIT = re.compile(r"[0-9a-f]{40}|[0-9a-f]{64}")
_IDF_NAME = re.compile(r"[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)?")
_GROUP_FILTER = re.compile(r"[+-][^\s,+-][^\s,]*")
_STR_TAG = "tag:yaml.org,2002:str"
_NULL_TAG = "tag:yaml.org,2002:null"
_IDF_DEPENDENCY_KEYS = {
    "version",
    "public",
    "require",
    "rules",
    "matches",
    "override_path",
    "path",
    "git",
    "service_url",
    "registry_url",
}
_IDF_LOCK_SOURCES = {
    "service": ({"type", "service_url"}, {"service_url"}),
    "idf": ({"type"}, set()),
    "git": ({"type", "git", "path"}, {"git"}),
    "local": ({"type", "path"}, {"path"}),
}


class UnsupportedLockFormat(ValueError):
    """The file is recognised but not in a shape this reader supports."""


@dataclass(frozen=True)
class _Scalar:
    text: str
    tag: str


def _yaml_tree(text: str) -> Any:
    """One YAML document as dicts, lists and scalars; anchors, aliases, tags, duplicate keys and
    non-string keys are rejected so every value read has exactly one meaning."""
    if yaml is None:
        raise UnsupportedLockFormat("PyYAML is not installed, so YAML manifests cannot be read")
    resolver = Resolver()
    try:
        events = list(yaml.parse(text, Loader=yaml.SafeLoader))
    except yaml.YAMLError as exc:
        raise UnsupportedLockFormat(f"malformed YAML: {str(exc).splitlines()[0]}") from None
    position = 0
    documents = sum(isinstance(event, yaml.DocumentStartEvent) for event in events)
    if documents != 1:
        raise UnsupportedLockFormat(f"expected exactly one YAML document, found {documents}")

    def node() -> Any:
        nonlocal position
        event = events[position]
        position += 1
        if isinstance(event, yaml.AliasEvent):
            raise UnsupportedLockFormat("YAML aliases are not supported")
        if getattr(event, "anchor", None) is not None:
            raise UnsupportedLockFormat("YAML anchors are not supported")
        if getattr(event, "tag", None) is not None:
            raise UnsupportedLockFormat("explicit YAML tags are not supported")
        if isinstance(event, yaml.ScalarEvent):
            return _Scalar(event.value, resolver.resolve(yaml.ScalarNode, event.value, event.implicit))
        if isinstance(event, yaml.SequenceStartEvent):
            items = []
            while not isinstance(events[position], yaml.SequenceEndEvent):
                items.append(node())
            position += 1
            return items
        if isinstance(event, yaml.MappingStartEvent):
            mapping: dict[str, Any] = {}
            while not isinstance(events[position], yaml.MappingEndEvent):
                key = node()
                if not isinstance(key, _Scalar) or key.tag != _STR_TAG:
                    raise UnsupportedLockFormat("mapping keys must be plain strings")
                if key.text in mapping:
                    raise UnsupportedLockFormat(f"duplicate key {key.text!r}")
                mapping[key.text] = node()
            position += 1
            return mapping
        raise UnsupportedLockFormat("unsupported YAML structure")

    while not isinstance(events[position], yaml.DocumentStartEvent):
        position += 1
    position += 1
    return node()


def _string(value: Any, where: str) -> str:
    if not isinstance(value, _Scalar) or value.tag != _STR_TAG or not value.text.strip():
        raise UnsupportedLockFormat(f"{where} must be a nonempty string")
    return value.text


def _mapping(
    value: Any, where: str, *, allowed: set[str] | None = None, required: set[str] = frozenset()
) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise UnsupportedLockFormat(f"{where} must be a mapping")
    unknown = sorted(set(value) - allowed) if allowed is not None else []
    if unknown:
        raise UnsupportedLockFormat(f"{where} has unsupported key {unknown[0]!r}")
    missing = sorted(required - set(value))
    if missing:
        raise UnsupportedLockFormat(f"{where} lacks required key {missing[0]!r}")
    return value


def _sequence(value: Any, where: str) -> list[Any]:
    if not isinstance(value, list):
        raise UnsupportedLockFormat(f"{where} must be a sequence")
    return value


def _relative_path(value: str, where: str) -> str:
    if (
        PurePosixPath(value).is_absolute()
        or PureWindowsPath(value).drive
        or "\\" in value
        or any(part in {"", ".", ".."} for part in value.split("/"))
    ):
        raise UnsupportedLockFormat(f"{where} must be a relative POSIX path without traversal")
    return value


def _hash_or_null(value: Any, where: str, *, nullable: bool) -> str | None:
    if isinstance(value, _Scalar) and value.tag == _NULL_TAG:
        if nullable:
            return None
    elif isinstance(value, _Scalar) and value.tag == _STR_TAG and _HEX64.fullmatch(value.text):
        return value.text
    raise UnsupportedLockFormat(
        f"{where} must be a SHA-256 hex digest" + (" or null" if nullable else "")
    )


def _workspace(manifest: Path) -> dict[str, Any]:
    """The west workspace the manifest belongs to: its top directory and configured group filter."""
    for directory in (manifest.parent, *manifest.parent.parents):
        config_path = directory / ".west" / "config"
        if config_path.is_file():
            break
    else:
        return {"state": "no-workspace", "topdir": None, "group_filter": []}
    config = configparser.ConfigParser(interpolation=None)
    try:
        config.read_string(config_path.read_text(encoding="utf-8"))
        path = config.get("manifest", "path", fallback=None)
        name = config.get("manifest", "file", fallback="west.yml")
        raw_filter = config.get("manifest", "group-filter", fallback="")
    except (configparser.Error, OSError, UnicodeError):
        return {"state": "unsupported-config", "topdir": str(directory), "group_filter": []}
    group_filter = [item.strip() for item in raw_filter.split(",") if item.strip()]
    if path is None or (directory / path / name).resolve() != manifest:
        return {"state": "unbound", "topdir": str(directory), "group_filter": []}
    if not all(_GROUP_FILTER.fullmatch(item) for item in group_filter):
        return {"state": "unsupported-config", "topdir": str(directory), "group_filter": []}
    return {"state": "bound", "topdir": str(directory), "group_filter": group_filter}


def _active(groups: list[str], filters: list[str]) -> bool:
    """A project is active when it has no groups or any of them is enabled by the filters in order."""
    disabled: set[str] = set()
    for item in filters:
        (disabled.add if item[0] == "-" else disabled.discard)(item[1:])
    return not groups or any(group not in disabled for group in groups)


def _checkout_state(directory: Path, revision: str, kind: str) -> dict[str, Any]:
    state, head = worktree_head(directory)
    if state != "present":
        return {"state": state, "head": None}
    if kind != "commit":
        return {"state": "unverifiable-symbolic-revision", "head": head}
    return {"state": "matches" if head == revision else "differs", "head": head}


def _parse_west(root: Any, manifest: Path) -> dict[str, Any]:
    top = _mapping(root, "west manifest", allowed={"manifest"}, required={"manifest"})
    body = _mapping(
        top["manifest"],
        "manifest",
        allowed={"version", "defaults", "remotes", "projects", "self", "group-filter", "userdata"},
        required={"projects"},
    )
    if "version" in body:
        _string(body["version"], "manifest.version")
    defaults = _mapping(
        body.get("defaults", {}), "manifest.defaults", allowed={"remote", "revision"}
    )
    remotes: dict[str, str] = {}
    for index, item in enumerate(_sequence(body.get("remotes", []), "manifest.remotes")):
        remote = _mapping(
            item,
            f"manifest.remotes[{index}]",
            allowed={"name", "url-base", "userdata"},
            required={"name", "url-base"},
        )
        name = _string(remote["name"], f"manifest.remotes[{index}].name")
        if name in remotes:
            raise UnsupportedLockFormat(f"remote {name!r} is defined twice")
        remotes[name] = _string(remote["url-base"], f"remote {name!r} url-base").rstrip("/")
    default_remote = _string(defaults["remote"], "defaults.remote") if "remote" in defaults else None
    if default_remote is not None and default_remote not in remotes:
        raise UnsupportedLockFormat(f"defaults.remote {default_remote!r} is not a defined remote")
    default_revision = (
        _string(defaults["revision"], "defaults.revision") if "revision" in defaults else None
    )
    own = _mapping(
        body.get("self", {}),
        "manifest.self",
        allowed={"path", "west-commands", "import", "userdata"},
    )
    if "import" in own:
        raise UnsupportedLockFormat(
            "manifest.self.import imports further manifests, which are not followed; "
            "the project list would be incomplete"
        )
    filters = [
        _string(item, "manifest.group-filter entry")
        for item in _sequence(body.get("group-filter", []), "manifest.group-filter")
    ]
    if not all(_GROUP_FILTER.fullmatch(item) for item in filters):
        raise UnsupportedLockFormat("manifest.group-filter entries must be +group or -group")
    workspace = _workspace(manifest)
    topdir = Path(workspace["topdir"]) if workspace["state"] == "bound" else None
    names: set[str] = set()
    paths: set[str] = set()
    entries = []
    for index, item in enumerate(_sequence(body["projects"], "manifest.projects")):
        where = f"manifest.projects[{index}]"
        project = _mapping(
            item,
            where,
            allowed={
                "name",
                "remote",
                "repo-path",
                "url",
                "revision",
                "path",
                "clone-depth",
                "west-commands",
                "import",
                "groups",
                "submodules",
                "userdata",
            },
            required={"name"},
        )
        name = _string(project["name"], f"{where}.name")
        if name in names:
            raise UnsupportedLockFormat(f"project {name!r} is defined twice")
        names.add(name)
        if "import" in project:
            raise UnsupportedLockFormat(
                f"project {name!r} imports a manifest, which is not followed; "
                "the project list would be incomplete"
            )
        if "url" in project and ("remote" in project or "repo-path" in project):
            raise UnsupportedLockFormat(f"project {name!r} combines url with remote or repo-path")
        if "url" in project:
            url = _string(project["url"], f"project {name!r} url")
        else:
            remote = (
                _string(project["remote"], f"project {name!r} remote")
                if "remote" in project
                else default_remote
            )
            if remote is None:
                raise UnsupportedLockFormat(f"project {name!r} has neither url nor remote")
            if remote not in remotes:
                raise UnsupportedLockFormat(f"project {name!r} names unknown remote {remote!r}")
            repo_path = (
                _string(project["repo-path"], f"project {name!r} repo-path")
                if "repo-path" in project
                else name
            )
            url = f"{remotes[remote]}/{repo_path}"
        if "revision" in project:
            revision, source = _string(project["revision"], f"project {name!r} revision"), "project"
        elif default_revision is not None:
            revision, source = default_revision, "defaults"
        else:
            revision, source = "master", "west-default"
        path = _relative_path(
            _string(project["path"], f"project {name!r} path") if "path" in project else name,
            f"project {name!r} path",
        )
        if path in paths:
            raise UnsupportedLockFormat(f"project path {path!r} is used twice")
        paths.add(path)
        groups = [
            _string(group, f"project {name!r} group")
            for group in _sequence(project.get("groups", []), f"project {name!r} groups")
        ]
        kind = "commit" if _COMMIT.fullmatch(revision) else "symbolic"
        entry: dict[str, Any] = {
            "name": name,
            "url": url,
            "revision": revision,
            "revision_kind": kind,
            "revision_source": source,
            "path": path,
            "groups": groups,
        }
        if topdir is None:
            entry["active"] = None
            entry["checkout"] = {"state": "workspace-unavailable", "head": None}
        else:
            entry["active"] = _active(groups, [*filters, *workspace["group_filter"]])
            entry["checkout"] = (
                _checkout_state(topdir / path, revision, kind)
                if entry["active"]
                else {"state": "inactive", "head": None}
            )
        entries.append(entry)
    return {"entries": entries, "workspace": workspace, "group_filter": filters}


def _parse_idf_lock(root: Any) -> dict[str, Any]:
    top = _mapping(root, "dependencies.lock")
    version = _string(top.get("version"), "lock version") if "version" in top else None
    if version != IDF_LOCK_VERSION:
        raise UnsupportedLockFormat(
            f"lock format version {version!r} is not supported; supported: {IDF_LOCK_VERSION}"
        )
    _mapping(
        top,
        "dependencies.lock",
        allowed={"dependencies", "manifest_hash", "target", "version"},
        required={"dependencies", "manifest_hash", "target", "version"},
    )
    manifest_hash = _hash_or_null(top["manifest_hash"], "manifest_hash", nullable=False)
    target = _string(top["target"], "target")
    entries = []
    for name, value in _mapping(top["dependencies"], "dependencies").items():
        if not _IDF_NAME.fullmatch(name):
            raise UnsupportedLockFormat(f"dependency name {name!r} is not a component name")
        where = f"dependency {name!r}"
        item = _mapping(
            value, where, allowed={"version", "source", "component_hash"}, required={"version", "source", "component_hash"}
        )
        source = _mapping(item["source"], f"{where} source", required={"type"})
        kind = _string(source["type"], f"{where} source type")
        if kind not in _IDF_LOCK_SOURCES:
            raise UnsupportedLockFormat(f"{where} has unsupported source type {kind!r}")
        allowed, required = _IDF_LOCK_SOURCES[kind]
        _mapping(source, f"{where} source", allowed=allowed, required=required)
        entries.append(
            {
                "name": name,
                "version": _string(item["version"], f"{where} version"),
                "source": {key: _string(text, f"{where} source {key}") for key, text in source.items()},
                "component_hash": _hash_or_null(
                    item["component_hash"], f"{where} component_hash", nullable=kind != "service"
                ),
                "role": "sdk" if kind == "idf" else "component",
            }
        )
    return {
        "entries": entries,
        "lock_version": version,
        "manifest_hash": manifest_hash,
        "target": target,
    }


def _parse_idf_manifest(root: Any) -> dict[str, Any]:
    top = _mapping(root, "idf_component.yml")
    entries = []
    for name, value in _mapping(top.get("dependencies", {}), "dependencies").items():
        if not _IDF_NAME.fullmatch(name):
            raise UnsupportedLockFormat(f"dependency name {name!r} is not a component name")
        where = f"dependency {name!r}"
        if isinstance(value, _Scalar):
            spec, source, details, conditional = _string(value, where), "registry", {}, False
        else:
            item = _mapping(value, where, allowed=_IDF_DEPENDENCY_KEYS)
            spec = _string(item["version"], f"{where} version") if "version" in item else None
            if "git" in item and ("path" in item or "override_path" in item):
                raise UnsupportedLockFormat(f"{where} combines git with a local path")
            details = {
                key: _string(item[key], f"{where} {key}")
                for key in ("git", "path", "override_path", "service_url", "registry_url")
                if key in item
            }
            source = "git" if "git" in details else "local" if {"path", "override_path"} & set(details) else "registry"
            conditional = "rules" in item or "matches" in item
        entries.append(
            {
                "name": name,
                "spec": spec,
                "source": source,
                **details,
                "conditional": conditional,
                "role": "sdk-constraint" if name == "idf" else "component",
            }
        )
    return {"entries": entries}


_PARSERS = {
    "esp-idf-dependencies-lock": lambda tree, path: _parse_idf_lock(tree),
    "esp-idf-component-manifest": lambda tree, path: _parse_idf_manifest(tree),
    "west-manifest": _parse_west,
}


def inspect_lock(path: Path, filename: str | None = None) -> dict[str, Any]:
    """Candidate evidence for a recognised lock or manifest file, bound to its path and SHA-256.

    ``filename`` is the recognised name the file was found under (the path's own name when
    omitted); it can differ from the name of a symlink's target. ``status`` is ``candidate``
    for a file read under strict validation, ``unsupported`` with a ``reason`` otherwise. The
    evidence is never a component and never selection by itself.
    """
    format_name = LOCK_FILES[filename or path.name]
    path = path.resolve(strict=True)
    item: dict[str, Any] = {"kind": "lock-file", "format": format_name, "path": str(path)}
    try:
        try:
            data = path.read_bytes()
        except OSError as exc:
            raise UnsupportedLockFormat(f"file cannot be read: {exc.strerror}") from None
        item["sha256"] = hashlib.sha256(data).hexdigest()
        if len(data) > MAX_BYTES:
            raise UnsupportedLockFormat(f"file exceeds {MAX_BYTES} bytes")
        try:
            text = data.decode("utf-8-sig")
        except UnicodeDecodeError:
            raise UnsupportedLockFormat("file is not UTF-8 text") from None
        item.update(_PARSERS[format_name](_yaml_tree(text), path))
        item["status"] = "candidate"
    except UnsupportedLockFormat as exc:
        item.update(status="unsupported", reason=str(exc), entries=[])
    return item


def idf_component_identity(directory: Path) -> dict[str, str] | None:
    """Identity of an ESP-IDF component the component manager fetched into ``directory``.

    The manager writes ``.component_hash`` (the registry's SHA-256 of the component) beside the
    ``idf_component.yml`` it was published with, which carries ``version``. Both must be present
    and well formed; the name is the directory name with ``__`` as the namespace separator. A
    directory without them (a project's own component, an unreadable manifest) has no identity.
    """
    marker, manifest = directory / ".component_hash", directory / "idf_component.yml"
    namespace, separator, name = directory.name.partition("__")
    if not (separator and namespace and name and marker.is_file() and manifest.is_file()):
        return None
    try:
        component_hash = marker.read_text(encoding="ascii").strip()
        top = _mapping(_yaml_tree(manifest.read_text(encoding="utf-8-sig")), "idf_component.yml")
        if not _HEX64.fullmatch(component_hash):
            return None
        identity = {
            "name": f"{namespace}/{name}",
            "version": _string(top["version"], "version") if "version" in top else "",
            "component_hash": component_hash,
        }
        if "url" in top:
            identity["url"] = _string(top["url"], "url")
    except (OSError, UnicodeError, UnsupportedLockFormat):
        return None
    return identity if identity["version"] else None


def enclosing_idf_component(path: Path, stop: Path) -> tuple[Path, dict[str, str]] | None:
    """The nearest directory above ``path``, below ``stop``, that is a fetched ESP-IDF component."""
    for directory in path.parents:
        if directory == stop or stop not in directory.parents:
            return None
        identity = idf_component_identity(directory)
        if identity:
            return directory, identity
    return None


def idf_sbom_manifest(directory: Path) -> dict[str, str] | None:
    """Name, version and description from the ``sbom.yml`` ESP-IDF keeps in a vendored component.

    The file is the component owner's own declaration. It is used only when it is a readable
    mapping with a nonempty ``name`` and ``version``.
    """
    path = directory / "sbom.yml"
    if not path.is_file():
        return None
    try:
        top = _mapping(_yaml_tree(path.read_text(encoding="utf-8-sig")), "sbom.yml")
        values = {
            key: _string(top[key], key).strip()
            for key in ("name", "version", "description")
            if key in top
        }
    except (OSError, UnicodeError, UnsupportedLockFormat):
        return None
    return values if values.get("name") and values.get("version") else None
