# MicroPython USBIP Server Feature Summary

## 1. Goal

To implement a USBIP (USB over IP) server within the MicroPython firmware. This allows USB devices connected to the host port of a MicroPython board to be accessed remotely over a TCP/IP network by a computer running a standard USBIP client (e.g., the `usbip` tool on Linux).

## 2. Requirements

*   **Hardware:** A MicroPython board with USB Host capabilities (specifically, a port configured for Host mode, like RP2040).
*   **Firmware:**
    *   MicroPython firmware built with USB Host support enabled (`MICROPY_HW_ENABLE_USB_HOST = 1`).
    *   LWIP network stack enabled (`MICROPY_PY_LWIP = 1`).
    *   The USBIP feature itself enabled (`MICROPY_PY_USBIP = 1`).
*   **Network:** TCP/IP network connectivity between the MicroPython board and the client computer.
*   **Client:** A computer running a USBIP client tool (e.g., `usbip` tools from `linux-tools-generic` on Debian/Ubuntu).

## 3. Design & Implementation Plan

### 3.1. Core Concept

The feature intercepts USB device connections at the TinyUSB host stack level *before* or *instead of* standard MicroPython class drivers (CDC, MSC, HID). It uses a custom TinyUSB application driver to manage these devices. A TCP server listens for incoming connections from USBIP clients. Once a client connects and requests to "attach" to a specific device, the server mediates the USBIP protocol, translating USBIP requests (like URB submissions) into TinyUSB host operations and forwarding the results back to the client.

### 3.2. Key Components (within `extmod/`)

*   **`usbip.h`**: Header file defining USBIP protocol constants, message structures (packed, big-endian), internal state structures (`usbip_client_state_t`, `usbip_host_device_t`, `usbip_transfer_context_t`), and function prototypes.
*   **`modusbip.c`**: The MicroPython C module providing the Python interface (`import usbip`).
    *   `usbip.start()`: Initializes the glue logic, starts the TCP server.
    *   `usbip.stop()`: Stops the TCP server, cleans up client connections and resources.
*   **`usbip_server.c`**: Implements the TCP server logic using LWIP's raw TCP API.
    *   Listens on TCP port 3240.
    *   Manages client connections (`accept`, `recv`, `sent`, `err` callbacks).
    *   Parses incoming USBIP commands from client buffers.
    *   Handles command logic (`OP_REQ_DEVLIST`, `OP_REQ_IMPORT`, `OP_CMD_SUBMIT`, `OP_CMD_UNLINK`).
    *   Constructs and sends USBIP responses.
*   **`usbip_tusb.c`**: Implements the TinyUSB host application driver interface.
    *   Defines the `tuh_driver_t usbip_driver` structure with callbacks (`init`, `open`, `set_config`, `xfer_cb`, `close`).
    *   Implements `usbh_app_driver_get_cb()`: Hook called by TinyUSB to claim interfaces; returns `&usbip_driver` to handle the device via USBIP.
    *   `open`: Called when a new device/interface is detected; stores device info (VID/PID).
    *   `close`: Called on device disconnect; triggers cleanup.
    *   `xfer_cb`: Crucial callback triggered upon completion of a TinyUSB transfer (`tuh_control_xfer`, `tuh_bulk_xfer`, etc.). Finds the associated client request context (via `usbip_glue`) and sends the `OP_RET_SUBMIT` response over TCP.
*   **`usbip_glue.c`**: Manages shared state and linking between components.
    *   Defines the global `usbip_state`.
    *   Manages linked lists of discovered USB devices (`usbip_host_device_t`).
    *   Manages linked lists of active client connections (`usbip_client_state_t`).
    *   Manages mapping of pending USB transfers (`usbip_transfer_context_t`) to allow correlation in `xfer_cb`.
    *   Handles cleanup of pending transfers when clients or devices disconnect.

### 3.3. Integration Points

*   **TinyUSB:** Hooks into the host stack via `usbh_app_driver_get_cb` when `CFG_TUH_APPLICATION_DRIVER=1` is defined (controlled via `MICROPY_PY_USBIP` in `shared/tinyusb/tusb_config.h`). Disables standard TinyUSB host class drivers (`CFG_TUH_CDC=0`, etc.) when USBIP is enabled. Uses `tuh_` functions (`tuh_control_xfer`, `tuh_bulk_xfer`, descriptor getters) to interact with the USB device.
*   **LWIP:** Uses the raw TCP API (`tcp_new_ip_type`, `tcp_bind`, `tcp_listen`, `tcp_accept`, `tcp_recv`, `tcp_write`, `tcp_output`, `tcp_close`, etc.) for network communication.
*   **MicroPython Core:** Registers as a built-in C module (`MP_REGISTER_MODULE`). Uses MicroPython's memory allocation (`gc_alloc`, `gc_free`) and printf.

