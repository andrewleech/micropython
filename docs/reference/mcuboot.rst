.. _mcuboot_bootloader:

MCUboot bootloader support
==========================

.. contents::
   :local:
   :depth: 2

This page covers the supported MicroPython MCUboot builds, how to sign and install firmware, and how to update it. The running firmware talks to the bootloader through the :mod:`mcuboot` module.

Supported hardware:

* The stm32 port on STM32H5 with the **NUCLEO_H563ZI** board definition and on STM32F7 with the **PYBD_SF6** board definition. Addresses, USB identifiers and flash layouts below are for NUCLEO_H563ZI unless noted; PYBD_SF6 has a single-slot layout described in :ref:`mcuboot_pybd_sf6`.
* The unix port's ``mcuboot`` and ``mcuboot_single`` variants, which keep flash in a file for module tests. They do not include a bootloader.

Hardware tests cover NUCLEO_H563ZI and PYBD-SF6W. The H5 results are from earlier revisions, not the current tree; F7 checks cover a signed DFU update, MicroPython boot and fsload from SPI flash. No physical power cuts have been run on either board, and PYBD invalid-image refusal and DFU re-entry are untested.

.. warning::

   Some operations can erase data or change protection settings in ways that are hard to reverse. Read :ref:`mcuboot_warnings` before changing option bytes, read-out protection, TrustZone or write protection, using production keys, or programming a board that already holds data.

Architecture
------------

Layers
~~~~~~
MCUboot bootutil validates images and handles the selected swap policy. MicroPython adds the flash map, application interface, update log, DFU and filesystem update paths. The stm32 port supplies board flash access and bootloader setup.


Bootloader role and application role
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

The same board configuration produces two builds.

The **bootloader** is one image holding MCUboot, the boot decision, the DFU front end and the filesystem loader. It sits at the start of flash (the ``boot`` area), reads flash through the flash map and validates images. It swaps the slots on NUCLEO_H563ZI and boots the only slot on PYBD_SF6. On the NUCLEO_H563ZI with DFU and the FAT reader it is about 52 KB (52284 bytes of text) and the layout reserves 64 KiB (``MCUBOOT_BOOT_SIZE``). On the PYBD_SF6 it is 42848 bytes of text in 64 KiB. It is the only code that verifies signatures. DFU and fsload write image data to the update slot (the primary slot on PYBD_SF6). On NUCLEO_H563ZI, :class:`mcuboot.Writer` writes to the secondary slot and :func:`mcuboot.request_upgrade` marks that image pending. None of the update paths writes the boot area. The :mod:`mcuboot` module also sets the confirmed flag in the swap state of the primary slot and appends to the update log.

The **application** is the MicroPython firmware, linked to start after the image header at the start of the primary slot and signed by ``tools/mcuboot_sign.py``. Its build contains the flash map, ``bootutil_public.c`` (no crypto code), the update log and request code, and the :mod:`mcuboot` module. It also has the same flash policy as the bootloader (``port_flash.c`` over ``flash.c``) and, on the STM32H5, the NMI handler that goes with it (see :ref:`mcuboot_torn_write`). It cannot start without the bootloader, since it is linked into the primary slot behind the header.

Image slots and swap
~~~~~~~~~~~~~~~~~~~~~~~~~~~

An MCUboot image is the firmware binary with a header in front (``MCUBOOT_HEADER_SIZE``, 0x400 on the NUCLEO_H563ZI) and a list of TLV records behind it. The records hold the SHA-256 of the image, a hash of the public key that signed it, the ECDSA P-256 signature, the security counter and a *layout id* that ties the image to the flash layout it was built for (see :ref:`mcuboot_signing`).

The flash layout of the NUCLEO_H563ZI board (2 MiB, two banks of 128 sectors of 8 KiB):

.. list-table::
   :header-rows: 1
   :widths: 25 25 15 35

   * - Area
     - Address
     - Size
     - Content
   * - ``boot``
     - ``0x08000000``
     - 64K
     - the bootloader; its last 64 bytes are an information block read by ``mcuboot.bootloader_info()``
   * - ``primary``
     - ``0x08010000``
     - 640K
     - the running image: header, then the firmware linked at ``0x08010400``, TLVs, and at the end of the slot the swap state
   * - ``log``
     - ``0x080B0000``
     - 16K
     - the update log (see :ref:`mcuboot_update_log`)
   * - ``seccnt``
     - ``0x080B4000``
     - 16K
     - the security counter records (only with ``MCUBOOT_ROLLBACK_COUNTER = 1`` and without ``MCUBOOT_SECCNT_PORT``)
   * - ``shadow``
     - ``0x080B8000``
     - 16K
     - the shadow copies of the swap state words (see :ref:`mcuboot_torn_write`)
   * - ``fs``
     - ``0x080BC000``
     - 272K
     - the application filesystem
   * - ``secondary``
     - ``0x08100000``
     - 648K
     - the update slot: one spare sector followed by the update image area, then the swap state

The two slots are in different banks, so the running firmware can erase and write the update slot while it executes from the other bank.

The supported STM32 boards use two slot policies: swap using offset on NUCLEO_H563ZI and single slot on PYBD_SF6.

``MCUBOOT_POLICY_SEL_SWAP`` on NUCLEO_H563ZI
    Two slots. A new image is written to the secondary slot and the bootloader swaps the contents at reset. The image is a test image: if the application does not confirm it, the bootloader swaps back to the old image at the next reset. This board uses ``MCUBOOT_SWAP_MODE_SEL_OFFSET``:

    The secondary slot is one erase unit larger than the primary slot, and the update image starts one erase unit into it. No extra area is needed. An update of one erase unit or less is refused (see :ref:`mcuboot_offset_limit`).

``MCUBOOT_POLICY_SEL_SINGLE`` on PYBD_SF6
    One slot. DFU writes the new image over the primary slot in place. An invalid image already in the slot is not started; if no valid image remains, the bootloader enters DFU. Fsload validates the file before writing, so a rejected file leaves the current image intact. If a reset leaves no valid image during fsload, the bootloader retries from the intent record while the file is available; otherwise it enters DFU (see :ref:`mcuboot_fsload`). The single policy needs ``MCUBOOT_ROLLBACK_COUNTER = 1`` because the old image's version is gone once the slot is overwritten. The whole slot is the update region and the largest image is the slot size less ``MCUBOOT_TRAILER_SIZE`` (48 bytes on PYBD_SF6). The :mod:`mcuboot` functions that need an update slot raise ``OSError(EPERM)``.

.. _mcuboot_offset_limit:

Limit of swap using offset
~~~~~~~~~~~~~~~~~~~~~~~~~~

