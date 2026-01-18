# Zephyr BLE Missing Features Implementation Plan

Based on multitest results (7/18 pass, 11 fail), this document outlines the implementation plan for missing features in Zephyr BLE integration.

## Summary

**Current State**: Core BLE functionality working (advertising, scanning, connections, GATT client/server basic operations)

**Missing Features** (in priority order):
1. GATTC NOTIFY/INDICATE callbacks (High priority)
2. MTU exchange (Medium priority)
3. Pairing/bonding (Medium priority)
4. L2CAP connection-oriented channels (Low priority)

---

## 1. GATTC NOTIFY/INDICATE Callbacks (High Priority)

### Impact
- **Tests affected**: 5/11 failing tests
- **Common use case**: Sensor data subscriptions, real-time notifications
- **User impact**: Cannot receive async updates from BLE peripherals

### Current Behavior
- `gatts_notify()` / `gatts_indicate()` work (peripheral sending)
- CCCD (Client Characteristic Configuration Descriptor) writes succeed
- But `_IRQ_GATTC_NOTIFY` / `_IRQ_GATTC_INDICATE` callbacks never fire

### Root Cause
Zephyr BLE integration missing `bt_gatt_subscribe()` implementation. The Zephyr stack requires explicit subscription to receive notifications/indications.

### Implementation Plan

#### Phase 1: Research (1-2 hours)
- [ ] Study Zephyr `bt_gatt_subscribe()` API in `lib/zephyr/include/zephyr/bluetooth/gatt.h`
- [ ] Review existing NimBLE implementation for comparison
- [ ] Understand Zephyr notification callback flow

#### Phase 2: Core Implementation (4-6 hours)
- [ ] Add `bt_gatt_subscribe_params` management to connection state
- [ ] Implement subscription in `mp_bluetooth_gattc_discover_descriptors()`
  - After discovering CCCD, automatically subscribe
  - Or add explicit `gattc_subscribe()` API
- [ ] Implement `notify_cb()` function to handle incoming notifications
  - Extract handle, data from notification
  - Queue to MicroPython event system
  - Fire `_IRQ_GATTC_NOTIFY` / `_IRQ_GATTC_INDICATE` callbacks
- [ ] Handle subscription cleanup on disconnect

**Key Files**:
- `extmod/zephyr_ble/modbluetooth_zephyr.c` - Main implementation
- `extmod/zephyr_ble/modbluetooth_zephyr.h` - Connection state structures

**Zephyr API**:
```c
int bt_gatt_subscribe(struct bt_conn *conn, struct bt_gatt_subscribe_params *params);
typedef void (*bt_gatt_notify_func_t)(struct bt_conn *conn,
                                       struct bt_gatt_subscribe_params *params,
                                       const void *data, uint16_t length);
```

#### Phase 3: Testing (2-3 hours)
- [ ] Test `ble_characteristic.py` (notifications)
- [ ] Test `ble_subscribe.py` (subscription management)
- [ ] Test `ble_gatt_data_transfer.py` (data transfer via notifications)
- [ ] Test multiple concurrent subscriptions
- [ ] Test unsubscribe on disconnect

#### Phase 4: Documentation (30 min)
- [ ] Update CLAUDE.local.md with feature status
- [ ] Document any API differences from NimBLE

**Estimated Total Time**: 8-12 hours

---

## 2. MTU Exchange (Medium Priority)

### Impact
- **Tests affected**: 4/11 failing tests
- **Use case**: Large data transfers, performance optimization
- **User impact**: Cannot negotiate larger packet sizes (stuck at default 23 bytes)

### Current Behavior
- `gattc_exchange_mtu()` returns `EOPNOTSUPP`
- Default MTU used (23 bytes ATT payload)

### Root Cause
MTU exchange not implemented in Zephyr integration.

### Implementation Plan

#### Phase 1: Research (1 hour)
- [ ] Study Zephyr `bt_gatt_exchange_mtu()` API
- [ ] Understand Zephyr MTU exchange flow
- [ ] Check if MTU negotiation happens automatically

#### Phase 2: Implementation (3-4 hours)
- [ ] Implement `mp_bluetooth_gattc_exchange_mtu()` wrapper
  - Call `bt_gatt_exchange_mtu()`
  - Store negotiated MTU in connection state
- [ ] Handle `_IRQ_MTU_EXCHANGED` callback
  - Extract negotiated MTU value
  - Fire callback to Python

**Key Files**:
- `extmod/zephyr_ble/modbluetooth_zephyr.c`

**Zephyr API**:
```c
int bt_gatt_exchange_mtu(struct bt_conn *conn, struct bt_gatt_exchange_params *params);
```

#### Phase 3: Testing (2 hours)
- [ ] Test `ble_mtu.py` (central-initiated MTU exchange)
- [ ] Test `ble_mtu_peripheral.py` (peripheral-initiated MTU exchange)
- [ ] Test `perf_gatt_char_write.py` (performance with larger MTU)
- [ ] Verify MTU limits (min 23, max 512)

