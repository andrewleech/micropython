.. currentmodule:: machine
.. _machine.USBHost:

class USBHost -- USB host mode support
=======================================

This class provides USB host mode functionality allowing MicroPython to act
as a USB host device. It can discover, enumerate, and communicate with various
types of USB devices including CDC (Communications Device Class), MSC (Mass
Storage Class), and HID (Human Interface Device) devices.

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

.. method:: USBHost.devices()

   Returns a list of all connected USB devices. Each device in the list
   is a USBDevice object with properties like vid, pid, manufacturer, etc.

.. method:: USBHost.cdc_devices()

   Returns a list of connected CDC devices (Communications Device Class).
   These are typically serial communication devices.
   
   Each device in the returned list is a USBH_CDC object.

.. method:: USBHost.msc_devices()

   Returns a list of connected MSC devices (Mass Storage Class).
   These are storage devices like USB flash drives.
   
   Each device in the returned list is a USBH_MSC object that can be
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

   Returns a list of connected HID devices (Human Interface Devices, like keyboards and mice).
   
   Each device in the returned list is a USBH_HID object.

USBDevice class
--------------

.. class:: USBDevice

   This class is not meant to be instantiated directly, but is returned by
   ``USBHost.devices()``.
   
   .. attribute:: USBDevice.vid

      Returns the Vendor ID (VID) of the device as an integer.

   .. attribute:: USBDevice.pid

      Returns the Product ID (PID) of the device as an integer.

   .. attribute:: USBDevice.manufacturer

      Returns the manufacturer string of the device, or ``None`` if not available.

   .. attribute:: USBDevice.product

      Returns the product string of the device, or ``None`` if not available.

   .. attribute:: USBDevice.serial

      Returns the serial number string of the device, or ``None`` if not available.

USBH_CDC class
-------------

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

   .. method:: USBH_CDC.irq(handler=None, trigger=IRQ_RX)

      Set a callback to be triggered when data is received.
      
      - ``handler`` is the function to call when data is received.
      - ``trigger`` is the event that triggers the callback.
      
      The handler will be passed the CDC device object.

USBH_MSC class
-------------

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

Constants
---------

.. data:: USBH_CDC.IRQ_RX

   Constant used with ``irq()`` method to trigger on data reception.