"""Native declaration identity, handler protocols and receiver-table consistency."""
from pathlib import Path
import subprocess

import pytest

from mpy_api import compiler
from mpy_api.bindings import constant_value, definitions
from mpy_api.frontend import syntax_adapter
from mpy_api.inventory import (
    _extract, _handler_dispatch_authority, _handler_write_contract, _normal_surface,
    _parameters, _receiver_offset,
)
from mpy_api.link import _map_contributions, collect_ownership, join


ROOT = Path(__file__).resolve().parents[3]


def extract_c(tmp_path, source, filename="fixture.c"):
    physical = tmp_path / filename
    physical.parent.mkdir(parents=True, exist_ok=True)
    physical.write_text(source)
    object_path = tmp_path / "fixture.o"
    arguments = ["gcc", "-std=gnu99", "-O0", "-fdata-sections", "-ffunction-sections",
                 "-I", str(tmp_path), "-c", str(physical), "-o", str(object_path)]
    _, snapshot = compiler.read_input(physical)
    record = {"directory": str(tmp_path), "file": str(physical), "physical": str(physical),
              "identity": str(physical), "object": str(object_path), "arguments": arguments,
              "_source_snapshot": snapshot, "_response_snapshots": {}}
    facts = _extract(record, compiler.authority(ROOT), tmp_path)
    return facts, record


def linked_artifacts(tmp_path, source, filename="fixture.c"):
    facts, record = extract_c(tmp_path, source, filename)
    subprocess.run(record["arguments"], cwd=tmp_path, check=True, capture_output=True, text=True)
    elf, map_path = tmp_path / "fixture.elf", tmp_path / "fixture.map"
    argv = ["gcc", record["object"], "-Wl,-Map=" + str(map_path), "-o", str(elf)]
    subprocess.run(argv, cwd=tmp_path, check=True, capture_output=True, text=True)
    return facts, record, {"directory": tmp_path, "elf": elf, "map": map_path, "argv": argv}


def linked_c(tmp_path, source, filename="fixture.c"):
    facts, record, link = linked_artifacts(tmp_path, source, filename)
    ownership = collect_ownership([record], link)
    raw, resolve, present = join([facts], ownership)
    return facts, raw, resolve, present


def parsed_definitions(source, physical="/fixture/declarations.c"):
    origins = [(physical, line) for line in range(1, len(source.splitlines()) + 1)]
    declarations, functions, errors, frontend = syntax_adapter(
        source, origins, physical, {"dependencies": {physical: "fixture"}})
    assert not errors
    return definitions(declarations, functions, errors, physical, "", [],
                       {"frontend": frontend, "contracts": {}, "dependencies": {}})


def test_one_initialized_static_object_owns_its_tentative_declarations(tmp_path):
    facts, _, resolve, _ = linked_c(tmp_path, """typedef struct { int marker; } mp_obj_type_t;
static const mp_obj_type_t native_type;
static const mp_obj_type_t native_type = { 7 };
const mp_obj_type_t *exported_type = &native_type;
int main(void) { return exported_type->marker != 7; }
""")
    entries = [item for item in facts["definitions"] if item["name"] == "native_type"]
    assert len(entries) == 1
    assert entries[0]["initialization_status"] == "initialized"
    assert len(entries[0]["tentative_declarations"]) == 1
    owner, status = resolve("native_type", facts["translation_unit"])
    assert owner[1]["id"] == entries[0]["id"]
    assert status == "same-translation-unit-allocated-object"


@pytest.mark.parametrize("source", [
    "static const int native_type = 1;\nstatic const int native_type = 2;\n",
    "static const int native_type;\nstatic const long native_type = 2;\n",
    "static int native_type;\nstatic const int native_type = 2;\n",
])
def test_multiple_or_incompatible_definitions_remain_ambiguous(source):
    entries = [item for item in parsed_definitions(source) if item["name"] == "native_type"]
    assert len(entries) == 2
    assert all("tentative_declarations" not in item for item in entries)
    facts = {"translation_unit": "/fixture/declarations.c", "definitions": entries, "functions": []}
    _, resolve, _ = join([facts], {"objects": []})
    owner, status = resolve("native_type", facts["translation_unit"])
    assert owner is None
    assert status == "ambiguous-translation-unit-definition"


