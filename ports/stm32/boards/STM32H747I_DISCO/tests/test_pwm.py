"""
PWM Test for STM32H747I-DISCO (MANUAL/INTERACTIVE)

Tests PWM output and timer functionality.
Requires visual inspection of LED fading.

Hardware:
- LEDs: PI12 (Green), PI13 (Orange), PI14 (Red), PI15 (Blue)
"""

import unittest
import machine
import time


class TestPWM(unittest.TestCase):
    """Test PWM and timers (MANUAL - watch LEDs)."""

    def setUp(self):
        """Initialize PWM channels before each test."""
        pass

    def tearDown(self):
        """Cleanup after each test."""
        pass

    def test_pwm_init(self):
        """Test PWM initialization."""
        try:
            tim = machine.Pin("I12")
            pwm = machine.PWM(tim, freq=1000, duty_u16=0)
            pwm.deinit()
        except Exception as e:
            self.fail(f"PWM initialization failed: {e}")

    def test_pwm_duty_cycle(self):
        """Test PWM duty cycle control (MANUAL - watch LED brightness)."""
        print("\nManual test: Watch LED1 (Green) fade through brightness levels")

        try:
            tim = machine.Pin("I12")
            pwm = machine.PWM(tim, freq=1000)

            duty_levels = [0, 16384, 32768, 49152, 65535]
            for duty in duty_levels:
                pwm.duty_u16(duty)
                time.sleep_ms(300)

            pwm.duty_u16(0)
            pwm.deinit()

        except Exception as e:
            self.fail(f"PWM duty cycle test failed: {e}")

    def test_pwm_fade(self):
        """Test PWM smooth fading (MANUAL - watch LED fade in/out)."""
        print("\nManual test: Watch LED1 (Green) smoothly fade in and out")

        try:
            tim = machine.Pin("I12")
            pwm = machine.PWM(tim, freq=1000)

            # Fade in
            for duty in range(0, 65536, 2048):
                pwm.duty_u16(duty)
                time.sleep_ms(10)

            # Fade out
            for duty in range(65535, 0, -2048):
                pwm.duty_u16(duty)
                time.sleep_ms(10)

            pwm.duty_u16(0)
            pwm.deinit()

        except Exception as e:
            self.fail(f"PWM fade test failed: {e}")


if __name__ == "__main__":
    unittest.main()
