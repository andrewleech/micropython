# Test CAN.recv() with a result list whose memoryview is a slice of a larger
# buffer, or is read-only, using loopback.

try:
    from machine import CAN

    CAN(1, 125_000)
except (ImportError, ValueError):
    print("SKIP")
    raise SystemExit

import time
import unittest


class Test(unittest.TestCase):
    def setUp(self):
        self.can = CAN(1, 125_000, mode=CAN.MODE_SILENT_LOOPBACK)
        self.can.set_filters(None)
        while self.can.recv():
            pass

    def tearDown(self):
        self.can.deinit()

    def _send(self, ident, data):
        self.can.send(ident, data, 0)
        time.sleep_ms(10)

    def test_slice_offset(self):
        buf = bytearray(b"\xaa" * 24)
        result = [0, memoryview(buf)[12:20], 0, 0]

        self._send(0x123, b"abc")
        self.assertIs(self.can.recv(result), result)
        self.assertEqual(result[0], 0x123)
        self.assertEqual(result[1], b"abc")
        self.assertEqual(buf, b"\xaa" * 12 + b"abc" + b"\xaa" * 9)

        # The shortened view is reused for a longer frame at the same offset.
        self._send(0x124, b"12345678")
        self.assertIs(self.can.recv(result), result)
        self.assertEqual(result[1], b"12345678")
        self.assertEqual(buf, b"\xaa" * 12 + b"12345678" + b"\xaa" * 4)

    def test_read_only_view(self):
        self._send(0x125, b"ro")
        with self.assertRaises(TypeError):
            self.can.recv([0, memoryview(bytes(8)), 0, 0])
        # The rejected call did not consume the frame.
        res = self.can.recv()
        self.assertEqual(res[0], 0x125)
        self.assertEqual(res[1], b"ro")


if __name__ == "__main__":
    unittest.main()
