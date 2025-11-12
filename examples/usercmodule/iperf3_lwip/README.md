# iperf3_lwip - High-Performance Network Testing Module

This module provides high-performance network throughput testing by directly using the lwIP TCP API, bypassing the socket layer for maximum speed.

## Architecture

- **Direct PCB Creation**: Creates own TCP PCBs with `tcp_new()` instead of hijacking socket PCBs
- **Callback-Driven**: Uses lwIP callbacks for zero-copy transmission
- **Heap-Allocated Buffers**: Avoids BSS bloat by allocating buffers on heap
- **Parameter-Based API**: Accepts Python dicts for configuration, returns dicts with results

## Expected Performance

- **STM32N6 @ 800MHz**: 400-600 Mbits/sec TCP throughput
- **Comparison**: Similar to ciperf module (489 Mbits/sec achieved)

## API

### tcp_send_test(params_dict)

Connects to an iperf3 server and sends data for the specified duration.

**Parameters** (dict):
- `server_ip`: str - IP address of iperf3 server (required)
- `port`: int - Port number (default: 5201)
- `duration_ms`: int - Test duration in milliseconds (default: 10000)
- `buffer_size`: int - TX buffer size in bytes (default: 16384)

**Returns** (dict):
- `bytes`: int - Total bytes transferred
- `duration_ms`: int - Actual test duration in milliseconds

**Example**:
```python
import network
import iperf3_lwip

# Initialize network
lan = network.LAN()
lan.active(True)

# Run test
result = iperf3_lwip.tcp_send_test({
    'server_ip': '192.168.0.8',
    'port': 5201,
    'duration_ms': 10000,
    'buffer_size': 16384
})

# Calculate throughput
mbits_per_sec = result['bytes'] * 8 / result['duration_ms'] / 1000
print(f"Throughput: {mbits_per_sec:.2f} Mbits/sec")
```

## Testing

1. Start iperf3 server on PC:
```bash
iperf3 -s
```

2. Run test on MicroPython device:
```bash
mpremote run test_iperf3_lwip.py
```

## Implementation Details

### Callbacks

- **tcp_err_cb**: Handles connection errors, marks test as complete
- **tcp_sent_cb**: Drives TX data pump, sends data as TCP window opens
- **tcp_connected_cb**: Handles connection establishment, starts test

### Memory Management

- TX buffer allocated on heap with `m_malloc()`
- Lazy initialization on first use
- Buffer reused across multiple tests

### Optimization Techniques

- `TCP_WRITE_FLAG_MORE` for segment coalescing
- `TCP_WRITE_FLAG_COPY` to allow immediate buffer reuse
- `tcp_output()` forces immediate transmission
- `tcp_nagle_disable()` disables Nagle's algorithm
- `tcp_setprio()` sets maximum priority

### Build Configuration

Compiled with `-Os` optimization in `micropython.mk` to reduce code size while maintaining performance.

## Future Phases

### Phase 2: TCP RX (Server Receive)
Add `tcp_recv_test()` function for receiving data from client.

### Phase 3: UDP Support
Add `udp_send_test()` and `udp_recv_test()` with iperf3 header parsing.

## Comparison with ciperf Module

**ciperf**:
- Simple positional arguments
- Prints results to console
- Achieves 489 Mbits/sec

**iperf3_lwip**:
- Dict-based parameters
- Returns results as dict
- Expected 400-600 Mbits/sec
- Designed for integration with Python iperf3 protocol layer

## License

MIT License - Same as MicroPython
