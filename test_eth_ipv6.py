# Test script for STM32 Ethernet IPv6 support
import network
import socket
import time

# Create and configure Ethernet interface
eth = network.ETH()
eth.active(True)

# Wait for link to be up
print("Waiting for Ethernet link...")
while not eth.isconnected():
    time.sleep_ms(100)

print("Ethernet connected")
print("MAC address:", ':'.join('%02x' % b for b in eth.config('mac')))

# Get network configuration
print("\nIPv4 configuration:")
print("  IP:", eth.ifconfig()[0])
print("  Netmask:", eth.ifconfig()[1])
print("  Gateway:", eth.ifconfig()[2])
print("  DNS:", eth.ifconfig()[3])

# Try to get IPv6 addresses
try:
    import lwip

    print("\nIPv6 configuration:")

    # Check if IPv6 is enabled in the build
    if hasattr(lwip, 'IPV6'):
        print("  IPv6 support: Enabled")

        # Get IPv6 addresses (if the method exists)
        if hasattr(eth, 'ifconfig6'):
            ipv6_addrs = eth.ifconfig6()
            if ipv6_addrs:
                for addr in ipv6_addrs:
                    print(f"  IPv6 address: {addr}")
            else:
                print("  No IPv6 addresses configured")
        else:
            print("  ifconfig6() method not available")
    else:
        print("  IPv6 support: Not enabled in this build")

except ImportError:
    print("\nLWIP module not available")
except Exception as e:
    print(f"\nError checking IPv6: {e}")

# Test DNS resolution (IPv4 and IPv6)
print("\nDNS resolution test:")
try:
    # IPv4 resolution
    addr = socket.getaddrinfo("micropython.org", 80, socket.AF_INET)[0][-1]
    print(f"  IPv4: micropython.org -> {addr[0]}")

    # IPv6 resolution (if supported)
    try:
        addr6 = socket.getaddrinfo("ipv6.google.com", 80, socket.AF_INET6)[0][-1]
        print(f"  IPv6: ipv6.google.com -> {addr6[0]}")
    except:
        print("  IPv6: DNS resolution not available")

except Exception as e:
    print(f"  Error: {e}")

print("\nTest complete.")
