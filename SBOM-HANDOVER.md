# SBOM candidate development handover

## Current state

The development branch is `feature/sbom-candidate` in Andrew Leech's MicroPython fork:

- SSH clone URL: `git@github.com:andrewleech/micropython.git`
- Branch: `feature/sbom-candidate`
- Last code commit: `335b55977ba117c57b5e881a0ad2c2dbfb2795fa` (`Conclude licences for mixed selected sources`)
- The feature branch was pushed to the fork. No PR has been created.

The handover is included at the branch root by a documentation commit on top of the last code commit. After the authorised push, fresh clones can read it. The comparison plan, tickets, adapters and capture reports are in a separate local SAST workspace; that workspace has no configured remote, and those files are not available from this MicroPython branch. Ask the coordinating user to provide them before attempting to reproduce the comparison.

This is a build-input SBOM implementation and an evidence-based comparison with `feature/sbom-minimal`, not an approved replacement or a licence-compliance determination. Q6 (cutover) and Q7 (single-image input selection) are still open. The package-level conjunction rule described below is implemented but has not been recorded as user-approved.

## Getting started on another host

Clone the fork branch, not `micropython/micropython`'s upstream branch:

```sh
git clone --branch feature/sbom-candidate --single-branch \
  https://github.com/andrewleech/micropython.git micropython-sbom-candidate
cd micropython-sbom-candidate
git rev-parse HEAD
git submodule update --init --recursive
```

The expected code starting revision is `335b55977ba117c57b5e881a0ad2c2dbfb2795fa`; the handover documentation commit is on top. Check `git log -1 -- tools tests` to confirm the latest code revision. If it differs, read the branch log and repository README before using the findings in this handover. Do not force-push, rebase published history, or push to `upstream`.

The reference producer commit `325d9c688edf478254f75e1a0aad0c4afc4e6bd6` on `feature/sbom-minimal` is not advertised by the fork's remote. A clone of this branch alone cannot reproduce a candidate/reference pair. Do not publish the reference branch without explicit user approval; ask for its source archive or a remote/ref to be provided.

Install the SBOM writer and licence-expression dependencies in an isolated environment:

```sh
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -r tools/sbom-requirements.txt
python -m pip install 'cyclonedx-python-lib[json-validation]==11.12.0'
```

Generate a repository inventory (this does not describe a particular firmware build):

```sh
python tools/sbom.py --repo . --offline -o repository.spdx.json
```

For build-input mode, provide an existing compilation database for one firmware image, a distinct image name, and the resolved frozen-input sidecar if the build has frozen Python modules:

```sh
python tools/sbom.py --repo . --offline \
  --compdb /path/to/compile_commands.json --image unix-standard \
  --exclude mpy-cross --frozen /path/to/frozen-inputs.json \
  -o unix-standard.spdx.json
```

Use `--format cyclonedx` for CycloneDX JSON. Repeat `--compdb`, `--exclude`, and `--root NAME=PATH` as needed. Do not mix application and bootloader databases unless they have been separated into distinct invocations. Exclude host-tool commands, including `mpy-cross`, where present. See `tools/README.md` for ESP-IDF grouping, package identity, external roots, versions and CLI details. The caller is responsible for freshness and for selecting one image; a compilation database is not proof of linked or runtime presence.

To capture resolved frozen inputs during an ordinary build, set `MICROPY_FROZEN_OUTPUT` to an absolute JSON path (or the corresponding CMake variable), then pass that output using `--frozen`. This uses the existing manifest evaluator; do not substitute a manifest source file for the resolved sidecar.

Run the candidate tests from the checkout:

```sh
python -m unittest discover -s tests/tools -p 'test_sbom_candidate*.py' -q
```

The latest recorded run at `335b559` passed 169 tests. Focused lint also passed with:

```sh
ruff check --select F821,F823,F841 tools/sbom*.py tests/tools/test_sbom_candidate*.py
```

Ruff is not listed in `tools/sbom-requirements.txt`; install or activate it separately if needed. The CycloneDX library can warn that the dependency graph is incomplete. The output deliberately does not invent runtime dependency edges from source selection. The final PYBD CycloneDX 1.6 output passed schema validation with the `json-validation` extra installed.

## What the branch implements

The candidate extends Matty Trentini's SBOM baseline from PR #43 (`efbf88cd35b778ea4d56dedb020bf310fe5c9b88`) in `mattytrentini/micropython` with a shared model and SPDX 2.3 / CycloneDX writers. It uses existing build outputs rather than adding a compiler-capture system, cross-port build driver, linker analyser, or mandatory ScanCode run.

Build-input selection combines:

- caller-supplied compilation databases and explicit image identity;
- existing dependency files for selected commands, to include `.c` prerequisites included by another translation unit;
- the resolved frozen-input JSON emitted by the existing manifest evaluator;
- explicit roots and exclusions, with source ownership and Git identity checks;
- available source-derived versions and PURLs, with Git revisions recorded separately from releases;
- selected CYW43 firmware data headers as a separate `cyw43-firmware` component, without inheriting the driver licence.

