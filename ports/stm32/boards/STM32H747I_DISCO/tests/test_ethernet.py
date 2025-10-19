"""
Ethernet Test for STM32H747I-DISCO

Tests Ethernet connectivity using the LAN8742A PHY via RMII interface.

Hardware:
- LAN8742A Ethernet PHY
- RMII interface

Prerequisites:
- Ethernet cable connected
- Network with DHCP server available
"""

import unittest
import network
import socket
import time


class TestEthernet(unittest.TestCase):
    """Test Ethernet connectivity."""

    @classmethod
    def setUpClass(cls):
        """Initialize Ethernet interface once for all tests."""
        try:
            cls.lan = network.LAN()
            cls.lan.active(True)

            # Wait for link up (timeout 10 seconds)
            for _ in range(20):
                if cls.lan.isconnected():
                    break
                time.sleep(0.5)

            cls.connected = cls.lan.isconnected()
        except Exception:
            cls.lan = None
            cls.connected = False

    @classmethod
    def tearDownClass(cls):
        """Cleanup Ethernet interface."""
        if cls.lan:
            try:
                cls.lan.active(False)
            except:
                pass

    def test_ethernet_init(self):
        """Test Ethernet interface initialization."""
        self.assertIsNotNone(self.lan, "Ethernet interface not initialized")

    def test_ethernet_link(self):
        """Test Ethernet link status."""
        if not self.lan:
            self.skipTest("Ethernet interface not initialized")

        self.assertTrue(self.connected, "Ethernet not connected - check cable and DHCP")

    def test_ethernet_ip_config(self):
        """Test Ethernet IP configuration."""
        if not self.connected:
            self.skipTest("Ethernet not connected")

        config = self.lan.ifconfig()
        ip, subnet, gateway, dns = config

        # IP should be valid (not 0.0.0.0)
        self.assertNotEqual(ip, "0.0.0.0", "No IP address obtained from DHCP")
        self.assertNotEqual(gateway, "0.0.0.0", "No gateway configured")

    def test_ethernet_dns(self):
        """Test DNS resolution."""
        if not self.connected:
            self.skipTest("Ethernet not connected")

        try:
            addr = socket.getaddrinfo("micropython.org", 80)[0][-1][0]
            # Should get valid IP address
            self.assertIsInstance(addr, str)
            self.assertNotEqual(addr, "0.0.0.0")
        except Exception as e:
            self.fail(f"DNS lookup failed: {e}")

    def test_ethernet_tcp_connect(self):
        """Test TCP connection."""
        if not self.connected:
            self.skipTest("Ethernet not connected")

        try:
            addr = socket.getaddrinfo("micropython.org", 80)[0][-1]
            s = socket.socket()
            s.settimeout(10.0)
            s.connect(addr)
            s.close()
        except Exception as e:
            self.fail(f"TCP connection failed: {e}")


if __name__ == "__main__":
    unittest.main()
