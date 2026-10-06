.. _mcuboot_module:

:mod:`mcuboot` - MCUboot bootloader interface
=============================================

.. module:: mcuboot
   :synopsis: query and control the MCUboot bootloader

This module lets the running firmware query the `MCUboot <https://docs.mcuboot.com/>`_ bootloader that started it, confirm itself, install a new firmware image, ask the bootloader to enter its update mode, and read the bootloader's update log. The bootloader, the image format, the board layout and the build targets are covered in :ref:`mcuboot_bootloader`.

**Availability:**

* The stm32 port, when the firmware is built with ``MCUBOOT=1``. The bootloader, and with it this module, supports STM32H5 and STM32F7 boards that define the ``MCUBOOT_*`` settings in their ``mpconfigboard.h``. These are the NUCLEO_H563ZI (two slots, swap using offset) and the PYBD_SF6 (one slot, the ``single`` policy). A firmware built without ``MCUBOOT=1`` has no ``mcuboot`` module.

* The unix port, when it is built with ``VARIANT=mcuboot`` (the swap policy) or ``VARIANT=mcuboot_single`` (the single slot policy). These variants keep their flash in a file and exist for testing the module. There is no bootloader behind them, so the functions that reset the device raise ``SystemExit`` instead.

The module is enabled by the ``MICROPY_PY_MCUBOOT`` build option, which is off by default. The module works directly on the bootloader's on-flash structures and does not verify signatures or hashes. The bootloader checks every image before running it, so an image installed through this module is only judged at the next reset, not when it is written.

Concepts
--------

The flash holds two image slots. The *primary slot* contains the image the bootloader starts, which is the firmware that is running when this module is imported. The *secondary slot* (the update slot) receives new images. With the default slot policy of a board, ``swap``, and its default swap mode the update slot is one flash erase unit larger than the primary slot, and a new image is installed by swapping the contents of the two slots at the next reset (the swap modes ``move`` and ``scratch`` need slots of other sizes, see the reference page).

A swap is normally a *test* swap: the new image runs once, and unless it calls :func:`confirm` before the next reset the bootloader swaps the old image back (a *revert*). A *permanent* swap has no revert. An image that the firmware already runs and has confirmed is *confirmed*.

With the slot policy ``overwrite-external`` there is a secondary slot of the size of the primary slot, which may be on an external flash, and a new image is copied over the primary slot at the next reset. There is no test image and no revert: the new image is always permanent, there is nothing to confirm, and the *permanent* argument of :func:`request_upgrade` and :class:`Writer` makes no difference.

If the slot policy of the board is ``single`` (the PYBD_SF6), there is only one slot, updates are written to it directly, there is no swap and nothing to revert. In that case :func:`confirm` does nothing, :func:`state` reports a confirmed image with no pending swap, and :func:`request_upgrade` and :class:`Writer` raise ``OSError(EPERM)``. An update then comes from the bootloader (DFU or :func:`request_fsload`), and an update that is interrupted leaves no valid image: the bootloader never starts an image that fails its validation and enters its recovery mode instead.

Version tuples have the form ``(major, minor, revision, build)``. Addresses returned by this module (for example by :func:`slots`) are CPU addresses of the flash, which are also the addresses the bootloader's DFU interface uses.

Querying the state
------------------

.. function:: version(slot=0)

   Return the version of the image in *slot* as a tuple ``(major, minor, revision, build)``, or ``None`` if the slot holds no image header (the slot is erased, or the header is marked non-bootable).

   *slot* is ``0`` for the primary slot (the running image) or ``1`` for the update slot. The update slot is read at the place where an update image starts, which is one flash erase unit after the start of the slot with swap using offset, the default swap mode, and at the start of the slot otherwise. With the ``single`` slot policy ``version(1)`` returns ``None``. Any other value of *slot* raises ``ValueError``.

   This reads the header only. It does not tell whether the image is valid.

.. function:: bootloader_info()

   Return a dictionary describing the bootloader, or ``None`` if the end of the bootloader area holds no valid information block (for example when the firmware was programmed without MCUboot). The keys are:

   * ``'version'``: the bootloader version string (a ``str``, taken from the MicroPython version the bootloader was built from).
   * ``'layout_id'``: the layout identifier of the flash layout the bootloader was built for, as an ``int``.
   * ``'api'``: the version number of the interface between the bootloader and this module, as an ``int``.

