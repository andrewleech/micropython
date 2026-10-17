"""GNU syntax must not manufacture callable semantics or reorder native arguments."""
from mpy_api.frontend import syntax_adapter
from mpy_api.semantics import semantics


def parsed(source):
    physical = "/fixture/gnu.c"
    origins = [(physical, line) for line in range(1, len(source.splitlines()) + 1)]
    declarations, functions, errors, evidence = syntax_adapter(
        source, origins, physical, {"dependencies": {physical: "fixture"}})
    assert not errors
    return declarations, functions, evidence


def test_variadic_intrinsic_body_does_not_claim_passthrough():
    _, functions, _ = parsed("""typedef void *mp_obj_t;
mp_obj_t retain(__builtin_va_list args, mp_obj_t value) {
    (void)__builtin_va_arg(args, unsigned long);
    return value;
}
""")
    function = next(item for item in functions if item["name"] == "retain")
    contract = semantics(function, {"retain": [function]}, [], {})
    assert contract["input_resolution"] == "unresolved"
    assert contract["return_resolution"] == "unresolved"
    assert contract["returns"] == []
    assert contract["constructor_calls"] == []
    assert contract["return_unresolved"]


def test_legacy_declaration_order_does_not_reorder_native_parameters():
    _, functions, _ = parsed("""void *select_value(first, second)
    int second;
    void *first;
{
    return first;
}
""")
    function = next(item for item in functions if item["name"] == "select_value")
    assert function["parameters"] == ["first", "second"]


def test_post_array_section_attribute_keeps_initializer_ownership():
    declarations, _, _ = parsed("""extern void handler(void);
const void *vectors[] __attribute__((section(".vectors"))) = { &handler };
""")
    table = next(item for item in declarations if item["name"] == "vectors")
    assert table["initializer_row_count"] == 1
    assert table["initializer_rows"][0]["expression"] == "& handler"
    assert table["array_dimensions"][0]["expression"] == ""
