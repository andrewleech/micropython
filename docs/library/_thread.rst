:mod:`_thread` -- multithreading support
========================================

.. module:: _thread
   :synopsis: multithreading support

|see_cpython_module| :mod:`python:_thread`.

This module provides low-level primitives for working with multiple threads.
For details on which ports support threading and with what semantics, see
:ref:`thread-availability` below.

Functions
---------

.. function:: start_new_thread(function, args, [kwargs])

   Start a new thread and return its identifier. The thread executes the
   *function* with the argument tuple *args* (which must be a tuple). The
   optional *kwargs* argument specifies a dictionary of keyword arguments.

   When the function returns, the thread silently exits.

   When the function terminates with an unhandled exception,
   :func:`sys.print_exception` is called and then the thread exits (other
   threads continue to run).

.. function:: exit()

   Raise the ``SystemExit`` exception. When not caught, this will cause the
   thread to exit silently.

.. function:: allocate_lock()

   Return a new lock object. See :ref:`thread-lock-objects` below.

.. function:: get_ident()

   Return the "thread identifier" of the current thread. This is a nonzero
   integer. It is used to identify threads system-wide (only valid until the
   thread exits, after which the value may be recycled by the OS).

.. function:: stack_size([size])

   Return the thread stack size used when creating new threads. The optional
   *size* argument specifies the stack size to be used for subsequently
   created threads (the value 0 uses the platform default). If *size* is not
   specified, this function returns the previous value that was set by a
   prior call (0 means the platform default is in use).

.. _thread-lock-objects:

Lock Objects
------------

.. class:: LockType()

   This is the type of lock objects. Lock objects are created with
   :func:`allocate_lock`.

.. method:: lock.acquire(blocking=True)

   Acquire the lock. If *blocking* is ``True`` (the default), this method
   blocks until the lock is released by another thread, then sets it to locked
   and returns ``True``.

   If *blocking* is ``False``, the method does not block. If the lock cannot
   be acquired immediately, it returns ``False``; otherwise it sets the lock
   to locked and returns ``True``.

.. method:: lock.release()

   Release the lock. The lock must have been acquired earlier, but not
   necessarily by the same thread. Raises ``RuntimeError`` if the lock is
   not currently locked.

.. method:: lock.locked()

   Return ``True`` if the lock is currently locked, ``False`` otherwise.

Lock objects support the context manager protocol, so they can be used in
``with`` statements::

   lock = _thread.allocate_lock()
   with lock:
       # critical section
       pass

.. _thread-availability:

Port-Specific Availability and Behaviour
----------------------------------------

Threading support varies by port. Check ``sys.implementation._thread`` to
determine threading behaviour at runtime:

- ``"GIL"``: The Global Interpreter Lock serializes Python bytecode execution.
  Only one thread executes Python code at a time, but threads can still run
  concurrently during blocking I/O or C extension calls. Mutable objects can
  be shared between threads without explicit locking (though race conditions
  in application logic are still possible).

- ``"unsafe"``: No GIL. Multiple threads may execute Python bytecode truly in
  parallel on multi-core systems. **Mutable objects (dict, list, set,
  bytearray) shared between threads must be protected with explicit locks**,
  or corruption may occur.

If ``_thread`` is not available, ``sys.implementation._thread`` will not exist.

Unix and Windows
^^^^^^^^^^^^^^^^

Threading is implemented via POSIX threads (pthreads) or Windows threads.
GIL is enabled (``sys.implementation._thread == "GIL"``). No hard limit on
thread count beyond OS resources.

ESP32
^^^^^

Threading via FreeRTOS. GIL is enabled. Despite being a dual-core chip,
Python threads are pinned to a single core for GIL simplicity; the second
core handles WiFi/BLE networking. Practical thread count limited by available
RAM for thread stacks.

RP2 (Raspberry Pi Pico/Pico W)
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

The default build uses the Pico SDK multicore API:

- GIL disabled (``sys.implementation._thread == "unsafe"``)
- Maximum 2 threads: one on each core
- True parallel execution on both cores
- Users must protect shared mutable objects with locks

Alternative FreeRTOS-based builds may support more threads with different
core affinity settings.

STM32
^^^^^

Threading disabled by default. When enabled (``MICROPY_PY_THREAD=1``), uses
a port-specific threading implementation with GIL enabled.

Zephyr
^^^^^^

Threading via Zephyr's native thread API. GIL is enabled.

Example: Safe Multi-threaded Counter
------------------------------------

This example demonstrates proper locking for shared state::

   import _thread
   import time

   counter = [0]
   lock = _thread.allocate_lock()

   def worker(n):
       for _ in range(n):
           with lock:
               counter[0] += 1

   # Start two worker threads
   _thread.start_new_thread(worker, (1000,))
   _thread.start_new_thread(worker, (1000,))

   # Wait for threads to complete (simple approach)
   time.sleep(1)

   print(f"Counter: {counter[0]}")  # Should be 2000

.. note::

   When ``sys.implementation._thread == "GIL"``, the lock in this example is
   technically unnecessary for protecting the counter increment itself, but
   using locks is good practice for portable code that may run on "unsafe"
   platforms.