.. function:: state()

   Return a dictionary with the state of the slots and of the next reset. The keys are:

   * ``'swap'``: what the bootloader will do at the next reset, one of the constants :data:`SWAP_NONE`, :data:`SWAP_TEST`, :data:`SWAP_PERM`, :data:`SWAP_REVERT` or :data:`SWAP_FAIL`.
   * ``'confirmed'``: ``True`` if the image in the primary slot (the running image) is confirmed.
   * ``'pending'``: ``True`` if the next reset swaps or reverts, that is if ``'swap'`` is :data:`SWAP_TEST`, :data:`SWAP_PERM` or :data:`SWAP_REVERT`.
   * ``'secondary_valid_header'``: ``True`` if the update slot holds an image header at the place where an update image starts.
   * ``'layout_mismatch'``: ``True`` if the layout identifier in the bootloader's information block differs from the one this firmware was built for. It is ``False`` when there is no information block. The bootloader rejects images built for a different layout, and :class:`Writer` and :func:`request_upgrade` refuse to run in that state.

   A test image that has booted and is not yet confirmed reports ``'swap'`` as :data:`SWAP_REVERT`, ``'pending'`` as ``True`` and ``'confirmed'`` as ``False``, because the next reset would revert it. An image that was programmed as the first image of a device, or that was confirmed, reports :data:`SWAP_NONE`, ``True`` and ``False``.

   With the ``single`` slot policy the result is always ``'swap'`` :data:`SWAP_NONE`, ``'confirmed'`` ``True`` and ``'pending'`` ``False``. Raises ``OSError(ENODEV)`` if the compiled-in flash layout does not match what the flash driver provides, and ``OSError(EIO)`` if a flash read fails.

.. function:: slots()

   Return a list of ``(name, start, size)`` tuples, one per flash area of the board's MCUboot layout. *name* is a string, one of ``'boot'``, ``'primary'``, ``'secondary'``, ``'scratch'``, ``'log'``, ``'seccnt'``, ``'fs'``, ``'intent'`` or ``'shadow'`` (only the areas the board has are listed: ``'secondary'`` is missing with the ``single`` slot policy, ``'scratch'`` needs the swap mode ``scratch``, ``'seccnt'`` the flash security counter, ``'intent'`` the ``single`` policy with fsload and ``'shadow'`` a flash with ECC). *start* is a CPU address and *size* is in bytes. The order of the list is not specified. The ``'secondary'`` area is the whole update slot, including the spare sector in front of the update image.

.. function:: log(n=None)

   Return the records of the bootloader's update log as a list, newest first. With *n* given, at most *n* records are returned; ``None`` returns all records that are in the log. A negative *n* raises ``ValueError``. Each record is a tuple::

       (seq, type, result, source, (major, minor, revision, build), detail)

   * *seq* is the sequence number of the record. It increases by one for each record.
   * *type* is a number that says what happened, *result* is a result code and *source* says who wrote the record (``0`` bootloader, ``1`` DFU, ``2`` fsload, ``3`` application). *detail* depends on the type. These numbers are listed in :ref:`mcuboot_update_log`; the module does not define named constants for them.
   * The version tuple is the version of the image the record is about, or ``(0, 0, 0, 0)`` if there is none.

   The log is stored in a flash area of two sectors which is used as a ring, so only the most recent records are kept.

Confirming and installing images
--------------------------------

.. function:: confirm()

   Mark the running image as confirmed, so that the bootloader does not revert it at the next reset. The call is idempotent. When it changes the state, an ``APP_CONFIRMED`` record is added to the update log. Raises ``OSError(EIO)`` if the flash cannot be written. Does nothing with the ``single`` slot policy.

   Call it once the application has decided that it works, for example after its self checks have passed. If the board is built with ``MCUBOOT_CONFIRM_AUTO``, the stm32 port confirms a test image itself after ``boot.py`` has finished, and calling this function is then only needed to confirm earlier or on other conditions.

