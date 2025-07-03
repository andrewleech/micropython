# Test script for STM32 Ethernet link change detection
import network
import time

# Create and configure Ethernet interface
eth = network.LAN()
eth.active(True)

print("Ethernet Link Change Test")
print("========================")
print("This script monitors for Ethernet cable connect/disconnect events")
print("Try plugging/unplugging the Ethernet cable to test the functionality")
print()


def check_link_status():
    """Check and display current link status"""
    connected = eth.isconnected()
    status = eth.status()

    # Status meanings:
    # 0 = link down (cable disconnected)
    # 1 = link up (cable connected, establishing)
    # 2 = link up, no IP
    # 3 = link up with IP configured

    status_text = {
        0: "Cable disconnected",
        1: "Cable connected, establishing link",
        2: "Link up, no IP",
        3: "Link up with IP - fully connected",
    }

    print(f"Status: {status} ({status_text.get(status, 'Unknown')})")
    print(f"isconnected(): {connected}")

    if connected:
        config = eth.ifconfig()
        print(f"IP: {config[0]}")

    return status, connected


# Initial status
print("Initial status:")
last_status, last_connected = check_link_status()
print()

print("Monitoring for changes (Ctrl+C to exit)...")
print("=" * 50)

try:
    while True:
        # Check status every 500ms
        current_status, current_connected = check_link_status()

        # Detect changes
        if current_status != last_status or current_connected != last_connected:
            print(f"\n*** CHANGE DETECTED at {time.ticks_ms()} ms ***")

            if current_status == 0 and last_status != 0:
                print("🔴 Cable DISCONNECTED!")
            elif current_status > 0 and last_status == 0:
                print("🟢 Cable CONNECTED!")
            elif current_connected and not last_connected:
                print("🌐 Network CONNECTION established!")
            elif not current_connected and last_connected:
                print("❌ Network CONNECTION lost!")

            print()

            last_status = current_status
            last_connected = current_connected
        else:
            # Just show a dot for activity
            print(".", end="")

        time.sleep_ms(500)

except KeyboardInterrupt:
    print("\n\nTest completed.")
    print("Final status:")
    check_link_status()
