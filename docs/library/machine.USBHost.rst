.. currentmodule:: machine
.. _machine.USBHost:

class USBHost -- USB Host controller
====================================

.. note:: ``machine.USBHost`` is currently only available on select ports with TinyUSB and
          hardware USB host controller support.

The USBHost class provides a way to connect to and communicate with USB devices connected to the
microcontroller's USB host port.

Constructors
------------

.. class:: USBHost()

   Create a USBHost object.

Methods
-------

.. method:: USBHost.active([value])

   With no arguments, get the active state of the USB host.

   With ``value`` set to ``True``, enable the USB host.
   
   With ``value`` set to ``False``, disable the USB host.

   Example::

      # Enable USB host
      host = machine.USBHost()
      host.active(True)
      
      # Check if USB host is active
      print(host.active())  # Returns True

.. method:: USBHost.devices()

   Returns a list of connected USB devices.
   
   Each device in the returned list is a USBDevice object with properties for
   the device attributes.
   
   Example::

      # Get list of connected devices
      devices = host.devices()
      for dev in devices:
          print(f"Device: VID=0x{dev.vid:04x}, PID=0x{dev.pid:04x}")
          if dev.manufacturer:
              print(f"  Manufacturer: {dev.manufacturer}")
          if dev.product:
              print(f"  Product: {dev.product}")
          if dev.serial:
              print(f"  Serial: {dev.serial}")

.. method:: USBHost.cdc_devices()

   Returns a list of connected CDC devices (virtual serial ports).
   
   Each device in the returned list is a USBH_CDC object which implements the
   stream interface.
   
   Example::

      # Get list of CDC devices
      cdc_devices = host.cdc_devices()
      if cdc_devices:
          # Use the first CDC device
          cdc = cdc_devices[0]
          cdc.write(b'Hello from MicroPython!\r\n')
          if cdc.any():
              data = cdc.read(64)
              print(f"Received: {data}")
              
.. method:: USBHost.msc_devices()

   Returns a list of connected MSC devices (Mass Storage Class, like USB flash drives).
   
   Each device in the returned list is a USBH_MSC object which implements the
   block device interface for use with a filesystem.
   
   Example::

      # Get list of MSC devices
      msc_devices = host.msc_devices()
      if msc_devices:
          # Use the first MSC device
          msc = msc_devices[0]
          if msc.is_connected():
              # Mount the device as a filesystem
              import vfs
              vfs.mount(msc, '/usb')
              
              # List files
              import os
              print(os.listdir('/usb'))

.. method:: USBHost.hid_devices()

   Returns a list of connected HID devices (Human Interface Devices, like keyboards and mice).
   
   Each device in the returned list is a USBH_HID object.
   
   Example::

      # Get list of HID devices
      hid_devices = host.hid_devices()
      for hid in hid_devices:
          protocol = "Unknown"
          if hid.protocol == hid.PROTOCOL_KEYBOARD:
              protocol = "Keyboard"
          elif hid.protocol == hid.PROTOCOL_MOUSE:
              protocol = "Mouse"
          print(f"HID device: {protocol}, usage page={hid.usage_page}, usage={hid.usage}")
          
          # Get the latest report from the device
          report = hid.get_report()
          if report:
              print(f"Latest report: {report}")

USBDevice class
--------------

.. class:: USBDevice

   This class is not meant to be instantiated directly, but is returned by
   ``USBHost.devices()``.
   
   .. method:: USBDevice.vid

      Returns the device's Vendor ID.

   .. method:: USBDevice.pid

      Returns the device's Product ID.

   .. method:: USBDevice.serial

      Returns the device's serial number as a string, or ``None`` if not available.

   .. method:: USBDevice.manufacturer

      Returns the device's manufacturer string, or ``None`` if not available.

   .. method:: USBDevice.product

      Returns the device's product string, or ``None`` if not available.

USBH_CDC class
-------------

.. class:: USBH_CDC

   This class is not meant to be instantiated directly, but is returned by
   ``USBHost.cdc_devices()``. It implements the :term:`stream` interface.
   
   .. method:: USBH_CDC.is_connected()

      Returns ``True`` if the CDC device is still connected, ``False`` otherwise.

   .. method:: USBH_CDC.any()

      Returns the number of bytes available to read from the CDC device.

   .. method:: USBH_CDC.read(size)

      Read up to ``size`` bytes from the CDC device. Returns a bytes object with
      the data read.

   .. method:: USBH_CDC.write(data)

      Write ``data`` to the CDC device. Returns the number of bytes written.

   .. method:: USBH_CDC.irq(handler=None, trigger=IRQ_RX, hard=False)

      Set a callback to be triggered when data is received.
      
      - ``handler`` is the function to call when an event occurs.
      - ``trigger`` is the event that triggers the callback.
      
      The handler will be passed the CDC device object.
      
      Example::

         def on_rx(cdc):
             print(f"Data available: {cdc.any()} bytes")
             if cdc.any():
                 print(f"Received: {cdc.read(64)}")
         
         cdc.irq(handler=on_rx, trigger=cdc.IRQ_RX)
         
USBH_MSC class
-------------