Licence resolution is per selected file. The precedence is source SPDX header, explicit package metadata or a reviewed path declaration, the micropython-lib package-builder MIT default only inside that library, and then an applicable repository default. External reviewed declarations are bound to repository URL and commit, and scoped by path. Dirty, untracked, mismatched or unverifiable source identity prevents use of the reviewed declaration. Conflicts remain issues; the computed package result does not resolve conflicting evidence.

When every selected file has trusted licence evidence and the component has no recorded issues, the candidate computes the conjunction of those selected-file expressions. It keeps any `OR` choices intact instead of selecting an alternative. SPDX stores this as `licenseConcluded`, not `licenseDeclared`, with a comment explaining the selected-source scope. CycloneDX emits a concluded expression when no declared expression exists. If evidence is unresolved or untrusted, no conclusion is emitted. This is only a conclusion for the selected source set, not for every file in the upstream dependency or every file shipped in a product.

Curated reviewed data now covers selected paths in pinned lwIP, NimBLE, STM32F7 and libm-dbl trees, along with other project/vendor declarations. Examples from the clean PYBD run:

- lwIP: a 12-term conjunction of the distinct trusted expressions from selected core and PPP sources, including the PPP `LicenseRef` terms;
- NimBLE: `Apache-2.0 AND BSD-2-Clause AND BSD-3-Clause`, including TinyCrypt ECC terms and the BSD-derived `os_mbuf.c` notice;
- STM32F7 selected HAL sources: `BSD-3-Clause`;
- CYW43 driver: `LicenseRef-CYW43-NonCommercial`, with both upstream licence links retained; the separate CYW43 firmware blob remains `NOASSERTION`;
- libm-dbl scopes remain separate: selected MIT files, SunPro files, the three Sun Microsystems 2004 notice files, and three README-excluded files with no asserted licence. Do not apply the root MIT result to every child file.

Nonstandard SPDX `LicenseRef` entries need `extractedText` under SPDX 2.3. The curated declarations keep short extracted notices and link to immutable source files where available; these snippets do not necessarily reproduce complete licence terms. The ST Liberty referenced agreement is not in-tree, so the notice is not the full agreement.

## Commit history on the feature branch

The code history on the feature branch, newest first:

| Commit | Subject |
|---|---|
| `335b559` | Conclude licences for mixed selected sources |
| `34051cc` | Restore reviewed libm licence evidence |
| `63e056d` | Restore reviewed dependency licence scopes |
| `21aa318` | Preserve build input licence and version evidence |
| `1658d96` | Record scoped ESP-IDF component licences |
| `7ec6934` | Mark selected components as build inputs |
| `e060f21` | Generate build-selected SBOMs with licence evidence |

The branch contains the neutral build-input layer used by the feature. The current local workspace also had unrelated untracked `tools/mpy_analysis/`, `tools/mpy_build_inputs/`, and `uv.lock`; these were not part of the feature commits. Do not copy or commit such files without confirming they belong to the intended change.
## Comparison evidence and its limits

The local SAST workspace contains the comparison programme, tickets, adapters and result artifacts. It has no configured remote, and those files are not included in this feature branch. If the coordinating user supplies that workspace or a portable archive, start with:

1. `planning/sbom-comparison/README.md` for navigation and evidence conventions.
2. `planning/sbom-comparison/20261030_plan.md` for decisions, phases and Q6/Q7/Q8 status.
3. `planning/sbom-comparison/20261030_pybd-clean-smoke.md` for the final clean paired run and earlier PYBD history.
4. `planning/sbom-comparison/20261030_minimal-design-assessment.md`, `20261030_minimal-cost-assessment.md`, `20261030_native-delta-adjudication.md`, and `20261030_licence-delta-adjudication.md` for interpretation and open findings.
5. `planning/sbom-comparison/tickets/` for phase acceptance criteria. Revalidate ticket anchors and status against current branch HEAD before continuing.

The programme includes eleven port/board build configurations (Unix minimal/standard; STM32 PYBD_SF6, F091 LTO and H573; ESP32 C3/S3 applications; RP2 Pico W/W5100S; MIMXRT1020/1060) plus a separately compiled Unix external user-module fixture. The twelve broad paired captures are exploratory: they use earlier candidate revisions and dirty/instrumented source checkouts. They are not twelve clean `335b559` comparisons.

