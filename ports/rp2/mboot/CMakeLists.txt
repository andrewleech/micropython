cmake_minimum_required(VERSION 3.13)

# Set build type for size-optimised bootloader image.
if(NOT CMAKE_BUILD_TYPE)
    set(CMAKE_BUILD_TYPE MinSizeRel)
endif()

# ---------------------------------------------------------------------------
# Path configuration — mirrors ports/rp2/CMakeLists.txt conventions.
# ---------------------------------------------------------------------------
set(MBOOT_TARGET mboot)
get_filename_component(MICROPY_DIR "../../.." ABSOLUTE)

# pico-sdk path: allow override via PICO_SDK_PATH_OVERRIDE.
if(PICO_SDK_PATH_OVERRIDE)
    set(PICO_SDK_PATH ${PICO_SDK_PATH_OVERRIDE})
else()
    set(PICO_SDK_PATH ${MICROPY_DIR}/lib/pico-sdk)
endif()

# Use MicroPython's pinned tinyusb, not the one bundled in pico-sdk.
set(PICO_TINYUSB_PATH ${MICROPY_DIR}/lib/tinyusb)

set(MBOOT_PORT_DIR ${CMAKE_CURRENT_LIST_DIR})
set(SHARED_MBOOT_DIR ${MICROPY_DIR}/shared/tinyusb/mboot)

# ---------------------------------------------------------------------------
# Board selection
# ---------------------------------------------------------------------------
if(NOT MICROPY_BOARD)
    set(MICROPY_BOARD RPI_PICO)
endif()

if(NOT MICROPY_BOARD_DIR)
    set(MICROPY_BOARD_DIR ${MICROPY_DIR}/ports/rp2/boards/${MICROPY_BOARD})
endif()
get_filename_component(MICROPY_BOARD_DIR ${MICROPY_BOARD_DIR} ABSOLUTE)

if(NOT EXISTS ${MICROPY_BOARD_DIR}/mpconfigboard.cmake)
    message(FATAL_ERROR "Invalid MICROPY_BOARD specified: ${MICROPY_BOARD}")
endif()

# Include the board cmake fragment (sets PICO_BOARD, PICO_PLATFORM, USE_MBOOT,
# etc.).  This is the same mpconfigboard.cmake used by the main rp2 firmware
# build, so there is no per-board duplication on the mboot side.
include(${MICROPY_BOARD_DIR}/mpconfigboard.cmake)

if(NOT USE_MBOOT)
    message(FATAL_ERROR
        "Board ${MICROPY_BOARD} does not enable mboot.  Add `set(USE_MBOOT 1)` "
        "to its mpconfigboard.cmake before building this target.")
endif()

# Default PICO_BOARD to lowercase board name if not set by the board cmake.
if(NOT PICO_BOARD)
    string(TOLOWER ${MICROPY_BOARD} PICO_BOARD)
endif()

# ---------------------------------------------------------------------------
# pico-sdk init (must happen before project() call).
# ---------------------------------------------------------------------------
include(${PICO_SDK_PATH}/pico_sdk_init.cmake)

# ---------------------------------------------------------------------------
# Top-level project declaration.
# ---------------------------------------------------------------------------
project(${MBOOT_TARGET})

pico_sdk_init()

# ---------------------------------------------------------------------------
# mboot source files.
#
# This list mirrors shared/tinyusb/mboot/mboot_common.mk SHARED_TINYUSB_MBOOT_SRC_C
# plus the rp2 port-specific sources.  CMake cannot include a .mk fragment
# directly so the list is restated here.
# ---------------------------------------------------------------------------
set(MBOOT_SRC_C
    # rp2 port adapter
    ${MBOOT_PORT_DIR}/main.c
    ${MBOOT_PORT_DIR}/mboot_port.c
    ${MBOOT_PORT_DIR}/port_flash.c
    ${MBOOT_PORT_DIR}/port_regions.c
    # shared/tinyusb/mboot common (mirrors mboot_common.mk SRC_C list)
    ${SHARED_MBOOT_DIR}/common/mboot_dbl_tap.c
    ${SHARED_MBOOT_DIR}/common/mboot_dfu.c
    ${SHARED_MBOOT_DIR}/common/mboot_region.c
    ${SHARED_MBOOT_DIR}/common/mboot_elem.c
    ${SHARED_MBOOT_DIR}/common/mboot_usbd.c
)