### 3.4. Protocol Handling

*   **`OP_REQ_DEVLIST` / `OP_RET_DEVLIST`:** Server lists discovered USB devices from its internal list (`usbip_state.host_devices`), fetching descriptor details via TinyUSB.
*   **`OP_REQ_IMPORT` / `OP_RET_IMPORT`:** Client requests to attach to a device by bus ID. Server checks availability and attaches the client logically, updating state (`client->attached_dev_addr`, `dev->attached`).
*   **`OP_CMD_SUBMIT` / `OP_RET_SUBMIT`:** Client sends a USB request (URB). Server parses it, creates a transfer context, initiates the corresponding TinyUSB transfer (`tuh_control_xfer`, `tuh_bulk_xfer`). When the transfer completes (`xfer_cb`), the server finds the context and sends the result back to the client.
*   **`OP_CMD_UNLINK` / `OP_RET_UNLINK`:** Client requests to cancel a pending transfer. Server attempts to find the context, potentially tries to clear the endpoint state via TinyUSB, removes the context, and sends a response.

## 4. Build Configuration

1.  **Enable Feature:** Define `MICROPY_PY_USBIP = 1` in the relevant `mpconfigport.h`. This requires `MICROPY_HW_ENABLE_USB_HOST = 1` and `MICROPY_PY_LWIP = 1`.
2.  **Modify `tusb_config.h`:** The definition in `shared/tinyusb/tusb_config.h` automatically sets `CFG_TUH_CDC/MSC/HID = 0` and `CFG_TUH_APPLICATION_DRIVER = 1` when `MICROPY_PY_USBIP` is true.
3.  **Add Sources to Build:** Add `extmod/modusbip.c`, `extmod/usbip_server.c`, `extmod/usbip_tusb.c`, `extmod/usbip_glue.c` to the port's build file (`CMakeLists.txt` for rp2) guarded by `if(MICROPY_PY_USBIP)`.
4.  **Add QSTRs:** Add `extmod/modusbip.c` to the QSTR sources list in the build file, guarded by `if(MICROPY_PY_USBIP)`.

## 5. Current Status & TODOs (as of conversation end)

*   **Implemented:**
    *   Basic file structure and build system integration (for rp2).
    *   TinyUSB application driver hook (`usbh_app_driver_get_cb`).
    *   LWIP TCP server listener and client connection management.
    *   Handling for `OP_REQ_DEVLIST`, `OP_REQ_IMPORT`, `OP_CMD_SUBMIT` (Control & Bulk), `OP_CMD_UNLINK`.
    *   State management for devices, clients, and pending transfers (using context map).
    *   Partial TCP message handling via per-client receive buffers.
    *   Basic error reporting via status codes in response headers.
    *   Fetching of basic device/configuration descriptor details.
*   **Key TODOs / Limitations:**
    *   **Interrupt Transfers:** Not implemented in `usbip_handle_cmd_submit`.
    *   **Transfer Abort:** Actual hardware transfer abortion in UNLINK/cleanup is not reliably implemented due to TinyUSB API limitations; relies on context removal.
    *   **Descriptor Details:** Fetching comprehensive descriptor info (interfaces, strings) is missing and synchronous calls used may cause issues. Async fetching preferred.
    *   **Transfer Context Mapping:** Current array map is basic; might need improvement for efficiency or handling multiple transfers per endpoint.
    *   **Error Handling:** Needs more comprehensive mapping of TinyUSB/LWIP errors to USBIP status codes. Handling of TCP buffer full errors needs review.
    *   **Concurrency:** No explicit locking added; potential issues if used in an RTOS environment without GIL.
    *   **Testing:** Feature has not been tested on hardware.

## 6. Testing Plan

1.  Build firmware for a suitable board (e.g., Pico W) with USBIP enabled.
2.  Configure network (WiFi for Pico W).
3.  Connect a test USB device (e.g., USB-Serial, mouse, MSC drive) to the board's host port.
4.  Run `import usbip; usbip.start()` on the board.
5.  On a Linux client:
    *   `sudo modprobe vhci-hcd`
    *   `usbip list -r <board_ip>` (Verify device appears)
    *   `sudo usbip attach -r <board_ip> -b <bus_id>` (Attach device)
    *   Verify device appears locally (`lsusb`, `dmesg`).
    *   Test device functionality (serial I/O, mouse movement, mount filesystem).
    *   `sudo usbip detach -p <port>` (Detach device)
6.  Run `usbip.stop()` on the board.
7.  Repeat with various devices and test edge cases (disconnects, errors).
