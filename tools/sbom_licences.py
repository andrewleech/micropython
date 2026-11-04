# This file is part of the MicroPython project, http://micropython.org/
#
# The MIT License (MIT)
#
# Copyright (c) 2026 Andrew Leech
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in
# all copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
# THE SOFTWARE.

"""Resolve selected source licences from reviewed, path-scoped declarations."""

import ast
import fnmatch
import json
import re
import subprocess
from pathlib import Path
from urllib.parse import urlsplit

import yaml
from license_expression import ExpressionError, get_spdx_licensing


_LICENSING = get_spdx_licensing()
_REF = re.compile(r"LicenseRef-[A-Za-z0-9.-]+\Z")
_GLOB = re.compile(r"[*?\[]")
_COMMIT = re.compile(r"git\+\S+@([0-9a-f]{40})\Z")
_TREE = re.compile(r"gitoid:tree:sha1:([0-9a-f]{40})\Z")


def _expression(value, texts):
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("licence expression must be a string")
    if not value.strip():
        return None
    if value.strip() == "NOASSERTION":
        return "NOASSERTION"
    try:
        parsed = _LICENSING.parse(value, strict=True)
        if parsed is None:
            raise ValueError("empty expression")
        for key in _LICENSING.unknown_license_keys(parsed):
            if not _REF.fullmatch(key):
                raise ValueError("unknown SPDX identifier " + key)
        for symbol in _LICENSING.license_symbols(parsed):
            if _REF.fullmatch(symbol.key) and not texts.get(symbol.key, "").strip():
                raise ValueError("missing extracted text for " + symbol.key)
        return str(parsed)
    except ExpressionError as error:
        raise ValueError(str(error)) from error


def _equivalent(left, right):
    if "NOASSERTION" in (left, right):
        return left == right
    return _LICENSING.is_equivalent(left, right)


def _load(root):
    path = root / "LICENSE.spdx.yaml"
    if not path.is_file():
        return [], {}, {}
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(document, dict) or not isinstance(document.get("packages"), list):
        raise ValueError(f"{path}: expected packages list")
    texts = {}
    details = {}
    for item in document.get("hasExtractedLicensingInfos", []):
        if not isinstance(item, dict) or not isinstance(item.get("licenseId"), str) or not isinstance(item.get("extractedText"), str):
            raise ValueError(f"{path}: invalid extracted licence text")
        if item["licenseId"] in texts:
            raise ValueError(f"{path}: duplicate extracted licence {item['licenseId']}")
        if "seeAlso" in item:
            urls = item["seeAlso"]
            if not isinstance(urls, list):
                raise ValueError(f"{path}: invalid seeAlso for {item['licenseId']}")
            for url in urls:
                try:
                    parsed = urlsplit(url) if isinstance(url, str) else None
                    valid = (parsed and parsed.scheme in ("http", "https") and parsed.hostname
                             and parsed.port != 0 and not re.search(r"[\s\x00-\x1f\x7f\\]", url)
                             and not re.search(r"%(?![0-9a-fA-F]{2})", url))
                except ValueError:
                    valid = False
                if not valid:
                    raise ValueError(f"{path}: invalid seeAlso URL for {item['licenseId']}: {url!r}")
        texts[item["licenseId"]] = item["extractedText"]
        details[item["licenseId"]] = {key: item[key] for key in ("name", "comment", "seeAlso") if key in item}
    entries = []
    seen = set()
    for item in document["packages"]:
        if not isinstance(item, dict):
            raise ValueError(f"{path}: invalid package")
        scope = item.get("packageFileName")
        if not isinstance(scope, str) or not scope or (scope != "." and (scope.startswith("/") or any(part in ("", ".", "..") for part in scope.split("/")))):
            raise ValueError(f"{path}: invalid packageFileName {scope!r}")
        if scope in seen:
            raise ValueError(f"{path}: duplicate packageFileName {scope}")
        seen.add(scope)
        entry = dict(item)
        entry["expression"] = _expression(item.get("licenseDeclared"), texts)
        entry["scope"] = scope
        entries.append(entry)
    directories = [entry for entry in entries if entry["scope"] != "." and not _GLOB.search(entry["scope"]) and not (root / entry["scope"]).is_file()]
    for entry in directories:
        scope = entry["scope"]
        parents = [other for other in directories if scope.startswith(other["scope"] + "/")]
        if parents:
            continue
        commit = _COMMIT.fullmatch(entry.get("downloadLocation") or "")
        refs = entry.get("externalRefs") or []
        if not isinstance(refs, list):
            refs = [refs]
        trees = []
        for ref in refs:
            if not isinstance(ref, dict):
                raise ValueError(f"{path}: invalid external reference for {scope}")
            if ref.get("referenceType") == "gitoid":
                match = _TREE.fullmatch(ref.get("referenceLocator") or "")
                if not match or ref.get("referenceCategory") != "PERSISTENT-ID":
                    raise ValueError(f"{path}: invalid tree pin for {scope}")
                trees.append(match.group(1))
        if bool(commit) == bool(trees) or len(trees) > 1:
            raise ValueError(f"{path}: {scope} requires exactly one directory pin")
        entry["pin"] = ("commit", commit.group(1)) if commit else ("tree", trees[0])
    return entries, texts, details


