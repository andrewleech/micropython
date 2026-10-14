# mpy-api: declarations from a selected MicroPython build

`mpy-api` produces a compiler-derived API inventory and a `.pyi` tree for one linked MicroPython build. It uses the build's compiler expansion, selected objects, ELF/map and frozen manifests, not a source-directory scan or a union of port configurations.

The package requires Python 3.10 or newer. Its parser dependencies are pinned in `pyproject.toml`. It reads the selected checkout's compilation-database and manifest helpers without importing scanner or consumer tooling.

```sh
python3 -m pip install "$MPY/tools/mpy_api"
mpy-api --help
```

The checkout also provides `python3 "$MPY/tools/mpy_api.py"` with the same interface, using the installed parser dependencies.

## Build inputs

Start with a fresh matching build and retain its complete compilation database, objects, unstripped ELF, linker map and actual successful link command. Do not change the sources, headers, manifests, compiler, parser installation or linked inputs while generating declarations. The generator does not build firmware or recover stripped symbols.

The link-command JSON contains the real argument vector and its absolute working directory. Record `exit: 0` from the successful invocation. This is an input contract, not a command to replay:

```json
{
  "argv": ["gcc", "build/main.o", "-Wl,-Map=build/firmware.map", "-o", "build/firmware.elf"],
  "directory": "/absolute/path/to/port",
  "exit": 0
}
```

The example shows the format only. Supply the full command from your build, including its actual objects, libraries, scripts and options. Use the matching target `nm` for cross builds; the generator also needs its companion `objdump`.

```sh
mpy-api --micropython-root "$MPY" --build-dir "$BUILD" \
    --compdb "$COMPDB" --port unix --target unix --variant standard \
    --elf "$ELF" --map "$MAP" --link-command "$LINK_JSON" \
    --manifest "$MANIFEST" \
    --source-root "micropython=$MPY" \
    --source-root "micropython-lib=$MPY_LIB" \
    --source-owner micropython=micropython \
    --source-owner micropython-lib=micropython-lib \
    --path-var "MPY_LIB_DIR=$MPY_LIB" \
    --jobs 4 --output "$NEW_OUTPUT"
```

Set the port/target/variant to the actual build. Manifest roots, owners and path variables are explicit and follow the shared scope helper's contract. Repeat these options for additional roots or manifests. A frozen target path identifies an import, its physical path identifies the consumed file. The same file can have multiple frozen import identities.

Omit manifest options only when the selected build has no `frozen_content.c`. A selected frozen payload requires its actual manifests, and manifest inputs without a selected frozen payload fail. Native/frozen import conflicts also fail rather than choosing a winner.

The output directory must not exist, including as a symlink. Generation stages the complete output and publishes it as one directory; failure does not publish a successful receipt. Use a fresh directory for each run.

`--jobs N` extracts translation units in N forked worker processes, so it needs a platform with `fork`. Results are collected in compiler-database order and every artifact is byte-identical for any N. A failing translation unit raises the same error as serial extraction: the first failing record in order. Within one run, symlink and directory layout under dependency and probe paths are assumed fixed (each such path is resolved once). Consumed input files are not: each path is captured once per process (content hash, size, inode, timestamps and resolved target), captures of the same file in different processes must agree, and before publication every captured path is resolved and stat'ed again and each distinct file hashed again.

Each unit's extraction result is written by its worker to a fact object, in a temporary directory beside the output that is removed when generation ends. The generating process keeps only each unit's index (identities, definitions, a call index and syntax barriers); function events, flow, syntax subtrees and frontend tables stay in the fact objects and are loaded per function where composition reads them. Workers do not compute unit semantics. The link stage computes the unit semantics of a function when it first reads them: the wrapper function of each binding, the attribute-handler, constructor and framework functions composition reads, and the helper functions their analysis looks up, each loaded from its unit's fact object. A function that nothing reads keeps `Unloaded` semantics, as do its helper lookups and frontend-unresolved reasons in the index, and any read of them fails. With `--inventory` the workers compute the unit semantics of every function, because the inventory publishes them. Each unit's consumed input snapshots and include selection constraints are reconciled as the unit arrives. Fact objects are inputs to publication: every section read is checked against the sha256 its worker recorded, and every fact object is hashed again before the output is published. A selected function's linked semantics reuse its unit semantics when none of the helper lookups those made finds a linked helper; otherwise they are computed again with the linked helpers.