Swap using offset can resume an interrupted swap only when the update image, including its header and TLVs, is larger than one erase unit. MicroPython images on the supported boards exceed this limit. The update paths reject images of one erase unit or less with ``ERR_TOO_SMALL``; :meth:`mcuboot.Writer.finish` raises ``OSError(EINVAL)``. A cut while swapping a smaller image can leave the device in DFU.

Boot decision
~~~~~~~~~~~~~

At every reset the bootloader runs this sequence:

1. Initialise the clock and the log UART, and take the request the application may have left (see :ref:`mcuboot_handoff`). The request and the handoff register are cleared before anything acts on them, so a crash during recovery cannot repeat the request. After a power-on reset a leftover request is discarded.
2. Initialise the flash driver and check the flash map against it. A failure goes to recovery.
3. If the previous three boots all ended in a fault of the bootloader itself (the fault handlers count in a backup register), go to recovery.
4. Run the startup scrub of the flash policy, which repairs interrupted flash writes (see :ref:`mcuboot_torn_write`). A failure goes to recovery.
5. If ``MCUBOOT_ROLLBACK_COUNTER = 1``, initialise the flash-backed security counter. A failure is logged and remembered (see :ref:`mcuboot_recovery`).
6. Request recovery mode if the application left a request or the board's forced entry input is active (the USER button held at reset on the NUCLEO_H563ZI).
7. Unless recovery was requested, run MCUboot's ``boot_go()``. If an update is pending, its layout id is checked first. ``boot_go()`` validates a pending update image, swaps or reverts as the swap state says, validates the image it is about to start and returns it. The bootloader then records the outcome in the update log and jumps to the image. A pending swap or revert is also completed when recovery was requested, before the bootloader stays in recovery, because a DFU session erases the update slot and that holds the image to revert to.
8. If ``boot_go()`` finds no bootable image, log an entry and clean up the primary slot (see :ref:`mcuboot_recovery`; the single policy leaves its only slot as it is). With the single policy and fsload, an intent record left by an interrupted fsload makes the bootloader run that request again (step 9). Otherwise the bootloader goes to DFU mode with no timeout.
9. If the request was a filesystem load, run it (see :ref:`mcuboot_fsload`). A successful load, or a failed one while the old image is still bootable, resets the device so step 7 runs from a clean state. A failed load with no bootable image ends in DFU mode.
10. Otherwise enter DFU mode.

What is validated on every boot
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

The supported STM32 configurations validate the primary slot image before starting it: the header, SHA-256 hash, ECDSA signature against the public key compiled into the bootloader and the security counter. A swap boot also validates the update image before swapping. Boot time grows with image size.

The layout id is checked when an update is received (DFU, fsload) and, at boot, for a pending update image that got into the secondary slot another way (:class:`mcuboot.Writer`, :func:`mcuboot.request_upgrade` or a programmer). An image with a missing or different layout id is rejected and removed, not swapped in.

.. _mcuboot_config:

Configuration
-------------

The board's ``mpconfigboard.h`` sets the slot sizes, addresses and policy. ``mcuboot_dev.h`` describes its flash devices, and ``shared/mcuboot/include/mcuboot_layout.h`` derives the remaining areas and rejects invalid configurations with ``#error``. The NUCLEO_H563ZI configuration includes:

.. code-block:: c

    #define MCUBOOT_PRIMARY_SIZE                (640 * 1024)
    #define MCUBOOT_SECONDARY_ADDR              (0x08100000)
    #define MCUBOOT_ROLLBACK_COUNTER            (1)
    #define MCUBOOT_FIH_LEVEL                   (1)
    #define MCUBOOT_FSLOAD_FAT                  (1)


Validation
~~~~~~~~~~

An invalid configuration stops both the bootloader and the application build with an ``#error`` that names the problem. ``mcuboot_layout.h`` refuses:

* a board without ``MCUBOOT_PRIMARY_SIZE``, ``MCUBOOT_SECONDARY_ADDR`` (except with the single policy, which must not define it) or ``MCUBOOT_ROLLBACK_COUNTER``, a port that does not describe device 0 (or device 1 once ``MCUBOOT_DEV1_BASE`` is defined), and ``MCUBOOT_FS_ADDR`` without ``MCUBOOT_FS_SIZE``;
* a bootloader region, slot, update log, counter, shadow, scratch, intent or filesystem area that does not lie in one run of equal sectors or does not start at a multiple of the erase unit of that run, a size that is not a multiple of it where the area is sized in units, and any two areas that overlap;
* a primary slot that is not in device 0, that overlaps the bootloader region, or whose address or header size does not fit ``MCUBOOT_VTOR_ALIGN`` and the trailer alignment;
* log, counter, shadow, scratch and intent areas that do not fit in device 0, a secondary slot or filesystem that does not fit in its device, a filesystem with no room, and slots of more than 4096 sectors;
* a write unit above 32 bytes, an erase or write unit that is not a power of two, an erase unit below the write unit, runs that do not add up to the device size or do not hold whole erase units, a device description that gives both ``MCUBOOT_DEVn_ERASE`` and ``MCUBOOT_DEVn_RUNS`` or neither, more than four runs, a scratch or shadow area whose run has another erase unit than the slots, a trailer alignment that is not a multiple of the write unit of a slot device, and, on a device with ECC, a write unit that differs from the trailer alignment;
* a device with ECC and ``MCUBOOT_DFU = 0``;
* a device 1 that is not above device 0, that has ECC, whose erase unit (of every run) is not a multiple of the 4 KiB erase block of the SPI flash driver, or, when the secondary slot is on it, whose erase unit differs from the one of device 0 or whose write unit differs from the trailer alignment;
* an unknown ``MCUBOOT_POLICY`` or ``MCUBOOT_SWAP_MODE``, a swap mode with a policy other than swap, swap using move with a primary slot of less than two erase units, ``MCUBOOT_VERSION_CHECK`` with the security counter;
* the single policy without ``MCUBOOT_ROLLBACK_COUNTER = 1`` (a version comparison with the primary slot cannot run after the only slot has been overwritten);
* a scratch area (``MCUBOOT_SCRATCH_ADDR``, ``MCUBOOT_SCRATCH_SIZE``) that is smaller than an erase unit, not a multiple of it, outside device 0, overlapping another area, or given without swap using scratch, swap using scratch on a device with ECC, and an intent area (``MCUBOOT_INTENT_ADDR``) that is unaligned, outside device 0, overlapping another area, or given without the single policy with fsload; ``MCUBOOT_SECCNT_ADDR`` without the security counter in flash and ``MCUBOOT_SHADOW_ADDR`` on a device without ECC;
* ``MCUBOOT_HASH_DIRECT`` with a slot device that is not memory mapped, with swap using offset or with fsload;
* a header and TLV reserve that leave no room in the largest image, and an update slot without room for an image in front of its trailer;
* ``MCUBOOT_FIH_LEVEL`` above 3, an unknown ``MCUBOOT_CRYPTO``, ``MCUBOOT_SECCNT_PORT`` without ``MCUBOOT_ROLLBACK_COUNTER = 1``, ``MCUBOOT_FSLOAD_GZIP`` without a reader, ``MCUBOOT_LOG_LEVEL`` outside 0 to 4, a ``MCUBOOT_TMPBUF_SZ`` that is not a multiple of the trailer alignment, a security counter that does not fit 32 bits and USB identifiers that are not 16 bit.


