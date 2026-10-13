# Firmware build receipt

`mpy_analysis.sbom` can consume a versioned `mpy-firmware-build/v1` receipt to add final firmware images and link-derived source dependencies to an SPDX 2.3 or CycloneDX 1.6 SBOM. The receipt is evidence for one build, not a package-name registry and not proof that every possible firmware dependency has been discovered.

The schema validator, SBOM consumer and event-time GNU Make wrapper prototype are implemented. Fresh captures now cover a Unix build and both the NUCLEO_H563ZI application and mboot; the application and bootloader receipts can be merged while retaining separate image actions and deployment artifacts. The receipt validator follows in-place output generations such as Unix link followed by `strip`, and requires each replacement to consume the prior hash before accepting the final file hash.

A receipt is `complete` only when every image has a gnu-ld map that matches its ELF, no LTO, verified repository state, no unresolved link entry and no unsupported operand or object decision; otherwise it stays `incomplete` with the exact entries. The SBOM summary reports `firmware_completeness: established` only when the receipt is complete, every linked source resolves to a selected component, and no repository provenance gap (dirty, unpinned, untracked, or a submodule remote that differs from its declared URL) exists; component name, version and licence metadata gaps are reported separately through `coverage_status` and do not change that field. Submodule and lock evidence for fetched dependencies is described in the README section "Fetched dependency provenance". Fresh captures from a committed checkout reach `complete` for the Unix standard build and for NUCLEO_H563ZI application plus mboot under one build ID.

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

For GCC-style `-MF` depfiles, the event journal records the depfile SHA-256 and parsed dependency path / hash pairs immediately after the compiler exits, and the receipt validator checks those pairs against the action's captured inputs. Receipts are intended for CI builds where sources and headers are not modified during the build, so depfile dependencies are accepted as the bytes the compiler consumed. A build that does modify an input during an action is still flagged through `changed_inputs_during_action`.

Generated sources (for example `frozen_content.c` or `pins_*.c`) are bound to the source boundaries of the inputs of the captured generator action that wrote them; generator actions that produce files in the build directory are recorded in the receipt. A generated source whose producer was not captured stays an outside-declared-roots gap.

A prebuilt archive inside the installation prefix of the captured compiler (for example `libgcc.a`) is recorded as a `library` operand with a `toolchain` binding to the captured compiler, its SHA-256, and the archive members the GNU map allocated. The SBOM emits it as a `library` component with the compiler version, and it is a dependency of the firmware image. The compiler itself is not a component; it stays in receipt `toolchain` as build provenance. Libraries outside the compiler prefix remain unresolved.

Submodule working trees that were not verified against their pinned Gitlink are only listed as gaps when a captured action consumed an input under them.

Generated-source bindings carry `via: generator`. The SBOM consumer adds each such input (a generator script or data file in a selected repository) as a `generator-input` file component under the repository that owns the path, using the single owner the selected sources already assign to that root; a generator input outside every selected root stays a `linked-source-not-in-selected-scope` gap.

Files the linker loaded without a captured operand (crt objects, system archives, shared libraries) are recorded in `link.implicit_inputs`. No package manager is consulted. The captured compiler answers the identification questions: a static file in the directory named by `-print-libgcc-file-name` is compiler runtime and carries the compiler's release version; a static file in the directory named by `-print-file-name=libc.a` (or `libc.so`) is C library, identified from the macros the compiler sees in the library's own header (`features.h` for glibc, `newlib.h` for newlib) and, where that directory is shared with other libraries, only for the library's own file names. When the compiler has a configured sysroot the file must lie inside it. A contributing static file that is neither stays unresolved. Shared libraries are recorded with their hash and the disposition `dynamic`; they have no name, version or package identity in the receipt because the loader resolves them at run time. Loaded files that contribute nothing are recorded with their hash and reason only, and linker-generated stub sections are recorded in `link.linker_generated_sections`.

`-Wl,`, `-Xlinker` and `@response` forms are expanded before map, linker-script and LTO detection, so `-Map`, `-Map=` and `--Map=` in any of those forms bind the map to the link action and `-flto` inside a response file is seen.