Each C translation unit takes two compiler runs. Pass 1 (`-E -fdirectives-only -dI -v -MD`) is the only run that resolves includes; its dependency rule gives the `-M` set, the system-header flags of its linemarkers give the `-MM` set, and its stderr gives the include search order. A header first entered inside a system header and entered again outside one is listed by `-MM` only when the later entry is a separate compiler file entry, which the output does not show; such a unit takes one real `-MM` run, which must agree with every file pass 1 decides. Pass 2 (`-E -fpreprocessed -fdirectives-only`) expands pass 1's output from stdin, with the contract probe declarations appended, and reads no other file. Pass 2 cannot reproduce plain `-E` for `__BASE_FILE__` and `__TIMESTAMP__` (in pass 2 they name stdin), for `#pragma push_macro` of a macro that is defined at the push (pass 1 writes the pop as `#undef`), or for `push_macro`/`pop_macro` in a `_Pragma` operand (it acts in pass 2 but not in pass 1's conditionals). A unit with any of these is expanded by plain `-E` instead, which must enter the same files in the same order and report the same dependency rule; its contract values are unassessed. Pass 2 poisons both builtins, so one produced by token pasting fails pass 2 and routes the unit too. `__COUNTER__` in a directive and `-traditional` fail pass 1, as they fail `-fdirectives-only`. Every other preprocessor construct expands in pass 2 as in plain `-E`.

Pass 1 runs a second time when it read an input, searched a directory, or tested a path with `__has_include`, that was not captured or observed before it started, so the expansion used always comes from a run bracketed by those captures and the final verification. The build's own dependency file beside each object (`.d`, else `.P`) is read as a prediction of those inputs, so this happens about once per worker. Instead of re-running dependency selection, every include lookup of pass 1, including guarded repeats and `#include_next`, is replayed by stat in the reported search order, and the candidates before each selected file must stay absent. The search order is held by the identity (device and inode) of every directory it names: each kept directory stays a distinct directory, one left out as nonexistent or not a directory stays no directory, and one left out as a duplicate stays the same directory as its twin, so every file is found through the same entry with the same spelling and system flag. Each literal `__has_include` probe in a consumed file must keep what it found in every directory the compiler can evaluate it in (the directory of any file of the unit, and the search order). These hold through the final verification before publication. Lookups the replay does not model (command-line includes, `-I-`, `-w`, `__has_include` without a literal operand, renamed presumed files) repeat pass 1 after expansion and require the same `-M` and `-MM` sets instead. Only the compiler identity queries (`-dumpmachine`/`--version`) are run once per distinct flag set.

## Output and consumer contract

| Path | Role |
|---|---|
| `inventory.json` | Only with `--inventory`: schema-v1 build identities, modules/types/symbols, call shapes, source evidence, resolution, coverage, per-unit extraction overviews and raw link provenance; compact canonical JSON, a diagnostic intermediate |
| `modules/**/*.pyi` | Selected runtime module/frozen declarations; packages use `__init__.pyi` |
| `typeshed/stdlib/builtins.pyi` | Generated selected-runtime builtin replacement, not an additive overlay |
| `generation.json` | Schema-v1 producer/input identities, artifact sizes/hashes/roles, coverage and generation ID |
| `enrichment-notices/**` | Captured third-party licence notices, when an enrichment manifest is supplied |

