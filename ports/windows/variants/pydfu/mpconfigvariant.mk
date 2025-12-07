# Minimal variant for pydfu standalone application.
# Enables FFI for pyusb to call libusb.
# Disables compiler since only frozen .mpy bytecode is used.

MICROPY_PY_FFI = 1
MICROPY_ENABLE_COMPILER = 0
