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

#if MICROPY_PY_USBIP && MICROPY_PY_LWIP // Depends on LWIP

#include "extmod/usbip.h"
#include "lwip/tcp.h"
#include "lwip/err.h"
#include "lwip/pbuf.h"
#include "lwip/netif.h"
#include "shared/netutils/netutils.h"
#include <stdlib.h>
#include <stdio.h>

#define USBIP_PORT 3240

static struct tcp_pcb *usbip_listen_pcb = NULL;

static void usbip_cleanup_client(usbip_client_state_t *client, struct tcp_pcb *pcb);
static err_t usbip_recv_cb(void *arg, struct tcp_pcb *pcb, struct pbuf *p, err_t err);
static err_t usbip_sent_cb(void *arg, struct tcp_pcb *pcb, u16_t len);
static void usbip_err_cb(void *arg, err_t err);

// Placeholder accept callback
static err_t usbip_accept_cb(void *arg, struct tcp_pcb *newpcb, err_t err) {
    (void)arg;
    if (err != ERR_OK || newpcb == NULL) {
        mp_printf(MP_PYTHON_PRINTER, "USBIP Accept Error: %d\n", err);
        return ERR_VAL;
    }

    mp_printf(MP_PYTHON_PRINTER, "USBIP Client Connected from %s:%u\n",
        ipaddr_ntoa(&newpcb->remote_ip), newpcb->remote_port);

    // Allocate state structure for this client
    usbip_client_state_t *client_state = usbip_add_client(newpcb);
    if (client_state == NULL) {
        mp_printf(MP_PYTHON_PRINTER, "USBIP Accept Error: Failed to allocate client state\n");
        // Close the connection immediately
        tcp_recv(newpcb, NULL); // Deregister recv callback
        tcp_err(newpcb, NULL); // Deregister error callback
        tcp_abort(newpcb);     // Abort connection
        return ERR_MEM;
    }

    // Pass the client state structure as the callback argument for this PCB
    tcp_arg(newpcb, client_state);

    // Set up callbacks for the new connection
    tcp_recv(newpcb, usbip_recv_cb);
    tcp_sent(newpcb, usbip_sent_cb);
    tcp_err(newpcb, usbip_err_cb);

    // Reduce priority? (Optional)
    // tcp_setprio(newpcb, TCP_PRIO_MIN);

    return ERR_OK; // Accept the connection
}
static err_t usbip_handle_op_req_devlist(usbip_client_state_t *client, struct tcp_pcb *pcb, const uint8_t *data, uint16_t len);
static err_t usbip_handle_op_req_import(usbip_client_state_t *client, struct tcp_pcb *pcb, const uint8_t *data, uint16_t len);
static err_t usbip_handle_cmd_submit(usbip_client_state_t *client, struct tcp_pcb *pcb, const uint8_t *data, uint16_t len);
static err_t usbip_handle_cmd_unlink(usbip_client_state_t *client, struct tcp_pcb *pcb, const uint8_t *data, uint16_t len);
static err_t usbip_send_status_response(usbip_client_state_t *client, uint16_t command_ret_code, uint32_t seqnum, int32_t status);

