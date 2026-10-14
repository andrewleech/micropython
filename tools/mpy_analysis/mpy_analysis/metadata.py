"""Source-local, non-executing dependency metadata with field-level evidence."""

from __future__ import annotations

import ast
import configparser
import hashlib
import json
import re
from email.parser import Parser
from pathlib import Path, PurePosixPath
from typing import Any

try:
    import tomllib
except ImportError:  # Python 3.10
    import tomli as tomllib

from license_expression import get_spdx_licensing
from reuse.copyright import SourceType
from reuse.global_licensing import NestedReuseTOML, PrecedenceType, ReuseDep5, ReuseTOML
from reuse.project import Project

from . import license_text, mpy_tree
from .metadata_bom import valid_uri

FIELDS = ("name", "version", "description", "author", "license", "url")
CONTEXT = "repository-context"
LICENSING = get_spdx_licensing()
LICENSEREF = re.compile(r"LicenseRef-[A-Za-z0-9.-]+\Z")
CORE_KEYS = {
    "Name": "name",
    "Version": "version",
    "Summary": "description",
    "Author": "author",
    "Home-page": "url",
    "License-Expression": "license",
}


def validate_license(expression: str) -> str:
    """Validate SPDX syntax/identifiers; LicenseRef text is checked by the collector."""
    if not isinstance(expression, str) or not expression.strip():
        raise ValueError("licence expression must be a nonempty string")
    try:
        parsed = LICENSING.parse(expression, strict=True)
        if parsed is None:
            raise ValueError("empty SPDX expression")
        unknown = LICENSING.unknown_license_keys(parsed)
        invalid = [key for key in unknown if not LICENSEREF.fullmatch(key)]
        if invalid:
            raise ValueError("unknown SPDX identifier(s): " + ", ".join(invalid))
        return str(parsed)
    except Exception as error:
        raise ValueError(f"invalid SPDX expression {expression!r}: {error}") from error


def selected_digest(directory: Path, files: list[Path]) -> str:
    """Hash unique physical relative path/content-hash pairs, matching declarations."""
    directory = directory.resolve(strict=True)
    pairs = {
        path.relative_to(directory).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in {Path(file).resolve(strict=True) for file in files}
    }
    return hashlib.sha256(
        json.dumps(sorted(pairs.items()), separators=(",", ":")).encode()
    ).hexdigest()


class _ScopedLicensing:
    """Adapt ancestor/local REUSE files to one physical selection root."""

    def __init__(self, root: Path, tomls: list, dep5s: list[tuple[Path, Any]]):
        self.root = root
        self.tomls = NestedReuseTOML(reuse_tomls=tomls, source=str(root)) if tomls else None
        self.dep5s = dep5s

    def reuse_info_of(self, path: Path) -> dict:
        result = self.tomls.reuse_info_of(path) if self.tomls else {}
        absolute = self.root / path
        for directory, dep5 in self.dep5s:
            if absolute.is_relative_to(directory):
                for precedence, infos in dep5.reuse_info_of(
                    absolute.relative_to(directory)
                ).items():
                    result.setdefault(precedence, []).extend(
                        info.copy(
                            source_path=(directory / ".reuse" / "dep5")
                            .relative_to(self.root)
                            .as_posix()
                        )
                        for info in infos
                    )
        return result


def _reuse_info_of(project: Project, path: Path) -> list:
    """Apply REUSE closest fallback independently for copyright and licensing."""
    records = project.reuse_info_of(path)
    global_results = project.global_licensing.reuse_info_of(project.relative_from_root(path))
    closest = global_results.get(PrecedenceType.CLOSEST, [])
    if not closest:
        return records
    closest_sources = {info.source_path for info in closest}
    result = [
        info
        for info in records
        if info.source_type != SourceType.REUSE_TOML or info.source_path not in closest_sources
    ]
    # Override excludes closest fallback even when it leaves the file header unread.
    if PrecedenceType.OVERRIDE in global_results:
        return result
    header_types = (SourceType.FILE_HEADER, SourceType.DOT_LICENSE)
    header_copyright = any(
        info.source_type in header_types and info.copyright_notices for info in records
    )
    header_licensing = any(
        info.source_type in header_types and info.spdx_expressions for info in records
    )
    for info in closest:
        fallback = info.copy(
            copyright_notices=set() if header_copyright else info.copyright_notices,
            spdx_expressions=set() if header_licensing else info.spdx_expressions,
        )
        if fallback.contains_info():
            result.append(fallback)
    return result


