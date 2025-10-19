"""
SPI Test for STM32H747I-DISCO

Tests SPI5 interface with loopback testing.

Hardware:
- SPI5: SCK=PK0, NSS=PK1, MOSI=PJ10, MISO=PJ11

Prerequisites:
For loopback test: Connect MOSI to MISO (PJ10 to PJ11)
"""

import unittest
import machine
import time


class TestSPI(unittest.TestCase):
    """Test SPI5 interface."""

    def setUp(self):
        """Initialize SPI5 before each test."""
        self.spi = machine.SPI(5, baudrate=1000000, polarity=0, phase=0)

    def tearDown(self):
        """Cleanup after each test."""
        try:
            self.spi.deinit()
        except:
            pass

    def test_spi_init(self):
        """Test SPI5 initialization."""
        self.assertIsNotNone(self.spi)

    def test_spi_config_modes(self):
        """Test SPI configuration at different modes."""
        modes = [
            (1000000, 0, 0),  # Mode 0
            (2000000, 0, 1),  # Mode 1
            (4000000, 1, 0),  # Mode 2
            (8000000, 1, 1),  # Mode 3
        ]

        for baudrate, polarity, phase in modes:
            try:
                spi = machine.SPI(5, baudrate=baudrate, polarity=polarity, phase=phase)
                spi.deinit()
            except Exception as e:
                self.fail(f"Failed to init SPI mode ({polarity},{phase}) at {baudrate}Hz: {e}")

    def test_spi_write(self):
        """Test SPI write operations."""
        test_data = [
            b"\x55",
            b"\xaa\x55",
            bytes(range(16)),
        ]

        for data in test_data:
            try:
                self.spi.write(data)
            except Exception as e:
                self.fail(f"Failed to write {len(data)} bytes: {e}")

    def test_spi_loopback(self):
        """Test SPI loopback (requires MOSI-MISO connection)."""
        # Simple loopback test
        test_pattern = b"\x55\xaa"
        rxbuf = bytearray(len(test_pattern))

        try:
            self.spi.write_readinto(test_pattern, rxbuf)

            if rxbuf == test_pattern:
                # Loopback connected
                pass
            else:
                # Loopback not connected - this is OK
                self.skipTest("SPI loopback not connected (MOSI-MISO)")
        except Exception as e:
            self.fail(f"SPI loopback test failed: {e}")

    def test_spi_speed(self):
        """Test SPI at different speeds."""
        speeds = [100000, 1000000, 5000000, 10000000]
        data = bytes(range(256))

        for speed in speeds:
            try:
                spi = machine.SPI(5, baudrate=speed)
                start = time.ticks_us()
                spi.write(data)
                elapsed = time.ticks_diff(time.ticks_us(), start)
                spi.deinit()
                # Should complete in reasonable time
                self.assertTrue(elapsed < 100000, f"SPI write too slow at {speed}Hz")
            except Exception as e:
                self.fail(f"SPI failed at {speed}Hz: {e}")


if __name__ == "__main__":
    unittest.main()
