"""Headers consumed from a checkout none of whose sources are selected get a header-only boundary."""

import json
import shutil
import subprocess
from pathlib import Path

from firmware_build import (
    LINK,
    PREAMBLE,
    capture,
    components,
    gap_kinds,
    generate,
    requires_gnu_build,
    run_git,
    spdx,
)
from mpy_analysis.sbom_spdx import validate_spdx

pytestmark = requires_gnu_build

MAIN = '#include "vendor.h"\nint main(void) { return VALUE; }\n'
LIBRARY_RULE = (
    "$(BUILD)/library.o: $(ROOT)/other/library.c | $(BUILD)\n"
    "\t$(CC) $(CFLAGS) -MD -MF $(@:.o=.d) -c $< -o $@\n"
)


def _makefile(objects="$(BUILD)/main.o", rules=""):
    return PREAMBLE + rules + LINK.format(objects=objects)


def _vendor(tmp_path, *, repository=True, dirty=False, files=None):
    """A header-only directory outside the fixture repository, optionally its own commit."""
    vendor = tmp_path / "vendor"
    contents = files or {
        "include/vendor.h": "#define VALUE 5\n",
        "include/unused.h": "#define NEVER 1\n",
        "LICENSE": "Permission is hereby granted.\n",
    }
    for relative, content in contents.items():
        (vendor / relative).parent.mkdir(parents=True, exist_ok=True)
        (vendor / relative).write_text(content)
    if repository:
        subprocess.run(["git", "init", "-q", str(vendor)], check=True)
        run_git(vendor, "config", "user.email", "sbom-test@example.invalid")
        run_git(vendor, "config", "user.name", "SBOM Test")
        run_git(vendor, "add", "-A")
        run_git(vendor, "commit", "-qm", "vendor")
        if dirty:
            (vendor / "include/vendor.h").write_text("#define VALUE 5 /* edited */\n")
    return vendor


def _build(tmp_path, vendor, **options):
    return capture(
        tmp_path,
        files={"src/main.c": MAIN},
        makefile=_makefile(**options.pop("makefile", {})),
        roots={"fixture": "src", "vendor": str(vendor), **options.pop("roots", {})},
        variables=(f"CFLAGS=-I{vendor}/include",),
        **options,
    )


def _boundary_of(model, header):
    [owner] = [
        item
        for item in model.components.values()
        if "boundary" in item.properties and header.ref in model.depends_on.get(item.ref, set())
    ]
    return owner


def test_header_only_checkout_owns_the_consumed_header_and_not_the_unused_one(tmp_path):
    vendor = _vendor(tmp_path)
    built = _build(tmp_path, vendor)
    assert built.exit_code == 0 and built.receipt["status"] == "complete"
    assert built.headers()["vendor.h"]["source_bindings"] == [
        {"root": "vendor", "path": "include/vendor.h"}
    ]

    model, summary = generate(built)
    [header] = components(model, role="header", path="include/vendor.h")
    assert header.properties["root"] == "vendor"
    assert not components(model, path="include/unused.h")
    owner = _boundary_of(model, header)
    assert owner.properties["directory"] == str(vendor.resolve())
    assert owner.properties["boundary-kind"] == "repository"
    assert owner.properties["source-revision"]
    [firmware] = components(model, **{"image-role": "application"})
    assert owner.ref in model.depends_on[firmware.ref]

    assert "linked-header-not-in-selected-scope" not in gap_kinds(summary)
    assert summary["firmware_completeness"] == "established"
    image = summary["link_closure"]["images"][0]
    assert image["headers"]["status"] == "attributed" and image["headers"]["source"] == 1
    [row] = [item for item in summary["metadata"] if item["component"] == owner.ref]
    assert row["headers"] == [
        {
            "root": "vendor",
            "path": "include/vendor.h",
            "sha256": built.headers()["vendor.h"]["sha256"],
        }
    ]
    assert row["fields"]["source_content"]["status"] == "not-applicable"
    assert row["fields"]["header_content"]["status"] == "known"
    assert {gap["field"] for gap in summary["gaps"] if gap["kind"] == "dependency-metadata-unresolved"
            and gap["component"] == owner.ref} >= {"name", "version", "license"}
    validate_spdx(spdx(model))


def test_header_only_directory_that_is_no_longer_a_git_checkout_stays_a_gap(tmp_path):
    vendor = _vendor(tmp_path)
    built = _build(tmp_path, vendor)
    assert built.headers()["vendor.h"]["disposition"] == "source"
    shutil.rmtree(vendor / ".git")

    model, summary = generate(built)
    assert "linked-header-not-in-selected-scope" in gap_kinds(summary)
    assert summary["firmware_completeness"] == "not-established"
    assert not components(model, path="include/vendor.h")


