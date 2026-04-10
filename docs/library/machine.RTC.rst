.. currentmodule:: machine
.. _machine.RTC:

class RTC -- real time clock
============================

The RTC is an independent clock that keeps track of the date
and time.

Example usage::

    rtc = machine.RTC()
    rtc.datetime((2020, 1, 21, 2, 10, 32, 36, 0))
    print(rtc.datetime())


Constructors
------------

.. class:: RTC(id=0, ...)

   Create an RTC object. See init for parameters of initialization.

Methods
-------

.. method:: RTC.datetime([datetimetuple])

   Get or set the date and time of the RTC.

   With no arguments, this method returns an 8-tuple with the current
   date and time.  With 1 argument (being an 8-tuple) it sets the date
   and time.

   The 8-tuple has the following format:

       (year, month, day, weekday, hours, minutes, seconds, subseconds)

   The meaning of the ``subseconds`` field is hardware dependent.

.. method:: RTC.init(datetime)

   Initialise the RTC. Datetime is a tuple of the form:

      ``(year, month, day, hour, minute, second, microsecond, tzinfo)``

   All eight arguments must be present. The ``microsecond`` and ``tzinfo``
   values are currently ignored but might be used in the future.

   Availability: CC3200, ESP32, MIMXRT, SAMD. The rtc.init() method on
   the stm32 and renesas-ra ports just (re-)starts the RTC and does not
   accept arguments.

.. method:: RTC.now()

   Get get the current datetime tuple.

   Availability: WiPy.

.. method:: RTC.deinit()

   Resets the RTC to the time of January 1, 2015 and starts running it again.

.. method:: RTC.alarm(id, time, *, repeat=False)

   Set the RTC alarm. Time might be either a millisecond value to program the alarm to
   current time + time_in_ms in the future, or a datetimetuple. If the time passed is in
   milliseconds, repeat can be set to ``True`` to make the alarm periodic.

.. method:: RTC.alarm_left(alarm_id=0)

   Get the number of milliseconds left before the alarm expires.

.. method:: RTC.alarm_cancel(alarm_id=0)

   Cancel a running alarm.

   The mimxrt port also exposes this function as ``RTC.cancel(alarm_id=0)``, but this is
   scheduled to be removed in MicroPython 2.0.

.. method:: RTC.irq(*, trigger, handler=None, wake=machine.IDLE)

   Create an irq object triggered by a real time clock alarm.

      - ``trigger`` must be ``RTC.ALARM0``
      - ``handler`` is the function to be called when the callback is triggered.
      - ``wake`` specifies the sleep mode from where this interrupt can wake
        up the system.

.. method:: RTC.memory([data])

   ``RTC.memory(data)`` will write *data* to the RTC memory, where *data* is any
   object which supports the buffer protocol (including `bytes`, `bytearray`,
   `memoryview` and `array.array`). ``RTC.memory()`` reads RTC memory. On
   alif, mimxrt, rp2, and stm32 it returns a writable ``memoryview`` of
   32-bit unsigned integers backed directly by the hardware registers or
   memory. On esp32 and esp8266 it returns a ``bytes`` copy.

   Data written to RTC user memory is persistent across restarts, including
   :ref:`soft_reset` and `machine.deepsleep()`, except on rp2 where data
   is lost on power-off (see note below).

   On alif, mimxrt, rp2, and stm32, only the registers covered by the data
   are modified when writing; registers beyond the data length are left
   untouched. A partial trailing word is read-modify-written to preserve the
   remaining bytes.

   The maximum length depends on the port and hardware:

   =======  ======================================  ============  ==============
   Port     Backing storage                         Size          Battery-backed
   =======  ======================================  ============  ==============
   alif     Backup SRAM                             4096 bytes    yes           
   esp32    RTC slow memory                         2048 bytes    yes           
   esp8266  RTC user memory                         492 bytes     yes           
   mimxrt   SNVS LPGPR registers (4-8 per chip)     16-32 bytes   yes           
   rp2      Watchdog scratch registers (8)          32 bytes      no            
   stm32    RTC backup registers (5-32 per family)  20-128 bytes  yes           
   =======  ======================================  ============  ==============

   .. note::

      On rp2, data persists across soft resets but is lost on power-off.

   Some registers may be reserved by the system depending on port and board
   configuration. These are exposed in the memoryview but should not be
   overwritten by application code:

   ======  ==============  =========================================================
   Port    Register(s)     Used by
   ======  ==============  =========================================================
   mimxrt  LPGPR[3]        UF2 bootloader double-tap (when ``USE_UF2_BOOTLOADER``)
   rp2     scratch[4-7]    pico-sdk ``watchdog_reboot()`` / ``machine.deepsleep()``
   stm32   BKP0R           Arduino bootloader (Portenta H7, Giga, Opta, Nicla)
   stm32   BKP16R-BKP18R   ``rfcore_firmware.py`` on STM32WB
   stm32   last BKP reg    clock frequency (``MICROPY_HW_CLK_LAST_FREQ``)
   stm32   BKP31R (N6)     mboot bootloader entry
   ======  ==============  =========================================================

   On ports returning a ``memoryview``, the buffer allows direct register
   access and can be combined with ``uctypes`` for structured layouts::

      import machine, uctypes

      rtc = machine.RTC()
      mem = rtc.memory()

      # Direct register access (each element is a 32-bit register)
      mem[0] = 0x12345678       # write register 0
      print(hex(mem[0]))        # read register 0

      # Structured access via uctypes (check len(mem) for your board)
      layout = {
          "flags": (0 * 4, uctypes.UINT32),    # register 0
          "counter": (1 * 4, uctypes.UINT32),  # register 1
      }
      regs = uctypes.struct(uctypes.addressof(mem), layout)
      regs.flags = 0x01
      print(regs.counter)

   Availability: alif, esp32, esp8266, mimxrt, rp2, stm32 ports.

Constants
---------

.. data:: RTC.ALARM0

    irq trigger source
