"""Source-derived wrapper/parser contracts at the emitted declaration boundary."""
import ast
import copy
import ctypes
import subprocess
import inspect
import json
from pathlib import Path

import pytest

from mpy_api import compiler
from mpy_api.declarations import render_declarations
from mpy_api.inventory import _call_slot_abi_matches, _call_slot_binding, _checked_wrapper_type_authority, _extract, _header_helper_authority, _linked_functions, _native_receiver_contract, _normal_surface, _parameters, _public_contracts, _receiver_offset
from mpy_api.link import collect_ownership, join
from mpy_api.semantics import checked_receiver_authority, semantics
from test_native_bindings import ARGUMENT_HEADER, HANDLERS, ROOT, argument_parser_source, extract_c, linked_c, parsed_definitions
from test_semantic_contracts import connected_framework_fixture


FIXED_HEADER = """
extern const int mp_type_fun_builtin_1;
typedef struct { mp_obj_base_t base; union { mp_obj_t (*_1)(mp_obj_t); } fun; } mp_obj_fun_builtin_fixed_t;
mp_obj_t mp_obj_new_bool(_Bool);
mp_obj_t mp_obj_new_int(int);
mp_obj_t mp_obj_new_bytes(const void *, size_t);
mp_obj_t mp_obj_new_str(const char *, size_t);
mp_obj_t mp_obj_new_list(size_t, const mp_obj_t *);
typedef struct { char *buf; size_t len; } vstr_t;
const void *mp_obj_get_type(mp_obj_t);
mp_obj_t mp_obj_new_str_type_from_vstr(const void *, vstr_t *);
typedef struct { void *buf; size_t len; } mp_buffer_info_t;
enum { MP_BUFFER_READ = 1, MP_BUFFER_WRITE = 2 };
void mp_get_buffer_raise(mp_obj_t, mp_buffer_info_t *, unsigned);
"""


def prepare_headers(tmp_path, parser=None):
    py = tmp_path / "py"
    py.mkdir(exist_ok=True)
    signature = next(line for line in (ROOT / "py/obj.h").read_text().splitlines()
                     if line.startswith("#define MP_OBJ_FUN_MAKE_SIG("))
    header = ARGUMENT_HEADER.replace("union { mp_fun_kw_t kw; }", "union { mp_fun_kw_t kw; mp_obj_t (*var)(size_t, const mp_obj_t *); }")
    (py / "runtime.h").write_text("#pragma once\n" + header + FIXED_HEADER + signature + "\n")
    (py / "argcheck.c").write_text(argument_parser_source() if parser is None else parser)


def extracted_wrapper(tmp_path, body, *, fixed=True, parser=None, minimum=1, maximum=1):
    prepare_headers(tmp_path, parser)
    wrapper = ("const mp_obj_fun_builtin_fixed_t selected_obj = {"
               ".base = { &mp_type_fun_builtin_1 }, .fun = { ._1 = selected } };" if fixed else
               "const mp_obj_fun_builtin_var_t selected_obj = { .base = { &mp_type_fun_builtin_var }, "
               f".sig = MP_OBJ_FUN_MAKE_SIG({minimum}, {maximum}, 1), .fun = {{ .kw = selected }} }};")
    facts, _ = extract_c(tmp_path, '#include "py/runtime.h"\n#include "py/argcheck.c"\n' + body + "\n" + wrapper)
    function = next(item for item in facts["functions"] if item["name"] == "selected")
    declaration = next(item for item in facts["definitions"] if item["name"] == "selected_obj")
    return facts, function, declaration


def bind_wrapper(facts, function, declaration, offset=0, receiver=None):
    shape = copy.deepcopy(declaration["callshape"])
    shape["implicit_argument_offset"] = offset
    parser = next((item for item in facts["functions"] if item["name"] == "mp_arg_parse_all"), None)
    _parameters(shape, function["semantics"], facts, (facts, parser) if parser else None)
    summary = _public_contracts(shape, function["semantics"], function,
                                {"object_id": declaration["id"], "object_source_span": declaration["source_span"]}, receiver)
    symbol = {"name": "selected", "kind": "function", "identity": declaration["id"],
              "resolution": {"availability": {"status": "resolved"}}, "callshape": shape, "semantics": summary}
    if receiver is not None:
        symbol["receiver_contract"] = copy.deepcopy(receiver)
    return symbol


def emitted(symbol):
    inventory = {"schema_version": 1, "modules": [{"name": "fixture", "identity": "fixture-module", "exports": [symbol]}]}
    text = render_declarations(inventory)["modules/fixture.pyi"]
    tree = ast.parse(text)
    function = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "selected")
    return text, tree, function


def annotation_name(node, tree):
    if isinstance(node, ast.Name):
        for statement in tree.body:
            if isinstance(statement, ast.ImportFrom):
                for alias in statement.names:
                    if (alias.asname or alias.name) == node.id:
                        return statement.module + "." + alias.name
        return node.id
    return ast.unparse(node)


def test_fixed_alias_identity_has_required_typevar(tmp_path):
    facts, function, wrapper = extracted_wrapper(tmp_path, """mp_obj_t selected(mp_obj_t value) {
    mp_obj_t alias = value;
    return alias;
}""")
    symbol = bind_wrapper(facts, function, wrapper)
    _, tree, declaration = emitted(symbol)
    argument = declaration.args.posonlyargs[0]
    assert argument.arg == "arg0"
    assert annotation_name(argument.annotation, tree) == annotation_name(declaration.returns, tree)
    typevar = next(node for node in tree.body if isinstance(node, ast.Assign)
                   and any(isinstance(target, ast.Name) and target.id == argument.annotation.id for target in node.targets))
    assert annotation_name(typevar.value.func, tree) == "typing.TypeVar"
    assert symbol["callshape"]["parameters"][0]["type_contract"]["kind"] == "object"


@pytest.mark.parametrize("body", [
    "mp_obj_t alias = value; alias = mp_obj_new_int(9); return alias;",
    "mp_obj_t alias = value; overwrite(&alias); return alias;",
    "mp_obj_t alias = value; (void)alias; { mp_obj_t alias = mp_obj_new_int(17); return alias; }",
])
def test_reassigned_escaped_and_shadowed_aliases_never_get_input_typevar(tmp_path, body):
    facts, function, wrapper = extracted_wrapper(tmp_path, "void overwrite(mp_obj_t *);\nmp_obj_t selected(mp_obj_t value) {" + body + "}")
    symbol = bind_wrapper(facts, function, wrapper)
    _, tree, declaration = emitted(symbol)
    assert annotation_name(declaration.args.posonlyargs[0].annotation, tree) == "typing.Any"
    assert not any(isinstance(node, ast.Assign) and isinstance(node.value, ast.Call)
                   and annotation_name(node.value.func, tree) == "typing.TypeVar" for node in tree.body)
    assert all(contract.get("exported_argument") != "arg0" for contract in symbol["semantics"]["returns"])


@pytest.mark.parametrize("body, parameter_kind, result", [
    ("mp_obj_t alias = value; return mp_obj_new_bool(mp_obj_is_true(alias));", "object", "bool"),
    ("mp_obj_t alias = value; int converted = mp_obj_get_int(alias); return mp_obj_new_int(converted);", "integer-conversion-capability", "int"),
])
def test_prototype_only_alias_helper_input_stays_unknown_with_independent_result(tmp_path, body, parameter_kind, result):
    facts, function, wrapper = extracted_wrapper(tmp_path, "mp_obj_t selected(mp_obj_t value) {" + body + "}")
    symbol = bind_wrapper(facts, function, wrapper)
    _, tree, declaration = emitted(symbol)
    parameter = symbol["callshape"]["parameters"][0]
    assert parameter["type_contract"] is None
    assert parameter["type_resolution"] == "unresolved"
    assert any(constraint["kind"] == parameter_kind for constraint in parameter["constraints"])
    assert annotation_name(declaration.returns, tree) == result
    assert annotation_name(declaration.args.posonlyargs[0].annotation, tree) == "typing.Any"


def shifted_body(count="n_args - 1", vector="pos_args + 1", table_count="2"):
    return f"""mp_obj_t selected(size_t n_args, const mp_obj_t *pos_args, mp_map_t *kw_args) {{
    static const mp_arg_t allowed[] = {{
        {{ MP_QSTR_mode, MP_ARG_INT, {{ .u_int = 17 }} }},
        {{ MP_QSTR_filter, MP_ARG_BOOL | MP_ARG_KW_ONLY, {{ .u_bool = 0 }} }},
    }};
    mp_arg_val_t vals[2];
    mp_arg_parse_all({count}, {vector}, kw_args, {table_count}, allowed, vals);
    return mp_obj_new_bool(vals[1].u_bool);
}}"""


def test_shifted_parser_keeps_receiver_defaults_and_storage_without_getter_bodies(tmp_path):
    facts, function, wrapper = extracted_wrapper(tmp_path, shifted_body(), fixed=False, minimum=1, maximum=3)
    symbol = bind_wrapper(facts, function, wrapper, offset=1)
    assert [parameter["name"] for parameter in symbol["callshape"]["parameters"]] == ["mode", "filter"]
    mode, flag = symbol["callshape"]["parameters"]
    assert mode["default_contract"]["value"] == 17
    assert mode["type_contract"] is None and mode["type_resolution"] == "unresolved"
    assert flag["type_contract"] is None and flag["type_resolution"] == "unresolved"
    assert mode["result_storage"]["kind"] == "int"
    assert flag["parser_conversion"]["helper_contract_authority"]["resolution"] == "unresolved"
    assert flag["result_storage"]["kind"] == "bool"
    _, tree, declaration = emitted(symbol)
    assert annotation_name(declaration.returns, tree) == "bool"
    assert annotation_name(declaration.args.args[0].annotation, tree) == "typing.Any"
    assert annotation_name(declaration.args.kwonlyargs[0].annotation, tree) == "typing.Any"
    _receiver_offset(symbol["callshape"], 0)
    assert [parameter["name"] for parameter in symbol["callshape"]["parameters"]] == ["self", "mode", "filter"]