// Callback for when data is received from a client
static err_t usbip_recv_cb(void *arg, struct tcp_pcb *pcb, struct pbuf *p, err_t err) {
    usbip_client_state_t *client = (usbip_client_state_t *)arg;

    // If pbuf is NULL, connection has been closed by remote end
    if (p == NULL) {
        mp_printf(MP_PYTHON_PRINTER, "USBIP Client %s:%u disconnected.\n",
            ipaddr_ntoa(&pcb->remote_ip), pcb->remote_port);
        usbip_cleanup_client(client, pcb);
        return ERR_OK; // Connection closed, we are done
    }

    // If error or no client state, something went wrong
    if (err != ERR_OK || client == NULL) {
        mp_printf(MP_PYTHON_PRINTER, "USBIP Recv Error: %d, client=%p\n", err, client);
        if (p != NULL) {
            pbuf_free(p);
        }
        // Don't clean up here, let err_cb handle it if needed
        return err;
    }

    mp_printf(MP_PYTHON_PRINTER, "USBIP Recv: %d bytes from %s:%u\n", p->tot_len,
        ipaddr_ntoa(&pcb->remote_ip), pcb->remote_port);

    // TODO: Implement USBIP message parsing here.
    // - Check if we have enough data for a header.
    // - If so, parse header (command, sequence number, devid etc.).
    // - Check if we have enough data for the full command payload.
    // - Handle commands: OP_REQ_DEVLIST, OP_REQ_IMPORT, OP_CMD_SUBMIT...
    // - This might involve buffering partial messages in client_state.

    // Acknowledge reception of the data
    tcp_recved(pcb, p->tot_len);

    // Process received data (pbuf chain)
    struct pbuf *current_pbuf = p;
    uint16_t bytes_processed = 0;
    while (current_pbuf != NULL) {
        // TODO: Add partial message buffering here using client_state buffer.
        // For now, assume a command fits within a single pbuf segment.

        if (current_pbuf->len >= sizeof(struct usbip_header_op_basic)) {
            struct usbip_header_op_basic *hdr = (struct usbip_header_op_basic *)current_pbuf->payload;
            uint16_t cmd_code = lwip_ntohs(hdr->command_code);

            mp_printf(MP_PYTHON_PRINTER, "USBIP Recv: Command %04X\n", cmd_code);

            if (cmd_code == USBIP_OP_REQ_DEVLIST) {
                // Ensure enough data for the command (just the header here)
                if (current_pbuf->len >= sizeof(struct usbip_header_op_basic)) {
                    err_t send_err = usbip_handle_op_req_devlist(client, pcb, current_pbuf);
                    if (send_err != ERR_OK) {
                        mp_printf(MP_PYTHON_PRINTER, "USBIP: Error sending DEVLIST response (%d)\n", send_err);
                        // Error handling: maybe close connection?
                    }
                    bytes_processed = sizeof(struct usbip_header_op_basic);
                } else {
                    mp_printf(MP_PYTHON_PRINTER, "USBIP Recv: Incomplete OP_REQ_DEVLIST\n");
                    // TODO: Buffer
                    bytes_processed = current_pbuf->len;
                }

            } else if (cmd_code == USBIP_OP_REQ_IMPORT) {
                // Ensure enough data for header + busid
                if (current_pbuf->len >= sizeof(struct usbip_header_op_basic) + 32) {
                    err_t send_err = usbip_handle_op_req_import(client, pcb, current_pbuf);
                    if (send_err != ERR_OK) {
                        mp_printf(MP_PYTHON_PRINTER, "USBIP: Error sending IMPORT response (%d)\n", send_err);
                    }
                    bytes_processed = sizeof(struct usbip_header_op_basic) + 32;
                } else {
                    mp_printf(MP_PYTHON_PRINTER, "USBIP Recv: Incomplete OP_REQ_IMPORT\n");
                    // TODO: Buffer
                    bytes_processed = current_pbuf->len;
                }

            } else if (cmd_code == USBIP_CMD_SUBMIT) {
                // Ensure enough data for header
                uint32_t required_len = sizeof(struct usbip_header_cmd_submit);
                if (current_pbuf->len >= required_len) {
                    struct usbip_header_cmd_submit *submit_hdr = (struct usbip_header_cmd_submit *)current_pbuf->payload;
                    // Check if OUT transfer and if we have enough data for the payload too
                    if (lwip_ntohl(submit_hdr->direction) == TUSB_DIR_OUT) {
                        required_len += lwip_ntohl(submit_hdr->transfer_buffer_length);
                    }
                    if (current_pbuf->len >= required_len) {
                        err_t submit_err = usbip_handle_cmd_submit(client, pcb, current_pbuf);
                        if (submit_err != ERR_OK) {
                            mp_printf(MP_PYTHON_PRINTER, "USBIP: Error handling CMD_SUBMIT (%d)\n", submit_err);
                        }
                        bytes_processed = required_len;
                    } else {
                        mp_printf(MP_PYTHON_PRINTER, "USBIP Recv: Incomplete CMD_SUBMIT (payload)\n");
                        bytes_processed = current_pbuf->len;  // Consume header for now
                        // TODO: Buffer
                    }
                } else {
                    mp_printf(MP_PYTHON_PRINTER, "USBIP Recv: Incomplete CMD_SUBMIT (header)\n");
                    // TODO: Buffer
                    bytes_processed = current_pbuf->len;
                }

            } else if (cmd_code == USBIP_CMD_UNLINK) {
                // Ensure enough data for header
                if (current_pbuf->len >= sizeof(struct usbip_header_cmd_unlink)) {
                    err_t unlink_err = usbip_handle_cmd_unlink(client, pcb, current_pbuf);
                    if (unlink_err != ERR_OK) {
                        mp_printf(MP_PYTHON_PRINTER, "USBIP: Error handling CMD_UNLINK (%d)\n", unlink_err);
                    }
                    bytes_processed = sizeof(struct usbip_header_cmd_unlink);
                } else {
                    mp_printf(MP_PYTHON_PRINTER, "USBIP Recv: Incomplete OP_CMD_UNLINK\n");
                    bytes_processed = current_pbuf->len;
                    // TODO: Buffer
                }

            } else {
                mp_printf(MP_PYTHON_PRINTER, "USBIP Recv: Unknown command %04X\n", cmd_code);
                // Handle unknown command - maybe close connection?
                bytes_processed = current_pbuf->len; // Consume the unknown data
            }

            // For now, assume one command per pbuf segment and exit loop
            // TODO: Improve this to handle multiple commands or partial commands
            break;

        } else {
            // Not enough data for even a basic header
            mp_printf(MP_PYTHON_PRINTER, "USBIP Recv: Pbuf too small (%d bytes)\n", current_pbuf->len);
            // TODO: Buffer this data for later processing
            bytes_processed = current_pbuf->len; // Consume the partial data
        }

        current_pbuf = current_pbuf->next;
    }

    pbuf_free(p); // Free the pbuf chain

    return ERR_OK;
}


