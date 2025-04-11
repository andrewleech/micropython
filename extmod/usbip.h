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

#ifndef MICROPY_INCLUDED_EXTMOD_USBIP_H
#define MICROPY_INCLUDED_EXTMOD_USBIP_H

#include "py/runtime.h"
#include "py/objlist.h"
#include "lwip/ip_addr.h"
#include "lwip/tcp.h"
#include "tusb.h"

// --- USBIP Protocol Constants ---

#define USBIP_VERSION 0x0111 // v1.1.1

// Commands
#define USBIP_CMD_SUBMIT   0x0001
#define USBIP_CMD_UNLINK   0x0002
#define USBIP_RET_SUBMIT   0x0003
#define USBIP_RET_UNLINK   0x0004

#define USBIP_OP_REQ_DEVLIST 0x8005
#define USBIP_OP_RET_DEVLIST 0x0005
#define USBIP_OP_REQ_IMPORT  0x8003
#define USBIP_OP_RET_IMPORT  0x0003

// Status codes (used in ret headers)
#define USBIP_ST_OK          0x00
#define USBIP_ST_NA          0x01 // Not available/applicable
#define USBIP_ST_NODEV       0x02 // Device not found
#define USBIP_ST_CONNREFUSED 0x03 // Connection refused (already in use?)
#define USBIP_ST_ERROR       0xFF // Generic error

// --- USBIP Protocol Structures ---
// All multi-byte fields are big-endian (network byte order)

#pragma pack(push, 1)

// Basic Operation Header (for OP_REQ/RET_DEVLIST, OP_REQ/RET_IMPORT)
struct usbip_header_op_basic {
    uint16_t version;
    uint16_t command_code;
    uint32_t status; // 0 for REQ, status for RET
};

// OP_REQ_DEVLIST: Uses usbip_header_op_basic

// Part of OP_RET_DEVLIST response (repeated for each device)
struct usbip_exported_device {
    char path[256];
    char busid[32];
    uint32_t busnum;
    uint32_t devnum;
    uint32_t speed; // Use TUSB_SPEED_xxx constants
    uint16_t idVendor;
    uint16_t idProduct;
    uint16_t bcdDevice;
    uint8_t bDeviceClass;
    uint8_t bDeviceSubClass;
    uint8_t bDeviceProtocol;
    uint8_t bConfigurationValue;
    uint8_t bNumConfigurations;
    uint8_t bNumInterfaces;
};

// Part of OP_RET_DEVLIST response (repeated for each interface)
struct usbip_exported_interface {
    uint8_t bInterfaceClass;
    uint8_t bInterfaceSubClass;
    uint8_t bInterfaceProtocol;
    uint8_t padding; // Align to 4 bytes?
};

// OP_RET_DEVLIST: usbip_header_op_basic + uint32_t num_devices + N * (usbip_exported_device + M * usbip_exported_interface)

// OP_REQ_IMPORT: usbip_header_op_basic + char busid[32]

// OP_RET_IMPORT: usbip_header_op_basic + usbip_exported_device (without interfaces)

// USBIP URB Command Header
struct usbip_header_cmd_submit {
    uint32_t command;             // Should be USBIP_CMD_SUBMIT
    uint32_t seqnum;              // Sequence number
    uint32_t devid;               // high 16: busnum, low 16: devnum
    uint32_t direction;           // 0: OUT, 1: IN
    uint32_t ep;                  // Endpoint number
    uint32_t transfer_flags;      // URB flags
    uint32_t transfer_buffer_length; // Length of data stage
    uint32_t start_frame;         // For ISO
    uint32_t number_of_packets;   // For ISO
    uint32_t interval;            // For Int/ISO
    uint8_t setup[8];             // Setup packet for control transfers
};
// Followed by `transfer_buffer_length` bytes of data if direction is OUT
// Followed by ISO packet descriptors if number_of_packets > 0

