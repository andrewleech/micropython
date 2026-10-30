.. _mboot_bootloader:

Mboot bootloader support
========================

Mboot is a small bootloader which starts MicroPython and installs signed firmware updates, using MCUboot for image validation and installation. It checks that an application was signed by a trusted key before starting it, and can accept updates over USB DFU or load them from storage without requiring a debug probe. Missing or invalid firmware enters recovery instead of being started.

The board configuration determines how updates are stored and installed. A two-slot swap layout can test a new application and restore the previous image if it is not confirmed. Overwrite layouts need less storage or can stage an update in external flash, but cannot restore the previous image. Downgrade protection can reject releases older than the permitted security counter or version.

The boot and update core is shared between ports. Port integration is currently implemented for STM32H5 and STM32F7, with NUCLEO_H563ZI (two slots with swap) and PYBD_SF6 (STM32F767, one slot with the update image in SPI flash) as the configured boards. Boards need a matching bootloader/application configuration and suitable flash space. This page describes the MCUboot-backed implementation, not the legacy STM32 mboot build in ``ports/stm32/mboot``.

Using the bootloader
--------------------

Building and signing
~~~~~~~~~~~~~~~~~~~~

Build steps
^^^^^^^^^^^

Build the bootloader and application for the same board configuration. Their image slots, reserved flash areas and signing key must agree. The STM32 builds need the usual Arm toolchain and port dependencies, plus MCUboot and its Python signing dependencies:

    git submodule update --init lib/mcuboot lib/stm32lib lib/CMSIS_6 lib/tinyusb
    python3 -m pip install cryptography intelhex click cbor2 pyyaml
    make -C mpy-cross

Run these commands from the repository root, replacing ``<board>`` with a board configured for Mboot:

    make -C ports/stm32 -f Makefile.mboot BOARD=<board>
    make -C ports/stm32 BOARD=<board>
    make -C ports/stm32 BOARD=<board> initial

The bootloader build produces ``firmware.elf``, ``firmware.bin`` and ``firmware.hex`` in ``build-<board>-mboot``. The application build, in ``build-<board>``, produces ``firmware.signed.bin`` for updates. The ``initial`` target produces ``firmware.initial.bin`` and ``firmware.initial.hex``, including the state needed to boot the initial application without treating it as an unconfirmed update. The unsigned application binary is not an installable update, and the stock ``deploy``, ``deploy-stlink``, ``deploy-openocd`` and ``firmware.dfu`` targets of the port write that unsigned binary, so they are not used on these boards. Add ``MICROPY_PY_MBOOT=1`` to the application build to include the optional ``mboot`` Python module.

Signing keys
^^^^^^^^^^^^

Set ``MBOOT_PUBKEY=<public-key.pem>`` for both builds and ``MBOOT_SIGN_KEY=<private-key.pem>`` for the application build. These must be a matching ECDSA P-256 key pair. The signing step rejects a private key that does not match ``MBOOT_PUBKEY``. Keep the private key outside the device and repository. The default builds use a public test key included in the repository, which is unsuitable for a product because anyone can sign an image with it. Set ``MBOOT_PRODUCTION=1`` for production builds; this rejects the supplied test keys and configurations that skip primary-image validation.

The application targets ``sign-digest`` and ``sign-apply SIG=<signature-file>`` support signing on a separate machine. ``sign-digest`` writes ``firmware.digest``; ``sign-apply`` applies the signature and writes ``firmware.signed.bin``. Use ``MBOOT_SIGN_PUBKEY`` to select the public key for checking an externally supplied signature.

MIMXRT opt-in builds
~~~~~~~~~~~~~~~~~~~~

Mboot build support is also available for ``SEEED_ARCH_MIX`` (RT1052) and ``MIMXRT1170_EVK`` (RT1176 M7). It is opt-in and leaves ordinary board builds on their existing boot paths. Initialize the mimxrt port dependencies and signing tools as described by the port build instructions, then build the bootloader and signed application for the same board::

    make -C ports/mimxrt -f Makefile.mboot BOARD=SEEED_ARCH_MIX MBOOT_PUBKEY=<public-key.pem>
    make -C ports/mimxrt BOARD=SEEED_ARCH_MIX MBOOT=1 MBOOT_ROMFS=<romfs-image> MBOOT_PUBKEY=<public-key.pem> MBOOT_SIGN_KEY=<private-key.pem>