// --- Command Handlers ---

static err_t usbip_handle_op_req_devlist(usbip_client_state_t *client, struct tcp_pcb *pcb) {
    (void)client; // Not used yet
    err_t err = ERR_OK;

    // 1. Calculate size needed for response
    uint32_t num_devices = 0;
    usbip_host_device_t *dev = usbip_state.host_devices;
    while (dev) {
        num_devices++;
        dev = dev->next;
    }

    // Size = Basic Header + num_devices_field + (num_devices * (device_struct + 0 * interface_struct))
    // NOTE: We are not sending interface details for now for simplicity
    uint32_t response_size = sizeof(struct usbip_header_op_basic) + sizeof(uint32_t) +
        (num_devices * sizeof(struct usbip_exported_device));

    mp_printf(MP_PYTHON_PRINTER, "USBIP: Sending DEVLIST for %lu devices, size %lu\n", num_devices, response_size);

    // 2. Check if send buffer has enough space
    if (tcp_sndbuf(pcb) < response_size) {
        mp_printf(MP_PYTHON_PRINTER, "USBIP: DEVLIST response too large for send buffer (%lu > %u)\n",
            response_size, tcp_sndbuf(pcb));
        return ERR_MEM;
    }

    // 3. Allocate buffer for response (use TCP_WRITE_FLAG_COPY)
    // Using NO_COPY requires careful buffer management with pbufs, COPY is simpler.
    uint8_t *buffer = gc_alloc(response_size, 0);
    if (!buffer) {
        mp_printf(MP_PYTHON_PRINTER, "USBIP: Failed to allocate buffer for DEVLIST response\n");
        return ERR_MEM;
    }
    memset(buffer, 0, response_size);
    uint8_t *ptr = buffer;

    // 4. Fill the basic header
    struct usbip_header_op_basic *hdr = (struct usbip_header_op_basic *)ptr;
    hdr->version = lwip_htons(USBIP_VERSION);
    hdr->command_code = lwip_htons(USBIP_OP_RET_DEVLIST);
    hdr->status = lwip_htonl(USBIP_ST_OK);
    ptr += sizeof(struct usbip_header_op_basic);

    // 5. Fill number of devices
    *((uint32_t *)ptr) = lwip_htonl(num_devices);
    ptr += sizeof(uint32_t);

    // 6. Fill device details
    dev = usbip_state.host_devices;
    uint8_t config_desc_buffer[CFG_TUH_ENUMERATION_BUFSIZE]; // Buffer for config descriptor

    while (dev) {
        struct usbip_exported_device *exp_dev = (struct usbip_exported_device *)ptr;
        memset(exp_dev, 0, sizeof(struct usbip_exported_device)); // Clear previous data

        // Construct bus ID (e.g., "1-1") - dev_addr might not be stable, use index? Using dev_addr for now.
        snprintf(exp_dev->busid, sizeof(exp_dev->busid), "1-%d", dev->dev_addr);
        snprintf(exp_dev->path, sizeof(exp_dev->path), "/sys/devices/platform/rp2-usbip/usb1/1-%d", dev->dev_addr);
        exp_dev->busnum = lwip_htonl(1); // Hardcode bus 1 for now
        exp_dev->devnum = lwip_htonl(dev->dev_addr);

        // Get device details (VID, PID already stored)
        exp_dev->idVendor = lwip_htons(dev->vid);
        exp_dev->idProduct = lwip_htons(dev->pid);
        exp_dev->speed = lwip_htonl(tuh_speed_get(dev->dev_addr));

        // Get Device Descriptor details
        tusb_desc_device_t desc_device;
        if (tuh_descriptor_get_device_sync(dev->dev_addr, &desc_device, sizeof(desc_device)) == sizeof(desc_device)) {
            exp_dev->bcdDevice = desc_device.bcdDevice; // Already uint16_t, swap later
            exp_dev->bDeviceClass = desc_device.bDeviceClass;
            exp_dev->bDeviceSubClass = desc_device.bDeviceSubClass;
            exp_dev->bDeviceProtocol = desc_device.bDeviceProtocol;
            exp_dev->bNumConfigurations = desc_device.bNumConfigurations;
        } else {
            mp_printf(MP_PYTHON_PRINTER, "USBIP DEVLIST: Failed to get device descriptor for %u\n", dev->dev_addr);
            // Leave fields as 0
        }

        // Get Configuration Descriptor details (bNumInterfaces, bConfigurationValue)
        // WARNING: Sync call might block LWIP task. Async preferred for production.
        // Fetching only the first configuration descriptor (index 0).
        uint16_t fetched_len = tuh_descriptor_get_configuration_sync(dev->dev_addr, 0, config_desc_buffer, sizeof(config_desc_buffer));
        if (fetched_len >= sizeof(tusb_desc_configuration_t)) {
            tusb_desc_configuration_t *desc_config = (tusb_desc_configuration_t *)config_desc_buffer;
            exp_dev->bConfigurationValue = desc_config->bConfigurationValue;
            exp_dev->bNumInterfaces = desc_config->bNumInterfaces;
        } else {
            mp_printf(MP_PYTHON_PRINTER, "USBIP DEVLIST: Failed/Short get config descriptor for %u (len=%u)\n", dev->dev_addr, fetched_len);
            // Leave fields as 0
        }

        // Convert multi-byte fields to network byte order *after* filling
        exp_dev->bcdDevice = lwip_htons(exp_dev->bcdDevice);

        ptr += sizeof(struct usbip_exported_device);

        // TODO: Add interface descriptors here if needed by iterating config_desc_buffer

        dev = dev->next;
    }

    // 7. Send the response
    err = tcp_write(pcb, buffer, response_size, TCP_WRITE_FLAG_COPY);
    if (err == ERR_OK) {
        // Force output if needed (e.g., if buffer wasn't full)
        err = tcp_output(pcb);
        if (err != ERR_OK) {
            mp_printf(MP_PYTHON_PRINTER, "USBIP: tcp_output error (%d) after writing DEVLIST\n", err);
        }
    } else {
        mp_printf(MP_PYTHON_PRINTER, "USBIP: tcp_write error (%d) for DEVLIST response\n", err);
    }

    gc_free(buffer); // Free the allocated buffer
    return err;
}

