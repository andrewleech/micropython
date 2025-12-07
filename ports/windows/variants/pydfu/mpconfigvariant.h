/*
 * This file is part of the MicroPython project, http://micropython.org/
 *
 * The MIT License (MIT)
 *
 * Copyright (c) 2024 Andrew Leech
 *
 * Permission is hereby granted, free of charge, to any person obtaining a copy
 * of this software and associated documentation files (the "Software"), to deal
 * in the Software without restriction, including without limitation the rights
 * to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
 * copies of the Software, and to permit persons to whom the Software is
 * furnished to do so, subject to the following conditions:
 *
 * The above copyright notice and this permission notice shall be included in
 * all copies or substantial portions of the Software.
 *
 * THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
 * IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
 * FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
 * AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
 * LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
 * OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
 * THE SOFTWARE.
 */

// Minimal variant for pydfu standalone application.
// Disables features not needed to reduce binary size.
// The compiler is disabled via mpconfigvariant.mk since only frozen .mpy
// bytecode is used.

// Disable modules not needed by pydfu
#define MICROPY_PY_JSON                     (0)
#define MICROPY_PY_RE                       (0)
#define MICROPY_PY_DEFLATE                  (0)
#define MICROPY_PY_DEFLATE_COMPRESS         (0)
#define MICROPY_PY_HASHLIB                  (0)
#define MICROPY_PY_UCTYPES                  (0)
#define MICROPY_PY_HEAPQ                    (0)
#define MICROPY_PY_RANDOM                   (0)
#define MICROPY_PY_CMATH                    (0)
#define MICROPY_PY_MATH_SPECIAL_FUNCTIONS   (0)
#define MICROPY_PY_MATH_ISCLOSE             (0)
#define MICROPY_PY_MACHINE                  (0)
#define MICROPY_PY_MACHINE_PULSE            (0)
#define MICROPY_PY_MACHINE_PIN_BASE         (0)
#define MICROPY_PY_ERRNO                    (0)

// Disable collection extras
#define MICROPY_PY_COLLECTIONS_DEQUE        (0)
#define MICROPY_PY_COLLECTIONS_DEQUE_ITER   (0)
#define MICROPY_PY_COLLECTIONS_DEQUE_SUBSCR (0)
#define MICROPY_PY_COLLECTIONS_ORDEREDDICT  (0)
#define MICROPY_PY_BUILTINS_FROZENSET       (0)

// Disable builtin extras not needed
#define MICROPY_PY_BUILTINS_COMPILE         (0)
#define MICROPY_PY_BUILTINS_INPUT           (0)
#define MICROPY_PY_BUILTINS_HELP            (0)
#define MICROPY_PY_BUILTINS_HELP_MODULES    (0)
#define MICROPY_PY_BUILTINS_NOTIMPLEMENTED  (0)
#define MICROPY_PY_BUILTINS_POW3            (0)
#define MICROPY_PY_BUILTINS_ROUND_INT       (0)
#define MICROPY_PY_BUILTINS_SLICE_ATTRS     (0)

// Disable advanced object features
#define MICROPY_PY_DESCRIPTORS              (0)
#define MICROPY_PY_DELATTR_SETATTR          (0)
#define MICROPY_PY_ALL_SPECIAL_METHODS      (0)
#define MICROPY_PY_REVERSE_SPECIAL_METHODS  (0)
#define MICROPY_PY_FUNCTION_ATTRS           (0)

// Disable REPL features (not needed for standalone app)
#define MICROPY_REPL_EMACS_KEYS             (0)
#define MICROPY_REPL_AUTO_INDENT            (0)
#define MICROPY_USE_READLINE_HISTORY        (0)

// Disable debug/stats features
#define MICROPY_DEBUG_PRINTERS              (0)
#define MICROPY_MEM_STATS                   (0)
#define MICROPY_MALLOC_USES_ALLOCATED_SIZE  (0)

// Disable sys extras
#define MICROPY_PY_SYS_ATEXIT               (0)
#define MICROPY_PY_MICROPYTHON_MEM_INFO     (0)

// Use terse error reporting to save space
#define MICROPY_ERROR_REPORTING             (MICROPY_ERROR_REPORTING_TERSE)
#define MICROPY_WARNINGS                    (0)
#define MICROPY_PY_STR_BYTES_CMP_WARN       (0)

