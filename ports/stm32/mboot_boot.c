/*
 * This file is part of the MicroPython project, http://micropython.org/
 *
 * The MIT License (MIT)
 *
 * Copyright (c) 2026 Andrew Leech
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

// The bootloader side of the mboot_port.h implementation for the stm32 port (mboot_port.c has the
// part both roles use). The bootloader runs on the clock, SysTick, pin, UART, USB and flash
// services of the port: SystemClock_Config() with the board's MICROPY_HW_CLK_* values, the HAL
// tick, the pin objects of the board and mp_hal_pin_config() (mphalport_hw.c), uart_init_tx() and
// uart_tx_strn() (uart.c, without its runtime parts) and mp_usbd_ll_init() (usbd_conf.c), so a
// board needs only the settings its application already has. mboot_port_deinit() puts back what
// the bootloader changed, so the application starts from the state a reset leaves.
//
// The STM32H5 bootloader runs with TZEN=0 on the non-secure register aliases.

#include <errno.h>
#include <stddef.h>

#include "py/mphal.h"
#include "boardctrl.h"
#include "powerctrl.h"
#include "uart.h"

#include "mcuboot_config/mcuboot_config.h"
#include "mcuboot_config/mcuboot_logging.h"
#include "mboot_log.h"
#include "mboot_port.h"

#if MBOOT_DFU_ENABLE
#include "tusb.h"
#endif

// Enable the instruction cache (STM32H5) or the instruction and data caches (STM32F7).
#ifndef MICROPY_HW_MBOOT_CACHE
#define MICROPY_HW_MBOOT_CACHE (1)
#endif

// The pin objects of the board (pins_<BOARD>.c) refer to the types of the pin classes of the
// application. The bootloader has no classes; the objects only need an address to point to.
const mp_obj_type_t pin_type;
const mp_obj_type_t pin_af_type;
const mp_obj_type_t pin_analog_type;

// The LEDs, USER button and log UART come from the board file (mpconfigboard.h), the same ones
// the application uses, with pins named pin_<port><number> (genhdr/pins.h):
//   MICROPY_HW_LED1, _LED2, _LED3   alive, USB active and error (bits 0 to 2 of
//                                   mboot_port_led()); MICROPY_HW_LED_ON(pin) and
//                                   MICROPY_HW_LED_OFF(pin) give the polarity
//   MICROPY_HW_USRSW_PIN            forced entry while the input reads MICROPY_HW_USRSW_PRESSED
//                                   (0 or 1), with the pull MICROPY_HW_USRSW_PULL
//   MICROPY_HW_UART_REPL            the log UART (when MICROPY_MBOOT_LOG_LEVEL > 0), at
//                                   MICROPY_HW_UART_REPL_BAUD; a board without a log UART sets
//                                   MICROPY_MBOOT_LOG_LEVEL 0

#ifndef MICROPY_HW_LED1
#error "the board file does not define MICROPY_HW_LED1 (the bootloader shows alive on it)"
#endif
#ifndef MICROPY_HW_LED2
#error "the board file does not define MICROPY_HW_LED2 (the bootloader shows USB activity on it)"
#endif
#ifndef MICROPY_HW_LED3
#error "the board file does not define MICROPY_HW_LED3 (the bootloader shows errors on it)"
#endif
#ifndef MICROPY_HW_LED_ON
#error "the board file does not define MICROPY_HW_LED_ON(pin)"
#endif
#ifndef MICROPY_HW_LED_OFF
#error "the board file does not define MICROPY_HW_LED_OFF(pin)"
#endif
#ifndef MICROPY_HW_USRSW_PIN
#error "the board file does not define MICROPY_HW_USRSW_PIN (the bootloader's forced entry button)"
#endif
#ifndef MICROPY_HW_USRSW_PULL
#error "the board file does not define MICROPY_HW_USRSW_PULL"
#endif
#ifndef MICROPY_HW_USRSW_PRESSED
#error "the board file does not define MICROPY_HW_USRSW_PRESSED"
#endif
#if MICROPY_MBOOT_LOG_LEVEL > 0
#ifndef MICROPY_HW_UART_REPL
#error "the board file does not define MICROPY_HW_UART_REPL (the bootloader's log UART)"
#endif
#ifndef MICROPY_HW_UART_REPL_BAUD
#error "the board file does not define MICROPY_HW_UART_REPL_BAUD"
#endif
#endif

// The GPIO ports are the first bits of the AHB enable and reset registers: AHB2 on STM32H5, AHB1
// on STM32F7.
#if defined(STM32H5)
#define GPIO_CLK_ENR (RCC->AHB2ENR)
#define GPIO_RSTR (RCC->AHB2RSTR)
#else
#define GPIO_CLK_ENR (RCC->AHB1ENR)
#define GPIO_RSTR (RCC->AHB1RSTR)
#endif
#if defined(GPIOK)
#define GPIO_PORTS (11)
#elif defined(GPIOJ)
#define GPIO_PORTS (10)
#elif defined(GPIOI)
#define GPIO_PORTS (9)
#else
#define GPIO_PORTS (8)
#endif
#define GPIO_PORTS_MASK ((1u << GPIO_PORTS) - 1)

// ---- state ----

extern const uint32_t g_pfnVectors[];

static uint32_t s_rsr;
static bool s_started;

// Register values at entry, which mboot_port_deinit() writes back.
static struct {
    #if defined(STM32H5)
    uint32_t ahb2enr;
    uint32_t apb1lenr;
    uint32_t apb2enr;
    uint32_t apb3enr;
    uint32_t voscr;
    uint32_t dbpcr;
    #else
    uint32_t ahb1enr;
    uint32_t ahb2enr;
    uint32_t ahb3enr;
    uint32_t apb1enr;
    uint32_t apb2enr;
    uint32_t pwr_cr1;
    #endif
    uint32_t flash_acr;
} s_entry;

// ---- timing ----

// The tick of the HAL (HAL_GetTick(), the time base of the HAL timeouts and of the flash driver).
void SysTick_Handler(void) {
    HAL_IncTick();
}

// The mp_hal timing of the port services the bootloader uses (powerctrlboot.c, the SPI flash
// drivers).
mp_uint_t mp_hal_ticks_ms(void) {
    return HAL_GetTick();
}

void mp_hal_delay_ms(mp_uint_t ms) {
    if (!(SysTick->CTRL & SysTick_CTRL_ENABLE_Msk)) {
        // The tick is stopped (before mboot_port_early_init() or after mboot_port_deinit()):
        // count loop iterations instead.
        for (volatile uint32_t n = ms * (SystemCoreClock / 4000); n != 0; n--) {
        }
        return;
    }
    HAL_Delay(ms);
}

uint32_t mboot_port_ticks_ms(void) {
    return mp_hal_ticks_ms();
}

void mboot_port_delay_ms(uint32_t ms) {
    mp_hal_delay_ms(ms);
}

// ---- board hook ----

#if defined(MICROPY_HW_MBOOT_BOARD_EARLY_INIT)

// The board hook of mboot (MICROPY_HW_MBOOT_BOARD_EARLY_INIT in mpconfigboard.h, PYBD: the pull-up
// on the pin that selects 500 mA on WBUS-DIP28). mboot leaves what the hook sets in place when it
// starts the application and the application does not set it again, so mboot_port_deinit() runs
// the hook once more after it has reset the GPIO ports.
static void mboot_board_hook(void) {
    uint32_t initial_r0 = 0;
    MICROPY_HW_MBOOT_BOARD_EARLY_INIT(&initial_r0);
    (void)initial_r0;
}

#else

static inline void mboot_board_hook(void) {
}

#endif

// ---- LEDs and log UART ----

#define LED_SET(pin, on) \
    do { \
        if (on) { \
            MICROPY_HW_LED_ON(pin); \
        } else { \
            MICROPY_HW_LED_OFF(pin); \
        } \
    } while (0)

void mboot_port_led(uint32_t mask) {
    LED_SET(MICROPY_HW_LED1, mask & 1u);
    LED_SET(MICROPY_HW_LED2, mask & 2u);
    LED_SET(MICROPY_HW_LED3, mask & 4u);
}

#if MICROPY_MBOOT_LOG_LEVEL > 0

// The UART of the console of the application (MICROPY_HW_UART_REPL), from uart.c without its
// runtime parts (MICROPY_HW_UART_NO_RUNTIME). timeout_char bounds the wait for each character.
static machine_uart_obj_t s_uart = {
    .uart_id = MICROPY_HW_UART_REPL,
    .timeout_char = 10,
};
static bool s_uart_ready;

static void uart_start(void) {
    s_uart_ready = uart_init_tx(&s_uart, MICROPY_HW_UART_REPL_BAUD);
}

#endif

void mboot_port_log_write(const char *s, size_t n) {
    #if MICROPY_MBOOT_LOG_LEVEL > 0
    if (!s_uart_ready) {
        return;
    }
    while (n > 0) {
        size_t len = 0;
        while (len < n && s[len] != '\n') {
            len++;
        }
        uart_tx_strn(&s_uart, s, len);
        if (len < n) {
            uart_tx_strn(&s_uart, "\r\n", 2);
            len++;
        }
        s += len;
        n -= len;
    }
    #else
    (void)s;
    (void)n;
    #endif
}

// ---- lifecycle ----

// SystemClock_Config() of the port stops in the board fatal error handler when an oscillator or
// the PLL does not start. The bootloader logs the failure and resets, as it does for a fault.
MP_NORETURN void boardctrl_fatal_error(const char *msg) {
    mboot_fault_fatal(msg);
}

void mboot_port_early_init(void) {
    s_rsr = RCC->RCC_SR;

    #if defined(STM32H5)
    s_entry.ahb2enr = RCC->AHB2ENR;
    s_entry.apb1lenr = RCC->APB1LENR;
    s_entry.apb2enr = RCC->APB2ENR;
    s_entry.apb3enr = RCC->APB3ENR;
    s_entry.voscr = PWR->VOSCR;
    s_entry.dbpcr = PWR->DBPCR;
    volatile uint32_t *uid = (volatile uint32_t *)UID_BASE;
    for (size_t i = 0; i < 3; i++) {
        ((uint32_t *)mp_hal_unique_id_address)[i] = uid[i];
    }
    #else
    s_entry.ahb1enr = RCC->AHB1ENR;
    s_entry.ahb2enr = RCC->AHB2ENR;
    s_entry.ahb3enr = RCC->AHB3ENR;
    s_entry.apb1enr = RCC->APB1ENR;
    s_entry.apb2enr = RCC->APB2ENR;
    // The PWR registers read as zero until the interface clock runs.
    __HAL_RCC_PWR_CLK_ENABLE();
    s_entry.pwr_cr1 = PWR->CR1;
    #endif
    s_entry.flash_acr = FLASH->ACR;

    SCB->VTOR = (uint32_t)g_pfnVectors;
    mboot_fault_enable();
    NVIC_SetPriorityGrouping(NVIC_PRIORITYGROUP_4);

    // The clock configuration reads the clock of a previous run from a backup register when the
    // board has MICROPY_HW_CLK_LAST_FREQ, so its access has to be up.
    (void)mboot_port_bkp_read(MBOOT_BKP_RETENTION);
    mboot_board_hook();

    // The HAL timeouts of the clock configuration run on SysTick.
    HAL_InitTick(TICK_INT_PRIORITY);
    SystemClock_Config();

    #if MICROPY_HW_MBOOT_CACHE
    #if defined(ICACHE)
    HAL_ICACHE_Enable();
    #elif __DCACHE_PRESENT == 1
    SCB_EnableICache();
    SCB_EnableDCache();
    #endif
    #endif

    mp_hal_pin_output(MICROPY_HW_LED1);
    mp_hal_pin_output(MICROPY_HW_LED2);
    mp_hal_pin_output(MICROPY_HW_LED3);
    mboot_port_led(0);

    #if MICROPY_MBOOT_LOG_LEVEL > 0
    uart_start();
    #endif

    s_started = true;
}

#if defined(STM32F7)
// Waits for (*reg & mask) == value; false if it does not happen within a bounded number of reads.
static bool rcc_wait(const volatile uint32_t *reg, uint32_t mask, uint32_t value) {
    for (uint32_t n = 0; n < 200000; n++) {
        if ((*reg & mask) == value) {
            return true;
        }
    }
    return false;
}

// The clock tree back to the reset state. The F7 HAL_RCC_DeInit() only writes registers: it clears
// PLLON and rewrites PLLCFGR right after the write that selects HSI as the system clock, while the
// PLL may still be the system clock. The PLL is only stopped once HSI is the system clock, and
// PLLCFGR is only written once the PLL is stopped.
static void rcc_deinit(void) {
    RCC->CR |= RCC_CR_HSION;
    rcc_wait(&RCC->CR, RCC_CR_HSIRDY, RCC_CR_HSIRDY);
    RCC->CFGR &= ~RCC_CFGR_SW;
    rcc_wait(&RCC->CFGR, RCC_CFGR_SWS, RCC_CFGR_SWS_HSI);
    RCC->CR &= ~(RCC_CR_CSSON | RCC_CR_PLLON | RCC_CR_PLLI2SON | RCC_CR_PLLSAION);
    rcc_wait(&RCC->CR, RCC_CR_PLLRDY | RCC_CR_PLLI2SRDY | RCC_CR_PLLSAIRDY, 0);
    HAL_RCC_DeInit();
    // SystemClock_Config() switched the overdrive on. The HAL sequence waits for the regulator to
    // leave it, so the application can switch it on again.
    HAL_PWREx_DisableOverDrive();
}
#endif

void mboot_port_deinit(void) {
    if (!s_started) {
        return;
    }
    s_started = false;

    #if MBOOT_DFU_ENABLE
    mboot_port_usb_deinit();
    #endif

    // The flash controller is locked by the flash driver after every operation; wait for an
    // erase that may still run.
    if (FLASH_WaitForLastOperation(50) == HAL_TIMEOUT) {
        MCUBOOT_LOG_ERR("flash controller still busy, an operation is still running");
    }
    HAL_FLASH_Lock();

    // Write the caches back (the data cache of a Cortex-M7 holds the request region and everything
    // else the bootloader wrote) and disable them before the application starts.
    #if defined(ICACHE)
    HAL_ICACHE_Disable();
    #elif __DCACHE_PRESENT == 1
    SCB_DisableICache();
    SCB_DisableDCache();
    #endif

    // The clock tree back to the reset state. The HAL uses the tick, so this comes before the tick
    // and the interrupts are stopped.
    #if defined(STM32F7)
    rcc_deinit();
    #else
    HAL_RCC_DeInit();
    #endif

    __disable_irq();
    SysTick->CTRL = 0;
    SCB->ICSR = SCB_ICSR_PENDSTCLR_Msk;
    for (size_t i = 0; i < sizeof(NVIC->ICER) / sizeof(NVIC->ICER[0]); i++) {
        NVIC->ICER[i] = 0xFFFFFFFFu;
        NVIC->ICPR[i] = 0xFFFFFFFFu;
    }
    mboot_fault_disable();

    // Peripherals used by the bootloader back to their reset state, then their clocks.
    GPIO_RSTR = GPIO_CLK_ENR & GPIO_PORTS_MASK;
    GPIO_RSTR = 0;
    // The board hook sets state that is meant to outlive the bootloader (see mboot_board_hook()).
    mboot_board_hook();
    #if MICROPY_MBOOT_LOG_LEVEL > 0
    if (s_uart_ready) {
        uart_deinit(&s_uart);
        s_uart_ready = false;
    }
    #endif
    #if !defined(STM32H5) && defined(MICROPY_HW_MBOOT_SPIFLASH_ADDR) && defined(RCC_AHB3RSTR_QSPIRST)
    // The early init of the board may have set up the QUADSPI of a second SPI flash.
    RCC->AHB3RSTR = RCC_AHB3RSTR_QSPIRST;
    RCC->AHB3RSTR = 0;
    #endif

    // Reaching this point means the teardown that can fault is done; what follows is plain register
    // writes and the jump. A fault before it is counted, and after repeated ones the bootloader
    // enters recovery instead of starting the application again. The count is backup domain state,
    // so it is cleared before the register restore below, which can switch the access off.
    mboot_fault_count_clear();

    FLASH->ACR = s_entry.flash_acr;
    #if defined(STM32H5)
    PWR->VOSCR = s_entry.voscr;
    PWR->DBPCR = s_entry.dbpcr;
    RCC->AHB2ENR = s_entry.ahb2enr;
    RCC->APB1LENR = s_entry.apb1lenr;
    RCC->APB2ENR = s_entry.apb2enr;
    RCC->APB3ENR = s_entry.apb3enr;
    #else
    PWR->CR1 = s_entry.pwr_cr1;
    RCC->AHB1ENR = s_entry.ahb1enr;
    RCC->AHB2ENR = s_entry.ahb2enr;
    RCC->AHB3ENR = s_entry.ahb3enr;
    RCC->APB1ENR = s_entry.apb1enr;
    RCC->APB2ENR = s_entry.apb2enr;
    #endif
}

uint32_t mboot_port_reset_cause(void) {
    // BOR and POR, the soft reset by NVIC_SystemReset, the watchdogs and a low-power reset.
    uint32_t cause = 0;
    #if defined(RCC_SR_PORRSTF)
    if (s_rsr & (RCC_SR_PORRSTF | RCC_SR_BORRSTF)) {
    #else
    if (s_rsr & RCC_SR_BORRSTF) {
        #endif
        cause |= MBOOT_RESET_POR;
    }
    if (s_rsr & RCC_SR_SFTRSTF) {
        cause |= MBOOT_RESET_SOFT;
    }
    if (s_rsr & (RCC_SR_IWDGRSTF | RCC_SR_WWDGRSTF)) {
        cause |= MBOOT_RESET_WDT;
    }
    #if defined(RCC_SR_LPWRRSTF)
    if (s_rsr & RCC_SR_LPWRRSTF) {
        cause |= MBOOT_RESET_OTHER;
    }
    #endif
    // The NRST pin flag is set by every reset on this part, so it only identifies the cause
    // when nothing else does.
    if (cause == 0) {
        cause = (s_rsr & RCC_SR_PINRSTF) ? MBOOT_RESET_PIN : MBOOT_RESET_OTHER;
    }
    // The application finds the reset flags cleared, so it gets them through the backup words.
    mboot_reset_flags_stash(s_rsr, cause);
    RCC->RCC_SR |= RCC_SR_RMVF;
    return cause;
}

bool mboot_port_entry_forced(void) {
    // USER button: sampled for 10 ms, and every sample must read pressed.
    mp_hal_pin_config(MICROPY_HW_USRSW_PIN, MP_HAL_PIN_MODE_INPUT, MICROPY_HW_USRSW_PULL, 0);
    for (int i = 0; i < 5; i++) {
        mboot_port_delay_ms(2);
        if (mp_hal_pin_read(MICROPY_HW_USRSW_PIN) != MICROPY_HW_USRSW_PRESSED) {
            return false;
        }
    }
    return true;
}

// Entered from Reset_Handler in ports/stm32/resethandler.s, after .data and .bss have been
// initialised. The value of r0 at reset is not used.
int main(void);
void stm32_main(uint32_t r0) {
    (void)r0;
    stm32_system_init();
    SystemCoreClockUpdate();
    main();
    for (;;) {
    }
}

// ---- USB ----
//
// The USB hardware (pins, clock gates and the NVIC) is set up by mp_usbd_ll_init() of usbd_conf.c,
// the USB clock by SystemClock_Config(). STM32H5: the USB DRD FS controller (TinyUSB root hub port
// 0) on PA11/PA12. STM32F7: the OTG_HS core (root hub port 1) in full-speed mode with its internal
// full-speed PHY (MICROPY_HW_USB_HS_IN_FS in the board file). The interrupt handlers are those
// of this file.

#if MBOOT_DFU_ENABLE

#if defined(STM32H5)
#define USB_IRQn USB_DRD_FS_IRQn
#define USB_IRQ_HANDLER USB_DRD_FS_IRQHandler
#define USB_RHPORT (0)
#elif defined(STM32F7)
#define USB_IRQn OTG_HS_IRQn
#define USB_IRQ_HANDLER OTG_HS_IRQHandler
#define USB_RHPORT (1)
#else
#error "the USB interrupt and root hub port of this MCU series are not described, see usbd_conf.c"
#endif

void mp_usbd_port_get_serial_number(char *serial_buf);

static bool s_usb_up;

int mboot_port_usb_init(void) {
    mp_usbd_ll_init();
    s_usb_up = true;
    return 0;
}

void mboot_port_usb_deinit(void) {
    if (!s_usb_up) {
        return;
    }
    s_usb_up = false;

    NVIC_DisableIRQ(USB_IRQn);
    if (tud_inited()) {
        // Remove the pull-up so the host sees the device leave.
        tud_disconnect();
    }
    NVIC_ClearPendingIRQ(USB_IRQn);

    #if defined(STM32H5)
    __HAL_RCC_USB_FORCE_RESET();
    __HAL_RCC_USB_RELEASE_RESET();
    __HAL_RCC_USB_CLK_DISABLE();
    HAL_PWREx_DisableVddUSB();
    __HAL_RCC_CRS_FORCE_RESET();
    __HAL_RCC_CRS_RELEASE_RESET();
    __HAL_RCC_CRS_CLK_DISABLE();
    #else
    // Both OTG cores are set up when the board has the full-speed and the high-speed one.
    #if MICROPY_HW_USB_FS
    __HAL_RCC_USB_OTG_FS_FORCE_RESET();
    __HAL_RCC_USB_OTG_FS_RELEASE_RESET();
    __HAL_RCC_USB_OTG_FS_CLK_DISABLE();
    #endif
    __HAL_RCC_USB_OTG_HS_FORCE_RESET();
    __HAL_RCC_USB_OTG_HS_RELEASE_RESET();
    __HAL_RCC_USB_OTG_HS_CLK_DISABLE();
    #endif
}

void mboot_port_usb_serial_number(char *buf, size_t len) {
    // The serial number of the application and of the ST DFU bootloader.
    char serial[13];
    mp_usbd_port_get_serial_number(serial);
    if (len == 0) {
        return;
    }
    size_t n = 0;
    while (n + 1 < len && serial[n] != '\0') {
        buf[n] = serial[n];
        n++;
    }
    buf[n] = '\0';
}

void USB_IRQ_HANDLER(void) {
    tusb_int_handler(USB_RHPORT, true);
}

// TinyUSB without an OS gets its time base from the port.
uint32_t tusb_time_millis_api(void) {
    return HAL_GetTick();
}

#endif