HANDLERS = """#include <stdint.h>
typedef uintptr_t mp_obj_t;
typedef mp_obj_t mp_rom_obj_t;
typedef unsigned qstr;
#define MP_OBJ_NULL ((mp_obj_t)0)
#define MP_OBJ_SENTINEL ((mp_obj_t)4)
#define MP_OBJ_FROM_PTR(pointer) ((mp_obj_t)(pointer))
#define MP_ROM_PTR(pointer) ((mp_rom_obj_t)(pointer))
typedef struct _mp_obj_type_t mp_obj_type_t;
typedef struct { const mp_obj_type_t *type; } mp_obj_base_t;
struct _mp_obj_type_t {
    mp_obj_base_t base;
    uint16_t flags, name;
    uint8_t slot_index_attr;
    const void *slots[1];
};
typedef void (*mp_attr_fun_t)(mp_obj_t, qstr, mp_obj_t *);
const mp_obj_type_t mp_type_type = { .base = { &mp_type_type } };
const mp_obj_type_t mp_type_fun_builtin_1 = { .base = { &mp_type_type } };
typedef struct {
    mp_obj_base_t base;
    union { mp_obj_t (*_1)(mp_obj_t); } fun;
} mp_obj_fun_builtin_fixed_t;
static mp_obj_t method(mp_obj_t self_in) { return self_in; }
static const mp_obj_fun_builtin_fixed_t method_obj = {
    .base = { &mp_type_fun_builtin_1 }, .fun = { ._1 = method },
};
typedef struct { mp_obj_base_t base; mp_obj_t payload; } sample_instance_t;
const mp_obj_type_t mp_type_NoneType = { .base = { &mp_type_type } };
typedef struct { mp_obj_base_t base; } mp_obj_none_t;
const mp_obj_none_t mp_const_none_obj = {{&mp_type_NoneType}};
#define mp_const_none MP_OBJ_FROM_PTR(&mp_const_none_obj)
enum { MP_QSTR_Sample = 1, MP_QSTR_unknown, MP_QSTR_method, MP_QSTR_value, MP_QSTR_clear };
void mp_load_method_maybe(mp_obj_t obj, qstr attr, mp_obj_t *dest) {
    const mp_obj_type_t *type = (const mp_obj_type_t *)obj;
    dest[0] = MP_OBJ_NULL;
    dest[1] = MP_OBJ_NULL;
    if (type->slot_index_attr) {
        ((mp_attr_fun_t)type->slots[type->slot_index_attr - 1])(obj, attr, dest);
        if (dest[1] != MP_OBJ_SENTINEL) {
            return;
        }
        dest[1] = MP_OBJ_NULL;
    }
}
void sample_attr(mp_obj_t self_in, qstr attr, mp_obj_t *dest) {
    sample_instance_t *self = (sample_instance_t *)self_in;
    if (dest[0] == MP_OBJ_NULL) {
        if (attr == MP_QSTR_unknown) {
            dest[0] = self->payload;
        } else if (attr == MP_QSTR_method) {
            dest[0] = MP_OBJ_FROM_PTR(&method_obj);
            dest[1] = self_in;
        } else if (attr == MP_QSTR_value) {
            dest[0] = mp_const_none;
        } else if (attr == MP_QSTR_clear) {
            dest[0] = MP_OBJ_NULL;
        }
    } else {
        if (attr == MP_QSTR_value) {
            dest[0] = MP_OBJ_NULL;
        }
    }
}
static const mp_obj_type_t sample = {
    .base = { &mp_type_type }, .flags = 0, .name = MP_QSTR_Sample,
    .slot_index_attr = 1, .slots = { (const void *)sample_attr },
};
const mp_obj_type_t *exported_type = &sample;
int main(void) { return exported_type->name != MP_QSTR_Sample; }
"""


def handler_fixture(tmp_path, source=HANDLERS):
    facts, raw, resolve, present = linked_c(tmp_path, source, "py/runtime.c")
    functions = {function["name"]: function for function in facts["functions"]}
    dispatch = _handler_dispatch_authority((facts, functions["mp_load_method_maybe"]))
    assert dispatch["status"] == "bounded-resolved"
    return facts, raw, resolve, present, functions, dispatch


