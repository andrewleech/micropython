# Test script to verify active(True) works without cable connected
import network
import time

print("Testing active(True) without cable connected")
print("============================================")

# Create LAN interface
eth = network.LAN()
print("After creating LAN object:")
print(f"  eth.active(): {eth.active()}")
print(f"  eth.status(): {eth.status()}")
print()

# Test: Call active(True) WITHOUT cable connected
# This should succeed quickly and not timeout
print("Testing active(True) without cable (should NOT timeout)...")
start_time = time.ticks_ms()

try:
    eth.active(True)
    end_time = time.ticks_ms()
    duration = time.ticks_diff(end_time, start_time)

    print(f"✅ SUCCESS: active(True) completed in {duration}ms")
    print(f"  eth.active(): {eth.active()}")
    print(f"  eth.status(): {eth.status()}")
    print(f"  eth.isconnected(): {eth.isconnected()}")

except Exception as e:
    end_time = time.ticks_ms()
    duration = time.ticks_diff(end_time, start_time)
    print(f"❌ FAILED: active(True) failed after {duration}ms")
    print(f"  Error: {e}")

print()
print("Now plug in the Ethernet cable and watch for automatic detection...")
print("The status should change from 0 to 1, 2, or 3 when cable is connected")
print()

# Monitor for cable connection
last_status = eth.status()
for i in range(30):  # Monitor for 30 seconds
    current_status = eth.status()
    connected = eth.isconnected()

    if current_status != last_status:
        print(f"*** STATUS CHANGE at {time.ticks_ms()}ms ***")
        print(f"  Status changed from {last_status} to {current_status}")
        if current_status == 0:
            print("  🔴 Cable disconnected")
        elif current_status > 0 and last_status == 0:
            print("  🟢 Cable connected!")
        last_status = current_status

    if connected:
        config = eth.ifconfig()
        print(f"  Connected! IP: {config[0]}")
        break

    print(f"  Time {i + 1:2d}s: status={current_status}, connected={connected}")
    time.sleep(1)

print()
print("Test results:")
print("- active(True) should complete quickly even without cable")
print("- status() should be 0 when cable is unplugged")
print("- status() should change when cable is plugged in")
print("- Connection should work once cable is connected")

print()
print("Testing active(False) and active(True) cycle...")
eth.active(False)
print(f"After active(False): active()={eth.active()}, status()={eth.status()}")

eth.active(True)
print(f"After active(True): active()={eth.active()}, status()={eth.status()}")

print("\nTest complete!")
