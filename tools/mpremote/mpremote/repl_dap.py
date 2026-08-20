"""Host end of the DAP channel that shares the REPL's own serial stream.

For a board with one UART and no network there is nothing else to put the
debug channel on, so it rides the stream that already carries the REPL,
marked with `0x18` the way `mpremote mount` marks its filesystem RPC. The
wire is described once, in the device's `debugpy/common/repl_mux.py`; this is
the same protocol read and written from the host, and the constants below are
that module's constants. They are duplicated rather than imported because
`mpremote` cannot depend on a `micropython-lib` package being present on the
machine it runs on; a test in the integration repo feeds identical byte
streams to both implementations, so the two cannot drift silently.

The port is read by one thread from the moment the channel opens, not from
the moment a DAP client connects: the program's output belongs on the
terminal whether or not anything is attached, and it is also how a traceback
from the debug server itself is seen at all.
"""

import errno
import sys
import threading

from .dap_log import PumpingProxy

MARKER = 0x18
_MARKER_B = bytes((MARKER,))

# `fs_hook_cmds` in transport_serial.py owns 1..13.
CMD_DAP = 14
CMD_DAP_ACK = 15
# The device saying the DAP channel is over. A serial port has no EOF of its
# own, so without this a finished session reads exactly like an idle one.
CMD_DAP_EOF = 16

RX_CREDIT = 192
MAX_PAYLOAD = 128  # capped so one frame always fits inside the credit window
ACK_THRESHOLD = 64


def frame(code, payload):
    """One framed message: marker, code, two-byte length, payload."""
    n = len(payload)
    return bytes((MARKER, code, n & 0xFF, (n >> 8) & 0xFF)) + bytes(payload)


def _consume(buf, n):
    """Drop the first `n` bytes of `buf`, in place.

    Spelled the way the device's copy has to spell it: `del buf[:n]` works
    here and raises on a MicroPython bytearray, which implements slice
    assignment but not slice deletion, and the two readers are kept
    line-for-line comparable.
    """
    if n >= len(buf):
        buf[:] = b""
    else:
        buf[:] = buf[n:]


def escape(data):
    """Plain bytes with the marker doubled, so a reader never mistakes one."""
    data = bytes(data)
    if data.find(_MARKER_B) < 0:
        return data
    return data.replace(_MARKER_B, bytes((MARKER, MARKER)))


class Demux:
    """Reads the marker-framed wire into plain bytes and framed payload.

    Incremental: a marker, a length or a payload split across two reads
    resumes where it left off, which is the normal case on a serial port that
    delivers whatever a USB packet happened to contain. `unknown_code` records
    the first code with no handler, which means the two ends disagree about
    the protocol and every later byte is suspect.
    """

    _PLAIN, _CODE, _LEN0, _LEN1, _BODY = 0, 1, 2, 3, 4

    def __init__(self):
        self.plain = bytearray()
        self.dap = bytearray()
        self.credited = 0
        self.eof = False
        self.unknown_code = None
        self._state = self._PLAIN
        self._code = 0
        self._need = 0
        self._body = bytearray()

    def feed(self, data):
        data = bytes(data)
        i = 0
        end = len(data)
        while i < end:
            if self._state == self._PLAIN:
                j = data.find(_MARKER_B, i)
                if j < 0:
                    self.plain += data[i:]
                    return
                self.plain += data[i:j]
                i = j + 1
                self._state = self._CODE
                continue

            c = data[i]
            i += 1
            if self._state == self._CODE:
                if c == MARKER:
                    self.plain.append(MARKER)  # escaped literal
                    self._state = self._PLAIN
                else:
                    self._code = c
                    self._state = self._LEN0
            elif self._state == self._LEN0:
                self._need = c
                self._state = self._LEN1
            elif self._state == self._LEN1:
                self._need |= c << 8
                self._body = bytearray()
                self._state = self._BODY if self._need else self._PLAIN
                if not self._need:
                    self._deliver()
            else:  # _BODY
                self._body.append(c)
                if len(self._body) == self._need:
                    self._deliver()
                    self._state = self._PLAIN

    def _deliver(self):
        if self._code == CMD_DAP:
            self.dap += self._body
        elif self._code == CMD_DAP_ACK:
            if len(self._body) >= 2:
                self.credited += self._body[0] | (self._body[1] << 8)
        elif self._code == CMD_DAP_EOF:
            self.eof = True
        elif self.unknown_code is None:
            self.unknown_code = self._code

    def take_plain(self, n):
        out = bytes(self.plain[:n])
        _consume(self.plain, len(out))
        return out

    def take_dap(self, n):
        out = bytes(self.dap[:n])
        _consume(self.dap, len(out))
        return out