@pytest.mark.parametrize("count,vector,table_count", [
    ("n_args - 1", "pos_args + 2", "2"),
    ("n_args - 3", "pos_args + 3", "2"),
    ("n_args - 1", "pos_args + 1", "1"),
    ("n_args + 1", "pos_args + 1", "2"),
])
def test_unqualified_parser_shift_or_extent_does_not_supply_named_parameters(tmp_path, count, vector, table_count):
    facts, function, wrapper = extracted_wrapper(tmp_path, shifted_body(count, vector, table_count), fixed=False, minimum=1, maximum=3)
    symbol = bind_wrapper(facts, function, wrapper, offset=1)
    assert symbol["callshape"]["parameters_resolution"] == "unresolved"
    _, tree, declaration = emitted(symbol)
    assert declaration.args.vararg and declaration.args.kwarg
    assert annotation_name(declaration.returns, tree) == "bool"


def test_flag_spelling_cannot_bless_changed_selected_parser_conversion(tmp_path):
    source = argument_parser_source().replace("mp_obj_is_true(given_arg)", "(given_arg != 0)")
    facts, function, wrapper = extracted_wrapper(tmp_path, shifted_body(), fixed=False, parser=source, minimum=1, maximum=3)
    symbol = bind_wrapper(facts, function, wrapper, offset=1)
    assert symbol["callshape"]["parameters_resolution"] == "unresolved"
    _, tree, declaration = emitted(symbol)
    assert declaration.args.kwarg is not None
    assert annotation_name(declaration.returns, tree) == "bool"

@pytest.mark.parametrize("before,after", [
    ("i < n_allowed", "i < n_allowed - 1"),
    ("i < n_allowed", "i <= n_allowed"),
    ("MP_OBJ_NEW_QSTR(allowed[i].qst)", "MP_OBJ_NEW_QSTR(MP_QSTR_mode)"),
    ("MP_OBJ_NEW_QSTR(allowed[i].qst)", "MP_OBJ_NEW_QSTR(allowed[i].qst + 1)"),
    ("continue;", ";"),
    ("mp_arg_error_terse_mismatch();", "(void)0;"),
    ("pos_found++;", "pos_found += 2;"),
    ("given_arg = pos[i];", "i++; given_arg = pos[i];"),
])
def test_incomplete_selected_parser_protocol_cannot_rebind_table(tmp_path, before, after):
    parser = argument_parser_source()
    assert before in parser
    facts, function, wrapper = extracted_wrapper(
        tmp_path, shifted_body(), fixed=False, minimum=1, maximum=3,
        parser=parser.replace(before, after))
    symbol = bind_wrapper(facts, function, wrapper, offset=1)
    assert symbol["callshape"]["parameters_resolution"] == "unresolved"
    _, tree, declaration = emitted(symbol)
    assert declaration.args.kwarg is not None
    assert annotation_name(declaration.returns, tree) == "bool"


@pytest.mark.parametrize("rows", [
    """{ MP_QSTR_mode, MP_ARG_INT, { .u_int = 17 } },
       { MP_QSTR_filter, MP_ARG_OBJ | MP_ARG_REQUIRED, { .u_obj = 0 } }""",
    """{ MP_QSTR_mode, MP_ARG_OBJ | MP_ARG_REQUIRED, { .u_obj = 0 } },
       { MP_QSTR_mode, MP_ARG_OBJ | MP_ARG_REQUIRED, { .u_obj = 0 } }""",
])
def test_unrepresentable_parser_names_preserve_runtime_shape_with_valid_declaration(tmp_path, rows):
    body = """mp_obj_t selected(size_t n_args, const mp_obj_t *pos_args, mp_map_t *kw_args) {
        static const mp_arg_t allowed[] = {""" + rows + """};
        mp_arg_val_t vals[2];
        mp_arg_parse_all(n_args, pos_args, kw_args, 2, allowed, vals);
        return mp_const_none;
    }"""
    facts, function, wrapper = extracted_wrapper(tmp_path, body, fixed=False, minimum=0, maximum=2)
    symbol = bind_wrapper(facts, function, wrapper)
    parameters = copy.deepcopy(symbol["callshape"]["parameters"])
    assert symbol["callshape"]["parameters_resolution"] == "resolved"
    _, tree, declaration = emitted(symbol)
    assert declaration.args.vararg and declaration.args.kwarg
    assert annotation_name(declaration.returns, tree) == "None"
    assert symbol["callshape"]["parameters"] == parameters
    assert symbol["resolution"]["declaration"]["status"] == "unresolved"


def test_unused_object_parser_row_accepts_whole_objects_without_result_identity(tmp_path):
    body = """mp_obj_t selected(size_t n_args, const mp_obj_t *pos_args, mp_map_t *kw_args) {
        static const mp_arg_t allowed[] = {
            { MP_QSTR_filter, MP_ARG_OBJ | MP_ARG_KW_ONLY, { .u_rom_obj = mp_const_none } },
        };
        mp_arg_val_t vals[1];
        mp_arg_parse_all(n_args, pos_args, kw_args, 1, allowed, vals);
        return mp_const_none;
    }"""
    facts, function, wrapper = extracted_wrapper(tmp_path, body, fixed=False, minimum=0, maximum=1)
    symbol = bind_wrapper(facts, function, wrapper)
    parameter = symbol["callshape"]["parameters"][0]
    assert parameter["parser_conversion"]["helper_contract_authority"]["resolution"] == "bounded-resolved"
    _, tree, declaration = emitted(symbol)
    assert annotation_name(declaration.args.kwonlyargs[0].annotation, tree) == "object"
    assert annotation_name(declaration.returns, tree) == "None"



def test_json_mode_parser_shift_preserves_leading_required_argument(tmp_path):
    body = """enum { STRING_MODE = 1, STREAM_MODE = 2 };
static mp_obj_t helper(size_t n_args, const mp_obj_t *pos_args, mp_map_t *kw_args, unsigned mode) {
    static const mp_arg_t allowed[] = { { MP_QSTR_filter, MP_ARG_OBJ | MP_ARG_KW_ONLY, { .u_rom_obj = mp_const_none } } };
    mp_arg_val_t vals[1];
    mp_arg_parse_all(n_args - mode, pos_args + mode, kw_args, 1, allowed, vals);
    if (mode == STRING_MODE) { return mp_obj_new_str("ok", 2); }
    return mp_const_none;
}
mp_obj_t selected(size_t n_args, const mp_obj_t *pos_args, mp_map_t *kw_args) {
    return helper(n_args, pos_args, kw_args, STRING_MODE);
}"""
    facts, function, wrapper = extracted_wrapper(tmp_path, body, fixed=False, minimum=1, maximum=65535)
    symbol = bind_wrapper(facts, function, wrapper)
    assert [parameter["name"] for parameter in symbol["callshape"]["parameters"]] == ["arg0", "filter"]
    assert symbol["callshape"]["parameters"][0]["required"] is True
    assert symbol["callshape"]["parameters"][1]["default_contract"]["kind"] == "None"
    _, tree, declaration = emitted(symbol)
    assert annotation_name(declaration.returns, tree) == "str"
    assert declaration.args.posonlyargs[0].arg == "arg0"
    assert declaration.args.kwonlyargs[0].arg == "filter"


