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

## Output and consumer contract

| Path | Role |
|---|---|
| `inventory.json` | Schema-v1 build identities, modules/types/symbols, call shapes, source evidence, resolution and coverage |
| `modules/**/*.pyi` | Selected runtime module/frozen declarations; packages use `__init__.pyi` |
| `typeshed/stdlib/builtins.pyi` | Generated selected-runtime builtin replacement, not an additive overlay |
| `generation.json` | Schema-v1 producer/input identities, artifact sizes/hashes/roles, coverage and generation ID |
| `enrichment-notices/**` | Captured third-party licence notices, when an enrichment manifest is supplied |

`generation_id` hashes the canonical receipt before adding its own ID. Artifact hashes cover the inventory and declarations, not the receipt itself. Consumers should verify the receipt and all artifact hashes before using a generation, and retain its build/input identity alongside checker results.

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

The adapter uses `ast.parse(feature_version=(3, 10))`, matching the frozen-source parser. This is a best-effort grammar restriction, accepted syntax can still vary with the host Python recorded in the receipt's `inputs.python`. It compares qualified source fields with bounded Python annotation/default/call-shape facts and records the results in `inventory.enrichment`. Rendering and comparison share exact nominal kinds and kind aliases; opaque native capabilities are not approximated by builtin annotations. The adapter does not add excluded APIs, narrow accepted inputs, replace defaults or change generated runtime declarations. Source inference works without a reference tree. Unresolved source fields remain `documentation-unverified`; unsupported or shadowed annotations are not typing authority.

`provenance.producer_inputs.enrichment` is the shared reference lineage, containing `provenance` and consumed `inputs`; the generation receipt retains it in `inputs.enrichment`. The comparison report's `inputs_ref` resolves to those consumed inputs, and its source lineage's `producer_inputs_ref` resolves to the selected producer inputs, both relative to the inventory root. Comparison records retain attributed declaration evidence and point to shared source lineage. Licence notices are published as `enrichment-license-notice` artifacts and covered by the generation receipt. Metadata and copied notices do not establish permission to redistribute third-party material.

Generation verifies captured inputs again after staging all artifacts, immediately before publishing the output directory. Changed declaration/evidence/notice bytes or tree membership abort publication rather than producing a successful receipt.

## Coverage and failure boundaries

All selected compilation-database units must be covered. Missing generated inputs, unsupported parser syntax, incomplete compilation databases, changed consumed inputs and unqualified frozen payload namespaces are operational failures. An accepted declaration whose value, type, binding or behaviour is unknown has an explicit unresolved/unassessed field instead, not a successful precise guess.

The C frontend uses the actual compiler's expanded source. A bounded GNU syntax adapter handles grammar gaps while preserving source spans and original evidence. Projections that lose value/effect/control-flow meaning impose a containing-body semantic barrier, so parser acceptance cannot establish helper, constructor or return semantics. Arbitrary C layout, precedence and dataflow inference are not supported.

The frontend indexes syntax ancestry once per translation unit, avoiding repeated tree searches for scope and evaluation metadata. These indexes are local to extraction and are not retained across translation units or builds.

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