.. _mcuboot_pybd_sf6:

STM32F7 board (PYBD_SF6)
~~~~~~~~~~~~~~~~~~~~~~~~

PYBD_SF6 is an STM32F767 board with 2 MiB of flash and SPI flash #1 for the application filesystem. It uses the single-slot policy. Its 1.1 MB firmware image needs five of the seven 256 KiB sectors, so two slots do not fit. DFU and fsload write the only slot in place. At boot, the bootloader validates the image and enters DFU if no valid image is available.

.. list-table::
   :header-rows: 1
   :widths: 14 16 22 48

   * - Area
     - Address
     - Size
     - Content
   * - ``boot``
     - ``0x08000000``
     - 64 KiB (2 x 32 KiB)
     - The bootloader (42848 bytes of text and 44 bytes of data) and its information block in the last 64 bytes (``0x0800FFC0``).
   * - ``log``
     - ``0x08010000``
     - 64 KiB (2 x 32 KiB)
     - The update log.
   * - ``intent``
     - ``0x08020000``
     - 128 KiB (1 x 128 KiB)
     - The intent area of fsload.
   * - ``primary``
     - ``0x08040000``
     - 1280 KiB (5 x 256 KiB)
     - The only slot: image header, firmware and TLVs.
   * - ``seccnt``
     - ``0x08180000``
     - 512 KiB (2 x 256 KiB)
     - The security counter records.
   * - ``fs``
     - ``0x80000000``
     - 2 MiB, on device 1
     - The FAT filesystem on SPI flash #1, read by fsload.

The internal flash has three runs of equal sectors: four of 32 KiB, one of 128 KiB and seven of 256 KiB. Every area lies in one run and has the sector size of that run. The slot and the counter are in the 256 KiB sectors, the intent area is the 128 KiB sector and the log is in 32 KiB sectors. An update erases the five 256 KiB sectors of the slot. The SPI flash is a 2 MiB or an 8 MiB chip, detected at run time. The layout takes the 2 MiB of the smaller chip, and the bootloader reads the filesystem inside that window only, so on an 8 MiB chip a file stored beyond the first 2 MiB cannot be read by fsload. The bootloader calls the early init of the board (``board_early_init_sf6``) so that the chip parameters are set before the flash is used. It also runs the board hook of mboot (``MBOOT_BOARD_EARLY_INIT``, which sets the pull-up on pin W23 that selects 500 mA on WBUS-DIP28), at entry and again after it has reset the GPIO ports on the way to the application, so that the application finds the pin as it does after mboot.

The values derived from the layout are: layout id ``63a90b9f``, image header ``0x400`` bytes, application linked at ``0x08040400`` with ``1308624`` bytes available, largest image ``1310672`` bytes (the slot less the 48 byte trailer reservation) and no smallest image. The update region of DFU, fsload and the slot erase is the whole slot. The signed image of the ``MCUBOOT=1`` build is 1112595 bytes. The DFU alternate settings are ``@Application /0x08040000/5*256Kg`` (the slot, readable, erasable and writable) and ``@Update log /0x08010000/2*032Ka`` (read only). The device enumerates as ``f055:dfa5`` with the product string ``PYBD-SF6W MCUboot``.

**Updating.** There is no second slot, so an update replaces the running image.

* DFU: :func:`mcuboot.request_dfu` or :func:`machine.bootloader`, the USR button held at reset, or no bootable image enters the bootloader's DFU mode. ``dfu-util -d f055:dfa5 -a 0 -D firmware.signed.bin`` writes the slot. The bootloader erases each sector before its first write and validates the image when the transfer ends. A rejected image has its header erased, and the next boot enters DFU mode.
* fsload: :func:`mcuboot.request_fsload` names a file on the FAT filesystem. The bootloader validates the whole file before it writes anything. It then records the request in the intent area and writes the slot. If a reset leaves no valid image, the bootloader retries from the intent record while the file remains available. Without the file, it enters DFU mode.
* :class:`mcuboot.Writer` and :func:`mcuboot.request_upgrade` raise ``OSError(EPERM)``. :func:`mcuboot.confirm` does nothing and :func:`mcuboot.state` reports a confirmed image. There is no test swap, so an installed image is final.
* The first image of a device and the bootloader are programmed with a programmer. The board file sets no ``PYOCD_TARGET``, so the deploy targets need it on the command line.

The USR button is on PA13, the pin of SWDIO. While the bootloader samples it, a debugger cannot access the part.

**Flash without ECC.** The STM32F7 flash uses 4 byte program words and has no ECC. The bootloader has no shadow-word policy and relies on each word program completing atomically. A torn image word fails validation; counter records fail their CRC and torn log records are skipped.

The bootloader runs at the 144 MHz board clock with the instruction and data caches of the Cortex-M7 on. After an erase or program the lines of the data cache that cover the flash are invalidated, and the instruction cache is dropped. The data cache is cleaned before a reset (it holds the request region) and cleaned and disabled before the application starts. USB is the OTG_HS core in full-speed mode on its internal PHY (PB14 and PB15) with the 48 MHz clock from the PLLQ output of the board clock. PYBD_SF6 has no log UART. The reset flags, the fault counter and the handoff word are in the RTC backup registers 28 to 30.

**Limits.** One slot, no revert or test swap, and images up to 1310672 bytes. The bootloader log level is 0 (there is no log UART; USB is the console) and the fault injection hardening level is the default, 0. The counter area takes two 256 KiB sectors because the sectors behind the slot are all that size. Check the STM32F7 data sheet before frequent updates.

**Building.**

.. code-block:: bash

    make -C ports/stm32/mcuboot BOARD=PYBD_SF6
    make -C ports/stm32 BOARD=PYBD_SF6 MCUBOOT=1

**Comparison with mboot.** The default PYBD_SF6 firmware boots through ``ports/stm32/mboot``. The numbers are from ``arm-none-eabi-size`` and the build outputs of both bootloaders for this board:

.. list-table::
   :header-rows: 1
   :widths: 22 39 39

   * - Item
     - mboot
     - MCUboot
   * - Bootloader region
     - 32 KiB, one sector at ``0x08000000``
     - 64 KiB, two 32 KiB sectors at ``0x08000000``
   * - Bootloader size
     - 25047 bytes of text, 32 of data; ``firmware.bin`` 32763 bytes
     - 42848 bytes of text, 44 of data; ``firmware.bin`` 65536 bytes (padded to the region)
   * - Other flash it needs
     - none
     - update log 64 KiB, intent area 128 KiB, security counter 512 KiB
   * - Application
     - from ``0x08008000``, 2016 KiB
     - slot of 1280 KiB at ``0x08040000``, images up to 1310672 bytes
   * - Check before starting
     - the stack pointer word has its two low bits clear
     - header, SHA-256, ECDSA P-256 signature, layout id and security counter, at every boot
   * - Rollback protection
     - none
     - security counter in flash
   * - DFU
     - DfuSe (``dfu-util``, ``.dfu`` files); one alternate setting addresses the internal flash outside the bootloader and both SPI flashes
     - DFU 1.1; two alternate settings, the slot and the update log; the image is validated at the end of the transfer
   * - Update from a file
     - fsload with the FAT reader (``MBOOT_FSLOAD``, ``MBOOT_VFS_FAT``)
     - fsload with the FAT reader and the intent area; the file is validated before the slot is written
   * - I2C update interface
     - yes (``MBOOT_I2C_*``)
     - no
   * - Update cut short
     - the application region holds what was written; mboot starts it when its first word passes the check
     - an image that fails validation is rejected; if no valid image remains, the bootloader enters DFU
   * - Test swap and revert
     - none
     - not available with one slot

mboot has a smaller bootloader, no flash set aside for a log or counter, and DFU access to the whole flash and SPI flash. It also has flash upload, an I2C interface and optional packed update files with signing and encryption (``MBOOT_ENABLE_PACKING``, off for this board). MCUboot validates the signed primary image at boot and validates update images before acceptance. Both STM32 configurations use a security counter for rollback protection.

SPI flash
~~~~~~~~~

The supported PYBD_SF6 configuration uses SPI NOR device 1 for its FAT filesystem. The bootloader reads it during fsload but does not write to it.

Building
--------

Prerequisites: initialise ``lib/mcuboot`` with ``git submodule update --init lib/mcuboot`` and the STM32 submodules with ``make -C ports/stm32 BOARD=NUCLEO_H563ZI submodules``. Build ``mpy-cross`` and install the Python packages in ``lib/mcuboot/scripts/requirements.txt``. Deploy targets also need ``pyocd``.

Bootloader
~~~~~~~~~~

.. code-block:: bash

    make -C ports/stm32/mcuboot BOARD=NUCLEO_H563ZI

The output is ``ports/stm32/mcuboot/build-NUCLEO_H563ZI/firmware.elf``, ``firmware.bin`` and ``firmware.hex`` (and the link map). ``BUILD=`` overrides the build directory ``build-$(BOARD)``.

* ``MCUBOOT_CLK=hsi`` keeps the reset clock instead of the board's PLL clock.
* ``MCUBOOT_PUBKEY=<file>`` names the key whose public key the bootloader accepts (default: the public test key, see :ref:`mcuboot_signing`).
* ``MCUBOOT_PRODUCTION=1`` refuses a public test key as ``MCUBOOT_PUBKEY`` (see :ref:`mcuboot_signing`).
* ``DEBUG=1`` builds with ``-Og``.

``make -C ports/stm32/mcuboot BOARD=NUCLEO_H563ZI PROBE=<serial number of the debug probe> deploy-bootloader`` programs ``firmware.hex`` with ``pyocd flash --erase sector``, which erases only the sectors the image occupies and leaves option bytes alone. ``PROBE`` is required, and the pyocd target comes from the board's ``PYOCD_TARGET``.

Application
~~~~~~~~~~~

.. code-block:: bash

    make -C ports/stm32 BOARD=NUCLEO_H563ZI MCUBOOT=1

The build directory is ``build-<BOARD>-mcuboot``, or ``build-<BOARD>-<BOARD_VARIANT>-mcuboot`` with a board variant, so it does not share objects with the default firmware. ``BUILD=`` overrides it. The ``MCUBOOT=1`` build links the firmware at the primary slot address behind the header, adds the :mod:`mcuboot` module and the ECC and handoff support, and produces ``firmware.bin`` (unsigned, so the bootloader will not boot it), ``firmware.hex`` and, when a signing key is set (see below), ``firmware.signed.bin``.

.. list-table::
   :header-rows: 1
   :widths: 30 70

   * - Target (``make -C ports/stm32 BOARD=... MCUBOOT=1 <target>``)
     - Result
   * - ``sign``
     - ``firmware.signed.bin``: the update image, for DFU, fsload and :class:`mcuboot.Writer`. ``all`` builds it too when a signing key is set.
   * - ``initial``
     - ``firmware.initial.bin`` and ``firmware.initial.hex``: the first image of a device. The binary covers the whole primary slot including the swap state, with the image marked confirmed.
   * - ``dfu``
     - ``firmware.signed.dfu``: the signed image wrapped as a DfuSe file for ``tools/pydfu.py``.
   * - ``sign-digest``
     - ``firmware.digest``: the SHA-256 that a hardware security module signs (see :ref:`mcuboot_signing`).
   * - ``sign-apply SIG=<file>``
     - ``firmware.signed.bin`` made from a signature produced elsewhere for that digest. ``MCUBOOT_SIGN_PUBKEY=<file>`` names another public key than ``MCUBOOT_PUBKEY``.
   * - ``deploy-initial PROBE=<serial>``
     - Programs ``firmware.initial.bin`` at the primary slot address with ``pyocd flash --erase sector``. The hex file is not used for this, because pyocd leaves its detached swap state segment erased.

``MCUBOOT_SIGN_KEY=<file>`` selects the private key used for signing. The default is the public test key ``tests/mcuboot/keys/test-ecdsa-p256.pem`` (see :ref:`mcuboot_signing`), and ``MCUBOOT_PRODUCTION=1`` removes that default.

To install a board for the first time, run both builds:

.. code-block:: bash

    make -C ports/stm32/mcuboot BOARD=NUCLEO_H563ZI PROBE=<serial> deploy-bootloader
    make -C ports/stm32 BOARD=NUCLEO_H563ZI MCUBOOT=1 PROBE=<serial> deploy-initial

Always name the probe by its serial number to select the intended probe rather than another connected probe.

The application filesystem of an ``MCUBOOT=1`` build is the ``fs`` area of the layout. That is not where the board's default firmware keeps its filesystem, and nothing copies an existing filesystem across.

.. _mcuboot_signing:

Signing, keys, layout id and production mode
--------------------------------------------