@pytest.mark.parametrize("optimization", ["-O0", "-O2"])
def test_exact_linked_cross_tu_identity_and_unallocated_definition(tmp_path, optimization):
    header = tmp_path / "shared.h"
    header.write_text("typedef unsigned long mp_obj_t;\nmp_obj_t external_identity(mp_obj_t);\n")
    facts, records = [], []
    for filename, source in (("helper.c", '#include "shared.h"\nstatic mp_obj_t backend_alias(mp_obj_t value) { return value; }\nmp_obj_t external_identity(mp_obj_t value) { return backend_alias(value); }\n'),
                             ("caller.c", '#include "shared.h"\nmp_obj_t selected(mp_obj_t value) { return external_identity(value); }\nint main(void) { return selected(0); }\n')):
        physical, obj = tmp_path / filename, tmp_path / (filename + ".o")
        physical.write_text(source)
        args = ["gcc", "-std=gnu99", optimization, "-ffunction-sections", "-I", str(tmp_path), "-c", str(physical), "-o", str(obj)]
        _, snapshot = compiler.read_input(physical)
        record = {"directory": str(tmp_path), "file": str(physical), "physical": str(physical), "identity": str(physical),
                  "object": str(obj), "arguments": args, "_source_snapshot": snapshot, "_response_snapshots": {}}
        facts.append(_extract(record, compiler.authority(ROOT), tmp_path))
        records.append(record)
        subprocess.run(args, check=True, capture_output=True)
    elf, map_path = tmp_path / "fixture.elf", tmp_path / "fixture.map"
    argv = ["gcc", *(record["object"] for record in records), "-Wl,-Map=" + str(map_path), "-o", str(elf)]
    subprocess.run(argv, check=True, capture_output=True)
    ownership = collect_ownership(records, {"directory": tmp_path, "elf": elf, "map": map_path, "argv": argv})
    _, _, present = join(facts, ownership)
    linked = _linked_functions(facts, present)
    caller = next(function for function in facts[1]["functions"] if function["name"] == "selected")
    summary = semantics(caller, {"selected": [caller]}, [], facts[1]["contracts"], linked_functions=linked)
    shape = {"minimum": 1, "maximum": 1, "keywords": False, "implicit_argument_offset": 0}
    mapped = _public_contracts(shape, summary, caller, {"object_id": "selected-object"})
    assert mapped["return_resolution"] == "bounded-resolved"
    assert mapped["returns"][0]["exported_argument"] == "arg0"
    assert linked["external_identity"][0]["authority"]["object"] == records[0]["object"]
    assert "backend_alias" in linked
    if optimization == "-O2":
        assert not present(facts[0], {"name": "backend_alias"})
        assert linked["backend_alias"][0]["authority"]["allocated_caller_authority"]["direct_static_call_path"]
    reduced = _linked_functions(facts[1:], present)
    assert "external_identity" not in reduced
    unavailable = semantics(caller, {"selected": [caller]}, [],
                            facts[1]["contracts"], linked_functions=reduced)
    unbound = _public_contracts(dict(shape), unavailable, caller, {"object_id": "selected-object"})
    assert unbound["return_resolution"] == "unresolved"
    assert all(contract.get("exported_argument") != "arg0" for contract in unbound["returns"])


@pytest.mark.parametrize("minimum,expression,public_name", [
    (1, "args[0]", "arg0"),
    (2, "args[1]", "arg1"),
])
def test_positional_vector_identity_uses_actual_item_index(tmp_path, minimum, expression, public_name):
    prepare_headers(tmp_path)
    facts, _ = extract_c(tmp_path, f"""#include "py/runtime.h"
mp_obj_t selected(size_t n_args, const mp_obj_t *args) {{ return {expression}; }}
const mp_obj_fun_builtin_var_t selected_obj = {{
    .base = {{ &mp_type_fun_builtin_var }}, .sig = MP_OBJ_FUN_MAKE_SIG({minimum}, 2, 0), .fun = {{ .var = selected }},
}};
""")
    function = next(item for item in facts["functions"] if item["name"] == "selected")
    wrapper = next(item for item in facts["definitions"] if item["name"] == "selected_obj")
    symbol = bind_wrapper(facts, function, wrapper)
    assert symbol["semantics"]["returns"][0]["exported_argument"] == public_name
    _, tree, declaration = emitted(symbol)
    parameter = next(parameter for parameter in declaration.args.posonlyargs if parameter.arg == public_name)
    assert annotation_name(parameter.annotation, tree) == annotation_name(declaration.returns, tree)
    assert any(isinstance(node, ast.Assign) and isinstance(node.value, ast.Call)
               and annotation_name(node.value.func, tree) == "typing.TypeVar" for node in tree.body)


def test_vector_spelling_without_count_declaration_authority_cannot_get_identity(tmp_path):
    prepare_headers(tmp_path)
    facts, _ = extract_c(tmp_path, """#include "py/runtime.h"
mp_obj_t selected(int n_args, const mp_obj_t *args) { return args[0]; }
const mp_obj_fun_builtin_var_t selected_obj = {
    .base = { &mp_type_fun_builtin_var }, .sig = MP_OBJ_FUN_MAKE_SIG(1, 2, 0), .fun = { .var = selected },
};
""")
    function = next(item for item in facts["functions"] if item["name"] == "selected")
    wrapper = next(item for item in facts["definitions"] if item["name"] == "selected_obj")
    symbol = bind_wrapper(facts, function, wrapper)
    _, tree, declaration = emitted(symbol)
    assert annotation_name(declaration.returns, tree) == "typing.Any"
    assert all(annotation_name(parameter.annotation, tree) == "typing.Any" for parameter in declaration.args.posonlyargs)


@pytest.mark.parametrize("mode", ["MP_BUFFER_READ", "MP_BUFFER_WRITE"])
def test_prototype_only_buffer_helper_stays_unknown_with_independent_none_result(tmp_path, mode):
    facts, function, wrapper = extracted_wrapper(tmp_path, f"""mp_obj_t selected(mp_obj_t value) {{
    mp_obj_t alias = value;
    mp_buffer_info_t info;
    mp_get_buffer_raise(alias, &info, {mode});
    return mp_const_none;
}}""")
    symbol = bind_wrapper(facts, function, wrapper)
    _, tree, declaration = emitted(symbol)
    parameter = symbol["callshape"]["parameters"][0]
    assert parameter["type_contract"] is None
    assert parameter["type_resolution"] == "unresolved"
    assert annotation_name(declaration.args.posonlyargs[0].annotation, tree) == "typing.Any"
    assert isinstance(declaration.returns, ast.Constant) and declaration.returns.value is None


def test_complete_successful_union_is_not_blocked_by_unknown_truth_value(tmp_path):
    facts, function, wrapper = extracted_wrapper(tmp_path, """mp_obj_t selected(mp_obj_t value) {
    if (mp_obj_is_true(value)) { return mp_obj_new_int(7); }
    return mp_obj_new_str("no", 2);
}""")
    symbol = bind_wrapper(facts, function, wrapper)
    _, tree, declaration = emitted(symbol)
    assert symbol["semantics"]["return_resolution"] == "bounded-resolved"
    assert isinstance(declaration.returns, ast.BinOp)
    assert {annotation_name(declaration.returns.left, tree), annotation_name(declaration.returns.right, tree)} == {"int", "str"}


def test_constructor_function_pointer_shadow_does_not_inherit_runtime_result(tmp_path):
    facts, function, wrapper = extracted_wrapper(tmp_path, """mp_obj_t unknown_bool(_Bool);
mp_obj_t selected(mp_obj_t value) {
    mp_obj_t (*mp_obj_new_bool)(_Bool) = unknown_bool;
    return mp_obj_new_bool(mp_obj_is_true(value));
}""")
    symbol = bind_wrapper(facts, function, wrapper)
    _, tree, declaration = emitted(symbol)
    assert annotation_name(declaration.returns, tree) == "typing.Any"


def test_containing_native_class_does_not_narrow_unchecked_receiver_family(tmp_path):
    facts, function, wrapper = extracted_wrapper(tmp_path, """mp_obj_t selected(mp_obj_t value) {
    if (mp_obj_is_true(value)) { return value; }
    vstr_t vstr;
    return mp_obj_new_str_type_from_vstr(mp_obj_get_type(value), &vstr);
}""")
    receiver = {"native_type": "str", "native_type_identity": "selected-str-type",
                "nominal_authority_resolution": "unresolved",
                "authority": {"resolution": "bounded-resolved", "domain_scope": "containing native type only"}}
    symbol = bind_wrapper(facts, function, wrapper, offset=1, receiver=receiver)
    _, tree, declaration = emitted(symbol)
    assert annotation_name(declaration.returns, tree) == "typing.Any"
    assert any(contract["kind"] == "same-native-family" for contract in symbol["semantics"]["returns"])
    assert not any(isinstance(node, ast.Assign) and isinstance(node.value, ast.Call)
                   and annotation_name(node.value.func, tree) == "typing.TypeVar" for node in tree.body)


def wrapper_type_source(flags, name="mp_type_fun_builtin_1", physical="/fixture/declarations.c"):
    source = """typedef unsigned short uint16_t;
typedef unsigned char uint8_t;
typedef struct { const void *type; } mp_obj_base_t;
typedef struct {
    mp_obj_base_t base; uint16_t flags, name; uint8_t slot_index_call; const void *slots[1];
} mp_obj_type_t;
enum { MP_TYPE_FLAG_BINDS_SELF = 1, MP_TYPE_FLAG_BUILTIN_FUN = 2 };
extern const mp_obj_type_t mp_type_type;
void fun_builtin_1_call(void);
const mp_obj_type_t mp_type_fun_builtin_1 = {
    .base = { .type = &mp_type_type }, .name = 1, .flags = """ + flags + """,
    .slot_index_call = 1, .slots = { (const void *)fun_builtin_1_call },
};"""
    source = source.replace("const mp_obj_type_t mp_type_fun_builtin_1 =", "const mp_obj_type_t " + name + " =")
    values = parsed_definitions(source, physical)
    return next(value for value in values if value["name"] == name)


