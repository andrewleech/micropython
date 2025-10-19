"""
UART Test for STM32H747I-DISCO

Tests UART8 interface with loopback testing.

Hardware:
- UART8: TX=PJ8, RX=PJ9

Prerequisites:
For loopback test: Connect TX to RX (PJ8 to PJ9)
"""

import unittest
import machine
import time


class TestUART(unittest.TestCase):
    """Test UART8 interface."""

    def setUp(self):
        """Initialize UART8 before each test."""
        self.uart = machine.UART(8, baudrate=115200)
        # Clear any existing data
        while self.uart.any():
            self.uart.read()

    def tearDown(self):
        """Cleanup after each test."""
        try:
            self.uart.deinit()
        except:
            pass

    def test_uart_init(self):
        """Test UART8 initialization."""
        self.assertIsNotNone(self.uart)

    def test_uart_config_baudrates(self):
        """Test UART configuration at different baud rates."""
        baudrates = [9600, 19200, 38400, 57600, 115200, 230400]

        for baud in baudrates:
            try:
                uart = machine.UART(8, baudrate=baud)
                uart.deinit()
            except Exception as e:
                self.fail(f"Failed to init UART at {baud} baud: {e}")

    def test_uart_write(self):
        """Test UART write operations."""
        test_data = [
            b"Hello",
            b"\x55\xaa",
            bytes(range(256)),
        ]

        for data in test_data:
            try:
                written = self.uart.write(data)
                self.assertEqual(written, len(data))
            except Exception as e:
                self.fail(f"Failed to write {len(data)} bytes: {e}")

    def test_uart_loopback(self):
        """Test UART loopback (requires TX-RX connection)."""
        # Clear buffer
        while self.uart.any():
            self.uart.read()

        test_pattern = b"Hello"
        self.uart.write(test_pattern)
        time.sleep_ms(50)  # Wait for data

        if self.uart.any():
            rxdata = self.uart.read(len(test_pattern))
            if rxdata == test_pattern:
                # Loopback connected and working
                pass
            else:
                self.fail(f"Loopback data mismatch: sent {test_pattern}, got {rxdata}")
        else:
            # Loopback not connected - this is OK
            self.skipTest("UART loopback not connected (TX-RX)")

    def test_uart_buffer(self):
        """Test UART buffering."""
        # Test uart.any()
        available = self.uart.any()
        self.assertIsInstance(available, int)
        self.assertTrue(available >= 0, f"uart.any() returned {available}")


if __name__ == "__main__":
    unittest.main()
