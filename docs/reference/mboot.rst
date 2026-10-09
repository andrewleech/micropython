.. _mboot_bootloader:

Mboot bootloader support
========================

Mboot provides signed firmware boot and update support to MicroPython using the
MCUboot image-validation and swap engine. The bootloader validates images before
starting them. On STM32 builds, :func:`machine.bootloader` enters the bootloader
or requests a filesystem update.

Supported boards
----------------

* ``NUCLEO_H563ZI`` (STM32H5) uses two image slots. The bootloader starts an
  update as a test image and reverts to the previous image if the application
  does not confirm it.
* ``PYBD_SF6`` (STM32F7) uses one image slot. An update replaces the current
  image, so there is no automatic revert.

Updating firmware
-----------------

Both boards support USB DFU and filesystem updates. If the bootloader cannot
find a valid image, it enters DFU mode so firmware can be installed again. The
single-slot PYBD_SF6 configuration has no previous image to restore if an update
fails.

Building and signing
--------------------

Build the bootloader and MicroPython firmware for the same board. Select the
MCUboot-backed application with ``MBOOT_BACKEND=mcuboot``:

.. code-block:: bash

    make -C ports/stm32/mboot/mcuboot BOARD=NUCLEO_H563ZI
    make -C ports/stm32 BOARD=NUCLEO_H563ZI MBOOT_BACKEND=mcuboot

Use ``PYBD_SF6`` instead of ``NUCLEO_H563ZI`` to build for that board. An
initial installation requires programming both the bootloader and the first
firmware image with a debugger. Later firmware updates can use DFU or filesystem
loading.

The bootloader accepts images signed by its configured public key. Set
``MBOOT_PUBKEY`` for the bootloader build and use the matching
``MBOOT_SIGN_KEY`` to sign firmware. The test signing keys in the repository
are public and are only suitable for testing.
