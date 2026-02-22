/*
 * Zephyr BLE iterable sections for nRF port
 *
 * Adds Zephyr BLE struct registration sections after .text.
 * Uses INSERT AFTER to extend the base common.ld without modifying it.
 */

SECTIONS
{
    .zephyr_ble_rodata :
    {
        /* Net buffer pools (registered via NET_BUF_POOL_DEFINE) */
        _net_buf_pool_list_start = .;
        KEEP(*(SORT(._net_buf_pool.static.*)));
        _net_buf_pool_list_end = .;

        /* BT connection callbacks (registered via BT_CONN_CB_DEFINE) */
        _bt_conn_cb_list_start = .;
        KEEP(*(SORT(._bt_conn_cb.static.*)));
        _bt_conn_cb_list_end = .;

        /* Static GATT services (registered via BT_GATT_SERVICE_DEFINE) */
        _bt_gatt_service_static_list_start = .;
        KEEP(*(SORT(._bt_gatt_service_static.static.*)));
        _bt_gatt_service_static_list_end = .;

        /* L2CAP fixed channels (registered via BT_L2CAP_CHANNEL_DEFINE) */
        _bt_l2cap_fixed_chan_list_start = .;
        KEEP(*(SORT(._bt_l2cap_fixed_chan.static.*)));
        _bt_l2cap_fixed_chan_list_end = .;

        /* L2CAP BR fixed channels */
        _bt_l2cap_br_fixed_chan_list_start = .;
        KEEP(*(SORT(._bt_l2cap_br_fixed_chan.static.*)));
        _bt_l2cap_br_fixed_chan_list_end = .;
    }
} INSERT AFTER .text;
