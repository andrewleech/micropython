sys.settrace() Local Variable Name Preservation
===============================================

This document describes the local variable name preservation feature for MicroPython's
``sys.settrace()`` functionality, which allows debuggers and profilers to access
meaningful variable names instead of just indexed values.

Overview
--------

MicroPython's ``sys.settrace()`` implementation traditionally provided access to local
variables through generic index-based names (``local_00``, ``local_01``, etc.).
The local variable name preservation feature extends this by storing and exposing
the actual variable names when available.

This feature enables:

* Enhanced debugging experiences with meaningful variable names
* Better profiling and introspection capabilities
* Improved development tools that can show actual variable names

Configuration
-------------

The feature is controlled by configuration macros:

``MICROPY_PY_SYS_SETTRACE_LOCALNAMES``
  Enables local variable name preservation for source files (RAM storage).
  Default: ``0`` (disabled)

``MICROPY_PY_SYS_SETTRACE_LOCALNAMES_PERSIST``
  Enables local variable name preservation in bytecode for .mpy files.
  Default: ``0`` (disabled, implementation pending)

Dependencies
~~~~~~~~~~~~

* ``MICROPY_PY_SYS_SETTRACE`` must be enabled
* ``MICROPY_PERSISTENT_CODE_SAVE`` must be enabled

Memory Usage
~~~~~~~~~~~~

When enabled, the feature adds:

* One pointer field (``local_names``) per function in ``mp_raw_code_t``
* One length field (``local_names_len``) per function in ``mp_raw_code_t``
* One qstr array per function containing local variable names

Total memory overhead per function: ``8 bytes + (num_locals * sizeof(qstr))``

Implementation Details
----------------------

Architecture
~~~~~~~~~~~~

The implementation consists of several components:

1. **Compilation Phase** (``py/compile.c``)
   Collects local variable names during scope analysis and stores them
   in an array allocated with the raw code object.

2. **Storage** (``py/emitglue.h``)
   Extends ``mp_raw_code_t`` structure to include local variable name storage
   with proper bounds checking.

3. **Runtime Access** (``py/profile.c``)
   Provides access to variable names through ``frame.f_locals`` in trace
   callbacks, falling back to index-based names when real names unavailable.

4. **Unified Access Layer** (``py/emitglue.h``)
   Provides ``mp_raw_code_get_local_name()`` function with bounds checking
   and hybrid storage support.

Data Structures
~~~~~~~~~~~~~~~

Extended ``mp_raw_code_t`` structure:

.. code-block:: c

    typedef struct _mp_raw_code_t {
        // ... existing fields ...
        #if MICROPY_PY_SYS_SETTRACE_LOCALNAMES
        const qstr *local_names;      // Array of local variable names
        uint16_t local_names_len;     // Length of local_names array
        #endif
    } mp_raw_code_t;

Local Variable Name Collection
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

During compilation in ``scope_compute_things()``:

1. Check if scope is function-like and has local variables
2. Allocate qstr array with size ``scope->num_locals``
3. Iterate through ``id_info`` and populate array at correct indices
4. Store array pointer and length in raw code object

.. code-block:: c

    if (SCOPE_IS_FUNC_LIKE(scope->kind) && scope->num_locals > 0) {
        qstr *names = m_new0(qstr, scope->num_locals);
        for (int i = 0; i < scope->id_info_len; i++) {
            id_info_t *id = &scope->id_info[i];
            if ((id->kind == ID_INFO_KIND_LOCAL || id->kind == ID_INFO_KIND_CELL) && 
                id->local_num < scope->num_locals) {
                names[id->local_num] = id->qst;
            }
        }
        scope->raw_code->local_names = names;
        scope->raw_code->local_names_len = scope->num_locals;
    }

Bounds Checking
~~~~~~~~~~~~~~~

Critical for memory safety, the unified access function includes bounds checking:

.. code-block:: c

    static inline qstr mp_raw_code_get_local_name(const mp_raw_code_t *rc, uint16_t local_num) {
        #if MICROPY_PY_SYS_SETTRACE_LOCALNAMES
        if (rc->local_names != NULL && local_num < rc->local_names_len && 
            rc->local_names[local_num] != MP_QSTR_NULL) {
            return rc->local_names[local_num];
        }
        #endif
        return MP_QSTR_NULL; // No name available
    }

Usage
-----

Python API
~~~~~~~~~~

The feature integrates transparently with existing ``sys.settrace()`` usage:

.. code-block:: python

    import sys

    def trace_handler(frame, event, arg):
        if event == 'line':
            locals_dict = frame.f_locals
            print(f"Local variables: {list(locals_dict.keys())}")
        return trace_handler

    def test_function():
        username = "Alice"
        age = 25
        return username, age

    sys.settrace(trace_handler)
    result = test_function()
    sys.settrace(None)

Expected output with feature enabled:

.. code-block::

    Local variables: ['username', 'age']

Expected output with feature disabled:

.. code-block::

    Local variables: ['local_00', 'local_01']

Behavior
~~~~~~~~

**With Real Names Available:**
Variables appear with their actual names (``username``, ``age``, etc.)

**With Fallback Behavior:**
Variables appear with index-based names (``local_00``, ``local_01``, etc.)

**Mixed Scenarios:**
Some variables may have real names while others use fallback names,
depending on compilation and storage availability.

Limitations
-----------

Source Files vs .mpy Files
~~~~~~~~~~~~~~~~~~~~~~~~~~~

**Current Implementation (Phase 1):**

