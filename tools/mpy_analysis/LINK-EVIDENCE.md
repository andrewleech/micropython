# Link evidence

`mpy_analysis.link_evidence` reads what a finished build leaves behind and writes an `mpy-link-evidence/v1` file that `mpy_analysis.sbom` uses to add the firmware images and the sources, headers and runtime libraries they link to an SPDX 2.3 or CycloneDX 1.6 SBOM. Nothing is observed while the build runs, and no package manager is consulted.

## Inputs

- A compilation database. For make ports use [compiledb](https://github.com/nickdiego/compiledb) in dry-run mode, then build normally; for CMake projects use `CMAKE_EXPORT_COMPILE_COMMANDS`. Each entry's `-o` operand names the object, and its `file` the source.
- The GNU ld map of each image. Only input sections that land in a section the final ELF allocates (`SHF_ALLOC`) count as contributing, so an object whose code the linker discarded is not a component.
- The compilers' dependency files (`-MF`, or the `.P` copy `py/mkrules.mk` keeps). Ninja deletes these after use, so for a Ninja build the same data comes from `ninja -t deps` in the compile database's directory. A compile with neither is reported, never assumed to read no headers. An assembler source named `.s` is not preprocessed and reads no headers through a dependency file.
- The ELF, for its `DT_NEEDED` list and its allocated sections.

```sh
# make port: the dry run records every command, including ones for files a real run builds later
(cd ports/stm32 && python -m compiledb -n -S -o ../../out/app-cc.json make BOARD=NUCLEO_H563ZI && make -j8 BOARD=NUCLEO_H563ZI)

python -m mpy_analysis.link_evidence \
  --compile-database out/app-cc.json \
  --image application:application:ports/stm32/build-NUCLEO_H563ZI/firmware.elf:ports/stm32/build-NUCLEO_H563ZI/firmware.map \
  --source-root micropython="$MPY" --source-root stm32lib="$MPY/lib/stm32lib" \
  --output out/evidence.json --selected-database out/selected.json
```

`-S` keeps entries for generated sources that do not exist yet. Use the default build directory of a port: a `BUILD=` override on the command line is passed to the nested `mpy-cross` build, which then writes its objects into the same directory. `--image ID:ROLE:ELF:MAP` is repeated for an application and a bootloader built by separate make runs, each with its own `--compile-database`. `--artifact IMAGE_ID=PATH` names a deployment file (`.bin`, `.hex`, `.dfu`) derived from an image's ELF so a deployment manifest can bind it by hash.

`--selected-database` is the compilation database of the objects that contribute to the images. Pass it to `mpy-build-scope --compile-database` (or `python -m mpy_build_inputs.scope --compile-database`) so the scope selects what was linked and not every file the build compiled (a compile of `mpy-cross`, or of a feature the board leaves out, is not firmware). Then run `sast-sbom --scope ... --link-evidence out/evidence.json` and `sast-sbom-policy`.

## What the evidence records

Per image: the ELF and map hashes, `dynamic_needed`, the contributing sources as `{root, path}` bindings under a declared `--source-root` (the deepest root containing the file), the headers the contributing compiles read (owned by a root, inside the compiler's own directories, or unowned), statically linked compiler runtime and C library files, and `unresolved` rows.

- An archive member is tied to its source by its bytes: exactly one compiled object with the member's name must hash to the member. Otherwise it is unresolved.
- Sources generated into the build directory (`frozen_content.c`, `pins_*.c`) are listed as `generated_sources` and are not components.
- A contributing object with no compile command (an LTO object, or a prebuilt object) and a header outside every root and the compiler's directories are `unresolved` and block `firmware_completeness: established`.
- Compiler runtime (`libgcc`) and C library files are identified by asking the compiler that compiled the objects (`-print-libgcc-file-name`, `-print-file-name=libc.a`, `-dumpfullversion`, and the version macros of the library's own header). When the objects were built with more than one compiler or multilib flag set, the most common one is used.

## SBOM output

The SBOM contains each ELF as a `firmware` component (SPDX package with purpose FIRMWARE) hashed by its SHA-256. Its dependencies are the source components of the contributing objects, header components under their owning boundary, a toolchain headers library, and the compiler runtime and C library. Shared libraries are not components: the sonames the ELF records are kept on the firmware component, as the `mpy:dynamic-needed` property in CycloneDX and as `DYNAMIC_LINK` relationships to unversioned placeholder packages in SPDX. The summary reports `link_closure.status`, mapped sources and unmapped bindings. `firmware_completeness` is `established` only when the link closure is complete and no repository provenance gap exists; name, version and licence gaps are reported through `coverage_status`.

SPDX 2.3 requires a SHA-1 checksum on every file element. Files are hashed with SHA-1 as well as SHA-256 from the bytes on disk; a component without a SHA-1 is written as an SPDX package, never as a file without one. SPDX file names are relative: a system file recorded by its absolute path is written as `./` plus the path without its leading slash, and the file comment keeps the original path. A package that contains files has `filesAnalyzed: true`, a `packageVerificationCode` and `licenseInfoFromFiles`. Output is checked with `validate_spdx` and, as an independent reference, with `pyspdxtools` from the SPDX `tools-python` project.

## Policy gate

`sast-sbom` exits 0 for a valid partial result. `sast-sbom-policy --summary sbom-summary.json --link-evidence evidence.json --profile firmware-linked|firmware-complete|deployable` is the separate CI check. With `--link-evidence` it requires the summary's `link_closure` `schema`, `evidence_id` and image ELF hashes to match the file; a mismatch is invalid input (exit 2). `firmware-linked` requires `firmware_completeness: established` and a complete link closure; `firmware-complete` additionally requires accounted sources, no gap of any kind and complete required metadata. See the README section "Completeness policy".

## Deployment

Link evidence records the images a build produced, not which of them reach a device or what else the device carries. An application-owned `mpy-deployment/v1` manifest, passed with `sast-sbom --deployment`, binds deployed image files to evidence images by `image` id or exact hash and adds `deployment_completeness` to the summary. See the README section "Deployment manifest".

## Posthoc published linker inventories

`--link-inventory` reads a qualified schema-v1 mpy-api inventory; it verifies the generation artifact hash and the current ELF, map, linker, link operands, compiler objects and contributing sources against `build.consumed_inputs`. The image is labelled `posthoc-incomplete`, and the summary retains unresolved inputs, unassessed objects, unmapped sources and explicit limits. It cannot be combined with `--link-evidence` and never marks firmware closure complete.

```sh
sast-sbom --scope scope.json --micropython-root "$MPY" --link-inventory inventory.json --output-dir build/sbom
```

`generation.json` is read from the inventory's directory by default; `--link-generation` selects another companion.

## Not checked

The evidence is derived after the build, from files on disk. It does not prove that the files are the ones the build used, and it does not detect an input changed during the build. A stale compilation database or map describing another build is not detected beyond the object and member lookups failing. A frozen manifest is an explicit input (`manifest.py`, handled by `mpy_build_inputs.scope`), and generated files are not components.
