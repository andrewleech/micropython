# This file is part of the MicroPython project, http://micropython.org/
#
# The MIT License (MIT)
#
# Copyright (c) 2026 Andrew Leech
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in
# all copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
# THE SOFTWARE.
"""Single parameterised boot script for MicroPython debugpy sessions.

Usage: mpy_launch_debugpy.py [target_module] [target_method] [port]

The bind address is probed at runtime rather than passed in: boards with a
`network` module report their own address, everything else binds all
interfaces. No device IP is hardcoded by the caller; the port, if given, is
supplied by the caller (0 is rejected by `debugpy.listen()` on every current
MicroPython port, since none implements `socket.getsockname()`). The actual
bound endpoint plus the probed firmware capabilities are reported in a
single machine-readable handshake line on stdout, printed as soon as the
socket is bound and before any client has attached:

    MPDBG-READY {"host": "...", "port": ..., "caps": {...}}

Tooling parses that one line rather than any of the human-readable banner
text around it. `wait_for_client()` (not a fixed sleep) blocks until the DAP
client has finished configuring breakpoints, so breakpoints set before then
are already applied by the time the target starts running.
"""

import json
import sys


def _detect_host():
    """Return the address debugpy should bind to on this runtime.

    A board that has an address of its own reports it, so tooling never has
    to guess or hardcode a device IP. Interfaces are tried cheapest first: a
    wired `LAN` is up without anything having to associate, while
    constructing a `WLAN` starts the wifi driver on some ports. Only an
    interface that is already active is asked, since bringing one up is the
    caller's business and not a side effect of launching a debug session.

    Everything else - the unix port with no `network` module, a board whose
    interfaces are all down, any error while probing - falls back to binding
    all interfaces, so a probe failure never aborts the launch. The host
    side treats that as "no address" rather than something to connect to.
    """
    try:
        import network
    except ImportError:
        return "0.0.0.0"

    makers = []
    if hasattr(network, "LAN"):
        makers.append(network.LAN)
    if hasattr(network, "WLAN"):
        makers.append(lambda: network.WLAN(network.STA_IF))

    for make in makers:
        try:
            nic = make()
            if not nic.active():
                continue
            try:
                addr = nic.ipconfig("addr4")[0]
            except (AttributeError, ValueError, OSError):
                # Firmware predating ipconfig().
                addr = nic.ifconfig()[0]
        except Exception:
            continue
        if addr and addr != "0.0.0.0":
            return addr
    return "0.0.0.0"


def _parse_args():
    import debugpy

    args = sys.argv[1:]
    target_module = args[0] if len(args) > 0 else "target"
    target_method = args[1] if len(args) > 1 else "main"
    port = int(args[2]) if len(args) > 2 else debugpy.DEFAULT_PORT
    if len(args) > 3:
        raise ValueError(
            "Too many arguments. Usage: mpy_launch_debugpy.py "
            "[target_module] [target_method] [port]"
        )
    return target_module, target_method, port


def _run():
    # Before `import debugpy`: a firmware built without MICROPY_PY_SYS_SETTRACE
    # cannot be debugged at all, and debugpy's own import fails on it with a
    # message about its internals rather than about the firmware. Checking
    # first is what makes this message reachable.
    if not hasattr(sys, "settrace"):
        print(
            "sys.settrace is not available. You need a firmware compiled with "
            "MICROPY_PY_SYS_SETTRACE."
        )
        return

    import debugpy

    target_module, target_method, port = _parse_args()
    print(f"Target module: {target_module}")
    print(f"Target method: {target_method}")

    if not hasattr(sys, "settrace"):
        print(
            "sys.settrace is not available. You need a firmware compiled with debugging features."
        )
        return

    host = _detect_host()
    actual_host, actual_port = debugpy.listen(host=host, port=port)
    print(f"Debug server listening on {actual_host}:{actual_port}")

    caps = debugpy.get_capabilities()
    # Exactly one MPDBG-READY line, valid JSON, nothing else on this line.
    print("MPDBG-READY " + json.dumps({"host": actual_host, "port": actual_port, "caps": caps}))

    print("Waiting for the client to finish configuring (configurationDone)...")
    if not debugpy.wait_for_client():
        print(
            "[DAP] No client finished configuring (timed out or disconnected) - "
            "not running the target under a dead debug session."
        )
        debugpy.disconnect()
        return

    debugpy.debug_this_thread()

    # Imported only once a client is configured: the module's top-level code
    # runs on import, and it should run under the debugger with the client's
    # breakpoints already in place, not before the session exists.
    try:
        target = __import__(target_module, None, None, ("*",))
    except ImportError as e:
        print(f"Error importing target module '{target_module}': {e}")
        return

    method = getattr(target, target_method, None)
    if method is None:
        print(f"Method '{target_method}' not found in module '{target_module}'")
        return

    result = method()

    print("Target completed successfully!")
    if result is None:
        print("No result returned from target method")
    else:
        print("Result type:", type(result))
        print("Result:", result)


# Guarded so importing this module does not run device boot code: it ships as
# a resource inside the mpremote package, where a package walk or autodoc pass
# would otherwise execute it on the host. Both real invocations - `micropython
# mpy_launch_debugpy.py ...` and the raw-REPL exec mpremote performs - run it
# as __main__.
if __name__ == "__main__":
    try:
        _run()
    except KeyboardInterrupt:
        print("\nInterrupted by user")
    except Exception as e:
        print(f"Error: {e}")