def test_unresolved_and_method_handler_values_are_not_ordinary_constants(tmp_path):
    facts, raw, resolve, present, _, _ = handler_fixture(tmp_path)
    _, types, _ = _normal_surface(raw, [facts], resolve, present, tmp_path)
    sample = next(item for item in types if item["name"] == "Sample")
    members = {item["name"]: item for item in sample["members"]}
    assert members["unknown"]["kind"] == "unknown"
    assert members["unknown"]["resolution"]["status"] == "unresolved"
    assert members["unknown"]["resolution"]["availability"]["status"] == "unresolved"
    assert members["unknown"]["value_contract"]["kind"] == "unresolved"
    assert members["method"]["kind"] == "unknown"
    assert members["method"]["resolution"]["status"] == "unresolved"
    assert members["method"]["handler_binding"]["destination_one_writes"]
    assert "callshape" not in members["method"]
    assert members["value"]["kind"] == "constant"
    assert members["value"]["resolution"]["status"] == "bounded-resolved"
    assert members["value"]["value_contract"]["kind"] == "None"
    assert members["value"]["value_contract"]["value"] is None
    assert "clear" not in members


def test_missing_selected_null_contract_cannot_qualify_handler_presence(tmp_path):
    source = HANDLERS.replace("#define MP_OBJ_NULL ((mp_obj_t)0)", "enum { MP_OBJ_NULL = 0 };")
    facts, _, resolve, present = linked_c(tmp_path, source, "py/runtime.c")
    functions = {function["name"]: function for function in facts["functions"]}
    dispatch = _handler_dispatch_authority((facts, functions["mp_load_method_maybe"]))
    assert "null" not in facts["contracts"]
    assert dispatch["status"] == "unresolved"
    handler = functions["sample_attr"]
    write = next(item for item in handler["semantics"]["destination_writes"]
                 if item["destination_index"] == 0
                 and selected_pointer_contract(item, facts["contracts"]).get("target") == "mp_const_none_obj")
    qualification = _handler_write_contract(write, handler, facts["contracts"], dispatch,
                                             resolve, present, facts["translation_unit"])
    assert qualification["resolved"] is False
    assert qualification["availability"]["status"] == "unresolved"


def selected_pointer_contract(write, contracts):
    references = {key: value for key, value in contracts.items()
                  if key in ("obj_reference", "rom_reference")}
    return constant_value(write["expression"], references)


def test_store_and_null_success_writes_are_not_getter_values(tmp_path):
    facts, _, resolve, present, functions, dispatch = handler_fixture(tmp_path)
    handler = functions["sample_attr"]
    writes = [item for item in handler["semantics"]["destination_writes"]
              if item["destination_index"] == 0 and item["expression"] == facts["contracts"]["null"]]
    assert len(writes) == 2
    for write in writes:
        qualification = _handler_write_contract(write, handler, facts["contracts"], dispatch,
                                                 resolve, present, facts["translation_unit"])
        assert qualification["indicator"] == "NULL/no-value-or-store-success"
        assert "resolved" not in qualification


def test_nonnull_store_write_cannot_be_published_as_a_getter_value(tmp_path):
    source = HANDLERS.replace(
        "if (attr == MP_QSTR_value) {\n            dest[0] = MP_OBJ_NULL;",
        "if (attr == MP_QSTR_value) {\n            dest[0] = mp_const_none;")
    facts, _, resolve, present, functions, dispatch = handler_fixture(tmp_path, source)
    handler = functions["sample_attr"]
    writes = [item for item in handler["semantics"]["destination_writes"]
              if item["destination_index"] == 0
              and selected_pointer_contract(item, facts["contracts"]).get("target") == "mp_const_none_obj"]
    store = next(item for item in writes if any(
        guard["condition"] == "dest [ 0 ] == " + facts["contracts"]["null"] and guard["branch"] == "else"
        for guard in item["guards"]))
    qualification = _handler_write_contract(store, handler, facts["contracts"], dispatch,
                                             resolve, present, facts["translation_unit"])
    assert qualification["getter"] is False
    assert qualification["resolved"] is False
    assert qualification["availability"]["status"] == "unresolved"