def _covers(scope, source):
    return scope == "." or fnmatch.fnmatchcase(source, scope) or source.startswith(scope + "/")


def _git(root, *args):
    try:
        result = subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, check=False)
    except OSError:
        return None
    return result.stdout.strip() if result.returncode == 0 else None

def _git_url(value):
    """Normalize network Git URLs without treating different paths as equal."""
    if not isinstance(value, str):
        return None
    scp = re.fullmatch(r"(?:[^/@:]+@)?([^/:]+):(.+)", value)
    if scp and "://" not in value:
        host, path = scp.groups()
        port = None
    else:
        try:
            parsed = urlsplit(value)
            if parsed.scheme.lower() not in ("https", "http", "ssh", "git") or not parsed.hostname or parsed.query or parsed.fragment:
                return None
            host, path, port = parsed.hostname, parsed.path, parsed.port
        except ValueError:
            return None
    path = path.strip("/")
    if path.endswith(".git"):
        path = path[:-4]
    if not path or any(part in ("", ".", "..") for part in path.split("/")):
        return None
    return (host.lower(), port, path)


def _external_declarations(root, texts):
    path = root / "LICENSE.spdx.yaml"
    if not path.is_file():
        return {}
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    repositories = document.get("external_repositories", [])
    if not isinstance(repositories, list):
        raise ValueError(f"{path}: expected external_repositories list")
    result = {}
    for item in repositories:
        if not isinstance(item, dict):
            raise ValueError(f"{path}: invalid external repository")
        url = _git_url(item.get("url"))
        commit = item.get("commit")
        if not url or not isinstance(commit, str) or not re.fullmatch(r"[0-9a-f]{40}", commit):
            raise ValueError(f"{path}: invalid external repository URL or commit")
        key = (url, commit)
        if key in result or not isinstance(item.get("licenses"), list):
            raise ValueError(f"{path}: duplicate or invalid external repository {item.get('url')}")
        scopes = []
        seen = set()
        for declaration in item["licenses"]:
            if not isinstance(declaration, dict):
                raise ValueError(f"{path}: invalid external licence declaration")
            scope = declaration.get("path")
            if (not isinstance(scope, str) or not scope or scope.startswith("/")
                    or (scope != "." and any(part in ("", ".", "..") for part in scope.split("/")))):
                raise ValueError(f"{path}: invalid external licence path {scope!r}")
            if scope in seen:
                raise ValueError(f"{path}: duplicate external licence path {scope}")
            seen.add(scope)
            expression = _expression(declaration.get("license"), texts)
            if not expression:
                raise ValueError(f"{path}: missing external licence for {scope}")
            scopes.append({"scope": scope, "expression": expression})
        result[key] = scopes
    return result