class ReplDapChannel:
    """Owns the shared serial port for the length of a debug session.

    One reader thread demultiplexes the port: plain bytes go to `console`
    (mpremote's stdout, so a program's `print()` lands where it would without
    a debugger), framed bytes queue for whoever is bridging DAP. Writes are
    credit-limited, because the device's receive ring discards the tail of a
    packet when it overflows rather than exerting back-pressure, and a target
    inside `time.sleep()` is not draining it.

    Nothing else may read the port while this is open. That holds for the
    `repl` channel by construction - a mount is refused on it, and the
    handshake has already been read by the time the channel opens.
    """

    _POLL_TIMEOUT = 0.2  # seconds; caps how long close() takes to be noticed

    def __init__(self, serial, console=None):
        self._serial = serial
        self._console = console
        self._demux = Demux()
        self._lock = threading.Lock()
        self._arrived = threading.Condition(self._lock)
        self._sent = 0  # wire bytes written
        self._closed = False
        self.error = None  # the exception that ended the reader, if one did
        self._serial.timeout = self._POLL_TIMEOUT
        self._thread = threading.Thread(
            target=self._read_loop, name="repl-dap-reader", daemon=True
        )

    def start(self):
        self._thread.start()

    def close(self):
        with self._lock:
            self._closed = True
            self._arrived.notify_all()
        if self._thread.is_alive():
            self._thread.join(timeout=self._POLL_TIMEOUT * 5)

    # -- reader -----------------------------------------------------------

    def _write_console(self, data):
        if not data:
            return
        if self._console is not None:
            self._console(data)
        else:
            sys.stdout.buffer.write(data)
            sys.stdout.buffer.flush()

    def _read_loop(self):
        while not self._closed:
            try:
                # Wait for one byte with the port's timeout, then take
                # whatever else is already buffered without waiting for more:
                # asking for a fixed count up front would pay the whole
                # timeout on every read, since a frame is essentially never
                # exactly that many bytes.
                chunk = self._serial.read(1)
                if chunk:
                    chunk += self._serial.read(self._serial.in_waiting)
            except Exception as er:  # pyserial raises several shapes on a lost port
                if not self._closed:
                    self.error = er
                with self._arrived:
                    self._arrived.notify_all()
                return
            if not chunk:
                continue
            with self._lock:
                self._demux.feed(chunk)
                plain = self._demux.take_plain(len(self._demux.plain))
                self._arrived.notify_all()
            # Outside the lock: a slow terminal must not hold up the reader,
            # and nothing in here touches the demux.
            self._write_console(plain)

    # -- DAP side ---------------------------------------------------------

    def read_dap(self, n):
        """Up to `n` bytes of DAP payload, or `b""` once the channel is over.

        Over means one of three things: this end closed it, the port stopped
        being readable, or the device sent `CMD_DAP_EOF`. The queued payload
        is drained before any of them is reported, so an end-of-session frame
        can never discard a response that arrived in the same read.
        """
        with self._arrived:
            while True:
                if self._demux.dap:
                    return self._demux.take_dap(n)
                if self._closed or self.error is not None or self._demux.eof:
                    return b""
                self._arrived.wait(self._POLL_TIMEOUT)

    def write_dap(self, data):
        """Frame `data` onto the port, waiting for credit rather than overrunning.

        The only thing this end writes to the port, which is what makes
        `_sent` comparable with the byte count the device credits back: the
        device counts everything it reads, so anything written around this
        method would be credited without ever having been charged.
        """
        data = bytes(data)
        for i in range(0, len(data), MAX_PAYLOAD):
            chunk = frame(CMD_DAP, data[i : i + MAX_PAYLOAD])
            self._await_credit(len(chunk))
            if self._closed or self._demux.eof:
                raise OSError(errno.EPIPE, "repl DAP channel closed")
            if self.error is not None:
                raise OSError(errno.EIO, f"repl DAP channel is not readable: {self.error}")
            self._serial.write(chunk)
            with self._lock:
                self._sent += len(chunk)

    def _await_credit(self, n):
        """Wait until `n` more wire bytes fit inside the outstanding-byte window.

        `MAX_PAYLOAD` is capped so that one frame always fits an empty window;
        without that a large enough frame would wait for credit only its own
        unsent bytes could ever earn.
        """
        with self._arrived:
            while not self._closed and self.error is None and not self._demux.eof:
                if self._sent - self._demux.credited + n <= RX_CREDIT:
                    return
                self._arrived.wait(self._POLL_TIMEOUT)

    @property
    def unknown_code(self):
        return self._demux.unknown_code

    @property
    def finished(self):
        """True once the device's end is over, however it ended.

        Read by the caller's wait loop, which otherwise only learns a session
        has ended through the client that attached to it - and a device can
        finish before one ever does.
        """
        return self._demux.eof or self.error is not None


class _ChannelDuplex:
    """Presents `ReplDapChannel` as the connected target `PumpingProxy` expects.

    The four methods the pump code uses - `recv`/`sendall`/`shutdown`/`close` -
    over a channel that outlives any one client, so `close()` here ends the
    client's view of the session without tearing down the port the console is
    still being read from.
    """

    def __init__(self, channel):
        self._channel = channel
        self._shut = False

    def recv(self, n):
        if self._shut:
            return b""
        return self._channel.read_dap(n)

    def sendall(self, data):
        self._channel.write_dap(data)

    def shutdown(self, how=None):
        self._shut = True

    def close(self):
        self._shut = True


class ReplDapBridge(PumpingProxy):
    """Bridges a localhost DAP client onto the REPL stream's framed channel.

    A client attaches over plain TCP and never
    learns that the other side is a serial port. The difference is that the
    port is not a second device node but the one `mpremote` is already holding,
    so the channel is opened by the caller and outlives this bridge.
    """

    def __init__(self, channel, logger, bind_host="127.0.0.1", bind_port=0):
        super().__init__(logger, bind_host=bind_host, bind_port=bind_port)
        self._channel = channel

    def _connect_target(self):
        if self._channel.error is not None:
            raise OSError(errno.EIO, f"repl DAP channel is not readable: {self._channel.error}")
        return _ChannelDuplex(self._channel)
