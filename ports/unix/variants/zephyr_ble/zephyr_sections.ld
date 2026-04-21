/*
 * Linker script fragment for Zephyr BLE iterable sections on Unix/Linux.
 *
 * On embedded targets, the main linker script collects STRUCT_SECTION_ITERABLE
 * objects (e.g. net_buf_pool) into contiguous arrays bounded by start/end
 * symbols.  On Unix, GCC places these in custom ELF sections automatically
 * but doesn't provide boundary symbols.  This fragment collects them and
 * defines the symbols that the Zephyr host code expects.
 */

SECTIONS
{
    ._net_buf_pool_list : {
        PROVIDE(_net_buf_pool_list_start = .);
        KEEP(*(SORT_BY_NAME(._net_buf_pool.static.*)))
        PROVIDE(_net_buf_pool_list_end = .);
    }
}
INSERT AFTER .data;