static err_t usbip_handle_cmd_submit(usbip_client_state_t *client, struct tcp_pcb *pcb, const uint8_t *data, uint16_t len) {
    struct usbip_header_cmd_submit *req_hdr = (struct usbip_header_cmd_submit *)data;

    // --- Extract header fields (Network to Host byte order) ---
    uint32_t seqnum = lwip_ntohl(req_hdr->seqnum);
    uint32_t devid = lwip_ntohl(req_hdr->devid);
    uint32_t direction = lwip_ntohl(req_hdr->direction);
    uint32_t ep = lwip_ntohl(req_hdr->ep);
    uint32_t transfer_flags = lwip_ntohl(req_hdr->transfer_flags);
    uint32_t transfer_buffer_length = lwip_ntohl(req_hdr->transfer_buffer_length);

    uint8_t dev_addr = (uint8_t)(devid & 0xFFFF); // Assuming devid low 16 bits is dev_addr
    uint8_t ep_addr = (uint8_t)ep | (direction ? TUSB_DIR_IN_MASK : 0);

    mp_printf(MP_PYTHON_PRINTER, "USBIP: Handling CMD_SUBMIT: seq=%lu, dev=%u, ep=%02X, dir=%lu, len=%lu\n",
        seqnum, dev_addr, ep_addr, direction, transfer_buffer_length);

    // --- Sanity Checks ---
    if (client->attached_dev_addr == 0) {
        mp_printf(MP_PYTHON_PRINTER, "USBIP Error: Client not attached to any device.\n");
        usbip_send_status_response(client, USBIP_RET_SUBMIT, seqnum, -MP_ENODEV); // Or appropriate error
        return ERR_CONN; // Connection state error
    }
    if (client->attached_dev_addr != dev_addr) {
        mp_printf(MP_PYTHON_PRINTER, "USBIP Error: Client attached to %u, but CMD_SUBMIT is for %u\n",
            client->attached_dev_addr, dev_addr);
        usbip_send_status_response(client, USBIP_RET_SUBMIT, seqnum, -MP_EPERM); // Operation not permitted
        return ERR_ARG; // Invalid argument
    }
    if (!usbip_find_device(dev_addr)) {
        mp_printf(MP_PYTHON_PRINTER, "USBIP Error: Device %u not found for CMD_SUBMIT\n", dev_addr);
        usbip_send_status_response(client, USBIP_RET_SUBMIT, seqnum, -MP_ENODEV);
        return ERR_ARG; // Invalid argument
    }

    // --- Prepare Transfer Context ---
    usbip_transfer_context_t *context = gc_alloc(sizeof(usbip_transfer_context_t), 0);
    if (!context) {
        mp_printf(MP_PYTHON_PRINTER, "USBIP Error: Failed to allocate transfer context\n");
        return ERR_MEM;
    }
    memset(context, 0, sizeof(usbip_transfer_context_t));
    context->client = client;
    context->seqnum = seqnum;
    context->in_buffer = NULL;
    context->in_buffer_len = 0;

    // --- Allocate IN buffer if needed ---
    if (direction == TUSB_DIR_IN && transfer_buffer_length > 0) {
        context->in_buffer = gc_alloc(transfer_buffer_length, 0);
        if (!context->in_buffer) {
            mp_printf(MP_PYTHON_PRINTER, "USBIP Error: Failed to allocate IN buffer (%lu bytes)\n", transfer_buffer_length);
            gc_free(context);
            return ERR_MEM;
        }
        context->in_buffer_len = transfer_buffer_length;
    }

    // --- Register Transfer Context --- // Must be done *before* calling tuh_ function
    if (!usbip_glue_add_transfer(dev_addr, ep_addr, context)) {
        mp_printf(MP_PYTHON_PRINTER, "USBIP Error: Failed to add transfer context (endpoint busy?)\n");
        gc_free(context->in_buffer);
        gc_free(context);
        usbip_send_status_response(client, USBIP_RET_SUBMIT, seqnum, -MP_EBUSY);
        return ERR_INPROGRESS; // Endpoint busy
    }

    // --- Initiate TinyUSB Transfer --- // Control & Bulk (Interrupt TBD)
    bool submitted = false;
    if (ep == 0) {
        // Control Transfer
        uint8_t *setup_packet = req_hdr->setup;
        uint8_t *buffer = NULL;
        uint16_t buffer_len = (uint16_t)transfer_buffer_length;

        if (direction == TUSB_DIR_IN) {
            buffer = context->in_buffer;
        } else if (transfer_buffer_length > 0) {
            // Data follows header in the buffer
            buffer = (uint8_t *)data + sizeof(struct usbip_header_cmd_submit);
            // Length check already done in recv_cb
        }

        mp_printf(MP_PYTHON_PRINTER, "USBIP: Submitting Control: setup=[%02x %02x %02x %02x %02x %02x %02x %02x], len=%u\n",
            setup_packet[0], setup_packet[1], setup_packet[2], setup_packet[3],
            setup_packet[4], setup_packet[5], setup_packet[6], setup_packet[7], buffer_len);

        submitted = tuh_control_xfer(dev_addr, setup_packet, buffer, buffer_len);

    } else {
        // Bulk Transfer (Interrupt Transfers would be similar)
        uint8_t *buffer = NULL;
        uint16_t buffer_len = (uint16_t)transfer_buffer_length;

        if (direction == TUSB_DIR_IN) {
            buffer = context->in_buffer;
            if (!buffer && buffer_len > 0) {
                mp_printf(MP_PYTHON_PRINTER, "USBIP Error: IN buffer requested but not allocated (ep=%02X)\n", ep_addr);
                // Need to clean up context
                usbip_glue_find_remove_transfer(dev_addr, ep_addr);
                gc_free(context);
                return ERR_MEM;
            }
            mp_printf(MP_PYTHON_PRINTER, "USBIP: Submitting Bulk IN: ep=%02X, len=%u\n", ep_addr, buffer_len);

        } else {
            // OUT transfer
            if (buffer_len > 0) {
                buffer = (uint8_t *)data + sizeof(struct usbip_header_cmd_submit);
                // Length check done in recv_cb
                mp_printf(MP_PYTHON_PRINTER, "USBIP: Submitting Bulk OUT: ep=%02X, len=%u\n", ep_addr, buffer_len);
            } else {
                mp_printf(MP_PYTHON_PRINTER, "USBIP: Submitting Bulk OUT: ep=%02X, ZLP\n", ep_addr);
            }
        }

        // Ensure endpoint is not busy before submitting
        if (tuh_endpoint_is_busy(dev_addr, ep_addr)) {
            mp_printf(MP_PYTHON_PRINTER, "USBIP Error: Bulk endpoint %02X is busy\n", ep_addr);
            usbip_glue_find_remove_transfer(dev_addr, ep_addr);
            gc_free(context->in_buffer);
            gc_free(context);
            // TODO: Send error response?
            return ERR_INPROGRESS;
        }

        // Check transfer type based on endpoint descriptor (if possible) or assume bulk/interrupt are similar
        // For now, assume endpoint type doesn't change the call signature needed vs bulk
        // Use tuh_interrupt_xfer for interrupt endpoints if known, otherwise tuh_bulk_xfer
        tusb_desc_endpoint_t desc_ep;
        bool ep_desc_found = false;
        // TODO: Need a way to efficiently get endpoint descriptor to check bEndpointAddress & bmAttributes
        // This sync call is potentially slow and might not be feasible in this context.
        // It might be better to store endpoint attributes when the device is imported.
        // if (tuh_descriptor_get_endpoint_sync(dev_addr, ep, &desc_ep, sizeof(desc_ep))) {
        //    ep_desc_found = true;
        // }

        // bool is_interrupt = ep_desc_found && (desc_ep.bmAttributes & TUSB_XFER_INTERRUPT);
        bool is_interrupt = false; // Assume bulk for now unless descriptor known

        if (is_interrupt) {
            // submitted = tuh_interrupt_xfer(dev_addr, ep_addr, buffer, buffer_len);
            mp_printf(MP_PYTHON_PRINTER, "USBIP Error: Interrupt transfers not fully implemented yet\n");
            submitted = false; // Treat as unsupported for now
            // Need to clean up context if we don't submit
            usbip_glue_find_remove_transfer(dev_addr, ep_addr);
            gc_free(context->in_buffer);
            gc_free(context);
            usbip_send_status_response(client, USBIP_RET_SUBMIT, seqnum, -MP_EOPNOTSUPP);
            // return ERR_VAL; // Already sent response
            return ERR_OK; // Return OK as we handled the error by sending a response
        } else {
            submitted = tuh_bulk_xfer(dev_addr, ep_addr, buffer, buffer_len, true); // Auto-retry = true
        }
    }

    // --- Handle Submission Failure ---
    if (!submitted) {
        mp_printf(MP_PYTHON_PRINTER, "USBIP Error: tuh function returned false for seq %lu\n", seqnum);
        // Transfer failed to submit, remove context we just added
        usbip_glue_find_remove_transfer(dev_addr, ep_addr);
        gc_free(context->in_buffer);
        gc_free(context);
        usbip_send_status_response(client, USBIP_RET_SUBMIT, seqnum, -MP_EIO); // Generic I/O error
        return ERR_IF; // Generic interface error
    }

    mp_printf(MP_PYTHON_PRINTER, "USBIP: Transfer submitted successfully for seq %lu\n", seqnum);
    return ERR_OK;
}

