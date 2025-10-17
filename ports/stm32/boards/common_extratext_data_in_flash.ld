/* This linker script fragment is intended to be included in SECTIONS. */

/* For C++ exception handling */
.ARM :
{
    __exidx_start = .;
    *(.ARM.exidx*)
    __exidx_end = .;
} >FLASH_COMMON

/* Used by the start-up code to initialise data */
_sidata = LOADADDR(.data);

/* Initialised data section, start-up code will copy it from flash to RAM */
.data :
{
    . = ALIGN(4);
    _sdata = .;
    *(.data*)

    /* Zephyr iterable sections with static initializers */
    . = ALIGN(4);
    _net_buf_pool_list_start = .;
    KEEP(*(SORT(._net_buf_pool.static.*)));
    _net_buf_pool_list_end = .;

    . = ALIGN(4);
    _bt_conn_cb_list_start = .;
    KEEP(*(SORT(._bt_conn_cb.static.*)));
    _bt_conn_cb_list_end = .;

    . = ALIGN(4);
    _bt_gatt_service_static_list_start = .;
    KEEP(*(SORT(._bt_gatt_service_static.static.*)));
    _bt_gatt_service_static_list_end = .;

    . = ALIGN(4);
    _bt_l2cap_fixed_chan_list_start = .;
    KEEP(*(SORT(._bt_l2cap_fixed_chan.static.*)));
    _bt_l2cap_fixed_chan_list_end = .;

    . = ALIGN(4);
    _settings_handler_static_list_start = .;
    KEEP(*(SORT(._settings_handler_static.static.*)));
    _settings_handler_static_list_end = .;

    . = ALIGN(4);
    _edata = .;
} >RAM AT> FLASH_COMMON