Replace ``SEEED_ARCH_MIX`` with ``MIMXRT1170_EVK`` for the RT1176 M7 build. ``MBOOT_ROMFS`` must name a valid ROMFS image no larger than the board's 512 KiB reserved size. The application build pads this image and links it into the signed application payload. Rebuild and sign the application whenever its ROMFS contents change; do not write a replacement ROMFS directly into a running Mboot application.

All ARCH-MIX build roles use a 498 MHz core profile with a 1.2 V supply target for the fitted DVL5B device and 100 MHz FlexSPI for the IS25WP064A's six-dummy-cycle ``EBh`` reads, rated up to 104 MHz.

Standalone MIMXRT Mboot also supports optional board LEDs. In a board's ``mpconfigboard.h``, define contiguous ``MBOOT_LED1_*`` through ``MBOOT_LED3_*`` GPIO descriptors for the LEDs it has: each descriptor supplies ``GPIO``, ``PIN``, ``MUX``, ``PAD_CONFIG`` and ``ACTIVE_LOW`` values. Slot 1 maps to alive and blinks at 50 ms intervals, slot 2 maps to USB active and slot 3 maps to error. Leave the descriptors undefined on boards without LEDs. These settings are used by the standalone Mboot build and do not use Python pin objects. The ARCH-MIX LED mapping and pad settings are described in ``ports/mimxrt/boards/SEEED_ARCH_MIX/deploy.md``.

For first-image packaging, run the application ``initial`` target with the same board, ROMFS image and signing key arguments. Do not program the ordinary unsigned ``firmware.bin`` or ``firmware.hex`` as an Mboot application. Before programming ``firmware.initial.hex`` or ``firmware.initial.bin``, make a complete flash backup, verify that the board can be restored through an independent recovery path, and review the image, map and programmer commands against the board's flash layout. The build commands alone are not an installation or restore procedure. MIMXRT storage loading and layout-mismatch rejection lack hardware validation; the M4 core is not supported by this integration.

The current map reserves 256 KiB for the bootloader, a 2 MiB primary slot, a 2 MiB plus 4 KiB offset-swap secondary slot, and 8 KiB for the security counter. The application filesystem begins at flash offset ``0x443000``. On ARCH-MIX the ordinary VFS starts at flash offset ``0x200000`` and overlaps the Mboot primary slot, secondary slot and security-counter area. Back up any needed files before an approved layout migration; the Mboot VFS starts after these regions. On the EVK the map leaves the final 1 MiB of its configured 16 MiB flash window reserved. These are Mboot-only settings.

The primary slot contains the MCUboot header, the complete signed application including padded ROMFS, validation metadata and the swap trailer. Signing limits the image to ``0x1fe000`` bytes, leaving two 4 KiB sectors for the trailer. The NXP FCB, IVT and boot data belong only to the bootloader image.

MIMXRT flash erase and program operations are blocking and mask interrupts while the flash is unavailable for instruction fetch. Each NOR-ready wait is limited to 10 seconds, each controller transfer to 100 ms, and controller reset and eDMA quiescence to 10 ms each; these are individual wait limits, not a total update deadline. The driver pauses eDMA during an operation and restores its configuration only when flash access is safe, so continuous DMA streams must tolerate this pause. Ethernet may remain enabled only with the port's fixed SRAM descriptor rings and buffers. Other enabled Ethernet rings or a released M4 core prevent writes. Unsafe bus-master state or a quiescence timeout returns ``EBUSY``.

After a failed flash-status transfer, the driver retains the first error and performs bounded controller recovery and retries within the same 10-second readiness deadline. It returns ``EIO`` through Mboot only after confirming that the NOR is idle and the controller can safely resume instruction fetch. If that deadline expires or controller recovery fails, RAM-resident code resets the device through the port reset path without restoring interrupts, caches or DMA into unsafe flash access. Application flash waits do not feed ``machine.WDT``; auxiliary and bootloader watchdog service remains limited by these wait bounds.

