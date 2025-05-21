# TOML Integration for MicroPython

## Overview

This document outlines the plan, progress, and next steps for integrating TOML (Tom's Obvious Minimal Language) support into MicroPython.

## Plan

1. Add the tomlc17 library as a submodule in lib/
2. Create a new module in extmod/modtoml.c based on the JSON module structure
3. Update the build system to include the TOML module
4. Create documentation for the TOML module
5. Create tests for the TOML module
6. Test the implementation on various MicroPython ports

## Progress

- [x] Added tomlc17 library as a submodule in lib/
- [x] Created modtoml.c with basic TOML parsing support
- [x] Updated extmod.mk and extmod.cmake to include the TOML module
- [x] Added warning suppression flags for tomlc17 compilation
- [x] Created basic tests for the TOML module
- [x] Successfully compiled and tested the module on Unix minimal port
- [ ] Fixed all issues with complex data structures
- [ ] Cross-port testing (STM32, ESP32, etc.)
- [ ] Documentation 

## Current Status

The TOML integration is partially working. Basic key-value pairs can be parsed successfully, but complex data structures like nested tables and arrays have issues. The implementation is working on the Unix port with minimal configuration, but cross-port compatibility requires additional work.

### Working Features

- Basic parsing of TOML strings with `loads()` function
- Support for string, integer, and boolean values
- Conversion between TOML and MicroPython data structures

### Known Issues

1. Overflow errors occur with complex data structures
2. Issues with float value handling
3. STM32 port compilation errors due to missing dependencies
4. Date/time value support is problematic on embedded platforms

## Next Steps

1. **Fix complex data structure handling**: Investigate and fix the overflow errors occurring with complex TOML documents.
2. **Improve error handling**: Add more robust error handling for malformed TOML input.
3. **Cross-port compatibility**: Resolve issues with compiling on STM32 and other embedded ports.
4. **Documentation**: Create proper documentation for the TOML module in docs/library/toml.rst.
5. **Test suite**: Expand test coverage for various TOML features.
6. **Optimization**: Reduce memory usage for embedded platforms.

## Implementation Notes

- The TOML module is implemented as an extension module in extmod/modtoml.c
- It uses the tomlc17 library (https://github.com/ToruNiina/tomlc17) for parsing
- The module is enabled using the MICROPY_PY_TOML build flag
- Current implementation offers a minimal `loads()` function similar to the JSON module

## References

- [TOML Specification](https://toml.io/en/v1.0.0)
- [tomlc17 Repository](https://github.com/ToruNiina/tomlc17)
- [MicroPython JSON module](https://docs.micropython.org/en/latest/library/json.html)