// USBIP URB Response Header
struct usbip_header_ret_submit {
    uint32_t command;             // Should be USBIP_RET_SUBMIT
    uint32_t seqnum;              // Matching sequence number
    uint32_t devid;
    uint32_t direction;
    uint32_t ep;
    int32_t status;               // Transfer status (0 for success, neg errno)
    uint32_t actual_length;       // Actual length transferred
    uint32_t start_frame;         // For ISO
    uint32_t number_of_packets;   // For ISO
    uint32_t error_count;         // For ISO
};
// Followed by `actual_length` bytes of data if direction is IN
// Followed by ISO packet descriptors if number_of_packets > 0

// USBIP UNLINK Command Header
struct usbip_header_cmd_unlink {
    uint32_t command;             // Should be USBIP_CMD_UNLINK
    uint32_t seqnum;              // URB seqnum to unlink
    uint32_t devid;               // Unused?
    uint32_t direction;           // Unused?
    uint32_t ep;                  // Unused?
    uint32_t unlink_seqnum;       // URB seqnum to unlink (yes, repeated?)
};

// USBIP UNLINK Response Header
struct usbip_header_ret_unlink {
    uint32_t command;             // Should be USBIP_RET_UNLINK
    uint32_t seqnum;              // Matching unlink seqnum
    uint32_t devid;               // Unused?
    uint32_t direction;           // Unused?
    uint32_t ep;                  // Unused?
    int32_t status;               // Status of unlink operation
};

#pragma pack(pop)

// --- Structures ---


// Represents a USB device discovered by the host hook
typedef struct _usbip_host_device_t {
    uint8_t dev_addr; // TinyUSB device address
    uint16_t vid;
    uint16_t pid;
    // Add other relevant info like class/subclass/protocol if needed for filtering/DEVLIST
    bool attached; // Is a client currently attached to this device?
    struct _usbip_host_device_t *next;
} usbip_host_device_t;

// Represents a pending USB transfer initiated by a client
typedef struct _usbip_transfer_context_t {
    struct _usbip_client_state_t *client; // Which client initiated?
    uint32_t seqnum;                      // USBIP sequence number
    uint8_t *in_buffer;                   // Buffer for IN data (if allocated)
    uint32_t in_buffer_len;               // Size of allocated IN buffer
    // Add other fields if needed (e.g., original command header)
} usbip_transfer_context_t;

// --- Global State (Potentially in usbip_glue.c) ---
#define MAX_USB_DEVICES CFG_TUH_DEVICE_MAX
#define MAX_USB_ENDPOINTS 16 // Max endpoint number (0-15) * 2 directions

typedef struct _usbip_global_state_t {
    usbip_host_device_t *host_devices; // Linked list of discovered devices
    usbip_client_state_t *clients;     // Linked list of active clients
    // Simple map for pending transfers [dev_addr][ep_addr_with_dir]
    usbip_transfer_context_t *pending_transfers[MAX_USB_DEVICES + 1][MAX_USB_ENDPOINTS];
    // Add mutexes/locks if using RTOS
} usbip_global_state_t;

extern usbip_global_state_t usbip_state;

// --- Function Prototypes ---

// usbip_server.c
void usbip_server_init(void);
void usbip_server_deinit(void);

// usbip_glue.c (Device Tracking)
void usbip_glue_init(void);
void usbip_add_device(uint8_t dev_addr, uint16_t vid, uint16_t pid);
void usbip_remove_device(uint8_t dev_addr);
usbip_host_device_t *usbip_find_device(uint8_t dev_addr);

// usbip_glue.c (Client Tracking)
usbip_client_state_t *usbip_add_client(struct tcp_pcb *pcb);
void usbip_remove_client(usbip_client_state_t *client);

// usbip_glue.c (Transfer Tracking)
bool usbip_glue_add_transfer(uint8_t dev_addr, uint8_t ep_addr, usbip_transfer_context_t *context);
usbip_transfer_context_t *usbip_glue_find_remove_transfer(uint8_t dev_addr, uint8_t ep_addr);
void usbip_glue_cleanup_transfers_for_client(usbip_client_state_t *client);
void usbip_glue_cleanup_transfers_for_device(uint8_t dev_addr);


#endif // MICROPY_INCLUDED_EXTMOD_USBIP_H
