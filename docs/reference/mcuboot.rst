.. _mcuboot_bootloader:

MCUboot bootloader support
==========================

.. contents::
   :local:
   :depth: 2

This page covers how MicroPython firmware is built, signed, installed and updated when it runs under the `MCUboot <https://docs.mcuboot.com/>`_ bootloader. It is for people who build firmware, define boards or work on ports. The running firmware talks to the bootloader through the :mod:`mcuboot` module.

Supported hardware:

* The stm32 port on STM32H5, with the **NUCLEO_H563ZI** board definition, and on STM32F7, with the **PYBD_SF6** board definition. The bootloader Makefile and the ``MCUBOOT=1`` application build stop with an error for any other MCU series. Another STM32F7 board needs the ``MCUBOOT_*`` inputs in its ``mpconfigboard.h`` and stops in ``mcuboot_layout.h`` without them. The addresses, USB identifiers and flash layouts on this page are the NUCLEO_H563ZI ones, except where the text names PYBD_SF6. PYBD_SF6 has one slot, updated in place (the single policy), and a flash without ECC. It has its own section, :ref:`mcuboot_pybd_sf6`.
* The unix port, with the ``mcuboot`` and ``mcuboot_single`` variants. They keep the flash in a file and run the :mod:`mcuboot` module tests (see :ref:`mcuboot_tests`). There is no bootloader on the unix port.

Hardware testing so far is one NUCLEO_H563ZI in the configuration of its board definition: swap using offset, security counter in flash, fault injection hardening level 1 and the FAT reader. That covers boot, DFU download, test swap, confirm, revert, fsload from a FAT filesystem, recovery and interrupted flash operations. The other slot policies and swap modes, the littlefs 2, raw and gzip readers, the other hardening levels, Mbed TLS, the port-kept security counter, automatic confirmation and the SPI flash backend have only run in the host tests and the unix port (automatic confirmation only as a build and in the layout tests). No power has been cut on a real board. Nothing of the STM32F7 port has run on a board: the hardware phase is blocked (see ``planning/tickets/TICKET-019-stm32f7-hardware-bringup.md``), and PYBD_SF6 has only been built and run in the host tests.

.. warning::

   Some operations in this area are not reversible or destroy data, and the support has only run on an open (unprotected) development board. Read :ref:`mcuboot_warnings` before changing option bytes, read-out protection, TrustZone or write protection, using production keys, or programming a board that already holds data.

Architecture
------------

Layers
~~~~~~

.. list-table::
   :header-rows: 1
   :widths: 24 76

   * - Path
     - Role
   * - ``lib/mcuboot``
     - The MCUboot project as a git submodule, **unmodified**. The build uses ``boot/bootutil`` (image format, validation and the swap algorithms: offset, move and scratch, with scratch also serving as the overwrite-only copy), ``boot/zephyr/single_loader.c`` (the single slot loader), ``ext/tinycrypt`` and ``ext/mbedtls-asn1`` (ECDSA P-256 verification and ASN.1 parsing) and ``scripts/imgtool`` (signing). With ``MCUBOOT_CRYPTO_SEL_MBEDTLS`` the crypto sources come from the port instead of ``ext``. Initialise it with ``git submodule update --init lib/mcuboot``.
   * - ``shared/mcuboot``
     - Port independent glue between bootutil and a port: the configuration header ``mcuboot_layout.h``, the flash map tables and backend, the ECC shadow words, the security counter, the update log, the request handoff from the application, the boot decision, image validation, the DFU glue and its region tables, fsload with its readers (FAT, littlefs 2, raw window and gzip), the SPI flash backend, the application side API behind the :mod:`mcuboot` module, and the build fragments ``mcuboot_common.mk`` and ``mcuboot_rules.mk``. A port implements the interface in ``shared/mcuboot/include/mcuboot_port.h``.
   * - ``shared/tinyusb/mboot``
     - A generic TinyUSB DFU 1.1 device core: USB descriptors, the table of regions that becomes the DFU alternate settings, dispatch of download, upload and erase to flash, and the parser for request element streams. It knows nothing about MCUboot; ``shared/mcuboot/src/dfu_glue.c`` ties the two together.
   * - ``ports/stm32/mcuboot``
     - The STM32 bootloader (STM32H5 and STM32F7): ``Makefile``, ``main.c``, the linker scripts ``mcuboot.ld`` (bootloader flash and RAM) and ``mcuboot_sram.ld.S`` (SRAM start and end and the request region, preprocessed into ``mcuboot_sram.ld`` and shared with the application link), the flash description ``mcuboot_dev.h``, clock, reset and fault handling (``port_core.c``), the flash policy layer (``port_flash.c``) over the flash driver ``ports/stm32/flash.c``, the handoff registers and ECC event counter (``port_common.c``), USB device controller setup (``port_usb.c``) and the TinyUSB configuration. Pin names are the ones ``ports/stm32/mboot`` uses. The bootloader turns the instruction cache off around a sector erase, because ``flash_erase()`` reads the flash size from the system flash area and that faults with the cache on. ``mcuboot_app.mk`` and ``mcuboot_app_rules.mk`` in the same directory are the build fragments for the application.
   * - ``ports/stm32``
     - Edits guarded by ``MICROPY_HW_MCUBOOT_APP``: the ``MCUBOOT=1`` build, the linker scripts ``boards/stm32h573xi_mcuboot.ld`` and ``boards/PYBD_SF6/f767_mcuboot.ld``, flash ECC NMI handling (STM32H5), passing the reset flags on, routing of ``machine.bootloader()`` and automatic confirmation after ``boot.py`` (``MCUBOOT_CONFIRM_AUTO``). The application erases and programs flash through the same ``flash.c`` and ``mcuboot/port_flash.c`` as the bootloader. A default build contains none of it.
   * - ``extmod/modmcuboot.c``
     - The :mod:`mcuboot` module, enabled with ``MICROPY_PY_MCUBOOT``.
   * - ``tools``
     - ``mcuboot_gen.py`` (C preprocessor pass over the configuration header, writes the linker, make and signing inputs), ``mcuboot_sign.py`` (signing and packaging), ``mcuboot_log.py`` (update log decoder) and ``pydfu.py`` (host DFU tool).

The DFU front end needs the TinyUSB submodule (``lib/tinyusb``). The fsload readers use ``lib/oofatfs``, ``lib/littlefs`` and ``lib/uzlib``.

Bootloader role and application role
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

The same board configuration produces two builds.

The **bootloader** is one image holding MCUboot, the boot decision, the DFU front end and the filesystem loader. It sits at the start of flash (the ``boot`` area), reads flash through the flash map, validates images, swaps the slots (or copies the update over the primary slot, or boots the only slot, depending on the slot policy) and jumps to the application. On the NUCLEO_H563ZI with DFU and the FAT reader it is about 52 KB (52284 bytes of text) and the layout reserves 64 KiB (``MCUBOOT_BOOT_SIZE``). On the PYBD_SF6 it is 42848 bytes of text in 64 KiB. It is the only code that verifies signatures. DFU, fsload and :class:`mcuboot.Writer` only write image data to the update slot (the only slot with the single policy), and none of the update paths writes the boot area. The :mod:`mcuboot` module also sets the confirmed flag in the swap state of the primary slot and appends to the update log.

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

How an update is installed depends on the *slot policy* of the board (``MCUBOOT_POLICY``) and, for the ``swap`` policy, on its *swap mode* (``MCUBOOT_SWAP_MODE``):

``MCUBOOT_POLICY_SEL_SWAP`` (the default)
    Two slots. A new image is written to the secondary slot and the bootloader exchanges the contents of the two slots at the next reset. By default this is a *test* swap: the old image stays in the secondary slot and is swapped back at a later reset unless the new image has been confirmed. The swap mode picks MCUboot's algorithm:

    ``MCUBOOT_SWAP_MODE_SEL_OFFSET`` (the default, and the mode that has run on hardware)
        The secondary slot is one erase unit larger than the primary slot and the update image starts one erase unit into it. Needs no area besides the two slots, and a device whose primary slot has no image can be brought up by installing into the secondary slot (bootstrap). An update of one erase unit or less is refused, see :ref:`mcuboot_offset_limit`.

    ``MCUBOOT_SWAP_MODE_SEL_MOVE``
        The secondary slot is one erase unit smaller than the primary slot, and the swap moves the primary image up by one unit (the unit the primary slot has extra). The largest image is the primary slot size less the swap state sectors and less that one erase unit. An interrupted revert looks like an upgrade to an older image. With ``MCUBOOT_ROLLBACK_COUNTER = 0`` the version check refuses it and the new image stays, so a product using this mode should use the security counter. The layout does not enforce that.

    ``MCUBOOT_SWAP_MODE_SEL_SCRATCH``
        Two slots of the same size plus a scratch area (``MCUBOOT_SCRATCH_ADDR`` and ``MCUBOOT_SCRATCH_SIZE``, a multiple of the erase unit and at least one unit; a bigger area gives a bigger swap buffer). The swap uses the scratch area as a buffer and to record its progress. For devices where a slot cannot be one erase unit larger. Not available on a device with ECC, because the swap status in the scratch area is not covered by the shadow words.

``MCUBOOT_POLICY_SEL_OVERWRITE_EXTERNAL``
    Two slots of the same size; the secondary slot may be on device 1 (an external flash). The bootloader copies the new image over the primary slot at the next reset. The update is always permanent: no test swap, no revert, nothing to confirm. An interrupted copy is repeated at the next boot because the secondary slot still holds the image, but the old image is gone as soon as the copy starts. Use the security counter with this policy. In the core harness torn word sweep with a version check only, 2 of 1515 torn runs lost the image when a flash word in the image body of the primary slot was torn during the copy. With the security counter (the ``overwrite`` board) none of 1535 torn runs of the 3 sector images did.

``MCUBOOT_POLICY_SEL_SINGLE``
    One slot, for devices with flash for only one image. DFU and fsload write the new image over the primary slot in place. There is no old image to fall back to, so an update that is interrupted or fails validation leaves a slot the bootloader refuses to start (*fail closed*), and the bootloader enters DFU mode, where an install recovers the device. The version of the old image is gone once the slot is overwritten, so the policy needs ``MCUBOOT_ROLLBACK_COUNTER = 1``; the bootloader raises the stored counter to that of the image it starts. With fsload, an *intent area* of one erase unit holds the request while the slot is overwritten, so an interrupted fsload is repeated at the next boot (see :ref:`mcuboot_fsload`). The only slot holds no swap state, so the whole slot is the update region. The largest image is the slot size less ``MCUBOOT_TRAILER_SIZE`` (48 bytes on the PYBD_SF6). There is no test swap, so nothing can be confirmed or reverted. The :mod:`mcuboot` functions that need an update slot raise ``OSError(EPERM)``.

.. _mcuboot_offset_limit:

Limit of swap using offset
~~~~~~~~~~~~~~~~~~~~~~~~~~

Swap using offset only resumes an interrupted swap when the image, with its header and TLVs, is larger than one erase unit. For an image that fits in one erase unit, bootutil reads the header of the image under swap from the first unit of the primary slot (``boot_read_image_header()`` in ``swap_offset.c`` compares a 1 based status index with a 0 based unit index). A power loss during the erase of that unit leaves a header that is neither valid nor erased. The bootloader then does not resume the swap, finds no valid image, and the device ends up in DFU mode, where an install recovers it. It never runs an unvalidated image. MicroPython firmware images are far larger than the erase units of the supported devices. An update of at most one erase unit (``MCUBOOT_MIN_IMAGE_SIZE``, the erase unit of the slots plus one byte with swap using offset, 0 for the other modes and policies) is refused. DFU, fsload and the check of a pending image at boot refuse it with ``ERR_TOO_SMALL`` (the DFU status is ``errFILE``; at boot the pending update is erased and the update log gets a ``SLOT_REJECTED_AT_BOOT`` record with the code). :meth:`mcuboot.Writer.finish` raises ``OSError(EINVAL)``, erases the first erase unit of the image and closes the writer. ``tools/mcuboot_sign.py sign`` and ``initial`` refuse to make such an image. Swap using move is not affected. In the host sweeps with images of one sector, swap using scratch lost no image either.

