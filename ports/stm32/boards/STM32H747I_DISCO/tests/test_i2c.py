"""
I2C Bus Test for STM32H747I-DISCO

Tests I2C4 bus functionality and detects the on-board WM8994 audio codec.

Hardware:
- I2C4: PD12 (SCL), PD13 (SDA)
- WM8994ECS/R audio codec at address 0x1A

Expected results:
- I2C4 bus initializes successfully
- WM8994 codec detected at address 0x1A (26 decimal)
- Can read device ID from codec registers
"""

import unittest
import machine
import time


class TestI2C(unittest.TestCase):
    """Test I2C4 bus and WM8994 codec."""

    def setUp(self):
        """Initialize I2C4 before each test."""
        self.i2c = machine.I2C(4, freq=100000)

    def tearDown(self):
        """Cleanup after each test."""
        pass

    def test_i2c_init(self):
        """Test I2C4 initialization."""
        self.assertIsNotNone(self.i2c)

    def test_i2c_scan(self):
        """Test I2C4 bus scanning."""
        devices = self.i2c.scan()
        self.assertIsInstance(devices, list)
        self.assertTrue(len(devices) > 0, "No I2C devices found")

    def test_wm8994_detection(self):
        """Test WM8994 audio codec detection."""
        WM8994_ADDR = 0x1A
        devices = self.i2c.scan()
        self.assertIn(WM8994_ADDR, devices, f"WM8994 not found at address 0x{WM8994_ADDR:02X}")

    def test_wm8994_chip_id(self):
        """Test reading WM8994 chip ID."""
        WM8994_ADDR = 0x1A

        # Skip if codec not present
        devices = self.i2c.scan()
        if WM8994_ADDR not in devices:
            self.skipTest("WM8994 not detected")

        # Try to read chip ID (register 0x0000)
        try:
            # WM8994 uses 16-bit register addresses - must specify addrsize=16
            data = self.i2c.readfrom_mem(WM8994_ADDR, 0x0000, 2, addrsize=16)
            chip_id = (data[0] << 8) | data[1]
            # WM8994 should return 0x8994
            self.assertEqual(
                chip_id, 0x8994, f"Unexpected chip ID: 0x{chip_id:04X} (expected 0x8994)"
            )
        except OSError as e:
            self.fail(f"Failed to read chip ID: {e}")

    def test_i2c_speed_100khz(self):
        """Test I2C at 100kHz."""
        i2c_test = machine.I2C(4, freq=100000)
        devices = i2c_test.scan()
        self.assertIsInstance(devices, list)

    def test_i2c_speed_400khz(self):
        """Test I2C at 400kHz."""
        i2c_test = machine.I2C(4, freq=400000)
        devices = i2c_test.scan()
        self.assertIsInstance(devices, list)


if __name__ == "__main__":
    unittest.main()