`inventory.json` and `generation.json` are canonical JSON: key-sorted, compact separators, ASCII-only, one trailing newline, so they are not formatted for reading (`python -m json.tool` or `jq` pretty-print them). `inventory.json` is encoded in bounded pieces and streamed to disk while its size and sha256 are computed, so no whole-document copy is held and the receipt needs no second read; per-unit overviews are read from the fact objects one unit at a time while it is written. The artifact hash is the hash of the file bytes. `generation_id` hashes the canonical receipt before adding its own ID. Artifact hashes cover the declarations, notices and, when requested, the inventory, not the receipt itself. Without `--inventory` the receipt records the same build, input and coverage identities and lists no inventory artifact. Consumers should verify the receipt and all artifact hashes before using a generation, and retain its build/input identity alongside checker results.

`translation_units` retains every selected unit's inputs, definitions, frontend/projection evidence and function extraction overview. Function summaries include compiled identities, spans, parameter contracts, event counts, semantic resolutions and all gap/barrier reasons. `diagnostic_stage` distinguishes translation-unit extraction from selected-linked composition. Detailed internal branch graphs for unselected functions are not a published API; selected module/type/member fields retain their own branch/value evidence.

Qualified exported names and type precision are separate. An available callable can have `Any` input/result fields when source evidence cannot prove a narrower contract. `object` means proven arbitrary-object acceptance, not a replacement for every unknown. Unqualified bindings are recorded as gaps, they are not restored as permissive named declarations. Per-symbol `resolution.declaration` records when the qualified runtime surface cannot be represented in `.pyi`.

Call shapes compose actual wrapper bounds, receiver/class/static binding and qualified parser rows. `parser_parameters` retains a complete qualified argument table; `parameters` is its independent public receiver-adjusted view, public type fields do not mutate parser rows. Optional defaults and required/keyword-only flags remain separate from inferred types. Static object identities are translation-unit qualified, including aliases and parent lookups.

Protocol slots describe runtime behaviour. They do not invent public C-named methods or `__new__`, `__init__` or metaclass calls absent from the runtime. Native class lookup and native instance lookup are not assumed equivalent. Genuine dynamic providers retain their own uncertainty, they do not make every omitted name valid.

### Bounded native semantic inference

Input acceptance, storage conversion and result identity are separate source contracts. Selected helper bodies can qualify truth testing, integer conversion and buffer access; a helper name or prototype alone cannot. Parser-table types require the selected parser protocol and its forwarding/storage path, not just an `MP_ARG_*` spelling.

Whole-domain input qualification and parser invocations require successful-path dominance, not merely an unguarded source spelling. Loop-contained, bypassable and conditionally evaluated operations retain applicability gaps; if/loop/switch controlling operands remain input consumers. Handler availability uses the same proof within qualified getter/attribute guards, recorded in `assumed_guards`.

Alias analysis follows source declarations and their lexical scopes. Reassignment, escaping object carriers, unknown offsets and unsupported effects retain barriers rather than proving an arbitrary-object domain. A result can still preserve an input's identity when its accepted domain is unknown; that does not justify emitting an unrestricted identity `TypeVar`.

Conservative origin tracking retains unknown input consumers when unchanged-value identity is unavailable, it does not recover a precise value or domain. Private-storage exemptions require bounded unescaped automatic slots; static, extern, thread-local and unsupported heap stores remain escape barriers. Nested scalar conversions require their exact selected helper/body evidence before passing a result to a recognised constructor.

Mutable nonautomatic initializers do not prove current-call values, constants or identity, including forwarded helper modes and per-call allocation/MPZ temporaries. Readonly static data needs its own immutable source certificate, a skipped opposite-branch write does not make persistent state an automatic local.

Connected native receiver checks depend on the selected runtime dispatch, wrapper flags, type call slot and allocator. `receiver_contract` retains that per-occurrence evidence independently of result inference. Source-unknown native receivers get an explicit `Any` annotation, including fallback signatures, rather than an implicit containing-class restriction. Configurations without the connected check do not inherit another build's receiver precision. Native builder families describe concrete base-type results, not subclass-preserving constructors.

