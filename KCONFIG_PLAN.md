# Kconfig Integration Plan for MicroPython

**Important Note:** This Kconfig system is intended as an *optional overlay* to the existing `mpconfig*.h` and build variable system. It allows users to easily customize builds locally using `make menuconfig`. The primary configuration mechanism for MicroPython remains the existing system. The goal is not (currently) to fully replace the existing system, but to provide a user-friendly alternative for common overrides.

This document outlines the plan to integrate the Kconfig system into the MicroPython build process for managing compile-time configuration.

## Goals

1.  **User-Friendly Customization:** Provide a standard, interactive interface (menuconfig, xconfig) for users to *optionally* override default MicroPython features and port-specific settings for their local builds.
2.  **Discoverability:** Make common configuration options easily discoverable with integrated help text.
3.  **Structure:** Organize Kconfig options in a hierarchy mirroring the project structure.
4.  **Integration:** Ensure Kconfig selections (`CONFIG_...`) correctly override the base configuration derived from `mpconfigport.h`, `mpconfigboard.h`, and Make/CMake variables where Kconfig options exist.

## Prerequisites

*   Installation of Kconfig frontends (e.g., `kconfig-frontends` package providing `menuconfig`, `kconfig-conf`, etc., or Zephyr's `kconfiglib`). This plan assumes standard Kconfig tools are available in the PATH.

## Kconfig File Structure

*   A top-level `Kconfig` file will be created in the repository root.
*   This root `Kconfig` will source other `Kconfig` files located in relevant directories:
    *   `py/Kconfig`
    *   `extmod/Kconfig`
    *   `drivers/Kconfig`
    *   `shared/Kconfig`
    *   `ports/<port>/Kconfig` (e.g., `ports/unix/Kconfig`, `ports/stm32/Kconfig`)
    *   `ports/<port>/boards/Kconfig` (Potentially, if board-level Kconfig is desired)

## Implementation Phases

**Phase 1: Initial Setup and Core `py/` Defines**

1.  **Create Top-Level Files:**
    *   Create `Kconfig` (root): Defines the main menu structure and sources other Kconfig files.
    *   Create `Makefile` (root): Contains targets for Kconfig (`menuconfig`, `xconfig`) and a default target to delegate builds to ports.
2.  **Create Core Kconfig:**
    *   Create `py/Kconfig`.
3.  **Identify & Define Core `py/` Options:**
    *   Use `grep` to find common `#define`s starting with `MICROPY_` or `MPY_` within the `py/` directory (e.g., `MICROPY_ENABLE_GC`, `MICROPY_FLOAT_IMPL`, `MICROPY_PY_BUILTINS_SET`).
    *   Add corresponding `config` entries to `py/Kconfig` with appropriate types (`bool`, `int`, `hex`, `string`), default values (extracted from defines), and initial help text.
    *   Define choices where mutually exclusive options exist (e.g., float implementation).
4.  **Configure Kconfig Output (Header):**
    *   Set up Kconfig build environment variables (e.g., `KCONFIG_CONFIG=.config`, `srctree=.`, `objtree=build`) likely via the top-level Makefile.
    *   Configure Kconfig (e.g., using `kconfig-conf --syncconfig Kconfig`) to generate a C header file (e.g., `build/mpconfig_kconfig.h`) containing `#define`s for chosen options (prefixed with `CONFIG_`).
5.  **Integrate Generated Header:**
    *   Modify `py/mpconfig.h` (or a similar central header like `py/mpconfigbase.h`) to include `build/mpconfig_kconfig.h` *if it exists*.
    *   Guard existing default `#define`s in MicroPython source files (`#ifndef CONFIG_...`) so that Kconfig values take precedence when the header is present.
6.  **Testing:**
    *   Run `make menuconfig` from the root.
    *   Save a configuration (`.config`).
    *   Run `make -C mpy-cross` or `make PORT=unix -C ports/unix` (or the equivalent using the new top-level make target).
    *   Verify `build/mpconfig_kconfig.h` is generated correctly.
    *   Verify the build incorporates the settings from the generated header.

**Phase 2: Makefile Integration**

1.  **Identify Makefile Variables:**
    *   Use `grep` to find common variables starting with `MICROPY_` or `MPY_` in Makefiles (`Makefile`, `*.mk`) across the project, especially in `py/mkrules.mk` and port Makefiles. Examples: `FROZEN_MANIFEST`, `MICROPY_PY_FFI`.
2.  **Add Kconfig Options:**
    *   Add corresponding `config` entries to the relevant `Kconfig` files (e.g., `ports/unix/Kconfig` for port-specific Make variables).
3.  **Configure Kconfig Output (Makefile Include):**
    *   Configure Kconfig to generate a Makefile include fragment (e.g., `build/autoconf.mk`) containing Make variable assignments based on chosen options (`CONFIG_...=y` or `CONFIG_...=value`).
4.  **Integrate Generated Makefile:**
    *   Modify relevant Makefiles (e.g., `py/mkrules.mk`, port Makefiles) to include `build/autoconf.mk`. The inclusion point needs careful consideration - include it early to allow overrides later, or late to enforce Kconfig values. Start by including it early.
5.  **Refactor Makefiles:**
    *   Update Makefiles to use the Kconfig variables (e.g., `ifeq ($(CONFIG_MICROPY_PY_FFI),y)`).
    *   Remove original variable definitions if they are now fully controlled by Kconfig.
6.  **Testing:**
    *   Run `make menuconfig`, change relevant options, save.
    *   Run `make` for a Make-based port (e.g., `unix`, `stm32`).
    *   Verify the build reflects the Kconfig settings passed via `autoconf.mk`.

**Phase 3: CMake Integration**

1.  **Identify CMake Variables:**
    *   Use `grep` to find common variables set with `set(MICROPY_...` or `set(MPY_...` in CMake files (`CMakeLists.txt`, `*.cmake`), particularly in CMake-based ports like `esp32` and `rp2`.
2.  **Add Kconfig Options:**
    *   Add corresponding `config` entries to relevant `Kconfig` files (e.g., `ports/esp32/Kconfig`).
3.  **Configure Kconfig Output (CMake Include):**
    *   Configure Kconfig to generate a CMake script (e.g., `build/config.cmake`) containing CMake variable assignments (`set(CONFIG_... ON)` or `set(CONFIG_... "value")`).
4.  **Integrate Generated CMake:**
    *   Modify relevant `CMakeLists.txt` files to include `build/config.cmake` early in the configuration process.
5.  **Refactor CMake:**
    *   Update CMake files to use the Kconfig variables (e.g., `if(CONFIG_MICROPY_BLUETOOTH_NIMBLE)`).
    *   Remove original variable definitions if controlled by Kconfig.
6.  **Testing:**
    *   Run `make menuconfig`, change relevant options, save.
    *   Build a CMake-based port (e.g., `esp32`, `rp2`).
    *   Verify the build reflects the Kconfig settings passed via `config.cmake`.

**Phase 4: Expansion and Refinement**

1.  **Systematic Scan:** Thoroughly scan *all* project directories (`extmod`, `drivers`, `ports`, etc.) for C `#define`s, Makefile variables, and CMake variables starting with `MICROPY_` or `MPY_`.
2.  **Create Kconfig Entries:** Add Kconfig entries for all identified configuration points into the structured `Kconfig` files.
3.  **Dependencies:** Analyze code logic (`#if`, conditional assignments) to define dependencies (`depends on`) and selections (`select`) between Kconfig options.
4.  **Help Text:** Review and improve the help text for all Kconfig options, explaining their purpose and impact.
5.  **Add Targets:** Implement the `xconfig` target in the top-level Makefile. Implement the default target to delegate `make PORT=<port> [BOARD=<board>] ...` calls correctly.
6.  **Testing:** Test across a wider range of ports and configurations.

**Phase 5: Documentation and Cleanup**

1.  **Documentation:** Add documentation explaining the new Kconfig system, prerequisites, and usage.
2.  **Cleanup:** Remove configuration settings from original source files, Makefiles, and CMake files if they have been fully superseded by the Kconfig system.
3.  **Final Review:** Ensure consistency, test edge cases, and verify build success across major ports.

## Output Files (in `build/` directory)

*   `.config`: The saved Kconfig selections.
*   `mpconfig_kconfig.h`: Generated C header with `#define CONFIG_...`.
*   `autoconf.mk`: Generated Makefile include with `CONFIG_...=y/value`.
*   `config.cmake`: Generated CMake include with `set(CONFIG_... ON/"value")`.
*   Other Kconfig intermediate files (e.g., `syncconfig.log`).

This phased approach allows for incremental implementation and testing, reducing the risk associated with a large-scale change to the build system.