def _external_checkout(source, component, repo, checkout_cache, root_cache, reviewed):
    """Return a verified checkout identity, or None if it cannot be trusted."""
    if component.get("unreviewed") or component.get("identity_problems") or not source.exists():
        return None
    directory = source if source.is_dir() else source.parent
    if directory not in root_cache:
        top = _git(directory, "rev-parse", "--show-toplevel")
        root_cache[directory] = Path(top).resolve() if top else None
    checkout = root_cache[directory]
    if not checkout or checkout == repo or not source.is_relative_to(checkout):
        return None
    relative = source.relative_to(checkout).as_posix()
    if not _git(checkout, "ls-files", "--", relative):
        return None
    if checkout not in checkout_cache:
        checkout_cache[checkout] = _verified_external_checkout(checkout, reviewed)
    identity = checkout_cache[checkout]
    if not identity or (component.get("revision") and component["revision"] != identity[2]):
        return None
    return identity


def _verified_external_checkout(checkout, reviewed):
    revision = _git(checkout, "rev-parse", "HEAD")
    remote = _git_url(_git(checkout, "remote", "get-url", "origin"))
    if (not remote or not revision or not re.fullmatch(r"[0-9a-f]{40}", revision)
            or (remote, revision) not in reviewed):
        return None
    if _git(checkout, "status", "--porcelain", "--untracked-files=all") != "":
        return None
    current = checkout
    while True:
        parent_top = _git(current.parent, "rev-parse", "--show-toplevel")
        if not parent_top:
            break
        parent = Path(parent_top).resolve()
        if parent == current or not current.is_relative_to(parent):
            return None
        subpath = current.relative_to(parent).as_posix()
        head = _git(current, "rev-parse", "HEAD")
        if _git(parent, "ls-tree", "HEAD", "--", subpath) != f"160000 commit {head}\t{subpath}":
            return None
        if _git(parent, "status", "--porcelain", "--ignore-submodules=none", "--", subpath) != "":
            return None
        current = parent
    return checkout, remote, revision


def _pin_problem(root, entry):
    kind, expected = entry["pin"]
    scope = entry["scope"]
    record = _git(root, "ls-tree", "HEAD", "--", scope)
    fields = record.split(None, 3) if record else []
    actual_kind = "commit" if len(fields) >= 3 and fields[1] == "commit" else "tree"
    actual = fields[2] if len(fields) >= 3 else None
    problems = []
    if (actual_kind, actual) != (kind, expected):
        problems.append({
            "message": f"{scope}: licence pin {kind} {expected} does not match HEAD {actual_kind} {actual or 'unavailable'}",
            "expected": f"{kind} {expected}",
            "actual": f"{actual_kind} {actual or 'unavailable'}",
        })
    if kind == "commit" and (root / scope / ".git").exists():
        checkout = _git(root / scope, "rev-parse", "HEAD")
        if checkout != expected:
            problems.append({
                "message": f"{scope}: licence pin commit {expected} does not match checkout {checkout or 'unavailable'}",
                "expected": f"commit {expected}",
                "actual": f"commit {checkout or 'unavailable'}",
            })
    return problems


class _MetadataConflict(ValueError):
    def __init__(self, directory, declarations):
        self.declarations = declarations
        super().__init__(f"{directory}: conflicting package licence declarations: {declarations}")