.. class:: USBH_MSC

   This class is not meant to be instantiated directly, but is returned by
   ``USBHost.msc_devices()``. It implements the block device protocol for use
   with filesystem operations.
   
   .. method:: USBH_MSC.is_connected()

      Returns ``True`` if the MSC device is still connected, ``False`` otherwise.

   .. method:: USBH_MSC.readblocks(block_num, buf, offset=0)

      Read data from the specified block into ``buf``.
      
      - ``block_num`` is the block number to read from
      - ``buf`` is a buffer to store the data in
      - ``offset`` is the offset within the block to start reading from
      
      This method implements part of the block device protocol and is typically
      used by the filesystem driver rather than called directly.

   .. method:: USBH_MSC.writeblocks(block_num, buf, offset=0)

      Write data from ``buf`` to the specified block.
      
      - ``block_num`` is the block number to write to
      - ``buf`` is a buffer containing the data to write
      - ``offset`` is the offset within the block to start writing to
      
      This method implements part of the block device protocol and is typically
      used by the filesystem driver rather than called directly.

   .. method:: USBH_MSC.ioctl(cmd, arg)

      Control the block device.
      
      - ``cmd`` is the command to perform
      - ``arg`` is an optional argument
      
      This method implements part of the block device protocol and is typically
      used by the filesystem driver rather than called directly.
      
      Common commands include:
      
      - ``MP_BLOCKDEV_IOCTL_INIT`` - Initialize the device
      - ``MP_BLOCKDEV_IOCTL_DEINIT`` - Deinitialize the device
      - ``MP_BLOCKDEV_IOCTL_SYNC`` - Synchronize/flush cached data
      - ``MP_BLOCKDEV_IOCTL_BLOCK_COUNT`` - Get total block count
      - ``MP_BLOCKDEV_IOCTL_BLOCK_SIZE`` - Get block size
      
      Example using the MSC device with a filesystem::
      
         # Mount the MSC device with a FAT filesystem
         import vfs
         msc = host.msc_devices()[0]
         vfs.VfsFat.mkfs(msc)  # Format if needed
         vfs.mount(msc, '/usb')
         
         # Use the filesystem
         import os
         os.listdir('/usb')
         
         # Write a file
         with open('/usb/test.txt', 'w') as f:
             f.write('Hello from MicroPython!')
             
         # Read a file
         with open('/usb/test.txt', 'r') as f:
             print(f.read())

USBH_HID class
-------------

.. class:: USBH_HID

   This class is not meant to be instantiated directly, but is returned by
   ``USBHost.hid_devices()``. It provides access to USB HID devices like keyboards,
   mice, and other input devices.
   
   .. method:: USBH_HID.is_connected()

      Returns ``True`` if the HID device is still connected, ``False`` otherwise.

   .. method:: USBH_HID.protocol

      Returns the device's HID protocol type. This can be one of the following constants:
      
      - ``USBH_HID.PROTOCOL_NONE`` - No specific protocol
      - ``USBH_HID.PROTOCOL_KEYBOARD`` - Keyboard protocol
      - ``USBH_HID.PROTOCOL_MOUSE`` - Mouse protocol
      - ``USBH_HID.PROTOCOL_GENERIC`` - Generic HID protocol

   .. method:: USBH_HID.usage_page

      Returns the device's HID usage page, which is a number that identifies the general
      category of the device according to the USB HID specification.

   .. method:: USBH_HID.usage

      Returns the device's HID usage, which is a number that identifies the specific
      function of the device according to the USB HID specification.

   .. method:: USBH_HID.get_report()

      Returns the most recently received report from the device as a bytes object,
      or ``None`` if no report has been received yet.

   .. method:: USBH_HID.request_report()

      Requests a new report from the device. Returns ``True`` if the request was
      successfully sent, ``False`` otherwise.

   .. method:: USBH_HID.send_report(data)

      Sends an output report to the device. This is typically used for devices that
      have output capabilities, such as keyboard LEDs or rumble motors.
      
      - ``data`` is a bytes object containing the report data to send.
      
      Returns ``True`` if the report was successfully sent, ``False`` otherwise.

   .. method:: USBH_HID.irq(handler=None, trigger=IRQ_REPORT, hard=False)

      Set a callback to be triggered when a report is received.
      
      - ``handler`` is the function to call when a report is received.
      - ``trigger`` is the event that triggers the callback.
      
      The handler will be passed the HID device object.
      
      Example::

         def on_report(hid, report):
            print(f"Received HID report: {report}")
         
         hid.irq(handler=on_report, trigger=hid.IRQ_REPORT)

Constants
---------

.. data:: USBH_CDC.IRQ_RX

   Constant used with CDC ``irq()`` method to trigger on data reception.

.. data:: USBH_HID.PROTOCOL_NONE
.. data:: USBH_HID.PROTOCOL_KEYBOARD
.. data:: USBH_HID.PROTOCOL_MOUSE
.. data:: USBH_HID.PROTOCOL_GENERIC

   Constants representing different HID protocol types.

.. data:: USBH_HID.IRQ_REPORT

   Constant used with HID ``irq()`` method to trigger on report reception.