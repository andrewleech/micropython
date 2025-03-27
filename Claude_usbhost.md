# MicroPython USB Host Mode Implementation

## Overview

This document summarizes the USB host mode implementation for MicroPython, built on top of TinyUSB. The implementation enables MicroPython to act as a USB host, detecting and communicating with connected USB devices, including CDC (Serial) devices, MSC (Mass Storage) devices, and HID (Human Interface Device) devices.

## Architecture

The USB host implementation consists of:

1. **Core USB Host Framework**
   - Initialization and management of USB host mode
   - Device detection and management (VID/PID/serial/etc.)
   - TinyUSB integration and event handling

2. **CDC Device Support**
   - Communication with CDC devices (virtual serial ports)
   - Stream interface (read/write/readinto/etc.)
   - IRQ callbacks for data reception

3. **MSC Device Support**
   - Block device protocol implementation
   - Filesystem integration 
   - Disk operations (read/write blocks)
   
4. **HID Device Support**
   - Input and output reports handling
   - Keyboard, mouse, and generic HID device support
   - IRQ callbacks for report reception
   - Protocol, usage page, and usage identification

## Files Implemented

- `/shared/tinyusb/mp_usbh.h`: Header file defining classes and structures
- `/shared/tinyusb/mp_usbh.c`: Implementation of USB host functionality
- `/docs/library/machine.USBHost.rst`: Documentation

## Python API

### USBHost Class

The main class for interacting with USB devices:

```python
import machine

# Create and activate USB host
host = machine.USBHost()
host.active(True)

# Get all connected devices
devices = host.devices()
for dev in devices:
    print(f"Device: VID=0x{dev.vid:04x}, PID=0x{dev.pid:04x}")
    
# Get CDC devices (serial ports)
cdc_devices = host.cdc_devices()

# Get MSC devices (mass storage)
msc_devices = host.msc_devices()

# Get HID devices (keyboards, mice, etc.)
hid_devices = host.hid_devices()
```

### CDC Devices

For communicating with USB serial ports:

```python
# Find a CDC device
cdc_devices = host.cdc_devices()
if cdc_devices:
    cdc = cdc_devices[0]
    
    # Check connection
    if cdc.is_connected():
        # Write data
        cdc.write(b'Hello')
        
        # Read data
        if cdc.any():
            data = cdc.read(64)
            print(data)
            
        # Set up IRQ for data reception
        def on_rx(cdc):
            print(f"Received data: {cdc.read(64)}")
        
        cdc.irq(handler=on_rx, trigger=cdc.IRQ_RX)
```

### MSC Devices

For working with USB flash drives:

```python
# Find an MSC device
msc_devices = host.msc_devices()
if msc_devices:
    msc = msc_devices[0]
    
    # Mount the filesystem
    import vfs
    vfs.mount(msc, '/usb')
    
    # Use the filesystem
    import os
    print(os.listdir('/usb'))
    
    # Write a file
    with open('/usb/test.txt', 'w') as f:
        f.write('Hello from MicroPython!')
        
    # Read a file
    with open('/usb/test.txt', 'r') as f:
        print(f.read())
```

### HID Devices

For working with keyboards, mice, and other HID devices:

```python
# Find HID devices
hid_devices = host.hid_devices()
for hid in hid_devices:
    # Check device type
    if hid.protocol == hid.PROTOCOL_KEYBOARD:
        print("Found a keyboard")
    elif hid.protocol == hid.PROTOCOL_MOUSE:
        print("Found a mouse")
    
    # Check if device is connected
    if hid.is_connected():
        # Request a report from the device
        hid.request_report()
        
        # Check if report was received
        if hid.has_report():
            # Get the report data
            report = hid.get_report()
            print(f"Report data: {report}")
        
        # Set up IRQ for report reception
        def on_report(hid):
            report = hid.get_report()
            if hid.protocol == hid.PROTOCOL_KEYBOARD:
                # Process keyboard report
                print(f"Keyboard report: {report}")
            elif hid.protocol == hid.PROTOCOL_MOUSE:
                # Process mouse report
                print(f"Mouse report: {report}")
        
        hid.irq(handler=on_report, trigger=hid.IRQ_REPORT)
        
        # For output reports (e.g., LED status for keyboards)
        hid.send_report(b'\x00\x01')  # Example: Set NUM lock LED
```

## Integration Guide for Ports

To enable USB host mode in a port:

1. **Configuration**
   Add to `mpconfigport.h`:
   ```c
   #define MICROPY_HW_USB_HOST (1)
   ```

2. **TinyUSB Configuration**
   Configure TinyUSB host mode in `tusb_config.h`:
   ```c
   #define CFG_TUH_ENABLED (1)
   #define CFG_TUH_MAX_SPEED OPT_MODE_FULL_SPEED
   #define CFG_TUH_DEVICE_MAX (4)
   #define CFG_TUH_CDC (2)
   #define CFG_TUH_MSC (1)
   #define CFG_TUH_HID (3)
   ```

3. **Task Loop Integration**
   Add to the port's main task function:
   ```c
   // In the port's main task loop
   for (;;) {
       #if MICROPY_HW_USB_HOST
       // Process USB host events
       mp_usbh_task();
       #endif
       
       // Other processing
       ...
   }
   ```

4. **Hardware Initialization**
   Add USB host controller initialization in the port's startup code (specific to each port's hardware).

## Limitations

- Only Full Speed and High Speed devices are supported (not Low Speed)
- Number of devices is limited by CFG_TUH_DEVICE_MAX
- Number of CDC interfaces is limited by CFG_TUH_CDC
- Number of MSC devices is limited by CFG_TUH_MSC
- Number of HID devices is limited by CFG_TUH_HID
- Complex HID report descriptors may not be fully parsed
- Power management capabilities depend on hardware support