Images are signed with ECDSA over NIST P-256 and SHA-256, and tinycrypt verifies them in the bootloader. ``tools/mcuboot_sign.py`` wraps MCUboot's ``imgtool`` (imported from ``lib/mcuboot/scripts``, never a system installed copy) and passes it the arguments the generator derives from the board configuration. Its subcommands:

* ``sign``: make the update image. It adds the header, SHA-256, key hash, signature, security counter and layout id, and checks that the image fits. ``--external digest`` writes the digest for a hardware security module to sign, and ``--external apply --sig <file>`` inserts the signature that comes back. ``--version`` and ``--security-counter`` override the layout values.
* ``initial``: make the first image of a device, padded to the slot, with the swap state written and the image marked confirmed. ``--hex`` also keeps the Intel HEX file.
* ``dfu``: wrap a signed image as a DfuSe file for the secondary slot, using ``tools/dfu.py``.
* ``check-key``: report whether a key is one of the public test keys; ``--production`` makes that an error.

All of them except ``check-key`` need ``--layout <build>/mcuboot_gen/mcuboot_layout.json``. The tool refuses a signing key whose public key is not the layout's bootloader key, because the bootloader would reject the image. After writing an image it checks it (hash and signature against the key with imgtool, header size, size limit and layout id) and removes the output if a check fails.

The make targets normally run it. A direct call:

.. code-block:: bash

    python3 tools/mcuboot_sign.py sign --layout ports/stm32/build-NUCLEO_H563ZI-mcuboot/mcuboot_gen/mcuboot_layout.json -k my-key.pem firmware.bin firmware.signed.bin

Keys
~~~~

``tests/mcuboot/keys/test-ecdsa-p256.pem`` and ``other-ecdsa-p256.pem`` are private keys committed to the repository, so they are public. They are the default ``MCUBOOT_PUBKEY`` and ``MCUBOOT_SIGN_KEY`` of the Makefiles and exist for tests only. The build prints ``WARNING: signing with a public test key`` when it uses one. A bootloader holding the public half of a test key accepts images signed by anyone.

Make a developer key with MCUboot's tool, for example ``python3 lib/mcuboot/scripts/imgtool.py keygen -k dev.pem -t ecdsa-p256``. Build the bootloader with ``MCUBOOT_PUBKEY=dev.pem`` (public material is enough) and the application with ``MCUBOOT_SIGN_KEY=dev.pem``. A board file never contains a private key.

The bootloader holds one public key, ``MCUBOOT_PUBKEY``. The image carries a hash of the key it was signed with, and an image signed with another key is rejected. Changing the key means programming a new bootloader (see :ref:`mcuboot_warnings`).

Layout id
~~~~~~~~~

Every image carries the layout id of the flash layout it was built for, in a protected TLV with tag ``0x00A1``. The bootloader rejects an update image whose id is missing or differs from its own (result code ``ERR_LAYOUT``). :class:`mcuboot.Writer` and :func:`mcuboot.request_upgrade` refuse to work when the id the firmware was built with differs from the id in the bootloader's information block. This stops an image built for a different flash layout, with its vector table or filesystem in the wrong place, from being swapped in.

Rollback protection
~~~~~~~~~~~~~~~~~~~

``MCUBOOT_ROLLBACK_COUNTER`` selects what prevents an older image from being installed:

``0`` (version check)
    An update image with a version older than the image in the primary slot is refused. The comparison covers major, minor and revision, not the build number. It protects the swap path only: an older, correctly signed image written straight into the primary slot still boots.

``1`` (flash counter)
    Every image carries a security counter (``MCUBOOT_SECURITY_COUNTER``) that is independent of its version. The bootloader refuses an image with a counter below the stored value, including one already in the primary slot, and raises the stored value only for an image that has been confirmed or installed permanently (with the single policy it raises it to the counter of the image it starts, since that slot has no test state). The counter is kept as records in the two erase unit ``seccnt`` area. Anything that can erase that area can reset the counter, so this mode protects against software downgrades, not against an attacker who can write flash.


The single policy needs the counter, because the old image's version is gone once the only slot has been overwritten.

The NUCLEO_H563ZI configuration uses hardening level 1; PYBD_SF6 uses the default level 0. Both configurations use a security counter for rollback protection.

Production mode
~~~~~~~~~~~~~~~

Production mode is selected with ``MCUBOOT_PRODUCTION=1`` in the environment or on the make command line, or ``--production`` when the tools are called directly. In this mode:

* ``mcuboot_common.mk`` (through ``mcuboot_sign.py check-key --production``) and ``mcuboot_sign.py`` refuse any key whose public key equals one of the public test keys (the ``*.pem`` files in ``tests/mcuboot/keys`` and in the top directory of ``lib/mcuboot``). The default ``MCUBOOT_PUBKEY`` is a test key, so ``MCUBOOT_PUBKEY`` has to be given.
* The Makefiles set no default ``MCUBOOT_SIGN_KEY``, so ``all`` makes no signed image unless a key is given, and ``sign`` stops with an error when none is set.

The private key does not have to be on the build host. ``make sign-digest`` writes the image digest, a hardware security module signs it, and ``make sign-apply SIG=<file>`` produces the signed image (the signature file is the base64 DER form imgtool writes with ``--sig-out``).

Production mode only changes signing-key selection. It does not change the bootloader's flash protections or the application's access to flash; see :ref:`mcuboot_warnings`.

.. _mcuboot_updates:

Updating firmware
-----------------

There are four ways to put a new image on a device, and all of them end in the same boot decision:

* the bootloader's USB DFU mode (``dfu-util`` or ``tools/pydfu.py``),
* a filesystem load, where the bootloader reads the signed image from a file on the device (:func:`mcuboot.request_fsload`),
* the running firmware writing the update slot itself (:class:`mcuboot.Writer` or :func:`mcuboot.request_upgrade`),
* a programmer (SWD), for the first installation and for updating the bootloader.

Only the first three need no programmer. None of them can write the boot area.

Test swap, confirm and revert
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

With the swap policy, DFU and fsload always install the new image as a *test* image. So do :class:`mcuboot.Writer` and :func:`mcuboot.request_upgrade`, unless ``permanent=True`` is given. At the next reset the bootloader validates the update image, swaps the slots and starts it. The new firmware then has to confirm itself:

.. code-block:: python

    import mcuboot

    if not mcuboot.state()["confirmed"]:
        # run the application's checks here
        mcuboot.confirm()

On NUCLEO_H563ZI, if reset comes before :func:`mcuboot.confirm` is called (because of a crash, watchdog or power cycle), the bootloader swaps back to the old image and starts it. The revert is recorded in the update log. PYBD_SF6 uses the single-slot policy, so it has no test image and the installed image is final.

A *permanent* swap installs the image without a revert. One also happens unasked on a device whose primary slot has no image header: the bootloader copies a valid update image into the primary slot instead of swapping, there is nothing to revert to, and the image counts as confirmed. This is how a device with no application is brought up through DFU.

