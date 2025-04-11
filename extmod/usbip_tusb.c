/*
 * This file is part of the MicroPython project, http://micropython.org/
 *
 * The MIT License (MIT)
 *
 * Copyright (c) 2025 Your Name
 *
 * Permission is hereby granted, free of charge, to any person obtaining a copy
 * of this software and associated documentation files (the "Software"), to deal
 * in the Software without restriction, including without limitation the rights
 * to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
 * copies of the Software, and to permit persons to whom the Software is
 * furnished to do so, subject to the following conditions:
 *
 * The above copyright notice and this permission notice shall be included in
 * all copies or substantial portions of the Software.
 *
 * THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
 * IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
 * FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
 * AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
 * LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
 * OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
 * THE SOFTWARE.
 */

#include "py/runtime.h"

#if MICROPY_PY_USBIP && CFG_TUH_ENABLED // Depends on TinyUSB Host

#include "extmod/usbip.h"
#include "tusb.h" // TinyUSB Host API

// --- Forward Declarations for Callbacks ---
static void usbip_tusb_init(void);
static uint16_t usbip_tusb_open(uint8_t rhport, uint8_t dev_addr, tusb_desc_interface_t const *desc_intf, uint16_t max_len);
static bool usbip_tusb_set_config(uint8_t dev_addr, uint8_t itf_num);
static bool usbip_tusb_xfer_cb(uint8_t dev_addr, uint8_t ep_addr, xfer_result_t result, uint32_t xferred_bytes);
static void usbip_tusb_close(uint8_t dev_addr);

// --- TinyUSB Application Driver Structure ---

#if CFG_TUH_APPLICATION_DRIVER
const tuh_driver_t usbip_driver = {
    .init = usbip_tusb_init,
    .open = usbip_tusb_open,
    .set_config = usbip_tusb_set_config,
    .xfer_cb = usbip_tusb_xfer_cb,
    .close = usbip_tusb_close
};
#endif // CFG_TUH_APPLICATION_DRIVER

// --- TinyUSB Application Driver Callbacks Implementation ---

// This is a conceptual structure. The actual way to register might differ.
// We might need to modify how MicroPython sets up `tuh_driver_mount_t` or
// provide a function pointer via `usbh_app_driver_get_cb` if that hook exists.

static void usbip_tusb_init(void) {
    // Called once at TinyUSB host initialization
    mp_printf(MP_PYTHON_PRINTER, "USBIP TUSB Init (Placeholder)\n");
}

// Example `open` callback (adjust signature as needed)
static uint16_t usbip_tusb_open(uint8_t rhport, uint8_t dev_addr, tusb_desc_interface_t const *desc_intf, uint16_t max_len) {
    (void)rhport;
    (void)max_len;

    // Get VID/PID - needed for DEVLIST and potentially filtering
    uint16_t vid, pid;
    bool vid_pid_ok = tuh_vid_pid_get(dev_addr, &vid, &pid);

    // Add device to our tracking list (if not already added)
    // This might get called multiple times per device (once per interface)
    // usbip_add_device handles duplicates.
    if (vid_pid_ok) {
        usbip_add_device(dev_addr, vid, pid);
    } else {
        mp_printf(MP_PYTHON_PRINTER, "USBIP TUSB Open: Failed to get VID/PID for dev %u\n", dev_addr);
        // Decide if we still want to track it? Maybe add with VID/PID 0?
    }

    mp_printf(MP_PYTHON_PRINTER, "USBIP TUSB Open: dev_addr=%u, intf=%u (VID: %04X, PID: %04X)\n",
        dev_addr, desc_intf->bInterfaceNumber, vid, pid);

    // TODO: Add filtering logic here if we only want to handle specific devices/interfaces.

    // Claim the interface by returning its descriptor size.
    return sizeof(tusb_desc_interface_t);
}

// Example `set_config` callback (adjust signature as needed)
static bool usbip_tusb_set_config(uint8_t dev_addr, uint8_t itf_num) {
    mp_printf(MP_PYTHON_PRINTER, "USBIP TUSB Set Config: dev_addr=%u, itf=%u (Placeholder)\n", dev_addr, itf_num);
    // TODO: Handle configuration changes if necessary
    return true; // Indicate success
}