def test_header_only_root_inside_another_checkout_is_bounded_at_the_root(tmp_path):
    built = capture(
        tmp_path,
        files={"src/main.c": MAIN, "vendor/include/vendor.h": "#define VALUE 5\n", "vendor/include/unused.h": "\n"},
        makefile=_makefile(),
        roots={"fixture": "src", "vendor": "vendor"},
        variables=(f"CFLAGS=-I{tmp_path / 'fixture' / 'vendor' / 'include'}",),
    )
    model, summary = generate(built)
    [header] = components(model, role="header", path="include/vendor.h")
    owner = _boundary_of(model, header)
    assert owner.properties["directory"] == str(built.roots["vendor"].resolve())
    assert owner.properties["boundary-kind"] == "unresolved-root"
    assert "linked-header-not-in-selected-scope" not in gap_kinds(summary)
    assert summary["firmware_completeness"] == "established"


def test_dirty_header_only_checkout_is_a_provenance_gap(tmp_path):
    vendor = _vendor(tmp_path, dirty=True)
    built = _build(tmp_path, vendor)
    model, summary = generate(built)
    assert "linked-header-not-in-selected-scope" not in gap_kinds(summary)
    assert any(
        gap["kind"] == "dirty-selected-repository" and gap["path"] == str(vendor.resolve())
        for gap in summary["gaps"]
    )
    assert summary["firmware_completeness"] == "not-established"
    [header] = components(model, role="header", path="include/vendor.h")
    assert _boundary_of(model, header).properties["directory"] == str(vendor.resolve())


def test_header_root_declaration_binds_by_the_consumed_headers(tmp_path):
    """A source-bound declaration of a header-only checkout applies to its consumed headers."""
    vendor = _vendor(tmp_path)
    built = _build(tmp_path, vendor)
    revision = subprocess.run(
        ["git", "-C", str(vendor), "rev-parse", "HEAD"], capture_output=True, text=True, check=True
    ).stdout.strip()
    declaration = tmp_path / "declaration.json"
    declaration.write_text(
        json.dumps(
            {
                "bomFormat": "CycloneDX",
                "specVersion": "1.6",
                "version": 1,
                "components": [
                    {
                        "type": "library",
                        "bom-ref": "vendor",
                        "name": "vendor-headers",
                        "version": "1.0",
                        "licenses": [{"expression": "MIT"}],
                        "properties": [
                            {"name": "mpy:source:root", "value": "vendor"},
                            {"name": "mpy:source:path", "value": "."},
                            {"name": "mpy:source:revision", "value": revision},
                        ],
                    }
                ],
            }
        )
    )
    model, summary = generate(built, declaration_files=[declaration])
    [header] = components(model, role="header", path="include/vendor.h")
    owner = _boundary_of(model, header)
    assert owner.name == "vendor-headers" and owner.version == "1.0"
    assert not [gap for gap in summary["gaps"] if gap["kind"] == "declaration-applicability"]
    assert summary["firmware_completeness"] == "established"


LIBRARY = {
    "other/library.c": '#include "real.h"\nint library(void) { return REAL; }\n',
    "other/real.h": "#define REAL 3\n",
    "other/spare.h": "#define SPARE 1\n",
}
LIBRARY_MAKEFILE = {"rules": LIBRARY_RULE, "objects": "$(BUILD)/main.o $(BUILD)/library.o"}


def _source_state(model, summary):
    owners = {
        (row["root"], row["path"]): model.components[row["component"]].properties.get("owner")
        for row in summary["sources"]
        if row["root"] != "vendor"
    }
    boundaries = {
        (item["root"], item["path"]): item["boundary_kind"]
        for item in summary["metadata"]
        if not item["headers"]
    }
    return owners, boundaries


def test_source_boundaries_and_their_headers_are_unchanged_by_a_header_only_root(tmp_path):
    include = f"-I{tmp_path / 'plain' / 'fixture' / 'other'}"
    plain = capture(
        tmp_path / "plain",
        files={"src/main.c": '#include "real.h"\nint main(void) { return REAL; }\n', **LIBRARY},
        makefile=_makefile(**LIBRARY_MAKEFILE),
        roots={"fixture": "src", "other": "other"},
        variables=(f"CFLAGS={include}",),
    )
    plain_model, plain_summary = generate(plain)

    vendor = _vendor(tmp_path, files={"include/vendor.h": "#define VENDOR 5\n", "include/unused.h": "\n"})
    mixed = capture(
        tmp_path / "mixed",
        files={
            "src/main.c": '#include "vendor.h"\n#include "real.h"\nint main(void) { return REAL + VENDOR; }\n',
            **LIBRARY,
        },
        makefile=_makefile(**LIBRARY_MAKEFILE),
        roots={"fixture": "src", "other": "other", "vendor": str(vendor)},
        variables=(f"CFLAGS=-I{vendor}/include -I{tmp_path / 'mixed' / 'fixture' / 'other'}",),
    )
    mixed_model, mixed_summary = generate(mixed)

    assert _source_state(mixed_model, mixed_summary) == _source_state(plain_model, plain_summary)
    [real] = components(mixed_model, role="header", path="real.h")
    assert _boundary_of(mixed_model, real).properties["directory"].endswith("/other")
    [vendor_header] = components(mixed_model, role="header", path="include/vendor.h")
    assert _boundary_of(mixed_model, vendor_header).properties["directory"] == str(vendor.resolve())
    assert not components(mixed_model, path="spare.h") and not components(mixed_model, path="include/unused.h")
    assert mixed_summary["firmware_completeness"] == "established"
