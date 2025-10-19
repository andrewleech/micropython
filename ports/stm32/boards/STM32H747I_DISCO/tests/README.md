# STM32H747I-DISCO Test Suite

unittest-based test scripts for validating hardware peripherals and MicroPython functionality on the STM32H747I-DISCO board.

## Prerequisites

Install unittest module:
```bash
mpremote connect /dev/ttyACM5 mip install unittest
```

## Quick Start

### Running Automated Tests (Recommended)

Automated tests run without user interaction - no need to watch LEDs, press buttons, etc.

```bash
# Run all automated tests
mpremote connect /dev/ttyACM5 run run_all_tests.py

# Or from Python REPL:
import run_all_tests
run_all_tests.run_automated()
```

### Running Manual/Interactive Tests

Manual tests require user interaction: watching LEDs, pressing buttons, verifying waveforms.

```python
import run_all_tests
run_all_tests.run_manual()
```

### Running All Tests

```python
import run_all_tests
run_all_tests.run_all()
```

### Running Individual Tests

```bash
# Run specific test module
mpremote connect /dev/ttyACM5 run test_i2c.py
mpremote connect /dev/ttyACM5 run test_ethernet.py
```

## Test Organization

### Automated Tests (6 modules)

**test_i2c.py** - I2C4 bus and WM8994 codec
- No external hardware required
- Tests I2C initialization, scanning, codec detection, speeds

**test_adc.py** - ADC inputs
- No external hardware required
- Tests initialization, reading, stability, sampling speed

**test_spi.py** - SPI5 interface
- Optional: jumper wire PJ10 to PJ11 for loopback
- Tests initialization, modes, write, loopback

**test_uart.py** - UART8 interface
- Optional: jumper wire PJ8 to PJ9 for loopback
- Tests initialization, baudrates, write, loopback

**test_sdcard.py** - SD card filesystem
- Requires: SD card (FAT32 formatted)
- Tests mount, read/write, file operations

**test_ethernet.py** - Ethernet connectivity
- Requires: Ethernet cable + DHCP network
- Tests link, DHCP, DNS, TCP connection

### Manual/Interactive Tests (3 modules)

**test_peripherals_basic.py** - LEDs, button, joystick
- User interaction: Watch LEDs blink
- Tests initialization, LED control

**test_dac.py** - DAC outputs
- Optional: oscilloscope/multimeter
- Tests initialization, write, waveforms

**test_pwm.py** - PWM and timers
- User interaction: Watch LEDs fade
- Tests initialization, duty cycle, fading

## Hardware Setup

### No External Hardware Needed
- test_i2c.py - Uses on-board WM8994 codec
- test_adc.py - Reads Arduino header pins

### External Hardware Required
- test_ethernet.py - Ethernet cable + DHCP network
- test_sdcard.py - MicroSD card (FAT32)

### Optional Hardware (for full validation)
- test_spi.py - Jumper wire PJ10 to PJ11
- test_uart.py - Jumper wire PJ8 to PJ9
- test_dac.py - Oscilloscope or multimeter

## unittest Framework

All tests use Python's unittest framework with these features:

**Test Structure:**
```python
import unittest
import machine

class TestFoo(unittest.TestCase):
    def setUp(self):
        """Initialize before each test."""
        self.device = machine.Device()

    def tearDown(self):
        """Cleanup after each test."""
        pass

    def test_something(self):
        """Test description."""
        self.assertTrue(condition)
        self.assertEqual(a, b)
        self.assertIn(item, list)

if __name__ == '__main__':
    unittest.main()
```

**Supported Assertions:**
- `assertTrue(x)` / `assertFalse(x)`
- `assertEqual(a, b)` / `assertNotEqual(a, b)`
- `assertIsNone(x)` / `assertIsNotNone(x)`
- `assertIn(a, b)` / `assertNotIn(a, b)`
- `assertIsInstance(obj, class)`
- `skipTest(reason)` - Skip test conditionally
- `fail(msg)` - Explicitly fail test

**Test Output:**
```
test_i2c_init (__main__.TestI2C) ... ok
test_i2c_scan (__main__.TestI2C) ... ok
test_wm8994_detection (__main__.TestI2C) ... ok
...
----------------------------------------------------------------------
Ran 6 tests

OK
```

## Pin Reference

### LEDs
- LED1 (Green):  PI12
- LED2 (Orange): PI13
- LED3 (Red):    PI14
- LED4 (Blue):   PI15

### Buttons & Joystick
- User Button: PC13
- Joystick SEL:   PK2
- Joystick DOWN:  PK3
- Joystick LEFT:  PK4
- Joystick RIGHT: PK5
- Joystick UP:    PK6

### Communication Interfaces
- I2C4:    PD12 (SCL), PD13 (SDA)
- SPI5:    PK0 (SCK), PK1 (NSS), PJ10 (MOSI), PJ11 (MISO)
- UART8:   PJ8 (TX), PJ9 (RX)
- SDMMC1:  8-bit bus, detect on PI8
- Ethernet: RMII interface

### Analog
- ADC A0: PA4
- ADC A1: PF10
- DAC1:   PA4
- DAC2:   PA5

## Troubleshooting

### unittest not found
```bash
mpremote connect /dev/ttyACM5 mip install unittest
```

### Tests fail with "No module named 'foo'"
Ensure tests are in the same directory or update `sys.path`

### SD card tests fail
- Verify SD card is inserted
- Check card is formatted as FAT32
- Try reformatting card

### Ethernet tests fail
- Verify cable is connected
- Check DHCP server is available on network
- Try pinging gateway manually

### Loopback tests skipped
- SPI/UART loopback tests are optional
- Connect jumper wires if full validation needed
- Tests will skip gracefully if loopback not connected

## Contributing

When adding new tests:
1. Inherit from `unittest.TestCase`
2. Use `setUp()` for initialization
3. Use `tearDown()` for cleanup
4. Use supported assertions only
5. Add test to `run_all_tests.py` in appropriate category
6. Update this README

## License

Same as MicroPython project (MIT License)
