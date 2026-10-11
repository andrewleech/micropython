.. _mboot_bootloader:

Mboot bootloader support
========================

Mboot provides signed firmware boot and update support using the MCUboot
image-validation and swap engine. A board definition selects the flash layout
and update features; the port supplies flash access and bootloader entry.
Port integration is currently implemented for STM32.

Image layouts
-------------

``MBOOT_POLICY`` selects how firmware is installed:

* ``MBOOT_POLICY_SEL_SWAP`` uses primary and secondary image slots. An update
  exchanges the two images and boots as a test image, reverting on the next
  reset unless the application confirms it.
* ``MBOOT_POLICY_SEL_OVERWRITE_EXTERNAL`` copies an update from the secondary
  slot over the primary slot. The secondary slot may be on external flash.
  There is no automatic revert.
* ``MBOOT_POLICY_SEL_SINGLE`` overwrites one image slot in place. This needs
  less flash, but there is no previous image to restore if an update fails.

For swap layouts, ``MBOOT_SWAP_MODE`` selects offset, move or scratch swapping
with the corresponding ``MBOOT_SWAP_MODE_SEL_*`` constant. Offset and move
reserve space within the slots; scratch uses a separate erase area.

The board's ``mpconfigboard.h`` sets slot addresses and sizes with
``MBOOT_PRIMARY_ADDR``, ``MBOOT_PRIMARY_SIZE`` and, for two-slot layouts,
``MBOOT_SECONDARY_ADDR``.
The port describes each flash device's size, erase/program units and ECC
behaviour. The layout reserves space for the bootloader, image headers,
update state and rollback counter, with any required scratch, intent or ECC
shadow areas. Slots and filesystem areas must not overlap these regions.
The derived layout is shared by the linker, signing tools and bootloader.

Update features
---------------

Boards can enable USB DFU with ``MBOOT_DFU`` and filesystem loading with
``MBOOT_FSLOAD_FAT``, ``MBOOT_FSLOAD_LFS2`` or ``MBOOT_FSLOAD_RAW``.
``MBOOT_FSLOAD_GZIP`` enables compressed update files. These features depend
on the port providing the required USB and storage access.

An invalid or missing application enters recovery rather than being started.
With DFU enabled, recovery accepts a signed firmware image over USB.
Filesystem loading installs a signed image from a file or raw flash window
after an application requests the update. Single-slot layouts cannot revert
to an earlier image.

Building and signing
--------------------

Build the bootloader and application with the same board layout. Initial
installation requires programming the bootloader and a signed application;
later updates can use the enabled DFU or filesystem-loading path.

Set ``MBOOT_PUBKEY`` for the bootloader and use the matching ``MBOOT_SIGN_KEY``
to sign application images. ``MBOOT_PRODUCTION=1`` refuses the public test
keys supplied in the repository. Those keys are only suitable for testing.

``MBOOT_ROLLBACK_COUNTER=1`` enables a monotonic security counter, stored in
flash or provided by the port. Set ``MBOOT_SECURITY_COUNTER`` for each signed
release. This is required for single-slot layouts; disabling the counter
uses version-based downgrade checks instead.
