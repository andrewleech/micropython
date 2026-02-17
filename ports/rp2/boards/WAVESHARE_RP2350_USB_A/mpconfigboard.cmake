# cmake file for Waveshare RP2350-USB-A

# The Waveshare RP2350-USB-A has 16MB flash
set(MICROPY_PY_BTREE 1)

# Set the board variant for the Pico SDK
set(PICO_BOARD "waveshare_rp2350_one")

# Enable USB Host support via PIO USB
set(MICROPY_HW_USB_HOST 1)

# PIO USB requires sys clock to be a multiple of 12MHz.
# RP2350 default is 150MHz which doesn't divide evenly.
# 120MHz = 1440MHz VCO / (6 * 2)
add_compile_definitions(
    SYS_CLK_KHZ=120000
    PLL_SYS_VCO_FREQ_HZ=1440000000
    PLL_SYS_POSTDIV1=6
    PLL_SYS_POSTDIV2=2
)
