# Test script to verify network.LAN().active() behavior
import network
import time

print("Testing network.LAN().active() behavior")
print("======================================")

# Create LAN interface (should not start it automatically)
eth = network.LAN()
print("After creating LAN object:")
print(f"  eth.active(): {eth.active()}")
print(f"  eth.status(): {eth.status()}")
print(f"  eth.isconnected(): {eth.isconnected()}")
print()

# Enable the interface
print("Calling eth.active(True)...")
eth.active(True)
print("After eth.active(True):")
print(f"  eth.active(): {eth.active()}")
print(f"  eth.status(): {eth.status()}")
print(f"  eth.isconnected(): {eth.isconnected()}")
print()

# Wait a bit for link to come up
print("Waiting 3 seconds for link to establish...")
time.sleep(3)

print("After waiting:")
print(f"  eth.active(): {eth.active()}")
print(f"  eth.status(): {eth.status()}")
print(f"  eth.isconnected(): {eth.isconnected()}")
print()

print("Now try unplugging the Ethernet cable...")
print("eth.active() should remain True even with cable unplugged")
print("eth.status() should change to 0 when cable is unplugged")
print()

# Monitor for 10 seconds
for i in range(10):
    active = eth.active()
    status = eth.status()
    connected = eth.isconnected()
    print(f"  Time {i + 1}: active()={active}, status()={status}, isconnected()={connected}")
    time.sleep(1)

print()
print("Disabling interface with eth.active(False)...")
eth.active(False)
print("After eth.active(False):")
print(f"  eth.active(): {eth.active()}")
print(f"  eth.status(): {eth.status()}")
print(f"  eth.isconnected(): {eth.isconnected()}")
print()

print("Test complete!")
print()
print("Expected behavior:")
print("- eth.active() should be False initially")
print("- eth.active() should be True after eth.active(True), regardless of cable status")
print("- eth.active() should be False after eth.active(False)")
print("- eth.status() should reflect physical cable connection state")
print("- eth.isconnected() should be True only when active AND has IP address")