def _manifest_license(directory, texts, untrusted=()):
    declarations = []
    path = directory / "library.json"
    if path.is_file() and path not in untrusted:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError(f"{path}: expected an object")
        value = data.get("license")
        if value is not None:
            declarations.append(_expression(value, texts))
    path = directory / "manifest.py"
    if path.is_file() and path not in untrusted:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for statement in tree.body:
            if not isinstance(statement, ast.Expr) or not isinstance(statement.value, ast.Call):
                continue
            call = statement.value
            if not isinstance(call.func, ast.Name) or call.func.id != "metadata":
                continue
            for keyword in call.keywords:
                if keyword.arg == "license":
                    if not isinstance(keyword.value, ast.Constant) or not isinstance(keyword.value.value, str):
                        raise ValueError(f"{path}: licence is not a literal string")
                    declarations.append(_expression(keyword.value.value, texts))
    declarations = list(dict.fromkeys(value for value in declarations if value is not None))
    if declarations and any(not _equivalent(declarations[0], value) for value in declarations[1:]):
        raise _MetadataConflict(directory, declarations)
    return declarations[0] if declarations else None


def _source_license(path, texts):
    text = path.read_text(encoding="utf-8")
    comments = []
    pos = 0
    python = path.suffix == ".py"
    while pos < len(text):
        while pos < len(text) and text[pos].isspace():
            pos += 1
        if pos >= len(text):
            break
        if python and text[pos] == "#" or not python and text.startswith("//", pos):
            end = text.find("\n", pos)
            if end < 0:
                end = len(text)
            comments.append(text[pos + (1 if python else 2):end])
            pos = end
        elif not python and text.startswith("/*", pos):
            end = text.find("*/", pos + 2)
            if end < 0:
                break
            comments.extend(line.lstrip().removeprefix("*") for line in text[pos + 2:end].splitlines())
            pos = end + 2
        else:
            break
    declarations = []
    legacy_mit = False
    for line in comments:
        comment = line.strip()
        spdx = re.fullmatch(r"SPDX-License-Identifier:\s*(.*)", comment)
        if spdx:
            expression = _expression(spdx.group(1), texts)
            if not expression:
                raise ValueError("SPDX licence header has no asserted expression")
            declarations.append(expression)
        elif python and re.fullmatch(r"MIT licen[sc]e(?:;\s*Copyright\b.*)?", comment, re.IGNORECASE):
            legacy_mit = True
    declarations = list(dict.fromkeys(value for value in declarations if value is not None))
    if declarations and any(not _equivalent(declarations[0], value) for value in declarations[1:]):
        raise _MetadataConflict(path, declarations)
    if not declarations and legacy_mit:
        return "MIT", "source MIT header"
    provenance = "source SPDX header"
    return (declarations[0], provenance) if declarations else (None, None)


def _micropython_lib_package(root, owner_root, directory, component, entries):
    if component.get("kind") != "frozen" or component.get("unreviewed"):
        return False
    if not directory.is_relative_to(owner_root) or not (directory / "manifest.py").is_file():
        return False
    parts = directory.relative_to(owner_root).parts
    if len(parts) < 2 or parts[0] not in ("micropython", "python-stdlib", "python-ecosys", "unix-ffi"):
        return False
    for entry in entries:
        location = entry.get("downloadLocation") or ""
        if (entry["scope"] != "." and (root / entry["scope"]).resolve() == owner_root
                and _COMMIT.fullmatch(location)
                and location.startswith("git+https://github.com/micropython/micropython-lib.git@")):
            return True
    return False