ARGUMENT_HEADER = """#include <stdint.h>
#include <stddef.h>
#include <assert.h>
typedef uintptr_t mp_obj_t;
typedef mp_obj_t mp_rom_obj_t;
typedef struct { const void *type; } mp_obj_base_t;
typedef union { mp_obj_t u_obj, u_rom_obj; int u_int; _Bool u_bool; } mp_arg_val_t;
typedef struct { uint16_t qst, flags; mp_arg_val_t defval; } mp_arg_t;
typedef struct { mp_obj_t key, value; } mp_map_elem_t;
typedef struct { size_t used; mp_map_elem_t *table; } mp_map_t;
#define MP_OBJ_NEW_QSTR(value) (((mp_obj_t)(value) << 3) | 2)
#define MP_ROM_QSTR(value) MP_OBJ_NEW_QSTR(value)
enum { MP_MAP_LOOKUP = 0 };
#define MICROPY_ERROR_REPORTING 0
#define MICROPY_ERROR_REPORTING_TERSE 0
mp_map_elem_t *mp_map_lookup(mp_map_t *, mp_obj_t, unsigned);
void mp_arg_error_terse_mismatch(void);
_Bool mp_obj_is_true(mp_obj_t);
int mp_obj_get_int(mp_obj_t);
enum { MP_ARG_BOOL = 1, MP_ARG_INT = 2, MP_ARG_OBJ = 3, MP_ARG_KIND_MASK = 3,
       MP_ARG_REQUIRED = 256, MP_ARG_KW_ONLY = 512,
       MP_QSTR_self = 1, MP_QSTR_mode, MP_QSTR_filter };
#define MP_OBJ_NULL ((mp_obj_t)0)
#define MP_OBJ_SENTINEL ((mp_obj_t)4)
#define MP_OBJ_FROM_PTR(pointer) ((mp_obj_t)(pointer))
#define MP_ROM_PTR(pointer) ((mp_rom_obj_t)(pointer))
extern const int mp_type_type, mp_type_fun_builtin_var, none_obj;
#define mp_const_none MP_OBJ_FROM_PTR(&none_obj)
typedef mp_obj_t (*mp_fun_kw_t)(size_t, const mp_obj_t *, mp_map_t *);
typedef struct { mp_obj_base_t base; uint32_t sig; union { mp_fun_kw_t kw; } fun; } mp_obj_fun_builtin_var_t;
void mp_arg_parse_all(size_t n_pos, const mp_obj_t *pos, mp_map_t *kws, size_t n_allowed,
                      const mp_arg_t *allowed, mp_arg_val_t *out_vals);
"""

def argument_parser_source():
    source = (ROOT / "py/argcheck.c").read_text()
    start = source.index("void mp_arg_parse_all(")
    end = source.index("\nvoid mp_arg_parse_all_kw_array(", start)
    return source[start:end]