The map is tied to the final ELF structurally: every input section with a non-zero size must lie in an output section that exists in the ELF's section header table (pyelftools), starting inside it when that section is allocated. A map that does not describe the ELF it was recorded with fails validation and produces no receipt. `link.dynamic_needed` lists the ELF's `DT_NEEDED` sonames and is re-read from the ELF when files are verified.

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

The SBOM contains each final ELF as a `firmware` component (SPDX package with purpose FIRMWARE), hashed by its exact ELF SHA-256. Its dependencies point to source components derived from contributing link operands and archive members, plus library components for the statically linked compiler runtime and C library files. Shared libraries are not components: the sonames the final ELF records are kept on the firmware component, as the `mpy:dynamic-needed` property in CycloneDX and as `DYNAMIC_LINK` relationships to unversioned placeholder packages, one per soname, in SPDX. The summary exposes `link_closure.status`, per-image mapped sources and unmapped bindings. `firmware_completeness` is `established` only under the conditions above; it covers native link closure, not runtime-loaded or build-fetched dependencies. `sast-sbom` writes SPDX 2.3 (`sbom.spdx.json`) by default and CycloneDX 1.6 (`sbom.json`) with `--format cyclonedx`; both documents are written from the same format-neutral model by independent writers, and every property without a native SPDX field is kept in element annotations as `mpy:<name>`.

SPDX 2.3 requires a SHA-1 checksum on every file element. Source files and linked C library files are hashed with SHA-1 as well as SHA-256 from the bytes on disk (a C library file must still match the receipt SHA-256, otherwise generation fails). Any component that has no SHA-1, such as a runtime file that is no longer readable, is written as an SPDX package, never as a file without SHA-1, and `validate_spdx` rejects a file element lacking one.

## Policy gate

`sast-sbom` exits 0 for a valid partial result. `sast-sbom-policy --summary sbom-summary.json --receipt receipt.json --profile firmware-linked|firmware-complete` is the separate CI check over the summary. With `--receipt` it validates the receipt with `validate_firmware_receipt` (identity, schema and current artifact hashes) and requires the summary's `link_closure` `schema`, `receipt_id`, `build_id`, images and ELF hashes, and the summary's target, variant and port, to match it; any mismatch is invalid input (exit 2). `firmware-linked` requires `firmware_completeness: established` and a complete link closure; `firmware-complete` additionally requires accounted sources, no gap of any kind and complete required metadata. See the README section "Completeness policy".

## Posthoc published linker inventories

The consumer can also read a qualified schema-v1 mpy-api inventory with `--link-inventory`; it verifies the generation artifact hash and current ELF, map, linker, link operands, compiler objects and contributing sources against `build.consumed_inputs`. Ownership records are used only to create source edges when the exact source path and hash match a selected SBOM source. The image is labelled `posthoc-incomplete`, and the summary retains unresolved inputs, unassessed objects, unmapped sources and explicit limits.

```sh
sast-sbom --scope scope.json --micropython-root "$MPY" \
    --link-inventory inventory.json --output-dir build/sbom
```

`generation.json` is read from the inventory's directory by default; `--link-generation` selects another companion. This posthoc input is a distinct evidence contract, not a `mpy-firmware-build/v1` receipt. It does not capture event-time build actions, prove archive-member selection, establish transforms or deployment outputs, or qualify weak/COMDAT and LTO contribution. It cannot be combined with `--build-receipt` and never marks firmware closure complete.

## Deployment

A receipt records the images a build produced and their deployment artifact hashes, not which of them reach a device or what else the device carries. An application-owned `mpy-deployment/v1` manifest, passed with `sast-sbom --deployment`, binds deployed image files to receipt images by `receipt_image` id or exact hash and adds `deployment_completeness` to the summary. A receipt image that the manifest neither lists nor excludes with a reason makes the deployment `not-established`. See the README section "Deployment manifest".

## Frozen payload

