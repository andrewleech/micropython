# mpy_analysis -- static analysis and SBOM tooling for MicroPython firmware projects

This package supplies compiler-analysis checks, explicit MicroPython source inventories, scanner execution and positive/negative fixture checks for firmware projects built on MicroPython.

- `sast-compdb` proves a compilation database covers the build it came from, against the
  build's own object files.
- `sast-cppcheck-inputs` derives the predefines, `__has_*` answers, limit macros and platform
  file cppcheck needs from the build's own compiler, per compiler-flag group.
- `sast-cppcheck-coverage` and `sast-codechecker-coverage` fail a run that did not analyse every required unit/build action, unless the gap is in the project's accepted list. CodeChecker failure archives must match recorded build actions rather than masking other actions on the same source.
- `sast-fanalyzer` replays a compilation database under GCC's `-fanalyzer` with an
  analysis-only GCC 15, one SARIF per unit.
- `sast-install-tools` installs the pinned analysers (cppcheck, the Arm GNU Toolchain for
  `-fanalyzer`, CodeChecker and the clang it drives) into a prefix, in the project's own
  build image.
- `sast-scope` collects manifest-selected frozen sources and optional compilation-database inputs using explicit roots and build variables.
- `sast-run` invokes a selected external scanner with consumer-owned policy, records findings separately from operational and coverage failures, and rebases staged source locations.
- `sast-check-fixtures` checks external positive/negative expectations using the same runner and coverage receipts.
- `sast-sbom` generates an SPDX 2.3 (default) or CycloneDX 1.6 (`--format cyclonedx`) selected-source inventory and provenance/coverage receipt from the same manifest/compiler scope as SAST.
- `sast-sbom-policy` judges an `sbom-summary.json` (and optionally its firmware receipt) against a named completeness profile and exits non-zero unless the profile is satisfied.

A project installs it from its MicroPython checkout, like mpremote:

The package requires Python 3.10 or newer, matching its pinned REUSE dependency.

    python3 -m pip install --target build/analysis-tools tools/mpy_analysis

Projects call these entry points from their build systems and own their scanner policy, dependencies, suppressions and accepted coverage gaps. Scanner findings retain their rule/message content; staged source locations are rebased to physical files before SARIF or JSON is consumed by CI.

## Explicit scanner runner

`sast-run` invokes one external scanner over a schema-v1 source inventory. It does not discover a consumer checkout, select a build target, capture compiler commands, install scanner tools or import a consumer resolver. The six choices are `opengrep-stable`, `opengrep-interfile-alpha`, `semgrep-ce`, `ruff-s`, `pyrefly` and `pysa`.

```sh
python3 -m pip install tools/mpy_analysis
sast-run --scope /absolute/path/inventory.json --policy-dir /absolute/path/policy \
    --scanner opengrep-stable --output-dir build/analysis \
    --executable /absolute/path/opengrep
```

Paths in the command line are relative to the caller's working directory unless absolute. Inventory roots must be absolute existing directories. All roots are explicit, including libraries outside the consumer checkout. A record's `root` plus `path` identifies its physical file; its `target_path` identifies a frozen import and is not its report location. Multiple frozen identities for the same physical file are preserved. `owner` and `provenance` are required and retained without changing selection.

```json
{
  "schema_version": 1,
  "roots": {"repo": "/checkout/project", "library": "/checkout/library"},
  "target": "unix",
  "variant": "standard",
  "port": "unix",
  "groups": {
    "runtime_frozen_python": [
      {"root": "repo", "path": "app/main.py", "target_path": "main.py", "owner": "application", "provenance": {"manifest": "manifest.py"}},
      {"root": "library", "path": "driver.py", "target_path": "driver.py", "owner": "library", "provenance": {"revision": "selected-library-revision"}}
    ],
    "host_and_example_python": [],
    "frontend": [],
    "native": []
  }
}
```

The scope helpers can generate frozen records with MicroPython's manifest authority and native records with the shared compilation-database path rules. Consumer adapters own group selection and build metadata. Native records retain compiler directory, arguments or command, output and entry provenance. Missing or empty compiler input is an adapter prerequisite failure, not permission to scan a directory instead. Analysis-only stub dependencies are configured separately and never added to this inventory.

