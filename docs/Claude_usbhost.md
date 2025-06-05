# MicroPython USB Host Development (Claude's Log)

This document tracks the development and testing process for the `machine.USBHost` feature in MicroPython.

## Initial Multi-Test Suite Creation

*   **Objective:** Develop a comprehensive multi-test suite for `machine.USBHost` using the `run-multitests.py` framework.
*   **Strategy:** Utilize two connected boards (Host USB <-> Device USB).
*   **Directory:** `tests/multi_usb/`
*   **Tests Implemented:**
    *   CDC (Basic I/O, IRQ, `usb-device-cdc` library)
    *   HID (Keyboard/Mouse reports via IRQ, output reports, `usb-device-hid` library)
    *   MSC (Host write/read, Host write -> device reset -> device read)
    *   Enumeration (Composite CDC+HID)
    *   Disconnects (CDC, HID, MSC)
*   **Status:** Initial tests implemented and committed to the `usbhost` branch.

## Feature: USBIP Server Integration

*   **Objective:** Implement a USBIP server within MicroPython, allowing remote machines to access USB devices connected to the MicroPython host port.
*   **Core Concept:** Intercept USB devices using a custom TinyUSB application driver and expose them over the network via the USBIP protocol using the LWIP stack.
*   **Key Components:**
    *   TinyUSB Host Stack (`shared/tinyusb/`)
    *   Custom TinyUSB Application Driver (hooking via `usbh_app_driver_get_cb` or similar)
    *   USBIP Protocol Implementation (C-level state machine, message packing/unpacking)
    *   LWIP Network Stack (TCP server on port 3240)
    *   MicroPython Module (`usbip`) for Python-level control.
*   **Implementation Steps:**
    1.  **Configuration:** Add `MICROPY_PY_USBIP` config option, depending on `MICROPY_HW_ENABLE_USB_HOST` and `MICROPY_PY_LWIP`.
    2.  **File Structure:** Create skeleton files in `extmod/`:
        *   `usbip.h`: Shared definitions.
        *   `modusbip.c`: MicroPython module (`usbip.start()`, `usbip.stop()`).
        *   `usbip_server.c`: TCP server and USBIP protocol logic (using LWIP).
        *   `usbip_tusb.c`: TinyUSB application driver implementation (callbacks: `open`, `set_config`, `xfer_cb`, `close`).
        *   `usbip_glue.c`: Data structures linking components.
    3.  **TinyUSB Integration:** Implement the TinyUSB driver callbacks in `usbip_tusb.c` and devise a mechanism to register this driver with TinyUSB during host initialization (potentially modifying port-specific TinyUSB setup). The driver needs to:
        *   Identify devices/interfaces to be managed by USBIP (e.g., based on VID/PID filters or claiming unclaimed interfaces).
        *   Store device information for `OP_REQ_DEVLIST`.
        *   Translate `OP_CMD_SUBMIT` USBIP requests into `tuh_xxx_xfer` TinyUSB calls.
        *   Translate `xfer_cb` callbacks into `OP_RET_SUBMIT` USBIP responses.
        *   Handle device disconnection (`close`).
    4.  **USBIP Server Logic (`usbip_server.c`):**
        *   Create a listening TCP socket/netconn (LWIP).
        *   Handle client connections (accept, manage state).
        *   Parse incoming USBIP commands.
        *   Implement command handlers (`OP_REQ_DEVLIST`, `OP_REQ_IMPORT`, `OP_CMD_SUBMIT`).
        *   Send USBIP responses based on results from `usbip_tusb.c`.
    5.  **Data Structures (`usbip_glue.c`):**
        *   Define structures to track managed USB devices, client connections, and pending request correlation.
        *   Consider thread safety if using RTOS tasks.
    6.  **MicroPython Module (`modusbip.c`):**
        *   Implement `usbip.start()` to initialize the server and register the driver.
        *   Implement `usbip.stop()` to deinitialize.
    7.  **Build System Integration:** Add config flags and source files to relevant port Makefiles/CMakeLists, conditioned on `MICROPY_PY_USBIP`. Start with the `rp2` port.
*   **Challenges:** Concurrency (Network/USB), performance/latency, resource usage, driver priority, error handling.
*   **Initial Target Port:** `rp2`.