@pytest.mark.parametrize("flags,mutation,nominal", [
    ("MP_TYPE_FLAG_BINDS_SELF | MP_TYPE_FLAG_BUILTIN_FUN", None, True),
    ("MP_TYPE_FLAG_BINDS_SELF", None, False),
    ("MP_TYPE_FLAG_BUILTIN_FUN", None, False),
    ("MP_TYPE_FLAG_BINDS_SELF | MP_TYPE_FLAG_BUILTIN_FUN",
     lambda source: source.replace(".slots = {(const void *)checked_fun_call}",
                                   ".slots = {(const void *)unchecked_fun_call}"), False),
    ("MP_TYPE_FLAG_BINDS_SELF | MP_TYPE_FLAG_BUILTIN_FUN",
     lambda source: source.replace("mp_convert_member_lookup(obj, type, elem->value, dest);",
                                   "dest[0] = elem->value; dest[1] = 0;"), False),
])
def test_actual_connected_receiver_framework_controls_emitted_identity_domain(tmp_path, flags, mutation, nominal):
    definitions = connected_framework_fixture(mutation)
    checked = checked_receiver_authority(definitions)
    native_wrapper = wrapper_type_source(flags)
    facts, function, wrapper = extracted_wrapper(tmp_path, "mp_obj_t selected(mp_obj_t value) { return value; }")
    owner = wrapper_type_source("0", name="mp_type_str", physical="/fixture/py/objstr.c")
    receiver = _native_receiver_contract(owner, wrapper["callshape"], native_wrapper,
                                         checked, definitions, Path("/fixture"))
    assert (receiver["nominal_authority_resolution"] == "bounded-resolved") is nominal
    symbol = bind_wrapper(facts, function, wrapper, offset=1, receiver=receiver)
    tree, declaration = emitted_native_method(symbol)
    receiver_argument = (declaration.args.posonlyargs + declaration.args.args)[0]
    assert annotation_name(receiver_argument.annotation, tree) == ("str" if nominal else "typing.Any")
    assert annotation_name(declaration.returns, tree) == ("str" if nominal else "typing.Any")


@pytest.mark.parametrize("invocation,return_annotation", [
    ("for (size_t i = 0; i < n_args; ++i) { PARSE; }", "None"),
    ("for (size_t i = 0; i < n_args; ++i) { return mp_const_none; } PARSE;", "typing.Any"),
])
def test_parser_public_rows_require_successful_path_applicability(tmp_path, invocation, return_annotation):
    body = """mp_obj_t selected(size_t n_args, const mp_obj_t *pos_args, mp_map_t *kw_args) {
    static const mp_arg_t allowed[] = {
        { MP_QSTR_filter, MP_ARG_OBJ | MP_ARG_KW_ONLY, { .u_rom_obj = mp_const_none } },
    };
    mp_arg_val_t vals[1];
    """ + invocation.replace("PARSE", "mp_arg_parse_all(n_args, pos_args, kw_args, 1, allowed, vals)") + """
    return mp_const_none;
}"""
    facts, function, wrapper = extracted_wrapper(tmp_path, body, fixed=False, minimum=0, maximum=1)
    symbol = bind_wrapper(facts, function, wrapper)
    assert symbol["callshape"]["parameters_resolution"] == "unresolved"
    assert not symbol["semantics"]["public_parameters"]
    _, tree, declaration = emitted(symbol)
    assert declaration.args.vararg and declaration.args.kwarg
    assert not declaration.args.kwonlyargs
    assert annotation_name(declaration.returns, tree) == return_annotation


@pytest.mark.parametrize("name,source,descriptor,nominal", [
    ("mp_type_str", "/fixture/py/objstr.c", "function", True),
    ("custom_type", "/fixture/py/objstr.c", "function", False),
    ("mp_type_str", "/fixture/custom.c", "function", False),
    ("mp_type_str", "/fixture/py/objstr.c", "staticmethod", False),
])
def test_receiver_nominal_qualification_requires_native_owner_and_descriptor(tmp_path, name, source, descriptor, nominal):
    facts, function, wrapper = extracted_wrapper(tmp_path, "mp_obj_t selected(mp_obj_t value) { return value; }")
    definitions = connected_framework_fixture()
    owner = wrapper_type_source("0", name=name, physical=source)
    native_wrapper = wrapper_type_source("MP_TYPE_FLAG_BINDS_SELF | MP_TYPE_FLAG_BUILTIN_FUN")
    shape = {**wrapper["callshape"], "descriptor": descriptor}
    receiver = _native_receiver_contract(owner, shape, native_wrapper,
                                         checked_receiver_authority(definitions), definitions, Path("/fixture"))
    assert (receiver["nominal_authority_resolution"] == "bounded-resolved") is nominal
    symbol = bind_wrapper(facts, function, wrapper, offset=1, receiver=receiver)
    tree, declaration = emitted_native_method(symbol)
    receiver_argument = (declaration.args.posonlyargs + declaration.args.args)[0]
    assert annotation_name(receiver_argument.annotation, tree) == ("str" if nominal else "typing.Any")
    assert annotation_name(declaration.returns, tree) == ("str" if nominal else "typing.Any")


def test_selected_surface_publishes_native_receiver_and_original_c_declarations(tmp_path):
    source = HANDLERS.replace(
        "uint8_t slot_index_attr;\n    const void *slots[1];",
        "uint8_t slot_index_attr, slot_index_locals_dict;\n    const void *slots[2];")
    source = source.replace("static const mp_obj_type_t sample = {", """
#define MP_ROM_QSTR(value) (((mp_rom_obj_t)(value) << 3) | 2)
typedef struct { mp_rom_obj_t key, value; } mp_rom_map_elem_t;
typedef struct { unsigned used; const mp_rom_map_elem_t *table; } fixture_map_t;
typedef struct { mp_obj_base_t base; fixture_map_t map; } mp_obj_dict_t;
static const mp_rom_map_elem_t sample_table[] = {
    { MP_ROM_QSTR(MP_QSTR_method), MP_ROM_PTR(&method_obj) },
};
static const mp_obj_dict_t sample_locals = {
    .base = { &mp_type_type }, .map = { .used = 1, .table = sample_table },
};
static const mp_obj_type_t sample = {""")
    source = source.replace(
        ".slot_index_attr = 1, .slots = { (const void *)sample_attr },",
        ".slot_index_attr = 1, .slot_index_locals_dict = 2, .slots = { (const void *)sample_attr, &sample_locals },")
    facts, raw, resolve, present = linked_c(tmp_path, source, "py/runtime.c")
    _, types, _ = _normal_surface(raw, [facts], resolve, present, tmp_path)
    sample = next(item for item in types if item["name"] == "Sample")
    symbol = next(item for item in sample["members"] if item["name"] == "method")
    assert symbol["receiver_contract"]["native_type_identity"] == sample["identity"]
    assert symbol["receiver_contract"]["nominal_authority_resolution"] == "unresolved"
    function = next(item for item in facts["functions"] if item["name"] == "method")
    declarations = function["parameter_source_contract"]["declarations"]
    assert declarations[0]["name"] == "self_in"
    returned = symbol["semantics"]["returns"][0]
    assert returned["selected_receiver_binding"]["binding_authority"]["parameter_source_contract"]["declarations"] == declarations
    assert function["semantics"]["diagnostic_stage"] == "translation-unit-extraction"
    assert symbol["semantics"]["diagnostic_stage"] == "selected-linked-semantics"
    tree, declaration = emitted_native_method(symbol)
    receiver_argument = (declaration.args.posonlyargs + declaration.args.args)[0]
    assert annotation_name(receiver_argument.annotation, tree) == "typing.Any"
    assert annotation_name(declaration.returns, tree) == "typing.Any"



def test_python_keyword_qstr_retains_runtime_name_without_invalid_declaration(tmp_path):
    body = """enum { MP_QSTR_class = 20 };
mp_obj_t selected(size_t n_args, const mp_obj_t *pos_args, mp_map_t *kw_args) {
    static const mp_arg_t allowed[] = {
        { MP_QSTR_class, MP_ARG_OBJ | MP_ARG_KW_ONLY, { .u_rom_obj = mp_const_none } },
    };
    mp_arg_val_t vals[1];
    mp_arg_parse_all(n_args, pos_args, kw_args, 1, allowed, vals);
    return mp_const_none;
}"""
    facts, function, wrapper = extracted_wrapper(tmp_path, body, fixed=False, minimum=0, maximum=1)
    symbol = bind_wrapper(facts, function, wrapper)
    before = copy.deepcopy(symbol["callshape"]["parameters"])
    assert before[0]["name"] == "class"
    _, tree, declaration = emitted(symbol)
    assert declaration.args.vararg and declaration.args.kwarg
    assert annotation_name(declaration.returns, tree) == "None"
    assert symbol["callshape"]["parameters"] == before
    assert symbol["resolution"]["declaration"]["status"] == "unresolved"


