"""Shared parsing of the `MPDBG-READY {json}` handshake line.

Every control plane that launches a debug session (unix subprocess stdout,
raw-REPL exec output over serial) gets the same one-line handshake back over
a different byte stream. `read_handshake()` scans whatever `read_chunk()`
supplies for that line, validates its payload, and resolves the endpoint it
reports against caller-supplied context - so no caller ever hands a bare
`0.0.0.0` bind address to a DAP client.
"""

import json
import time

PREFIX = "MPDBG-READY "

CONTROL_KIND_UNIX = "unix"
CONTROL_KIND_SERIAL = "serial"
_CONTROL_KINDS = (CONTROL_KIND_UNIX, CONTROL_KIND_SERIAL)

# Bind addresses meaning "no address of my own to report" (see
# mpy_launch_debugpy.py's _detect_host, which reports "0.0.0.0"). Never a
# connectable endpoint, so never returned by _resolve_host.
# Spellings of "bound to every interface". Normalised before comparison, so
# "::0", "[::]" and stray whitespace are recognised too - a wildcard that slips
# through would be handed to a client as an address to connect to.
_WILDCARD_HOSTS = ("0.0.0.0", "0:0:0:0:0:0:0:0", "::", "::0", "")


def _is_wildcard(host):
    return host.strip().strip("[]").lower() in _WILDCARD_HOSTS


class HandshakeError(Exception):
    """A missing/duplicate/malformed handshake line, or an unresolvable endpoint."""


def _tail(text):
    text = text.strip()
    return f"; last output: {text!r}" if text else ""


def _validate(payload):
    if not isinstance(payload, dict):
        raise HandshakeError(
            f"malformed handshake payload from the device, expected a JSON object: {payload!r}"
        )
    try:
        host, port, caps = payload["host"], payload["port"], payload["caps"]
    except KeyError as er:
        raise HandshakeError(f"malformed handshake payload from the device, missing key {er}")
    if not isinstance(host, str):
        raise HandshakeError(
            f"malformed handshake payload from the device, 'host' is not a string: {host!r}"
        )
    if not isinstance(port, int) or isinstance(port, bool):
        raise HandshakeError(
            f"malformed handshake payload from the device, 'port' is not an int: {port!r}"
        )
    if not isinstance(caps, dict) or any(not isinstance(v, bool) for v in caps.values()):
        raise HandshakeError(
            f"malformed handshake payload from the device, 'caps' is not a table of booleans: {caps!r}"
        )
    return host, port, caps


def _resolve_host(host, port, control_kind, known_host):
    if control_kind not in _CONTROL_KINDS:
        raise HandshakeError(
            f"unknown control_kind {control_kind!r}, expected one of {_CONTROL_KINDS!r}"
        )
    # host is a bind address ("what I listened on"), not necessarily a
    # connectable one - see _WILDCARD_HOSTS.
    if not _is_wildcard(host):
        return host
    if control_kind == CONTROL_KIND_UNIX:
        return "127.0.0.1"
    # known_host is equally unusable if it's itself a wildcard - a caller
    # that only knows "device is listening on all interfaces" knows nothing.
    if known_host and known_host not in _WILDCARD_HOSTS:
        return known_host
    raise HandshakeError(
        "the device reported no network address (bound all interfaces on port "
        f"{port}); connect it to a network, or debug it over a control plane "
        "that knows its address - refusing to guess"
    )


def read_handshake(
    read_chunk,
    timeout,
    control_kind,
    known_host=None,
    initial="",
    on_line=None,
    eof=None,
    on_eof=None,
):
    """Scan `read_chunk()` for one `MPDBG-READY {json}` line and resolve it.

    `read_chunk()` returns available text each poll, `''` when none yet; it
    owns its own pacing (a real blocking read with a short timeout, or a
    non-blocking read plus a short sleep) - this loop just calls it
    repeatedly until a line is found or `timeout` elapses. `initial` seeds
    the buffer with text a caller already drained before this was called.

    Matches `PREFIX` at line start only - never as a substring - and
    tolerates the line splitting across `read_chunk()` calls. The JSON is
    parsed with `raw_decode`, so trailing bytes after the closing brace but
    before the newline (a stray `\\r`, echoed prompt/control bytes) don't
    break it. Two `MPDBG-READY` lines arriving in the same buffered batch is
    a duplicate error, matching the emit side's one-line guarantee; a
    duplicate arriving only after this has already returned is not
    detected. A line-at-a-time `read_chunk` (serial: one `read_until(...,
    b"\\n")` per poll) can only ever buffer one line at a time, so it can
    detect a duplicate only if the caller seeds both lines via `initial`.

    `on_line`, given a complete non-handshake line (trailing newline
    included), lets a caller pass banner text straight through.

    `eof`, if given, is a marker string that never appears in ordinary
    output; seeing it before any handshake line ends the wait immediately
    instead of stalling for the full timeout (a device that crashes on
    import prints no `MPDBG-READY` line, ever). `on_eof(rest)`, given
    whatever trailed the marker so far, may do further caller-side reads to
    build a diagnostic suffix appended to the resulting error.

    Returns `{"kind": "tcp", "host", "port", "caps", "raw_host"}`. `host` is
    resolved (never `"0.0.0.0"`); `raw_host` keeps what the device actually
    reported, for diagnostics.
    """
    deadline = time.monotonic() + timeout
    buf = initial
    last_line = ""
    matches = []
    while True:
        # Cut the eof marker out first: on serial, read_chunk returns
        # everything through the next newline, so `\x04Traceback ...\r\n`
        # arrives as one chunk with the marker glued to the line after it.
        # Splitting on "\n" before checking for eof would swallow the
        # marker into that line and hide it from the check below.
        if eof is not None and eof in buf:
            head, _, rest = buf.partition(eof)
        else:
            head, rest = buf, None
        while "\n" in head:
            line, head = head.split("\n", 1)
            if line.startswith(PREFIX):
                matches.append(line[len(PREFIX) :])
            else:
                if on_line:
                    on_line(line + "\n")
                last_line = line
        if matches:
            break
        if rest is not None:
            extra = on_eof(rest) if on_eof else ""
            raise HandshakeError(
                f"device exited before printing a {PREFIX!r} line{_tail(last_line + head)}{extra}"
            )
        buf = head
        if time.monotonic() >= deadline:
            raise HandshakeError(
                f"timed out waiting for a {PREFIX!r} line{_tail(last_line + buf)}"
            )
        buf += read_chunk()

    if len(matches) > 1:
        raise HandshakeError(
            f"expected exactly one {PREFIX!r} line, got {len(matches)}{_tail(last_line + head)}"
        )

    payload_text = matches[0]
    try:
        payload, _ = json.JSONDecoder().raw_decode(payload_text)
    except json.JSONDecodeError as er:
        raise HandshakeError(f"malformed {PREFIX!r} line from the device: {payload_text!r} ({er})")

    host, port, caps = _validate(payload)
    resolved_host = _resolve_host(host, port, control_kind, known_host)
    return {"kind": "tcp", "host": resolved_host, "port": port, "caps": caps, "raw_host": host}
