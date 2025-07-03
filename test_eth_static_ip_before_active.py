# Test script to verify static IP can be set before active(True)
import network
import time

print("Testing static IP configuration before active(True)")
print("==================================================")

# Create LAN interface (should initialize netif but not start networking)
eth = network.LAN()
print("After creating LAN object:")
print(f"  eth.active(): {eth.active()}")
print(f"  eth.status(): {eth.status()}")
print(f"  eth.isconnected(): {eth.isconnected()}")

# Check initial IP configuration
try:
    config = eth.ifconfig()
    print(f"  Initial IP config: {config}")
except Exception as e:
    print(f"  Error getting initial config: {e}")

print()

# Set static IP BEFORE calling active(True)
print("Setting static IP before active(True)...")
try:
    eth.ipconfig(addr='192.168.1.100', subnet='255.255.255.0', gw='192.168.1.1', dns='8.8.8.8')
    config = eth.ifconfig()
    print(f"  Static IP set: {config}")
except Exception as e:
    print(f"  Error setting static IP: {e}")

print()

# Now enable the interface
print("Calling eth.active(True)...")
eth.active(True)
print("After eth.active(True):")
print(f"  eth.active(): {eth.active()}")
print(f"  eth.status(): {eth.status()}")
print(f"  eth.isconnected(): {eth.isconnected()}")

# Check if static IP is preserved
config = eth.ifconfig()
print(f"  IP config after active(True): {config}")
print()

# Wait a bit for link to come up
print("Waiting 5 seconds for link to establish...")
for i in range(5):
    status = eth.status()
    connected = eth.isconnected()
    print(f"  Time {i + 1}: status()={status}, isconnected()={connected}")
    time.sleep(1)

print()
print("Final state:")
print(f"  eth.active(): {eth.active()}")
print(f"  eth.status(): {eth.status()}")
print(f"  eth.isconnected(): {eth.isconnected()}")
config = eth.ifconfig()
print(f"  Final IP config: {config}")

print()
print("Expected behavior:")
print("- Should be able to set static IP before active(True)")
print("- Static IP should be preserved after active(True)")
print("- DHCP should NOT start if static IP is already configured")
print("- Interface should connect with the static IP")

print()
print("Test for DHCP behavior:")
print("====================")

# Test DHCP behavior - disable interface first
print("Disabling interface...")
eth.active(False)
print(f"  eth.active(): {eth.active()}")

# Clear IP configuration (set to DHCP)
print("Setting IP to 0.0.0.0 (DHCP mode)...")
eth.ipconfig(addr='0.0.0.0', subnet='255.255.255.0', gw='192.168.1.1', dns='8.8.8.8')
config = eth.ifconfig()
print(f"  IP config set to DHCP: {config}")

print("Enabling interface again (should start DHCP)...")
eth.active(True)
print(f"  eth.active(): {eth.active()}")

# Wait for DHCP
print("Waiting 10 seconds for DHCP...")
for i in range(10):
    status = eth.status()
    connected = eth.isconnected()
    config = eth.ifconfig()
    print(f"  Time {i + 1}: status()={status}, connected={connected}, IP={config[0]}")
    time.sleep(1)

print("\nTest complete!")