def test_keyword_only_first_row_limits_entire_table_to_keywords(tmp_path):
    body = """mp_obj_t selected(size_t n_args, const mp_obj_t *pos_args, mp_map_t *kw_args) {
        static const mp_arg_t allowed[] = {
            { MP_QSTR_filter, MP_ARG_OBJ | MP_ARG_KW_ONLY, { .u_rom_obj = mp_const_none } },
            { MP_QSTR_mode, MP_ARG_INT, { .u_int = 17 } },
        };
        mp_arg_val_t vals[2];
        mp_arg_parse_all(n_args, pos_args, kw_args, 2, allowed, vals);
        return mp_const_none;
    }"""
    facts, function, wrapper = extracted_wrapper(tmp_path, body, fixed=False, minimum=0, maximum=2)
    symbol = bind_wrapper(facts, function, wrapper)
    parameters = copy.deepcopy(symbol["callshape"]["parameters"])
    assert [parameter["kind"] for parameter in parameters] == ["keyword_only", "keyword_only"]
    _, tree, declaration = emitted(symbol)
    assert not declaration.args.args and not declaration.args.posonlyargs
    assert declaration.args.vararg is None and declaration.args.kwarg is None
    assert [parameter.arg for parameter in declaration.args.kwonlyargs] == ["filter", "mode"]
    assert annotation_name(declaration.returns, tree) == "None"
    assert symbol["callshape"]["parameters"] == parameters
    runtime_source = tmp_path / "keyword_runtime.c"
    runtime_source.write_text(r'''
#include "fixture.c"
#include <setjmp.h>
const int mp_type_type = 0, mp_type_fun_builtin_var = 0, none_obj = 1;
static jmp_buf argument_error;
void mp_arg_error_terse_mismatch(void) { longjmp(argument_error, 1); }
mp_map_elem_t *mp_map_lookup(mp_map_t *map, mp_obj_t key, unsigned mode) {
    assert(mode == MP_MAP_LOOKUP);
    for (size_t i = 0; i < map->used; ++i) {
        if (map->table[i].key == key) { return &map->table[i]; }
    }
    return NULL;
}
_Bool mp_obj_is_true(mp_obj_t value) { return value != 0; }
int mp_obj_get_int(mp_obj_t value) { return (int)value; }
int probe_positional_count(size_t count) {
    const mp_obj_t positional[] = { 0, 0 };
    mp_map_t keywords = { 0 };
    if (setjmp(argument_error)) { return 1; }
    selected(count, positional, &keywords);
    return 0;
}
''')
    library = tmp_path / "keyword_runtime.so"
    subprocess.run(["gcc", "-std=gnu99", "-shared", "-fPIC", "-O0", "-I", str(tmp_path),
                    str(runtime_source), "-o", str(library)], check=True, capture_output=True, text=True)
    probe = ctypes.CDLL(str(library)).probe_positional_count
    probe.argtypes = [ctypes.c_size_t]
    probe.restype = ctypes.c_int
    assert probe(0) == 0
    assert probe(1) == 1 and probe(2) == 1


def test_surplus_checks_before_selected_parser_loop_cannot_preserve_callshape(tmp_path):
    parser = argument_parser_source()
    surplus_start = parser.index("    if (pos_found < n_pos)")
    body_end = parser.rfind("\n}")
    surplus = parser[surplus_start:body_end]
    parser = parser[:surplus_start] + parser[body_end:]
    parser = parser.replace("    for (size_t i", surplus + "\n    for (size_t i", 1)
    facts, function, wrapper = extracted_wrapper(
        tmp_path, shifted_body(), fixed=False, parser=parser, minimum=1, maximum=3)
    symbol = bind_wrapper(facts, function, wrapper, offset=1)
    assert symbol["callshape"]["parameters_resolution"] == "unresolved"
    _, tree, declaration = emitted(symbol)
    assert declaration.args.vararg and declaration.args.kwarg
    assert annotation_name(declaration.returns, tree) == "bool"


@pytest.mark.parametrize("prototype,consumer", [
    ("void validate(mp_arg_val_t *);", "validate(vals);"),
    ("void validate(mp_arg_val_t);", "validate(vals[0]);"),
    ("void validate(mp_arg_val_t *);", "validate(&vals[0]);"),
    ("void validate(mp_arg_val_t *);", "mp_arg_val_t *alias = vals; validate(alias);"),
])
def test_unknown_parsed_object_carrier_consumer_blocks_whole_domain_with_independent_none(tmp_path, prototype, consumer):
    body = prototype + """
mp_obj_t selected(size_t n_args, const mp_obj_t *pos_args, mp_map_t *kw_args) {
    static const mp_arg_t allowed[] = {
        { MP_QSTR_filter, MP_ARG_OBJ | MP_ARG_KW_ONLY, { .u_rom_obj = mp_const_none } },
    };
    mp_arg_val_t vals[1];
    mp_arg_parse_all(n_args, pos_args, kw_args, 1, allowed, vals);
    """ + consumer + """
    return mp_const_none;
}"""
    facts, function, wrapper = extracted_wrapper(tmp_path, body, fixed=False, minimum=0, maximum=1)
    symbol = bind_wrapper(facts, function, wrapper)
    parameter = symbol["callshape"]["parameters"][0]
    assert parameter["type_contract"] is None
    assert parameter["type_resolution"] == "unresolved"
    _, tree, declaration = emitted(symbol)
    assert annotation_name(declaration.args.kwonlyargs[0].annotation, tree) == "typing.Any"
    assert annotation_name(declaration.returns, tree) == "None"


@pytest.mark.parametrize("field,index,prototype", [
    ("u_int", 1, "void validate(int);"),
    ("u_bool", 2, "void validate(_Bool);"),
])
def test_converted_scalar_consumer_does_not_constrain_other_object_row(tmp_path, field, index, prototype):
    body = prototype + """
mp_obj_t selected(size_t n_args, const mp_obj_t *pos_args, mp_map_t *kw_args) {
    static const mp_arg_t allowed[] = {
        { MP_QSTR_filter, MP_ARG_OBJ | MP_ARG_KW_ONLY, { .u_rom_obj = mp_const_none } },
        { MP_QSTR_mode, MP_ARG_INT | MP_ARG_KW_ONLY, { .u_int = 17 } },
        { MP_QSTR_self, MP_ARG_BOOL | MP_ARG_KW_ONLY, { .u_bool = 0 } },
    };
    mp_arg_val_t vals[3];
    mp_arg_parse_all(n_args, pos_args, kw_args, 3, allowed, vals);
    validate(vals[""" + str(index) + """].""" + field + """);
    return mp_const_none;
}"""
    facts, function, wrapper = extracted_wrapper(tmp_path, body, fixed=False, minimum=0, maximum=3)
    symbol = bind_wrapper(facts, function, wrapper)
    _, tree, declaration = emitted(symbol)
    assert annotation_name(declaration.args.kwonlyargs[0].annotation, tree) == "object"
    assert annotation_name(declaration.returns, tree) == "None"
    assert symbol["callshape"]["parameters"][1]["result_storage"]["kind"] == "int"
    assert symbol["callshape"]["parameters"][2]["result_storage"]["kind"] == "bool"


@pytest.mark.parametrize("consumer,annotations", [
    ("size_t index = choose_index(); mp_arg_val_t *alias = &vals[index]; validate(alias->u_obj);",
     ["typing.Any", "typing.Any"]),
    ("size_t offset = choose_index(); validate((vals + offset)->u_obj);",
     ["typing.Any", "typing.Any"]),
    ("validate((vals + 1)->u_obj);", ["object", "typing.Any"]),
    ("mp_arg_val_t *alias = &vals[1]; validate(alias->u_obj);", ["object", "typing.Any"]),
    ("opaque_bytes((char *)vals + 1);", ["typing.Any", "typing.Any"]),
    ("validate(vals[0].u_obj + 1);", ["typing.Any", "object"]),
    ("int SLOT = 1; validate(vals[SLOT].u_obj);", ["typing.Any", "typing.Any"]),
])
def test_two_object_parser_rows_distinguish_unknown_and_literal_carrier_offsets(tmp_path, consumer, annotations):
    body = """enum { SLOT = 0 };
size_t choose_index(void);
void validate(mp_obj_t);
void opaque_bytes(char *);
mp_obj_t selected(size_t n_args, const mp_obj_t *pos_args, mp_map_t *kw_args) {
    static const mp_arg_t allowed[] = {
        { MP_QSTR_filter, MP_ARG_OBJ | MP_ARG_KW_ONLY, { .u_rom_obj = mp_const_none } },
        { MP_QSTR_mode, MP_ARG_OBJ | MP_ARG_KW_ONLY, { .u_rom_obj = mp_const_none } },
    };
    mp_arg_val_t vals[2];
    mp_arg_parse_all(n_args, pos_args, kw_args, 2, allowed, vals);
    """ + consumer + """
    return mp_const_none;
}"""
    facts, function, wrapper = extracted_wrapper(tmp_path, body, fixed=False, minimum=0, maximum=2)
    symbol = bind_wrapper(facts, function, wrapper)
    _, tree, declaration = emitted(symbol)
    assert [annotation_name(parameter.annotation, tree) for parameter in declaration.args.kwonlyargs] == annotations
    assert annotation_name(declaration.returns, tree) == "None"


@pytest.mark.parametrize("parameter_shadow,returns_field", [(False, True), (True, False), (True, True)])
def test_parser_index_shadowing_file_enum_does_not_invent_row_zero_identity(tmp_path, parameter_shadow, returns_field):
    count = "SLOT" if parameter_shadow else "n_args"
    body = """enum { SLOT = 0 };
void validate(mp_obj_t);
mp_obj_t selected(size_t """ + count + """, const mp_obj_t *pos_args, mp_map_t *kw_args) {
    static const mp_arg_t allowed[] = {
        { MP_QSTR_filter, MP_ARG_OBJ | MP_ARG_KW_ONLY, { .u_rom_obj = mp_const_none } },
        { MP_QSTR_mode, MP_ARG_OBJ | MP_ARG_KW_ONLY, { .u_rom_obj = mp_const_none } },
    };
    mp_arg_val_t vals[2];
    mp_arg_parse_all(""" + count + """, pos_args, kw_args, 2, allowed, vals);
    """ + ("" if parameter_shadow else "int SLOT = 1;") + """
    """ + ("return vals[SLOT].u_obj;" if returns_field else "validate(vals[SLOT].u_obj); return mp_const_none;") + """
}"""
    facts, function, wrapper = extracted_wrapper(tmp_path, body, fixed=False, minimum=0, maximum=2)
    symbol = bind_wrapper(facts, function, wrapper)
    _, tree, declaration = emitted(symbol)
    if returns_field:
        assert annotation_name(declaration.returns, tree) == "typing.Any"
    else:
        assert [annotation_name(parameter.annotation, tree) for parameter in declaration.args.kwonlyargs] == ["typing.Any", "typing.Any"]
        assert annotation_name(declaration.returns, tree) == "None"