For a plain MicroPython Unix checkout, generate the selected manifest inventory with explicit roots and manifest variables, then pass the resulting JSON to the runner:

```sh
sast-scope --micropython-root "$MPY" \
    --manifest "$MPY/ports/unix/variants/standard/manifest.py" \
    --root "micropython=$MPY" --root "micropython-lib=$MPY_LIB" \
    --owner micropython=micropython --owner micropython-lib=micropython-lib \
    --var "PORT_DIR=$MPY/ports/unix" --var "MPY_LIB_DIR=$MPY_LIB" \
    --target unix --variant standard --port unix --output scope.json
```

In manifest mode, `sast-scope --manifest ... --compile-database DB` appends native records using the same explicit roots and owners. `--compile-database` does not provide a standalone native-only mode and is not combined with `--scope`. Python interfaces are `load_scope(path)`, `parse_scope(data)`, `collect_manifest_scope(...)` and `collect_compdb_sources(database, roots=..., owners=...)`; consumers can add their externally selected groups before serializing the validated scope. The CLI does not infer application files or port/target matrices.

The manifest collector additionally retains `packages` (physical selected-manifest identity, actual required name, explicit root/path, requested library alias, declared metadata and dependencies) and `package_requirements` (observed direct/transitive/repeated edges). Frozen `provenance.package_id` identifies the authority metadata owner; an ordinary include does not invent a new package and a transitive requirement's files remain attributed to that requirement. Physical aliases share package identity. Existing schema-v1 consumer scopes without package fields remain valid for scanners; SBOM generation explicitly reports their missing package inventory.

## Selected-source SBOM

Generate the inventory with `sast-scope`, adding `--compile-database /absolute/path/compile_commands.json` for the recorded build when native component selection is required. Pass the same inventory to both scanners and SBOM generation:

```sh
sast-sbom --scope scope.json --micropython-root "$MPY" --output-dir build/sbom
```

`$MPY` must match the inventory's actual manifest authority, including its version. The library root selected by `MPY_LIB_DIR` need not be the MicroPython checkout's gitlinked library; its actual repository revision is recorded. Explicit roots can also name separately versioned locally frozen libraries. Only `runtime_frozen_python` and compiler-evidenced `native` records enter this SBOM; other groups are reported as unassessed.

The Python API is `generate_sbom(scope, *, micropython_root, declaration_files=(), build_receipt=None, posthoc_link_inventory=None, link_generation=None)` in `mpy_analysis.sbom`, returning `(model, receipt)` where `model` is the format-neutral `mpy_analysis.sbom_model.Model`. `mpy_analysis.sbom_spdx.to_spdx` and `mpy_analysis.sbom_cyclonedx.to_cyclonedx` are independent writers over that model; neither document is derived from the other. The CLI writes a schema-validated SPDX 2.3 `sbom.spdx.json` by default, or CycloneDX 1.6 `sbom.json` with `--format cyclonedx`, plus `sbom-summary.json` (which records `document_format` and `document_file`), deterministically and without network access; a previous document of either format is removed before a run. Selected source files retain SHA-256 hashes and dependency ownership. Actual Git roots/submodules retain their origin, revision, tracked state and gitlink evidence. Git revisions identify checkout state, not release versions. Multiple compiler actions remain distinct receipt rows without duplicating their source file; frozen import aliases remain distinct identities.

### Firmware build receipts

The selected-source SBOM does not establish which compiled code reached a firmware image. G5 build receipts bind captured build/link evidence to final ELF images and add proven link-derived source edges:

```sh
sast-sbom --scope scope.json --micropython-root "$MPY" \
    --build-receipt firmware-build-receipt.json --output-dir build/sbom
```

A qualified published linker inventory can also be imported with `--link-inventory inventory.json`. This verifies exact published hashes and source ownership evidence, but the output is always labelled `posthoc-incomplete`, never a build-time receipt.