.. function:: request_upgrade(permanent=False)

   Mark the image that is in the update slot as pending, so that the bootloader installs it at the next reset. The function does not reset the device. It does not check the signature of the image; the bootloader does that at the next reset, and if the image is rejected the current image keeps running.

   If *permanent* is false the new image is installed as a test image and reverts at the next reset unless it calls :func:`confirm`. If *permanent* is true the new image is installed without a revert.

   The update slot has to hold an image header at the place where an update image starts (see :func:`version`). Raises ``OSError(EINVAL)`` if it does not, ``OSError(ENODEV)`` on a layout mismatch (see :func:`state`) and ``OSError(EPERM)`` with the ``single`` slot policy. An ``APP_UPGRADE_REQUESTED`` record is added to the update log.

   This function is for images that were written to the update slot by other means. :class:`Writer` writes an image and marks it in one step.

.. class:: Writer(permanent=False)

   Open the update slot for writing a new signed image and return a writer object. Opening the writer erases the state of any earlier update (a pending update is cancelled) and the sector in front of the update image if the layout has one (swap using offset), so that no old image can be combined with the new data. The image data itself is erased sector by sector just before it is written.

   *permanent* is passed on to the final marking of the image, as in :func:`request_upgrade`.

   Only the most recently opened writer is usable. Opening a writer, even one whose opening fails, makes every older writer raise ``OSError(EBADF)``, because opening erases the sectors that all writers share.

   Opening raises ``OSError(EPERM)`` with the ``single`` slot policy, ``OSError(ENODEV)`` if the flash layout check fails or the layout identifier of the bootloader differs (see :func:`state`), and ``OSError(EIO)`` if a flash erase fails.

   .. method:: Writer.write(buf)

      Append the bytes in *buf*, which can be any object that supports the buffer protocol, to the image. Data is written sequentially from the start of the image. Return the number of bytes accepted, which is ``len(buf)``.

      The writer accepts at most as many bytes as the largest image the update slot can hold (for the NUCLEO_H563ZI layout this is 647168 bytes). A call that would go past that raises ``OSError(ENOSPC)`` and accepts nothing. A flash error raises ``OSError(EIO)`` and every later call on this writer except :meth:`abort` raises ``OSError(EIO)`` too. A writer that was finished or aborted raises ``OSError(EBADF)``.

      The writer collects data in a small buffer and writes it to flash in multiples of the flash write unit, so a call does not always write to flash and a call that does can take as long as a flash sector erase.

   .. method:: Writer.finish()

      Write out the buffered data, check that an image header is at the start of the image, and mark the image as pending (as :func:`request_upgrade` does, with the *permanent* choice of the writer). Return the total number of bytes written. The device keeps running the current image until the next reset.

      The method does not check the hash or the signature of the image and does not compare the size fields of the header with the number of bytes written. Raises ``OSError(EINVAL)`` if there is no image header, or if the image is not larger than one flash erase unit with swap using offset (the bootloader refuses such an update; what was written is erased and the writer is closed), ``OSError(EIO)`` on a flash error and ``OSError(EBADF)`` if the writer was already finished or aborted.

   .. method:: Writer.abort()

      Erase the first sector of the image so that the partly written image cannot be mistaken for a complete one. Nothing is pending after an abort. Calling it again after an abort is allowed and does nothing; calling it after :meth:`finish` raises ``OSError(EBADF)``.

   A writer is a context manager. Leaving the ``with`` block calls :meth:`finish`, or :meth:`abort` if an exception is propagating out of the block. A writer that the block already finished or aborted is left as it is::

       import mcuboot

       with mcuboot.Writer() as w:
           with open("/sd/firmware.signed.bin", "rb") as f:
               buf = bytearray(2048)
               while True:
                   n = f.readinto(buf)
                   if not n:
                       break
                   w.write(memoryview(buf)[:n])
       mcuboot.reset()

   The image has to be a signed image made for the board's flash layout; see :ref:`mcuboot_signing`. After the reset the bootloader validates it, swaps it in as a test image, and the new firmware has to call :func:`confirm` to keep it.

Resetting and entering the bootloader
-------------------------------------

.. function:: reset()

   Reset the device into a normal boot. Any pending request to the bootloader and the handoff word are cleared first, so the bootloader runs its usual boot decision. This function does not return.

.. function:: request_dfu()

   Reset the device into the bootloader's DFU (USB update) mode. An ``APP_DFU_REQUESTED`` record is added to the update log first. The bootloader leaves DFU mode on its own after a period without USB activity (``MCUBOOT_DFU_TIMEOUT_S`` of the board, 120 seconds by default), and resets into a normal boot. This function does not return.

   :func:`machine.bootloader` with no argument does the same on a build with ``MCUBOOT=1``.

