/*
 * Wrapper for Zephyr hci_driver.c that adds cooperative poll_rx function.
 *
 * hci_driver.c's static sem_recv, recv_fifo, node_rx_recv(), and
 * process_node() are inaccessible from outside the translation unit.
 * We compile hci_driver.c as part of this TU so we can provide
 * hci_driver_poll_rx() without patching the Zephyr submodule.
 *
 * This file REPLACES hci_driver.c in the build — do not compile both.
 */

#include "lib/zephyr/subsys/bluetooth/controller/hci/hci_driver.c"

/* Cooperative polling: process one recv_fifo node per call.
 * Called from the main loop when running without real Zephyr threads
 * (e.g. MicroPython's cooperative BLE integration).
 * Returns true if more nodes are pending, false if recv_fifo is empty.
 * Caller should interleave work processing between calls to match
 * full Zephyr's threading model.
 */
bool hci_driver_poll_rx(const struct device *dev) {
    const struct hci_driver_data *data = dev->data;

    /* Process controller RX — moves PDUs from LL into recv_fifo
     * and sends completed-event notifications directly via bt_recv.
     */
    #if !defined(CONFIG_BT_CTLR_RX_PRIO_STACK_SIZE)
    if (k_sem_count_get(&sem_recv) > 0) {
        k_sem_take(&sem_recv, K_NO_WAIT);
        node_rx_recv(dev);
    }
    #endif /* !CONFIG_BT_CTLR_RX_PRIO_STACK_SIZE */

    /* Process ONE recv_fifo node */
    struct node_rx_pdu *node_rx = k_fifo_get(&recv_fifo, K_NO_WAIT);

    if (node_rx == NULL) {
        return false;
    }

    struct net_buf *buf = process_node(node_rx);

    while (buf) {
        struct net_buf *frag = net_buf_ref(buf);

        buf = net_buf_frag_del(NULL, buf);

        if (frag->len > 1) {
            data->recv(dev, frag);
        } else {
            net_buf_unref(frag);
        }
    }

    /* Return true if more nodes pending */
    return !k_fifo_is_empty(&recv_fifo);
}
