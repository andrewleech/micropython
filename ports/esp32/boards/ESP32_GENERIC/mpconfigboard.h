// Both of these can be set by mpconfigboard.cmake if a BOARD_VARIANT is
// specified.

#ifndef MICROPY_HW_BOARD_NAME
#define MICROPY_HW_BOARD_NAME "Generic ESP32 module"
#endif

#ifndef MICROPY_HW_MCU_NAME
#define MICROPY_HW_MCU_NAME "ESP32"
#endif

// This board is a debug-firmware build target: enable sys.settrace() support.
#ifndef MICROPY_PY_SYS_SETTRACE
#define MICROPY_PY_SYS_SETTRACE (1)
#endif
#ifndef MICROPY_PY_SYS_SETTRACE_LOCALNAMES
#define MICROPY_PY_SYS_SETTRACE_LOCALNAMES (1)
#endif