static err_t usbip_handle_cmd_unlink(usbip_client_state_t *client, struct tcp_pcb *pcb, const uint8_t *data, uint16_t len) {
    struct usbip_header_cmd_unlink *req_hdr = (struct usbip_header_cmd_unlink *)data;
    uint32_t unlink_seqnum = lwip_ntohl(req_hdr->unlink_seqnum); // Use the correct field
    uint32_t req_seqnum = lwip_ntohl(req_hdr->seqnum);
    int32_t unlink_status = USBIP_ST_OK;

    mp_printf(MP_PYTHON_PRINTER, "USBIP: Handling CMD_UNLINK for URB seq %lu (req_seq=%lu)\n", unlink_seqnum, req_seqnum);

    // Find the transfer context associated with unlink_seqnum
    bool found = false;
    uint8_t target_dev_addr = 0;
    uint8_t target_ep_index = 0;
    usbip_transfer_context_t *ctx_to_unlink = NULL;

    // TODO: This search is inefficient. Need a better way to map seqnum to context.
    // Maybe add a list/map of contexts to usbip_client_state_t?
    // For now, iterate through the global map.
    for (int dev_addr = 0; dev_addr <= MAX_USB_DEVICES; ++dev_addr) {
        for (int index = 0; index < MAX_USB_ENDPOINTS; ++index) {
            usbip_transfer_context_t *ctx = usbip_state.pending_transfers[dev_addr][index];
            if (ctx && ctx->client == client && ctx->seqnum == unlink_seqnum) {
                ctx_to_unlink = ctx;
                target_dev_addr = dev_addr;
                target_ep_index = index;
                found = true;
                break;
            }
        }
        if (found) {
            break;
        }
    }

    if (found && ctx_to_unlink) {
        mp_printf(MP_PYTHON_PRINTER, "USBIP UNLINK: Found pending transfer on dev %u, ep_idx %u\n", target_dev_addr, target_ep_index);
        uint8_t ep_addr = ep_index_to_addr(target_ep_index);

        // Attempt to abort the transfer via TinyUSB
        // Note: tuh_control_abort only takes dev_addr. Aborting specific EP transfers seems more complex.
        // tuh_edpt_abort_xfer(target_dev_addr, ep_addr) might be needed but isn't standard TinyUSB API?
        // Check endpoint status and attempt clear if possible
        bool cleared = false;
        if (tuh_edpt_is_busy(target_dev_addr, ep_addr)) {
            mp_printf(MP_PYTHON_PRINTER, "USBIP UNLINK: Endpoint %02X busy, attempting clear...\n", ep_addr);
            // This might clear stall or pending state, doesn't guarantee abort
            cleared = tuh_edpt_clear_feature(target_dev_addr, ep_addr, TUSB_REQ_FEATURE_EDPT_HALT);
            mp_printf(MP_PYTHON_PRINTER, "USBIP UNLINK: tuh_edpt_clear_feature result: %d\n", cleared);
            // Aborting transfers in TinyUSB host without specific class driver involvement is hard.
            // We rely on the xfer_cb *not* finding the context later as the primary mechanism.
        }

        // Remove the context to prevent sending a response later
        usbip_state.pending_transfers[target_dev_addr][target_ep_index] = NULL;
        gc_free(ctx_to_unlink->in_buffer);
        gc_free(ctx_to_unlink);
        unlink_status = USBIP_ST_OK; // Report success as we removed the context
    } else {
        mp_printf(MP_PYTHON_PRINTER, "USBIP UNLINK: URB seq %lu not found or not owned by this client\n", unlink_seqnum);
        unlink_status = -MP_ENOENT; // Indicate transfer not found using negative errno
    }

    // Send OP_RET_UNLINK response
    uint32_t response_size = sizeof(struct usbip_header_ret_unlink);
    if (tcp_sndbuf(pcb) < response_size) {
        mp_printf(MP_PYTHON_PRINTER, "USBIP UNLINK Error: Response too large for send buffer\n");
        return ERR_MEM;
    }

    uint8_t *buffer = gc_alloc(response_size, 0);
    if (!buffer) {
        return ERR_MEM;
    }

    struct usbip_header_ret_unlink *res_hdr = (struct usbip_header_ret_unlink *)buffer;
    memset(res_hdr, 0, response_size);
    res_hdr->command = lwip_htonl(USBIP_RET_UNLINK);
    res_hdr->seqnum = lwip_htonl(req_seqnum); // Echo the unlink request's seqnum
    res_hdr->status = lwip_htonl(unlink_status);

    err_t err = tcp_write(pcb, buffer, response_size, TCP_WRITE_FLAG_COPY);
    gc_free(buffer);
    if (err == ERR_OK) {
        err = tcp_output(pcb);
    }
    if (err != ERR_OK) {
        mp_printf(MP_PYTHON_PRINTER, "USBIP UNLINK Error: Failed to send response (%d)\n", err);
    }

    return err;
}