def test_parser_shift_shadowing_file_enum_cannot_drop_required_public_argument(tmp_path):
    body = "enum { SHIFT = 1 };\n" + shifted_body("n_args - SHIFT", "pos_args + SHIFT")
    body = body.replace("    static const mp_arg_t", "    int SHIFT = 2;\n    static const mp_arg_t", 1)
    facts, function, wrapper = extracted_wrapper(tmp_path, body, fixed=False, minimum=2, maximum=4)
    symbol = bind_wrapper(facts, function, wrapper, offset=1)
    assert symbol["callshape"]["parameters_resolution"] == "unresolved"
    _, tree, declaration = emitted(symbol)
    assert declaration.args.vararg and declaration.args.kwarg
    assert not declaration.args.kwonlyargs
    assert annotation_name(declaration.returns, tree) == "bool"


def test_parser_count_shadowing_file_enum_cannot_publish_unparsed_table_row(tmp_path):
    body = "enum { COUNT = 2 };\n" + shifted_body("n_args", "pos_args", "COUNT")
    body = body.replace("    static const mp_arg_t", "    int COUNT = 1;\n    static const mp_arg_t", 1)
    body = body.replace("return mp_obj_new_bool(vals[1].u_bool);", "return mp_const_none;")
    facts, function, wrapper = extracted_wrapper(tmp_path, body, fixed=False, minimum=0, maximum=2)
    symbol = bind_wrapper(facts, function, wrapper)
    assert symbol["callshape"]["parameters_resolution"] == "unresolved"
    _, tree, declaration = emitted(symbol)
    assert declaration.args.vararg and declaration.args.kwarg
    assert not declaration.args.kwonlyargs
    assert annotation_name(declaration.returns, tree) == "None"


def test_visible_file_enum_parser_shift_preserves_required_public_prefix(tmp_path):
    body = "enum { SHIFT = 2 };\n" + shifted_body("n_args - SHIFT", "pos_args + SHIFT")
    facts, function, wrapper = extracted_wrapper(tmp_path, body, fixed=False, minimum=2, maximum=4)
    symbol = bind_wrapper(facts, function, wrapper, offset=1)
    parameters = symbol["callshape"]["parameters"]
    assert [parameter["name"] for parameter in parameters] == ["arg0", "mode", "filter"]
    assert parameters[0]["required"] is True
    _, tree, declaration = emitted(symbol)
    assert [parameter.arg for parameter in declaration.args.posonlyargs] == ["arg0"]
    assert [parameter.arg for parameter in declaration.args.args] == ["mode"]
    assert [parameter.arg for parameter in declaration.args.kwonlyargs] == ["filter"]
    assert annotation_name(declaration.returns, tree) == "bool"


def test_visible_file_enum_parser_count_keeps_complete_named_table(tmp_path):
    body = "enum { COUNT = 2 };\n" + shifted_body("n_args", "pos_args", "COUNT")
    facts, function, wrapper = extracted_wrapper(tmp_path, body, fixed=False, minimum=0, maximum=2)
    symbol = bind_wrapper(facts, function, wrapper)
    _, tree, declaration = emitted(symbol)
    assert declaration.args.vararg is None and declaration.args.kwarg is None
    assert [parameter.arg for parameter in declaration.args.args] == ["mode"]
    assert [parameter.arg for parameter in declaration.args.kwonlyargs] == ["filter"]
    assert annotation_name(declaration.returns, tree) == "bool"


def test_parser_argument_authority_uses_call_point_not_unrelated_later_assignments(tmp_path):
    body = shifted_body().replace("return mp_obj_new_bool(vals[1].u_bool);",
                                  "n_args = 0; pos_args = 0; kw_args = 0; return mp_obj_new_bool(vals[1].u_bool);")
    facts, function, wrapper = extracted_wrapper(tmp_path, body, fixed=False, minimum=1, maximum=3)
    symbol = bind_wrapper(facts, function, wrapper, offset=1)
    _, tree, declaration = emitted(symbol)
    assert declaration.args.vararg is None and declaration.args.kwarg is None
    assert [parameter.arg for parameter in declaration.args.args] == ["mode"]
    assert [parameter.arg for parameter in declaration.args.kwonlyargs] == ["filter"]
    assert annotation_name(declaration.returns, tree) == "bool"


def test_parser_sibling_out_argument_escape_cannot_certify_unchanged_count(tmp_path):
    body = "mp_arg_val_t *escape_count(size_t *, mp_arg_val_t *);\n" + shifted_body()
    body = body.replace("kw_args, 2, allowed, vals);", "kw_args, 2, allowed, escape_count(&n_args, vals));")
    facts, function, wrapper = extracted_wrapper(tmp_path, body, fixed=False, minimum=1, maximum=3)
    symbol = bind_wrapper(facts, function, wrapper, offset=1)
    assert symbol["callshape"]["parameters_resolution"] == "unresolved"
    _, tree, declaration = emitted(symbol)
    assert declaration.args.vararg and declaration.args.kwarg
    assert annotation_name(declaration.returns, tree) == "bool"


def emitted_native_method(symbol, owner_name="Native"):
    inventory = {"schema_version": 1, "modules": [{"name": "fixture", "identity": "fixture-module", "exports": [
        {"name": owner_name, "kind": "class", "identity": "native-export", "type_identity": "native-source-type",
         "resolution": {"availability": {"status": "resolved"}}, "members": [symbol]},
    ]}]}
    tree = ast.parse(render_declarations(inventory)["modules/fixture.pyi"])
    owner = next(node for node in tree.body if isinstance(node, ast.ClassDef))
    method = next(node for node in owner.body if isinstance(node, ast.FunctionDef))
    return tree, method


def source_receiver_contract(wrapper, checked):
    definitions = connected_framework_fixture()
    owner = wrapper_type_source("0", name="mp_type_str", physical="/fixture/py/objstr.c")
    native_wrapper = wrapper_type_source("MP_TYPE_FLAG_BINDS_SELF | MP_TYPE_FLAG_BUILTIN_FUN" if checked else "MP_TYPE_FLAG_BINDS_SELF")
    return _native_receiver_contract(owner, wrapper["callshape"], native_wrapper,
                                     checked_receiver_authority(definitions), definitions, Path("/fixture"))


@pytest.mark.parametrize("body,result", [
    ("return mp_obj_new_list(0, 0);", "list"),
    ("return unknown(value);", "typing.Any"),
])
@pytest.mark.parametrize("checked", [False, True])
def test_native_receiver_annotation_is_independent_of_list_or_unknown_return(tmp_path, body, result, checked):
    facts, function, wrapper = extracted_wrapper(
        tmp_path, "mp_obj_t unknown(mp_obj_t);\nmp_obj_t selected(mp_obj_t value) {" + body + "}")
    receiver = source_receiver_contract(wrapper, checked)
    symbol = bind_wrapper(facts, function, wrapper, offset=1, receiver=receiver)
    tree, method = emitted_native_method(symbol)
    arguments = method.args.posonlyargs + method.args.args
    assert len(arguments) == 1
    assert annotation_name(arguments[0].annotation, tree) == ("str" if checked else "typing.Any")
    assert annotation_name(method.returns, tree) == result
    assert not method.decorator_list
    executable = ast.Module(body=[copy.deepcopy(next(node for node in tree.body if isinstance(node, ast.ClassDef)))], type_ignores=[])
    executable_method = executable.body[0].body[0]
    executable_method.returns = None
    for parameter in executable_method.args.posonlyargs + executable_method.args.args:
        parameter.annotation = None
    namespace = {}
    exec(compile(ast.fix_missing_locations(executable), "<emitted-native-arity>", "exec"), namespace)
    native = namespace["Native"]
    inspect.signature(native.selected).bind(object())
    inspect.signature(native().selected).bind()
    with pytest.raises(TypeError):
        inspect.signature(native.selected).bind()
    with pytest.raises(TypeError):
        inspect.signature(native().selected).bind(object())


@pytest.mark.parametrize("fallback", [False, True])
def test_unknown_native_receiver_annotation_survives_name_collision_and_parser_gap(tmp_path, fallback):
    body = """mp_obj_t selected(size_t n_args, const mp_obj_t *pos_args, mp_map_t *kw_args) {
        static const mp_arg_t allowed[] = {
            { MP_QSTR_self, MP_ARG_OBJ | MP_ARG_KW_ONLY, { .u_rom_obj = mp_const_none } },
        };
        mp_arg_val_t vals[1];
        mp_arg_parse_all(n_args - 1, pos_args + 1, kw_args, 1, allowed, vals);
        return mp_obj_new_list(0, 0);
    }"""
    parser = argument_parser_source().replace("i < n_allowed", "i < n_allowed - 1") if fallback else None
    facts, function, wrapper = extracted_wrapper(tmp_path, body, fixed=False, minimum=1, maximum=2, parser=parser)
    symbol = bind_wrapper(facts, function, wrapper, offset=1, receiver=source_receiver_contract(wrapper, False))
    tree, method = emitted_native_method(symbol)
    receiver = (method.args.posonlyargs + method.args.args)[0]
    assert annotation_name(receiver.annotation, tree) == "typing.Any"
    assert annotation_name(method.returns, tree) == "list"
    assert not method.decorator_list
    if fallback:
        assert method.args.vararg and method.args.kwarg
    else:
        assert receiver.arg == "_self"
        assert [parameter.arg for parameter in method.args.kwonlyargs] == ["self"]