Boot decision
~~~~~~~~~~~~~

At every reset the bootloader runs this sequence:

1. Initialise the clock and the log UART, and take the request the application may have left (see :ref:`mcuboot_handoff`). The request and the handoff register are cleared before anything acts on them, so a crash during recovery cannot repeat the request. After a power-on reset a leftover request is discarded.
2. Initialise the flash driver and check the flash map against it. A failure goes to recovery.
3. If the previous three boots all ended in a fault of the bootloader itself (the fault handlers count in a backup register), go to recovery.
4. Run the startup scrub of the flash policy, which repairs interrupted flash writes (see :ref:`mcuboot_torn_write`). A failure goes to recovery.
5. If the board keeps a security counter (``MCUBOOT_ROLLBACK_COUNTER = 1``), initialise it. A counter kept by the port needs no initialisation. A failure is logged and remembered (see :ref:`mcuboot_recovery`).
6. Request recovery mode if the application left a request or the board's forced entry input is active (the USER button held at reset on the NUCLEO_H563ZI).
7. Unless recovery was requested, run MCUboot's ``boot_go()``. If an update is pending, its layout id is checked first. ``boot_go()`` validates a pending update image, swaps or reverts as the swap state says, validates the image it is about to start and returns it. The bootloader then records the outcome in the update log and jumps to the image. A pending swap or revert is also completed when recovery was requested, before the bootloader stays in recovery, because a DFU session erases the update slot and that holds the image to revert to.
8. If ``boot_go()`` finds no bootable image, log an entry and clean up the primary slot (see :ref:`mcuboot_recovery`; the single policy leaves its only slot as it is). With the single policy and fsload, an intent record left by an interrupted fsload makes the bootloader run that request again (step 9). Otherwise the bootloader goes to DFU mode with no timeout.
9. If the request was a filesystem load, run it (see :ref:`mcuboot_fsload`). A successful load, or a failed one while the old image is still bootable, resets the device so step 7 runs from a clean state. A failed load with no bootable image ends in DFU mode.
10. Otherwise enter DFU mode. A board without DFU shows an error pattern on the LED and waits for a reset.

What is validated on every boot
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Every boot validates the primary slot image before starting it: the header, the SHA-256 hash of the whole image, the ECDSA signature against the public key compiled into the bootloader and, when the board keeps a security counter, that the image counter is not below the stored one. Boot time grows with image size, and a boot that swaps validates twice (the update image, then the swapped-in image). Validation of the primary slot can be switched off by defining ``MCUBOOT_VALIDATE_PRIMARY`` as 0. Production builds and the single policy refuse that setting, because the bootloader would start whatever the primary slot holds and an application that can write the slot would bypass the signature.

The layout id is checked when an update is received (DFU, fsload) and, at boot, for a pending update image that got into the secondary slot another way (:class:`mcuboot.Writer`, :func:`mcuboot.request_upgrade` or a programmer). An image with a missing or different layout id is rejected and removed, not swapped in.

.. _mcuboot_config:

Configuration
-------------

The flash layout and the build choices are C macros, so the C code, linker scripts, Makefiles and signing tools all read the same values and no address is written down twice. Three files are involved:

* The board's ``mpconfigboard.h`` holds the ``MCUBOOT_*`` inputs: where the slots are, plus the choices listed below. A board without them has no MCUboot variant. The end of ``ports/stm32/boards/NUCLEO_H563ZI/mpconfigboard.h`` is the example:

  .. code-block:: c

      #define MCUBOOT_PRIMARY_SIZE                (640 * 1024)
      #define MCUBOOT_SECONDARY_ADDR              (0x08100000)
      #define MCUBOOT_ROLLBACK_COUNTER            (1)
      #define MCUBOOT_FIH_LEVEL                   (1)
      #define MCUBOOT_FSLOAD_FAT                  (1)

* The port's ``mcuboot_dev.h`` (``ports/stm32/mcuboot/mcuboot_dev.h``) describes the flash devices and the linker symbols of the port.
* ``shared/mcuboot/include/mcuboot_layout.h`` includes both, derives every other area and macro, and refuses an invalid configuration with ``#error``. The comment at the top of that header lists the inputs and their defaults.

The host tests use the same header pair for other configurations. ``BOARD=h5`` is the real board, and the others are ``mpconfigboard.h`` and ``mcuboot_dev.h`` pairs in ``tests/mcuboot/host/boards``. The unix variants have their own pairs in ``ports/unix/variants/mcuboot`` and ``ports/unix/variants/mcuboot_single`` (see :ref:`mcuboot_tests`).

Board inputs
~~~~~~~~~~~~

Sizes are in bytes. Addresses are CPU addresses, or DFU addresses for a device that is not memory mapped.

.. list-table::
   :header-rows: 1
   :widths: 28 22 50

   * - Macro
     - Default
     - Meaning
   * - ``MCUBOOT_PRIMARY_SIZE``
     - required
     - Size of the primary slot, the slot the CPU boots from. A multiple of the erase unit of the run of equal sectors the slot lies in.
   * - ``MCUBOOT_SECONDARY_ADDR``
     - required, not defined with the single policy
     - Address of the update slot. Its size follows from the policy and swap mode: one erase unit larger than the primary slot for swap using offset, one smaller for swap using move, and the same size as the primary slot otherwise. On the NUCLEO_H563ZI it is the start of bank 2.
   * - ``MCUBOOT_ROLLBACK_COUNTER``
     - required
     - ``1`` keeps a monotonic security counter, in flash or (with ``MCUBOOT_SECCNT_PORT``) in the port. Products should use it. ``0`` only refuses an older version, or nothing at all with ``MCUBOOT_VERSION_CHECK = 0``. The single policy needs ``1``. See :ref:`mcuboot_signing`.
   * - ``MCUBOOT_POLICY``
     - ``MCUBOOT_POLICY_SEL_SWAP``
     - The slot policy: ``MCUBOOT_POLICY_SEL_SWAP``, ``MCUBOOT_POLICY_SEL_OVERWRITE_EXTERNAL`` or ``MCUBOOT_POLICY_SEL_SINGLE`` (see above).
   * - ``MCUBOOT_SWAP_MODE``
     - ``MCUBOOT_SWAP_MODE_SEL_OFFSET``
     - With the swap policy: ``MCUBOOT_SWAP_MODE_SEL_OFFSET``, ``MCUBOOT_SWAP_MODE_SEL_MOVE`` or ``MCUBOOT_SWAP_MODE_SEL_SCRATCH``. An error with the other policies.
   * - ``MCUBOOT_SCRATCH_ADDR``, ``MCUBOOT_SCRATCH_SIZE``
     - behind the update log, counter and shadow areas; one erase unit
     - Placement of the scratch area of swap using scratch, in device 0, a multiple of the erase unit of the slots and at least one. An error with the other modes.
   * - ``MCUBOOT_INTENT_ADDR``
     - behind the scratch area
     - Placement of the intent area (one erase unit of its run, in device 0) of the single policy with fsload. An error in other configurations.
   * - ``MCUBOOT_LOG_ADDR``
     - directly behind the primary slot
     - Start of the update log, two erase units of the run at that address.
   * - ``MCUBOOT_SECCNT_ADDR``
     - directly behind the update log
     - Start of the flash security counter area, two erase units of the run at that address. An error without the counter in flash.
   * - ``MCUBOOT_SHADOW_ADDR``
     - directly behind the counter area
     - Start of the shadow area. An error on a device 0 without ECC.
   * - ``MCUBOOT_VERSION_CHECK``
     - ``1``
     - With ``MCUBOOT_ROLLBACK_COUNTER = 0``, ``0`` also drops the refusal of an older version, so there is no rollback protection at all. An error with the counter.
   * - ``MCUBOOT_HASH_DIRECT``
     - ``0``
     - ``1`` lets bootutil hash an image in place instead of through a read buffer (``MCUBOOT_HASH_STORAGE_DIRECTLY``). Needs memory mapped slot devices. Refused with swap using offset (the hash would miss the erase unit in front of the image in the secondary slot) and with fsload (the stream area has no memory to read).
   * - ``MCUBOOT_VALIDATE_PRIMARY``
     - ``1``
     - ``0`` starts whatever the primary slot holds without checking its signature, so an application that can write the primary slot bypasses secure boot. An error with the single policy and with ``MCUBOOT_PRODUCTION=1``. Otherwise a ``#warning``, which stops the stm32 builds because they compile with ``-Werror``.
   * - ``MCUBOOT_PRIMARY_ADDR``
     - directly behind the bootloader region
     - Start of the primary slot, in device 0 and aligned to ``MCUBOOT_VTOR_ALIGN``.
   * - ``MCUBOOT_BOOT_SIZE``
     - the value of the port
     - The bootloader region at the start of device 0. It lies in one run of equal sectors and is a number of its units. The port sets it (64 KiB on the STM32H5 and the STM32F7) unless the board defines it first.
   * - ``MCUBOOT_HEADER_SIZE``
     - ``0x400``
     - Size of the image header. At least 0x20, a multiple of ``MCUBOOT_VTOR_ALIGN`` and of the trailer alignment.
   * - ``MCUBOOT_TLV_RESERVE``
     - ``0x400``
     - Space kept behind the image for the TLV records when the application link region is computed.
   * - ``MCUBOOT_SECURITY_COUNTER``
     - ``0``
     - Security counter put into the signed image.
   * - ``MCUBOOT_FIH_LEVEL``
     - ``0``
     - MCUboot's fault injection hardening profile: 0 off, 1 low, 2 medium, 3 high. The high profile adds random delays and needs the port to provide ``mcuboot_port_entropy_u8()`` (``shared/mcuboot/src/fih_delay_rng.c`` feeds it to bootutil). The stm32 port has no such function, so a level 3 bootloader build fails to link with an undefined ``mcuboot_port_entropy_u8``. The NUCLEO_H563ZI uses 1.
   * - ``MCUBOOT_CRYPTO``
     - ``MCUBOOT_CRYPTO_SEL_TINYCRYPT``
     - The library that verifies the signature and hashes the image: ``MCUBOOT_CRYPTO_SEL_TINYCRYPT`` or ``MCUBOOT_CRYPTO_SEL_MBEDTLS``. Both do ECDSA P-256 with SHA-256. With Mbed TLS the port supplies the library sources in ``MCUBOOT_MBEDTLS_SRC_C`` (relative to the repository root) and its own ``MBEDTLS_CONFIG_FILE`` and include paths (ECDSA over secp256r1, SHA-256 and the ASN.1 parser); the build does not add the ASN.1 parser configuration of the tinycrypt build. The stm32 Makefile sets no ``MCUBOOT_MBEDTLS_SRC_C``, so ``mcuboot_common.mk`` stops with an error for that choice on stm32. The host test board ``mbedtls`` builds it from ``lib/mbedtls``.
   * - ``MCUBOOT_SECCNT_PORT``
     - not defined
     - Define as ``1`` with ``MCUBOOT_ROLLBACK_COUNTER = 1`` to keep the security counter in the port (OTP, fuses, a battery backed register) instead of in flash. The port provides ``mcuboot_port_seccnt_read()``, ``_write()``, ``_can_update()`` and ``_lock()``, and the layout has no counter area. ``_can_update()`` refuses a counter that can run out of increments, and the bootloader calls ``_lock()`` after an update. The stm32 port does not implement these, so a bootloader build with it fails to link with undefined ``mcuboot_port_seccnt_*`` references.
   * - ``MCUBOOT_CONFIRM_AUTO``
     - not defined
     - Define as ``1`` to confirm a test image automatically: the stm32 port calls ``mcuboot_app_confirm_if_pending()`` after ``boot.py`` finishes. Without it the application must call :func:`mcuboot.confirm`.
   * - ``MCUBOOT_DFU``
     - ``1``
     - ``0`` leaves out the DFU front end. A device with ECC needs it, because recovery has to stay reachable.
   * - ``MCUBOOT_DFU_VID``, ``MCUBOOT_DFU_PID``
     - ``MICROPY_HW_USB_VID`` or ``0xF055``; ``0xDFA5``
     - USB identifiers of the bootloader's DFU device. The NUCLEO_H563ZI uses ``f055:dfa5``.
   * - ``MCUBOOT_DFU_PRODUCT``
     - the board name (``MICROPY_HW_BOARD_NAME``) and the text MCUboot; ``MicroPython MCUboot`` without a board name
     - USB product string.
   * - ``MCUBOOT_DFU_TIMEOUT_S``
     - ``120``
     - Inactivity timeout of a DFU session asked for by the application or a button. ``0`` disables it. A DFU session entered because there is no bootable image never times out.
   * - ``MCUBOOT_FSLOAD_FAT``, ``MCUBOOT_FSLOAD_LFS2``, ``MCUBOOT_FSLOAD_RAW``
     - not defined
     - Define as ``1`` to include the FAT, littlefs 2 or raw window reader. Any one of them enables fsload. The NUCLEO_H563ZI defines ``MCUBOOT_FSLOAD_FAT``.
   * - ``MCUBOOT_FSLOAD_GZIP``
     - not defined
     - Define as ``1`` to accept a gzip compressed update file, on top of any reader (it needs one). Adds ``shared/mcuboot/src/gzstream.c`` and the inflate part of ``lib/uzlib``. The reader keeps a 32 KiB inflate window in bootloader RAM.
   * - ``MCUBOOT_FS_ADDR``, ``MCUBOOT_FS_SIZE``
     - derived
     - The application filesystem, in device 0 or device 1. Give both or neither. Used by the application linker script and as the default source of :func:`mcuboot.request_fsload`. The bootloader never writes it, and an fsload window may cover it. The default is what remains behind the other areas. It has to lie in one run of equal sectors and have room, so a device with more than one run needs both values.
   * - ``MCUBOOT_LOG_LEVEL``
     - ``3``
     - Log text of the bootloader: 0 off, 1 error, 2 warning, 3 info, 4 debug. On the STM32 ports it goes to the REPL UART (USART3). PYBD_SF6 has none and uses level 0.
   * - ``MCUBOOT_TMPBUF_SZ``
     - ``4096``
     - Size of the chunk the image is read in for hashing.

