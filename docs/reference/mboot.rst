.. _mboot_bootloader:

Mboot bootloader support
========================

Mboot is a small bootloader which starts MicroPython and installs signed firmware updates, using MCUboot for image validation and installation. It checks that an application was signed by a trusted key before starting it, and can accept updates over USB DFU or load them from storage without requiring a debug probe. Missing or invalid firmware enters recovery instead of being started.

The board configuration determines how updates are stored and installed. A two-slot swap layout can test a new application and restore the previous image if it is not confirmed. Overwrite layouts need less storage or can stage an update in external flash, but cannot restore the previous image. Downgrade protection can reject releases older than the permitted security counter or version.

The boot and update core is shared between ports. Port integration is currently implemented for STM32; boards need a matching bootloader/application configuration and suitable flash space. This page describes the MCUboot-backed implementation, not the legacy STM32 mboot build.

Using the bootloader
--------------------

Building and signing
~~~~~~~~~~~~~~~~~~~~

Build the bootloader and application for the same board configuration. Their image slots, reserved flash areas and signing key must agree. The STM32 builds need the usual Arm toolchain and port dependencies, plus MCUboot and its Python signing dependencies::

    git submodule update --init lib/mcuboot lib/stm32lib lib/CMSIS_6 lib/tinyusb
    python3 -m pip install cryptography intelhex click cbor2 pyyaml
    make -C mpy-cross

For the current STM32 integration, the following commands run from the repository root, with ``<board>`` replaced by a board configured for this bootloader::

    make -C ports/stm32 -f Makefile.mboot BOARD=<board>
    make -C ports/stm32 BOARD=<board>
    make -C ports/stm32 BOARD=<board> initial

The bootloader build produces ``firmware.bin`` and ``firmware.hex`` in its build directory. The application build produces ``firmware.signed.bin`` for updates. The ``initial`` target produces ``firmware.initial.bin`` and ``firmware.initial.hex``, including the state needed to boot the initial application without treating it as an unconfirmed update. The unsigned application binary is not an installable update.

Set ``MBOOT_PUBKEY=<public-key.pem>`` when building the bootloader and ``MBOOT_SIGN_KEY=<private-key.pem>`` when building the application, using a matching ECDSA P-256 key pair. Keep the private key outside the device and repository. The default builds use the public test key supplied in the repository, which anyone can use to sign an image and is unsuitable for a product. Use ``MBOOT_PRODUCTION=1`` for production builds; it refuses the supplied test keys and configurations which skip primary-image validation.

The application targets ``sign-digest`` and ``sign-apply SIG=<signature-file>`` support signing outside the build machine. The former writes ``firmware.digest``; the latter applies the signature to produce ``firmware.signed.bin``. ``MBOOT_SIGN_PUBKEY`` selects the public key used to check an externally supplied signature.

Initial installation and updates
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Initial installation requires a programmer to write both the bootloader and the initial application image to their configured flash addresses. On STM32, ``deploy-bootloader`` in the bootloader build and ``deploy-initial`` in the application build use pyocd, with ``PROBE=<probe-serial-number>`` required to identify the probe. Replacing the bootloader or changing the flash layout also requires programming rather than an ordinary application update.

After installation, enter the bootloader using the board's boot-entry control or ``machine.bootloader()``. With USB DFU enabled, the ``dfu`` application target packages the signed image as ``firmware.signed.dfu`` for ``tools/pydfu.py`` or dfu-util. The USB update path writes the image to the location selected by the board layout, not to an arbitrary application address.

Filesystem loading is an alternative for applications which can first download an update to storage. The application requests a bootloader update from a file on a supported filesystem, or from a raw flash window. The bootloader validates the signed image before installing it. Gzip-compressed update files can be enabled by the board.

With a swap layout, an update normally boots as a test image. The application must confirm it once its startup checks succeed, otherwise the bootloader restores the previous image on the next reset. A board can enable automatic confirmation after ``boot.py``, but that does not check whether the rest of the application is healthy. Overwrite and single-slot layouts do not provide this test/revert cycle.

Security and recovery
~~~~~~~~~~~~~~~~~~~~~

Signing authenticates an image; it does not encrypt it. Protecting the bootloader and its trusted key from application writes, and restricting debug access, are separate device provisioning requirements.

A monotonic security counter prevents installation of images below the accepted counter. Set ``MICROPY_MBOOT_SECURITY_COUNTER`` for each signed release, and keep it consistent with the release process. Counter-based rollback protection is required for single-slot installations, which cannot compare an incoming image with a previous image after that slot has been overwritten. Other layouts can use version-based downgrade checks instead.

Recovery can accept a replacement signed application through the board's enabled update paths. It cannot recover an overwritten previous application in a layout without revert support. Power-loss handling also depends on the selected layout and the storage remaining available for the update.

Port integration
----------------

A port supplies flash access, boot entry and hardware lifecycle hooks through ``mboot_port.h``. It must initialise the bootloader's clocks and peripherals, service any watchdog, preserve update requests across reset, and leave the hardware in a suitable state before starting the application. Flash operations must obey the device's erase/program constraints and handle cache coherency and flash ECC where applicable.

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

Reserve enough flash for the image header, validation metadata and update state, as well as any security-counter, scratch or recovery areas required by the configuration. Each area must start on an erase boundary, contain whole erase units and not overlap another area. An external update slot must satisfy the selected algorithm's erase/program geometry. ``shared/mboot/include/mboot_layout.h`` documents the exact constraints and defaults; the layout generator checks them before building or signing.

Optional features
~~~~~~~~~~~~~~~~~

``MICROPY_MBOOT_DFU`` enables USB DFU. The board can customise the bootloader's USB identifiers/product string and requested-recovery timeout. ``MICROPY_MBOOT_FSLOAD_FAT``, ``MICROPY_MBOOT_FSLOAD_LFS2`` and ``MICROPY_MBOOT_FSLOAD_RAW`` enable storage update sources, with ``MICROPY_MBOOT_FSLOAD_GZIP`` enabling compressed input. Only enable features supported by the port and the board's storage.

``MICROPY_MBOOT_ROLLBACK_COUNTER`` selects counter-based downgrade protection. ``MICROPY_MBOOT_SECCNT_PORT`` selects the port's counter implementation rather than flash storage. Without a counter, ``MICROPY_MBOOT_VERSION_CHECK`` controls version-based downgrade checks. ``MICROPY_MBOOT_CONFIRM_AUTO`` enables automatic confirmation after ``boot.py`` for swap installations.

The board can also select the cryptographic implementation, fault-injection hardening level and logging level. Signature validation of the primary image is enabled by default and should remain enabled in a product. These settings and their dependencies are documented alongside their defaults in ``mboot_layout.h``.