Receiver qualification also requires the selected type-getter and object-tag predicate bodies, complete fixed callback ABI and captured wrapper/dispatch flags. Unsupported calling conventions, attributes or variadic callbacks retain an unresolved receiver rather than inheriting a nominal class annotation.

Fresh-object qualification follows a bounded selected `m_malloc`/`gc_alloc` allocation protocol under the allocator's normal area, bitmap and synchronisation invariants. It does not prove GC liveness, thread safety or memory safety; unsupported allocation/control-flow paths retain gaps.

Supported return evidence includes bounded tuple elements, list append paths, selected JSON modes and selected integer conversion implementations. Unsupported paths remain explicit gaps. This is not a general C interpreter or a promise that every successful native return has a precise annotation.

### Pinned reference comparison

`--enrichment <manifest.json>` accepts a schema-v1 `mpy-api-pyi-enrichment` manifest. It pins a `.pyi` tree, producer/source versions and revisions, file sizes/SHA-256 hashes, source roles, evidence files and licence notices. Every declaration file must identify its module, sources and licences; the tree must contain exactly the declared `.pyi` files. Hexadecimal revisions are case-insensitive and recorded in lowercase. A null target records an unmatched reference; a non-null target is an exact-build claim. Known port/target/variant mismatches fail before extraction; consumed/producer input claims are verified against the completed inventory.

The adapter uses `ast.parse(feature_version=(3, 10))`, matching the frozen-source parser. This is a best-effort grammar restriction, accepted syntax can still vary with the host Python recorded in the receipt's `inputs.python`. It compares qualified source fields with bounded Python annotation/default/call-shape facts and records the results in `inventory.enrichment` (published with `--inventory`). Rendering and comparison share exact nominal kinds and kind aliases; opaque native capabilities are not approximated by builtin annotations. The adapter does not add excluded APIs, narrow accepted inputs, replace defaults or change generated runtime declarations. Source inference works without a reference tree. Unresolved source fields remain `documentation-unverified`; unsupported or shadowed annotations are not typing authority.

`provenance.producer_inputs.enrichment` is the shared reference lineage, containing `provenance` and consumed `inputs`; the generation receipt retains it in `inputs.enrichment`. The comparison report's `inputs_ref` resolves to those consumed inputs, and its source lineage's `producer_inputs_ref` resolves to the selected producer inputs, both relative to the inventory root. Comparison records retain attributed declaration evidence and point to shared source lineage. Licence notices are published as `enrichment-license-notice` artifacts and covered by the generation receipt. Metadata and copied notices do not establish permission to redistribute third-party material.

Generation verifies captured inputs again after staging all artifacts, immediately before publishing the output directory. Changed declaration/evidence/notice bytes or tree membership abort publication rather than producing a successful receipt.

## Coverage and failure boundaries

All selected compilation-database units must be covered. Missing generated inputs, unsupported parser syntax, incomplete compilation databases, changed consumed inputs and unqualified frozen payload namespaces are operational failures. An accepted declaration whose value, type, binding or behaviour is unknown has an explicit unresolved/unassessed field instead, not a successful precise guess.

The C frontend uses the actual compiler's expanded source. A bounded GNU syntax adapter handles grammar gaps while preserving source spans and original evidence. Projections that lose value/effect/control-flow meaning impose a containing-body semantic barrier, so parser acceptance cannot establish helper, constructor or return semantics. Arbitrary C layout, precedence and dataflow inference are not supported.

The semantics of a function are computed when the link stage first reads them, not by the extraction worker. An exception in the semantics of a function that nothing reads therefore does not abort generation; one in a function that is read fails with the `API extraction failed for compiled TU` message of its unit.

The frontend indexes syntax ancestry once per translation unit, avoiding repeated tree searches for scope and evaluation metadata. These indexes are local to extraction and are not retained across translation units or builds.

