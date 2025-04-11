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
#include "py/mperrno.h"
#include "py/gc.h"

#if MICROPY_PY_USBIP

#include <string.h>
#include "extmod/usbip.h"

usbip_global_state_t usbip_state;

void usbip_glue_init(void) {
    memset(&usbip_state, 0, sizeof(usbip_state));
    // Explicitly NULL out the pointer array
    for (int i = 0; i <= MAX_USB_DEVICES; ++i) {
        for (int j = 0; j < MAX_USB_ENDPOINTS; ++j) {
            usbip_state.pending_transfers[i][j] = NULL;
        }
    }
}

// --- Device Tracking ---

void usbip_add_device(uint8_t dev_addr, uint16_t vid, uint16_t pid) {
    // Check if already exists
    if (usbip_find_device(dev_addr)) {
        mp_printf(MP_PYTHON_PRINTER, "USBIP Glue: Device %u already added.\n", dev_addr);
        return;
    }

    // Allocate new device entry
    usbip_host_device_t *new_dev = gc_alloc(sizeof(usbip_host_device_t), 0);
    if (!new_dev) {
        mp_printf(MP_PYTHON_PRINTER, "USBIP Glue: Failed to allocate memory for device %u.\n", dev_addr);
        // Or raise MP_ENOMEM?
        return;
    }

    memset(new_dev, 0, sizeof(usbip_host_device_t));
    new_dev->dev_addr = dev_addr;
    new_dev->vid = vid;
    new_dev->pid = pid;
    new_dev->attached = false;

    // Add to head of list (simple approach)
    // TODO: Add locking if multi-threaded
    new_dev->next = usbip_state.host_devices;
    usbip_state.host_devices = new_dev;

    mp_printf(MP_PYTHON_PRINTER, "USBIP Glue: Added device %u (VID: %04X, PID: %04X)\n", dev_addr, vid, pid);
}

void usbip_remove_device(uint8_t dev_addr) {
    // TODO: Add locking if multi-threaded
    usbip_host_device_t *prev = NULL;
    usbip_host_device_t *curr = usbip_state.host_devices;

    while (curr) {
        if (curr->dev_addr == dev_addr) {
            if (prev) {
                prev->next = curr->next;
            } else {
                usbip_state.host_devices = curr->next;
            }
            mp_printf(MP_PYTHON_PRINTER, "USBIP Glue: Removed device %u\n", dev_addr);
            // TODO: Notify any attached client?
            gc_free(curr);
            return;
        }
        prev = curr;
        curr = curr->next;
    }
    mp_printf(MP_PYTHON_PRINTER, "USBIP Glue: Device %u not found for removal.\n", dev_addr);
}

usbip_host_device_t *usbip_find_device(uint8_t dev_addr) {
    // TODO: Add locking if multi-threaded
    usbip_host_device_t *curr = usbip_state.host_devices;
    while (curr) {
        if (curr->dev_addr == dev_addr) {
            return curr;
        }
        curr = curr->next;
    }
    return NULL;
}

// --- Client Tracking ---

usbip_client_state_t *usbip_add_client(struct tcp_pcb *pcb) {
    usbip_client_state_t *new_client = gc_alloc(sizeof(usbip_client_state_t), 0);
    if (!new_client) {
        mp_printf(MP_PYTHON_PRINTER, "USBIP Glue: Failed to allocate memory for client.\n");
        return NULL;
    }

    memset(new_client, 0, sizeof(usbip_client_state_t));
    new_client->pcb = pcb;
    new_client->remote_ip = pcb->remote_ip;
    new_client->remote_port = pcb->remote_port;
    new_client->attached_dev_addr = 0; // Not attached initially
    new_client->recv_buf = gc_alloc(USBIP_RECV_BUF_SIZE, 0);
    if (!new_client->recv_buf) {
        mp_printf(MP_PYTHON_PRINTER, "USBIP Glue: Failed to allocate recv buffer for client.\n");
        gc_free(new_client);
        return NULL;
    }
    new_client->recv_buf_size = USBIP_RECV_BUF_SIZE;
    new_client->recv_data_len = 0;

    // Add to head of list
    // TODO: Add locking if multi-threaded
    new_client->next = usbip_state.clients;
    usbip_state.clients = new_client;

    mp_printf(MP_PYTHON_PRINTER, "USBIP Glue: Added client %s:%u\n",
        ipaddr_ntoa(&new_client->remote_ip), new_client->remote_port);

    return new_client;
}