def test_unknown_native_receiver_any_import_is_safe_for_native_class_alias(tmp_path):
    facts, function, wrapper = extracted_wrapper(
        tmp_path, "mp_obj_t selected(mp_obj_t value) { return mp_obj_new_list(0, 0); }")
    symbol = bind_wrapper(facts, function, wrapper, offset=1, receiver=source_receiver_contract(wrapper, False))
    tree, method = emitted_native_method(symbol, owner_name="Any")
    receiver = (method.args.posonlyargs + method.args.args)[0]
    assert annotation_name(receiver.annotation, tree) == "typing.Any"
    assert receiver.annotation.id != "Any"


@pytest.mark.parametrize("cast,parameter,storage,qualified,signature,accepted", [
    ("mp_call_fun_t", "size_t", "const void *", True, "ordinary", True),
    ("mp_call_fun_t", "int", "const void *", True, "ordinary", False),
    ("mp_uint_t", "size_t", "const void *", True, "ordinary", False),
    ("mp_call_fun_t", "size_t", "uintptr_t", True, "ordinary", False),
    ("mp_call_fun_t", "size_t", "const void *", False, "ordinary", False),
    ("mp_call_fun_t", "size_t", "const void *", True, "variadic", False),
    ("mp_call_fun_t", "size_t", "const void *", True, "ms_abi", False),
    ("mp_call_fun_t", "size_t", "const void *", True, "typedef_ms_abi", False),
    ("mp_call_fun_t", "size_t", "const void *", True, "typedef_ms_abi_suffix", False),
    (None, "size_t", "const void *", True, "ordinary", False),
    ("bare", "size_t", "const void *", True, "ordinary", False),
])
def test_selected_typed_call_slot_requires_actual_storage_typedef_and_function_abi(tmp_path, cast, parameter, storage, qualified, signature, accepted):
    py = tmp_path / "py"
    py.mkdir()
    header = """#include <stdint.h>
    #include <stddef.h>
    typedef uintptr_t mp_obj_t;
    typedef uintptr_t mp_uint_t;
    typedef struct { const void *type; } mp_obj_base_t;
    typedef struct {
        mp_obj_base_t base; uint16_t flags, name; uint8_t slot_index_call;
        """ + storage + """ slots[1];
    } mp_obj_type_t;
    extern const mp_obj_type_t mp_type_type;
    """
    typedef = "typedef mp_obj_t (*mp_call_fun_t)(mp_obj_t self_in, size_t n_args, size_t n_kw, const mp_obj_t *args);\n"
    if signature == "typedef_ms_abi":
        typedef = typedef.replace("typedef mp_obj_t", "typedef __attribute__((ms_abi)) mp_obj_t")
    elif signature == "typedef_ms_abi_suffix":
        typedef = typedef.replace(");", ") __attribute__((ms_abi));")
    prefix = "static __attribute__((ms_abi)) mp_obj_t" if signature == "ms_abi" else "static mp_obj_t"
    suffix = ", ..." if signature == "variadic" else ""
    initializer = ("(const void *)(" + cast + ") checked_fun_call" if cast not in (None, "bare")
                   else "(const void *) checked_fun_call" if cast is None else "checked_fun_call")
    (py / "obj.h").write_text(header + (typedef if qualified else ""))
    body = """#include "obj.h"
    """ + ("" if qualified else typedef) + """
    """ + prefix + """ checked_fun_call(mp_obj_t self_in, """ + parameter + """ n_args, size_t n_kw, const mp_obj_t *args""" + suffix + """) {
        return self_in;
    }
    static const mp_obj_type_t mp_type_checked_fun = {
        .base = { .type = &mp_type_type }, .flags = 0, .name = 1,
        .slot_index_call = 1, .slots = { """ + initializer + """ },
    };"""
    facts, _ = extract_c(tmp_path, body, filename="py/runtime.c")
    owner = next(definition for definition in facts["definitions"] if definition["name"] == "mp_type_checked_fun")
    function = next(function for function in facts["functions"] if function["name"] == "checked_fun_call")
    binding = _call_slot_binding(facts, owner, owner["slots"][0])
    assert bool(binding and _call_slot_abi_matches(function, binding)) is accepted
    if accepted:
        assert binding["function"] == function["name"]
        assert binding["typedef_abi"]["source_span"]["source"] == str(py / "obj.h")
        assert function["source_span"]["source"] == str(py / "runtime.c")


@pytest.mark.parametrize("mutation,qualified", [
    (None, True),
    ("opaque_vector", True),
    ("standalone", False),
    ("computed", False),
    ("parser_field", False),
    ("branch", False),
    ("source", False),
])
def test_verbose_parser_terminal_literal_payload_preserves_public_contracts(tmp_path, mutation, qualified):
    payload_header = "unqualified_payload.h" if mutation == "source" else "misc.h"
    parser = '#undef MICROPY_ERROR_REPORTING\n#define MICROPY_ERROR_REPORTING 1\n#include "' + payload_header + '"\n' + argument_parser_source()
    if mutation == "standalone":
        parser = parser.replace("size_t pos_found = 0, kws_found = 0;",
                                'size_t pos_found = 0, kws_found = 0;\nMP_COMPRESSED_ROM_TEXT("not an error argument");')
    elif mutation == "computed":
        parser = parser.replace("MP_ERROR_TEXT(\"'%q' argument required\")",
                                "MP_ERROR_TEXT((const char *)pos[0])")
    elif mutation == "parser_field":
        parser = parser.replace("MP_ERROR_TEXT(\"'%q' argument required\")",
                                "MP_ERROR_TEXT((const char *)allowed[i].qst)")
    elif mutation == "branch":
        parser = parser.replace('mp_raise_TypeError(MP_ERROR_TEXT("extra keyword arguments given"));',
                                'if (allowed[0].qst) { mp_raise_TypeError(MP_ERROR_TEXT("extra keyword arguments given")); }')
    prepare_headers(tmp_path, parser=parser)
    py = tmp_path / "py"
    (py / payload_header).write_text("const char *MP_COMPRESSED_ROM_TEXT(const char *);\n")
    runtime = py / "runtime.h"
    runtime.write_text(runtime.read_text() + """
    typedef const char *mp_rom_error_text_t;
    #define MP_ERROR_TEXT(text) ((mp_rom_error_text_t)MP_COMPRESSED_ROM_TEXT(text))
    extern const int mp_type_TypeError;
    void mp_raise_msg_varg(const void *, const char *, ...);
    void mp_raise_TypeError(const char *);
    """)
    body = """#include "py/runtime.h"
    #include "py/argcheck.c"
    mp_obj_t selected(size_t n_args, const mp_obj_t *pos_args, mp_map_t *kw_args) {
        static const mp_arg_t allowed[] = {
            { MP_QSTR_self, MP_ARG_OBJ | MP_ARG_REQUIRED },
            { MP_QSTR_mode, MP_ARG_INT, { .u_int = 17 } },
        };
        mp_arg_val_t vals[2];
        mp_arg_parse_all(n_args, pos_args, kw_args, 2, allowed, vals);
        return vals[0].u_obj;
    }
    const mp_obj_fun_builtin_var_t selected_obj = {
        .base = { &mp_type_fun_builtin_var }, .sig = MP_OBJ_FUN_MAKE_SIG(1, 2, 1), .fun = { .kw = selected },
    };"""
    if mutation == "opaque_vector":
        body = body.replace('    #include "py/argcheck.c"',
                            '    #include "py/argcheck.c"\nvoid opaque_vector(const mp_obj_t *);')
        body = body.replace("mp_arg_parse_all(n_args, pos_args, kw_args, 2, allowed, vals);",
                            "mp_arg_parse_all(n_args, pos_args, kw_args, 2, allowed, vals); opaque_vector(pos_args);")
    facts, _ = extract_c(tmp_path, body)
    function = next(item for item in facts["functions"] if item["name"] == "selected")
    wrapper = next(item for item in facts["definitions"] if item["name"] == "selected_obj")
    symbol = bind_wrapper(facts, function, wrapper)
    text, tree, declaration = emitted(symbol)
    diagnostic_path = tmp_path / "verbose-parser-contracts.json"
    diagnostic_path.write_text(json.dumps({
        "mutation": mutation, "raw_semantics": function["semantics"], "public_symbol": symbol,
        "source_returns": function["events"]["returns"], "emitted": text,
    }, indent=2, sort_keys=True) + "\n")
    if qualified:
        assert symbol["callshape"]["parameters_resolution"] == "resolved"
        public_parameter = symbol["semantics"]["public_parameters"][0]
        assert public_parameter["name"] == "self"
        if mutation == "opaque_vector":
            assert public_parameter["accepted_domain"]["contract"] is None, str(diagnostic_path)
            assert public_parameter["accepted_domain"]["resolution"] == "unresolved"
        else:
            assert public_parameter["accepted_domain"]["contract"] == {"kind": "object"}, str(diagnostic_path)
            assert public_parameter["accepted_domain"]["resolution"] == "bounded-resolved"
        assert symbol["semantics"]["return_resolution"] == "bounded-resolved", str(diagnostic_path)
        returns = symbol["semantics"]["returns"]
        assert returns and all(contract.get("kind") == "argument" and contract.get("exported_argument") == "self"
                               and contract.get("source_ref", {}).get("kind") == "parser-field"
                               and contract["source_ref"].get("table_entry_index") == 0
                               and contract["source_ref"].get("storage_field") == "u_obj"
                               for contract in returns), str(diagnostic_path)
        parameters = declaration.args.posonlyargs + declaration.args.args
        assert [parameter.arg for parameter in parameters] == ["self", "mode"]
        assert [parameter.arg for parameter in declaration.args.posonlyargs] == ["self"]
        assert [parameter.arg for parameter in declaration.args.args] == ["mode"]
        assert declaration.args.vararg is None and declaration.args.kwarg is None
        assert [ast.literal_eval(default) for default in declaration.args.defaults] == [17]
        assert annotation_name(parameters[0].annotation, tree) == annotation_name(declaration.returns, tree)
        if mutation == "opaque_vector":
            assert annotation_name(parameters[0].annotation, tree) == "typing.Any"
            assert not any(isinstance(node, ast.Assign) and isinstance(node.value, ast.Call)
                           and annotation_name(node.value.func, tree) == "typing.TypeVar" for node in tree.body)
            vector_uses = [use for use in function["semantics"]["input_uses"]
                           if use.get("helper") == "opaque_vector"]
            parser_uses = [use for use in function["semantics"]["input_uses"]
                           if use.get("helper") == "mp_arg_parse_all" and use.get("operand") == 1]
            assert vector_uses and vector_uses[0]["resolution"] == "unresolved"
            assert vector_uses[0]["source_span"] == parser_uses[0]["source_span"]
        else:
            typevar = next(node for node in tree.body if isinstance(node, ast.Assign)
                           and any(isinstance(target, ast.Name) and target.id == declaration.returns.id for target in node.targets))
            assert annotation_name(typevar.value.func, tree) == "typing.TypeVar"
        executable = copy.deepcopy(declaration)
        executable.returns = None
        for parameter in executable.args.posonlyargs + executable.args.args:
            parameter.annotation = None
        namespace = {}
        exec(compile(ast.fix_missing_locations(ast.Module(body=[executable], type_ignores=[])),
                     "<emitted-parser-signature>", "exec"), namespace)
        signature = inspect.signature(namespace[declaration.name])
        bound = signature.bind(object())
        bound.apply_defaults()
        assert bound.arguments["mode"] == 17
        assert signature.bind(object(), 23).arguments["mode"] == 23
        assert signature.bind(object(), mode=23).arguments["mode"] == 23
        with pytest.raises(TypeError):
            signature.bind()
        with pytest.raises(TypeError):
            signature.bind(self=object())
        with pytest.raises(TypeError):
            signature.bind(object(), 23, 29)
    else:
        assert symbol["callshape"]["parameters_resolution"] == "unresolved"
        assert declaration.args.vararg and declaration.args.kwarg