On the RT1170 EVK, resetting during early bootloader initialisation can leave the ROM stopped without USB enumeration. A full power cycle is required to recover from this state; SWD programming remains available. Do not rely on a software reset as the only recovery path. Physical double-reset entry on this board lacks hardware validation.

For bootloader debugging, ``MICROPY_MBOOT_DIAGNOSTIC_WAIT=1`` enables an indefinite USB DFU gate after flash initialisation and layout checks, before flash scrubbing, security-counter initialisation, swap processing or application boot. It is disabled by default and should stay disabled in release builds. Enable it by passing ``CFLAGS_EXTRA=-DMICROPY_MBOOT_DIAGNOSTIC_WAIT=1`` to the bootloader make command.

USB enumeration and ordinary USB resets do not release this gate. To continue boot processing without resetting the chip, send a vendor/interface USB control transfer to the DFU interface with ``bmRequestType=0x41``, ``bRequest=0x82``, ``wValue=0``, ``wIndex=0`` and no payload. A successful DFU download followed by its USB reset also releases the gate. Normal boot policy can still select recovery if the image or update state is not bootable. Every chip reset returns to the diagnostic gate.

In swap layouts, the diagnostic gate rejects downloads and other destructive DFU operations while an update or revert is pending, a test image is unconfirmed, a swap is interrupted, or either slot's trailer cannot be read. This protects the rollback image. Non-destructive diagnostics and the continue request remain available. Continue normal boot processing to settle a pending swap or revert, and confirm a running test image before staging another update.

Initial installation
~~~~~~~~~~~~~~~~~~~~

Use a programmer to write both the bootloader and initial application image to their configured flash addresses. On STM32, run ``deploy-bootloader`` in the bootloader build and ``deploy-initial`` in the application build. Both use pyocd and require ``PROBE=<probe-serial-number>`` to identify the probe; there is no default. The pyocd target comes from ``PYOCD_TARGET`` in the board's ``mpconfigboard.mk``. NUCLEO_H563ZI sets this value; PYBD_SF6 does not, so pass ``PYOCD_TARGET=<target>`` for that board.

Mboot does not update itself. To replace the bootloader or change the flash layout, program the device rather than using an application update.

Entering recovery
~~~~~~~~~~~~~~~~~

After installation, enter the bootloader with ``machine.bootloader()`` or reset twice in quick succession if the board defines ``MICROPY_MBOOT_DOUBLE_TAP``. On STM32, hold the user button (``MICROPY_HW_USRSW_PIN``) at reset. An MIMXRT board can force entry at reset by defining ``MICROPY_HW_MBOOT_ENTRY_FORCED()``; no MIMXRT board defines this hook yet.

USB update
~~~~~~~~~~

Mboot implements plain DFU 1.1 over USB. Download a signed image to alternate setting 0 with ``dfu-util -a 0 -D firmware.signed.bin``. The application ``dfu`` target also packages the image as ``firmware.signed.dfu`` for ``tools/pydfu.py``. The USB update path writes to the location selected by the board layout, not to an arbitrary application address.

A DFU session entered with ``machine.bootloader()`` or the user button resets into a normal boot after ``MICROPY_MBOOT_DFU_TIMEOUT_S`` seconds (120 by default) without USB activity. Recovery from a missing or invalid image waits for an update.

Filesystem update
~~~~~~~~~~~~~~~~~

Filesystem loading is an alternative for applications that can first download an update to storage. The application requests a bootloader update from a file on a supported filesystem or from a raw flash window. The bootloader validates the signed image before installing it. A board can enable gzip-compressed update files.

With a swap layout, an update normally boots as a test image. The application must confirm it after its startup checks succeed. Otherwise, the bootloader restores the previous image on the next reset. A board can enable automatic confirmation after ``boot.py``, but this does not check whether the rest of the application is healthy. Overwrite and single-slot layouts do not support this test-and-revert cycle.

Security and recovery
~~~~~~~~~~~~~~~~~~~~~

Signing authenticates an image but does not encrypt it. Protecting the bootloader and its trusted key from application writes, and restricting debug access, are separate device provisioning requirements. The STM32H5 bootloader runs with TrustZone disabled. The image version comes from the MicroPython version of the build, with build number 0.

