.. _security:

Security model and practices
============================

This chapter describes MicroPython's security model and the practices the project follows to keep
the interpreter's C runtime sound. It is written for developers, contributors, and product
integrators who need to understand what MicroPython guarantees and where those guarantees stop.

For how to report a vulnerability and what response to expect, see the ``SECURITY.md`` file in the
repository root. This page does not repeat the reporting process.

The security model
------------------

MicroPython runs application code as bytecode on a managed virtual machine over a C runtime.
Ordinary Python code has no native pointer type and cannot perform address arithmetic, so a routine
application bug raises a catchable exception (``IndexError``, ``MemoryError``, ``RuntimeError``, and
so on) rather than corrupting memory. The security-relevant C attack surface is therefore the
interpreter itself -- the compiler, VM dispatch, object model, VFS, and peripheral bindings -- not
the application.

This guarantee is a property of the default, managed Python code path. It is not an absolute
property of the platform.

Leaving the managed subset
~~~~~~~~~~~~~~~~~~~~~~~~~~~~

The following documented mechanisms deliberately leave the managed subset and carry the same
memory-safety profile as C. A function or module using any of them should be assessed as C at that
point, not as sandboxed Python:

* ``machine.mem8`` / ``machine.mem16`` / ``machine.mem32`` -- direct, unchecked memory and register
  access.
* ``@micropython.native`` and ``@micropython.viper`` -- native code emitters. ``viper`` exposes raw
  ``ptr``, ``ptr8``, ``ptr16`` and ``ptr32`` types with no bounds checking.
* ``@micropython.asm_thumb`` and ``@micropython.asm_rv32`` -- inline assembly.
* Native C extension modules (``dynruntime.h`` / ``.mpy`` native modules).

This boundary defines the scope of what the project treats as its own vulnerability versus the
firmware author's responsibility.

Loading untrusted bytecode
~~~~~~~~~~~~~~~~~~~~~~~~~~~~

MicroPython does not verify the integrity of ``.mpy`` bytecode it loads: the loader assumes a valid
prelude and well-formed bytecode. The VM and loader are not hardened against invalid or corrupted
bytecode, so a malformed ``.mpy`` can crash or corrupt the runtime. The ``.mpy`` format is a trusted
deployment artefact, not an untrusted-input format; as with firmware images, a product that stores
or receives ``.mpy`` files must protect their integrity before loading them. This is a long-standing,
openly tracked assumption, not a hidden defect. Loading ``.mpy`` files from an untrusted source is
equivalent to running untrusted native code, and should be treated as such.

Isolation
~~~~~~~~~

MicroPython does not use a Memory Protection Unit to isolate Python tasks from each other or from
the interpreter, and its stack-overflow protection (``MICROPY_STACK_CHECK``) is a software counter
check. Devices requiring hardware-enforced isolation between mutually distrusting components must
not rely on the VM boundary alone to provide it.

Development practices
---------------------

The project relies on the following practices to keep the C runtime sound.

Code review
~~~~~~~~~~~

All changes are submitted as pull requests and reviewed before merge. Review covers functional
correctness, adherence to the code conventions, and appropriate handling of memory and untrusted
input. Merges are gated on maintainer approval.

Automated checks on every pull request
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

The following run in CI on the core, and a change cannot merge without them:

* **AddressSanitizer and UndefinedBehaviorSanitizer** -- the test suite runs under ASan and UBSan
  on the unix port. The workflow that runs it triggers on changes under ``py/``, ``extmod/`` and
  ``shared/``, so core changes are exercised under the sanitizers before merge.
* **Code coverage** -- ``gcov`` runs against ``py/*.c`` and ``extmod/*.c`` with results uploaded to
  Codecov.
* **Compiler warnings as errors** -- the unix port, which is where the sanitizer and coverage jobs
  run, builds with ``-Wall -Werror`` plus ``-Wextra -Wpointer-arith -Wdouble-promotion
  -Wfloat-conversion`` (``ports/unix/Makefile``). Other ports carry their own warning sets; this is
  not a single tree-wide flag.
* **Formatting and linting** -- C via ``tools/codeformat.py`` (uncrustify), Python via ``ruff`` and
  ``ruff format``; commit-message format is checked.

Because the sanitizer jobs run on core pull requests, the memory-safety defect classes that
sanitizers detect are caught before merge for any code path the test suite exercises.

The project does not currently run continuous fuzzing and is not enrolled in OSS-Fuzz. The
interpreter parses ``.mpy`` files, integer and array data, and string and slice inputs -- the code
where the project's historical CVEs have concentrated -- so these paths are the natural target for
any future fuzzing work.

Guidance for product developers
-------------------------------

If you are shipping a product on MicroPython:

* **Ship trusted, frozen bytecode.** A device that runs only pre-compiled, frozen, trusted
  application bytecode is not exposed to the "run untrusted Python" issues that most MicroPython
  security reports require. Exposing a REPL or loading untrusted ``.mpy`` files enlarges the attack
  surface considerably.
* **Audit for escape hatches.** Before relying on the managed-safety framing, audit the build for
  ``viper``, ``native``, inline assembly, ``machine.mem*`` and native C modules. Each reintroduces
  C-level risk in the functions that use it.
* **Pin and track releases.** MicroPython maintains a single evolving release line; keeping a device
  patched means upgrading, not backporting. See :ref:`releaseandversioning`.
* **Tune the GC for real-time paths.** The mark-sweep collector introduces non-deterministic pauses;
  pre-allocate buffers and tune ``gc.threshold()`` for latency-sensitive loops.

.. note::

   This page covers the security *model* and *practices*. To report a vulnerability, see
   ``SECURITY.md`` in the repository root.
