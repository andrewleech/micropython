# Debug script for static IP isconnected() issue
import network
import time

print("=== Static IP isconnected() Debug ===")

# Create LAN interface
eth = network.LAN()
print("Initial state:")
print(f"  active(): {eth.active()}")
print(f"  status(): {eth.status()}")
print(f"  isconnected(): {eth.isconnected()}")
print()

# Set static IP
print("Setting static IP...")
eth.ipconfig(addr4='192.168.0.100/24', gw4='192.168.0.1')
current_ip = eth.ipconfig("addr4")
print(f"  IP configured: {current_ip}")
print(f"  status(): {eth.status()}")
print(f"  isconnected(): {eth.isconnected()}")
print()

# Activate interface
print("Calling active(True)...")
eth.active(True)
print(f"  active(): {eth.active()}")
print(f"  status(): {eth.status()}")
print(f"  isconnected(): {eth.isconnected()}")

# Check IP address
current_ip = eth.ipconfig("addr4")
print(f"  IP after active: {current_ip}")
print()

# Wait and monitor status changes
print("Monitoring status for 10 seconds...")
for i in range(10):
    status = eth.status()
    connected = eth.isconnected()
    ip = eth.ipconfig("addr4")

    print(f"  {i + 1:2d}s: status={status}, connected={connected}, IP={ip}")

    if connected:
        print("  ✅ Connection established!")
        break

    time.sleep(1)

print()
print("Status meanings:")
print("  0 = link down (cable disconnected)")
print("  1 = link up (cable connected, establishing)")
print("  2 = link up, no IP")
print("  3 = link up with IP - fully connected")
print()
print("For isconnected() to return True, status must be 3")
print("This requires: interface up + link up + non-zero IP")

# Additional diagnostics
print()
print("=== Additional Diagnostics ===")
print("Try unplugging and replugging the cable...")
print("Watch for status changes:")

last_status = eth.status()
for i in range(20):
    status = eth.status()
    connected = eth.isconnected()

    if status != last_status:
        print(f"*** STATUS CHANGE: {last_status} -> {status} ***")
        if status == 0:
            print("    Cable unplugged")
        elif status > 0 and last_status == 0:
            print("    Cable plugged in")
        last_status = status

    if connected:
        print(f"  {i + 1:2d}s: ✅ CONNECTED (status={status})")
        break
    else:
        print(f"  {i + 1:2d}s: status={status}, not connected")

    time.sleep(1)

print("\nDiagnostic complete!")