System header text is analysed once per distinct content, not once per unit. System header text is every expanded line that comes from a captured dependency that is neither the unit's own file nor a project header. A run of consecutive such lines is keyed by its bytes and the (file, line) origins of its lines. The first time a run is met it is adapted and parsed alone and its tables are built: the function declarations the frontend resolves callees by, and the ordinary bindings it resolves typedef casts by. A run has tables only when its own adaptation raises nothing and projects no GNU form, its own parse has no `ERROR` or `MISSING` node, and its last top-level node is a function definition or a top-level node ending in `;` (a bare `struct s { ... }` could be continued by a declarator in the text that follows). A unit then adapts, parses and indexes its text with each such run replaced by spaces of equal length, so every byte offset and line is unchanged, and looks the names that its own functions use up in the run tables, rebased to the unit's offsets. The result is the one the whole text gives. The unit is processed whole, as without runs, when its blanked text has a syntax error, when the top-level node before a blanked run is not a function definition or a top-level node ending in `;`, when a top-level node of the blanked text would span a run, or when adapting the blanked text raises. A run without tables stays in the unit's text, so a syntax error or adaptation failure in system header text still aborts the unit, with the message the whole text gives. Tables are kept in memory per process and cleared at the start of each run (`frontend.clear_run_tables`). With `--jobs` above 1 the workers also share them through a `runs` directory inside the run's fact object directory: a worker that finds a run's table file there reads it instead of analysing the run, and otherwise writes one, whole under a temporary name and renamed into place. Each file starts with the sha256 of the rest, which is checked on every read, and a mismatch aborts generation. The directory belongs to one run and is removed with the fact objects.

Semantic queries use function-local indexes and memoisation. Deduplicating consumers request ordered unique parameter roots, while controlling-expression evidence retains duplicate paths. Query contexts are local to analysis, not serialized or shared across builds.

Compatible same-spelling static tentative declarations join their sole initialized owner. Multiple initialized definitions, incompatible declaration spellings and other unqualified ownership remain ambiguous. Attribute-handler availability requires the selected getter dispatch/NULL/sentinel protocol and a qualified non-null object reference. Computed results and unresolved store/receiver/fallback bindings remain gaps; this is not arbitrary handler analysis.

Linker-generated glue/veneer contributions retain their original map rows and unassessed ownership, they are not compiled-TU objects or filesystem inputs. Missing real link operands still fail. Weak/COMDAT ownership and archive-member allocations retain separate unassessed gaps.

Frozen source parsing never executes Python. It tracks bounded source-local namespace effects and assignment-time aliases; arbitrary cross-module/callback effects remain unassessed. Unsupported source-byte encodings, BOM and identifiers that the host AST would normalise are rejected rather than silently changing MicroPython import semantics.

Frozen STR content is compared with its actual compiled payload bytes. Frozen MPY namespace/table/pointer qualification does not decode bytecode or prove source/body equivalence. MPY-derived source declarations require the fresh matching-build prerequisite and carry an unassessed compiled-content field.

A successful generation is not builtin-aware checker approval. `typing_compatibility` remains `unverified`; checker-support declarations and runtime API declarations have different roles. Do not install a CPython builtin overlay or restore unsupported APIs to make a checker bootstrap. Matching-runtime checks can prove selected behaviour without closing that separate typing gate, and bounded probes do not establish complete builtin coverage.

The tool licence applies to the generator, not to third-party source material retained in build evidence. Preserve the original licences of consumed sources and parser dependencies.

## Developer checks

Install pytest separately from the runtime package, then run the behavioural regressions with the selected checkout's shared scope helpers importable:

```sh
PYTHONPATH="$MPY/tools/mpy_analysis" \
    python3 -m pytest "$MPY/tools/mpy_api/tests"
```

The native fixtures compile and link small C inputs and require host GCC, `nm` and `objdump`. Tests cover uncertain extraction boundaries, they do not replace full selected-build generation, artifact validation or matching-runtime checks.
