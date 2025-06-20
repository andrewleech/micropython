# Technical Planning Document: Local Variable Name Preservation in MicroPython

## ✅ IMPLEMENTATION COMPLETED

**Status:** Phase 1 successfully implemented and tested  
**Completion Date:** June 2025  
**Implementation:** Local variable name preservation for source files with hybrid architecture

## Executive Summary

This document presents architectural options for preserving local variable names in MicroPython's compilation and runtime system, enabling proper name resolution in `sys.settrace()` callbacks without modifying the bytecode format.

**COMPLETED IMPLEMENTATION:** Phase 1 provides local variable name preservation for source files with fallback support for .mpy files.

## Current Architecture Analysis

### String Interning (QSTR) System
- MicroPython uses **QSTR** (interned strings) for all identifiers
- Function names, class names, and global attributes are already preserved as QSTRs
- Local variable names exist as QSTRs during compilation but are **discarded** after bytecode generation
- The QSTR system is highly optimized for memory efficiency

### Compilation Flow
```
Source Code → Lexer (QSTRs created) → Parser → Scope Analysis → Bytecode Emission
                                                      ↓
                                              Local names exist here
                                              but are discarded
```

### Current Data Structures
```c
// During compilation (py/scope.h)
typedef struct _id_info_t {
    uint8_t kind;
    uint8_t flags;
    uint16_t local_num;
    qstr qst;  // Variable name as QSTR - currently discarded for locals
} id_info_t;

// Runtime structure (py/emitglue.h)
typedef struct _mp_raw_code_t {
    // ... existing fields ...
    mp_bytecode_prelude_t prelude;  // Contains n_state, n_pos_args, etc.
    // No local name information preserved
} mp_raw_code_t;
```

## Design Constraints

1. **No bytecode format changes** - Existing .mpy files must remain compatible
2. **Minimal code changes** - Reduce implementation complexity
3. **Memory efficiency** - Only store when needed (conditional compilation)
4. **QSTR integration** - Leverage existing string interning system
5. **Profile accessibility** - Names must be accessible from sys.settrace() callbacks

## Proposed Solutions

### Hybrid Approach: RAM + Bytecode Storage (Recommended)

We can implement **both** approaches with separate feature flags, providing complete coverage:

1. **RAM-only storage** for source file debugging (backward compatible)
2. **Bytecode storage** for .mpy debugging (new format version)

### Option 1A: Extend mp_raw_code_t (RAM Storage)

**Implementation:**
```c
// py/emitglue.h
typedef struct _mp_raw_code_t {
    // ... existing fields ...
    #if MICROPY_PY_SYS_SETTRACE_LOCALNAMES
    const qstr *local_names;  // Array of QSTRs indexed by local_num
    #endif
} mp_raw_code_t;
```

**Controlled by:** `MICROPY_PY_SYS_SETTRACE_LOCALNAMES`

**Use case:** Source file debugging, RAM-compiled code

### Option 1B: Bytecode Format Extension (Persistent Storage)

**Implementation:**
```c
// Enhanced prelude with optional local names section
// py/bc.h - extend mp_bytecode_prelude_t
typedef struct _mp_bytecode_prelude_t {
    // ... existing fields ...
    #if MICROPY_PY_SYS_SETTRACE_LOCALNAMES_PERSIST
    // Local names stored after bytecode, referenced by offset
    uint16_t local_names_offset;  // Offset to names section, 0 if none
    #endif
} mp_bytecode_prelude_t;
```

**Controlled by:** `MICROPY_PY_SYS_SETTRACE_LOCALNAMES_PERSIST`

**Use case:** .mpy debugging, frozen modules, persistent debugging info

### Configuration Matrix

| Feature Flag | Storage | Use Case | .mpy Support |
|-------------|---------|----------|--------------|
| `MICROPY_PY_SYS_SETTRACE_LOCALNAMES` | RAM only | Source debugging | No |
| `MICROPY_PY_SYS_SETTRACE_LOCALNAMES_PERSIST` | Bytecode | .mpy debugging | Yes |
| Both enabled | Hybrid | Full debugging | Yes |

