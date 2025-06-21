# tests/multi_usb/cdc_irq.py
import time
import machine

try:
    import uos as os
except ImportError:
    import os

# Global state for IRQ handler on host
host_irq_triggered = False
host_irq_data = b''


# --- Instance 0: USB Host ---
def instance0():
    global host_irq_triggered, host_irq_data
    host_irq_triggered = False
    host_irq_data = b''
    host = None
    cdc = None

    try:
        # Host IRQ handler
        def on_rx(cdc_dev):
            global host_irq_triggered, host_irq_data
            print("IRQ: on_rx called")
            available = cdc_dev.any()
            if available > 0:
                chunk = cdc_dev.read(available)
                host_irq_data += chunk
                print(f"IRQ: read {len(chunk)} bytes: {chunk}")
                host_irq_triggered = True  # Signal that IRQ ran
            else:
                print("IRQ: any() was 0")

        # Need host controller enabled before device connects
        host = machine.USBHost()
        host.active(True)
        print("Host active")

        # Synchronization point
        multitest.next()

        # Signal device it can start
        multitest.broadcast("HOST_READY")
        print("Waiting for device")
        multitest.wait("DEVICE_READY")

        # Synchronization point
        multitest.next()

        # Wait for CDC device to enumerate with proper polling
        cdc = None
        print("Searching for CDC device...")
        deadline = time.ticks_add(time.ticks_ms(), 5000)  # 5 second timeout
        while time.ticks_diff(deadline, time.ticks_ms()) > 0:
            cdc_devices = host.cdc_devices()
            if cdc_devices:
                cdc = cdc_devices[0]
                print(f"CDC device found: {cdc}")
                break
            time.sleep_ms(200)

        if not cdc:
            print("CDC device not found - ABORTING")
            multitest.broadcast("HOST_ABORT")
            return

        # Register IRQ handler
        print("Registering IRQ handler")
        cdc.irq(handler=on_rx, trigger=cdc.IRQ_RX)
        print("IRQ handler registered")

        # Synchronization point
        multitest.next()

        # Signal device to send data
        multitest.broadcast("SEND_DATA")
        print("Waiting for device to send data...")
        multitest.wait("DATA_SENT")
        print("Device data supposedly sent.")

        # Wait for IRQ handler to signal completion
        print("Waiting for IRQ handler to trigger...")
        deadline = time.ticks_add(time.ticks_ms(), 3000)  # 3 sec timeout
        while not host_irq_triggered and time.ticks_diff(deadline, time.ticks_ms()) > 0:
            # The IRQ should be processed by the TinyUSB task, which is implicitly
            # run by the test framework or background scheduler.
            # We just need to poll our flag.
            time.sleep_ms(50)

        if host_irq_triggered:
            print(f"IRQ handler triggered successfully.")
            print(f"Data received via IRQ: {host_irq_data}")
        else:
            print("TIMEOUT waiting for IRQ handler to trigger")

        # Unregister IRQ
        print("Unregistering IRQ handler")
        cdc.irq(handler=None)

        print("Host finished")

    finally:
        # Cleanup resources
        if host:
            host.active(False)
            print("Host deactivated")


# --- Instance 1: USB Device ---
def instance1():
    cdc_dev = None

    try:
        # Try to import the proper CDC device module
        try:
            import usb.device.cdc

            cdc_dev = usb.device.cdc.Device()
            print("CDC device configured")
        except ImportError:
            print("usb.device.cdc not available, skipping test")
            multitest.skip()

        # Synchronization point
        multitest.next()

        # Wait for host to be ready
        print("Device waiting for HOST_READY")
        multitest.wait("HOST_READY")
        print("HOST_READY received")

        # Give host time to see the device after enumeration with polling
        deadline = time.ticks_add(time.ticks_ms(), 3000)  # 3 second timeout
        while time.ticks_diff(deadline, time.ticks_ms()) > 0:
            time.sleep_ms(100)

        print("Broadcasting DEVICE_READY")
        multitest.broadcast("DEVICE_READY")

        # Synchronization point
        multitest.next()

        # Wait for host signal to send data, or abort signal
        print("Device waiting for SEND_DATA")
        try:
            multitest.wait("SEND_DATA")
            print("SEND_DATA received")
        except:
            # Check if we got an abort signal
            try:
                multitest.wait("HOST_ABORT")
                print("HOST_ABORT received, device finishing.")
                return
            except:
                print("Timeout waiting for signals")
                return

        # Synchronization point
        multitest.next()

        # Send data using proper CDC device API
        data_to_send = b"irq_test_data"
        try:
            print(f"Device sending '{data_to_send}'...")
            bytes_sent = cdc_dev.write(data_to_send)
            print(f"CDC write returned: {bytes_sent} bytes")
            time.sleep_ms(200)  # Give time for data to transit
            print("Broadcasting DATA_SENT")
            multitest.broadcast("DATA_SENT")
        except Exception as e:
            print(f"Device write error: {e}")
            print("Broadcasting DATA_SENT despite error")
            multitest.broadcast("DATA_SENT")
            return

        # Allow time for host IRQ processing and printing
        time.sleep_ms(500)
        print("Device finished")

    finally:
        # No explicit cleanup needed for CDC device
        pass