The remaining choices are fixed: ECDSA P-256 with SHA-256, one image, one verifying key. The signed image's version is the MicroPython version (``MICROPY_VERSION_MAJOR.MINOR.MICRO+0`` from ``py/mpconfig.h``), which ``tools/mcuboot_sign.py sign --version`` can override. The build number is not compared.

The keys and production mode are Make variables:

.. list-table::
   :header-rows: 1
   :widths: 28 22 50

   * - Variable
     - Default
     - Meaning
   * - ``MCUBOOT_PUBKEY``
     - ``tests/mcuboot/keys/test-ecdsa-p256.pem``
     - The one key the bootloader verifies images against. A private or public PEM file works (only the public key is used). Unencrypted ECDSA P-256 keys only.
   * - ``MCUBOOT_SIGN_KEY``
     - the test key
     - The private key the application build signs with. Not set in production mode.
   * - ``MCUBOOT_PRODUCTION``
     - ``0``
     - ``1`` refuses the public test keys, see :ref:`mcuboot_signing`.
   * - ``MCUBOOT_TEST_FI``
     - ``0``
     - ``1`` adds the fault injection hook of the hardware test campaigns to the bootloader.

Port description
~~~~~~~~~~~~~~~~

``mcuboot_dev.h`` of a port describes device 0 with ``MCUBOOT_DEV0_*`` and, when there is a second flash, device 1 with ``MCUBOOT_DEV1_*``. The values are integer constant expressions so the generator's preprocessor pass can read them. ``ports/stm32/mcuboot/mcuboot_dev.h`` does not hard-code the STM32H5 flash numbers. It reads them from macros the port already uses (``FLASH_BASE_NS``, ``FLASH_SIZE_DEFAULT`` and ``FLASH_SECTOR_SIZE`` of the CMSIS device header, ``FLASH_WORD_BYTES`` and ``FLASH_WORD_HAS_ECC`` of ``ports/stm32/flash.h``; for the STM32F7 also the runs of equal sectors ``FLASH_LAYOUT_RUN0_*`` to ``FLASH_LAYOUT_RUN2_*`` of that header, which ``ports/stm32/flash.c`` builds its sector table from). The generator and the host tests therefore need the board's CMSIS include paths and device macro (for example ``-DSTM32H573xx``). The stm32 bootloader and application Makefiles pass them as ``MCUBOOT_CPP_FLAGS``, and the host tests add a stub for the Arm specific parts of the CMSIS core header (see :ref:`mcuboot_tests`).

.. list-table::
   :header-rows: 1
   :widths: 30 70

   * - Macro
     - Meaning
   * - ``MCUBOOT_DEVn_NAME``
     - Short name of the device, used in logs. The flash map table reads it.
   * - ``MCUBOOT_DEVn_BASE``
     - CPU address (and DFU address) of device offset 0. For a device that is not memory mapped, a made-up base chosen by the port. Device 1 has to be above device 0.
   * - ``MCUBOOT_DEVn_SIZE``
     - Bytes of the device that MCUboot areas may use. A SPI flash whose size is detected at run time gets a constant that every chip the board can carry has.
   * - ``MCUBOOT_DEVn_ERASE``
     - Erase unit in bytes, for a device with one erase size.
   * - ``MCUBOOT_DEVn_RUNS``, ``MCUBOOT_DEVn_RUNj_SIZE``, ``MCUBOOT_DEVn_RUNj_ERASE``
     - For a device whose sectors differ in size: 1 to 4 runs of equal sectors from the start of the device, ``j`` counting from 0. Each run has its size in bytes and its erase unit, and starts at a multiple of that unit. The runs add up to ``MCUBOOT_DEVn_SIZE``. Giving ``MCUBOOT_DEVn_ERASE`` as well is an error.
   * - ``MCUBOOT_DEVn_WRITE``
     - Program unit and required write alignment in bytes, at most 32.
   * - ``MCUBOOT_DEVn_ERASED_VAL``
     - Value of erased flash, ``0xff`` or ``0``.
   * - ``MCUBOOT_DEVn_MAPPED``
     - ``1`` if the device can be read as memory at ``base + offset``, ``0`` otherwise.
   * - ``MCUBOOT_DEVn_ECC``
     - ``1`` if every program unit has its own ECC and an interrupted program can leave an unreadable unit (STM32H5 internal flash). ``1`` switches on the torn write policy of :ref:`mcuboot_torn_write`, which needs the DFU front end, the shadow area and a write unit equal to the trailer alignment.

Besides the devices, the file gives ``MCUBOOT_BOOT_SIZE`` (the board default), ``MCUBOOT_VTOR_ALIGN`` (alignment of the application vector table) and the RAM symbols ``MCUBOOT_REQ_START`` (the request region), ``MCUBOOT_STATUS_RAM_START`` and ``MCUBOOT_STATUS_RAM_END`` (the status window), which are linker symbols. The STM32H5 description has the internal flash as device 0 (2 MiB on the STM32H573, in two banks of 128 sectors of 8 KiB, with 16 byte flash words that have ECC), ``MCUBOOT_BOOT_SIZE`` of eight sectors and, when the board defines ``MBOOT_SPIFLASH_ADDR``, a SPI flash as device 1 (see below). The STM32F7 description has the internal flash as device 0 in three runs (four sectors of 32 KiB, one of 128 KiB and seven of 256 KiB, 2 MiB on the STM32F767), programmed in 4 byte words without ECC, and ``MCUBOOT_BOOT_SIZE`` of two 32 KiB sectors. A board states a SPI flash whose size is detected at run time (PYBD_SF6, 2 MiB or 8 MiB) with a constant ``MCUBOOT_DEV1_SIZE`` that all its chips have.

RAM
~~~

``ports/stm32/mcuboot/mcuboot_sram.ld.S`` states the SRAM start and end once, from the device's CMSIS macros (``SRAM1_BASE_NS`` to the end of SRAM3, ``0x20000000`` to ``0x200A0000`` on the STM32H573, with a check that SRAM1 to SRAM3 are contiguous), and the request region size from ``mcuboot_request.h``. On the STM32F767 the SRAM is the 128 KiB DTCM RAM followed by SRAM1 (368 KiB) and SRAM2 (16 KiB), ``0x20000000`` to ``0x20080000`` (the CMSIS header has the base of SRAM2 and not its size). The build preprocesses it into ``mcuboot_sram.ld`` in the build directory (``mcuboot_sram.mk``), and both builds take the rest from it:

* The request region is the last KiB of SRAM, ``0x2009FC00`` to ``0x200A0000``. The application linker script keeps it outside its RAM.
* The filesystem cache of the application is the 8 KiB below the request region, ``0x2009DC00`` to ``0x2009FC00``, and the application RAM is below that.
* The RAM of the bootloader is a 64 KiB window at the start of SRAM.
* The status window is the RAM between the bootloader window and the request region, ``0x20010000`` to ``0x2009FC00``. It is where an fsload request may name a word that receives the result (see :ref:`mcuboot_handoff`).

On the STM32F767 the request region is ``0x2007FC00`` to ``0x20080000``, the application RAM is everything below it (the filesystem is on SPI flash, so there is no filesystem cache area) and the status window is ``0x20010000`` to ``0x2007FC00``.

Derived areas
~~~~~~~~~~~~~

``mcuboot_layout.h`` places the areas from the inputs:

* ``boot``: ``MCUBOOT_BOOT_SIZE`` bytes at the start of device 0, a number of erase units of the run it lies in. Never written by DFU, fsload or the :mod:`mcuboot` module.
* ``primary``: ``MCUBOOT_PRIMARY_SIZE`` bytes at ``MCUBOOT_PRIMARY_ADDR``, in one run.
* ``secondary``: at ``MCUBOOT_SECONDARY_ADDR``. The size is the primary slot plus one erase unit (swap using offset, where the update image starts one erase unit in), minus one erase unit (swap using move) or the same as the primary slot. Not present with the single policy.
* ``log``: two erase units of the run at ``MCUBOOT_LOG_ADDR``, by default directly behind the primary slot. The update log is always present, and the DFU interface has a log alternate setting.
* ``seccnt``: two erase units of the run at ``MCUBOOT_SECCNT_ADDR``, by default behind the log, only with ``MCUBOOT_ROLLBACK_COUNTER = 1`` and without ``MCUBOOT_SECCNT_PORT``. It holds the security counter records.
* ``scratch``: with swap using scratch, ``MCUBOOT_SCRATCH_SIZE`` bytes behind the shadow area (or at ``MCUBOOT_SCRATCH_ADDR``).
* ``intent``: with the single policy and fsload, one erase unit of its run behind the scratch area (or at ``MCUBOOT_INTENT_ADDR``).
* ``shadow``: on a device 0 with ECC, behind ``seccnt`` (or behind ``log``, or at ``MCUBOOT_SHADOW_ADDR``), holding the shadow copies of the swap state words. Its size is the number of trailer sectors of one slot, times the number of slots on an ECC device, times the erase unit of the slots (16K on the NUCLEO_H563ZI).
* ``fs``: the application filesystem. By default it starts behind the areas above and ends at the secondary slot, or at the end of device 0 when the secondary slot is on device 1 or the policy is single. ``MCUBOOT_FS_ADDR`` and ``MCUBOOT_FS_SIZE`` place it elsewhere, also on device 1.