The receipt and posthoc inventory schemas, API, evidence limits and adapter status are documented in [FIRMWARE-RECEIPT.md](FIRMWARE-RECEIPT.md). Neither source selection nor posthoc inventories establish complete firmware coverage.

Dependency discovery starts from selected records and walks their ancestors within explicit physical roots. Authoritative require ownership takes priority; other manifests are candidates, not permission to execute new metadata code. Package/native markers and current source-bound declarations can establish dependency boundaries. A checkout, root directory or licence filename alone does not establish a distribution identity. Unmarked vendored units cannot be found reliably, their discovery limit remains explicit. There is no table of recognised libraries or package-specific version macro rules.

| Format | Static metadata support |
|---|---|
| Selected manifest / `metadata.txt` | Declared fields and authoritative require edges, including external micropython-lib roots. Logical require names are fallback identities, not replacements for declared distribution names. |
| `pyproject.toml` | Static `[project]` fields, dependencies, SPDX expressions and licence-file references. Dynamic backend fields remain unresolved. |
| `setup.cfg` / `setup.py` | Declarative literals or an unambiguous, unshadowed setuptools/distutils setup call with literal keywords. No imports or setup execution. |
| Core Metadata | `PKG-INFO`, `METADATA`, immediate `*.egg-info` / `*.dist-info` metadata, including licence-file references and declared dependencies, with current source bindings. Host-installed or unbound metadata does not establish selected-source facts. |
| SPDX / REUSE | Selected-file headers, `.license` sidecars, `REUSE.toml`, supported dep5 annotations and retained custom `LicenseRef` texts, with scoped applicability. Bare LICENSE/COPYING/NOTICE text is retained, not guessed into an SPDX identifier. |
| CMake / Meson | Bounded literal project declarations. Variables, control flow, overridden project commands and computed values remain unsupported; the tooling never runs the build language. |
| Upstream BOM | Offline schema validation for CycloneDX 1.6 / SPDX2.3 JSON and current source-bound component association. Other JSON BOM formats/versions are reported as unsupported, malformed JSON or invalid recognised schemas fail the operation. |