def test_receiver_rebinding_restores_complete_qualified_table_rows(tmp_path):
    py = tmp_path / "py"
    py.mkdir()
    signature = next(line for line in (ROOT / "py/obj.h").read_text().splitlines()
                     if line.startswith("#define MP_OBJ_FUN_MAKE_SIG("))
    (py / "runtime.h").write_text(ARGUMENT_HEADER + signature + "\n")
    (py / "argcheck.c").write_text(argument_parser_source())
    facts, _ = extract_c(tmp_path, """#include "py/runtime.h"
#include "py/argcheck.c"
const int mp_type_type = 0, mp_type_fun_builtin_var = 0, none_obj = 1;
mp_obj_t configure(size_t n_args, const mp_obj_t *args, mp_map_t *kw_args) {
    static const mp_arg_t allowed[] = {
        { MP_QSTR_self, MP_ARG_OBJ | MP_ARG_REQUIRED },
        { MP_QSTR_mode, MP_ARG_INT, { .u_int = 17 } },
        { MP_QSTR_filter, MP_ARG_OBJ | MP_ARG_KW_ONLY, { .u_rom_obj = mp_const_none } },
    };
    mp_arg_val_t vals[3];
    mp_arg_parse_all(n_args, args, kw_args, 3, allowed, vals);
    return mp_const_none;
}
const mp_obj_fun_builtin_var_t configure_obj = {
    .base = { &mp_type_fun_builtin_var },
    .sig = MP_OBJ_FUN_MAKE_SIG(1, 5, 1),
    .fun = { .kw = configure },
};
""")
    wrapper = next(item for item in facts["definitions"] if item["name"] == "configure_obj")
    assert wrapper["kind"] == "wrapper"
    shape = dict(wrapper["callshape"])
    _receiver_offset(shape, 1)
    function = next(item for item in facts["functions"] if item["name"] == "configure")
    parser = next(item for item in facts["functions"] if item["name"] == "mp_arg_parse_all")
    _parameters(shape, function["semantics"], facts, (facts, parser))
    assert shape["parameters_resolution"] == "resolved"
    assert [row["name"] for row in shape["parser_parameters"]] == ["self", "mode", "filter"]
    assert [row["name"] for row in shape["parameters"]] == ["mode", "filter"]
    assert shape["parameters"][0]["default_contract"]["value"] == 17
    _receiver_offset(shape, 0)
    assert shape["implicit_argument_offset"] == 0
    assert [row["name"] for row in shape["parameters"]] == ["self", "mode", "filter"]
    assert shape["parameters"][0]["required"] is True
    assert shape["parameters"][0]["kind"] == "positional_only"
    assert (shape["minimum"], shape["maximum"]) == (1, 5)


def test_generated_stub_load_and_zero_sections_have_nonfilesystem_ownership(tmp_path):
    rows = [
        " .glue_7        0x00030968        0x0 linker stubs",
        " .glue_7t       0x00030968        0x0 linker stubs",
        " .vfp11_veneer  0x00030968        0x0 linker stubs",
        " .v4_bx         0x00030968        0x0 linker stubs",
        "LOAD linker stubs",
        "LOAD selected.o",
        " .text.main     0x00010000        0x4 selected.o",
    ]
    loaded, allocations, generated = _map_contributions("\n".join(rows), tmp_path, first_line=40)
    assert loaded == ["selected.o"]
    assert allocations == [{"section": ".text.main", "address": 0x10000, "size": 4,
                            "object": str(tmp_path / "selected.o")}]
    assert [item["section"] for item in generated] == [
        ".glue_7", ".glue_7t", ".vfp11_veneer", ".v4_bx", None]
    assert [item["size"] for item in generated] == [0, 0, 0, 0, None]
    assert [item["map_rows"][0] for item in generated] == [
        {"line": 40 + index, "text": row} for index, row in enumerate(rows[:5])]
    assert all(item["producer"] == "linker stubs" for item in generated)
    assert all(item["ownership"]["status"] == "unassessed" for item in generated)
    assert all(not ({"object", "path", "sha256"} & set(item)) for item in generated)


def test_nonzero_generated_stub_continuation_keeps_bytes_without_compiled_owner(tmp_path):
    rows = [" .text.stub", "                0x00020000       0x10 linker stubs"]
    loaded, allocations, generated = _map_contributions("\n".join(rows), tmp_path)
    assert loaded == allocations == []
    assert len(generated) == 1
    assert (generated[0]["section"], generated[0]["address"], generated[0]["size"]) == (
        ".text.stub", 0x20000, 16)
    assert generated[0]["map_rows"] == [
        {"line": index + 1, "text": row} for index, row in enumerate(rows)]
    assert generated[0]["ownership"]["status"] == "unassessed"


@pytest.mark.parametrize("absolute", [False, True])
def test_generated_stub_identity_cannot_alias_an_explicit_file_operand(tmp_path, absolute):
    operand = str(tmp_path / "linker stubs") if absolute else "linker stubs"
    with pytest.raises(ValueError, match="conflicts with an explicit filesystem input"):
        _map_contributions("LOAD linker stubs", tmp_path, filesystem_operands=[operand])


def test_missing_real_map_load_remains_a_fatal_input_error(tmp_path):
    _, record, link = linked_artifacts(tmp_path, "int main(void) { return 0; }\n")
    map_path = link["map"]
    map_path.write_text(map_path.read_text() + "\nLOAD missing-selected.o\n")
    with pytest.raises(FileNotFoundError):
        collect_ownership([record], link)
