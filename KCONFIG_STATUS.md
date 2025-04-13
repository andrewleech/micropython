# Kconfig Integration Status Summary

This document summarizes the current state of the Kconfig integration effort.

## Completed Steps (Phases 1-3 & Initial Scan)

*   **Initial Setup:**
    *   Created top-level `Kconfig` and `Makefile`.
    *   Created skeleton `Kconfig` files for core directories (`py`, `extmod`, `drivers`, `shared`) and all ports.
*   **Core `py/` Defines:**
    *   Identified and added initial Kconfig options for core features, types, modules, optimizations, and memory settings based on `py/mpconfig.h` and `py/mpconfigbase.h`.
    *   Configured Kconfig output generation (`mpconfig_kconfig.h`, `autoconf.mk`, `config.cmake`) via the top-level Makefile.
    *   Integrated the generated header include into `py/mpconfig.h`.
*   **Makefile Integration:**
    *   Integrated `build/autoconf.mk` include into `py/mkrules.mk`.
*   **CMake Integration:**
    *   Integrated `build/config.cmake` include into `py/py.cmake`.
*   **Systematic Scan:**
    *   Scanned `py/`, `extmod/`, `drivers/`, `shared/`, `ports/`, and `mpy-cross/` for C `#define`s and Make/CMake variables starting with `MICROPY_` or `MPY_`.
    *   Added corresponding Kconfig entries for found configuration points into the relevant `Kconfig` files (e.g., `ports/unix/Kconfig`, `ports/stm32/Kconfig`, etc.).
*   **Documentation:**
    *   Created initial documentation page `docs/develop/kconfig.rst`.
    *   Added the page to the development documentation index.

## Kconfig Maintenance Check (`tools/check_kconfig_settings.sh`)

To help ensure new configuration options are added to Kconfig going forward, a check script has been added:

*   **Purpose:** Scans committed changes for new `#define MICROPY_...`, `#define MPY_...`, `MICROPY_... =`, `MPY_... =`, `set(MICROPY_...)`, or `set(MPY_...)` definitions/variables in relevant source files (`.c`, `.h`, `.mk`, `.cmake`, `CMakeLists.txt`), excluding common library/build directories.
*   **Verification:** For each new setting found, it searches all `Kconfig` files to ensure a corresponding `config SETTING_NAME` entry exists.
*   **Integration:** Designed to be run in pre-commit hooks and CI workflows.
*   **Failure:** If settings are found without a matching Kconfig entry, the script prints an error and exits with a non-zero status.

**Example Error Output:**

```
--------------------------------------------------
Error: Found new MICROPY_/MPY_ settings without corresponding Kconfig entries:
  - MICROPY_NEW_FEATURE_X
  - MICROPY_HW_COOL_SENSOR

Please add a 'config MICROPY_HW_COOL_SENSOR' entry to the appropriate Kconfig file.
--------------------------------------------------
```

This provides a mechanism to catch accidental omissions and maintain consistency between the code/build system and the Kconfig definitions.

## Next Steps (Phase 4 & 5 - Refinement, Testing, Cleanup)

Significant manual effort is required for the following:

1.  **Dependencies:** Analyze code logic (`#if`, conditional assignments in Make/CMake) across the entire codebase to define dependencies (`depends on`) and selections (`select`) between Kconfig options. This is crucial for ensuring valid configurations.
2.  **Help Text:** Review and significantly improve the help text for *all* Kconfig options, explaining their purpose, impact on resources (flash/RAM), and any potential side effects or requirements.
3.  **Refactoring:**
    *   Systematically replace hardcoded `#define` values in C source files with checks for the corresponding `CONFIG_...` Kconfig definition (e.g., `#if CONFIG_MICROPY_ENABLE_GC`).
    *   Refactor Makefiles (`*.mk`) to use variables derived from Kconfig (e.g., `ifeq ($(CONFIG_MICROPY_PY_FFI),y)`) instead of their original definitions.
    *   Refactor CMake files (`*.cmake`, `CMakeLists.txt`) to use Kconfig variables (e.g., `if(CONFIG_MICROPY_BLUETOOTH_NIMBLE)`).
    *   Remove original definitions from source/Make/CMake files once they are fully controlled by Kconfig.
4.  **Testing:**
    *   Perform thorough build testing across a wide range of ports (Make-based and CMake-based) and boards.
    *   Test various combinations of Kconfig options (enabling/disabling features) to ensure the build system and code behave correctly.
    *   Verify that generated firmware reflects the chosen Kconfig settings.
5.  **Documentation:**
    *   Expand and refine the `docs/develop/kconfig.rst` documentation with more detailed usage instructions, information about common options, and guidance for port maintainers.
6.  **Cleanup:**
    *   Consolidate duplicated options (e.g., `MICROPY_PY_NETWORK_WIZNET5K` defined in both `extmod/Kconfig` and `ports/stm32/Kconfig`). Decide on the canonical location for each option.
    *   Ensure consistency in naming and style across all Kconfig files.

This integration provides the basic framework, but the refinement phase is essential for making it robust and user-friendly.
