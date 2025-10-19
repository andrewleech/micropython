"""
Master Test Runner for STM32H747I-DISCO

Runs all test suites using unittest framework and generates reports.

Usage:
    import run_all_tests

    # Run only automated tests (recommended - no user interaction)
    run_all_tests.run_automated()

    # Run only manual/interactive tests
    run_all_tests.run_manual()

    # Run all tests
    run_all_tests.run_all()

Or run individual test modules:
    mpremote run test_i2c.py
    mpremote run test_ethernet.py
"""

import unittest
import sys
import os

# Test suite classifications
AUTOMATED_TESTS = [
    "test_i2c",
    "test_adc",
    "test_spi",
    "test_uart",
    "test_sdcard",
    "test_ethernet",
]

MANUAL_TESTS = [
    "test_peripherals_basic",
    "test_dac",
    "test_pwm",
]


def run_tests(test_modules):
    """
    Run specified test modules using unittest.

    Args:
        test_modules: List of test module names to run
    """
    # Log system information
    print("\n" + "=" * 70)
    print("SYSTEM INFORMATION")
    print("=" * 70)
    uname = os.uname()
    print(f"sysname:  {uname.sysname}")
    print(f"nodename: {uname.nodename}")
    print(f"release:  {uname.release}")
    print(f"version:  {uname.version}")
    print(f"machine:  {uname.machine}")
    print("=" * 70)
    print()

    # Track overall results
    total_tests = 0
    total_failures = 0
    total_errors = 0
    total_skipped = 0

    # Run each test module
    for module_name in test_modules:
        try:
            print(f"\n{'=' * 70}")
            print(f"Running: {module_name}")
            print("=" * 70)

            # Import and run the module
            module = __import__(module_name)

            # Find all TestCase classes and run them
            for item_name in dir(module):
                item = getattr(module, item_name)
                if (
                    isinstance(item, type)
                    and issubclass(item, unittest.TestCase)
                    and item is not unittest.TestCase
                ):
                    # Run each test method manually
                    for method_name in dir(item):
                        if method_name.startswith("test_"):
                            test_instance = item()
                            test_method = getattr(test_instance, method_name)
                            result = unittest.TestResult()

                            # Run setUp
                            try:
                                test_instance.setUp()
                            except Exception as e:
                                print(f"{method_name} ... ERROR (setUp)")
                                result.errors.append((test_instance, str(e)))
                                total_errors += 1
                                total_tests += 1
                                continue

                            # Run test
                            try:
                                test_method()
                                print(f"{method_name} ... ok")
                                total_tests += 1
                            except unittest.SkipTest as e:
                                print(f"{method_name} ... skipped: {e}")
                                total_skipped += 1
                                total_tests += 1
                            except AssertionError as e:
                                print(f"{method_name} ... FAIL")
                                print(f"  {e}")
                                total_failures += 1
                                total_tests += 1
                            except Exception as e:
                                print(f"{method_name} ... ERROR")
                                print(f"  {e}")
                                total_errors += 1
                                total_tests += 1

                            # Run tearDown
                            try:
                                test_instance.tearDown()
                            except Exception as e:
                                print(f"  tearDown error: {e}")

        except ImportError as e:
            print(f"Warning: Could not import {module_name}: {e}")
        except Exception as e:
            print(f"Error loading tests from {module_name}: {e}")

    # Print summary
    print("\n" + "=" * 70)
    print("TEST SUMMARY")
    print("=" * 70)
    print(f"Tests run: {total_tests}")
    print(f"Failures: {total_failures}")
    print(f"Errors: {total_errors}")
    print(f"Skipped: {total_skipped}")

    if total_failures == 0 and total_errors == 0:
        print("\n✓ ALL TESTS PASSED")
    else:
        print(f"\n✗ {total_failures + total_errors} TEST(S) FAILED")

    print("=" * 70)

    # Return a result-like object
    class ResultSummary:
        def __init__(self, run, failures, errors, skipped):
            self.testsRun = run
            self.failures = failures
            self.errors = errors
            self.skipped = skipped

        def wasSuccessful(self):
            return self.failures == 0 and self.errors == 0

    return ResultSummary(total_tests, total_failures, total_errors, total_skipped)


def run_automated():
    """
    Run only automated tests (no user interaction required).

    These tests run without needing to watch LEDs, press buttons, etc.
    Includes: I2C, ADC, SPI, UART, SD card, Ethernet
    """
    print("\n*** AUTOMATED TESTS ONLY (No user interaction required) ***\n")
    return run_tests(AUTOMATED_TESTS)


def run_manual():
    """
    Run only manual/interactive tests.

    These tests require user interaction: watching LEDs, pressing buttons,
    verifying waveforms with oscilloscope, etc.
    Includes: Basic peripherals, DAC, PWM
    """
    print("\n*** MANUAL/INTERACTIVE TESTS (User interaction required) ***\n")
    return run_tests(MANUAL_TESTS)


def run_all():
    """Run all tests (automated + manual)."""
    print("\n*** RUNNING ALL TESTS ***\n")
    return run_tests(AUTOMATED_TESTS + MANUAL_TESTS)


if __name__ == "__main__":
    # Run automated tests by default (since they don't need user interaction)
    run_automated()