# ---------------------------------------------------------------------------
# Build target.
# ---------------------------------------------------------------------------
add_executable(${MBOOT_TARGET} ${MBOOT_SRC_C})

# ---------------------------------------------------------------------------
# Include paths.
# ---------------------------------------------------------------------------
target_include_directories(${MBOOT_TARGET} PRIVATE
    # mboot port headers (tusb_config.h, mboot_board.h with port defaults)
    ${MBOOT_PORT_DIR}
    # main board dir for mpconfigboard.h (per-board MBOOT_USB_PID etc.)
    ${MICROPY_BOARD_DIR}
    # shared mboot API and implementation headers
    ${SHARED_MBOOT_DIR}/include
)

# ---------------------------------------------------------------------------
# Compile definitions.
# ---------------------------------------------------------------------------
target_compile_definitions(${MBOOT_TARGET} PRIVATE
    # Guard: prevent any transitive py/mpconfig.h inclusion.
    MICROPY_INCLUDED_PY_MPCONFIG_H=1
    # mboot flash slot size (64 KiB default; matches linker --defsym below).
    MBOOT_RESERVED=0x10000
    # Shared functions annotated MBOOT_RAM_FUNC_ATTR must live in RAM because
    # flash erase / program suspends XIP.  `.time_critical.*` is collected
    # into RAM by pico-sdk's standard linker scripts and by ports/rp2/
    # memmap_mp_rp2*.ld.  Contract documented in mboot_api.h.
    "MBOOT_RAM_FUNC_ATTR=__attribute__((section(\".time_critical.mboot\")))"
)

# ---------------------------------------------------------------------------
# Link libraries.
# ---------------------------------------------------------------------------
# Link against pico-sdk hardware and tinyusb device libraries.
# Deliberately exclude pico_runtime_init to avoid pulling in lwip/mbedtls.
target_link_libraries(${MBOOT_TARGET}
    pico_stdlib
    pico_unique_id
    hardware_flash
    hardware_watchdog
    hardware_gpio
    hardware_sync
    tinyusb_device
    tinyusb_common
)

# ---------------------------------------------------------------------------
# Compiler flags.
# ---------------------------------------------------------------------------
target_compile_options(${MBOOT_TARGET} PRIVATE
    # Force-include the board's mpconfigboard.h so its `#if defined(USE_MBOOT)`
    # block (MBOOT_USB_PID, MBOOT_LED_PIN, MBOOT_PRODUCT_STRING) overrides the
    # port-level mboot_board.h #ifndef defaults.
    -include ${MICROPY_BOARD_DIR}/mpconfigboard.h
    -Wall
    -Werror
    -Os
    -DNDEBUG
    -DUSE_MBOOT
)

# ---------------------------------------------------------------------------
# Linker configuration.
# ---------------------------------------------------------------------------
# Use pico-sdk's standard linker script.  Note: setting PICO_FLASH_SIZE_BYTES
# here has no effect because pico_sdk_init() has already captured the board
# default from the board header before this point.  The generated
# pico_flash_region.ld therefore reflects the full chip flash size.
#
# The 64 KiB mboot slot is enforced two ways:
#   1. Runtime: mboot_port_flash_is_writable() rejects any write below
#      __mboot_end (0x10010000), preventing mboot from erasing itself.
#   2. Link time: mboot_assert.ld contains an ASSERT that fires if the
#      mboot image (__flash_binary_end) grows beyond __mboot_end.
#
# The linker symbols __mboot_start and __mboot_end are exported as absolute
# values for use by mboot_port_flash_is_writable.

target_link_options(${MBOOT_TARGET} PRIVATE
    -Wl,--defsym=MBOOT_RESERVED=0x10000
    -Wl,--defsym=__mboot_start=0x10000000
    -Wl,--defsym=__mboot_end=0x10010000
    # Slot-size assertion: fails at link time if mboot exceeds 64 KiB.
    -T${MBOOT_PORT_DIR}/mboot_assert.ld
)

# ---------------------------------------------------------------------------
# UF2 / binary outputs.
# ---------------------------------------------------------------------------
pico_add_extra_outputs(${MBOOT_TARGET})