The local workspace has one completed clean `335b559` PYBD_SF6 candidate/reference pair with matching recorded firmware inputs and preserved reports/receipts under `planning/results/sbom-comparison/concluded-clean-pybd-pair/`. Its direct candidate SPDX/CycloneDX smoke and build artifacts are under `concluded-clean-pybd-smoke/`. A separate actual-IDF two-source smoke is under `planning/results/sbom-comparison/final-smokes/{spdx-final.json,cdx-final.json}`; these predate `335b559` and are not a paired full ESP firmware build. These reports are not present in a fresh candidate clone. The paired PYBD run is one target, not full qualification. Its receipts report link-map proof as unverified; matching input hashes do not prove complete capture, link closure or equivalent component boundaries.

Earlier PYBD comparisons at `21aa318` are retained separately. The branch was later changed to restore scoped evidence and compute conclusions; never treat earlier outputs as current-branch outputs. The broad matrix also captures useful historical contrasts, but the source, generator revision, and cleanliness differ by row. Read each observation and receipt before quoting a result. Preserved artifacts do not include full source trees or all build inputs, and `/tmp` paths are not portable.

The real IDF C3/S3 candidate comparisons predate later IDF grouping/declarations and need rerunning before conclusions about current ESP grouping or managed components.

### Capture notes (if the programme workspace is provided)

The build does not automatically produce `compile_commands.json` for every port. Use the existing build-tool support where available, record the capture tool and version, and preserve the database beside the build inputs. The recorded Unix capture used Bear 3.1.3 and produced 318 entries. A command-line `BUILD=` can reach the nested make that builds `mpy-cross`, causing that invocation to fail; build `mpy-cross` separately before the firmware build. ESP-IDF creates application and bootloader databases separately; run the producer on each database as a separate image if both are in scope. The local programme workspace has a `20261030_capture.md` note with further port-specific details, but it is not available from this branch.

## Recommended next work

Use the phase tickets as acceptance criteria, not this list alone. Current priorities:
1. **Decide the mixed-package conjunction rule and revalidate phase 1/Q7 against `335b559`.** The rule is implemented but is not recorded as user-approved; phase 1 cannot close until it is explicitly accepted or changed and the required adversarial licence review has no remaining findings. Read the phase-1 ticket and later decisions, resolve its stale status, and record current acceptance evidence. If the rule changes, update the candidate and tests before building the final comparison matrix.
2. **Restore the comparison prerequisites.** The `feature/sbom-minimal` reference commit and the SAST programme/adapters/results are not published with this branch. Obtain a portable source/ref for the reference and a copy of the programme workspace before attempting paired comparison work. Candidate-only maintenance and tests can continue meanwhile.
3. **Run the clean paired matrix using one final source and candidate revision, after phase 1 closes.** Build once per target; preserve source/submodule identity, toolchain, compilation database, depfiles, frozen sidecar, generated inputs, ELF/map and both reports. Keep ESP application and bootloader invocations separate. Do not program hardware.
4. **Rerun ESP32 C3/S3 with current IDF grouping and declarations.** Verify managed-component lock identities, versions, selected file paths and licences against actual downloaded source. Capture bootloaders independently if they are in scope.
5. **Resolve material licence and source-scope questions against exact selected files:** CYW43 firmware blobs; ST Liberty agreement applicability; the Berkeley DB BSD-4-Clause versus BSD-3-Clause conflict; mbedTLS `sha512.c` applicability; `requests` and other frozen package defaults; IDF managed sources; and any declared custom terms. Preserve unresolved conflicts rather than guessing.
6. **Close header, generated, prebuilt and toolchain questions.** MIMXRT captures have startup dependency-file gaps. The minimal candidate does not generally inventory consumed headers, generated-source lineage, prebuilt archives or runtime-loaded libraries. Decide which are materially needed for the intended consumer and add only targeted mechanisms supported by evidence.
7. **Extend external user-module coverage** beyond the synthetic fixture, including component boundaries, version identities and both output formats.
8. **Make Q6 cutover decision only after the material deltas are adjudicated.** The evidence may support the smaller producer, targeted additions, or retaining the richer reference for consumers needing linked-image, header, archive or scanner evidence.
Do not infer that a selected source is retained in an image, that a link map proves legal applicability, or that matching SBOMs establish compliance. The reference is not an oracle; its link/header/ScanCode evidence is richer for some questions, while the candidate carries clearer source-specific licences for others.

## Publishing and collaboration

The user requested a handover that another host can retrieve from this fork branch. That request authorises one fast-forward push of `feature/sbom-candidate` to the fork, including `SBOM-HANDOVER.md`; it does not authorise a PR, a push to `upstream`, or publication of the reference branch. The P1 ticket says "No push or PR creation"; this specific user instruction authorises the one handover push only. Do not push additional changes without new authorisation.

For the authorised push, use the fork's SSH push URL even when the fetch URL is HTTPS:

```sh
git remote set-url --push origin git@github.com:andrewleech/micropython.git
git push origin feature/sbom-candidate
```

Keep all other planning files and generated outputs out of the MicroPython feature branch. `SBOM-HANDOVER.md` is the single requested handover-document exception.
