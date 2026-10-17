"""Consumer-visible argument names and ordinals from C aggregate declarations."""
from mpy_api.frontend import syntax_adapter
from mpy_api.semantics import argument_table_rows


def table_rows(source):
    physical = "/fixture/args.c"
    origins = [(physical, line) for line in range(1, len(source.splitlines()) + 1)]
    _, functions, errors, _ = syntax_adapter(
        source, origins, physical, {"dependencies": {physical: "fixture"}})
    assert not errors
    function = next(item for item in functions if item["name"] == "configure")
    variable = next(item for item in function["events"]["variables"] if item["name"] == "allowed")
    environment = {"MP_ARG_INT": 2, "MP_ARG_REQUIRED": 256, "MP_ARG_KW_ONLY": 512}
    return list(argument_table_rows(variable, function, environment, {}))


def test_required_row_without_default_preserves_following_parameter_ordinals():
    rows = table_rows("""void configure(void) {
    static const mp_arg_t allowed[] = {
        { MP_QSTR_bitrate, MP_ARG_INT | MP_ARG_REQUIRED },
        { MP_QSTR_mode, MP_ARG_INT, { .u_int = 0 } },
        { MP_QSTR_filter, MP_ARG_INT | MP_ARG_KW_ONLY, { .u_int = 1 } },
    };
}
""")
    assert [(row["name"], row["table_entry_index"]) for row in rows] == [
        ("bitrate", 0), ("mode", 1), ("filter", 2)]
    assert rows[0]["default_omitted"] is True
    assert rows[0]["default_storage_contract"]["kind"] == "zero-initialized-aggregate-storage"
    assert all(row["row_resolution"] == "resolved" for row in rows)
    assert all(row["row_count"] == 3 and row["table_count_resolution"] == "resolved" for row in rows)


def test_declared_extent_does_not_bless_partial_parameter_table():
    rows = table_rows("""void configure(void) {
    static const mp_arg_t allowed[3] = {
        { MP_QSTR_bitrate, MP_ARG_INT | MP_ARG_REQUIRED },
        { MP_QSTR_mode, MP_ARG_INT, { .u_int = 0 } },
    };
}
""")
    assert [(row["name"], row["table_entry_index"]) for row in rows] == [("bitrate", 0), ("mode", 1)]
    assert all(row["row_count"] == 3 and row["table_count_resolution"] == "unresolved" for row in rows)