// Helper function to send a simple status response (e.g., for errors)
static err_t usbip_send_status_response(usbip_client_state_t *client, uint16_t command_ret_code, uint32_t seqnum, int32_t status) {
    if (!client || !client->pcb) {
        return ERR_ARG;
    }

    uint32_t response_size = 0;
    uint8_t *response_buffer = NULL;
    err_t err = ERR_OK;
    struct tcp_pcb *pcb = client->pcb;

    if (command_ret_code == USBIP_RET_UNLINK) {
        response_size = sizeof(struct usbip_header_ret_unlink);
    } else if (command_ret_code == USBIP_RET_SUBMIT) {
        response_size = sizeof(struct usbip_header_ret_submit);
    } else {
        mp_printf(MP_PYTHON_PRINTER, "USBIP Error: Unsupported command %04X for status response\n", command_ret_code);
        return ERR_VAL;
    }

    if (tcp_sndbuf(pcb) < response_size) {
        mp_printf(MP_PYTHON_PRINTER, "USBIP Error: Status response too large for send buffer\n");
        return ERR_MEM;
    }

    response_buffer = gc_alloc(response_size, 0);
    if (!response_buffer) {
        return ERR_MEM;
    }
    memset(response_buffer, 0, response_size);

    // Fill header based on type
    if (command_ret_code == USBIP_RET_UNLINK) {
        struct usbip_header_ret_unlink *hdr = (struct usbip_header_ret_unlink *)response_buffer;
        hdr->command = lwip_htonl(command_ret_code);
        hdr->seqnum = lwip_htonl(seqnum); // Use the original UNLINK seqnum
        hdr->status = lwip_htonl(status);
    } else { // USBIP_RET_SUBMIT
        struct usbip_header_ret_submit *hdr = (struct usbip_header_ret_submit *)response_buffer;
        hdr->command = lwip_htonl(command_ret_code);
        hdr->seqnum = lwip_htonl(seqnum); // Use the original SUBMIT seqnum
        hdr->status = lwip_htonl(status);
        // Other fields (devid, ep, direction, actual_length) might be needed for context by client,
        // but are zeroed here for a simple error case.
    }

    mp_printf(MP_PYTHON_PRINTER, "USBIP: Sending status response: cmd=%04X, seq=%lu, status=%ld\n",
        command_ret_code, seqnum, status);

    err = tcp_write(pcb, response_buffer, response_size, TCP_WRITE_FLAG_COPY);
    gc_free(response_buffer);

    if (err == ERR_OK) {
        err = tcp_output(pcb);
    }
    if (err != ERR_OK) {
        mp_printf(MP_PYTHON_PRINTER, "USBIP Error: Failed to send status response (%d)\n", err);
    }
    return err;
}