Declared metadata dependencies are retained separately from observed selected require edges, a declared dependency does not prove firmware inclusion. The generic design takes lessons from [Matt Trentini's MicroPython SBOM work](https://github.com/mattytrentini/micropython/blob/efbf88cd35b778ea4d56dedb020bf310fe5c9b88/tools/sbom.py); its original adaptation attribution remains in the licence and commit history. Vendored validation schemas have separate [source/licence notices](mpy_analysis/data/schema-sources.md).

### Maintained dependency declarations

Applications can supply a flat, schema-valid CycloneDX 1.6 JSON document through repeatable `--dependency-metadata dependencies.cdx.json`. This is a standard carrier with a tooling-specific `mpy:source:*` property profile, not an existing cross-tool binding standard. The application owns these facts and their refresh policy, the shared tooling owns validation and merging.

Each component supplies `mpy:source:root` (an exact scope root name), `mpy:source:path` (a contained relative directory, or `.`), and exactly one of `mpy:source:revision` or `mpy:source:sha256`. Revision binding requires the actual Git HEAD and tracked selected bytes matching HEAD. Content binding uses all selected frozen/native physical descendants of the directory: deduplicate relative paths, pair each with its content SHA-256, sort the pairs, JSON-encode with `separators=(",", ":")`, then SHA-256 that UTF-8 payload. `mpy_analysis.declarations.selected_digest(scope, directory)` returns `(digest, selected_row_count)` using this definition. Repeated compiler actions preserve row counts but do not change the digest. Licence applicability still covers only files actually assigned to that dependency, not every nested unit in the binding footprint.

Source-bound Core Metadata uses `X-Mpy-Source-Root`, `X-Mpy-Source-Path` and one of `X-Mpy-Source-Revision` / `X-Mpy-Source-Sha256`. Upstream CycloneDX components use the same properties; SPDX2.3 packages carry their binding JSON in a package annotation. Upstream metadata can use root/path `.` / `.` for a portable boundary-local binding. Explicit application declarations must use the consumer's actual root name.

Standard name/version/licences/copyright/description and explicit supplier/publisher/URLs/purl/CPE remain assertions, not inferred identities. A custom licence needs a named `LicenseRef` and nonempty retained text; supported base64 text is validated and decoded as UTF-8. Unusable assertions are reported, not promoted to known facts. Compatible assertions combine; contrary assertions retain both evidence sets and remain unresolved.

An intentional correction uses `mpy:metadata:corrections`, whose value is a JSON object keyed by field (including `license_text:LicenseRef-Name`). Each entry has a nonempty `reason` and `supersedes` containing the exact current contrary `{path, sha256, value}` evidence. Changed or retired evidence makes the correction stale and requires refresh/removal, it does not silently keep an override. Invalid correction syntax fails the operation.

### Evidence and operation status

The receipt has separate `source_accounting`, `boundary_resolution`, `metadata_completeness` and `firmware_completeness` results. Each dependency's field matrix records `known`, `missing`, `unsupported`, `conflicting`, `stale` or `not-applicable`, with parser/path/hash/assertion evidence and issues. Required selected-dependency facts are name, release version, licence and licence applicability. Optional descriptive fields remain visible without invented values. Required gaps, stale declarations/corrections and conflicts prevent complete metadata, even when all source rows are accounted for.

Tracked source state and content are separate evidence. Generated, ignored or untracked inputs retain hashes with provenance gaps; dirty repositories retain their base revision rather than claiming those bytes are committed. `coverage_status` remains incomplete when gaps or unassessed groups exist. Compilation records do not prove link/header closure, fetched SDK inclusion or runtime-loaded dependencies, `firmware_completeness` remains `not-established`. Use `sast-compdb` separately to qualify the build capture.

The CLI returns 0 for valid generated output, including an explicitly partial SBOM, and 2 for invalid inputs, schema failures or I/O/operational failures. Consumer CI owns completeness acceptance. Before replacing owned output names it protects selected sources, manifests, declarations, metadata references and licensing inputs, including physical aliases/hardlinks. Output symlinks and input collisions fail without deleting the input; safe stale outputs are invalidated even on early scope/profile failure. Use a separate artifact directory, stored scopes describe recorded selections rather than a fresh build.

If metadata-input protection cannot be completed (for example, a malformed upstream BOM stops discovery before licence references), the CLI fails before any output mutation. Previously present files are not fresh results, consumers must not accept output after exit 2.

## Scanner source selection

Pattern scanners submit `runtime_frozen_python`, `host_and_example_python` and `frontend` to the `project` policy, and C/C++ translation-unit suffixes in `native` to `native-c`. Other native records, such as headers, remain explicitly unassessed. Ruff submits Python records in both Python groups. Pyrefly and Pysa submit only `runtime_frozen_python`. Other groups are retained in the inventory and reported as unassessed, not silently discarded. Empty scanner selection fails.

Interfile staging also retains selected companion files in the supported groups, including native headers, without counting companions as directly assessed translation units.

The policy directory owns `scanners.json` (`project` and `native-c` lists of rule paths or scanner registry URLs), `ruff.toml`, `typing.json` and application `pysa/` inputs. Relative policy paths resolve from that directory. No application rules are bundled. For Pysa, `--executable` overrides `pyre`; `--pyrefly-executable` independently overrides its prerequisite.

Typing policy is `{"environments": [...]}`. Each object names `port`, `target`, `variant`, `directory`, `typeshed`, `python_version`, `python_platform`, `pyrefly_version`, `target_package`, `packages` and `provenance` including `firmware_version` and `source_revision`. Selection matches the exact `(port, target, variant)` tuple; duplicate tuples and absent selections fail. Multiple variants may share a real port without replacing its identity with a target key. Paths are relative to the policy directory; package pins and the selected target distribution are validated against installed metadata. A custom typeshed is mandatory; module stubs alone do not provide builtin coverage. **No Pyrefly release currently has an approved target-aware environment in this package. Pyrefly and Pysa therefore stop at the prerequisite, return `1` with `status: operation_failed`, and retain available prerequisite/version receipts instead of running normal analysis.** Pyrefly 1.3.2 is blocked for known bundled-typeshed fallback and bootstrap defects; other releases are blocked as unverified, not accepted by changing a pin. Typing coverage remains `incomplete`. No tool or stub mutation bypasses these blockers.

The typing object can explicitly opt into `common_models` families `input`, `eval_exec`, `socket` and `execfile`. `input` provides the `UserControlled` interactive input source and requires `MICROPY_PY_BUILTINS_INPUT`; `eval_exec` provides the `RemoteCodeExecution` eval/exec sinks and requires `MICROPY_PY_BUILTINS_EVAL_EXEC`. Socket payload sources and optional file-execution sinks remain independently selectable. Each receipt records source/signature provenance, required capabilities, provided kinds and `model_files` entries with the selected `models.pysa` byte SHA256. Typing receipts consume this common provenance authority. Select `execfile` only when that firmware enables it; no family selection or hash proves target API fidelity, evaluates firmware capabilities or overrides a compatibility blocker.

`mpy_analysis.common_models.resolve_model_policy(policy_dir, families)` returns `model_paths`, `config_paths` and `common_model_provenance`. Paths are real `Path` objects: an existing application `policy_dir/pysa` directory comes first, followed by requested family directories in selection order. Config paths are recursive `*.config` files, sorted within each model directory and deduplicated by real path. At least one config and one `.pysa` must exist across all inputs; an application config with only shared model files is valid. No family is selected implicitly. Unknown names (including `builtins`), duplicate selections, invalid checked config structures and selected-kind mismatches raise `ValueError`; missing policy/config/model resources raise `FileNotFoundError`.

Declarations combine across all discovered configs. Each selected provided source/sink kind must be declared on its matching side and occur on that side in at least one ordinary `rules` entry. App-owned kinds, model extensions, filters and unrelated engine settings remain engine-owned. `string_combine_rules` and `combined_source_rules` are preserved when ordinary participation suffices; if selected-kind validation needs these forms, the resolver fails naming the unsupported form. This check does not establish effective rule pairings, filter effects, source reachability, model signature validity or target completeness. Execution-only composition can use an explicit application source; its useful flows require actual engine qualification. Pysa runner composition occurs only after the unchanged typing compatibility gate and Pyrefly export; the production branch is not accepted without supported real-runner and missing-import admission evidence.

Interfile and typing runs stage only selected sources. Frozen Python uses its import identity; other interfile inputs use `_roots/<root>/<path>`. Traversal, file/directory collisions and different physical files claiming the same destination fail rather than overwrite. SARIF source locations become actual source `file:` URIs, including external roots; Pysa JSON path fields become actual source paths. Rebasing matches exact source locations only, never message text, snippets or arbitrary string suffixes. Partial valid JSON reports are rebased even when the tool exits operationally unsuccessfully.

### Completeness policy

`sast-sbom` exits 0 for any valid partial result and records what it could not account for in `sbom-summary.json`. `sast-sbom-policy` (`python -m mpy_analysis.sbom_policy`) is the separate gate for CI. It reads only that summary, never the SBOM document, and does not regenerate anything:

```sh
sast-sbom-policy --summary build/sbom/sbom-summary.json \
    --receipt firmware-build-receipt.json --profile firmware-complete
```

A JSON result is always printed on standard output. Each dimension carries `required`, `status` (`satisfied`, `unsatisfied`, or `not-applicable` when the profile does not require it), `observed` (what the summary shows regardless of the profile) and numeric `evidence`: gaps by kind and field, per-image link closure with unmapped bindings and unresolved inputs, selected sources accounted against the total, and required metadata fields known against the total.

| Profile | Requires |
|---|---|
| `firmware-complete` | `firmware_completeness` established, link closure complete, `source_accounting` accounted, no gap of any kind, `metadata_completeness` complete |
| `firmware-linked` | `firmware_completeness` established and link closure complete; metadata and other gaps are reported but do not fail |

Exit status is 0 when the profile is satisfied, 1 when a requirement is not, and 2 for input that cannot be judged: an unreadable file, a `schema_version` the tool does not know, an unknown profile, a summary that contradicts itself (for example `established` with an incomplete link closure or a blocking gap), or a receipt that fails `mpy-firmware-build/v1` validation, is stale on disk, or does not match the summary's receipt id, build id, target, variant, port and image hashes. With `--receipt`, the receipt is validated with the same checks `sast-sbom` applies, including current artifact hashes, so the gate must run where the build outputs still exist. Profiles and dimensions are data in `mpy_analysis/sbom_policy.py`.

### Results and failure contract

The runner exits `0` for completed report-only findings or diagnostics, and `1` for an operational failure such as missing executable, scope, policy, prerequisite or fresh valid report. Actual scanner exit codes remain in `analysis-summary.json` and its `invocations`; exit `1` from the supported scanners denotes findings/diagnostics, while other tool exits fail the operation. SARIF results can indicate findings even with tool exit `0`. `status` is `clean`, `findings`, `diagnostics` or `operation_failed`, independently of `coverage_status` (`assessed`, `not_assessed` or `incomplete`). Pattern-scanner coverage requires a fresh structured JSON sidecar whose `paths.scanned` accounts for every submitted file; skipped files, analysis errors and SARIF execution warnings/failures leave the invocation unassessed. Ruff uses its explicit-file invocation contract plus reported execution gaps. `assessed` is not a proof of security or rule completeness.

Ruff `invalid-syntax` results remain visible and mark every scope identity sharing the rebased physical source as unassessed; valid files in the same invocation remain assessed. An unmappable or missing syntax-location URI invalidates coverage for the whole invocation, as do existing execution-notification gaps and report rebasing failures. `findings_count` counts all SARIF results, including parser diagnostics, rather than only security findings. These records do not establish global parser coverage for engines that emit no corresponding diagnostics.

`analysis-scope.json` retains the canonical input inventory. Summary schema version 1 contains target/variant/port, report-only mode, actual scanner exit, invocation command/cwd/report/exit, report-validity flags, source counts and owners, finding count, and per-group selected/submitted counts with unassessed identities. Invocation `coverage_evidence` records pattern-scanner scanned/missing/skipped paths and errors; `coverage_gaps` records structured and SARIF execution gaps. Successful earlier invocations survive a later failure. `analysis-prerequisites.json` records typing dependencies, compatibility gaps and version-check output when applicable. Tool stdout/stderr are retained in named logs. A partial report rebasing error is recorded without replacing the original tool exit.

Each run removes only its owned names: the three `analysis-*.json` reports, `ruff-s.sarif`/`.log`, `pyrefly.sarif`/`.log`, `pysa.log`, `pysa-results/`, and `<pattern-scanner>-project`/`-native-c` `.sarif`/`.json`/`.log` files. Unrelated artifacts are neither removed nor counted as findings. Owned symlinks are unlinked, not followed. A scope, policy or selected source that conflicts with owned output is rejected before removal; such destructive input/output collisions cannot safely produce a summary at that destination.

### Shared fixture checks

`sast-check-fixtures` uses `sast-run` rather than a second scanner-dispatch implementation. It accepts all six scanner choices and the same explicit scope, policy and executable paths, plus external `--expectations` and required `--output-dir`:

```sh
sast-check-fixtures --scanner opengrep-interfile-alpha --scope fixture-scope.json \
    --expectations fixture-expectations.json --policy-dir policy \
    --output-dir build/fixture-results --executable /absolute/path/opengrep
```

Source fixtures are packaged under the directory returned by `mpy_analysis.fixtures.fixture_directory()`: pickle/JSON deserialization, unsafe/safe shell invocation, invalid/valid target typing, direct input-to-eval flow, and imported positive/negative flows with `imported_companion.py`. Consumers select fixtures and import identities in an explicit scope and provide rule IDs matching their external policy. Import companions must be included as selected scope records, not discovered from a consumer layout. The helper returns sources only; it does not supply rules, target stubs or an approval baseline.

Expectations schema version 1 requires nonempty `positive` and `negative` arrays. Each record names a selected `root` and `path`, optionally an exact `rule` string. A positive requires a matching finding; a negative forbids any matching finding, or only that rule when specified. Pysa records may also select an exact `callable`, matched against the structured error's `define`; distinct positive/negative callables can occupy the same file. Overlapping selectors are invalid. Matching uses exact rebased physical source locations, not filename substrings or text appearing elsewhere in a report.

```json
{
  "schema_version": 1,
  "positive": [{"root": "fixtures", "path": "imported_positive.py", "rule": "configured-interfile-rule"}],
  "negative": [{"root": "fixtures", "path": "imported_negative.py"}]
}
```

The checker writes its own `fixture-summary.json` with matched findings, failures, coverage status and the runner summary path. It returns `0` only when expectations match and coverage is assessed. Missing positives, unexpected negatives, operational failure, unassessed coverage and blocked/incomplete typing return `1`; a known typing compatibility blocker is labeled `blocked`, not passed. The checker rejects conflicting inputs before replacing owned artifacts and preserves unrelated output.

### Verification

Run the package regressions:

```sh
python3 -m pytest tools/mpy_analysis/tests
```

Runner behavioral tests use controlled executable responses to cover report-only findings, operational failures, missing/partial reports, exact rebasing, explicit external roots, duplicate import identities, staging collisions/escapes, stale cleanup, unknown-group accounting and fail-closed typing receipts. They do not replace a real installed-scanner smoke. For that smoke, supply a manifest-selected positive fixture with an imported companion, a matching negative fixture, explicit policy and executable paths; check source accounting, actual reports and rebased source URIs for each selected scanner. Run missing-executable/report/prerequisite cases separately. Type/Pysa acceptance remains blocked until the external builtin environment and supported tool combination have been verified.

#### Target runtime inventory

Run the inventory fixture with the actual target-matched MicroPython executable, not CPython. From the MicroPython checkout, with `MICROPYTHON_EXECUTABLE` set to the built Unix executable for the recorded target/variant/revision:

```sh
"$MICROPYTHON_EXECUTABLE" tools/mpy_analysis/tests/fixtures/typing/runtime_inventory.py \
    > runtime-inventory.json
```

The receipt reports runtime identity and API presence, including builtin methods, `micropython.const` and `execfile`. It is evidence for that executable only: a stub's declared port, a scope labeled Windows or a Python-platform string does not prove a corresponding executable/build. API presence is not signature or taint-model verification; compare this inventory with the selected type environment and record remaining gaps.

The adjacent `valid_builtins.py` and `invalid_builtins.py` probes cover compiler constants, Viper pointer stores, timing arguments, valid core operations and unavailable core methods. `execfile_enabled.py` is selected only for firmware that enables that optional API. Their target-aware type checks remain blocked by the unsupported prerequisite; bundled CPython builtins cannot substitute for target fidelity.

#### Standalone common-model declaration check

The following is a **host-only model/fixture check**, independent of Picolet. It checks common builtin/socket declaration grammar, signature binding and positive/negative flows using Pyrefly's bundled CPython builtin scaffold with explicitly installed MicroPython socket-module declarations in an isolated workspace. It does not invoke the production runner, approve target-aware typing or permit production CPython fallback. Do not disable Pysa model verification.

The shared `tests/fixtures/common_models/expectations.json` uses root `fixture`, four positive callable/rule selectors and three negative selectors for `flows.py` and its imported `transport.py`. The rule is `"3101"` from the fixture's `taint.config`, not application security policy. `execfile-expectations.json` supplies the separate optional complete set, five positives and four negatives, requiring `file_flows.py` and verified execfile capability. No general-purpose execution sanitizer is modeled; `literal_expression` has fixed source-inferred outputs rather than a blanket sanitizer declaration.

Run from the MicroPython checkout after installing this package, `pyrefly` and `pyre`. Set `MPY_MODEL_STUBS` to the absolute installed module-stub directory containing `socket.pyi`:

```sh
: "${MPY_MODEL_STUBS:?Set MPY_MODEL_STUBS to the installed target module-stub directory}"
export MPY_MODEL_STUBS
export MPY_MODEL_WORK="$(mktemp -d -t mpy-model-fixture-XXXXXX)"
export MPY_MODEL_CHECKOUT="$PWD"
python3 - <<'PY'
import json
import os
from pathlib import Path
import shutil

work = Path(os.environ["MPY_MODEL_WORK"])
stubs = Path(os.environ["MPY_MODEL_STUBS"]).resolve()
if not (stubs / "socket.pyi").is_file():
    raise SystemExit("Selected module-stub directory does not contain socket.pyi")
fixtures = Path(os.environ["MPY_MODEL_CHECKOUT"]) / "tools/mpy_analysis/tests/fixtures/common_models"
(work / "src").mkdir()
(work / "policy/pysa").mkdir(parents=True)
for name in ("flows.py", "transport.py"):
    shutil.copy2(fixtures / name, work / "src" / name)
shutil.copy2(fixtures / "taint.config", work / "policy/pysa/taint.config")
config = {
    "project-includes": [str(work / "src/**/*.py")],
    "search-path": [str(work / "src"), str(stubs)],
    "skip-interpreter-query": True,
    "site-package-path": [],
    "disable-search-path-heuristics": True,
    "python-version": "3.9",
    "python-platform": "linux",
}
(work / "pyrefly.toml").write_text("".join(f"{key} = {json.dumps(value)}\n" for key, value in config.items()))
(work / ".pyre_configuration").write_text(json.dumps({
    "source_directories": [str(work / "src")],
    "search_path": [str(work / "src"), str(stubs)],
    "workers": 1,
}))
print(work)
PY
pyrefly check --config "$MPY_MODEL_WORK/pyrefly.toml" \
    --report-pysa "$MPY_MODEL_WORK/pyrefly-pysa.json" --report-pysa-format json \
    --output "sarif:$MPY_MODEL_WORK/pyrefly.sarif"
python3 - <<'PY'
import os
from pathlib import Path
import subprocess
from mpy_analysis.common_models import resolve_model_policy

work = Path(os.environ["MPY_MODEL_WORK"])
command = ["pyre", "--noninteractive", "--dot-pyre-directory", str(work / ".pyre"), "analyze"]
for path in resolve_model_policy(work / "policy", ["input", "eval_exec", "socket"])["model_paths"]:
    command.extend(["--taint-models-path", str(path)])
command.extend(["--save-results-to", str(work / "results"), "--output-format", "json",
                "--pyrefly-results", str(work / "pyrefly-pysa.json")])
raise SystemExit(subprocess.run(command, cwd=work).returncode)
PY
python3 - <<'PY'
from collections import Counter
import json
import os
from pathlib import Path

work = Path(os.environ["MPY_MODEL_WORK"])
fixtures = Path(os.environ["MPY_MODEL_CHECKOUT"]) / "tools/mpy_analysis/tests/fixtures/common_models"
expectations = json.loads((fixtures / "expectations.json").read_text())
issues = json.loads((work / "results/errors.json").read_text())
observed = Counter((issue["define"], str(issue["code"])) for issue in issues)
expected = Counter((item["callable"], item["rule"]) for item in expectations["positive"])
forbidden = {(item["callable"], item["rule"]) for item in expectations["negative"]}
if observed != expected or set(observed) & forbidden:
    raise SystemExit(f"Fixture findings differ: observed={sorted(observed)}, expected={sorted(expected)}")
print(f"Matched {len(issues)} fixture issues; {len(forbidden)} negative selectors absent")
PY
```

Retain the printed workspace path, tool versions, actual exit codes and model-validation output with the findings receipt. The optional execfile family requires an environment defining that callable, which CPython builtins does not provide. A copied reference typeshed augmented with a synthetic execfile declaration can test optional model binding only inside a separate fixture workspace; it is not selected-firmware acceptance and must not be installed as the production target typeshed. `sast-check-fixtures` remains blocked/incomplete for typing/model fixtures until the production prerequisite is supported.
