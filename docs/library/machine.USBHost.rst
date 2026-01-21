.. currentmodule:: machine
.. _machine.USBHost:

class USBHost -- USB host mode support
======================================

This class provides USB host mode functionality allowing MicroPython to act
as a USB host device. It can discover, enumerate, and communicate with various
types of USB devices including CDC (Communications Device Class), MSC (Mass
Storage Class), and HID (Human Interface Device) devices.

.. note:: ``machine.USBHost`` is currently supported on the esp32, mimxrt, rp2,
          and stm32 ports. Requires a board variant configured for USB host mode
          (e.g. boards with ``_USBHOST`` suffix).

Example usage::

    import machine

    # Create and activate USB host
    host = machine.USBHost()
    host.active(True)

    # Check for connected devices
    devices = host.devices()
    for device in devices:
        print(f"Device: VID={device.vid:04x}, PID={device.pid:04x}")

    # Work with specific device types
    cdc_devices = host.cdc_devices()
    msc_devices = host.msc_devices()
    hid_devices = host.hid_devices()

Constructor
-----------

.. class:: USBHost()

   Create a USBHost object.

Methods
-------

.. method:: USBHost.active([state])

   Without arguments, return the current active state of the USB host.

   With one argument, set the active state. If ``True``, activates USB host
   mode and begins looking for connected devices. If ``False``, deactivates
   USB host mode.

.. method:: USBHost.deinit()

   Shut down the USB host stack and release all resources. After calling this,
   the ``USBHost`` object must be re-created to use USB host again.

.. method:: USBHost.devices()

   Returns a tuple of all connected USB devices. Each device in the tuple
   is a :class:`USBH_Device` object with properties like vid, pid, manufacturer, etc.

.. method:: USBHost.cdc_devices()

   Returns a tuple of connected CDC devices (Communications Device Class).
   These are typically serial communication devices.

   Each device in the returned tuple is a USBH_CDC object.

.. method:: USBHost.msc_devices()

   Returns a tuple of connected MSC devices (Mass Storage Class).
   These are storage devices like USB flash drives.

   Each device in the returned tuple is a USBH_MSC object that can be
   used as a block device for mounting filesystems.

   Example::

      # Get MSC devices and mount the first one
      msc_devices = host.msc_devices()
      if msc_devices:
          msc = msc_devices[0]

          # Mount the device as a filesystem
          import vfs
          vfs.mount(msc, '/usb')

          # List files
          import os
          print(os.listdir('/usb'))

.. method:: USBHost.hid_devices()

   Returns a tuple of connected HID devices (Human Interface Devices, like keyboards and mice).

   Each device in the returned tuple is a USBH_HID object.

USBH_Device class
-----------------

.. class:: USBH_Device

   This class is not meant to be instantiated directly, but is returned by
   ``USBHost.devices()``.

   .. attribute:: USBH_Device.vid

      The Vendor ID (VID) of the device as an integer.

   .. attribute:: USBH_Device.pid

      The Product ID (PID) of the device as an integer.

   .. attribute:: USBH_Device.manufacturer

      The manufacturer string of the device, or ``None`` if not available.

   .. attribute:: USBH_Device.product

      The product string of the device, or ``None`` if not available.

   .. attribute:: USBH_Device.serial

      The serial number string of the device, or ``None`` if not available.

   .. attribute:: USBH_Device.dev_class

      The USB device class code (``bDeviceClass`` from the device descriptor).
      A value of 0 means class information is in the interface descriptors.

   .. attribute:: USBH_Device.dev_subclass

      The USB device subclass code (``bDeviceSubClass``).

   .. attribute:: USBH_Device.dev_protocol

      The USB device protocol code (``bDeviceProtocol``).

USBH_CDC class
--------------

.. class:: USBH_CDC

   This class is not meant to be instantiated directly, but is returned by
   ``USBHost.cdc_devices()``. It provides access to USB CDC devices for
   serial communication.

   This class implements the stream protocol and can be used with standard
   stream methods like read() and write().

   .. method:: USBH_CDC.is_connected()

      Returns ``True`` if the CDC device is still connected, ``False`` otherwise.

   .. method:: USBH_CDC.any()

      Returns the number of bytes available for reading.

   .. method:: USBH_CDC.read(nbytes)

      Read at most ``nbytes`` from the device. Returns a bytes object.

   .. method:: USBH_CDC.write(buf)

      Write the bytes from ``buf`` to the device. Returns the number of bytes written.

   .. method:: USBH_CDC.set_line_coding(baudrate=115200, bits=8, parity=0, stop=0)

      Set the serial line coding parameters. Defaults are applied automatically
      when the device is first connected.

      - ``baudrate``: baud rate (default 115200)
      - ``bits``: data bits (default 8)
      - ``parity``: 0=none, 1=odd, 2=even (default 0)
      - ``stop``: 0=1 stop bit, 1=1.5, 2=2 (default 0)

   .. method:: USBH_CDC.readline()

      Read a line from the device. Part of the stream protocol.

   .. method:: USBH_CDC.readinto(buf)

      Read bytes into an existing buffer. Part of the stream protocol.

   .. method:: USBH_CDC.close()

      Close the stream.

   .. method:: USBH_CDC.irq(handler=None, trigger=IRQ_RX)

      Set a callback to be triggered when data is received.

      - ``handler`` is the function to call when data is received.
      - ``trigger`` is the event that triggers the callback.

      The handler will be passed the CDC device object.