An update image that fails validation at the next boot (a bad hash or signature, a rollback check, a different layout id) is not installed. The current image keeps running, the update is removed and the update log gets a ``SLOT_REJECTED_AT_BOOT`` record.

DFU
~~~

The bootloader's DFU mode is USB DFU 1.1 (not the DfuSe variant). It is entered:

* by the application: :func:`mcuboot.request_dfu` or :func:`machine.bootloader` without an argument;
* by the board's forced entry input (on the NUCLEO_H563ZI, hold the USER button while resetting the board; on the PYBD_SF6, the USR button);
* by the bootloader itself, when there is no bootable image or after a fault (see :ref:`mcuboot_recovery`).

The device enumerates with the board's ``MCUBOOT_DFU_VID`` and ``MCUBOOT_DFU_PID`` (``f055:dfa5`` on the NUCLEO_H563ZI), the product string ``<board> MCUboot`` and the chip's unique id as the serial number. It has two alternate settings:

.. list-table::
   :header-rows: 1
   :widths: 10 40 50

   * - Alt
     - String (NUCLEO_H563ZI)
     - Use
   * - 0
     - ``@Secondary slot /0x08102000/79*008Kg``
     - The update image area, readable, erasable and writable. With the single policy it is called ``Application`` and covers the whole primary slot (``@Application /0x08040000/5*256Kg`` on the PYBD_SF6).
   * - 1
     - ``@Update log /0x080B0000/02*008Ka``
     - The update log, read only (only when the board has a log area).

The device builds the string at run time, so the case of the hex digits and the zero padding of the sector count can differ from the generated one. The last letter is the dfu-util permission character: ``g`` is read, erase and write, ``a`` is read only.

A download to alternate setting 0 maps DFU block *n* (2048 bytes each) to the address ``base + n * 2048``. The bootloader erases each sector before its first write. Blocks outside the alternate setting, including the swap state at the end of the slot, are refused with the DFU error ``errADDRESS``, so nothing outside the update image area can be written or erased through DFU. Erase is a vendor request (``0x80``), and a mass erase through it only erases the selected alternate setting.

Download with ``dfu-util``, using the signed binary:

.. code-block:: bash

    dfu-util -l
    dfu-util -d f055:dfa5 -a 0 -D ports/stm32/build-NUCLEO_H563ZI-mcuboot/firmware.signed.bin

or with ``tools/pydfu.py`` (needs ``pyusb``), using the DfuSe file of the ``dfu`` target:

.. code-block:: bash

    python3 tools/pydfu.py -l
    python3 tools/pydfu.py --vid 0xf055 --pid 0xdfa5 -u ports/stm32/build-NUCLEO_H563ZI-mcuboot/firmware.signed.dfu

Despite its help text, the ``-u`` option of ``pydfu.py`` writes the file to the device. ``--dfuse`` selects the legacy DfuSe protocol of ``ports/stm32/mboot`` instead. Add ``-S <serial>`` to ``dfu-util`` (or ``--vid`` and ``--pid`` to ``pydfu.py``) when more than one DFU device is attached.

When the last block has been sent the host ends the transfer, and the bootloader validates the image: header, sizes against the slot, layout id, hash, signature and rollback rule. On NUCLEO_H563ZI the image is marked as a test image before the next boot decision swaps it in. On PYBD_SF6 the image is already in the only slot. ``dfu-util`` can report an error exit status after ``Done!`` because the device resets itself off the bus.

A bad image is rejected and the DFU status is an error: ``errFILE`` for a hash, signature, rollback, layout, target flag or minimum size failure, ``errADDRESS`` for a header or size failure, ``errWRITE`` for a flash error. The first sector of a rejected image is erased so it cannot linger. With the single policy a rejected image has already overwritten the slot, so the bootloader removes its header and the next boot enters DFU mode. After a rejection the host clears the error and can send another image. A 535 KB image took about 30 seconds to download with ``dfu-util`` on the NUCLEO_H563ZI.

The vendor request ``0x81`` (device to host, 16 bytes) returns the result of the last operation of the session, which gives the reason for a rejection. Decode it with ``python3 tools/mcuboot_log.py --result <32 hex digits>``. The update log also records every accepted and rejected image.

A DFU session started by the application or the button ends by itself after ``MCUBOOT_DFU_TIMEOUT_S`` seconds (120 by default) without DFU activity, and the device resets into a normal boot. A session that is writing is not interrupted. A pending test swap or revert is completed before the DFU session starts.

.. _mcuboot_fsload:

Filesystem load (fsload)
~~~~~~~~~~~~~~~~~~~~~~~~

fsload installs a signed image from a file stored on the device, for example one downloaded into the application filesystem. The application calls :func:`mcuboot.request_fsload` (which does not return), the bootloader reads the file, and the normal swap follows.

.. code-block:: python

    import os, mcuboot

    # the file was written to the application filesystem
    os.sync()
    mcuboot.request_fsload("/update.bin")

The file is the signed image, ``firmware.signed.bin`` as made by the ``sign`` target, not a DfuSe file. With ``MCUBOOT_FSLOAD_GZIP`` the whole file may be gzip compressed, and the bootloader recognises the gzip header itself. The readers are the ones the board enables with ``MCUBOOT_FSLOAD_FAT``, ``MCUBOOT_FSLOAD_LFS2`` and ``MCUBOOT_FSLOAD_RAW``: FAT (with long file names), littlefs 2, and a window of flash that holds the file directly, with an optional second window that continues it. The NUCLEO_H563ZI bootloader has only the FAT reader. Without a *mount* argument the request names FAT, or littlefs 2 on a board that only enables the littlefs 2 reader.

The bootloader runs the load in two passes. Pass 1 opens the file and validates the image completely (header, whole-file read through for gzip, layout id, hash, signature and rollback rule) without writing any flash except the update log and, with the single policy, erasing a leftover intent record. Pass 2 clears earlier update state and copies the validated bytes into the update slot. On NUCLEO_H563ZI it marks the image as a test image; on PYBD_SF6 it writes the only slot in place. The bootloader then resets and makes the normal boot decision:

* NUCLEO_H563ZI: the primary slot is unchanged and nothing is pending.
* PYBD_SF6: the request is stored in the intent area before the slot is written. If a reset leaves no valid image, the bootloader retries from the intent record while the file is available. Without the file, it enters DFU mode.

The request is untrusted until the image has been validated, so the parsers for it and the filesystems bound reads to the window, check size arithmetic and use no heap. gzip is decoded by ``lib/uzlib``, which this support does not change. The decompressed size is limited to the update slot, and the decompressed image goes through the same validation. In practice that means:

* The source window (the filesystem area, or the window of a *mount* argument, also a raw window) must not overlap any flash area of MCUboot other than the ``fs`` area. A request that does is refused (``ERR_REQUEST``).
* A path has 1 to 254 printable ASCII characters and no ``.`` or ``..`` component (``ERR_FS_PATH``). For a raw mount the path is ignored but has to be present.
* A filesystem type whose reader is not in the bootloader is refused (``ERR_FS_MOUNT``), and so is littlefs 1, which is not offered. An unknown filesystem type is refused as a malformed request (``ERR_REQUEST``).
* A file larger than the update slot is refused (``ERR_TOO_BIG``).

Every run writes ``FSLOAD_BEGIN`` and then ``FSLOAD_DONE`` or ``FSLOAD_FAILED`` (with the result code) to the update log. When the load fails and the primary slot still holds a valid image, the bootloader resets and the old image runs; otherwise it stays in DFU mode. A bootloader built without any filesystem reader (none of ``MCUBOOT_FSLOAD_FAT``, ``MCUBOOT_FSLOAD_LFS2`` and ``MCUBOOT_FSLOAD_RAW`` defined) enters DFU mode for such a request.

Data written to the filesystem has to reach flash before the request, so call ``os.sync()`` first. :func:`machine.bootloader` flushes the storage itself.

From the running firmware
~~~~~~~~~~~~~~~~~~~~~~~~~

:class:`mcuboot.Writer` writes an image to the update slot from Python, for example while it downloads over a network, and marks it pending. Neither it nor :func:`mcuboot.request_upgrade` validates the image. The bootloader does that at the next reset and the update log shows the outcome. There is an example in the :mod:`mcuboot` documentation. :func:`mcuboot.state` and :func:`mcuboot.version` show what is installed and what is pending.

.. _mcuboot_handoff:

How the application reaches the bootloader
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

The application records a DFU or fsload request in reserved RAM, signals the bootloader through a retention register and resets. The bootloader checks the request before acting on it; any image installed through the request still passes signature and rollback validation.

.. _mcuboot_torn_write:

Torn write policy for ECC flash
-------------------------------

The NUCLEO_H563ZI uses 16-byte ECC flash words. A reset during programming can leave a word unreadable or corrected, while a supply loss can interrupt an erase. The STM32H5 bootloader and application keep shadow copies of swap state and repair them at startup. Image data is not shadowed, so a torn image fails hash validation and is not started.

With the swap policy, one interrupted flash operation during boot, swap, revert, confirmation, DFU or log append leaves the old or new image bootable, or sends the device to DFU. A second cut while repairing the same swap-state word can lose the revert or leave no usable image. Devices without ECC do not use the shadow-word policy and rely on each program unit being atomic. PYBD_SF6 uses one slot and enters DFU when the new image fails validation.

.. _mcuboot_update_log:

Update log
----------

The bootloader and the application write an append-only log in the ``log`` area. A record is 32 bytes: a magic number, a sequence number, the record type, a result code, the source, a flags byte (0 in every record), the image version, a ``detail`` word, the first four bytes of the image hash when known, and a CRC-32. The area is two erase units used as a ring, so a full unit is erased when the ring moves on and at least one unit of history is kept. A record torn by a power loss never reads as valid.

:func:`mcuboot.log` reads the log from the application. The bootloader's DFU alternate setting 1 gives a host the raw area, and ``tools/mcuboot_log.py`` decodes a dump of it:

.. code-block:: bash

    dfu-util -d f055:dfa5 -a 1 -U log.bin
    python3 tools/mcuboot_log.py log.bin --unit 0x2000

``--unit`` is the erase unit size and ``--json`` prints JSON. Records are listed in sequence order (newest last) with free, torn and gap information. The same tool decodes the 16 byte reply of vendor request ``0x81`` with ``--result``. A dump can also be taken over SWD.

Record types (the *type* field):

.. list-table::
   :header-rows: 1
   :widths: 10 35 55

   * - Value
     - Name
     - Written when
   * - 1
     - ``BOOT_OK``
     - Reserved; not written by the current code.
   * - 2
     - ``NO_IMAGE``
     - ``boot_go()`` found no bootable image.
   * - 3
     - ``DFU_BEGIN``
     - A DFU session started to write.
   * - 4
     - ``IMAGE_ACCEPTED``
     - A DFU download was validated and marked pending.
   * - 5
     - ``IMAGE_REJECTED``
     - A DFU download failed validation; *result* says why.
   * - 6
     - ``SWAP_DONE``
     - A test swap was performed; *detail* is the swap type before the boot.
   * - 7
     - ``SWAP_DONE_PERM``
     - A permanent swap or a copy into an empty primary slot was performed.
   * - 8
     - ``REVERTED``
     - An unconfirmed image was reverted.
   * - 9
     - ``SLOT_REJECTED_AT_BOOT``
     - A pending update was rejected at boot: result ``ERR_LAYOUT`` for a layout id mismatch, ``OK`` when bootutil rejected the image (hash, signature or rollback). The current image keeps running.
   * - 10
     - ``FSLOAD_BEGIN``
     - An fsload run started.
   * - 11
     - ``FSLOAD_DONE``
     - An fsload run installed an image.
   * - 12
     - ``FSLOAD_FAILED``
     - An fsload run failed; *result* says why.
   * - 13
     - ``APP_CONFIRMED``
     - The application confirmed its image (only when the state changed).
   * - 14
     - ``APP_UPGRADE_REQUESTED``
     - The application marked an update (:func:`mcuboot.request_upgrade`, :meth:`mcuboot.Writer.finish`).
   * - 15
     - ``APP_DFU_REQUESTED``
     - The application asked for DFU mode.
   * - 16
     - ``ASSERT``
     - An assertion failed; *detail* is the source line.
   * - 17
     - ``FSLOAD_RETRY``
     - The ``single`` policy restarted an interrupted fsload from the intent record.
   * - 18
     - ``SECCNT_FAILED``
     - The security counter area could not be initialised; see :ref:`mcuboot_recovery`.

Result codes (the *result* field, also reported by the vendor request ``0x81`` and used for the DFU status):