.. function:: request_fsload(path, mount=None)

   Reset the device into the bootloader and ask it to install the signed image stored in the file *path*, read by the bootloader from a filesystem in flash. This function does not return. The bootloader validates the file completely before it writes anything to the slots, installs it as a test image and resets; see :ref:`mcuboot_fsload` for the details, including what happens when the file is not valid.

   *path* is a ``str`` or ``bytes`` object of 1 to 254 ASCII characters without a NUL byte, otherwise ``ValueError`` is raised. The bootloader applies stricter rules (printable ASCII only, no ``.`` or ``..`` components) and records a failure in the update log.

   *mount* says where the filesystem is. With ``None`` it is the application filesystem area of the board's flash layout (the area ``'fs'`` of :func:`slots`), and the filesystem type is FAT, or littlefs 2 on a board whose bootloader carries the littlefs 2 reader and not the FAT reader. Otherwise *mount* is a tuple or list of three to five integers::

       (fs_type, base, len)
       (fs_type, base, len, arg2)
       (fs_type, base, len, arg2, arg3)

   * *fs_type* is :data:`FS_FAT`, :data:`FS_LFS2` or :data:`FS_RAW`.
   * *base* and *len* are the CPU address and the size in bytes of the flash window that holds the filesystem. The window has to lie in one flash device and must not overlap any flash area of MCUboot other than the filesystem area.
   * *arg2* is the block size for :data:`FS_LFS2` (``0`` or absent means 4096). For :data:`FS_RAW` it is the address of a second flash window, and *arg3* is the length of that window; the file is then the first window followed by the second one.

   A malformed *mount* raises ``ValueError``. For :data:`FS_RAW` the file is the content of the window itself and there is no filesystem; *path* is ignored by the bootloader but still has to be given.

   The call does not check that the bootloader contains a reader for the filesystem type or that the file exists. Data that the application wrote to its filesystem can still be in the filesystem's RAM cache, so call ``os.sync()`` before requesting fsload.

Constants
---------

.. data:: SWAP_NONE
          SWAP_TEST
          SWAP_PERM
          SWAP_REVERT
          SWAP_FAIL

   The possible values of ``state()['swap']``, which are the values ``1`` to ``5`` of the swap types of the MCUboot library:

   * ``SWAP_NONE`` (1): the next reset starts the primary slot as it is.
   * ``SWAP_TEST`` (2): the next reset swaps in the update image as a test image.
   * ``SWAP_PERM`` (3): the next reset swaps in the update image permanently.
   * ``SWAP_REVERT`` (4): the next reset swaps back to the image that was replaced, because the running test image was not confirmed.
   * ``SWAP_FAIL`` (5): the image that would be run is not valid.

.. data:: FS_FAT
          FS_LFS2
          FS_RAW

   The filesystem types for the *mount* argument of :func:`request_fsload`: FAT (``1``), littlefs 2 (``3``) and a raw flash window (``4``). littlefs 1 is not offered.

Interaction with ``machine.bootloader()``
-----------------------------------------

On a firmware built with ``MCUBOOT=1``, :func:`machine.bootloader` requests the MCUboot bootloader instead of the ROM bootloader. Called without an argument, or with an argument that is not a ``bytes`` object, it behaves like :func:`request_dfu`. A ``bytes`` object is taken as a bootloader element stream describing a filesystem load. It is validated and passed to the bootloader, and ``ValueError`` is raised if it is malformed. :func:`request_fsload` builds such a stream from a path and a mount description, and is the usual way to request a filesystem load.

Errors
------

Besides the ``ValueError`` and ``TypeError`` from argument checks, the functions of this module raise ``OSError`` with these error numbers:

* ``EPERM``: the board uses the ``single`` slot policy and the call needs an update slot.
* ``EBADF``: a :class:`Writer` was used after it was finished or aborted, or after a newer writer was opened.
* ``ENODEV``: the flash layout check failed, the update slot has no room for an image, or the layout identifier of the bootloader differs from the one of the firmware.
* ``EINVAL``: the update slot holds no image header, or a :class:`Writer` was finished with an image of one erase unit or less with swap using offset.
* ``ENOSPC``: a write would go past the end of the update slot.
* ``EIO``: a flash read, erase or write failed, or any other failure.

See :ref:`mcuboot_bootloader` for building, signing, the DFU and fsload update flows, the update log format and the tests.