class _Collector:
    def __init__(
        self,
        directory: Path,
        selected_files: list[Path] | None,
        source_context: dict[str, Any] | None,
        input_paths: set[Path] | None = None,
        binding_files: list[Path] | None = None,
    ):
        self.directory = directory.resolve(strict=True)
        self.files = (
            None
            if selected_files is None
            else sorted({Path(p).resolve(strict=True) for p in selected_files})
        )
        self.binding_files = (
            self.files
            if binding_files is None
            else sorted({Path(p).resolve(strict=True) for p in binding_files})
        )
        self.context = source_context or {}
        self.input_paths = input_paths
        self.result: dict[str, Any] = {
            "fields": {},
            "evidence": [],
            "issues": [],
            "declared_dependencies": [],
            "markers": [],
        }
        self.content: dict[Path, bytes] = {}
        self.assertions: dict[str, list[str]] = {}
        self.custom_text: dict[str, dict[str, Any]] = {}
        self.license_scope: str | None = None
        self.licensing_inputs: set[Path] = set()
        self.license_is_default = False
        self.context_dirs = self._context_chain()
        self.context_directory: Path | None = None
        for label, files in (("selected", self.files), ("binding", self.binding_files)):
            for path in files or []:
                if not path.is_relative_to(self.directory) or not path.is_file():
                    raise ValueError(f"{label} file is not contained by metadata boundary: {path}")

    def _context_chain(self) -> list[Path]:
        """Check the supplied metadata-only context is the boundary's contiguous parent chain."""
        chain: list[Path] = []
        expected = self.directory.parent
        for value in self.context.get("context_directories") or []:
            directory = Path(value).resolve(strict=True)
            if directory != expected or not directory.is_dir():
                raise ValueError(
                    f"metadata context is not the contiguous parent chain of the boundary: {value}"
                )
            chain.append(directory)
            expected = directory.parent
        return chain

    def in_context(self, path: Path) -> bool:
        """True for a file of an enclosing context directory rather than of the boundary."""
        return not path.is_relative_to(self.directory) and any(
            path.is_relative_to(directory) for directory in self.context_dirs
        )

    def allow_context(self, path: Path) -> None:
        resolved = path.resolve(strict=True)
        if self.context_directory is None or not resolved.is_relative_to(self.context_directory):
            raise ValueError(f"metadata context input escapes its directory: {path}")
        self.licensing_inputs.add(resolved)

    def read(self, path: Path) -> bytes:
        if self.input_paths is not None:
            self.input_paths.add(path.resolve())
        path = path.resolve(strict=True)
        if not path.is_relative_to(self.directory) and path not in self.licensing_inputs:
            raise ValueError(f"metadata reference escapes boundary: {path}")
        if path not in self.content:
            self.result["markers"].append(str(path))
            self.content[path] = path.read_bytes()
        return self.content[path]

    def allow_licensing(self, path: Path, root: Path) -> None:
        resolved = path.resolve(strict=True)
        if not resolved.is_relative_to(root):
            raise ValueError(f"licensing input escapes physical source root: {path}")
        self.licensing_inputs.add(resolved)

    def text(self, path: Path) -> str:
        return self.read(path).decode("utf-8-sig")

    def issue(
        self, field: str, status: str, reason: str, path: Path | None = None, **extra: Any
    ) -> None:
        if self.context_directory is not None:
            # Problems in enclosing metadata never decide a field of this boundary.
            extra = {**extra, "context": CONTEXT, "context_field": field}
            field, reason = CONTEXT, f"{field}: {reason}"
            status = "unsupported" if status == "conflicting" else status
        row = {"field": field, "status": status, "reason": reason, **extra}
        if path is not None:
            row["path"] = str(path.resolve())
        if row not in self.result["issues"]:
            self.result["issues"].append(row)

    def evidence(
        self,
        field: str,
        value: Any,
        path: Path | None,
        parser: str,
        status: str = "known",
        **extra: Any,
    ) -> None:
        row = {"field": field, "value": value, "parser": parser, "status": status, **extra}
        if path is not None:
            resolved = path.resolve()
            row.update(path=str(resolved), sha256=hashlib.sha256(self.read(path)).hexdigest())
            if self.in_context(resolved):
                row.setdefault("context", CONTEXT)
        self.result["evidence"].append(row)

    def add(self, field: str, value: Any, path: Path | None, parser: str, **extra: Any) -> None:
        raw = value
        if not isinstance(value, str) or not value.strip():
            self.evidence(field, raw, path, parser, "unsupported", **extra)
            self.issue(field, "unsupported", "expected nonempty literal string", path)
            return
        value = value.strip()
        if field == "license":
            try:
                value = validate_license(value)
            except ValueError as error:
                self.evidence(field, raw, path, parser, "unsupported", **extra)
                self.issue(field, "unsupported", str(error), path)
                return
        if field == "url" and not valid_uri(value):
            self.evidence(field, raw, path, parser, "unsupported", **extra)
            self.issue(field, "unsupported", "URL is not a valid URI", path)
            return
        if self.context_directory is not None:
            self.evidence(
                field,
                value,
                path,
                parser,
                "candidate",
                raw_value=raw,
                applicability=CONTEXT,
                detail="declared by an enclosing directory; not the identity of this boundary",
                **extra,
            )
            return
        self.evidence(
            field, value, path, parser, raw_value=raw, applicability="distribution", **extra
        )
        self.assertions.setdefault(field, []).append(value)

    def dependencies(self, values: Any, path: Path | None, parser: str) -> None:
        if isinstance(values, str):
            values = [line.strip() for line in values.splitlines() if line.strip()]
        if not isinstance(values, list) or any(not isinstance(value, str) for value in values):
            self.issue("dependencies", "unsupported", "dependencies must be literal strings", path)
            self.evidence("dependencies", values, path, parser, "unsupported")
            return
        if self.context_directory is not None:
            self.evidence("dependencies", values, path, parser, "candidate", applicability=CONTEXT)
            return
        for value in values:
            if value.strip() and value.strip() not in self.result["declared_dependencies"]:
                self.result["declared_dependencies"].append(value.strip())
        self.evidence("dependencies", values, path, parser, applicability="declared-not-selected")

    def reference(self, name: str, path: Path, parser: str, field: str = "license") -> None:
        try:
            relative = PurePosixPath(name)
            if relative.is_absolute() or ".." in relative.parts or "\\" in name:
                raise ValueError("reference must be a contained relative path")
            target = path.parent / relative
            if self.context_directory is not None:
                self.allow_context(target)
            raw = self.read(target)
            self.evidence(
                field,
                name,
                target,
                parser,
                "candidate",
                detail="referenced text, not an SPDX assertion",
                text=raw.decode("utf-8", errors="replace"),
            )
            if (
                self.context_directory is None
                and target.parent.name == "LICENSES"
                and LICENSEREF.fullmatch(target.stem)
            ):
                text = raw.decode("utf-8", errors="replace")
                prior = self.custom_text.get(target.stem)
                if prior and prior["text"] != text:
                    self.issue(
                        "license",
                        "conflicting",
                        f"different retained texts use the same {target.stem} identifier",
                        target,
                        cause_fields=["license_text:" + target.stem],
                    )
                self.custom_text[target.stem] = {"path": str(target.resolve()), "text": text}
        except (OSError, ValueError) as error:
            self.issue(
                field,
                "unsupported",
                f"unreadable/unsafe metadata reference {name!r}: {error}",
                path,
            )

    def binding(self, properties: dict[str, Any], path: Path) -> str:
        root, relative = properties.get("mpy:source:root"), properties.get("mpy:source:path")
        revision, digest = (
            properties.get("mpy:source:revision"),
            properties.get("mpy:source:sha256"),
        )
        if root is None or relative is None or bool(revision) == bool(digest):
            self.issue(
                "applicability",
                "unbound",
                "source binding requires root/path and exactly one revision or selected hash",
                path,
            )
            return "unbound"
        if not isinstance(relative, str) or not isinstance(root, str):
            self.issue(
                "applicability", "unsupported", "source binding root/path must be strings", path
            )
            return "unsupported"
        parts = PurePosixPath(relative)
        if parts.is_absolute() or ".." in parts.parts or "\\" in relative or ":" in relative:
            self.issue("applicability", "unsupported", "unsafe source binding path", path)
            return "unsupported"
        if digest and (not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest)):
            self.issue(
                "applicability",
                "unsupported",
                "source hash must be a canonical SHA-256 hex digest",
                path,
            )
            return "unsupported"
        if revision and (
            not isinstance(revision, str)
            or not re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", revision)
        ):
            self.issue(
                "applicability",
                "unsupported",
                "source revision must be an exact Git object ID",
                path,
            )
            return "unsupported"
        if root == "." and relative == ".":
            base = self.directory
            expected_root = "."
        else:
            expected_root = self.context.get("root", ".")
            base = Path(self.context.get("root_directory", self.directory)).resolve()
        bound = (base / relative).resolve()
        if root != expected_root or not bound.is_relative_to(base) or bound != self.directory:
            self.issue(
                "applicability",
                "unbound",
                "metadata is not bound to this physical source boundary",
                path,
            )
            return "unbound"
        if not self.binding_files:
            self.issue("applicability", "unbound", "source binding requires selected files", path)
            return "unbound"
        if revision:
            actual = self.context.get("revision")
            if not actual:
                self.issue(
                    "applicability",
                    "unbound",
                    "actual Git revision not supplied by source authority",
                    path,
                )
                return "unbound"
            if revision != actual:
                self.issue(
                    "applicability",
                    "stale",
                    "source revision does not match selected checkout",
                    path,
                )
                return "stale"
        else:
            for selected in self.binding_files:
                self.read(selected)
            if digest != selected_digest(self.directory, self.binding_files):
                self.issue(
                    "applicability",
                    "stale",
                    "selected-content hash does not match source binding",
                    path,
                )
                return "stale"
        return "known"

    def parse_manifest(self, metadata: dict[str, Any]) -> None:
        for field in FIELDS:
            if field in metadata:
                self.add(field, metadata[field], None, "micropython-manifest-authority-1")
        for key in ("dependencies", "requires"):
            if key in metadata:
                self.dependencies(metadata[key], None, "micropython-manifest-authority-1")

    def parse_legacy(self, path: Path) -> None:
        text = self.text(path)
        if re.search(r"^\s*[A-Za-z][A-Za-z-]*\s*=", text, re.MULTILINE):
            parser = configparser.ConfigParser(interpolation=None)
            parser.read_string("[metadata]\n" + text)
            values = dict(parser["metadata"])
            aliases = {
                "name": "name",
                "version": "version",
                "author": "author",
                "description": "description",
                "license": "license",
                "url": "url",
            }
            for key, value in values.items():
                if key in aliases:
                    self.add(
                        aliases[key],
                        value.strip().strip("\"'"),
                        path,
                        "micropython-metadata-txt-1",
                    )
                elif key in ("depends", "dependencies"):
                    self.dependencies(
                        [item.strip() for item in value.split(",")],
                        path,
                        "micropython-metadata-txt-1",
                    )
                else:
                    self.evidence(key, value, path, "micropython-metadata-txt-1", "candidate")
        else:
            self.parse_core(path, require_binding=False, parser_name="micropython-metadata-txt-1")

    def parse_project(self, path: Path) -> None:
        data = tomllib.loads(self.text(path))
        project = data.get("project")
        if project is None:
            self.issue(
                "identity",
                "not-applicable",
                "tool-only pyproject does not declare a project",
                path,
            )
            return
        if not isinstance(project, dict):
            raise ValueError("project must be a TOML table")
        parser = "pyproject-pep621-pep639-1"
        dynamic = project.get("dynamic", [])
        if not isinstance(dynamic, list) or any(not isinstance(item, str) for item in dynamic):
            raise ValueError("project.dynamic must be an array of field names")
        aliases = {"authors": "author", "urls": "url", "dependencies": "dependencies"}
        for key in dynamic:
            field = aliases.get(key, key)
            self.issue(
                field, "unsupported", "backend-generated dynamic metadata is not evaluated", path
            )
            self.evidence(field, None, path, parser, "dynamic", detail=f"project.dynamic: {key}")
            if key in project:
                self.issue(field, "conflicting", "field is both static and dynamic", path)
        for field in ("name", "version", "description"):
            if field in project and field not in dynamic:
                self.add(field, project[field], path, parser)
        if "authors" in project and "authors" not in dynamic:
            authors = project["authors"]
            if not isinstance(authors, list) or any(
                not isinstance(a, dict)
                or not (a.get("name") or a.get("email"))
                or any(
                    key in a and (not isinstance(a[key], str) or not a[key].strip())
                    for key in ("name", "email")
                )
                for a in authors
            ):
                self.issue(
                    "author", "unsupported", "authors must be literal name/email records", path
                )
            else:
                names = [
                    f"{a['name']} <{a['email']}>"
                    if a.get("name") and a.get("email")
                    else a.get("name", a.get("email"))
                    for a in authors
                ]
                self.add("author", ", ".join(names), path, parser)
        urls = project.get("urls", {})
        if "urls" not in dynamic and isinstance(urls, dict):
            home = [
                value
                for key, value in urls.items()
                if key.casefold() in ("homepage", "home", "home-page")
            ]
            for value in home:
                self.add("url", value, path, parser)
            for key, value in urls.items():
                self.evidence("urls", value, path, parser, "candidate", detail=key)
        elif "urls" not in dynamic:
            self.issue("url", "unsupported", "project.urls must be a literal table", path)
        license_value = project.get("license")
        if license_value is not None and "license" not in dynamic:
            if isinstance(license_value, str):
                self.add("license", license_value, path, parser)
            elif (
                isinstance(license_value, dict)
                and set(license_value) == {"file"}
                and isinstance(license_value["file"], str)
            ):
                self.reference(license_value["file"], path, parser)
                self.issue(
                    "license",
                    "unsupported",
                    "legacy license.file supplies text, not a validated SPDX expression",
                    path,
                )
            elif isinstance(license_value, dict) and set(license_value) == {"text"}:
                self.evidence(
                    "license",
                    license_value["text"],
                    path,
                    parser,
                    "candidate",
                    detail="legacy licence text is not an SPDX expression",
                )
                self.issue(
                    "license", "unsupported", "legacy license.text is not an SPDX assertion", path
                )
            else:
                self.issue("license", "unsupported", "unsupported project.license value", path)
        patterns = project.get("license-files", [])
        if isinstance(patterns, list) and all(isinstance(item, str) for item in patterns):
            for pattern in patterns:
                if (
                    PurePosixPath(pattern).is_absolute()
                    or ".." in PurePosixPath(pattern).parts
                    or "\\" in pattern
                    or (self.context_directory is not None and "**" in pattern)
                ):
                    self.issue("license", "unsupported", "unsafe licence-file pattern", path)
                    continue
                matches = sorted(path.parent.glob(pattern))
                if not matches:
                    self.issue(
                        "license",
                        "unsupported",
                        f"licence-file pattern matched no files: {pattern}",
                        path,
                    )
                for match in matches:
                    if match.is_file():
                        self.reference(match.relative_to(path.parent).as_posix(), path, parser)
        else:
            self.issue(
                "license", "unsupported", "license-files must be literal path patterns", path
            )
        if "dependencies" in project and "dependencies" not in dynamic:
            self.dependencies(project["dependencies"], path, parser)

    def parse_cfg(self, path: Path) -> None:
        config = configparser.ConfigParser(interpolation=None)
        config.read_string(self.text(path))
        parser = "setup-cfg-1"
        if config.has_section("metadata"):
            for field in FIELDS:
                if field not in config["metadata"]:
                    continue
                value = config["metadata"][field].strip()
                if value.startswith(("attr:", "file:")):
                    self.evidence(field, value, path, parser, "dynamic")
                    self.issue(
                        field, "unsupported", "attr/file references are not evaluated", path
                    )
                    if value.startswith("file:"):
                        for reference in value[5:].split(","):
                            self.reference(reference.strip(), path, parser, field)
                else:
                    self.add(field, value, path, parser)
            for reference in config["metadata"].get("license_files", "").splitlines():
                if reference.strip():
                    self.reference(reference.strip(), path, parser)
        if config.has_option("options", "install_requires"):
            self.dependencies(config["options"]["install_requires"], path, parser)

    def parse_setup(self, path: Path) -> None:
        tree = ast.parse(self.text(path), filename=str(path))
        parser = "setup-py-literal-ast-1"
        aliases = set()
        modules = set()
        supported_imports = set()
        for node in tree.body:
            if (
                isinstance(node, ast.ImportFrom)
                and node.level == 0
                and node.module
                in (
                    "setuptools",
                    "distutils.core",
                )
            ):
                for item in node.names:
                    if item.name == "setup":
                        name = item.asname or item.name
                        aliases.add(name)
                        supported_imports.add((node, name))
            if isinstance(node, ast.Import):
                for item in node.names:
                    if item.name in ("setuptools", "distutils.core"):
                        name = item.asname or item.name
                        modules.add(name)
                        supported_imports.add((node, name))

        def is_setup(node: ast.AST) -> bool:
            return isinstance(node, ast.Call) and (
                (isinstance(node.func, ast.Name) and node.func.id in aliases)
                or (
                    isinstance(node.func, ast.Attribute)
                    and node.func.attr == "setup"
                    and isinstance(node.func.value, ast.Name)
                    and node.func.value.id in modules
                )
            )

        calls = [node for node in ast.walk(tree) if is_setup(node)]
        direct = [
            node.value for node in tree.body if isinstance(node, ast.Expr) and is_setup(node.value)
        ]
        if len(calls) != 1 or len(direct) != 1:
            for field in FIELDS:
                self.issue(
                    field, "unsupported", "setup call is absent, ambiguous or conditional", path
                )
            return
        # Rebinding imported names makes even literal keywords computationally ambiguous.
        imported = aliases | modules

        def shadows_import(node: ast.AST) -> bool:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                return node.name in imported
            if isinstance(node, (ast.ExceptHandler, ast.MatchAs, ast.MatchStar)):
                return node.name in imported
            if isinstance(node, ast.MatchMapping):
                return node.rest in imported
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                for item in node.names:
                    if item.name == "*":
                        return True
                    name = item.asname or (
                        item.name.split(".")[0] if isinstance(node, ast.Import) else item.name
                    )
                    if name in imported and (node, name) not in supported_imports:
                        return True
            return False

        rebound = bool(aliases & modules) or any(
            (
                isinstance(node, ast.Name)
                and isinstance(node.ctx, (ast.Store, ast.Del))
                and node.id in imported
            )
            or (
                isinstance(node, ast.Attribute)
                and isinstance(node.ctx, (ast.Store, ast.Del))
                and node.attr == "setup"
                and isinstance(node.value, ast.Name)
                and node.value.id in modules
            )
            or shadows_import(node)
            for node in ast.walk(tree)
        )
        if rebound:
            for field in FIELDS:
                self.issue(field, "unsupported", "setup callable is rebound", path)
            return
        call = direct[0]
        if call.args or any(keyword.arg is None for keyword in call.keywords):
            for field in FIELDS:
                self.issue(
                    field,
                    "unsupported",
                    "setup positional/expanded arguments are not supported",
                    path,
                )
            return
        for keyword in call.keywords:
            field = "dependencies" if keyword.arg == "install_requires" else keyword.arg
            if field not in (*FIELDS, "dependencies"):
                continue
            try:
                value = ast.literal_eval(keyword.value)
            except (ValueError, TypeError):
                self.evidence(
                    field,
                    ast.get_source_segment(self.text(path), keyword.value),
                    path,
                    parser,
                    "dynamic",
                    line=keyword.lineno,
                )
                self.issue(field, "unsupported", "setup value is not literal", path)
                continue
            if field == "dependencies":
                self.dependencies(value, path, parser)
            else:
                self.add(field, value, path, parser, line=keyword.lineno)

    def parse_core(
        self, path: Path, *, require_binding: bool = True, parser_name: str = "core-metadata-2"
    ) -> None:
        message = Parser().parsestr(self.text(path))
        if require_binding and message.get("Metadata-Version") not in (
            "1.0",
            "1.1",
            "1.2",
            "2.1",
            "2.2",
            "2.3",
            "2.4",
        ):
            raise ValueError("unsupported or absent Core Metadata version")
        dynamic = {value.casefold() for value in message.get_all("Dynamic", [])}
        if any(
            len(message.get_all("X-Mpy-Source-" + key.title(), [])) > 1
            for key in ("root", "path", "revision", "sha256")
        ):
            raise ValueError("duplicate Core Metadata source-binding header")
        properties = {
            "mpy:source:" + key: message.get("X-Mpy-Source-" + key.title())
            for key in ("root", "path", "revision", "sha256")
        }
        binding_status = "known" if not require_binding else self.binding(properties, path)
        bound = binding_status == "known"
        for key, field in CORE_KEYS.items():
            for value in message.get_all(key, []):
                if key.casefold() in dynamic:
                    self.evidence(field, value, path, parser_name, "dynamic")
                    self.issue(
                        field, "unsupported", "Core Metadata declares a dynamic field", path
                    )
                elif bound:
                    self.add(field, value, path, parser_name)
                else:
                    self.evidence(field, value, path, parser_name, binding_status)
                    self.issue(
                        field,
                        binding_status,
                        "generated Core Metadata has no current source binding",
                        path,
                    )
        for value in message.get_all("License", []):
            if "license" in dynamic:
                self.evidence("license", value, path, parser_name, "dynamic")
                self.issue(
                    "license", "unsupported", "Core Metadata declares a dynamic licence", path
                )
            elif bound:
                self.add("license", value, path, parser_name)
            else:
                self.evidence("license", value, path, parser_name, binding_status)
                self.issue(
                    "license",
                    binding_status,
                    "generated Core Metadata has no current source binding",
                    path,
                )
        for value in message.get_all("License-File", []):
            self.reference(value, path, parser_name)
        for key in ("Requires-Dist", "Requires", "Dependencies"):
            if message.get_all(key):
                if bound:
                    self.dependencies(message.get_all(key), path, parser_name)
                else:
                    self.evidence(
                        "dependencies", message.get_all(key), path, parser_name, binding_status
                    )
                    self.issue(
                        "dependencies",
                        binding_status,
                        "generated dependency metadata has no current source binding",
                        path,
                    )
        for value in message.get_all("Dynamic", []):
            self.issue(
                CORE_KEYS.get(value, value.casefold()),
                "unsupported",
                "Core Metadata declares a dynamic field",
                path,
            )

    def parse_native(self, path: Path) -> None:
        from .metadata_native import parse_native

        assertions, issues = parse_native(self.text(path), path.name)
        for issue in issues:
            self.issue(issue["field"], issue.get("status", "unsupported"), issue["reason"], path)
        for field, value in assertions.items():
            self.add(field, value, path, "native-literal-1")

    def parse_library_json(self, path: Path) -> None:
        from .metadata_native import parse_library_json

        parser = "platformio-library-json-1"
        for row in parse_library_json(self.text(path)):
            field, value, status = row["field"], row["value"], row["status"]
            if status == "known":
                self.add(field, value, path, parser, key=row["key"])
            elif status == "candidate" and valid_uri(value):
                self.evidence(
                    field,
                    value,
                    path,
                    parser,
                    "candidate",
                    key=row["key"],
                    detail="retained, not asserted as the distribution URL",
                )
            else:
                reason = row.get("reason", "library.json value is not a valid URI")
                self.evidence(
                    field,
                    value,
                    path,
                    parser,
                    "unsupported" if status == "candidate" else status,
                    key=row["key"],
                    detail=reason,
                )
                self.issue(field, "unsupported" if status == "candidate" else status, reason, path)

    def parse_idf_component(self, path: Path) -> None:
        from .locks import idf_component_identity

        identity = idf_component_identity(path.parent)
        if identity is None:
            return
        self.read(path.parent / ".component_hash")
        parser = "idf-component-manifest-1"
        for field in ("name", "version", "url"):
            if field in identity:
                self.add(field, identity[field], path, parser)

    def parse_idf_sbom(self, path: Path) -> None:
        from .locks import idf_sbom_manifest

        manifest = idf_sbom_manifest(path.parent)
        if manifest is None:
            return
        for field in ("name", "version", "description"):
            if field in manifest:
                self.add(field, manifest[field], path, "idf-sbom-manifest-1")

    def parse_idf_version(self, path: Path) -> None:
        """The release of an ESP-IDF tree, from the version macros its own header defines."""
        text = self.text(path)
        parts = [
            re.search(rf"^#define\s+ESP_IDF_VERSION_{part}\s+(\d+)\s*$", text, re.MULTILINE)
            for part in ("MAJOR", "MINOR", "PATCH")
        ]
        if all(parts):
            self.add(
                "version",
                ".".join(match.group(1) for match in parts),
                path,
                "esp-idf-version-header-1",
            )

    def parse_mpy_tree(self) -> None:
        """Name, version and licence MicroPython declares for its own root and carried libraries."""
        found = mpy_tree.identity(self.directory, tuple(self.files or ()))
        if not found:
            return
        root = mpy_tree.find_root(self.directory)
        if "license" in found and root is not None and self.directory == root.resolve():
            found["license"] = self._root_license(root, found["license"])
        if self.assertions.get("name"):
            found.pop("name", None)
        for field, (value, path) in found.items():
            if path is None:
                if not self.assertions.get(field):
                    self.add(field, value, None, "git-exact-tag-1")
                continue
            self.licensing_inputs.add(path.resolve())
            self.add(field, value, path, "mpy-tree-1")
        self.license_is_default = "license" in found

    def parse_license_files(self) -> None:
        """A licence file in the boundary root whose text is exactly a canonical SPDX text."""
        for path in sorted(self.directory.iterdir()):
            if (
                path.is_file()
                and path.resolve().is_relative_to(self.directory)
                and re.fullmatch(
                    r"(?:LICENSE|LICENCE|COPYING)(?:\.(?:md|txt))?", path.name, re.IGNORECASE
                )
            ):
                identifier = license_text.identify(self.text(path))
                if identifier:
                    self.add("license", identifier, path, "license-text-match-1")
                    self.license_is_default = True

    def _root_license(self, root: Path, declared: tuple[str, Path]) -> tuple[str, Path]:
        """The root licence AND the licences the LICENSE tree gives selected files it lists."""
        licences, unmodelled = [declared[0]], {}
        for selected in self.files or []:
            relative = selected.resolve().relative_to(root.resolve()).as_posix()
            for pattern, licence in mpy_tree.deviations(root, relative):
                try:
                    licences.append(validate_license(licence))
                except ValueError:
                    unmodelled.setdefault(f"{pattern} ({licence})", []).append(str(selected))
        for entry, paths in unmodelled.items():
            self.issue(
                "license_applicability",
                "unsupported",
                f"LICENSE lists selected files under {entry}, which is not an SPDX expression",
                declared[1],
                selected_paths=paths,
            )
        unique = list(dict.fromkeys(licences))
        return " AND ".join(
            f"({value})" if len(unique) > 1 else value for value in unique
        ), declared[1]

    def parse_bom(self, path: Path) -> None:
        from .metadata_bom import parse_json, read_bom, schema_paths

        document = parse_json(self.text(path))
        # Schema inputs are packaged authority, not source-local references.
        for schema in schema_paths(document):
            resolved = schema.resolve()
            if self.input_paths is not None:
                self.input_paths.add(resolved)
            absolute = str(resolved)
            if absolute not in self.result["markers"]:
                self.result["markers"].append(absolute)
        components, _ = read_bom(document)
        matched = 0
        for component in components:
            binding_status = self.binding(component["binding"], path)
            bound = binding_status == "known"
            if bound:
                matched += 1
                if matched > 1:
                    self.issue(
                        "applicability",
                        "conflicting",
                        "multiple BOM components bind the same source boundary",
                        path,
                    )
                    for field in FIELDS:
                        self.issue(
                            field,
                            "conflicting",
                            "multiple BOM components bind the same source boundary",
                            path,
                        )
            assertions = list(component["fields"].items()) + component.get("assertions", [])
            for field, value in assertions:
                if bound:
                    self.add(field, value, path, component["parser"], component=component["id"])
                else:
                    self.evidence(
                        field,
                        value,
                        path,
                        component["parser"],
                        binding_status,
                        component=component["id"],
                    )
                    self.issue(
                        field,
                        binding_status,
                        "upstream BOM component has no matching current source binding",
                        path,
                    )
            for field, reason in component.get("issues", []):
                self.issue(field, "unsupported", reason, path)
            used_identifiers = set()
            for field, value in assertions:
                if field == "license":
                    try:
                        used_identifiers.update(
                            symbol.key
                            for symbol in LICENSING.license_symbols(validate_license(value))
                        )
                    except ValueError:
                        pass
            for identifier, text in component.get("license_texts", {}).items():
                if identifier not in used_identifiers:
                    continue
                if bound:
                    prior = self.custom_text.get(identifier)
                    if prior and prior["text"] != text:
                        self.issue(
                            "license",
                            "conflicting",
                            f"different retained texts use the same {identifier} identifier",
                            path,
                            cause_fields=["license_text:" + identifier],
                        )
                    self.custom_text[identifier] = {"path": str(path), "text": text}
                self.evidence(
                    "license_text",
                    text,
                    path,
                    component["parser"],
                    binding_status,
                    identifier=identifier,
                )
            if bound and component.get("dependencies"):
                self.dependencies(component["dependencies"], path, component["parser"])

    def parse_context(self, directory: Path) -> None:
        """Report declarative package metadata of an enclosing directory as evidence only.

        Nothing read here is asserted for this boundary or reaches ``fields``: an enclosing
        project file describes that project, and whether it also describes the selected
        sources is not something its format states.
        """
        parsers = {
            "pyproject.toml": self.parse_project,
            "setup.cfg": self.parse_cfg,
            "library.json": self.parse_library_json,
        }
        self.context_directory = directory
        try:
            for name, parse in parsers.items():
                path = directory / name
                if not path.is_file():
                    continue
                try:
                    self.allow_context(path)
                    parse(path)
                except (OSError, UnicodeError, ValueError, configparser.Error) as error:
                    self.issue("metadata", "unsupported", f"cannot parse {name}: {error}", path)
        finally:
            self.context_directory = None

    def parse_licensing(self) -> None:
        root = self.directory
        distribution_licenses = list(self.assertions.get("license", []))
        # Ancestor-only inspection captures nested REUSE scopes without visiting unrelated trees.
        licensing_root = Path(self.context.get("root_directory", root)).resolve(strict=True)
        if not root.is_relative_to(licensing_root):
            raise ValueError("metadata boundary is outside the declared physical source root")
        if self.context_dirs and licensing_root.is_relative_to(self.context_dirs[-1]):
            licensing_root = self.context_dirs[-1]
        dirs = {
            root,
            *[parent for parent in root.parents if parent.is_relative_to(licensing_root)],
        }
        for path in self.files or []:
            dirs.update(parent for parent in path.parents if parent.is_relative_to(licensing_root))
        tomls = []
        dep5s = []
        for directory in sorted(dirs):
            path = directory / "REUSE.toml"
            if path.is_file():
                try:
                    self.allow_licensing(path, licensing_root)
                    self.read(path)
                    tomls.append(ReuseTOML.from_file(path))
                except Exception as error:
                    self.issue(
                        "license_applicability",
                        "unsupported",
                        f"invalid REUSE.toml: {error}",
                        path,
                    )
        for directory in sorted(dirs):
            dep5_path = directory / ".reuse" / "dep5"
            if dep5_path.is_file():
                try:
                    self.allow_licensing(dep5_path, licensing_root)
                    self.read(dep5_path)
                    dep5s.append((directory, ReuseDep5.from_file(dep5_path)))
                except Exception as error:
                    self.issue(
                        "license_applicability",
                        "unsupported",
                        f"invalid REUSE dep5: {error}",
                        dep5_path,
                    )
        if tomls and dep5s:
            self.issue("license_applicability", "conflicting", "REUSE.toml and dep5 coexist")
        global_info = _ScopedLicensing(licensing_root, tomls, dep5s)
        project = Project(root=licensing_root, global_licensing=global_info)
        file_expressions = []
        missing = []
        for path in self.files or []:
            sidecar = Path(str(path) + ".license")
            input_path = sidecar if sidecar.is_file() else path
            try:
                self.read(input_path)
                infos = _reuse_info_of(project, path)
                expressions = []
                for info in infos:
                    origin = licensing_root / info.source_path if info.source_path else input_path
                    for expression in sorted(info.spdx_expressions, key=str):
                        try:
                            normalized = validate_license(str(expression))
                        except ValueError as error:
                            self.issue(
                                "license",
                                "unsupported",
                                str(error),
                                origin,
                                selected_path=str(path),
                            )
                            self.issue(
                                "license_applicability",
                                "unsupported",
                                "invalid selected-file SPDX expression prevents confirmed scope",
                                origin,
                                selected_path=str(path),
                            )
                            continue
                        expressions.append(normalized)
                        self.evidence(
                            "license",
                            normalized,
                            origin,
                            "reuse-6.2.0",
                            applicability="selected-file",
                            selected_path=str(path),
                        )
                    for notice in sorted(info.copyright_notices):
                        self.evidence(
                            "copyright",
                            str(notice),
                            origin,
                            "reuse-6.2.0",
                            applicability="selected-file",
                            selected_path=str(path),
                        )
                if expressions:
                    file_expressions.extend(expressions)
                else:
                    missing.append(str(path))
            except (OSError, ValueError) as error:
                self.issue("license_applicability", "unsupported", str(error), input_path)
                missing.append(str(path))
        scoped = [
            row
            for row in self.result["evidence"]
            if row["field"] == "license"
            and row["status"] == "known"
            and isinstance(row["value"], str)
        ]
        for row in scoped:
            for symbol in LICENSING.license_symbols(row["value"]):
                identifier = symbol.key
                selected_path = row.get("selected_path")
                origin = Path(selected_path).parent if selected_path else root
                ancestors = [
                    origin,
                    *[
                        parent
                        for parent in origin.parents
                        if parent.is_relative_to(licensing_root)
                    ],
                ]
                found = False
                for ancestor in ancestors:
                    target = ancestor / "LICENSES" / (identifier + ".txt")
                    if target.is_file():
                        try:
                            self.allow_licensing(target, licensing_root)
                            self.reference(
                                target.relative_to(licensing_root).as_posix(),
                                licensing_root / "REUSE.toml",
                                "reuse-license-text-1",
                            )
                            found = True
                        except (OSError, ValueError) as error:
                            self.issue(
                                "license",
                                "unsupported",
                                f"unsafe/unreadable licence text: {error}",
                                target,
                            )
                        break
                if (
                    not found
                    and LICENSEREF.fullmatch(identifier)
                    and identifier not in self.custom_text
                ):
                    self.issue(
                        "license",
                        "unsupported",
                        f"custom licence {identifier} has no retained text",
                        origin,
                        cause_fields=["license_text:" + identifier],
                    )
        # Arbitrary root licence texts are candidates, never component-wide declarations.
        for directory in (root, *self.context_dirs):
            for path in sorted(directory.iterdir()):
                if path.is_file() and re.fullmatch(
                    r"(?:LICENSE|LICENCE|COPYING|NOTICE)(?:[._-].*)?|[A-Za-z0-9._-]*LICENSE",
                    path.name,
                    re.IGNORECASE,
                ):
                    try:
                        if directory != root:
                            self.context_directory = directory
                            self.allow_context(path)
                        text = self.read(path).decode("utf-8", errors="replace")
                        self.evidence(
                            "license_text",
                            text,
                            path,
                            "license-text-1",
                            "candidate",
                            applicability="unresolved" if directory == root else CONTEXT,
                            detail="root text alone does not establish selected-file coverage",
                        )
                    except (OSError, ValueError) as error:
                        self.issue(
                            "license_applicability",
                            "unsupported",
                            f"unsafe/unreadable root licence text: {error}",
                            path,
                        )
                    finally:
                        self.context_directory = None
        if distribution_licenses:
            self.license_scope = "package-declaration"
            declarations = [
                row
                for row in self.result["evidence"]
                if row["field"] == "license"
                and row["status"] == "known"
                and row.get("applicability") == "distribution"
            ]
            for declaration in declarations:
                row = {
                    **declaration,
                    "field": "license_applicability",
                    "value": "confirmed",
                    "detail": "package-declaration",
                    "license_expression": declaration["value"],
                }
                self.result["evidence"].append(row)
        elif self.files is None:
            self.issue(
                "license_applicability",
                "unsupported",
                "selected-file applicability was not supplied",
            )
        elif not self.files:
            self.issue("license_applicability", "not-applicable", "boundary has no selected files")
        elif missing:
            self.issue(
                "license_applicability",
                "missing" if not file_expressions else "unsupported",
                "selected files have no applicable SPDX declaration"
                if not file_expressions
                else "SPDX evidence covers only part of the selection",
                selected_paths=missing,
            )
        if file_expressions:
            values = sorted(set(file_expressions))
            combined = validate_license(" AND ".join(f"({value})" for value in values))
            if not missing or distribution_licenses:
                if self.license_is_default and distribution_licenses:
                    extra = [
                        value
                        for value in values
                        if not any(
                            LICENSING.is_equivalent(value, d) for d in distribution_licenses
                        )
                    ]
                    combined = validate_license(
                        " AND ".join(f"({value})" for value in [*distribution_licenses, *extra])
                    )
                    self.assertions["license"] = [combined]
                else:
                    self.assertions.setdefault("license", []).append(combined)
            if not missing and not distribution_licenses:
                self.license_scope = "selected-file-annotations"
            self.evidence(
                "license_applicability",
                "confirmed" if not missing else None,
                None,
                "reuse-selected-files-1",
                "known" if not missing else "unsupported",
                detail="selected-file-annotations",
                covered=len(self.files) - len(missing),
                total=len(self.files),
                expressions=values,
            )

    def finish(self) -> dict[str, Any]:
        for field in FIELDS:
            values = list(dict.fromkeys(self.assertions.get(field, [])))
            if field == "license" and values:
                representative = values[0]
                values = [representative] + [
                    value
                    for value in values[1:]
                    if not LICENSING.is_equivalent(representative, value)
                ]
            if field == "name" and values:
                values = [values[0]] + [
                    value for value in values[1:] if value.casefold() != values[0].casefold()
                ]
            conflicts = [
                issue
                for issue in self.result["issues"]
                if issue["field"] == field and issue["status"] == "conflicting"
            ]
            if len(values) > 1 or conflicts:
                causes = {
                    cause for issue in conflicts for cause in issue.get("cause_fields", [field])
                }
                if len(values) > 1:
                    causes.add(field)
                self.issue(
                    field,
                    "conflicting",
                    "metadata assertions disagree",
                    values=values,
                    cause_fields=sorted(causes),
                )
            elif values:
                value = values[0]
                if field == "license":
                    missing = [
                        symbol.key
                        for symbol in LICENSING.license_symbols(value)
                        if LICENSEREF.fullmatch(symbol.key)
                        and not self.custom_text.get(symbol.key, {}).get("text", "").strip()
                    ]
                    if missing:
                        self.issue(
                            "license",
                            "missing",
                            "custom licence reference has no retained nonempty text",
                            identifiers=missing,
                            cause_fields=["license_text:" + identifier for identifier in missing],
                        )
                        continue
                self.result["fields"][field] = value
            elif not any(issue["field"] == field for issue in self.result["issues"]):
                context = [
                    row
                    for row in self.result["evidence"]
                    if row["field"] == field
                    and row["status"] == "candidate"
                    and row.get("applicability") == CONTEXT
                ]
                if context:
                    self.issue(
                        field,
                        "missing",
                        "an enclosing directory declares this field, but repository-context "
                        "metadata is not the identity of this boundary; supply package "
                        "metadata or a source-bound declaration",
                        context=CONTEXT,
                        values=sorted({str(row["value"]) for row in context}),
                    )
                else:
                    self.issue(field, "missing", "no static source-bound assertion")
        if "license" in self.result["fields"] and self.license_scope:
            applicability_unresolved = any(
                issue["field"] == "license_applicability"
                and issue["status"]
                in ("conflicting", "unsupported", "missing", "stale", "unbound")
                for issue in self.result["issues"]
            )
            if not applicability_unresolved:
                self.result["fields"]["license_applicability"] = "confirmed"
        elif self.license_scope:
            self.issue(
                "license_applicability",
                "unsupported",
                "licence expression or custom text remains unresolved",
                cause_fields=sorted(
                    {
                        cause
                        for issue in self.result["issues"]
                        if issue["field"] == "license"
                        and issue["status"] in ("conflicting", "unsupported", "missing")
                        for cause in issue.get("cause_fields", ["license"])
                    }
                    or {"license"}
                ),
            )
        notices = sorted(
            {
                row["value"]
                for row in self.result["evidence"]
                if row["field"] == "copyright"
                and row["status"] == "known"
                and isinstance(row["value"], str)
            }
        )
        if notices:
            self.result["fields"]["copyright"] = "\n".join(notices)
        self.result["markers"] = sorted(set(self.result["markers"]))
        self.result["declared_dependencies"].sort()
        self.result["license_texts"] = self.custom_text
        return self.result


