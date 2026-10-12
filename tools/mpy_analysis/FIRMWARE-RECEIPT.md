# Firmware build receipt

`mpy_analysis.sbom` can consume a versioned `mpy-firmware-build/v1` receipt to add final firmware images and link-derived source dependencies to a CycloneDX SBOM. The receipt is evidence for one build, not a package-name registry and not proof that every possible firmware dependency has been discovered.

The schema validator, SBOM consumer and event-time GNU Make wrapper prototype are implemented. Fresh captures now cover a Unix build and both the NUCLEO_H563ZI application and mboot; the application and bootloader receipts can be merged while retaining separate image actions and deployment artifacts. The receipt validator follows in-place output generations such as Unix link followed by `strip`, and requires each replacement to consume the prior hash before accepting the final file hash.

All generated receipts remain explicitly incomplete. The latest Unix receipt has 491 unresolved inputs; the H563 app has 548, including 509 dependencies first observed from depfiles after compilation, four contributing sources outside declared roots, one unresolved prebuilt/system input and 34 unverified Gitlinks. Mboot has 35 unresolved entries, including one prebuilt/system input and the same 34 Gitlinks. The latest SBOMs contain 340 Unix components / 39 gaps and 578 H563 components / 73 gaps, with firmware completeness `not-established` for both. Do not mark firmware coverage complete until every actual-build and control gate in `planning/sbom-generic/G5-build-link-closure.md` passes.

## Receipt contract

The top-level object uses `schema: "mpy-firmware-build/v1"`, a deterministic `receipt_id` (SHA-256 over canonical JSON excluding that field), and one `build_id` shared by every captured action and image. It records:

- target, port, variant and effective build variables;
- source repositories, checked-out revision, index digest and dirty state; unverified Gitlink roots retain the parent-recorded commit SHA and are explicitly marked dirty and unverified when their checkout metadata is unavailable;
- tool identities and absolute executable paths with SHA-256 hashes;
- successful compiler, assembler, linker and transform actions, each with argv, working directory, build ID, exit status and hashed inputs / outputs; compiler actions also retain the emitted depfile hash and the dependency paths / hashes parsed from it;
- one record per final image, including the exact link action, ELF and map hashes, linker format, LTO status, all link operands and explicit contribution decisions;
- complete static-archive inventories only when a captured archive action, GNU ld member identity, archived member bytes and captured object inputs agree; each member is marked contributed, discarded or not extracted. Prebuilt archives, duplicate member names and unbound extracted members stay explicitly unsupported;
- compiler / assembler object accounting, discarded-section evidence and deployment artifact hashes;
- an explicit complete or incomplete status. Unsupported inputs, unresolved symbols, unknown linker formats, unqualified LTO and missing contribution decisions prevent a complete receipt.

A source binding names a declared repository root and a canonical relative path. It is never inferred from a basename. The SBOM consumer connects a contributing binding to selected manifest / compiler source components only when both root and path match. Unmatched bindings are preserved as gaps and make the image link closure incomplete.

The validator checks receipt identity and schema, action/image relationships, explicit inclusion decisions, canonical source paths and current hashes for recorded files. A hash match proves that the current file bytes match the receipt, it does not prove the build adapter observed those bytes during the build. That temporal binding must come from an adapter capturing the actual successful build actions and their inputs / outputs.

For GCC-style `-MF` depfiles, the event journal records the depfile SHA-256 and parsed dependency path / hash pairs immediately after the compiler exits, and the receipt validator checks those pairs against the action's captured inputs. The receipt identity covers this evidence, but dependencies are still discovered after compilation; it does not prove the compiler consumed those exact bytes during the action, so the receipt remains incomplete for that evidence gap.

## Usage

The Python API accepts a receipt object or a JSON file path:

```python
from pathlib import Path
from mpy_analysis.sbom import generate_sbom

bom, summary = generate_sbom(
    selected_scope,
    micropython_root=Path("/path/to/micropython"),
    build_receipt=Path("/path/to/firmware-build-receipt.json"),
)
```

The CLI accepts the same receipt with `--build-receipt`:

```sh
python -m mpy_analysis.sbom \
  --scope selected-scope.json \
  --micropython-root /path/to/micropython \
  --build-receipt firmware-build-receipt.json \
  --output-dir sbom-output
```

The SBOM contains each final ELF as a CycloneDX `firmware` component, hashed by its exact ELF SHA-256. Its dependencies point to source components derived from contributing link operands and archive members. The summary exposes `link_closure.status`, per-image mapped sources and unmapped bindings. A complete G5 image link closure does not set `firmware_completeness` to complete: remaining firmware dependency classes and final MVP gates are separate roadmap work.

## Posthoc published linker inventories

The consumer can also read a qualified schema-v1 mpy-api inventory with `--link-inventory`; it verifies the generation artifact hash and current ELF, map, linker, link operands, compiler objects and contributing sources against `build.consumed_inputs`. Ownership records are used only to create source edges when the exact source path and hash match a selected SBOM source. The image is labelled `posthoc-incomplete`, and the summary retains unresolved inputs, unassessed objects, unmapped sources and explicit limits.

```sh
sast-sbom --scope scope.json --micropython-root "$MPY" \
    --link-inventory inventory.json --output-dir build/sbom
```

`generation.json` is read from the inventory's directory by default; `--link-generation` selects another companion. This posthoc input is a distinct evidence contract, not a `mpy-firmware-build/v1` receipt. It does not capture event-time build actions, prove archive-member selection, establish transforms or deployment outputs, or qualify weak/COMDAT and LTO contribution. It cannot be combined with `--build-receipt` and never marks firmware closure complete.

## Adapter and evidence boundary

A receipt is not a build-capture mechanism. A production adapter must observe the real build process rather than replay a compilation database or inspect old artifacts after the fact. It must keep application and bootloader images separate, capture actual successful link and transform actions, retain compiler / assembler outputs, archives and member selections, linker scripts, response files, generated and system inputs, and bind the final ELF / deployment artifacts to that same build ID. If the adapter cannot resolve an input class or linker format, it must emit an incomplete receipt with the exact missing evidence, not infer inclusion from source names or file presence.

The current wrapper retains `.bin`, `.hex`, `.dfu` and `.pack` outputs under the build directory even when a generator such as `dfu.py` emits them. This records the exact artifact hash, but does not qualify every generator's inputs or establish deployment / flashing behavior.

G5 now has fresh event-time Unix and NUCLEO_H563ZI application+mboot captures plus a host-native `capture_make()` inclusion test. The native receipt and generated SBOM verify selected versus unused archive members, discarded code and same-basename sources / objects in distinct paths. Duplicate archive-member names and the remaining stale-map / ELF, changed-input, mismatched-target, failed-build, prebuilt and LTO controls are still open; parser and consumer tests alone do not establish firmware coverage.