A monotonic security counter prevents installation of images below the accepted counter. Set ``MICROPY_MBOOT_SECURITY_COUNTER`` for each signed release and keep it consistent with the release process. Counter-based rollback protection is required for single-slot installations, which cannot compare an incoming image with a previous image after overwriting the slot. Other layouts can use version-based downgrade checks instead.

Recovery from a missing or invalid image uses the USB DFU front end, so ``MICROPY_MBOOT_DFU`` must be enabled (it is enabled by default). Without it, the bootloader shows its error LED and waits for a reset or programmer. A replacement signed application can then be installed over DFU. Layouts without revert support cannot recover an overwritten previous application. Power-loss handling also depends on the selected layout and whether update storage remains available.

Port integration
----------------

A port supplies flash access, boot entry and hardware lifecycle hooks through ``mboot_port.h``. It must initialise the bootloader's clocks and peripherals, service any watchdog, preserve update requests across reset, and leave the hardware in a suitable state before starting the application. Flash operations must obey the device's erase/program constraints and handle cache coherency and flash ECC where applicable.

The STM32 port (``ports/stm32/mboot_port.c`` and ``mboot_boot.c``) builds this on the services of the application: ``SystemClock_Config()`` with the board's ``MICROPY_HW_CLK_*`` values, the HAL tick, the pin objects, ``uart.c``, the USB device driver and ``flash.c``. A board therefore needs no bootloader-specific clock, pin or USB settings. The bootloader does read ``MICROPY_HW_LED1`` to ``MICROPY_HW_LED3`` with ``MICROPY_HW_LED_ON/OFF``, ``MICROPY_HW_USRSW_PIN``, ``_PULL`` and ``_PRESSED``, and ``MICROPY_HW_UART_REPL`` with ``_BAUD`` for its log, so the board file has to define them.

The port's ``mboot_dev.h`` describes each flash device using ``MICROPY_HW_MBOOT_DEV0_*`` and, when present, ``MICROPY_HW_MBOOT_DEV1_*`` settings. These cover the address range, program unit, erase geometry, erased value, memory mapping and ECC behaviour. Non-uniform flash is described as runs of equal erase units. The port also supplies bootloader-size defaults, vector-table alignment and RAM/linker information.

USB DFU requires a USB device-controller implementation and its interrupt handling. Storage updates require access to the relevant flash device. A port can provide a hardware-backed monotonic counter through the security-counter hooks instead of storing it in flash. The shared headers document the full interface and device-geometry settings; these are port implementation details, not options an end user needs to set for each build.

Board configuration
-------------------

A board's ``mpconfigboard.h`` selects the image layout and enabled features. Hardware placement uses ``MICROPY_HW_MBOOT_*`` defines; shared bootloader options use ``MICROPY_MBOOT_*``. The bootloader, application linker scripts and signing tools derive their layout from these same settings, so addresses and reserved sizes must not be maintained separately.

Update mode and flash layout
~~~~~~~~~~~~~~~~~~~~~~~~~~~~

``MICROPY_MBOOT_UPDATE_MODE`` selects the installation structure:

* ``MICROPY_MBOOT_UPDATE_MODE_SWAP`` uses primary and secondary slots, with test boot, confirmation and automatic revert. This is the default.
* ``MICROPY_MBOOT_UPDATE_MODE_OVERWRITE`` copies an update from a secondary slot over the primary slot, with no revert. The secondary slot can be in external flash.
* ``MICROPY_MBOOT_UPDATE_MODE_SINGLE_SLOT`` overwrites the primary slot in place. It uses less flash but retains no previous image and requires counter-based rollback protection.

For swap installations, ``MICROPY_MBOOT_SWAP_MODE`` selects ``MICROPY_MBOOT_SWAP_MODE_OFFSET`` (the default), ``MICROPY_MBOOT_SWAP_MODE_MOVE`` or ``MICROPY_MBOOT_SWAP_MODE_SCRATCH``. Offset requires a secondary slot one erase unit larger than the primary slot; move uses one erase unit less. Scratch swapping needs a separate erase area. Overwrite installations use equally sized slots.

