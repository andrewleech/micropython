import bluetooth
import machine

print("Test start")
ble = bluetooth.BLE()
print("BLE created")
ble.active(True)
print("BLE activated")
ble.active(False)
print("BLE deactivated")
print("Calling soft_reset...")
machine.soft_reset()
print("After soft_reset (should not reach here)")