@pytest.mark.parametrize("runtime_builtin,native_flags,macros,accepted", [
    ("0x40", "MP_TYPE_FLAG_BINDS_SELF | MP_TYPE_FLAG_BUILTIN_FUN", True, True),
    ("0x80", "MP_TYPE_FLAG_BINDS_SELF | MP_TYPE_FLAG_BUILTIN_FUN", True, False),
    ("0x40", "MP_TYPE_FLAG_BINDS_SELF", True, False),
    ("0x40", "MP_TYPE_FLAG_BINDS_SELF | MP_TYPE_FLAG_BUILTIN_FUN", False, True),
])
def test_wrapper_receiver_flags_require_selected_compile_tu_probe_agreement(tmp_path, runtime_builtin, native_flags, macros, accepted):
    py = tmp_path / "py"
    py.mkdir()
    (py / "obj.h").write_text("""#include <stdint.h>
    typedef struct { const void *type; } mp_obj_base_t;
    typedef struct {
        mp_obj_base_t base; uint16_t flags, name; uint8_t slot_index_call; const void *slots[1];
    } mp_obj_type_t;
    extern const mp_obj_type_t mp_type_type;
    """)
    def flags(builtin):
        return ("#define MP_TYPE_FLAG_BINDS_SELF 0x20\n#define MP_TYPE_FLAG_BUILTIN_FUN " + builtin + "\n"
                if macros else "enum { MP_TYPE_FLAG_BINDS_SELF = 0x20, MP_TYPE_FLAG_BUILTIN_FUN = " + builtin + " };\n")
    native_source = flags("0x40") + """#include "obj.h"
    void fun_builtin_1_call(void) {}
    const mp_obj_type_t mp_type_fun_builtin_1 = {
        .base = { .type = &mp_type_type }, .name = 1, .flags = """ + native_flags + """,
        .slot_index_call = 1, .slots = { (const void *)fun_builtin_1_call },
    };"""
    runtime_source = flags(runtime_builtin) + """#include "obj.h"
    int mp_convert_member_lookup(unsigned value) {
        return (value & MP_TYPE_FLAG_BINDS_SELF) && (value & MP_TYPE_FLAG_BUILTIN_FUN);
    }"""
    native_facts, _ = extract_c(tmp_path, native_source, filename="py/objfun.c")
    runtime_facts, _ = extract_c(tmp_path, runtime_source, filename="py/runtime.c")
    native = next(definition for definition in native_facts["definitions"] if definition["name"] == "mp_type_fun_builtin_1")
    runtime = next(function for function in runtime_facts["functions"] if function["name"] == "mp_convert_member_lookup")
    receiver_definitions = {"mp_convert_member_lookup": {
        "function": runtime, "authority": {"contracts": runtime_facts["contracts"]},
    }}
    authority = _checked_wrapper_type_authority(native, receiver_definitions, native_facts["contracts"])
    assert bool(authority) is accepted
    if accepted:
        assert authority["masks"] == authority["lookup_masks"]
        assert authority["flags"] & authority["masks"]["MP_TYPE_FLAG_BINDS_SELF"]
        assert authority["flags"] & authority["masks"]["MP_TYPE_FLAG_BUILTIN_FUN"]


@pytest.mark.parametrize("modifiers,allocated,static_path,accepted", [
    ("static inline", False, False, True),
    ("", False, False, False),
    ("static inline __attribute__((ms_abi))", False, False, False),
    ("extern", False, False, False),
    ("inline", False, False, False),
    ("static", False, False, False),
    ("static", False, True, True),
    ("", True, False, True),
])
def test_header_tag_helper_needs_actual_inline_or_selected_body_ownership(tmp_path, modifiers, allocated, static_path, accepted):
    py = tmp_path / "py"
    py.mkdir()
    (py / "obj.h").write_text("""#include <stdint.h>
    #include <stdbool.h>
    typedef uintptr_t mp_obj_t;
    """ + modifiers + """ bool mp_obj_is_obj(mp_obj_t value) { return (value & 7) == 0; }
    """)
    facts, _ = extract_c(tmp_path, """#include "obj.h"
    bool mp_obj_get_type(mp_obj_t value) { return mp_obj_is_obj(value); }
    """, filename="py/obj.c")
    function = next(function for function in facts["functions"] if function["name"] == "mp_obj_is_obj")
    getter = next(function for function in facts["functions"] if function["name"] == "mp_obj_get_type")
    caller = {"function": getter, "authority": {"translation_unit": facts["translation_unit"]}}
    present = lambda _facts, declaration: declaration["name"] == ("mp_obj_is_obj" if allocated else "mp_obj_get_type") if static_path or allocated else False
    linked = _linked_functions([facts], present) if static_path else {}
    authority = _header_helper_authority(facts, function, caller, linked, present)
    assert bool(authority) is accepted
    if accepted:
        assert authority["header_declaration_identity"] == function["declaration_identity"]
        assert authority["selected_caller_identity"] == getter["id"]
        if not allocated and not static_path:
            assert "inline_declaration" in authority
        if static_path:
            assert authority["allocated_caller_authority"]["allocated_function"] == getter["id"]


@pytest.mark.parametrize("modifiers,terminal,accepted", [
    ("inline __attribute__((always_inline))", True, True),
    ("inline", True, False),
    ("", True, False),
    ("inline __attribute__((always_inline))", False, False),
])
def test_forced_external_inline_codec_authority_is_terminal_literal_scope_only(tmp_path, modifiers, terminal, accepted):
    py = tmp_path / "py"
    py.mkdir()
    (py / "misc.h").write_text(modifiers + ' const char *MP_COMPRESSED_ROM_TEXT(const char *msg) { return msg; }\n')
    facts, _ = extract_c(tmp_path, """#include "misc.h"
    const char *checked_fun_call(void) { return MP_COMPRESSED_ROM_TEXT("error"); }
    """, filename="py/runtime.c")
    function = next(function for function in facts["functions"] if function["name"] == "MP_COMPRESSED_ROM_TEXT")
    checked = next(function for function in facts["functions"] if function["name"] == "checked_fun_call")
    caller = {"function": checked, "authority": {"translation_unit": facts["translation_unit"]}}
    authority = _header_helper_authority(facts, function, caller, {}, lambda *_: False, terminal_literal=terminal)
    assert bool(authority) is accepted
    if accepted:
        assert authority["header_declaration_identity"] == function["declaration_identity"]
        assert authority["selected_caller_identity"] == checked["id"]
