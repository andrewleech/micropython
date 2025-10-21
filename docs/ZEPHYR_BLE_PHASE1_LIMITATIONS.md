# Zephyr BLE Phase 1 Implementation Limitations

## Summary
The current Zephyr BLE implementation for MicroPython is **Phase 1: Server-only**, which means it supports peripheral role and GATT server functionality, but does **NOT** support central role or GATT client operations.

## Test Results

### Multi-test: WB55 (Zephyr) Central → PYBD (NimBLE) Peripheral
**Test**: `tests/multi_bluetooth/ble_gap_connect.py`
**Configuration**:
- Instance 0 (ttyACM0): PYBD running NimBLE - acts as peripheral (advertises)
- Instance 1 (ttyACM1): NUCLEO_WB55 running Zephyr BLE - attempts to act as central (connect)

**Result**: FAILED
```
Traceback (most recent call last):
  File "<stdin>", line 147, in <module>
  File "<stdin>", line 70, in instance1
OSError: [Errno 95] EOPNOTSUPP
```

**Root Cause**: `gap_connect()` calls `mp_bluetooth_gap_peripheral_connect()` which is a stub:

```c
// extmod/zephyr_ble/modbluetooth_zephyr.c:1073
int mp_bluetooth_gap_peripheral_connect(uint8_t addr_type, const uint8_t *addr,
                                        int32_t duration_ms,
                                        int32_t min_conn_interval_us,
                                        int32_t max_conn_interval_us) {
    DEBUG_printf("mp_bluetooth_gap_peripheral_connect\n");
    if (!mp_bluetooth_is_active()) {
        return ERRNO_BLUETOOTH_NOT_ACTIVE;
    }
    return MP_EOPNOTSUPP;  // Always returns errno 95
}
```

### Multi-test: PYBD (NimBLE) Central → WB55 (Zephyr) Peripheral
**Test**: Reverse roles - PYBD connects to WB55
**Configuration**:
- Instance 0 (ttyACM0): NUCLEO_WB55 running Zephyr BLE - acts as peripheral (advertises)
- Instance 1 (ttyACM1): PYBD running NimBLE - acts as central (connects)

**Expected Result**: Should work ✓ (both stacks support these roles)

## Supported Features (Phase 1)

### ✓ Peripheral Role
- `gap_advertise()` - Start/stop advertising
- Accept incoming connections from central devices
- Connection callbacks (`_IRQ_CENTRAL_CONNECT`, `_IRQ_CENTRAL_DISCONNECT`)
- Connection parameter handling

### ✓ GATT Server
- `gatts_register_service()` - Register services with characteristics/descriptors
- `gatts_read()` / `gatts_write()` - Read/write characteristic values
- `gatts_notify()` / `gatts_indicate()` - Send notifications/indications to connected centrals
- GATT attribute database management
- CCC (Client Characteristic Configuration) handling

### ✓ Scanning (Observer Role)
- `gap_scan()` - Start/stop passive/active scanning
- Advertising report reception (`_IRQ_SCAN_RESULT`)
- Scan parameter configuration (interval, window, duration)

## NOT Implemented (Phase 1)

### ✗ Central Role
All central role functions return `MP_EOPNOTSUPP`:
- `gap_connect()` - Connect to advertising peripheral
- `gap_connect_cancel()` - Cancel ongoing connection attempt

Implementation location: `extmod/zephyr_ble/modbluetooth_zephyr.c:1073-1087`

### ✗ GATT Client
All GATT client functions return `MP_EOPNOTSUPP`:
- `gattc_discover_services()` - Discover primary services on remote device
- `gattc_discover_characteristics()` - Discover characteristics in service range
- `gattc_discover_descriptors()` - Discover descriptors for characteristic
- `gattc_read()` - Read characteristic value from remote device
- `gattc_write()` - Write characteristic value to remote device
- `gattc_exchange_mtu()` - Negotiate MTU size

Implementation location: `extmod/zephyr_ble/modbluetooth_zephyr.c:1218-1247`