### Hybrid Implementation Strategy

```c
// py/emitglue.h
typedef struct _mp_raw_code_t {
    // ... existing fields ...
    #if MICROPY_PY_SYS_SETTRACE_LOCALNAMES
    const qstr *local_names;  // RAM storage for source files
    #endif
} mp_raw_code_t;

// Access function that handles both storage types
static inline qstr mp_raw_code_get_local_name(const mp_raw_code_t *rc, uint16_t local_num) {
    #if MICROPY_PY_SYS_SETTRACE_LOCALNAMES
    // Try RAM storage first (source files)
    if (rc->local_names != NULL && rc->local_names[local_num] != MP_QSTR_NULL) {
        return rc->local_names[local_num];
    }
    #endif
    
    #if MICROPY_PY_SYS_SETTRACE_LOCALNAMES_PERSIST
    // Fall back to bytecode storage (.mpy files)
    return mp_bytecode_get_local_name(rc, local_num);
    #endif
    
    return MP_QSTR_NULL; // No name available
}
```

**Advantages of Hybrid Approach:**
- **Complete coverage**: Works with source files AND .mpy files
- **Backward compatibility**: Can be enabled independently
- **Optimal performance**: RAM storage for hot paths, bytecode for persistence
- **Gradual adoption**: Can implement RAM-only first, add bytecode later
- **Future-proof**: Provides migration path for different deployment scenarios

### Option 2: Function Object Attribute

**Implementation:**
```c
// Add new attribute to function objects
// Accessed as: func.__localnames__ or func.co_varnames
```

**Advantages:**
- Python-accessible for introspection
- Compatible with CPython's `co_varnames`
- No change to raw_code structure

**Disadvantages:**
- Requires modifying function object creation
- Additional indirection at runtime
- More complex implementation

### Option 3: Separate Global Mapping

**Implementation:**
```c
// Global hash table: raw_code_ptr → local_names_array
static mp_map_t raw_code_to_locals_map;
```

**Advantages:**
- Completely decoupled from existing structures
- Can be added/removed without touching core structures

**Disadvantages:**
- Additional lookup overhead
- Memory overhead for hash table
- Cleanup complexity for garbage collection

### Option 4: Encode in Bytecode Prelude

**Implementation:**
- Extend bytecode prelude with optional local names section
- Use a flag bit to indicate presence

**Advantages:**
- Data travels with bytecode
- Works with frozen bytecode

**Disadvantages:**
- **Violates constraint**: Changes bytecode format
- Breaks .mpy compatibility
- Increases bytecode size

## .mpy File Compatibility Impact

### Current Limitation (No Bytecode Changes)

**Yes, with the recommended approach (Option 1), .mpy files will NOT have local names available for debugging.**

This is because:

1. **Local names stored in RAM only**: Our approach stores local names in the `mp_raw_code_t` structure in RAM, which is created when Python source is compiled
2. **Not persisted in .mpy format**: The .mpy format only contains bytecode and QSTRs for global identifiers, not local variable names
3. **Compilation context lost**: When a .mpy file is loaded, we only have the bytecode - the original compilation scope information (where local names exist) is not available

### Implications for Users

```python
# Source file example.py:
def test_function():
    user_name = "Alice"  # Local variable name
    user_age = 25
    return user_name

# Debugging scenarios:
# 1. Running from source: python example.py
#    → frame.f_locals shows: {'user_name': 'Alice', 'user_age': 25}

# 2. Running from .mpy: micropython example.mpy  
#    → frame.f_locals shows: {'local_00': 'Alice', 'local_01': 25}
```

### Workarounds and Alternatives

