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

To retain resolved frozen selections, set `MICROPY_FROZEN_OUTPUT` to an absolute JSON path during the normal make build, or set the CMake variable of that name. Pass the resulting file with `--frozen`. It records the source, frozen target, freeze kind and `require()` package identity from the manifest evaluation. Do not substitute a manifest source file for this resolved output.

Build-input mode also reads existing compiler depfiles. It selects additional `.c` prerequisites from the first rule, maps each to its own source owner, and records whether a source was compiled directly or included by another translation unit. It reports missing, unreadable, or unspecified depfiles as issues. It does not generate depfiles or recursively traverse headers.

For each selected file, licence resolution prefers its SPDX source header, then explicit package metadata or a reviewed path-specific declaration, then the owning library's default, then the applicable repository default. Each file retains its expression and provenance. Different expressions within a component are not combined into an inferred `AND` or `OR` expression; review the per-file data.

In both SPDX and CycloneDX output, build-input mode identifies selected build inputs. It does not prove that their code was retained in the linked image or is present at runtime. SPDX records components as build dependencies of the image; CycloneDX identifies the build lifecycle and build-input scope. Interpret CycloneDX output in that build lifecycle: although CycloneDX 1.6 recommends treating components without a scope as required at runtime, this output does not establish their runtime presence.

These inputs record compilation commands, available depfile prerequisites and frozen selections. They do not account for consumed headers, prebuilt dependencies, or `.c` inclusions absent from the available depfiles. Review reported issues and unknown licence declarations before qualification.
