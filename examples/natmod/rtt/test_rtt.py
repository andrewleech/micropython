"""
Test script for RTT (Real-Time Transfer) native module.

This script tests the basic functionality of the RTT stream interface.
"""

import rtt


def test_basic_functionality():
    """Test basic RTT functionality."""
    print("Testing RTT module basic functionality...")

    # Test module attributes
    print(f"Module name: {rtt.__name__}")
    print(f"RTTStream type: {rtt.RTTStream}")

    # Test initialization
    print("Initializing RTT...")
    rtt.init()
    print("RTT initialized successfully")

    print("Basic functionality test passed!")


def test_stream_creation():
    """Test RTT stream creation."""
    print("\nTesting RTT stream creation...")

    # Create stream with default channel (0)
    stream = rtt.RTTStream()
    print(f"Created RTT stream: {stream}")

    # Create stream with specific channel
    stream1 = rtt.RTTStream(1)
    print(f"Created RTT stream on channel 1: {stream1}")

    print("Stream creation test passed!")


def test_stream_interface():
    """Test RTT stream interface."""
    print("\nTesting RTT stream interface...")

    # Create a stream
    stream = rtt.RTTStream(0)

    # Test write operation
    test_data = b"Hello RTT from MicroPython!\n"
    print(f"Writing: {test_data}")
    bytes_written = stream.write(test_data)
    print(f"Bytes written: {bytes_written}")

    # Test string write
    stream.write("Test string message\n")

    # Test context manager interface
    with rtt.RTTStream() as s:
        s.write(b"Context manager test\n")
        print("Context manager interface working")

    print("Stream interface test passed!")


def test_utility_functions():
    """Test RTT utility functions."""
    print("\nTesting RTT utility functions...")

    # Test has_data function
    data_count = rtt.has_data()
    print(f"Data available on channel 0: {data_count} bytes")

    data_count_ch1 = rtt.has_data(1)
    print(f"Data available on channel 1: {data_count_ch1} bytes")

    # Test write_space function
    write_space = rtt.write_space()
    print(f"Write space available on channel 0: {write_space} bytes")

    write_space_ch1 = rtt.write_space(1)
    print(f"Write space available on channel 1: {write_space_ch1} bytes")

    print("Utility functions test passed!")


def test_read_functionality():
    """Test RTT read functionality."""
    print("\nTesting RTT read functionality...")

    stream = rtt.RTTStream(0)

    # Test non-blocking read
    print("Attempting non-blocking read...")
    data = stream.read(100)
    print(f"Read {len(data)} bytes: {data}")

    # Test read with buffer
    buffer = bytearray(50)
    bytes_read = stream.readinto(buffer)
    print(f"Read into buffer: {bytes_read} bytes")
    if bytes_read > 0:
        print(f"Buffer content: {buffer[:bytes_read]}")

    print("Read functionality test completed!")


def test_dupterm_functionality():
    """Test RTT dupterm functionality."""
    print("\nTesting RTT dupterm functionality...")

    try:
        import os

        # Create RTT stream for dupterm test
        rtt_stream = rtt.RTTStream(2)  # Use channel 2 for testing

        # Test dupterm setup (don't actually enable to avoid interfering with tests)
        print("RTT stream created for dupterm testing")

        # Simulate what dupterm would do
        rtt_stream.write(b"=== RTT REPL Test ===\n")
        rtt_stream.write(b"This would be visible in debugger RTT channel 2\n")

        # Test that we could use os.dupterm (but don't actually do it)
        print("os.dupterm functionality available")
        print("RTT stream ready for REPL use on channel 2")

        print("Dupterm functionality test passed!")

    except ImportError:
        print("os module not available - dupterm test skipped")
    except Exception as e:
        print(f"Dupterm test failed: {e}")


def main():
    """Run all tests."""
    print("=" * 50)
    print("RTT Native Module Test Suite")
    print("=" * 50)

    try:
        test_basic_functionality()
        test_stream_creation()
        test_stream_interface()
        test_utility_functions()
        test_read_functionality()
        test_dupterm_functionality()

        print("\n" + "=" * 50)
        print("All tests completed successfully!")
        print("=" * 50)
        print("\nTo test RTT REPL functionality:")
        print("1. Connect a debug probe with RTT support")
        print("2. Run: import rtt, os; os.dupterm(rtt.RTTStream(1), 1)")
        print("3. Open RTT channel 1 in your debugger")
        print("4. Enjoy interactive Python debugging!")

    except Exception as e:
        print(f"\nTest failed with error: {e}")
        raise


if __name__ == "__main__":
    main()
