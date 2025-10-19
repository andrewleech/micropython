"""
DAC Test for STM32H747I-DISCO (MANUAL/INTERACTIVE)

Tests DAC functionality on available pins.
Requires oscilloscope or multimeter for waveform verification.

Hardware:
- DAC1: PA4
- DAC2: PA5
"""

import unittest
import machine
import time


class TestDAC(unittest.TestCase):
    """Test DAC outputs (MANUAL - requires test equipment)."""

    def setUp(self):
        """Initialize DAC channels before each test."""
        try:
            self.dac1 = machine.DAC("A4")
        except Exception:
            self.dac1 = None

    def tearDown(self):
        """Cleanup after each test."""
        if self.dac1:
            try:
                self.dac1.write(0)
            except:
                pass

    def test_dac_init(self):
        """Test DAC initialization."""
        self.assertIsNotNone(self.dac1, "DAC1 (PA4) not initialized")

    def test_dac_write(self):
        """Test DAC write operations."""
        if not self.dac1:
            self.skipTest("DAC not initialized")

        # Write various levels
        levels = [0, 64, 128, 192, 255]
        for level in levels:
            self.dac1.write(level)
            time.sleep_ms(50)

        # Return to 0
        self.dac1.write(0)

    def test_dac_stepped_waveform(self):
        """Test DAC stepped waveform (MANUAL - view on oscilloscope)."""
        if not self.dac1:
            self.skipTest("DAC not initialized")

        print("\nManual test: Generate stepped waveform on PA4 (1 second)")
        steps = 8
        for cycle in range(2):
            for step in range(steps):
                value = (step * 255) // (steps - 1)
                self.dac1.write(value)
                time.sleep_ms(125)

        self.dac1.write(0)


if __name__ == "__main__":
    unittest.main()