def resolve_licences(repo, components, declaration_states=None, metadata_status=None, external_cache=None):
    """Mutate components with declared licences and return unresolved issues."""
    root = Path(repo).resolve()
    declaration_states = declaration_states or {}
    declarations = {}

    def load_declaration(declaration_root):
        if declaration_root not in declarations:
            status, _ = declaration_states.get(declaration_root, (None, None))
            declarations[declaration_root] = ([], {}, {}) if status else _load(declaration_root)
        return declarations[declaration_root]
    pin_cache = {}
    if external_cache is None:
        external_cache = {}
    checkout_cache = external_cache.setdefault("checkouts", {})
    root_cache = external_cache.setdefault("roots", {})
    repo_status, _ = declaration_states.get(root, (None, None))
    if not repo_status:
        _, repo_texts, repo_details = load_declaration(root)
        external_declarations = _external_declarations(root, repo_texts)
    else:
        repo_texts, repo_details, external_declarations = {}, {}, {}
    issues = []
    for component in components:
        component_path = Path(component["path"])
        directory = component_path if component_path.is_absolute() else root / component_path
        directory = directory.resolve()
        declaration_root = Path(component.get("declaration_root", root if directory.is_relative_to(root) else directory)).resolve()
        owner_root = Path(component.get("owner_root", directory))
        owner_root = (owner_root if owner_root.is_absolute() else root / owner_root).resolve()
        local = declaration_root == root
        component_entries, component_texts, component_details = load_declaration(declaration_root)
        sources = component.get("sources") or [str(directory)]
        expressions = []
        file_licences = component["file_licences"] = []
        comments = component.setdefault("licence_comments", [])
        component_issues = component.setdefault("licence_issues", [])

        def add_issue(kind, message, **evidence):
            issue = {"kind": kind, "status": "unresolved", "message": message, **evidence}
            if issue not in component_issues:
                component_issues.append(issue)
            if message not in comments:
                comments.append(message)
            issues.append(f"{component['name']}: {message}")
        external_library = False
        if declaration_root == owner_root and not owner_root.is_relative_to(root):
            remote = _git(owner_root, "remote", "get-url", "origin")
            if remote in (
                "https://github.com/micropython/micropython-lib",
                "https://github.com/micropython/micropython-lib.git",
                "git@github.com:micropython/micropython-lib.git",
            ):
                repo_entries, repo_texts, repo_details = load_declaration(root)
                library_entries = [
                    item for item in repo_entries
                    if (item.get("downloadLocation") or "").startswith(
                        "git+https://github.com/micropython/micropython-lib.git@"
                    ) and item.get("pin", (None,))[0] == "commit"
                ]
                for library in library_entries:
                    revision = library["pin"][1]
                    if (component.get("revision") != revision or component.get("unreviewed")
                            or component.get("identity_problems")
                            or _git(owner_root, "rev-parse", "--show-toplevel") != str(owner_root)
                            or _git(owner_root, "rev-parse", "HEAD") != revision
                            or _pin_problem(root, library)):
                        continue
                    prefix = library["scope"] + "/"
                    mapped = [
                        {**item, "scope": item["scope"][len(prefix):]}
                        for item in repo_entries if item["scope"].startswith(prefix)
                    ]
                    if any(key in component_texts and component_texts[key] != value
                           for key, value in repo_texts.items()):
                        continue
                    component_entries = mapped + component_entries
                    component_texts = {**component_texts, **repo_texts}
                    component_details = {**component_details, **repo_details}
                    external_library = True
                    break
                if not external_library:
                    add_issue("source_identity",
                              f"{owner_root}: external micropython-lib revision is not covered by a valid reviewed pin",
                              source=str(owner_root))
        untrusted_metadata = set()
        if metadata_status:
            for name in ("library.json", "manifest.py"):
                path = directory / name
                if path.is_file():
                    status = metadata_status(path)
                    if status:
                        untrusted_metadata.add(path)
                        add_issue("source_identity", f"{path}: package metadata is {status}",
                                  source=str(path), identity_status=status)
        library_default = (directory / "manifest.py" not in untrusted_metadata
                           and (_micropython_lib_package(
                               declaration_root, owner_root, directory, component, component_entries
                           ) or (external_library and component.get("kind") == "frozen"
                                 and directory.is_relative_to(owner_root)
                                 and (directory / "manifest.py").is_file()
                                 and len(directory.relative_to(owner_root).parts) >= 2
                                 and directory.relative_to(owner_root).parts[0] in (
                                     "micropython", "python-stdlib", "python-ecosys", "unix-ffi"
                                 ))))
        used_texts = component["licence_texts"] = {}
        used_details = component["licence_details"] = {}
        existing = component.get("manifest_license")
        manifest_path = component.get("manifest")
        unattributed_manifest_licence = existing is not None and not manifest_path
        if unattributed_manifest_licence:
            add_issue("source_identity", "package licence metadata has no declaring manifest",
                      identity_status="unattributed")
            existing = None
        if manifest_path:
            manifest_path = Path(manifest_path)
            manifest_path = (manifest_path if manifest_path.is_absolute() else root / manifest_path).resolve()
            if manifest_path in untrusted_metadata:
                existing = None
        if existing is None:
            existing = component.get("licence")
        explicit = None
        if existing is not None:
            try:
                explicit = _expression(existing, component_texts)
            except (ValueError, TypeError) as error:
                add_issue("invalid_metadata", f"invalid package licence metadata {existing!r}: {error}",
                          metadata=repr(existing))
        try:
            manifest = _manifest_license(directory, component_texts, untrusted_metadata)
            if explicit and manifest and not _equivalent(explicit, manifest):
                add_issue("metadata_conflict", f"conflicting package licence declarations {explicit} and {manifest}",
                          expressions=[explicit, manifest])
            explicit = explicit or manifest
        except _MetadataConflict as error:
            add_issue("metadata_conflict", str(error), expressions=error.declarations)
        except (OSError, UnicodeError, ValueError, TypeError, SyntaxError) as error:
            add_issue("invalid_metadata", f"invalid package licence metadata: {error}")
        if explicit is None and unattributed_manifest_licence:
            explicit = "NOASSERTION"
        for source in sources:
            selected = Path(source)
            selected = (selected if selected.is_absolute() else root / selected).resolve()
            file_licence = {"source": str(source), "expression": None, "provenance": None, "trusted": False}
            file_licences.append(file_licence)
            if not selected.is_relative_to(owner_root) or not selected.is_relative_to(declaration_root):
                add_issue("out_of_root", f"source {source} is outside its owning or declaration root",
                          source=str(source), owner_root=str(owner_root), declaration_root=str(declaration_root))
                continue
            relative = selected.relative_to(declaration_root).as_posix()
            declaration_status, declaration_digest = declaration_states.get(declaration_root, (None, None))
            if declaration_status:
                add_issue("declaration_identity",
                          f"{source}: licence declaration is {declaration_status}; declaration review required",
                          source=str(source), identity_status=declaration_status,
                          declaration_sha256=declaration_digest)
            source_entries = component_entries
            if external_declarations:
                identity = _external_checkout(selected, component, root, checkout_cache, root_cache,
                                              external_declarations)
                if identity:
                    checkout, remote, revision = identity
                    external = external_declarations.get((remote, revision), [])
                    if (checkout != root and checkout.is_relative_to(declaration_root)
                            and not any(key in component_texts and component_texts[key] != value
                                        for key, value in repo_texts.items())):
                        prefix = checkout.relative_to(declaration_root).as_posix()
                        prefix = "" if prefix == "." else prefix + "/"
                        mapped = [
                            {**item, "scope": (prefix.rstrip("/") or ".") if item["scope"] == "."
                             else prefix + item["scope"], "external": True}
                            for item in external
                        ]
                        source_entries = component_entries + mapped
                        component_texts = {**component_texts, **repo_texts}
                        component_details = {**component_details, **repo_details}
            matches = [entry for entry in source_entries if _covers(entry["scope"], relative)]
            def scope_rank(entry):
                scope = entry["scope"]
                if scope == ".":
                    return 0, 0, 0
                prefix = _GLOB.split(scope, maxsplit=1)[0]
                if prefix != scope:
                    prefix = prefix.rpartition("/")[0]
                depth = len(prefix.split("/")) if prefix else 0
                return depth, len(scope.split("/")), len(scope)

            matches.sort(key=scope_rank, reverse=True)
            entry = matches[0] if matches else None
            if entry:
                for other in matches[1:]:
                    if (other["scope"] == entry["scope"] and other.get("expression")
                            and entry.get("expression")
                            and not _equivalent(other["expression"], entry["expression"])):
                        add_issue("metadata_conflict",
                                  f"{source}: conflicting reviewed declarations at {entry['scope']}",
                                  source=str(source), scope=entry["scope"],
                                  expressions=[entry["expression"], other["expression"]])
            if entry and entry["scope"] == "." and local and (
                owner_root != root or component.get("unreviewed")
            ):
                entry = None
            declared = entry.get("expression") if entry else None
            source_pin_invalid = False
            holders = [item for item in matches if "pin" in item]
            for holder in holders:
                key = (declaration_root, holder["scope"])
                if key not in pin_cache:
                    pin_cache[key] = _pin_problem(declaration_root, holder)
                for problem in pin_cache[key]:
                    source_pin_invalid = True
                    claim = f"; reviewed declaration {declared}" if declared else ""
                    add_issue("pin_mismatch", f"{source}: {problem['message']}{claim}", source=str(source),
                              scope=holder["scope"], expected=problem["expected"], actual=problem["actual"],
                              declared=declared)
            for match in matches if not source_pin_invalid else ():
                if match.get("licenseComments"):
                    note = f"{match['scope']}: {match['licenseComments']}"
                    if note not in comments:
                        comments.append(note)
                if match.get("licenseGap"):
                    gap = f"{match['scope']}: {match['licenseGap']}"
                    if gap not in comments:
                        comments.append(gap)
                    add_issue("licence_gap", f"{source}: unresolved licence conflict: {gap}",
                              source=str(source), scope=match["scope"])
            file_texts = {} if source_pin_invalid else component_texts
            source_license = None
            provenance = None
            if selected.suffix.lower() in (".py", ".c", ".h", ".cc", ".cpp", ".cxx", ".hpp", ".hxx") and selected.is_file():
                try:
                    source_license, provenance = _source_license(selected, file_texts)
                except _MetadataConflict as error:
                    add_issue("metadata_conflict", str(error), source=str(source), expressions=error.declarations)
                    continue
                except (OSError, UnicodeError, ValueError, TypeError) as error:
                    add_issue("invalid_metadata", f"{source}: invalid source licence header: {error}", source=str(source))
                    continue
                if source_license:
                    note = f"{source}: source header declares {source_license}"
                    if component.get("revision"):
                        note += f" (source revision {component['revision']})"
                    if note not in comments:
                        comments.append(note)
            external_exception = (
                entry and entry.get("external") and declared
                and any(other.get("external") and other.get("expression")
                        and other["scope"] != entry["scope"]
                        and _covers(other["scope"], entry["scope"])
                        and not _equivalent(other["expression"], declared)
                        for other in matches)
            )
            scoped_conflict = (
                provenance in ("source SPDX header", "source MIT header")
                and source_license != "NOASSERTION"
                and declared and declared != "NOASSERTION"
                and not source_pin_invalid
                and entry["scope"] != "."
                and (entry["scope"] == relative or external_exception or (
                    directory.is_relative_to(declaration_root)
                    and (scope_rank(entry) > scope_rank({"scope": directory.relative_to(declaration_root).as_posix()})
                         if entry.get("external") else
                         scope_rank(entry) >= scope_rank({"scope": directory.relative_to(declaration_root).as_posix()}))
                ))
                and not _equivalent(source_license, declared)
            )
            if scoped_conflict:
                add_issue("metadata_conflict",
                          f"{source}: {provenance} {source_license} conflicts with reviewed declaration "
                          f"{declared} at {entry['scope']}",
                          source=str(source), scope=entry["scope"], expressions=[source_license, declared])
            metadata_conflict = (
                not source_license and entry and entry.get("external") and declared
                and explicit and explicit != "NOASSERTION" and declared != "NOASSERTION"
                and not source_pin_invalid
                and (entry["scope"] == relative or external_exception or (
                    directory.is_relative_to(declaration_root)
                    and scope_rank(entry) > scope_rank({
                        "scope": directory.relative_to(declaration_root).as_posix()
                    })
                ))
                and not _equivalent(explicit, declared)
            )
            if metadata_conflict:
                add_issue("metadata_conflict",
                          f"{source}: package metadata {explicit} conflicts with reviewed declaration "
                          f"{declared} at {entry['scope']}",
                          source=str(source), scope=entry["scope"], expressions=[explicit, declared])
            expression = source_license
            if not expression and explicit and entry and entry.get("external"):
                expression = explicit
                provenance = "package metadata"
            if (not expression and explicit == "NOASSERTION" and (
                    not entry or entry["scope"] == "." or (
                        directory.is_relative_to(declaration_root)
                        and scope_rank(entry) <= scope_rank({
                            "scope": directory.relative_to(declaration_root).as_posix()
                        })
                    ))):
                expression = explicit
                provenance = "package metadata"
            if (not expression and declared and not source_pin_invalid
                    and (entry["scope"] != "." or entry.get("external"))):
                expression = declared
                provenance = f"reviewed declaration: {entry['scope']}"
            if not expression and explicit and source_pin_invalid:
                try:
                    _expression(explicit, file_texts)
                except (ValueError, TypeError) as error:
                    add_issue("invalid_metadata", f"{source}: invalid package licence metadata: {error}",
                              source=str(source))
                else:
                    expression = explicit
                    provenance = "package metadata"
            elif not expression and explicit:
                expression = explicit
                provenance = "package metadata"
            if (not expression and library_default and not declaration_status
                    and not source_pin_invalid and selected.is_relative_to(directory)):
                expression = "MIT"
                provenance = "micropython-lib builder default"
            if not expression and declared and not source_pin_invalid:
                expression = declared
                provenance = f"repository default: {entry['scope']}"
            if expression == "NOASSERTION":
                expression = None
            if selected.is_dir():
                prefix = "" if relative == "." else relative + "/"
                nested = []
                for candidate in source_entries:
                    scope = candidate["scope"]
                    if scope == "." or candidate is entry:
                        continue
                    literal = _GLOB.split(scope, maxsplit=1)[0]
                    if not (literal.startswith(prefix) or (_GLOB.search(scope) and prefix.startswith(literal))):
                        continue
                    candidate_expression = candidate.get("expression")
                    if not expression or not candidate_expression or not _equivalent(expression, candidate_expression):
                        nested.append({"scope": scope, "expression": candidate_expression})
                if nested:
                    add_issue("nested_licence_scopes",
                              f"{source}: nested reviewed declarations may have different licences",
                              source=str(source), declarations=nested)
                    file_licence["expression"] = expression
                    file_licence["provenance"] = provenance
                    continue
            file_licence["expression"] = expression
            file_licence["provenance"] = provenance
            file_licence["trusted"] = bool(expression) and not scoped_conflict and not metadata_conflict
            if not expression:
                if not declaration_status:
                    add_issue("missing_assertion", f"{source}: no asserted licence; declaration review required",
                              source=str(source))
                continue
            if not any(_equivalent(expression, value) for value in expressions):
                expressions.append(expression)
            if file_licence["trusted"]:
                for symbol in _LICENSING.license_symbols(_LICENSING.parse(expression, strict=True)):
                    if _REF.fullmatch(symbol.key):
                        used_texts[symbol.key] = file_texts[symbol.key]
                        used_details[symbol.key] = component_details[symbol.key]
        component["licence_mixed"] = len(expressions) > 1
        component["licence_declared"] = (
            expressions[0] if len(expressions) == 1 and all(item["trusted"] for item in file_licences) else None
        )
        component["licence_status"] = "unresolved" if component_issues else "mixed" if component["licence_mixed"] else "resolved"
        component["licence"] = None if component_issues else component["licence_declared"]
    return list(dict.fromkeys(issues))
