# tools/

Notes on helper scripts in this directory. See each script's help for its options.

## SBOM generation

Install the dependencies with `python -m pip install -r tools/sbom-requirements.txt`. Run `python tools/sbom.py --repo . --offline -o repository.spdx.json` to inventory repository dependencies. This mode is separate from build-input mode and does not describe a firmware image. Omit `--offline` to query remote tags. SPDX JSON is the default; use `--format cyclonedx` for CycloneDX JSON.

For one image, pass one or more existing compilation databases and the image identity:

```sh
python tools/sbom.py --repo . --offline \
  --compdb /path/to/compile_commands.json --image unix-standard \
  --exclude mpy-cross --root sdk=/path/to/sdk \
  --frozen /path/to/frozen-inputs.json -o unix-standard.spdx.json
```

Repeat `--compdb`, `--root`, `--exclude`, or `--frozen` as needed. Roots use `NAME=PATH`; relative root and exclusion paths are relative to `--repo`. The deepest matching known or explicit root owns a source. An external source without a matching root remains in the output as an unmatched source. Exclusions match both source paths and command working directories. Exclude all host-tool source or working directories present in the databases, including `mpy-cross` where applicable. The caller must provide databases for a single image; use separate invocations and image identities for an application and bootloader. A database that mixes images without a usable directory boundary must be separated externally.

For ESP-IDF builds, the tool reads `project_description.json` beside each compilation database when present. It uses `build_component_info` to group selected sources under components in the declared IDF `components` directory and under direct children of a `managed_components` directory. This description supplies component boundaries and names, not proof of source identity, package version, or licence. It does not select components that have no build inputs. An invalid description or conflicting component names for the same directory causes an error. When the description is absent, supply `--root NAME=PATH` for external IDF or managed component roots that need to be identified; otherwise external sources without a matching root are reported as unmatched.

Selected native sources in a Git submodule nested inside an IDF component are grouped as a separate component named for the submodule directory, with its own revision. The nested checkout identity is trusted only when each parent Git link matches the corresponding checkout revision. A mismatch or unavailable link is reported as an identity issue. Components can share a name while having different paths and revisions; use those fields to distinguish them.

To retain resolved frozen selections, set `MICROPY_FROZEN_OUTPUT` to an absolute JSON path during the normal make build, or set the CMake variable of that name. Pass the resulting file with `--frozen`. It records the source, frozen target, freeze kind and `require()` package identity from the manifest evaluation. Do not substitute a manifest source file for this resolved output.

Build-input mode also reads existing compiler depfiles. It selects additional `.c` prerequisites from the first rule, maps each to its own source owner, and records whether a source was compiled directly or included by another translation unit. It reports missing, unreadable, or unspecified depfiles as issues. It does not generate depfiles or recursively traverse headers.

For each selected file, licence resolution prefers its SPDX source header, then explicit package metadata or a reviewed path-specific declaration, then the owning library's default, then the applicable repository default. Each file retains its expression and provenance. Different expressions within a component are not combined into an inferred `AND` or `OR` expression; review the per-file data.

The root `LICENSE.spdx.yaml` contains curated `external_repositories` declarations for selected external sources. Each entry names a Git `url`, a full 40-character lowercase commit, and `licenses` with checkout-relative `path` and `license` values. Paths can identify a file, directory, or matching pattern; the most specific matching declaration takes precedence over broader reviewed declarations. These entries cover only the sources listed, not all dependencies.

An external declaration applies only when the source is tracked in a clean checkout whose `origin` URL and HEAD match the reviewed repository and commit. URL matching normalizes the host and common Git URL forms, including a trailing `.git`, but preserves the repository path and port. For nested checkouts, every parent Git link must match the child HEAD and the parent must report that link as clean. A dirty checkout, untracked file, mismatched commit or Git link, or unavailable identity prevents use of the external declaration.

For an external declaration, a source licence header takes priority, followed by explicit package metadata and then the reviewed path declaration. Conflicting source headers or package metadata and a narrower reviewed declaration are reported as metadata conflicts and leave the component licence unresolved; do not treat the selected expression as a resolution of the conflict. Conflicting reviewed declarations at the same scope are also reported. Review the evidence and issues rather than combining different expressions automatically.

Nonstandard licences are defined under `hasExtractedLicensingInfos` in the root declaration. Their `seeAlso` entries can link to immutable source URLs at the reviewed revision. SPDX 2.3 requires `extractedText`, so the declaration includes a short extracted notice alongside the links; the links are exported as SPDX cross references and CycloneDX external references. The short notice is not necessarily the full licence text, so consult the linked source for the terms.

Selected sources identified as dirty or untracked, and IDF component sources that cannot be verified against a Git checkout, are reported as identity issues. Their component licence remains unresolved and has no trusted declared licence. A source SPDX header may still be recorded as an untrusted observation, including for managed components; it is not a trusted package declaration. A managed-component registry marker alone does not verify the source or its licence. Review the source identity and licence evidence before use.

In both SPDX and CycloneDX output, build-input mode identifies selected build inputs. It does not prove that their code was retained in the linked image or is present at runtime. SPDX records components as build dependencies of the image; CycloneDX identifies the build lifecycle and build-input scope. Interpret CycloneDX output in that build lifecycle: although CycloneDX 1.6 recommends treating components without a scope as required at runtime, this output does not establish their runtime presence.

These inputs record compilation commands, available depfile prerequisites and frozen selections. They do not account for consumed headers, prebuilt dependencies, or `.c` inclusions absent from the available depfiles. Review reported issues and unknown licence declarations before qualification.
