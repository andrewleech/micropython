"""
Basic Peripherals Test for STM32H747I-DISCO (MANUAL/INTERACTIVE)

Tests basic GPIO peripherals: LEDs, button, and joystick.
Requires user interaction.

Hardware:
- LEDs: PI12 (Green), PI13 (Orange), PI14 (Red), PI15 (Blue)
- User button: PC13
- Joystick: PK2 (SEL), PK3 (DOWN), PK4 (LEFT), PK5 (RIGHT), PK6 (UP)
"""

import unittest
import machine
import time


class TestBasicPeripherals(unittest.TestCase):
    """Test basic peripherals (MANUAL - requires user interaction)."""

    def setUp(self):
        """Initialize peripherals before each test."""
        self.leds = [
            machine.Pin("I12", machine.Pin.OUT, value=0),
            machine.Pin("I13", machine.Pin.OUT, value=0),
            machine.Pin("I14", machine.Pin.OUT, value=0),
            machine.Pin("I15", machine.Pin.OUT, value=0),
        ]

    def tearDown(self):
        """Cleanup after each test."""
        # Turn off all LEDs
        for led in self.leds:
            led.value(0)

    def test_led_init(self):
        """Test LED initialization."""
        self.assertEqual(len(self.leds), 4)

    def test_led_blink(self):
        """Test LED blinking (MANUAL - watch LEDs)."""
        print("\nManual test: Watch LEDs blink in sequence")
        for i, led in enumerate(self.leds):
            led.value(1)
            time.sleep_ms(200)
            led.value(0)
            time.sleep_ms(100)

    def test_button_init(self):
        """Test button initialization."""
        button = machine.Pin("C13", machine.Pin.IN, machine.Pin.PULL_UP)
        self.assertIsNotNone(button)

    def test_joystick_init(self):
        """Test joystick initialization."""
        joy_pins = ["K2", "K3", "K4", "K5", "K6"]
        joystick = [machine.Pin(p, machine.Pin.IN, machine.Pin.PULL_UP) for p in joy_pins]
        self.assertEqual(len(joystick), 5)


if __name__ == "__main__":
    unittest.main()