Every area lies in one run of equal sectors, starts at a multiple of the erase unit of that run and overlaps no other area. The slots, the scratch area and the shadow area share one erase unit.

The swap state at the end of each slot takes, with the swap policy, two states per sector of the larger slot at the largest write unit (three for swap using move and swap using scratch), five more units (four for move and scratch) and the magic, rounded up to whole erase units. The overwrite-external and single policies keep no status table: three (overwrite-external) or four (single) units plus the magic, and the largest image is the primary size minus that state, without rounding. With the single policy the update region (the DFU image alternate setting, :class:`mcuboot.Writer` and fsload) is the whole slot, because the only slot holds no swap state. With the swap policy the largest image is the primary size minus the swap state sectors, and one erase unit less again with swap using move. The application is linked at the start of the primary slot plus the header size. Its length is the smaller of the largest image and the room in the update slot, minus the header and ``MCUBOOT_TLV_RESERVE``. The sector count of the larger slot is the number the swap algorithm is built for (at most 4096).

For the NUCLEO_H563ZI the derived values are: swap state 2688 bytes (one erase sector); largest image 647168 bytes (``0x9E000``); application linked at ``0x08010400`` with ``0x9D800`` bytes available; DFU image alternate setting ``@Secondary slot /0x08102000/79*008Kg``; log alternate setting ``@Update log /0x080B0000/02*008Ka``. The DFU image alternate setting starts after the spare sector and ends where the swap state of the secondary slot begins, so the host cannot write swap state. The flash map tables (``shared/mcuboot/src/flash_map.c``) and DFU region tables (``shared/mcuboot/src/dfu_regions.c``) are static C code over these macros.

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
* ``MCUBOOT_VALIDATE_PRIMARY = 0`` with the single policy or in a production build (``MCUBOOT_PRODUCTION=1`` makes the build pass ``-DMCUBOOT_PRODUCTION=1`` to the compiler), and a ``#warning`` for it otherwise;
* a header and TLV reserve that leave no room in the largest image, and an update slot without room for an image in front of its trailer;
* ``MCUBOOT_FIH_LEVEL`` above 3, an unknown ``MCUBOOT_CRYPTO``, ``MCUBOOT_SECCNT_PORT`` without ``MCUBOOT_ROLLBACK_COUNTER = 1``, ``MCUBOOT_FSLOAD_GZIP`` without a reader, ``MCUBOOT_LOG_LEVEL`` outside 0 to 4, a ``MCUBOOT_TMPBUF_SZ`` that is not a multiple of the trailer alignment, a security counter that does not fit 32 bits and USB identifiers that are not 16 bit.

``tests/mcuboot/test_layout.py`` builds each of these cases.

Requirements of an STM32H5 board
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Besides the ``MCUBOOT_*`` inputs, the board's ``mpconfigboard.h`` has to define the pins the bootloader uses, named the way the application names them and the way ``ports/stm32/mboot/mphalport.h`` provides them: plain ``pin_<port><number>`` names and the aliases from the board's ``pins.csv`` (the bootloader build generates the pin definitions with ``make-pins.py --mboot-mode``). The macros are ``MICROPY_HW_LED1``, ``MICROPY_HW_LED2`` and ``MICROPY_HW_LED3`` (alive, USB active, error) with ``MICROPY_HW_LED_ON`` and ``MICROPY_HW_LED_OFF``; ``MICROPY_HW_USRSW_PIN`` with ``MICROPY_HW_USRSW_PULL`` and ``MICROPY_HW_USRSW_PRESSED`` (the forced entry button); and, for a log level above 0, ``MICROPY_HW_UART_REPL`` set to USART3 with ``MICROPY_HW_UART_REPL_BAUD`` and ``MICROPY_HW_UART3_TX``. The board's ``mpconfigboard.mk`` needs ``PYOCD_TARGET`` for the deploy targets (``stm32h563zitx`` on the NUCLEO_H563ZI). A missing definition stops the bootloader build with an ``#error`` naming it.

.. _mcuboot_pybd_sf6:

STM32F7 board (PYBD_SF6)
~~~~~~~~~~~~~~~~~~~~~~~~

PYBD_SF6 is an STM32F767 board with 2 MiB of flash and a SPI flash #1 that holds the application filesystem. It uses the single policy. A firmware image of 1.1 MB needs five of the seven 256 KiB sectors, so two slots do not fit. The one slot is updated in place by DFU or from a file on the filesystem (fsload), an update does not revert, and an image that fails validation or was cut short is never started.

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
* fsload: :func:`mcuboot.request_fsload` names a file on the FAT filesystem. The bootloader validates the whole file before it writes anything. It then records the request in the intent area and writes the slot. A cut during the write leaves a slot that is not started, and the next boot repeats the load from the intent record when the file is still there. Otherwise the bootloader enters DFU mode.
* :class:`mcuboot.Writer` and :func:`mcuboot.request_upgrade` raise ``OSError(EPERM)``. :func:`mcuboot.confirm` does nothing and :func:`mcuboot.state` reports a confirmed image. There is no test swap, so an installed image is final.
* The first image of a device and the bootloader are programmed with a programmer. The board file sets no ``PYOCD_TARGET``, so the deploy targets need it on the command line.

The USR button is on PA13, the pin of SWDIO. While the bootloader samples it, a debugger cannot access the part.

**Flash without ECC.** The flash is programmed in 4 byte words, has no ECC and is memory mapped. The bootloader keeps no shadow words and starts no NMI handler. It relies on one 4 byte program being atomic across a power cut, so that a word holds the old or the new data after a cut. That is a property of the part. It has not been measured, and the host tests only assume it (see :ref:`mcuboot_torn_write`). If a word is torn anyway, a counter record is rejected by its CRC, a log record is skipped and a torn word in the image body fails the validation of the image, so the device ends in DFU mode.

The bootloader runs at the 144 MHz board clock with the instruction and data caches of the Cortex-M7 on. After an erase or program the lines of the data cache that cover the flash are invalidated, and the instruction cache is dropped. The data cache is cleaned before a reset (it holds the request region) and cleaned and disabled before the application starts. USB is the OTG_HS core in full-speed mode on its internal PHY (PB14 and PB15) with the 48 MHz clock from the PLLQ output of the board clock. PYBD_SF6 has no log UART. The reset flags, the fault counter and the handoff word are in the RTC backup registers 28 to 30.

**Limits.** One slot, no revert, no test swap. Images of up to 1310672 bytes. The bootloader log level is 0 (there is no log UART, USB is the console) and the fault injection hardening level is the default, 0. ``MCUBOOT_TEST_FI=1`` is not available on the STM32F7. The counter area takes two 256 KiB sectors because the sectors behind the slot are all of that size. The STM32F7 flash endurance is not stated in this tree.

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
     - never started; DFU mode
   * - Test swap and revert
     - none
     - not available with one slot

mboot has what MCUboot does not have on this board: a smaller bootloader and no flash set aside for a log or counter, DFU access to the whole flash and to the SPI flashes, flash upload, the I2C interface and the optional packed update files with signing and encryption (``MBOOT_ENABLE_PACKING``, off for this board). MCUboot has what mboot does not have: a signature check at every boot, rollback protection and an update that is validated before it is accepted. The boot time of MCUboot with the ECDSA check has not been measured on this board.

SPI flash
~~~~~~~~~

A board can describe a SPI flash as device 1. ``ports/stm32/mcuboot/mcuboot_dev.h`` derives it from the macros ``ports/stm32/mboot`` uses for the same flash: ``MBOOT_SPIFLASH_ADDR`` (the DFU address of the flash, which is its base in the flash map), ``MBOOT_SPIFLASH_BYTE_SIZE`` and ``MBOOT_SPIFLASH_ERASE_BLOCKS_PER_PAGE`` (the erase unit of device 1 is that many 4 KiB blocks). ``port_flash.c`` uses ``MBOOT_SPIFLASH_SPIFLASH`` (the ``mp_spiflash_t``) and ``MBOOT_SPIFLASH_CONFIG`` to initialise the flash and to forward device 1 reads, writes and erases to ``shared/mcuboot/src/flash_spiflash.c``, a thin layer over ``drivers/memory/spiflash.c``. Device 1 is described as not memory mapped, without ECC, and with the write unit of device 0.

``mcuboot_layout.h`` allows two areas on device 1: the secondary slot (``MCUBOOT_SECONDARY_ADDR`` at or above the device 1 base) and the filesystem (``MCUBOOT_FS_ADDR`` and ``MCUBOOT_FS_SIZE``). The primary slot, bootloader region, update log, counter area, shadow area and the scratch and intent areas stay on device 0. A secondary slot on device 1 needs the device 1 erase unit to equal the one of device 0, a device 1 write unit equal to the trailer alignment, and has to fit in device 1. The shadow area then only covers the trailer sectors of the primary slot, and the default filesystem extends to the end of device 0. DFU alternate setting 0 is the update image area of the secondary slot at its SPI address, starting one erase unit after ``MCUBOOT_SECONDARY_ADDR`` with swap using offset, as for a slot in device 0.

The secondary slot is image data that the bootloader checks by hash and signature before swapping and again afterwards, and its trailer holds requests that fail closed when a write is torn: a torn magic never reads as valid, so the update is ignored, and a flag byte reads as set only when its program completed. Everything that decides a swap (the swap status and the primary slot flags) is written to the primary trailer on device 0 and covered by the shadow words. The filesystem is read only for the bootloader. The primary slot, security counter, update log and shadow area stay on device 0: the primary slot executes, and the counter and shadow words need a write unit that a power cut cannot leave half written. An interrupted erase of a trailer sector could leave a valid magic next to erased flags on NOR flash, which would lose a pending revert, so the backend programs the last 16 bytes of each erase block to zero before it erases the block.

The host tests run the real ``drivers/memory/spiflash.c`` against a model of a NOR chip (``tests/mcuboot/host/spi_nor``): 4 KiB erase blocks, byte programming, one page program or block erase per power cut position, with torn bytes and with the whole page or block left undefined. Swap, revert, confirm, permanent upgrade, DFU sessions, rejected and interrupted updates and fsload from a filesystem on the chip gave no halt, no lost revert and no state without a way to DFU in tens of thousands of sweep trials and about 8900 bootloader cut runs. There is no hardware run with a SPI flash. The model does not cover retention of partly erased cells or program disturb, and only the QSPI path of the driver was exercised.

Generated files
~~~~~~~~~~~~~~~

.. code-block:: bash

    python3 tools/mcuboot_gen.py --out /tmp/mcuboot_gen --board NUCLEO_H563ZI --key tests/mcuboot/keys/test-ecdsa-p256.pem -- arm-none-eabi-gcc -Ishared/mcuboot/include -Iports/stm32/boards/NUCLEO_H563ZI -Iports/stm32/mcuboot -mcpu=cortex-m33 -DSTM32H573xx -Ilib/CMSIS_6/CMSIS/Core/Include -Ilib/stm32lib/CMSIS/STM32H5xx/Include -Iports/stm32