// Callback for when previously sent data has been acknowledged
static err_t usbip_sent_cb(void *arg, struct tcp_pcb *pcb, u16_t len) {
    (void)arg; // client_state
    (void)pcb;
    mp_printf(MP_PYTHON_PRINTER, "USBIP Sent ACK: %u bytes\n", len);
    // TODO: Handle flow control if needed (e.g., send more data if buffer was full)
    return ERR_OK;
}

// Callback for when a fatal error occurs on the connection
static void usbip_err_cb(void *arg, err_t err) {
    usbip_client_state_t *client = (usbip_client_state_t *)arg;
    mp_printf(MP_PYTHON_PRINTER, "USBIP Error Callback: err=%d\n", err);
    if (client != NULL) {
        mp_printf(MP_PYTHON_PRINTER, "USBIP Cleaning up client %s:%u due to error\n",
            ipaddr_ntoa(&client->remote_ip), client->remote_port);
        // Don't close the PCB here, LWIP handles it after err_cb returns.
        // Just clean up our state.
        usbip_remove_client(client);
        // tcp_arg(pcb, NULL); // PCB is invalid after err_cb?
    }
}

// Helper function to clean up client state and close PCB
static void usbip_cleanup_client(usbip_client_state_t *client, struct tcp_pcb *pcb) {
    // First clean up any pending transfers associated with this client
    usbip_glue_cleanup_transfers_for_client(client);

    if (client) {
        usbip_remove_client(client); // This also detaches from device
    }
    if (pcb) {
        tcp_arg(pcb, NULL);
        tcp_sent(pcb, NULL);
        tcp_recv(pcb, NULL);
        tcp_err(pcb, NULL);
        // Don't need tcp_close if returning ERR_OK from recv_cb with p=NULL, LWIP does it.
        // If called from err_cb, PCB is already being cleaned up by LWIP.
        // If called from deinit, we call tcp_abort.
    }
}


