"""Localhost<->serial bridge for `mpremote debug` on multi-CDC boards.

On a board with a dedicated second USB CDC interface for DAP (`serial_dap`
in the MPDBG-READY `caps`), the device's debug channel is never a TCP
socket - it's a second serial device node, opened directly instead of
connected to over the network. `SerialDapBridge` is a `dap_log.PumpingProxy`
whose target is that node rather than (host, port): a DAP client still
attaches over plain localhost TCP, unaware the other side of the bridge is
serial.
"""

import errno

import serial

from .dap_log import PumpingProxy

# Native-USB CDC ACM interfaces don't have a meaningful line speed - the
# firmware doesn't gate on it - but pyserial requires some value be set.
DEFAULT_BAUDRATE = 115200


def check_device(device, baudrate=DEFAULT_BAUDRATE):
    """Open and immediately close `device`, raising `OSError` if it can't be opened.

    `SerialDapBridge` itself only opens the device lazily on first client
    connect, so a bad `dap_device` (typo, unplugged board) would otherwise
    surface as a session-in-progress failure reported as a lost connection
    ("board reset?") rather than the config error it actually is. Call this
    before reporting the bridge's endpoint to a caller.
    """
    serial.serial_for_url(device, baudrate=baudrate).close()


class _SerialDuplex:
    """Wraps a pyserial connection so it duck-types a connected socket.

    `PumpingProxy`'s pump threads only ever call `recv`/`sendall`/`shutdown`/
    `close` on the target - matching those four methods is what lets a
    serial connection stand in for `socket.create_connection`'s result
    without the pump code caring which one it has.

    A serial port has no independent-direction close the way a TCP socket's
    `shutdown(SHUT_RDWR)` does, and pyserial's blocking `read()` isn't
    reliably interrupted by another thread closing the port under it. So the
    port is opened with a short read timeout and `recv()` loops on it,
    rechecking `_closed` each time instead of blocking indefinitely -
    `shutdown()` just sets that flag, and the read in flight (bounded by
    `_POLL_TIMEOUT`) returns empty and lets the loop notice within one poll.

    `close()` running concurrently with a `read()`/`write()` already past
    that check is still possible within one poll window: pyserial's `close()`
    sets its internal fd to `None` rather than making the next syscall raise,
    so a read/write already inside pyserial can hit `os.read(None, ...)` and
    raise `TypeError`, not `OSError` - `_reraise_as_oserror` gives that the
    same shape every other "the port is gone" case already has, which is
    what `PumpingProxy._pump`'s `except OSError` is written against.
    """

    _POLL_TIMEOUT = 0.2  # seconds; caps how long shutdown()/close() take to be noticed

    def __init__(self, device, baudrate):
        self._serial = serial.serial_for_url(device, baudrate=baudrate, timeout=self._POLL_TIMEOUT)
        self._closed = False

    @staticmethod
    def _reraise_as_oserror(er):
        raise OSError(errno.EBADF, "serial port closed during I/O") from er

    def recv(self, n):
        # pyserial's read(size) blocks for the full port timeout unless
        # `size` bytes arrive - a DAP frame is essentially never exactly
        # `n` (4096) bytes, so requesting `n` up front would pay the whole
        # `_POLL_TIMEOUT` on every call. Wait for one byte with the port's
        # timeout, then take whatever else is already buffered
        # (non-blocking) instead of waiting for more.
        while not self._closed:
            try:
                chunk = self._serial.read(1)
                if chunk:
                    chunk += self._serial.read(self._serial.in_waiting)
            except TypeError as er:
                self._reraise_as_oserror(er)
            if chunk:
                return chunk
        return b""

    def sendall(self, data):
        try:
            self._serial.write(data)
        except TypeError as er:
            self._reraise_as_oserror(er)

    def shutdown(self, how=None):
        self._closed = True

    def close(self):
        self._closed = True
        self._serial.close()


class SerialDapBridge(PumpingProxy):
    """`PumpingProxy` forwarding to a serial device instead of a TCP endpoint.

    `device` is opened lazily, once a client has connected (`_connect_target`,
    called from the accept thread) - not in `__init__` - matching `DapProxy`,
    which doesn't dial its TCP target until then either, and keeping the
    device node untouched if no client ever attaches.
    """

    def __init__(
        self, device, logger, baudrate=DEFAULT_BAUDRATE, bind_host="127.0.0.1", bind_port=0
    ):
        super().__init__(logger, bind_host, bind_port)
        self._device = device
        self._baudrate = baudrate

    def _connect_target(self):
        return _SerialDuplex(self._device, self._baudrate)
