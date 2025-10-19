"""
SD Card Test for STM32H747I-DISCO

Tests SD card interface and filesystem operations.

Hardware:
- SDMMC1 interface
- Card detect pin: PI8

Prerequisites:
- SD card inserted in slot
- Card formatted as FAT32
"""

import unittest
import os
import pyb
import time


class TestSDCard(unittest.TestCase):
    """Test SD card interface."""

    def setUp(self):
        """Initialize SD card before each test."""
        try:
            self.sd = pyb.SDCard()
            # Check if SD card is already mounted
            try:
                os.listdir("/sd")
                self.mounted = True
            except OSError:
                # Not mounted, try to mount it
                os.mount(self.sd, "/sd")
                self.mounted = True
        except Exception:
            self.sd = None
            self.mounted = False

    def tearDown(self):
        """Cleanup after each test."""
        # SD card is auto-mounted by boot, don't umount
        pass

    def test_sdcard_init(self):
        """Test SD card initialization."""
        self.assertIsNotNone(self.sd, "SD card not initialized - check card is inserted")

    def test_sdcard_mount(self):
        """Test SD card mount."""
        self.assertTrue(self.mounted, "SD card not mounted - check card format (FAT32)")

    def test_sdcard_list(self):
        """Test listing SD card directory."""
        if not self.mounted:
            self.skipTest("SD card not mounted")

        files = os.listdir("/sd")
        self.assertIsInstance(files, list)

    def test_sdcard_write_read(self):
        """Test SD card write and read operations."""
        if not self.mounted:
            self.skipTest("SD card not mounted")

        test_file = "/sd/unittest_test.txt"
        test_data = "STM32H747I-DISCO SD Card Test\n"

        try:
            # Write
            with open(test_file, "w") as f:
                f.write(test_data)

            # Read
            with open(test_file, "r") as f:
                read_data = f.read()

            # Verify
            self.assertEqual(read_data, test_data)

            # Cleanup
            os.remove(test_file)

        except Exception as e:
            self.fail(f"SD card read/write failed: {e}")

    def test_sdcard_file_operations(self):
        """Test SD card file operations."""
        if not self.mounted:
            self.skipTest("SD card not mounted")

        test_dir = "/sd/unittest_dir"
        test_file = test_dir + "/test.txt"

        try:
            # Create directory
            try:
                os.mkdir(test_dir)
            except OSError:
                pass  # May already exist

            # Write file
            with open(test_file, "w") as f:
                f.write("test")

            # Stat file
            stat = os.stat(test_file)
            self.assertTrue(stat[6] > 0, "File size should be > 0")

            # Remove file
            os.remove(test_file)

            # Remove directory
            os.rmdir(test_dir)

        except Exception as e:
            # Cleanup on failure
            try:
                os.remove(test_file)
            except:
                pass
            try:
                os.rmdir(test_dir)
            except:
                pass
            self.fail(f"SD card file operations failed: {e}")


if __name__ == "__main__":
    unittest.main()