An image whose contributing compile consumes a `frozen_content.c` written by `tools/makemanifest.py` carries `images[].frozen_accounting`; an image without such a source carries `status: "none"` and needs nothing. The record is `{build_root, status, payloads}`, `status` being `none`, `accounted` or `unaccounted`, with one payload per generated frozen source:

- `generated`: path and hash of the `frozen_content.c` the compile consumed, and `generator`, the captured makemanifest action;
- `elements`: one per frozen module, each `bound` (with `source_bindings` under a declared root) or `unbound` (with `reason`). `mpy-compiled` elements name the `mpy-cross` action (`compile_action`), the `.mpy` it wrote, the `-s` target and the source file; `mpy-prebuilt` elements are `.mpy` files frozen without compiling, bound by their own path; `str` elements are sources frozen as text;
- `inputs`: every file outside the build directory the generator opened for reading, with the hash taken when it was opened (top-level and nested manifests, frozen sources, the generator's own Python modules), each `bound` to a declared root or `unbound`;
- `mpy_tool`: the `mpy-tool.py` action and the length and hash of its standard output;
- `unresolved`: reasons that no element or input can carry, and `status` (`accounted` only with no unresolved reason and every element and input bound).

What is observed while the build runs: the Python wrapper starts `makemanifest.py` with a `sitecustomize` module that installs an audit hook (`firmware_observe`). The hook logs each regular file the generator opens for reading with its SHA-256, each process it starts, and points `sys.executable` at the capture's Python wrapper so that the `mpy-tool.py` run is a captured action. The bytecode cache of that run is a fresh empty directory, so the source of every imported module is read. `mpy-cross` is wrapped through `MICROPY_MPYCROSS`; the adapter takes the real binary from the `MICROPY_MPYCROSS=` make variable, then the environment, then `mpy-cross/build/mpy-cross` of the checkout, and records it as an `mpy-cross` tool with its hash. A build that has to build `mpy-cross` itself is not wrapped, and its elements stay unbound (build `mpy-cross` first).

How the chain is tied, by bytes and not by names:

- the actions started by the generator are the captured actions whose parent process is the generator process and whose time lies inside the generator action. Their command lines must equal the processes the audit hook saw start, in order;
- `makemanifest.py` compiles a temporary copy of each source with the manifest's `__version__` line appended and then deletes it. The copy is an `ephemeral-input` of the `mpy-cross` action with its hash and a `derived_from` tie: the source file the generator read, how newlines were translated, and the appended text. The tie holds only when the hash of the current source bytes (translated) plus the appended text equals the recorded hash of the copy;
- each `.mpy` handed to `mpy-tool.py` is a captured input whose path and hash equal an `mpy-cross` output;
- the standard output of `mpy-tool.py`, which the generator copies to the end of `frozen_content.c`, must equal the tail of the file by length and hash;
- the text-frozen strings are parsed out of `frozen_content.c` (the escapes the generator writes) and each must equal the bytes of exactly one file the generator read.

The validator runs the same derivation over the receipt's actions and the current files and rejects a record that differs, so a frozen source, `.mpy`, manifest or `frozen_content.c` changed after the build invalidates the receipt, and a record cannot claim more than its actions show. A receipt with an `unaccounted` payload cannot be `complete`. The SBOM adds bound frozen sources, manifests and generator inputs as file components under the boundary that owns them (a manifest-selected frozen source keeps its component and gains `frozen-payload: bound`), makes the firmware depend on them, and reports `frozen_payload` per image with the selected frozen sources that are not in the payload. Gaps `frozen-payload-unbound` (an element, input or unresolved tie), `frozen-source-not-in-selected-scope` (a bound path that no selected boundary contains) and `frozen-accounting-unavailable` (a receipt of a frozen build captured before this record existed) block `firmware_completeness`.

Not observed, and therefore not claimed:

- `mpy-cross` and `mpy-tool.py` are tools recorded by hash; their own build and behaviour are not part of the account. The relation between the `.mpy` bytes and the bytecode printed into `frozen_content.c` is established by the `mpy-tool.py` output hash, not by decoding the C text;
- the audit hook sees opens made through Python (`open`, `os.open`, imports). A native extension that reads a file itself is invisible to it;
- a manifest that executes code reading other files sees those reads as generator inputs, but they are not tied to a frozen element;
- only `tools/makemanifest.py` run through the `PYTHON` make variable is observed; the CMake and `mkrules.cmake` paths are not captured. A `frozen_content.c` whose generator the adapter did not capture stays a generated-source gap, and a captured run whose observer did not start is `unaccounted`.

`tests/test_firmware_frozen.py` runs real makemanifest, mpy-cross, mpy-tool and compiler invocations through `capture_make()`: sources with a version line, with CRLF newlines and with their own version line, a nested manifest, text-frozen strings with escapes, tool modules loaded from warm bytecode, a source changed after the build, a changed `.mpy`, a changed `frozen_content.c`, `frozen_content.c` rewritten by a later captured action, compiles run outside the wrapper, a frozen source outside the declared roots, records that claim more than the actions show, a build with no frozen content, and a receipt that predates the record.

## Adapter and evidence boundary

A receipt is not a build-capture mechanism. A production adapter must observe the real build process rather than replay a compilation database or inspect old artifacts after the fact. It must keep application and bootloader images separate, capture actual successful link and transform actions, retain compiler / assembler outputs, archives and member selections, linker scripts, response files, generated and system inputs, and bind the final ELF / deployment artifacts to that same build ID. If the adapter cannot resolve an input class or linker format, it must emit an incomplete receipt with the exact missing evidence, not infer inclusion from source names or file presence.

The current wrapper retains `.bin`, `.hex`, `.dfu` and `.pack` outputs under the build directory even when a generator such as `dfu.py` emits them. This records the exact artifact hash, but does not qualify every generator's inputs or establish deployment / flashing behavior.

Recipe lines that need a shell (an `echo text > $@`, a `cat a b > $@`, a `cp`) run no wrapped tool, so the adapter also runs make with `SHELL` set to a recording shell. A line that starts one of the capture's tool wrappers goes straight to the real shell and is recorded by that wrapper. For any other line the shell is run unchanged and, when the files its words name under the build directory were created or rewritten, a `generate` action marked `derivation: "shell-command"` is journaled with the command text, the existing files its words name as inputs and the written files as outputs. A line that writes no named build file is not recorded. A generated header whose producer is such an action with no inputs is attributed as `generated` with empty `source_bindings` and `derivation: "shell-command"`: its bytes are a function of the recorded command alone, and the validator accepts the empty binding only for that case. A recipe that writes a file without naming it (a `cd dir && cmd > file`) is not observed and the file stays "generated header has no captured producing action". A Makefile that assigns `SHELL` itself is overridden by the command-line value.

Adapter-backed controls in `tests/test_firmware_capture_controls.py` and `tests/test_firmware_capture_receipt.py` run real GNU builds through `capture_make()`: selected versus unused archive members, a discarded translation unit, same-basename sources, duplicate archive member names, failed compile, failed link, a build with no captured link action, an object from an uncaptured compiler, an unknown prebuilt archive, LTO through flags and response files, stale ELF / map / archive / deployment bytes, every supported map option form, a response-file map option, a direct `ld` link, linker scripts passed through `-Wl,-T`, malformed and unsupported maps, a map that does not describe its ELF, a `DT_NEEDED` list that differs from the ELF, linker stubs, a generated source with its generator inputs, a dynamic link whose crt objects are identified as glibc and compiler runtime and whose `libc.so.6` is recorded only as a soname, runtime identification against a stub compiler (including a file in a shared library directory and a file outside the sysroot), and composition of separate images under mismatched build IDs or configuration. The adapter qualifies builds that invoke tools through the `CC`, `CXX`, `AS`, `LD`, `AR`, `OBJCOPY`, `STRIP` and `PYTHON` make variables; a tool invoked by absolute name is not observed, and an object produced that way is reported as unresolved rather than inferred.