1. **Development vs Production**:
   - Use source files during development/debugging
   - Deploy .mpy files for production (when debugging isn't needed)

2. **Hybrid debugging approach**:
   - Keep source files alongside .mpy for debugging
   - Tools could map .mpy local_XX back to source names

3. **Future enhancement** (requires bytecode format change):
   - Add optional local names section to .mpy format
   - Controlled by compilation flag: `mpy-cross --debug-info`

## Recommended Implementation Plan

### Phase 1: RAM Storage Infrastructure

1. **Add configuration macros:**
```c
// py/mpconfig.h
#ifndef MICROPY_PY_SYS_SETTRACE_LOCALNAMES
#define MICROPY_PY_SYS_SETTRACE_LOCALNAMES (0)
#endif

#ifndef MICROPY_PY_SYS_SETTRACE_LOCALNAMES_PERSIST  
#define MICROPY_PY_SYS_SETTRACE_LOCALNAMES_PERSIST (0)
#endif
```

2. **Add conditional field to mp_raw_code_t:**
```c
// py/emitglue.h
typedef struct _mp_raw_code_t {
    // ... existing fields ...
    #if MICROPY_PY_SYS_SETTRACE_LOCALNAMES
    const qstr *local_names;  // RAM storage for source files
    #endif
} mp_raw_code_t;
```

3. **Allocate and populate during compilation:**
```c
// py/compile.c - in scope_compute_things()
#if MICROPY_PY_SYS_SETTRACE_LOCALNAMES
if (scope->num_locals > 0) {
    qstr *names = m_new0(qstr, scope->num_locals);
    
    for (int i = 0; i < scope->id_info_len; i++) {
        id_info_t *id = &scope->id_info[i];
        if (ID_IS_LOCAL(id->kind) && id->local_num < scope->num_locals) {
            names[id->local_num] = id->qst;
        }
    }
    
    scope->raw_code->local_names = names;
}
#endif
```

### Phase 2: Bytecode Storage Infrastructure

1. **Extend bytecode prelude:**
```c
// py/bc.h
typedef struct _mp_bytecode_prelude_t {
    // ... existing fields ...
    #if MICROPY_PY_SYS_SETTRACE_LOCALNAMES_PERSIST
    uint16_t local_names_offset;  // Offset to names in bytecode
    #endif
} mp_bytecode_prelude_t;
```

2. **Add .mpy format support:**
```c
// py/persistentcode.c - extend save/load functions
#if MICROPY_PY_SYS_SETTRACE_LOCALNAMES_PERSIST
// Save local names after bytecode
// Load local names during .mpy loading
#endif
```

3. **Update mpy-cross tool:**
```bash
# Add command line option
mpy-cross --debug-locals source.py
```

### Phase 3: Unified Access Layer

1. **Create unified access function:**
```c
// py/emitglue.h
static inline qstr mp_raw_code_get_local_name(const mp_raw_code_t *rc, uint16_t local_num) {
    #if MICROPY_PY_SYS_SETTRACE_LOCALNAMES
    if (rc->local_names != NULL && rc->local_names[local_num] != MP_QSTR_NULL) {
        return rc->local_names[local_num];
    }
    #endif
    
    #if MICROPY_PY_SYS_SETTRACE_LOCALNAMES_PERSIST
    return mp_bytecode_get_local_name(rc, local_num);
    #endif
    
    return MP_QSTR_NULL;
}
```

2. **Update profile module:**
```c
// py/profile.c - in frame_f_locals()
qstr name = mp_raw_code_get_local_name(rc, i);
if (name != MP_QSTR_NULL) {
    // Use actual name
} else {
    // Fall back to local_XX
}
```

### Phase 4: Python Accessibility

Add `co_varnames` attribute to code objects:
```python
def func(a, b):
    x = 1
    y = 2

print(func.__code__.co_varnames)  # ('a', 'b', 'x', 'y')
```

## Memory Optimization Strategies

1. **Share common patterns:**
   - Many functions have similar local patterns (i, j, x, y)
   - Could use a pool of common name arrays

2. **Compress storage:**
   - Store only non-parameter locals (parameters can be reconstructed)
   - Use bit flags for common names

3. **Lazy allocation:**
   - Only allocate when settrace is active
   - Use weak references for cleanup

## Size Impact Analysis

**Typical function with 4 locals:**
- Storage: 4 * sizeof(qstr) = 8-16 bytes
- Overhead: ~0.5% of typical raw_code size

**Mitigation:**
- Only enabled with `MICROPY_PY_SYS_SETTRACE_LOCALNAMES`
- Zero cost when disabled

## Testing Strategy

1. **Correctness tests:**
   - Verify name mapping matches source order
   - Handle edge cases (no locals, many locals)
   - Test with nested functions and closures

2. **Memory tests:**
   - Measure overhead with typical programs
   - Verify cleanup on function deallocation

3. **Compatibility tests:**
   - Ensure .mpy files work unchanged
   - Test frozen bytecode compatibility

4. **.mpy limitation tests:**
   - Verify graceful fallback to local_XX naming
   - Test that debugging still works (with index names)

## Conclusion

**Option 1 (Extend mp_raw_code_t)** provides the best balance of:
- Minimal code changes
- Natural integration with existing architecture  
- Zero overhead when disabled
- Direct accessibility from profile module

This approach preserves the bytecode format while enabling full local variable name resolution in source-based debugging scenarios.

## Known Limitations

### 1. Pre-compiled .mpy Files (Conditional)

**Limitation**: Local variable names availability in .mpy files depends on compilation flags.

**Impact by Configuration**:

| Configuration | .mpy Debugging | Behavior |
|--------------|---------------|----------|
| `MICROPY_PY_SYS_SETTRACE_LOCALNAMES` only | No | Falls back to `local_XX` |
| `MICROPY_PY_SYS_SETTRACE_LOCALNAMES_PERSIST` only | Yes | Names preserved in bytecode |
| Both enabled | Yes | Full debugging support |

**Example with RAM-only configuration**:
```python
# Original source (example.py):
def calculate_total(price, quantity):
    tax_rate = 0.08
    subtotal = price * quantity
    tax = subtotal * tax_rate
    return subtotal + tax

# When debugging from source:
# frame.f_locals: {'price': 10.0, 'quantity': 5, 'tax_rate': 0.08, 'subtotal': 50.0, 'tax': 4.0}

# When debugging from .mpy (RAM-only config):
# frame.f_locals: {'local_00': 10.0, 'local_01': 5, 'local_02': 0.08, 'local_03': 50.0, 'local_04': 4.0}

# When debugging from .mpy (with PERSIST enabled):
# frame.f_locals: {'price': 10.0, 'quantity': 5, 'tax_rate': 0.08, 'subtotal': 50.0, 'tax': 4.0}
```

### 2. Memory Overhead

**Limitation**: Each function with local variables incurs additional memory overhead.

**Impact**:
- ~2-4 bytes per local variable per function
- Can be significant for memory-constrained devices with many functions
- Only incurred when `MICROPY_PY_SYS_SETTRACE_LOCALNAMES` is enabled

### 3. Frozen Bytecode

**Limitation**: Frozen bytecode in ROM will not have local names unless the freezing process is modified.

**Impact**:
- Built-in frozen modules lack local variable names
- Requires changes to the freezing toolchain for full support

### 4. Performance Considerations

**Limitation**: Slight increase in compilation time and memory allocation.

**Impact**:
- Additional array allocation during compilation
- One-time cost per function definition
- No runtime performance impact for normal execution

## Mitigation Strategies

1. **Development Workflow**:
   - Use source files during development and debugging
   - Deploy .mpy files only for production where debugging is less critical

2. **Hybrid Debugging**:
   - Keep source files available alongside .mpy for critical debugging scenarios
   - Tools can provide mapping between local_XX and actual names

3. **Selective Compilation**:
   - Enable local names only for modules that need debugging
   - Use different compilation flags for development vs production

4. **Future Enhancements**:
   - Optional debug sections in .mpy format (backward compatible)
   - Source map files for .mpy debugging
   - Enhanced mpy-cross with `--debug-info` flag

## Documentation Requirements

User-facing documentation should clearly state:

1. **Feature availability**: Local variable names in sys.settrace() require source files
2. **mpy limitation**: Pre-compiled .mpy files show generic local_XX names
3. **Memory impact**: Additional memory usage when feature is enabled
4. **Best practices**: Development/debugging workflow recommendations

This limitation is acceptable for the initial implementation as it provides significant debugging improvements for the common case (source file debugging) while maintaining full backward compatibility.