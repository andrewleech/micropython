"""`--dap-log`: a localhost proxy that records DAP traffic to JSONL.

Neither `debug` transport puts the byte stream through mpremote - the
command reports an endpoint and the DAP client connects to the device
directly. Logging therefore means interposing a proxy: bind an ephemeral
port, accept the one client connection, connect through to the device's
real endpoint, and pump both directions while handing each complete frame
to a `DapLogger`. The caller substitutes the proxy's host/port for the
device's in whatever it reports, so the client attaches to the logger
instead of bypassing it.
"""

import json
import os
import queue
import socket
import threading
import time

_HEADER_SEP = b"\r\n\r\n"


class FrameParser:
    """Incremental parser for Content-Length-framed DAP messages.

    `feed(chunk)` returns the list of message bodies (JSON bytes, header
    stripped) completed by that chunk; a header or body split across
    multiple `feed()` calls is carried in `_buf` until whole, and several
    frames arriving in one chunk are all returned.
    """

    def __init__(self):
        self._buf = b""

    def feed(self, chunk):
        self._buf += chunk
        frames = []
        while True:
            sep = self._buf.find(_HEADER_SEP)
            if sep == -1:
                break
            header = self._buf[:sep].decode("ascii", errors="replace")
            length = None
            for line in header.split("\r\n"):
                name, _, value = line.partition(":")
                if name.strip().lower() == "content-length":
                    try:
                        length = int(value.strip())
                    except ValueError:
                        length = None
            body_start = sep + len(_HEADER_SEP)
            if length is None:
                # No Content-Length, or an unparsable one: drop the header
                # and resync on the next separator rather than raising -
                # this parser sits in the data path, not just the log.
                self._buf = self._buf[body_start:]
                continue
            if len(self._buf) < body_start + length:
                break  # body not fully arrived yet
            frames.append(self._buf[body_start : body_start + length])
            self._buf = self._buf[body_start + length :]
        return frames


class DapLogger:
    """Appends one `{ts, dir, msg}` JSON line per frame, off the pump thread.

    `log()` only ever puts onto a queue; a dedicated writer thread does the
    file I/O, so a slow disk can't perturb the timing `--dap-log` exists to
    observe. The file is opened here, in the caller's thread, so a bad path
    (missing directory, permissions) raises `OSError` to the caller instead
    of surfacing as an uncaught exception in the writer thread later.
    """

    def __init__(self, path):
        self._file = open(path, "a", buffering=1)
        self._q = queue.Queue()
        self._thread = threading.Thread(target=self._run, name="dap-log-writer", daemon=True)
        self._thread.start()

    def log(self, direction, frame):
        self._q.put((time.time(), direction, frame))

    def _run(self):
        with self._file:
            while True:
                item = self._q.get()
                if item is None:
                    break
                ts, direction, frame = item
                try:
                    msg = json.loads(frame.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError):
                    msg = frame.decode("utf-8", errors="replace")
                self._file.write(json.dumps({"ts": ts, "dir": direction, "msg": msg}) + "\n")

    def close(self):
        self._q.put(None)
        self._thread.join(timeout=2)


def default_log_path():
    # Sortable and Windows-filename-safe (no ':'), unlike isoformat(). The
    # pid disambiguates two sessions started in the same second in the same
    # cwd, which would otherwise append-interleave into the same file.
    return "mpremote-dap-{}-{}.jsonl".format(time.strftime("%Y%m%dT%H%M%S"), os.getpid())


class DapProxy:
    """Localhost proxy: one client, forwarded to (target_host, target_port).

    Binds its port immediately (`host`/`port` are set once `__init__`
    returns); the accept and both pump directions run on background threads
    started by `start()`, so construction and reporting the substituted
    endpoint never block on a client actually connecting. `bind_port`
    defaults to 0 (OS-assigned); a caller pinning the client-facing endpoint
    (`--port` alongside `--dap-log`) passes the requested port instead.
    """

    def __init__(self, target_host, target_port, logger, bind_host="127.0.0.1", bind_port=0):
        self.host = bind_host
        self._target = (target_host, target_port)
        self._logger = logger
        self._listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._listener.bind((bind_host, bind_port))
        self._listener.listen(1)
        self.port = self._listener.getsockname()[1]
        self._client = None
        self._server = None
        self._pumps = []
        self._done = threading.Event()
        self._closed = False

    def start(self):
        threading.Thread(target=self._accept, name="dap-proxy-accept", daemon=True).start()

    def _accept(self):
        try:
            self._client, _ = self._listener.accept()
        except OSError:
            self._done.set()
            return
        try:
            self._server = socket.create_connection(self._target)
        except OSError:
            self._client.close()
            self._done.set()
            return
        a = threading.Thread(
            target=self._pump, args=(self._client, self._server, "client"), name="dap-proxy-c2s"
        )
        b = threading.Thread(
            target=self._pump, args=(self._server, self._client, "device"), name="dap-proxy-s2c"
        )
        self._pumps = [a, b]
        a.start()
        b.start()
        a.join()
        b.join()
        self._done.set()

    def _pump(self, src, dst, direction):
        # `direction` names where the frame came from, not where it's going.
        parser = FrameParser()
        try:
            while True:
                chunk = src.recv(4096)
                if not chunk:
                    break
                dst.sendall(chunk)
                # Forwarding must survive a logging fault - non-DAP bytes on
                # the socket (an HTTP probe, console output) or anything else
                # that trips the parser or writer must not tear down the pump.
                try:
                    frames = parser.feed(chunk)
                except Exception:
                    frames = []
                for frame in frames:
                    try:
                        self._logger.log(direction, frame)
                    except Exception:
                        pass
        except OSError:
            pass
        finally:
            # Half-closing only tells the peer we are done writing; if it
            # never closes its own side, the opposite pump stays blocked in
            # recv() and the session never ends. Shut down BOTH directions so
            # that pump returns and the proxy can report the session over.
            for sock in (dst, src):
                try:
                    sock.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass

    def wait(self, timeout=None):
        """Block until the one client session this proxy serves has ended."""
        return self._done.wait(timeout)

    def close(self):
        # Idempotent and signal-safe: called from every do_debug exit path,
        # including a second Ctrl-C landing mid-cleanup.
        if self._closed:
            return
        self._closed = True
        for sock in (self._listener, self._client, self._server):
            if sock is not None:
                try:
                    sock.close()
                except OSError:
                    pass
        for t in self._pumps:
            t.join(timeout=2)
        self._logger.close()
