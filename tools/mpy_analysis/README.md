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

Pattern scanners submit `runtime_frozen_python`, `host_and_example_python` and `frontend` to the `project` policy, and C/C++ translation-unit suffixes in `native` to `native-c`. Other native records, such as headers, remain explicitly unassessed. Ruff submits Python records in both Python groups. Pyrefly and Pysa submit only `runtime_frozen_python`. Other groups are retained in the inventory and reported as unassessed, not silently discarded. Empty scanner selection fails.

Interfile staging also retains selected companion files in the supported groups, including native headers, without counting companions as directly assessed translation units.

The policy directory owns `scanners.json` (`project` and `native-c` lists of rule paths or scanner registry URLs), `ruff.toml`, `typing.json` and `pysa/` models plus `taint.config`. Relative policy paths resolve from that directory. No application rules are bundled. For Pysa, `--executable` overrides `pyre`; `--pyrefly-executable` independently overrides its prerequisite.

Typing policy uses per-port objects with `directory`, `typeshed`, `target`, `variant`, `python_version`, `python_platform`, `pyrefly_version`, `target_package`, `packages` and `provenance` including `firmware_version` and `source_revision`. Paths are relative to the policy directory; package pins and the selected target distribution are validated against installed metadata. A custom typeshed is mandatory; module stubs alone do not provide builtin coverage. **No Pyrefly release currently has an approved target-aware environment in this package. Pyrefly and Pysa therefore stop at the prerequisite, return `1` with `status: operation_failed`, and retain available prerequisite/version receipts instead of running normal analysis.** Pyrefly 1.3.2 is blocked for known bundled-typeshed fallback and bootstrap defects; other releases are blocked as unverified, not accepted by changing a pin. Typing coverage remains `incomplete`. No tool or stub mutation bypasses these blockers.

The typing object can explicitly opt into `common_models` families `builtins`, `socket` and `execfile`. These declarations and their provenance are packaged with the tool; the consumer still owns `pysa/taint.config`, application extensions and acceptance policy. The runner passes selected common model directories alongside the external model directory and records their provenance. Select `execfile` only when that firmware enables it; no family selection proves builtin API fidelity or overrides a compatibility blocker.

Interfile and typing runs stage only selected sources. Frozen Python uses its import identity; other interfile inputs use `_roots/<root>/<path>`. Traversal, file/directory collisions and different physical files claiming the same destination fail rather than overwrite. SARIF source locations become actual source `file:` URIs, including external roots; Pysa JSON path fields become actual source paths. Rebasing matches exact source locations only, never message text, snippets or arbitrary string suffixes. Partial valid JSON reports are rebased even when the tool exits operationally unsuccessfully.

### Results and failure contract

The runner exits `0` for completed report-only findings or diagnostics, and `1` for an operational failure such as missing executable, scope, policy, prerequisite or fresh valid report. Actual scanner exit codes remain in `analysis-summary.json` and its `invocations`; exit `1` from the supported scanners denotes findings/diagnostics, while other tool exits fail the operation. SARIF results can indicate findings even with tool exit `0`. `status` is `clean`, `findings`, `diagnostics` or `operation_failed`, independently of `coverage_status` (`assessed`, `not_assessed` or `incomplete`). Pattern-scanner coverage requires a fresh structured JSON sidecar whose `paths.scanned` accounts for every submitted file; skipped files, analysis errors and SARIF execution warnings/failures leave the invocation unassessed. Ruff uses its explicit-file invocation contract plus reported execution gaps. `assessed` is not a proof of security or rule completeness.

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
(work / "policy").mkdir()
for name in ("flows.py", "transport.py"):
    shutil.copy2(fixtures / name, work / "src" / name)
shutil.copy2(fixtures / "taint.config", work / "policy/taint.config")
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
from mpy_analysis.common_models import common_model_paths

work = Path(os.environ["MPY_MODEL_WORK"])
command = ["pyre", "--noninteractive", "--dot-pyre-directory", str(work / ".pyre"), "analyze"]
for path in [work / "policy", *common_model_paths(["builtins", "socket"])]:
    command.extend(["--taint-models-path", str(path)])
command.extend(["--save-results-to", str(work / "results"), "--output-format", "json",
                "--pyrefly-results", str(work / "pyrefly-pysa.json")])
raise SystemExit(subprocess.run(command, cwd=work).returncode)
PY
python3 - <<'PY'
import json
import os
from pathlib import Path

work = Path(os.environ["MPY_MODEL_WORK"])
fixtures = Path(os.environ["MPY_MODEL_CHECKOUT"]) / "tools/mpy_analysis/tests/fixtures/common_models"
expectations = json.loads((fixtures / "expectations.json").read_text())
issues = json.loads((work / "results/errors.json").read_text())
observed = {(issue["define"], str(issue["code"])) for issue in issues}
expected = {(item["callable"], item["rule"]) for item in expectations["positive"]}
forbidden = {(item["callable"], item["rule"]) for item in expectations["negative"]}
if observed != expected or observed & forbidden:
    raise SystemExit(f"Fixture findings differ: observed={sorted(observed)}, expected={sorted(expected)}")
print(f"Matched {len(issues)} fixture issues; {len(forbidden)} negative selectors absent")
PY
```

Retain the printed workspace path, tool versions, actual exit codes and model-validation output with the findings receipt. The optional execfile family requires an environment defining that callable, which CPython builtins does not provide. A copied reference typeshed augmented with a synthetic execfile declaration can test optional model binding only inside a separate fixture workspace; it is not selected-firmware acceptance and must not be installed as the production target typeshed. `sast-check-fixtures` remains blocked/incomplete for typing/model fixtures until the production prerequisite is supported.