def collect_metadata(
    directory: Path,
    *,
    manifest_metadata: dict | None = None,
    selected_files: list[Path] | None = None,
    binding_files: list[Path] | None = None,
    source_context: dict[str, Any] | None = None,
    input_paths: set[Path] | None = None,
) -> dict:
    """Collect only source-local static declarations, preserving conflict and scope.

    ``selected_files`` supplies only this component's assigned physical sources
    for licence applicability, not a request for a source-tree sweep.
    ``binding_files`` supplies all selected physical descendants of this boundary
    for generated/BOM content binding, including sources assigned to nested
    components; it defaults to ``selected_files`` for standalone callers.
    ``source_context`` supplies the authority's root ID, physical root directory
    and Git revision clean across those binding descendants. ``root='.',
    path='.'`` denotes this physical package boundary independently of the
    consumer's root namespace. Other selectors must match the supplied analysis
    root and physical boundary. Every binding additionally requires an exact
    selected-content hash or the authority's clean Git revision.
    ``source_context['context_directories']`` optionally lists the boundary's parent
    directories, nearest first and contiguous, chosen by the caller (see
    ``boundaries.metadata_context``). They are a metadata-only context: their
    ``pyproject.toml``, ``setup.cfg`` and ``library.json``, licence texts, ``REUSE.toml``
    and dep5 are read, never their other files and never the source selection. Package
    metadata found there is evidence marked ``applicability='repository-context'``
    and is never asserted into ``fields``. REUSE annotations govern selected files by
    their own format, so they may supply file-level licences; those rows carry
    ``context='repository-context'``.
    ``input_paths`` receives physical inputs before reads, retaining attempted
    inputs even if parsing or reading fails.
    """
    from .metadata_bom import UnsupportedBomError

    collector = _Collector(
        Path(directory), selected_files, source_context, input_paths, binding_files
    )
    if manifest_metadata:
        collector.parse_manifest(manifest_metadata)
    dispatch = {
        "metadata.txt": collector.parse_legacy,
        "pyproject.toml": collector.parse_project,
        "setup.cfg": collector.parse_cfg,
        "setup.py": collector.parse_setup,
        "PKG-INFO": collector.parse_core,
        "METADATA": collector.parse_core,
        "CMakeLists.txt": collector.parse_native,
        "meson.build": collector.parse_native,
        "library.json": collector.parse_library_json,
        "idf_component.yml": collector.parse_idf_component,
        "sbom.yml": collector.parse_idf_sbom,
        "components/esp_common/include/esp_idf_version.h": collector.parse_idf_version,
    }
    for name, parse in dispatch.items():
        path = collector.directory / name
        if path.is_file():
            try:
                parse(path)
            except (OSError, UnicodeError, ValueError, SyntaxError, configparser.Error) as error:
                collector.issue("metadata", "unsupported", f"cannot parse {name}: {error}", path)
                for field in FIELDS:
                    collector.issue(field, "unsupported", f"metadata parser failed: {error}", path)
    for path in sorted(collector.directory.iterdir()):
        if path.is_dir() and path.name.endswith((".dist-info", ".egg-info")):
            marker = path / ("METADATA" if path.name.endswith(".dist-info") else "PKG-INFO")
            if marker.is_file():
                try:
                    collector.parse_core(marker)
                except (OSError, UnicodeError, ValueError) as error:
                    collector.issue("metadata", "unsupported", str(error), marker)
                    for field in FIELDS:
                        collector.issue(
                            field, "unsupported", f"Core Metadata parser failed: {error}", marker
                        )
        elif path.is_file() and (
            path.name in ("bom.json", "sbom.json")
            or path.name.endswith((".cdx.json", ".spdx.json"))
        ):
            try:
                collector.parse_bom(path)
            except UnsupportedBomError as error:
                collector.issue(
                    "metadata", "unsupported", f"unsupported upstream BOM: {error}", path
                )
                for field in FIELDS:
                    collector.issue(
                        field, "unsupported", f"upstream BOM parser failed: {error}", path
                    )
        elif path.is_file() and path.name.endswith(
            (".cdx.xml", ".spdx", ".spdx.xml", ".spdx.yaml", ".spdx.yml")
        ):
            try:
                collector.read(path)
                for field in FIELDS:
                    collector.issue(
                        field,
                        "unsupported",
                        "upstream BOM encoding is outside the supported JSON subset",
                        path,
                    )
            except (OSError, ValueError) as error:
                collector.issue("metadata", "unsupported", str(error), path)
    try:
        collector.parse_mpy_tree()
    except (OSError, ValueError) as error:
        collector.issue("metadata", "unsupported", f"cannot read MicroPython tree: {error}")
        for field in FIELDS:
            collector.issue(field, "unsupported", f"MicroPython tree parser failed: {error}")
    try:
        collector.parse_license_files()
    except (OSError, UnicodeError) as error:
        collector.issue("license", "unsupported", f"cannot read licence file: {error}")
    for directory in collector.context_dirs:
        collector.parse_context(directory)
    collector.parse_licensing()
    return collector.finish()