void usbip_remove_client(usbip_client_state_t *client) {
    if (!client) {
        return;
    }

    // TODO: Add locking if multi-threaded
    usbip_client_state_t *prev = NULL;
    usbip_client_state_t *curr = usbip_state.clients;

    while (curr) {
        if (curr == client) {
            if (prev) {
                prev->next = curr->next;
            } else {
                usbip_state.clients = curr->next;
            }
            mp_printf(MP_PYTHON_PRINTER, "USBIP Glue: Removed client %s:%u\n",
                ipaddr_ntoa(&client->remote_ip), client->remote_port);

            // If the client was attached to a device, mark the device as unattached
            if (client->attached_dev_addr != 0) {
                usbip_host_device_t *dev = usbip_find_device(client->attached_dev_addr);
                if (dev) {
                    dev->attached = false;
                    mp_printf(MP_PYTHON_PRINTER, "USBIP Glue: Marked device %u as unattached.\n", dev->dev_addr);
                }
            }

            // PCB should be closed by the caller in LWIP context
            gc_free(client->recv_buf);
            gc_free(client);
            return;
        }
        prev = curr;
        curr = curr->next;
    }
    mp_printf(MP_PYTHON_PRINTER, "USBIP Glue: Client %s:%u not found for removal.\n",
        ipaddr_ntoa(&client->remote_ip), client->remote_port);
}


#endif // MICROPY_PY_USBIP

// --- Transfer Tracking ---

static inline uint8_t ep_addr_to_index(uint8_t ep_addr) {
    // Map endpoint address (0x00-0x0F for OUT, 0x80-0x8F for IN)
    // to an index 0-31.
    return (ep_addr & 0x0F) | ((ep_addr & TUSB_DIR_IN_MASK) ? 0x10 : 0x00);
}

static inline uint8_t ep_index_to_addr(uint8_t index) {
    return (index & 0x0F) | ((index & 0x10) ? TUSB_DIR_IN_MASK : 0);
}

bool usbip_glue_add_transfer(uint8_t dev_addr, uint8_t ep_addr, usbip_transfer_context_t *context) {
    if (dev_addr > MAX_USB_DEVICES) {
        return false;
    }
    uint8_t index = ep_addr_to_index(ep_addr);
    if (index >= MAX_USB_ENDPOINTS) {
        return false;
    }

    // TODO: Add locking if multi-threaded
    if (usbip_state.pending_transfers[dev_addr][index] != NULL) {
        // Already a pending transfer on this endpoint
        mp_printf(MP_PYTHON_PRINTER, "USBIP Glue: Transfer collision on dev %u ep %02X\n", dev_addr, ep_addr);
        return false;
    }
    usbip_state.pending_transfers[dev_addr][index] = context;
    return true;
}

usbip_transfer_context_t *usbip_glue_find_remove_transfer(uint8_t dev_addr, uint8_t ep_addr) {
    if (dev_addr > MAX_USB_DEVICES) {
        return NULL;
    }
    uint8_t index = ep_addr_to_index(ep_addr);
    if (index >= MAX_USB_ENDPOINTS) {
        return NULL;
    }

    // TODO: Add locking if multi-threaded
    usbip_transfer_context_t *context = usbip_state.pending_transfers[dev_addr][index];
    usbip_state.pending_transfers[dev_addr][index] = NULL;
    return context;
}

// Clean up transfers associated with a disconnecting client
void usbip_glue_cleanup_transfers_for_client(usbip_client_state_t *client) {
    if (!client) {
        return;
    }

    mp_printf(MP_PYTHON_PRINTER, "USBIP Glue: Cleanup transfers for client %s:%u\n",
        ipaddr_ntoa(&client->remote_ip), client->remote_port);

    // Iterate through all devices and endpoints
    for (int dev_addr = 0; dev_addr <= MAX_USB_DEVICES; ++dev_addr) {
        for (int index = 0; index < MAX_USB_ENDPOINTS; ++index) {
            usbip_transfer_context_t *ctx = usbip_state.pending_transfers[dev_addr][index];
            if (ctx && ctx->client == client) {
                mp_printf(MP_PYTHON_PRINTER, "  - Cleaning up transfer for dev %d, ep_idx %d (seq %lu)\n",
                    dev_addr, index, ctx->seqnum);
                // TODO: Abort the actual TinyUSB transfer if possible/needed?
                // tuh_control_abort(dev_addr)? Requires endpoint address reconstruction.
                gc_free(ctx->in_buffer);
                gc_free(ctx);
                usbip_state.pending_transfers[dev_addr][index] = NULL;
            }
        }
    }
}

// Clean up transfers associated with a disconnecting device
void usbip_glue_cleanup_transfers_for_device(uint8_t dev_addr) {
    if (dev_addr > MAX_USB_DEVICES) {
        return;
    }
    // TODO: Implement - Iterate through pending_transfers[dev_addr] and cancel/free contexts
    mp_printf(MP_PYTHON_PRINTER, "USBIP Glue: Cleanup transfers for device %u (Not Implemented)\n", dev_addr);
    for (int i = 0; i < MAX_USB_ENDPOINTS; ++i) {
        if (usbip_state.pending_transfers[dev_addr][i] != NULL) {
            gc_free(usbip_state.pending_transfers[dev_addr][i]->in_buffer); // Free potential IN buffer
            gc_free(usbip_state.pending_transfers[dev_addr][i]);
            usbip_state.pending_transfers[dev_addr][i] = NULL;
        }
    }
}