Set ``MICROPY_HW_MBOOT_PRIMARY_SIZE`` and, for two-slot layouts, ``MICROPY_HW_MBOOT_SECONDARY_ADDR``. ``MICROPY_HW_MBOOT_PRIMARY_ADDR`` defaults to immediately after the bootloader region. The board can override bootloader space and placement of auxiliary areas, and set ``MICROPY_HW_MBOOT_FS_ADDR`` / ``MICROPY_HW_MBOOT_FS_SIZE`` for the application filesystem.

A board with a second flash device defines ``MICROPY_HW_MBOOT_SPIFLASH_ADDR``, ``MICROPY_HW_MBOOT_SPIFLASH_BYTE_SIZE``, ``MICROPY_HW_MBOOT_SPIFLASH_ERASE_BLOCKS_PER_PAGE`` and ``MICROPY_HW_MBOOT_SPIFLASH_SPIFLASH`` (the spiflash object), which ``mboot_dev.h`` turns into the ``MICROPY_HW_MBOOT_DEV1_*`` settings. If the chip is detected at run time the board must also define ``MICROPY_HW_MBOOT_DEV1_SIZE``. A single-slot board that supports filesystem loading needs an intent area (``MICROPY_HW_MBOOT_INTENT_ADDR``, one erase unit), and the security counter is stored in flash (``MICROPY_HW_MBOOT_SECCNT_ADDR``) unless ``MICROPY_MBOOT_SECCNT_PORT`` is set.

Reserve enough flash for the image header, validation metadata and update state, as well as any security-counter, scratch or recovery areas required by the configuration. Each area must start on an erase boundary, contain whole erase units and not overlap another area. An external update slot must satisfy the selected algorithm's erase/program geometry. ``shared/mboot/include/mboot_layout.h`` documents the exact constraints and defaults; the layout generator checks them before building or signing.

Optional features
~~~~~~~~~~~~~~~~~

Set ``MICROPY_MBOOT_DFU`` to enable USB DFU. The board can customise the bootloader's USB identifiers/product string and requested-recovery timeout. ``MICROPY_MBOOT_FSLOAD_FAT``, ``MICROPY_MBOOT_FSLOAD_LFS2`` and ``MICROPY_MBOOT_FSLOAD_RAW`` enable storage update sources; ``MICROPY_MBOOT_FSLOAD_GZIP`` enables compressed input. Only enable features supported by the port and board storage.

Rollback protection and confirmation
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Every board defines ``MICROPY_MBOOT_ROLLBACK_COUNTER``: ``1`` keeps a monotonic security counter and ``0`` uses a version check instead. ``MICROPY_MBOOT_SECCNT_PORT`` selects the port's counter implementation rather than flash storage. When ``MICROPY_MBOOT_ROLLBACK_COUNTER`` is ``0``, ``MICROPY_MBOOT_VERSION_CHECK`` controls version-based downgrade checks. Do not set it when the counter is ``1``. ``MICROPY_MBOOT_CONFIRM_AUTO`` enables automatic confirmation after ``boot.py`` for swap installations.

Validation and diagnostics
~~~~~~~~~~~~~~~~~~~~~~~~~~

The board can select the cryptographic implementation, fault-injection hardening level and logging level. Signature validation of the primary image is enabled by default and should remain enabled in a product. ``mboot_layout.h`` documents these settings, their dependencies and defaults.

``MICROPY_MBOOT_DOUBLE_TAP`` enters recovery when the board is reset a second time within ``MICROPY_MBOOT_DOUBLE_TAP_MS`` (350 ms by default) of the bootloader starting, as the UF2 bootloaders do. The bootloader leaves ``MICROPY_MBOOT_DOUBLE_TAP_MAGIC`` in a word that survives reset, supplied by the port through ``mboot_port_double_tap_word()``, and clears it when the window ends. Every boot that is not already going to recovery therefore waits for the whole window. The word is separate from the request and retention words, so taking a request does not touch it. On MIMXRT, it is ``SNVS->LPGPR[3]``, also used by TinyUF2. Set ``MICROPY_HW_SNVS_LPGPR_SAFE_COUNT`` so ``machine.mem_backup()`` does not use this word.
