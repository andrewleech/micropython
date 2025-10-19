"""
ADC Test for STM32H747I-DISCO

Tests ADC functionality on available pins.

Hardware:
- ADC channels available on Arduino headers:
  - A0: PA4  (ADC12_INP18)
  - A1: PF10 (ADC3_INP6)
"""

import unittest
import machine
import time


class TestADC(unittest.TestCase):
    """Test ADC inputs."""

    ADC_PINS = [
        ("A4", "Arduino A0"),
        ("F10", "Arduino A1"),
    ]

    def setUp(self):
        """Initialize ADC channels before each test."""
        self.adcs = []
        for pin, desc in self.ADC_PINS:
            try:
                adc = machine.ADC(pin)
                self.adcs.append((adc, pin, desc))
            except Exception:
                pass

    def tearDown(self):
        """Cleanup after each test."""
        self.adcs = []

    def test_adc_init(self):
        """Test ADC initialization."""
        self.assertTrue(len(self.adcs) > 0, "No ADC channels initialized")

    def test_adc_read(self):
        """Test basic ADC read operations."""
        if not self.adcs:
            self.skipTest("No ADC channels available")

        for adc, pin, desc in self.adcs:
            value = adc.read_u16()
            # Value should be in valid range for 16-bit ADC
            self.assertTrue(value >= 0 and value <= 65535, f"ADC value {value} out of range")

    def test_adc_stability(self):
        """Test ADC reading stability."""
        if not self.adcs:
            self.skipTest("No ADC channels available")

        # Test first channel only for stability
        adc, pin, desc = self.adcs[0]

        samples = []
        for _ in range(100):
            value = adc.read_u16() >> 4  # Convert to 12-bit
            samples.append(value)

        # Calculate statistics
        avg = sum(samples) // len(samples)
        min_val = min(samples)
        max_val = max(samples)
        spread = max_val - min_val

        # Spread should be reasonable (less than 200 counts for stable input)
        self.assertTrue(
            spread < 200,
            f"ADC {pin} unstable: spread={spread} (avg={avg}, min={min_val}, max={max_val})",
        )

    def test_adc_sampling_speed(self):
        """Test ADC sampling speed."""
        if not self.adcs:
            self.skipTest("No ADC channels available")

        adc, pin, desc = self.adcs[0]

        num_samples = 1000
        start = time.ticks_us()

        for _ in range(num_samples):
            adc.read_u16()

        elapsed = time.ticks_diff(time.ticks_us(), start)
        samples_per_sec = (num_samples * 1000000) // elapsed

        # Should be able to sample at least 1kHz
        self.assertTrue(
            samples_per_sec > 1000, f"Sampling rate too slow: {samples_per_sec} samples/sec"
        )

    def test_adc_range(self):
        """Test ADC value range."""
        if not self.adcs:
            self.skipTest("No ADC channels available")

        for adc, pin, desc in self.adcs:
            # Take average of multiple readings
            readings = []
            for _ in range(50):
                readings.append(adc.read_u16() >> 4)  # 12-bit

            avg = sum(readings) // len(readings)

            # Value should be within valid 12-bit range
            self.assertTrue(avg >= 0 and avg <= 4095, f"ADC average {avg} out of 12-bit range")


if __name__ == "__main__":
    unittest.main()