.. list-table::
   :header-rows: 1
   :widths: 10 30 60

   * - Value
     - Name
     - Meaning
   * - 0
     - ``OK``
     -
   * - 1
     - ``ERR_FLASH``
     - A flash read, erase or write failed; *detail* is an offset.
   * - 2
     - ``ERR_HEADER``
     - Bad magic, header size or TLV structure, or a truncated image.
   * - 3
     - ``ERR_HASH``
     - The hash is missing or does not match.
   * - 4
     - ``ERR_SIG``
     - No signature, an unknown key, or a signature mismatch.
   * - 5
     - ``ERR_DOWNGRADE``
     - Older version than the primary image, or a security counter below the stored one.
   * - 6
     - ``ERR_TOO_BIG``
     - The image does not fit.
   * - 7
     - ``ERR_NOT_TARGET``
     - A header flag of an unsupported feature (encrypted, compressed, RAM load, not bootable) or a wrong header size.
   * - 8
     - ``ERR_PENDING``
     - Marking the image pending failed.
   * - 9
     - ``ERR_LAYOUT``
     - The layout id is missing or different, or the flash map check failed.
   * - 10
     - ``ERR_NO_IMAGE``
     - There is no bootable image.
   * - 11
     - ``ERR_REQUEST``
     - The request or its element stream is malformed or names an unacceptable window.
   * - 12
     - ``ERR_TOO_SMALL``
     - An update of at most one erase unit with swap using offset (see :ref:`mcuboot_offset_limit`).
   * - 16 to 19
     - ``ERR_FS_MOUNT``, ``ERR_FS_OPEN``, ``ERR_FS_READ``, ``ERR_FS_GZIP``
     - Filesystem mount, open, read and gzip failures of fsload.
   * - 20
     - ``ERR_FS_PATH``
     - The path of an fsload request is not acceptable.

Sources (the *source* field): 0 bootloader, 1 DFU, 2 fsload, 3 application.

.. _mcuboot_recovery:

Recovery
--------

Recovery means the bootloader's DFU mode, which can install a new image. In the order of the boot decision, the bootloader enters it when:

* the flash map check, the startup scrub or the flash driver fails, an assertion fails (after the primary slot clean-up described next), or three consecutive resets came from a bootloader fault (the fault handlers log a line, count in ``TAMP->BKP29R`` and reset; the count is cleared when the bootloader hands over to an application). These end in DFU mode with no timeout. A fault inside the DFU front end resets the bootloader, which decides again. On these paths the bootloader never loops silently or halts, except for the fault injection hardening loop described in :ref:`mcuboot_torn_write`;
* ``boot_go()`` finds no bootable image. The primary slot is cleaned up: the swap state and first sector are erased so a header that is not an image does not block a later installation, unless a valid image is found there. The single policy has nothing to clean up and keeps its only slot as it is. With fsload, an intent record left by an interrupted fsload starts that load again before DFU mode is entered. DFU mode then follows with no timeout;
* an fsload failed and there is no bootable image (no timeout);
* the application asked for it, or the forced entry input is active. DFU mode ends after ``MCUBOOT_DFU_TIMEOUT_S`` seconds without activity.

A failing recovery path does not loop. DFU tables that fail the DFU glue's consistency check cause a log message, the error LED and a reset after three seconds.

**Damaged security counter area.** The flash backed counter starts at 0 only when its whole area is erased. An area that is not erased but holds no valid record counts as damaged: reading, updating and initialising the counter all fail, and the bootloader does not take that for a low counter. The failure is logged with a ``SECCNT_FAILED`` record. No image can pass the rollback check, so the bootloader ends in DFU mode and leaves the primary slot untouched, so repairing the counter area brings the application back. Erasing the area with a programmer repairs it; the counter then starts again at 0, which also gives up the anti-rollback state (see :ref:`mcuboot_warnings`).


Builds
~~~~~~

.. code-block:: bash

    make -C ports/stm32/mcuboot BOARD=NUCLEO_H563ZI
    make -C ports/stm32 BOARD=NUCLEO_H563ZI MCUBOOT=1
    make -C ports/stm32/mcuboot BOARD=PYBD_SF6
    make -C ports/stm32 BOARD=PYBD_SF6 MCUBOOT=1



.. _mcuboot_warnings:

Warnings: irreversible and destructive operations
--------------------------------------------------

.. warning::

   These operations can erase data or change protection settings in ways that are hard to reverse. Back up flash and make sure a programmer can recover the board before changing option bytes or installing the bootloader.

**Option bytes, read-out protection, TrustZone, write protection and one-time programmable memory (STM32H5).** The NUCLEO_H563ZI runs with TrustZone disabled (``TZEN=0``) on non-secure register aliases. Option bytes control TrustZone, bank swap, boot configuration, read-out protection, write protection and OTP areas. Some changes are permanent, and lowering read-out protection may erase flash. Check the STM32H5 reference manual and make sure a programmer can recover the board before changing option bytes. The deploy targets use ``pyocd flash --erase sector`` and leave option bytes unchanged.

**Production keys cannot be replaced from the device.** The public key hashes are part of the bootloader image. DFU, fsload and the :mod:`mcuboot` module never write the boot area, so changing keys takes a new bootloader programmed with a programmer. If the bootloader area has been write protected through option bytes (this support does not do that), the keys cannot be changed without removing the protection, which is one of the operations above. If the private key is lost, no new image can be signed for a bootloader holding only its public key, and the device can only be updated by reprogramming the bootloader. The bootloader holds one public key, so a backup key cannot be added to the same bootloader.

**Test keys are public.** The keys in ``tests/mcuboot/keys`` are committed to the repository. A bootloader built without ``MCUBOOT_PRODUCTION=1`` that holds one of them accepts images signed by anyone. Use production mode for any shipped firmware.

**The security counter can go up and be reset.** Once an image with a higher counter has been confirmed, images with a lower counter are refused, so confirming a test image whose ``MCUBOOT_SECURITY_COUNTER`` is too high cannot be undone through an update. The counter lives in ordinary flash: erasing its area resets it to 0, and anything that can write flash can do that.

**The application can erase the bootloader.** The update paths are confined to the update slot, but the application is not. It has access to the flash controller, so code that erases flash (including Python code that reaches the registers) can erase the bootloader, update log, counter area and shadow words. This support does not write protect the bootloader area. A device whose bootloader is gone can only be revived with a programmer, and a chip erase through the programmer also erases the bootloader.

**The default layout reuses addresses of the default firmware.** The NUCLEO_H563ZI layout puts the update slot at ``0x08100000``, where the default firmware has its filesystem, and the application filesystem at ``0x080BC000``. Moving a board from the default firmware to an MCUboot installation, or back, leaves data where the other layout means something different, and the first update overwrites the old filesystem. Back up the files first. On the PYBD_SF6 the filesystem is on SPI flash #1 in both layouts, but the MCUboot bootloader replaces mboot at ``0x08000000`` and the slot at ``0x08040000`` overwrites the default application at ``0x08008000``. Read the flash back before programming a board that holds firmware you need.

**Flash wear.** Each update writes the update slot; a swap and revert rewrite both slots. The update log and security counter erase areas when their records wrap. Check the STM32 data sheet before enabling frequent updates.


**Power cuts during updates.** A power cut can leave the board in DFU or require reprogramming. Use a board that can be recovered with a programmer.