// Example `xfer_cb` callback (adjust signature as needed)
static bool usbip_tusb_xfer_cb(uint8_t dev_addr, uint8_t ep_addr, xfer_result_t result, uint32_t xferred_bytes) {
    mp_printf(MP_PYTHON_PRINTER, "USBIP TUSB Xfer CB: dev_addr=%u, ep=0x%02X, result=%d, bytes=%lu\n",
        dev_addr, ep_addr, result, xferred_bytes);

    // Find the corresponding transfer context
    usbip_transfer_context_t *context = usbip_glue_find_remove_transfer(dev_addr, ep_addr);
    if (!context) {
        mp_printf(MP_PYTHON_PRINTER, "USBIP Xfer CB Error: No pending context found for dev %u, ep %02X\n",
            dev_addr, ep_addr);
        return true; // Cannot proceed without context, but ack callback handling
    }

    usbip_client_state_t *client = context->client;
    if (!client || !client->pcb) {
        mp_printf(MP_PYTHON_PRINTER, "USBIP Xfer CB Error: Client or PCB is invalid in context (seq=%lu)\n", context->seqnum);
        gc_free(context->in_buffer); // Clean up allocated IN buffer
        gc_free(context);
        return true;
    }

    // Prepare OP_RET_SUBMIT response
    uint32_t response_payload_len = 0;
    uint8_t *response_payload_ptr = NULL;
    int32_t usbip_status = 0;

    // Translate TinyUSB result to USBIP status (simple mapping for now)
    switch (result) {
        case XFER_RESULT_SUCCESS:
            usbip_status = USBIP_ST_OK;
            if ((ep_addr & TUSB_DIR_IN_MASK) && context->in_buffer && xferred_bytes > 0) {
                // Transfer was IN, include received data
                response_payload_len = xferred_bytes;
                response_payload_ptr = context->in_buffer;
            }
            break;
        case XFER_RESULT_STALLED:
            usbip_status = -MP_EPIPE; // Using negative errno for STALL
            break;
        case XFER_RESULT_TIMEOUT:    // Placeholder mapping
            usbip_status = -MP_ETIMEDOUT;
            break;
        case XFER_RESULT_FAILED:
        default:
            usbip_status = -MP_EIO;     // Generic I/O error
            break;
    }

    uint32_t response_header_len = sizeof(struct usbip_header_ret_submit);
    uint32_t response_total_len = response_header_len + response_payload_len;

    mp_printf(MP_PYTHON_PRINTER, "USBIP Xfer CB: Sending RET_SUBMIT for seq %lu, status %ld, len %lu\n",
        context->seqnum, usbip_status, response_payload_len);

    // Check send buffer space
    if (tcp_sndbuf(client->pcb) < response_total_len) {
        mp_printf(MP_PYTHON_PRINTER, "USBIP Xfer CB Error: RET_SUBMIT response too large for buffer (%lu > %u)\n",
            response_total_len, tcp_sndbuf(client->pcb));
        // What to do here? Lost response. Maybe close connection?
        gc_free(context->in_buffer);
        gc_free(context);
        return true; // Ack callback handling, but failed to send response
    }

    // Allocate buffer for header
    uint8_t *header_buffer = gc_alloc(response_header_len, 0);
    if (!header_buffer) {
        mp_printf(MP_PYTHON_PRINTER, "USBIP Xfer CB Error: Failed to allocate header buffer\n");
        gc_free(context->in_buffer);
        gc_free(context);
        return true;
    }

    // Fill response header
    struct usbip_header_ret_submit *res_hdr = (struct usbip_header_ret_submit *)header_buffer;
    memset(res_hdr, 0, response_header_len);
    res_hdr->command = lwip_htonl(USBIP_RET_SUBMIT);
    res_hdr->seqnum = lwip_htonl(context->seqnum);
    res_hdr->devid = lwip_htonl(((uint32_t)dev_addr)); // Assuming bus 0 for now
    res_hdr->direction = lwip_htonl((ep_addr & TUSB_DIR_IN_MASK) ? TUSB_DIR_IN : TUSB_DIR_OUT);
    res_hdr->ep = lwip_htonl(ep_addr & 0x0F);
    res_hdr->status = lwip_htonl(usbip_status);
    res_hdr->actual_length = lwip_htonl(xferred_bytes);
    // TODO: Fill ISO fields if needed

    // Send header (using COPY flag)
    err_t err = tcp_write(client->pcb, header_buffer, response_header_len, TCP_WRITE_FLAG_COPY);
    gc_free(header_buffer); // Free header buffer immediately after copy

    if (err != ERR_OK) {
        mp_printf(MP_PYTHON_PRINTER, "USBIP Xfer CB Error: tcp_write failed for header (%d)\n", err);
        gc_free(context->in_buffer);
        gc_free(context);
        return true;
    }

    // Send payload (IN data) if necessary
    if (response_payload_len > 0 && response_payload_ptr) {
        // Use NO_COPY if possible and buffer management allows, COPY is safer/simpler
        err = tcp_write(client->pcb, response_payload_ptr, response_payload_len, TCP_WRITE_FLAG_COPY);
        if (err != ERR_OK) {
            mp_printf(MP_PYTHON_PRINTER, "USBIP Xfer CB Error: tcp_write failed for payload (%d)\n", err);
            // Header might have been sent, but payload failed. Connection state unclear.
        }
    }

    // Try to push data out
    err = tcp_output(client->pcb);
    if (err != ERR_OK) {
        mp_printf(MP_PYTHON_PRINTER, "USBIP Xfer CB Warn: tcp_output failed (%d)\n", err);
    }

    // Clean up context and potential IN buffer
    gc_free(context->in_buffer);
    gc_free(context);

    return true; // Indicate success in handling callback
}

// Example `close` callback (adjust signature as needed)
static void usbip_tusb_close(uint8_t dev_addr) {
    mp_printf(MP_PYTHON_PRINTER, "USBIP TUSB Close: dev_addr=%u\n", dev_addr);

    // Clean up any pending transfers for this device
    usbip_glue_cleanup_transfers_for_device(dev_addr);

    // Remove device from our tracking list
    usbip_remove_device(dev_addr);

    // TODO: Notify the usbip_server so it can inform any attached client.
    // Maybe find the client attached to this dev_addr and close its connection?
}


// --- Registration Hook ---

// This function is expected by TinyUSB when CFG_TUH_APPLICATION_DRIVER is enabled.
// It should return a pointer to the driver structure that handles the interface.
const tuh_driver_t *usbh_app_driver_get_cb(uint8_t dev_addr, tusb_desc_interface_t const *desc_intf) {
    (void)dev_addr;

    // TODO: Add logic here to decide if this specific interface should be handled by USBIP.
    // For now, we unconditionally return our driver for *any* interface.
    // A real implementation might check desc_intf->bInterfaceClass, VID/PID etc.

    mp_printf(MP_PYTHON_PRINTER, "USBIP App Driver Get CB: dev_addr=%u, intf=%u -> Using USBIP Driver\n", dev_addr, desc_intf->bInterfaceNumber);
    return &usbip_driver;
}

#endif // MICROPY_PY_USBIP && CFG_TUH_ENABLED