#### Phase 4: Documentation (30 min)
- [ ] Update CLAUDE.local.md
- [ ] Document MTU range and behavior

**Estimated Total Time**: 6-8 hours

---

## 3. Pairing/Bonding (Medium Priority)

### Impact
- **Tests affected**: 2/11 failing tests
- **Use case**: Secure connections, device authentication
- **User impact**: Cannot establish encrypted connections

### Current Behavior
- `gap_pair()` returns `EOPNOTSUPP`
- No pairing/bonding support

### Root Cause
Pairing/bonding APIs not implemented in Zephyr integration.

### Implementation Plan

#### Phase 1: Research (2-3 hours)
- [ ] Study Zephyr pairing/bonding APIs in `lib/zephyr/include/zephyr/bluetooth/conn.h`
- [ ] Understand Zephyr security manager
- [ ] Review pairing methods (Just Works, Passkey, OOB)
- [ ] Check NimBLE implementation for comparison

#### Phase 2: Core Implementation (6-8 hours)
- [ ] Implement `mp_bluetooth_gap_pair()`
  - Call `bt_conn_set_security()`
  - Handle security level parameter
- [ ] Implement pairing callbacks
  - `auth_passkey_display` - Display passkey to user
  - `auth_passkey_entry` - Request passkey from user
  - `auth_cancel` - Pairing cancelled
  - `pairing_complete` - Pairing success/failure
- [ ] Fire `_IRQ_ENCRYPTION_UPDATE` callback
- [ ] Implement bonding storage (if needed)

**Key Files**:
- `extmod/zephyr_ble/modbluetooth_zephyr.c`
- `extmod/zephyr_ble/modbluetooth_zephyr.h`

**Zephyr API**:
```c
int bt_conn_set_security(struct bt_conn *conn, bt_security_t sec);
struct bt_conn_auth_cb {
    void (*passkey_display)(struct bt_conn *conn, unsigned int passkey);
    void (*passkey_entry)(struct bt_conn *conn);
    // ...
};
```

#### Phase 3: Testing (3-4 hours)
- [ ] Test `ble_gap_pair.py` (basic pairing)
- [ ] Test `ble_gap_pair_bond.py` (bonding/re-pairing)
- [ ] Test different security levels
- [ ] Test pairing failure scenarios

#### Phase 4: Documentation (1 hour)
- [ ] Update CLAUDE.local.md
- [ ] Document security levels and pairing methods
- [ ] Document any platform-specific limitations

**Estimated Total Time**: 12-16 hours

---

## 4. L2CAP Connection-Oriented Channels (Low Priority)

### Impact
- **Tests affected**: 2 tests (skipped)
- **Use case**: Custom protocols, bulk data transfer
- **User impact**: Advanced feature, rarely used

### Implementation Plan

#### Deferred
L2CAP support is an advanced feature with limited use cases. Focus on higher-priority features first. Can be implemented later if needed.

---

## Implementation Order

**Recommended sequence** (based on priority and dependencies):

1. **GATTC NOTIFY/INDICATE** (Week 1)
   - Highest impact on test pass rate (5 tests)
   - Common BLE use case
   - No dependencies

2. **MTU Exchange** (Week 2)
   - Medium impact (4 tests)
   - Performance optimization
   - No dependencies

3. **Pairing/Bonding** (Week 3-4)
   - Security feature
   - More complex implementation
   - May require platform-specific testing

4. **L2CAP** (Future)
   - Low priority
   - Advanced feature

---

## Testing Strategy

### Per-Feature Testing
1. Run specific failing multitest after implementation
2. Verify no regressions in passing tests
3. Test on both STM32WB55 and RP2 Pico W

### Full Regression Testing
After each feature:
```bash
./tests/run-multitests.py -t /dev/ttyACM5 -t /dev/ttyACM3 tests/multi_bluetooth/*.py
```

Target: 18/18 tests passing (excluding L2CAP if not implemented)

### Verification Checklist
- [ ] All affected multitests passing
- [ ] No regressions in previously passing tests
- [ ] Both STM32WB55 and RP2 Pico W working
- [ ] Memory leaks checked (repeated test cycles)
- [ ] Soft reset stability verified

---

## Success Metrics

**After NOTIFY implementation**: 12/18 tests passing (66% → 67%)
**After MTU implementation**: 16/18 tests passing (89%)
**After Pairing implementation**: 18/18 tests passing (100%, excluding L2CAP)

---

## Notes

- All implementations should follow existing Zephyr BLE integration patterns
- Maintain compatibility with NimBLE API where possible
- Document any behavioral differences from NimBLE
- Test thoroughly on STM32WB55 (IPCC transport) as it has unique constraints
- Consider memory constraints (STM32WB55 has limited RAM)

---

## References

- Zephyr BLE API: `lib/zephyr/include/zephyr/bluetooth/`
- NimBLE implementation: `extmod/nimble/`
- Existing Zephyr integration: `extmod/zephyr_ble/modbluetooth_zephyr.c`
- Multitest framework: `tests/run-multitests.py`