The generator runs the command after ``--`` (the build's compiler with its include and define options) as a C preprocessor over ``mcuboot_layout.h``, evaluates the integer expressions it gets and writes the files below into ``--out``. It needs a C compiler and the Python package ``cryptography`` for the key table (the signing tools need all the packages in ``lib/mcuboot/scripts/requirements.txt``: ``cryptography``, ``intelhex``, ``click``, ``cbor2`` and ``pyyaml``). The build runs it once for the bootloader and once for the application when the make fragment is read, each into ``<build directory>/mcuboot_gen``, and again when ``mpconfigboard.h``, ``mcuboot_dev.h``, ``mcuboot_layout.h``, the generator or the key file changes.

.. list-table::
   :header-rows: 1
   :widths: 35 65

   * - File
     - Content
   * - ``mcuboot_layout.ld``
     - Linker symbols: the areas (``MCUBOOT_BOOT_START``, ``MCUBOOT_PRIMARY_START``, ``MCUBOOT_SECONDARY_START``, ``MCUBOOT_LOG_START``, ``MCUBOOT_FS_START`` and so on with ``_SIZE``, also for the seccnt, shadow, scratch and intent areas the layout has), ``MCUBOOT_HEADER_SIZE``, ``MCUBOOT_MAX_IMAGE_SIZE``, ``MCUBOOT_APP_START`` (the link address of the application, after the header) and ``MCUBOOT_APP_LEN``. The RAM symbols come from ``mcuboot_sram.ld``, generated from ``mcuboot_sram.ld.S``.
   * - ``mcuboot_layout.mk``
     - Make variables for the Makefiles: ``MCUBOOT_APP_LINK_ADDR``, ``MCUBOOT_PRIMARY_ADDR``, ``MCUBOOT_LAYOUT_ID``, ``MCUBOOT_DFU_ENABLE``, ``MCUBOOT_DFU_WRITE_ALIGN``, ``MCUBOOT_DFU_UPDATE_ADDR``, ``MCUBOOT_SPIFLASH_ENABLE``, ``MCUBOOT_BL_VERSION``, the imgtool argument lines ``MCUBOOT_IMGTOOL_SIGN_ARGS`` and ``MCUBOOT_IMGTOOL_INITIAL_ARGS``, and the choices that select source files and signing arguments: ``MCUBOOT_POLICY`` (``swap``, ``overwrite-external``, ``single``), ``MCUBOOT_SWAP_MODE`` (``offset``, ``move``, ``scratch``), ``MCUBOOT_CRYPTO`` (``tinycrypt``, ``mbedtls``), ``MCUBOOT_ROLLBACK`` (``none``, ``version``, ``counter-flash``, ``counter-port``), ``MCUBOOT_FIH`` (``off``, ``low``, ``medium``, ``high``), ``MCUBOOT_CONFIRM_MODE`` (``manual``, ``auto``), ``MCUBOOT_HASH_DIRECT``, ``MCUBOOT_VALIDATE_PRIMARY``, ``MCUBOOT_FSLOAD`` (the readers: ``raw``, ``fat``, ``lfs2`` and ``gzip``) and ``MCUBOOT_FSLOAD_ENABLE``.
   * - ``mcuboot_layout.json``
     - A machine readable copy of the layout: policy, swap mode, rollback mode, crypto library, hardening level, confirmation mode, devices (with the runs of equal sectors of each), areas (with the erase unit of each), header size, largest image, smallest image (``min_image_size``), application link address, DFU update address, imgtool arguments, the fsload readers, the layout id and the hash of the public key. ``tools/mcuboot_sign.py``, the tests and the hardware scripts read it.
   * - ``mcuboot_keys_gen.c``
     - The public key of ``--key`` in the form of MCUboot's ``keys.c`` (``bootutil_keys``). Linked into the bootloader.

The linker symbols are plain assignments (``NAME = value;``). ``ports/stm32`` passes ``mcuboot_layout.ld`` to the linker as a ``-T`` script ahead of its own, and the symbols are then visible to the ``MEMORY`` expressions of the next script. A linker script that goes through the C preprocessor can instead take ``$(MCUBOOT_LD_LAYOUT)`` first in ``LD_FILES`` and use the symbols like numbers: ``FLASH (rx) : ORIGIN = MCUBOOT_APP_START, LENGTH = MCUBOOT_APP_LEN``. ``tests/mcuboot/host/ldtest`` links a toy image both ways and checks its addresses against ``mcuboot_layout.json`` (``make -C tests/mcuboot/host/ldtest test``, also ``BOARD=h5``).

Layout id
~~~~~~~~~

The layout id is a 32-bit hash the preprocessor computes (``MCUBOOT_LAYOUT_ID``): an FNV style hash over the primary slot address and size, the secondary slot address and size, the log address, the filesystem address and size, the header size, the erase unit of the slots, the largest write unit, the rollback mode and the interface version. When they differ from the default configuration (swap using offset, counter in flash, version check), the slot policy, swap mode, port counter, missing version check and scratch and intent placement are hashed too, and so are a counter or shadow address that is not the default one. The generator reads the same expression, so the C code and the tools see one value. It changes whenever one of these changes (``3535692f`` for the NUCLEO_H563ZI, ``63a90b9f`` for the PYBD_SF6). It is stored in the bootloader's information block and signed into every image (see :ref:`mcuboot_signing`).

Building
--------

Prerequisites: the ``lib/mcuboot`` submodule (``git submodule update --init lib/mcuboot``), the stm32 submodules (``make -C ports/stm32 BOARD=NUCLEO_H563ZI submodules``), ``mpy-cross``, ``arm-none-eabi-gcc`` and the Python packages named above. The deploy targets also need ``pyocd``.

Bootloader
~~~~~~~~~~

.. code-block:: bash

    make -C ports/stm32/mcuboot BOARD=NUCLEO_H563ZI

The output is ``ports/stm32/mcuboot/build-NUCLEO_H563ZI/firmware.elf``, ``firmware.bin`` and ``firmware.hex`` (and the link map). ``BUILD=`` overrides the build directory ``build-$(BOARD)``.

* ``MCUBOOT_CLK=hsi`` keeps the reset clock instead of the board's PLL clock.
* ``MCUBOOT_PUBKEY=<file>`` names the key whose public key the bootloader accepts (default: the public test key, see :ref:`mcuboot_signing`).
* ``MCUBOOT_PRODUCTION=1`` refuses a public test key as ``MCUBOOT_PUBKEY`` (see :ref:`mcuboot_signing`).
* ``MCUBOOT_TEST_FI=1`` adds the fault injection hook and the register level flash driver of ``tests/mcuboot/hw/fi_flash.c``, which the hardware test campaigns use. Do not use it for released firmware (see :ref:`mcuboot_warnings`).
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
     - ``firmware.initial.bin`` and ``firmware.initial.hex``: the first image of a device. The binary covers the whole primary slot including the swap state, with the image marked confirmed (with the overwrite-external policy the image has no swap state and no confirmation, see :ref:`mcuboot_signing`).
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

Always name the probe by its serial number, so a different board on the same computer is never programmed by mistake.

The application filesystem of an ``MCUBOOT=1`` build is the ``fs`` area of the layout. That is not where the board's default firmware keeps its filesystem, and nothing copies an existing filesystem across.

.. _mcuboot_signing:

Signing, keys, layout id and production mode
--------------------------------------------

Images are signed with ECDSA over NIST P-256 and SHA-256, and tinycrypt verifies them in the bootloader. ``tools/mcuboot_sign.py`` wraps MCUboot's ``imgtool`` (imported from ``lib/mcuboot/scripts``, never a system installed copy) and passes it the arguments the generator derives from the board configuration. Its subcommands:

* ``sign``: make the update image. It adds the header, SHA-256, key hash, signature, security counter and layout id, and checks that the image fits. ``--external digest`` writes the digest for a hardware security module to sign, and ``--external apply --sig <file>`` inserts the signature that comes back. ``--version`` and ``--security-counter`` override the layout values.
* ``initial``: make the first image of a device, padded to the slot, with the swap state written and the image marked confirmed. With the overwrite-external policy the image has no swap state and is not marked, because imgtool writes no ``image_ok`` for an overwrite-only layout. ``--hex`` also keeps the Intel HEX file.
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

``1`` (flash counter, or port counter)
    Every image carries a security counter (``MCUBOOT_SECURITY_COUNTER``) that is independent of its version. The bootloader refuses an image with a counter below the stored value, including one already in the primary slot, and raises the stored value only for an image that has been confirmed or installed permanently (with the single policy it raises it to the counter of the image it starts, since that slot has no test state). The counter is kept as records in the two erase unit ``seccnt`` area. Anything that can erase that area can reset the counter, so this mode protects against software downgrades, not against an attacker who can write flash.

``1`` with ``MCUBOOT_SECCNT_PORT`` (port counter)
    The same rule, with the stored value kept by the port through ``mcuboot_port_seccnt_read()``, ``_write()``, ``_can_update()`` and ``_lock()`` (``shared/mcuboot/src/security_cnt.c`` connects them to bootutil). For a counter in OTP, fuses or a battery backed register that flash writes cannot reach. ``_write()`` raises the value and has to accept the value it already holds. ``_can_update()`` says whether the value can still be reached (a fuse counter has a limited number of steps), and an update it refuses is not installed. ``_lock()`` is called after an update and may return 0 without doing anything. There is no ``seccnt`` area.

The single policy needs the counter, because the old image's version is gone once the only slot has been overwritten. With the overwrite-external policy, the image for the first programming of the primary slot (the ``initial`` target) is made without the trailer imgtool would add. imgtool writes no ``image_ok`` flag for an overwrite-only layout, and bootutil only raises the counter of a slot with a good magic when that flag is set.

A production board should use a security counter and a fault injection hardening level of 1 (low) or higher. The configuration enforces neither, and does not require the counter for swap using move or the overwrite-external policy.

Production mode
~~~~~~~~~~~~~~~

Production mode is selected with ``MCUBOOT_PRODUCTION=1`` in the environment or on the make command line, or ``--production`` when the tools are called directly. In this mode:

* ``mcuboot_common.mk`` (through ``mcuboot_sign.py check-key --production``) and ``mcuboot_sign.py`` refuse any key whose public key equals one of the public test keys (the ``*.pem`` files in ``tests/mcuboot/keys`` and in the top directory of ``lib/mcuboot``). The default ``MCUBOOT_PUBKEY`` is a test key, so ``MCUBOOT_PUBKEY`` has to be given.
* The Makefiles set no default ``MCUBOOT_SIGN_KEY``, so ``all`` makes no signed image unless a key is given, and ``sign`` stops with an error when none is set.

The private key does not have to be on the build host. ``make sign-digest`` writes the image digest, a hardware security module signs it, and ``make sign-apply SIG=<file>`` produces the signed image (the signature file is the base64 DER form imgtool writes with ``--sig-out``).

Production mode only filters keys. It does not make a bootloader secure by itself; see :ref:`mcuboot_warnings` for what remains open.

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

If the reset comes before :func:`mcuboot.confirm` is called, for any reason (crash, watchdog, power cycle), the bootloader swaps the old image back and starts it. The revert is recorded in the update log. A board that defines ``MCUBOOT_CONFIRM_AUTO`` confirms the test image itself once ``boot.py`` has finished; a reset before that still reverts. The overwrite-external and single policies have no test image: the installed image is final.

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

When the last block has been sent the host ends the transfer, and the bootloader validates the image: header, sizes against the slot, layout id, hash, signature and rollback rule. A good image is marked as a test image (permanent with the overwrite-external policy; with the single policy the image is already in place and nothing is marked). The device then waits for a USB reset, which ``dfu-util`` and ``pydfu.py`` issue, and restarts into the boot decision, which swaps the image in. ``dfu-util`` can report an error exit status after ``Done!`` because the device resets itself off the bus.

A bad image is rejected and the DFU status is an error: ``errFILE`` for a hash, signature, rollback, layout, target flag or minimum size failure, ``errADDRESS`` for a header or size failure, ``errWRITE`` for a flash error. The first sector of a rejected image is erased so it cannot linger. With the single policy a rejected image has already overwritten the slot, so the bootloader removes its header and the next boot enters DFU mode. After a rejection the host clears the error and can send another image. A 535 KB image took about 30 seconds to download with ``dfu-util`` on the NUCLEO_H563ZI.

The vendor request ``0x81`` (device to host, 16 bytes) returns the result of the last operation of the session, which gives the reason for a rejection. Decode it with ``python3 tools/mcuboot_log.py --result <32 hex digits>``. ``tests/mcuboot/dfu_raw.py`` can issue the request. The update log also records every accepted and rejected image.

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

The bootloader runs the load in two passes. Pass 1 opens the file and validates the image completely (header, whole-file read through for gzip, layout id, hash, signature, rollback rule) without writing any flash except the update log (and, with the single policy, the erase of a leftover intent record). Pass 2 clears any earlier update state, copies exactly the validated bytes into the update slot, and only then marks the image as a test image. The bootloader then resets and the swap happens at that boot. A power loss during pass 2 ends differently for each policy:

* Swap: the primary slot is as it was and nothing is pending.
* Overwrite-external: pass 2 marks the image permanent, and the copy over the primary slot is repeated at the next boot.
* Single: pass 2 stores the request in the intent area, writes the image into the only slot, validates it again and erases the intent record. The next boot finds no valid image and restarts the load from the intent record. The file has to still be there, otherwise the load fails and the device enters DFU mode. The old image is lost when pass 2 starts.

The request is untrusted until the image has been validated, so the parsers for it and for the filesystems are bounded: reads stay inside the window, size arithmetic is checked and there is no heap use. Those parsers are fuzzed (see :ref:`mcuboot_tests`). gzip is decoded by ``lib/uzlib``, which this support does not change. The decompressed size is limited to the update slot, and the decompressed image goes through the same validation. In practice that means:

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

The application leaves a *request* in a 1 KiB region of RAM at ``mcuboot_req_start`` (``0x2009FC00`` on the NUCLEO_H563ZI: the last KiB of SRAM, directly above the application's 8 KiB filesystem cache). It then writes a *retention word* (``0x70AD0000`` plus a flag byte) into a register that survives reset (``TAMP->BKP30R``) and resets. The region is outside the application's RAM, and the application neither clears nor initialises it. The request holds a magic number, its mode (DFU or fsload), its length and a CRC-32, followed by up to 988 bytes of an element stream. If the request does not check out but the retention word has the right key, the bootloader enters DFU mode without elements. The H5 port also uses ``TAMP->BKP29R`` to count consecutive bootloader faults and ``TAMP->BKP28R`` to give the application the reset flags the bootloader clears. Code that writes backup registers through ``machine.mem_backup()`` must leave ``BKP28R`` to ``BKP30R`` alone. On the STM32F7 the three words are ``RTC->BKP28R`` to ``RTC->BKP30R`` and the request region is at ``0x2007FC00``.

An element stream is a sequence of ``type``, ``length`` and payload bytes ended by an ``END`` element of length 0. The types are those of ``ports/stm32/mboot``:

.. list-table::
   :header-rows: 1
   :widths: 15 85

   * - Element
     - Payload
   * - ``END`` (1)
     - none.
   * - ``MOUNT`` (2)
     - ``mount point`` (1 byte), ``fs type`` (1 byte: 1 is FAT, 3 is littlefs 2, 4 is raw; 2 is littlefs 1, which is not offered), ``base`` and ``len`` (32-bit little endian, DFU addresses of the flash window), optionally ``arg2`` (the littlefs block size, or the address of the second raw window) and ``arg3`` (the length of the second raw window). Payload lengths are 10, 14 and 18.
   * - ``FSLOAD`` (3)
     - ``mount point`` (1 byte) followed by the path (1 to 254 bytes, not terminated).
   * - ``STATUS`` (4)
     - A 32-bit RAM address. The bootloader stores 0 there on success, or the negated result code on failure, before it resets. It only stores inside the status window (the RAM between the bootloader's RAM window and the request region, ``0x20010000`` to ``0x2009FC00`` on the NUCLEO_H563ZI), and only to a word aligned address. :func:`mcuboot.request_fsload` does not send this element; only the ``bytes`` form of :func:`machine.bootloader` can.

A request is not a way round the signature checks: an image that a request installs is validated like any other.

.. _mcuboot_torn_write:

Torn write policy for ECC flash
-------------------------------

The STM32H5 flash is programmed in 16 byte words, each with its own ECC. If power is lost or the device resets while a word is being programmed, the word can end up erased, completely programmed, uncorrectable (it raises a non-maskable interrupt when read, and the read still returns data), or corrected by the flash controller on read with no error flag and returning either the erased value or an arbitrary part of the data (a flag byte of ``0x01`` was seen to read as ``0xEB``). A word that is not erased cannot be programmed again: a second program gives the bitwise AND of the old and new data with an invalid ECC. A reset does not interrupt an erase, it carries on. Only a real loss of supply can tear an erase.

MCUboot's bootutil knows nothing of this. With the library as it is, a torn word in the swap state or the image header led, in measurements on the NUCLEO_H563ZI, to an assertion failure, an abandoned swap, a lost revert or an empty primary slot. So the support adds a policy below bootutil, in the flash map backend (``shared/mcuboot/src/flash_map_backend.c``, ``shadow.c``) and the port flash layer (``ports/stm32/mcuboot/port_flash.c``), without changing bootutil. It applies to every device with ``MCUBOOT_DEVn_ECC = 1``:

* **Failed reads are visible.** The NMI handler of the bootloader and the application clears the flash ECC flag and counts the event, and the port flash layer reports a read that raised an ECC error as an error. The policy layer also treats a word the controller had to correct as unreadable. Both builds contain the handler.
* **Shadow words.** The trailer sectors of both slots hold all of bootutil's swap state (the image ok, copy done and swap info flags, the sizes, the magic and the swap status table). Each 16 byte word in these sectors has a second copy at the same relative place in the ``shadow`` area. A write programs the word and then its shadow, and an erase erases the sector and then its shadow sector. Reading an unreadable word returns the shadow value if that is valid and the erased value if not. A cut between the two steps of a write or erase therefore reads as a state bootutil already knows (not written, or erased).
* **Scrub at startup.** Before ``boot_go()`` the bootloader completes missing shadow words and finishes an erase that a reset interrupted between the sector and its shadow.
* **Image header window.** An unreadable word in the first 32 bytes of the first or second sector of a slot reads as erased, so a torn header reads as "no image" and does not stop the swap logic.
* **No rewriting data.** The port flash layer never programs a word that already holds data: an all-erased word is skipped, an identical word is skipped and anything else is refused. Every programmed word is read back and every erased sector is checked.
* **Records with their own check.** The update log and security counter use records with a CRC. A torn record reads as a used slot, never as a valid record, and a slot that reads erased but is not blank is not reused.
* **Image data is not shadowed.** A torn word in the image body makes the hash check fail, which rejects the image. The swap algorithm only records a sector as copied after copying it, so a resumed swap copies it again.

For one interrupted write or erase at any point of a boot, swap, revert, confirmation, DFU session start or log append, this gives the following with the swap policy (the overwrite-external and single policies have no old image once an update starts, see their descriptions above):

* The bootloader does not halt, assert forever or stall on an NMI. Every failure on the way to the boot decision ends in DFU mode (see :ref:`mcuboot_recovery`).
* A running image is not lost, and a test image that was not confirmed is reverted.
* The device boots either the old or the new image, or is in DFU mode where a new image can be installed.

This was checked on the host with a flash model that tears every operation at different positions (including the corrected-word case), for swaps of 3 and 15 sectors. It was also checked on a NUCLEO_H563ZI with a test build (``MCUBOOT_TEST_FI=1``) that resets the board at every flash write and erase of a swap and a revert (every eighth copy of image data for the 15 sector images), before the operation, after it, and 10 to 14 microseconds into a program (the window that tears a word). The scripts are in ``tests/mcuboot/fi_sweep.py`` and ``tests/mcuboot/hw`` (see :ref:`mcuboot_tests`). Each trial interrupts one operation, so the guarantees cover one interruption at a time.

Limits and what was not tested:

* **Two cuts on the same word.** If a swap state word is torn and the next boot is torn again while it rewrites the shadow, both copies are invalid and the flag reads as erased until the sector is erased. Two-cut host sweeps have lost a revert, and a confirmed flag at the end of a revert, this way.
* **Two cuts in a resumed revert.** A cut while the primary image is copied back during a revert, followed by a second cut in the first operations of the resumed revert, leaves bootutil with a swap state it cannot interpret and no usable image in the primary slot. The bootloader then erases the swap state and the first sector of the primary slot and enters DFU mode, where a new image has to be installed.
* **Supply loss.** The hardware runs used software resets inside the program window. A torn erase can only come from a real supply loss, and none was applied to a board. The state of a partly erased word is not characterised.
* **Fault injection attacks.** With the profiles ``low`` and above, bootutil stops in an endless loop (``FIH_PANIC``) when it detects a glitch, and the bootloader starts no watchdog. A power cycle ends such a loop.
* **TrustZone and the bank swap option byte.** The policy was exercised on the STM32H5 in the NUCLEO_H563ZI layout with its bank 1 / bank 2 split. TrustZone and the bank swap option byte have not been exercised.

A device with ``MCUBOOT_DEVn_ECC = 0`` gets none of this policy: it has no shadow words and no NMI handler. The bootloader relies on the program of one write unit being atomic across a power cut, so that the unit holds the old or the new data afterwards. Whether a flash does that is a property of the part. On the STM32F7 (PYBD_SF6, 4 byte write unit) it has not been measured, and the core harness takes it as given (``atomic_program`` of its fake flash). The host sweeps that tear bits instead lose a revert on a swap policy flash without ECC (the ``plain`` and ``nrf`` boards) when the cut tears the ``copy_done`` flag of the primary trailer, so such a flash needs the property to hold. The PYBD_SF6 has no swap state. If a word is torn there:

* a security counter record is rejected by its CRC and reads as a used slot;
* a torn update log record is skipped;
* a torn word in the image body fails the hash check, so the image is not started and the device enters DFU mode;
* a cut during an install leaves the only slot without a valid image (*fail closed*), and a DFU install recovers the device.

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

.. _mcuboot_tests:

Running the tests
-----------------

The tests need the Python packages named above plus ``pytest``, ``gcc`` and ``make`` for the host tests, ``arm-none-eabi-gcc`` for builds and the ``lib/mcuboot`` submodule. Commands run from the repository root. ``tools/ci.sh`` has one ``ci_mcuboot_*`` function per group below and ``.github/workflows/mcuboot.yml`` runs them. Continuous integration does not run the libFuzzer targets, ``run_sim.py``, the core boards ``nrf``, ``f7_offset`` and ``f7_scratch`` (and the core ``control`` and ``arm`` targets), the host sweeps ``spi_big``, ``f7_offset`` and ``f7_scratch``, the bootloader sweeps of ``bl_spi`` and ``bl_f7`` or the hardware scripts. It builds the NUCLEO_H563ZI and PYBD_SF6 bootloaders and applications.

Python tests
~~~~~~~~~~~~

.. code-block:: bash

    python3 -m pytest tests/mcuboot shared/tinyusb/mboot/tests/test_pydfu_wire.py -q

This runs the configuration tests (``tests/mcuboot/test_layout.py``: the layout derived for the NUCLEO_H563ZI, its flash geometry from the device headers, the C layout id against the one the generator reads, the slot policies and swap modes, every configuration ``mcuboot_layout.h`` refuses, devices with several runs of equal sectors (the real PYBD_SF6 board against the host board ``f7``), the minimum image size of the signing tool, the SPI flash cases and the files the generator writes; ``tests/mcuboot/test_layout_features.py``: the fsload readers, the crypto choice, the hardening levels, the port counter and automatic confirmation), the tests of the tools and host harness drivers (``tests/mcuboot/host/test_tools.py``), the STM32 port tests (``tests/mcuboot/host/test_port_stm32.py``: the ``MCUBOOT=1`` build directory, the layout the bootloader and application of a board agree on, the bounded flash cache wait and the bootloader port board checks; for the PYBD_SF6 also that the application and the bootloader agree on the layout, that a slot across sectors of different sizes is refused and that the bootloader links into the boot area; the checks that need ``arm-none-eabi-gcc`` are skipped without it) and the ``pydfu.py`` wire tests, which need no ``pyusb``. ``tests/mcuboot/conftest.py`` keeps ``test_module.py`` and ``test_module_single.py`` out of collection, because those are MicroPython tests.

Host tests of the C code
~~~~~~~~~~~~~~~~~~~~~~~~

These build the unmodified bootutil and the ``shared/mcuboot`` sources for the host and run them over a fake flash (``tests/mcuboot/host/fake_flash.c``) that counts operations and can stop, tear or corrupt any write or erase. ``BOARD`` selects the configuration: the default ``h5`` is the real NUCLEO_H563ZI board (its ``mpconfigboard.h`` and ``ports/stm32/mcuboot/mcuboot_dev.h``), and the others are ``mpconfigboard.h`` and ``mcuboot_dev.h`` pairs in ``tests/mcuboot/host/boards`` (listed in ``tests/mcuboot/host/boards.mk``). All of them take the layout from the same ``mcuboot_layout.h`` and ``tools/mcuboot_gen.py`` as the firmware builds. The ``plain``, ``nrf`` and ``rt`` boards are flash geometries for exercising the shared code; no bootloader is built for them. The ``f7`` boards describe the flash map of the PYBD_SF6 in their own ``mcuboot_dev.h``. The boards and the harnesses that use them:

.. list-table::
   :header-rows: 1
   :widths: 16 62 22

   * - ``BOARD``
     - Configuration
     - Harness
   * - ``h5``
     - the NUCLEO_H563ZI board: ECC, 16 byte write unit, 8 KiB erase units
     - all
   * - ``stock``
     - ``h5`` on a flash without ECC
     - core ``control``
   * - ``plain``
     - a flash without ECC and without DFU: 1 MiB, 4 KiB sectors, 8 byte write unit, version check
     - core
   * - ``nrf``, ``rt``
     - 1 MiB (0x200 byte header, littlefs 2 reader) and 8 MiB (FAT reader) flash with 4 KiB sectors and a 4 byte write unit, version check
     - core (``nrf``), host sweep
   * - ``f7``
     - the PYBD_SF6 as shipped: 2 MiB of internal flash in runs of 32, 128 and 256 KiB sectors, 4 byte write unit, no ECC, single slot policy of five 256 KiB sectors with the counter in flash and an intent area, a 2 MiB SPI flash as device 1 with the FAT filesystem
     - core, host sweep
   * - ``f7_offset``, ``f7_scratch``
     - the flash map of ``f7`` with swap using offset (primary slot of three 256 KiB sectors, secondary slot of four at ``0x08100000``, version check) and with swap using scratch (two slots of three sectors, one 256 KiB scratch sector at ``0x081C0000``, version check)
     - core, host sweep
   * - ``bl_f7``
     - ``f7`` for the bootloader harness: DFU into the only slot, fsload from a FAT volume of 2 MiB on the SPI flash model
     - bootloader
   * - ``move``, ``scratch``
     - ``plain`` with swap using move (security counter) and with swap using scratch (scratch area of two erase units, version check)
     - core, host sweep
   * - ``overwrite``, ``single``
     - ``plain`` with the overwrite-external policy (security counter) and with the single slot policy (security counter, FAT reader, intent area)
     - core, host sweep
   * - ``fih_high``, ``mbedtls``
     - ``plain`` with hardening level 3 and with Mbed TLS (``lib/mbedtls``) as crypto library
     - core
   * - ``portcnt``
     - ``plain`` with the security counter kept by the port and hardening level 3
     - ``features``
   * - ``spi``, ``spi_big``
     - ``h5`` with the secondary slot in a 16 MiB SPI NOR flash; ``spi_big`` has a primary slot of 1536 KiB whose swap state spans both 4 KiB blocks of an erase unit
     - host sweep (``spi_big`` by hand)
   * - ``bl``, ``bl_ver``
     - ``h5`` with a 1 MiB SPI flash as device 1 for the FAT volumes of the fsload cases, with the raw and gzip readers; ``bl_ver`` with a version check
     - bootloader
   * - ``bl_spi``, ``bl_ow``, ``bl_single``
     - ``h5`` with a 4 MiB SPI NOR flash that holds the secondary slot and the filesystem, the same with the overwrite-external policy, and ``h5`` with the single slot policy; all with the raw and gzip readers
     - bootloader
   * - ``glue_ow``, ``glue_single``
     - ``h5`` with the overwrite-external and with the single slot policy
     - DFU glue

The boards that use ``ports/stm32/mcuboot/mcuboot_dev.h`` read the flash geometry from the STM32H5 CMSIS header, which needs ``lib/stm32lib`` and ``lib/CMSIS_6`` (``boards.mk`` adds the CMSIS directories). A host compiler cannot read that header as it stands, because the CMSIS core header includes ``arm_acle.h`` and needs the architecture macros of an Arm compiler. ``tests/mcuboot/host/cmsis_host/arm_acle.h`` defines them, and it is added with ``-idirafter`` so an Arm compiler uses its own header.

.. code-block:: bash

    make -C shared/tinyusb/mboot/tests check
    make -C shared/tinyusb/mboot/tests/glue test
    make -C shared/tinyusb/mboot/tests/glue test-board BOARD=glue_single

The DFU core (download dispatch, regions, element parsing, vendor requests, hooks, write alignment) and the MCUboot DFU glue (``dfu_glue.c`` and ``dfu_regions.c``, built with the headers of the real board) over a fake TinyUSB. ``check`` runs the core tests (``check_core``) and the glue tests (``check_glue``). The glue tests run once per slot policy: swap using offset (``BOARD=h5``), overwrite-external (``glue_ow``) and single (``glue_single``), which differ in DFU regions and write ranges. ``test-board`` runs one of them.

.. code-block:: bash

    make -C tests/mcuboot/host/core test

The glue unit tests, the functional scenarios (install, swap, confirm, revert, counters, update log, requests) and the fault injection sweeps with torn writes, on the NUCLEO_H563ZI board (ECC, 16 byte units). ``BOARD=plain`` repeats them on a device without ECC with 8 byte units and a version check, and ``BOARD=nrf`` with a 4 byte write unit. ``BOARD=move``, ``scratch``, ``overwrite`` and ``single`` run the same scenarios for swap using move, swap using scratch, the overwrite-external policy and the single slot policy on the plain device, ``BOARD=fih_high`` with the high hardening level and ``BOARD=mbedtls`` with Mbed TLS as the crypto library. ``BOARD=f7`` runs them on the PYBD_SF6 flash map with the single policy, and ``BOARD=f7_offset`` and ``f7_scratch`` with swap using offset and swap using scratch on the same map. They use images of two and three 256 KiB sectors; swap using offset refuses an image of one erase unit or less (:ref:`mcuboot_offset_limit`).

A run fails on a halt, an assertion, a lost revert, a state without a way to DFU, a swap that does not converge or an inconsistent state. A lost image is counted in its own column and listed among the failing cases, but does not decide the result of the run, because a DFU install brings the device back. The single policy has no old image to fall back to: a cut update ends without an image, which the sweep counts as the specified *fail closed* outcome and checks that a DFU install recovers the device.

``make -C tests/mcuboot/host/core control`` repeats the torn write sweep against a backend without the ECC policy (``BOARD=stock``), which is expected to fail, and ``arm`` compiles the glue for a Cortex-M core and prints the sizes. ``SAN=1 BUILD=build/san`` builds with AddressSanitizer and UndefinedBehaviorSanitizer. ``tests/mcuboot/test_layout.py`` tests the ``#error`` checks of the configuration.

.. code-block:: bash

    make -C tests/mcuboot/host/bootloader test
    make -C tests/mcuboot/host/bootloader sweep

The bootloader main flow through the real bootutil, with the harness driving the DFU and fsload front ends. ``test`` runs every functional case on the ``bl`` board (the NUCLEO_H563ZI configuration with a second flash device holding the FAT volumes of the fsload cases, and the raw and gzip readers), on ``bl_ver`` (a version check instead of the security counter), on ``bl_spi`` (secondary slot and filesystem in a SPI NOR flash), on ``bl_ow`` (the overwrite-external policy, secondary slot in the SPI flash) on ``bl_single`` (the single slot policy, with the fsload intent area retry under power cuts) and on ``bl_f7`` (the PYBD_SF6: DFU into the only slot, fsload from a FAT volume on the SPI flash model, an update of less than one erase unit taken). ``sweep`` runs the power cut sweeps (swap, revert, a DFU session, an empty and a damaged primary slot; ``SWEEP_ARGS="--stride 1 --big"`` for the long form) on ``BOARD``, which is ``bl`` unless given (``BOARD=bl_spi``, ``bl_ow``, ``bl_single`` and ``bl_f7`` for the others). ``make -C tests/mcuboot/host/bootloader test-app APP=<firmware.signed.bin>`` installs a real application image into a simulated device with the layout of the real board.

.. code-block:: bash

    make -C tests/mcuboot/host test
    make -C tests/mcuboot/host BOARD=nrf GLUE=shared test

Power cut sweeps of swap and revert over the stock bootutil. ``BOARD=move``, ``scratch`` and ``overwrite`` (with ``GLUE=shared``) sweep swap using move, swap using scratch and the overwrite-external policy. ``BOARD=single`` sweeps the install of an update over the only slot: there is no revert, and an install that is cut ends without a valid image (reported as *fail closed*), except for a cut before the first erase, which leaves the old image, and a cut after the last write, which leaves the new one. It never ends with an image that does not validate. Swap using move converges at every cut point of swap and, with the security counter, of revert; with a version check an interrupted revert is refused as a downgrade and the new image stays. The ``f7`` boards default to images of two sectors (``--sectors 2``), because the default sizes of 3 and 15 sectors do not fit their slots. Without ``GLUE=shared`` the sweep uses a minimal flash map backend that only supports swap using offset, which shows what unmodified bootutil does on its own. With ``GLUE=shared`` it uses the ``shared/mcuboot`` glue; run it for ``BOARD=h5``, ``nrf``, ``rt``, ``spi``, ``move``, ``scratch``, ``overwrite``, ``single``, ``f7``, ``f7_offset`` and ``f7_scratch``. ``make -C tests/mcuboot/host unit`` runs the tests of the Python drivers, ``layout`` prints the path of the board's layout and ``dry-run`` prints the commands only.

.. code-block:: bash

    make -C tests/mcuboot/host/features test
    make -C tests/mcuboot/host/ldtest test
    make -C tests/mcuboot/host/spi_nor test

``features`` tests the optional port hooks on the ``portcnt`` board: the port's security counter backend (``security_cnt.c`` over ``mcuboot_port_seccnt_*``: read, write, a counter that runs out of increments, lock) and the entropy source of the high hardening profile (``fih_delay_rng.c`` over ``mcuboot_port_entropy_u8``). ``ldtest`` links a toy image with the generated linker symbols, once through the C preprocessor and once with two ``-T`` scripts, and compares its addresses with the layout (``BOARD=plain``, or ``BOARD=h5``). ``spi_nor`` tests the NOR chip model of the SPI flash harnesses on its own: one operation per page program or erase block, programming that only clears bits, 32 bit addressing, the states a cut leaves behind a program and an erase, and commands the chip ignores.

.. code-block:: bash

    make -C tests/mcuboot/fuzz test

Host tests of the fsload readers (FAT, littlefs 2, raw, gzip, the element parser, the stream layer and the swap and single slot policies) with gcc, AddressSanitizer and UndefinedBehaviorSanitizer. ``fuzz-build``, ``fuzz-run T=<target> S=<seconds>`` and ``fuzz-all S=<seconds>`` build and run the libFuzzer targets (``fat``, ``lfs``, ``gz``, ``stream``, ``run`` and ``run_single``). These need clang; without it the Makefile runs a Docker image that has clang (``DOCKER_IMAGE``).

``python3 tests/mcuboot/run_sim.py`` runs MCUboot's own simulator (``lib/mcuboot/sim``, which needs a Rust toolchain and the ``ext/mbedtls-3.6.0`` submodule of ``lib/mcuboot``) over a bootutil configuration like the one of the boards. It does not use the glue and is not part of the continuous integration.

The module on the unix port
~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. code-block:: bash

    make -C mpy-cross
    make -C ports/unix VARIANT=mcuboot submodules
    make -C ports/unix VARIANT=mcuboot
    make -C ports/unix VARIANT=mcuboot_single
    cd tests
    MICROPY_MICROPYTHON=../ports/unix/build-mcuboot/micropython ./run-tests.py mcuboot/test_module.py
    MICROPY_MICROPYTHON=../ports/unix/build-mcuboot_single/micropython ./run-tests.py mcuboot/test_module_single.py

The unix variants have their own ``mpconfigboard.h`` and ``mcuboot_dev.h`` in ``ports/unix/variants/mcuboot`` (swap using offset, version check) and ``ports/unix/variants/mcuboot_single`` (the single slot policy with the security counter): a 1 MiB file backed device with 4 KiB erase units, no DFU and the littlefs 2 reader for fsload. The flash lives in a file named by ``MCUBOOT_UNIX_FLASH`` (a temporary file when it is not set). A reset raises ``SystemExit``. The variants' extra module ``mcuboot_fake`` lets a test read, write, tear and inspect the fake flash. ``test_module.py`` checks :class:`mcuboot.Writer`, :func:`mcuboot.state`, :func:`mcuboot.log`, the requests and the errors against the first variant. ``test_module_single.py`` checks the second: no secondary or scratch area, the fixed state, ``confirm()`` without a flash write, ``OSError(EPERM)`` from :func:`mcuboot.request_upgrade` and :class:`mcuboot.Writer`, and the requests of :func:`mcuboot.request_fsload` and :func:`mcuboot.request_dfu`.

Builds
~~~~~~

.. code-block:: bash

    make -C ports/stm32/mcuboot BOARD=NUCLEO_H563ZI
    make -C ports/stm32 BOARD=NUCLEO_H563ZI MCUBOOT=1
    make -C ports/stm32/mcuboot BOARD=PYBD_SF6
    make -C ports/stm32 BOARD=PYBD_SF6 MCUBOOT=1

The continuous integration also builds the NUCLEO_H563ZI bootloader with ``MCUBOOT_TEST_FI=1`` and installs the signed application into the simulated board of the bootloader host tests.

Hardware tests
~~~~~~~~~~~~~~

The hardware runs are on a NUCLEO_H563ZI in the configuration of its board definition (see the list of supported hardware at the top of this page). fsload from a FAT filesystem was run with a signed image placed on the application filesystem as ``/flash/fsapp.bin``: ``mcuboot.request_fsload('/fsapp.bin')`` made the bootloader install it as a test image, the swap ran, and a reset without ``mcuboot.confirm()`` reverted to the previous image.

Nothing of the STM32F7 port has run on a board. The hardware phase for the PYBD_SF6 is blocked (see ``planning/tickets/TICKET-019-stm32f7-hardware-bringup.md``); there are host tests and builds only.

The scripts in ``tests/mcuboot/hw``, ``tests/mcuboot/fi_sweep.py`` and ``tests/mcuboot/dfu_raw.py`` need a board and are not run by continuous integration. Scripts that program or read the board through the debug probe need ``pyocd``, always address the probe by its serial number and require ``--target`` (``stm32h563zitx`` for the NUCLEO_H563ZI). The scripts that reset the board inside a flash operation (``fi_sweep.py``, ``hw/tear_probe.py`` and ``hw/powercut.py``) take the address of the fault injection state from the bootloader ELF (``--elf``, the symbol ``mcuboot_req_start``) or from ``--fi-addr``. The scripts that talk to the DFU device need ``pyusb``.

.. list-table::
   :header-rows: 1
   :widths: 30 70

   * - Script
     - What it does and what it needs
   * - ``tests/mcuboot/fi_sweep.py``
     - Resets the board at every flash write or erase of a swap or a revert (before, after, or a number of microseconds into a program) and checks that the board converges to a valid image. Needs a bootloader built with ``MCUBOOT_TEST_FI=1`` (and its ELF), the board, its probe, the serial port of the bootloader log, and an old and a new signed image. ``--dry-run`` with ``--log`` prints the planned trials without a device.
   * - ``tests/mcuboot/hw/build_fiapp.py``
     - Builds and signs the small test application of the sweeps (``hw/fiapp``) with a chosen size in sectors. Needs ``arm-none-eabi-gcc``.
   * - ``tests/mcuboot/hw/tear_probe.py``
     - Tears chosen operations and reads the torn words over SWD to classify them as erased, programmed, corrected or ECC-invalid. Same needs as ``fi_sweep.py``.
   * - ``tests/mcuboot/hw/dfu_confine.py``
     - With the board in DFU mode, sends blocks and erase requests outside the update image area and checks over SWD, by hashing every flash area, that nothing outside it changed. Needs the board in DFU mode (``mcuboot.request_dfu()``) and ``pyusb``.
   * - ``tests/mcuboot/hw/powercut.py``
     - Cuts the supply of the board at random times through the power switch of a USB hub port. Needs a hub that switches the port that carries the board's debug probe, and refuses to cut unless the probe is on exactly the named port.
   * - ``tests/mcuboot/dfu_raw.py``
     - A raw DFU client (``list``, ``info``, ``status``, ``dnload``, ``erase``, ``result``, ``upload``) that sends blocks the standard tools never would.

.. _mcuboot_warnings:

Warnings: irreversible and destructive operations
--------------------------------------------------

.. warning::

   The items below can make a board unusable, remove protection that cannot be put back, or erase data with no way to recover it. Nothing in the build files, Makefile targets or code of this support does any of them, and the project's tests only ran on a board in the open product state with no protection enabled. Behaviour in other states is unknown.

**Option bytes, read-out protection, TrustZone, write protection and one-time programmable memory (STM32H5).** The bootloader and application run with TrustZone disabled (``TZEN=0``) on the non-secure register aliases. The flash driver reads the bank swap option bit to pick a bank, but the bootloader has not been run with the bank swap set, with TrustZone enabled, with any read-out protection level above 0, or with write protection or hide protection on any flash area. Changing option bytes (read-out protection, TrustZone enable, bank swap, boot configuration, write protection, hide protection, one-time programmable and option byte storage areas) can be permanent. Lowering read-out protection again may erase the flash, and the effect depends on the product state. Take the rules from the part's reference manual before using ``STM32_Programmer_CLI`` option byte commands, and do not do it on a board you need. The ``deploy-*`` targets use ``pyocd flash --erase sector`` and do not touch option bytes.

**Production keys cannot be replaced from the device.** The public key hashes are part of the bootloader image. DFU, fsload and the :mod:`mcuboot` module never write the boot area, so changing keys takes a new bootloader programmed with a programmer. If the bootloader area has been write protected through option bytes (this support does not do that), the keys cannot be changed without removing the protection, which is one of the operations above. If the private key is lost, no new image can be signed for a bootloader holding only its public key, and the device can only be updated by reprogramming the bootloader. The bootloader holds one public key, so a backup key cannot be added to the same bootloader.

**Test keys are public.** The keys in ``tests/mcuboot/keys`` are committed to the repository. A bootloader built without ``MCUBOOT_PRODUCTION=1`` that holds one of them accepts images signed by anyone. Use production mode for any shipped firmware.

**The security counter can go up and be reset.** Once an image with a higher counter has been confirmed, images with a lower counter are refused, so confirming a test image whose ``MCUBOOT_SECURITY_COUNTER`` is too high cannot be undone through an update. The counter lives in ordinary flash: erasing its area resets it to 0, and anything that can write flash can do that.

**The application can erase the bootloader.** The update paths are confined to the update slot, but the application is not. It has access to the flash controller, so code that erases flash (including Python code that reaches the registers) can erase the bootloader, update log, counter area and shadow words. This support does not write protect the bootloader area. A device whose bootloader is gone can only be revived with a programmer, and a chip erase through the programmer also erases the bootloader.

**The default layout reuses addresses of the default firmware.** The NUCLEO_H563ZI layout puts the update slot at ``0x08100000``, where the default firmware has its filesystem, and the application filesystem at ``0x080BC000``. Moving a board from the default firmware to an MCUboot installation, or back, leaves data where the other layout means something different, and the first update overwrites the old filesystem. Back up the files first. On the PYBD_SF6 the filesystem is on SPI flash #1 in both layouts, but the MCUboot bootloader replaces mboot at ``0x08000000`` and the slot at ``0x08040000`` overwrites the default application at ``0x08008000``. Read the flash back before programming a board that holds firmware you need.

**Flash wear.** Every update erases and writes the update slot. A swap erases and rewrites every sector of both slots, a revert does it again, and each trailer erase also erases the shadow sector. The update log and counter append records and erase a unit when a ring moves on. This tree does not state the STM32H5 flash endurance, so check the data sheet before using automatic or frequent updates.

**Fault injection builds.** ``MCUBOOT_TEST_FI=1`` adds a hook and a register level flash driver (``tests/mcuboot/hw/fi_flash.c``) that reset the device at a chosen flash operation when a 16 byte word at ``mcuboot_req_start + 0x3F0`` holds the key ``FINJ`` and a target. Anything that can write RAM can arm it. It is for test campaigns, not shipped firmware.

**Power cut tooling.** ``tests/mcuboot/hw/powercut.py`` removes the supply of the board's hub port while the board may be erasing or programming flash. That is the case the support is designed for, but it has no hardware evidence behind it yet (see above), and it can leave a board that needs reprogramming with a programmer. Use a board you can recover, not one that holds the only copy of something.
