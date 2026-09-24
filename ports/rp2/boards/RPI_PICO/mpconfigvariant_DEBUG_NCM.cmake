# Debug-firmware variant: sys.settrace plus USB NCM networking, so a debugger
# can reach the board over the USB cable with no WiFi.
set(MICROPY_PY_LWIP ON)

list(APPEND MICROPY_DEF_BOARD
    MICROPY_PY_NETWORK=1
    MICROPY_PY_NETWORK_USBD_NCM=1
    MICROPY_PY_SYS_SETTRACE=1
    MICROPY_PY_SYS_SETTRACE_LOCALNAMES=1
)