### ✗ Pairing/Bonding
All security functions are no-ops or return `MP_EOPNOTSUPP`:
- `gap_pair()` - Initiate pairing
- `gap_passkey()` - Provide passkey for pairing
- `set_bonding()` - Enable/disable persistent bonding
- `set_le_secure()` - Configure LE Secure Connections
- `set_mitm_protection()` - Configure MITM protection
- `set_io_capability()` - Configure I/O capability

Implementation location: `extmod/zephyr_ble/modbluetooth_zephyr.c:1249-1280`

## Code Comments

From `extmod/zephyr_ble/modbluetooth_zephyr.c`:

```c
// Line 1218
// GATT Client stubs (Phase 1: Server-only)
int mp_bluetooth_gattc_discover_primary_services(...) {
    return MP_EOPNOTSUPP; // Phase 1: GATT server only
}

// Line 1250
// Pairing/Bonding stubs (Phase 1: No persistent storage)
int mp_bluetooth_gap_pair(uint16_t conn_handle) {
    return MP_EOPNOTSUPP; // Phase 1: No pairing support
}

// Line 1260
void mp_bluetooth_set_bonding(bool enabled) {
    (void)enabled;
    // Phase 1: No bonding (persistent storage) - no-op
}
```

## Future Work

To implement Phase 2 (full central + client support), the following would need to be added:

1. **Central Role Connection**:
   - Implement `mp_bluetooth_gap_peripheral_connect()` using `bt_conn_le_create()`
   - Implement connection timeout handling
   - Add connection parameter negotiation
   - Implement `mp_bluetooth_gap_peripheral_connect_cancel()` using `bt_conn_le_create_cancel()`

2. **GATT Client**:
   - Service discovery using `bt_gatt_discover()`
   - Characteristic discovery
   - Descriptor discovery
   - Read/write operations using `bt_gatt_read()` / `bt_gatt_write()`
   - MTU exchange using `bt_gatt_exchange_mtu()`
   - Notification/indication subscription

3. **Security**:
   - Implement `bt_conn_auth_cb` callbacks for pairing
   - Add passkey handling
   - Implement bonding with persistent storage
   - Configure SMP (Security Manager Protocol) settings

## Zephyr BLE API References

The Zephyr BLE host stack provides these APIs for the missing features:
- `bt_conn_le_create()` - Create LE connection (central role)
- `bt_conn_le_create_cancel()` - Cancel pending connection
- `bt_gatt_discover()` - GATT service/characteristic discovery
- `bt_gatt_read()` - GATT read operation
- `bt_gatt_write()` - GATT write operation
- `bt_conn_auth_cb_register()` - Register pairing callbacks

See Zephyr documentation:
- https://docs.zephyrproject.org/latest/connectivity/bluetooth/api/conn.html
- https://docs.zephyrproject.org/latest/connectivity/bluetooth/api/gatt.html

## Test Strategy

Until central role is implemented, use this test configuration:

**Working Test (Peripheral Role)**:
```python
# WB55 (Zephyr) as peripheral, PYBD (NimBLE) as central
# Run: tests/run-multitests.py -t a1 -t a0 multi_bluetooth/ble_gap_connect.py
# (Note: reversed order - PYBD becomes instance 1, WB55 becomes instance 0)
```

**NOT Working (Central Role)**:
```python
# WB55 (Zephyr) as central, PYBD (NimBLE) as peripheral
# Run: tests/run-multitests.py -t a0 -t a1 multi_bluetooth/ble_gap_connect.py
# Result: OSError: [Errno 95] EOPNOTSUPP
```

## Workaround for Testing

To test Zephyr BLE connections:
1. Use WB55 (Zephyr) as **peripheral** (advertiser)
2. Use another device running NimBLE as **central** (connector)
3. Test GATT server features (read/write characteristics, notifications)

Example multi-test invocation (reversed roles):
```bash
cd tests
# Run with devices swapped - WB55 becomes instance0 (peripheral), PYBD becomes instance1 (central)
./run-multitests.py -t a1 -t a0 multi_bluetooth/ble_gap_connect.py
```

Note: This requires modifying the test script to swap peripheral/central roles based on instance number.