void usbip_server_init(void) {
    if (usbip_listen_pcb != NULL) {
        mp_printf(MP_PYTHON_PRINTER, "USBIP Server already initialized.\n");
        return;
    }

    usbip_listen_pcb = tcp_new_ip_type(IPADDR_TYPE_ANY);
    if (usbip_listen_pcb == NULL) {
        mp_printf(MP_PYTHON_PRINTER, "USBIP Server Error: Cannot create PCB\n");
        return;
    }

    err_t err = tcp_bind(usbip_listen_pcb, IP_ANY_TYPE, USBIP_PORT);
    if (err != ERR_OK) {
        mp_printf(MP_PYTHON_PRINTER, "USBIP Server Error: Cannot bind PCB (%d)\n", err);
        tcp_close(usbip_listen_pcb);
        usbip_listen_pcb = NULL;
        return;
    }

    usbip_listen_pcb = tcp_listen(usbip_listen_pcb);
    if (usbip_listen_pcb == NULL) {
        // tcp_listen freed the PCB on error
        mp_printf(MP_PYTHON_PRINTER, "USBIP Server Error: Cannot listen on PCB\n");
        return;
    }

    // Register the accept callback
    tcp_accept(usbip_listen_pcb, usbip_accept_cb);

    mp_printf(MP_PYTHON_PRINTER, "USBIP Server Initialized: Listening on port %u\n", USBIP_PORT);
}

void usbip_server_deinit(void) {
    if (usbip_listen_pcb != NULL) {
        tcp_close(usbip_listen_pcb);
        usbip_listen_pcb = NULL;
        mp_printf(MP_PYTHON_PRINTER, "USBIP Server Deinitialized.\n");
    } else {
        mp_printf(MP_PYTHON_PRINTER, "USBIP Server already deinitialized.\n");
    }

    // Clean up any active client connections
    // TODO: Add locking if multi-threaded
    while (usbip_state.clients) {
        usbip_client_state_t *client = usbip_state.clients;
        struct tcp_pcb *pcb = client->pcb; // Get PCB before removing client
        mp_printf(MP_PYTHON_PRINTER, "USBIP Deinit: Cleaning up client %s:%u\n",
            ipaddr_ntoa(&client->remote_ip), client->remote_port);
        usbip_cleanup_client(client, pcb); // This removes the client from the list
        if (pcb) {
            // Explicitly abort remaining connections during deinit
            tcp_abort(pcb);
        }
    }
}

// TODO: Implement USBIP protocol handling logic for active connections
// - Client state management
// - recv callback to parse commands (OP_REQ_DEVLIST, OP_REQ_IMPORT, OP_CMD_SUBMIT, etc.)
// - Interaction with usbip_tusb.c to list devices and submit transfers
// - sent callback (if needed for flow control)
// - err callback for handling connection errors
// - Sending responses (OP_RET_DEVLIST, OP_RET_IMPORT, OP_RET_SUBMIT, etc.)


#endif // MICROPY_PY_USBIP && MICROPY_PY_LWIP