USBH_MSC class
--------------

.. class:: USBH_MSC

   This class is not meant to be instantiated directly, but is returned by
   ``USBHost.msc_devices()``. It provides access to USB Mass Storage devices
   and implements the block device protocol for use with filesystems.

   .. method:: USBH_MSC.is_connected()

      Returns ``True`` if the MSC device is still connected, ``False`` otherwise.

   .. method:: USBH_MSC.readblocks(block_num, buf[, offset])

      Read blocks from the device. This is part of the block device protocol.

   .. method:: USBH_MSC.writeblocks(block_num, buf[, offset])

      Write blocks to the device. This is part of the block device protocol.

   .. method:: USBH_MSC.ioctl(op, arg)

      Control the device. This is part of the block device protocol.

      Example of mounting an MSC device::

         msc_devices = host.msc_devices()
         if msc_devices:
             msc = msc_devices[0]
             import vfs
             vfs.mount(msc, '/usb')

             # Write a file
             with open('/usb/test.txt', 'w') as f:
                 f.write('Hello from MicroPython!')

             # Read a file
             with open('/usb/test.txt', 'r') as f:
                 print(f.read())

USBH_HID class
--------------

.. class:: USBH_HID

   Represents a USB HID (Human Interface Device) connected to the host.
   Returned by :meth:`USBHost.hid_devices`, not created directly.

   .. method:: USBH_HID.is_connected()

      Returns ``True`` if the HID device is still connected.

   .. method:: USBH_HID.get_report()

      Returns the most recent HID report as ``bytes``, or ``None`` if no
      report is available. The report format depends on the device type
      (keyboard, mouse, etc.).

   .. method:: USBH_HID.readinto(buf)

      Read the most recent HID report into *buf*, returning the number of bytes
      copied (0 if no report is available). This avoids allocating a new bytes
      object on each call.

   .. method:: USBH_HID.irq(handler=None, trigger=IRQ_REPORT)

      Set a callback to be called when a new HID report is received.
      Pass ``None`` to disable.

      *handler*: a function that takes one argument (the ``USBH_HID`` object).
      *trigger*: the event that triggers the callback (default ``IRQ_REPORT``).

Constants
---------

.. data:: USBH_CDC.IRQ_RX

   Constant used with ``irq()`` method to trigger on data reception.

.. data:: USBH_HID.IRQ_REPORT

   IRQ trigger for report received events.

.. data:: USBH_HID.PROTOCOL_KEYBOARD
          USBH_HID.PROTOCOL_MOUSE
          USBH_HID.PROTOCOL_GENERIC

   HID protocol constants. ``PROTOCOL_KEYBOARD`` and ``PROTOCOL_MOUSE`` are
   boot protocol codes (values 1 and 2 from the HID specification).
   ``PROTOCOL_GENERIC`` (value 3) is a MicroPython-defined constant for
   devices that don't use boot protocol.

REPL access on host-only boards
-------------------------------

Some board variants use the sole USB port for host mode, disabling USB CDC
device mode entirely (e.g. ``MIMXRT1010_EVK_USBHOST``,
``MIMXRT1060_EVK_USBHOST``, ``MIMXRT1170_EVK_USBHOST``). On these boards
MicroPython boots normally but the REPL has no input or output source.

To get a REPL, use ``os.dupterm()`` to redirect it to a UART. On NXP EVK
boards, ``UART(0)`` (LPUART1) is connected to the MCU-Link debug probe's
UART bridge so no extra wiring is needed.

Add the following to ``boot.py``::

    import machine, os
    os.dupterm(machine.UART(0, 115200))

The REPL will then be available on the MCU-Link's USB serial interface at
115200 baud. Dual-port variants (e.g. ``MIMXRT1170_EVK_USBHOST_DUAL``) use
one USB port for CDC REPL and the other for host mode, so ``dupterm`` is not
required.