* ✅ Source files: Full local variable name preservation
* ❌ .mpy files: Fallback to index-based names (``local_XX``)

**Future Implementation (Phase 2):**

* ✅ Source files: Full local variable name preservation  
* ✅ .mpy files: Full local variable name preservation (when ``MICROPY_PY_SYS_SETTRACE_LOCALNAMES_PERSIST`` enabled)

Compatibility
~~~~~~~~~~~~~

* **Bytecode Compatibility:** Phase 1 maintains full bytecode compatibility
* **Memory Usage:** Adds memory overhead proportional to number of local variables
* **Performance:** Minimal runtime performance impact

Deployment Scenarios
~~~~~~~~~~~~~~~~~~~~

**Development Environment:**
Enable ``MICROPY_PY_SYS_SETTRACE_LOCALNAMES`` for full debugging capabilities
with source files.

**Production Deployment:**
Disable the feature to minimize memory usage, or enable selectively based
on debugging requirements.

**.mpy Distribution:**
Phase 1 provides fallback behavior. Phase 2 will enable full support with
``MICROPY_PY_SYS_SETTRACE_LOCALNAMES_PERSIST``.

Testing
-------

Unit Tests
~~~~~~~~~~

The feature includes comprehensive unit tests:

* ``tests/basics/sys_settrace_localnames.py`` - Basic functionality test
* ``tests/basics/sys_settrace_localnames_comprehensive.py`` - Detailed verification

Test Coverage
~~~~~~~~~~~~~

* Basic local variable access
* Nested function variables  
* Loop variable handling
* Exception handling scenarios
* Mixed real/fallback naming
* Memory safety (bounds checking)
* Integration with existing ``sys.settrace()`` functionality

Example Test
~~~~~~~~~~~~

.. code-block:: python

    def test_basic_names():
        def trace_handler(frame, event, arg):
            if frame.f_code.co_name == 'test_func':
                locals_dict = frame.f_locals
                real_names = [k for k in locals_dict.keys() if not k.startswith('local_')]
                return real_names
        
        def test_func():
            username = "test"
            return username
            
        sys.settrace(trace_handler)
        result = test_func()
        sys.settrace(None)
        # Should capture 'username' as a real variable name

Future Enhancements
-------------------

Phase 2: Bytecode Storage
~~~~~~~~~~~~~~~~~~~~~~~~~

Implementation of ``MICROPY_PY_SYS_SETTRACE_LOCALNAMES_PERSIST`` to store
local variable names in bytecode, enabling full support for .mpy files.

**Technical Approach:**
* Extend bytecode format to include local variable name tables
* Modify .mpy file format to preserve debugging information
* Implement bytecode-based name retrieval in ``mp_raw_code_get_local_name()``

Python Accessibility
~~~~~~~~~~~~~~~~~~~~

**Goal:** Make local variable names accessible through standard Python attributes

**Potential API:**
* ``function.__code__.co_varnames`` - Local variable names tuple
* ``frame.f_code.co_varnames`` - Access in trace callbacks

Performance Optimizations
~~~~~~~~~~~~~~~~~~~~~~~~~

* Lazy loading of variable names
* Compression of name storage
* Optional name interning optimizations

Integration Points
------------------

Debugger Integration
~~~~~~~~~~~~~~~~~~~

The feature provides a foundation for enhanced debugger support:

.. code-block:: python

    class MicroPythonDebugger:
        def __init__(self):
            self.breakpoints = {}
            
        def trace_callback(self, frame, event, arg):
            if event == 'line' and self.has_breakpoint(frame):
                # Access local variables with real names
                locals_dict = frame.f_locals
                self.show_variables(locals_dict)
            return self.trace_callback

Profiler Enhancement
~~~~~~~~~~~~~~~~~~~

Profilers can provide more meaningful variable analysis:

.. code-block:: python

    class VariableProfiler:
        def profile_function(self, func):
            def trace_wrapper(frame, event, arg):
                if event == 'return':
                    locals_dict = frame.f_locals
                    self.analyze_variable_usage(locals_dict)
                return trace_wrapper
            
            sys.settrace(trace_wrapper)
            result = func()
            sys.settrace(None)
            return result

Contributing
------------

Development Guidelines
~~~~~~~~~~~~~~~~~~~~~~

When modifying the local variable name preservation feature:

1. **Memory Safety:** Always include bounds checking for array access
2. **Compatibility:** Maintain bytecode compatibility in Phase 1
3. **Testing:** Add tests for new functionality
4. **Documentation:** Update this documentation for any API changes

Code Review Checklist
~~~~~~~~~~~~~~~~~~~~~

* ✅ Bounds checking implemented for all array access
* ✅ Memory properly allocated and freed
* ✅ Configuration macros respected
* ✅ Fallback behavior maintains compatibility
* ✅ Unit tests added for new functionality
* ✅ Documentation updated

File Locations
~~~~~~~~~~~~~~

**Core Implementation:**
* ``py/compile.c`` - Local name collection during compilation
* ``py/emitglue.h`` - Data structures and unified access
* ``py/emitglue.c`` - Initialization
* ``py/profile.c`` - Runtime access through ``frame.f_locals``
* ``py/mpconfig.h`` - Configuration macros

**Testing:**
* ``tests/basics/sys_settrace_localnames.py`` - Unit tests
* ``tests/basics/sys_settrace_localnames_comprehensive.py`` - Integration tests

**Documentation:**
* ``docs/develop/sys_settrace_localnames.rst`` - This document