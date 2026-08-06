import binascii
import codecs
import errno
import hashlib
import json
import os
import pkgutil
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
import zlib

import serial.tools.list_ports

from .transport import TransportError, TransportExecError, stdout_write_bytes
from .transport_serial import SerialTransport
from .romfs import make_romfs, VfsRomWriter
from .mpdebug_config import find_config, resolve_target, target_hint, warn_if_tty_device
from . import mpdebug_handshake
from . import dap_log


class CommandError(Exception):
    pass


def do_connect(state, args=None, device=None):
    dev = device if device is not None else (args.device[0] if args else "auto")
    do_disconnect(state)

    try:
        if dev == "list":
            # List attached devices.
            for p in sorted(serial.tools.list_ports.comports()):
                print(
                    "{} {} {:04x}:{:04x} {} {}".format(
                        p.device,
                        p.serial_number,
                        p.vid if isinstance(p.vid, int) else 0,
                        p.pid if isinstance(p.pid, int) else 0,
                        p.manufacturer,
                        p.product,
                    )
                )
            # Don't do implicit REPL command.
            state.did_action()
        elif dev == "auto":
            # Auto-detect and auto-connect to the first available USB serial port.
            for p in sorted(serial.tools.list_ports.comports()):
                if p.vid is not None and p.pid is not None:
                    try:
                        state.transport = SerialTransport(p.device, baudrate=115200)
                        return
                    except TransportError as er:
                        if not er.args[0].startswith("failed to access"):
                            raise er
            raise TransportError("no device found")
        elif dev.startswith("id:"):
            # Search for a device with the given serial number.
            serial_number = dev[len("id:") :]
            dev = None
            for p in serial.tools.list_ports.comports():
                if p.serial_number == serial_number:
                    state.transport = SerialTransport(p.device, baudrate=115200)
                    return
            raise TransportError("no device with serial number {}".format(serial_number))
        else:
            # Connect to the given device.
            if dev.startswith("port:"):
                dev = dev[len("port:") :]
            state.transport = SerialTransport(dev, baudrate=115200)
            return
    except TransportError as er:
        msg = er.args[0]
        if msg.startswith("failed to access"):
            msg += " (it may be in use by another program)"
        raise CommandError(msg)


def do_disconnect(state, _args=None):
    if not state.transport:
        return

    try:
        if state.transport.mounted:
            if not state.transport.in_raw_repl:
                state.transport.enter_raw_repl(soft_reset=False)
            state.transport.umount_local()
        if state.transport.in_raw_repl:
            state.transport.exit_raw_repl()
    except OSError:
        # Ignore any OSError exceptions when shutting down, eg:
        # - filesystem_command will close the connection if it had an error
        # - umounting will fail if serial port disappeared
        pass
    state.transport.close()
    state.transport = None
    state._auto_soft_reset = True


def show_progress_bar(size, total_size, op="copying"):
    if not sys.stdout.isatty():
        return
    verbose_size = 2048
    bar_length = 20
    if total_size < verbose_size:
        return
    elif size >= total_size:
        # Clear progress bar when copy completes
        print("\r" + " " * (13 + len(op) + bar_length) + "\r", end="")
    else:
        bar = size * bar_length // total_size
        progress = size * 100 // total_size
        print(
            "\r ... {} {:3d}% [{}{}]".format(op, progress, "#" * bar, "-" * (bar_length - bar)),
            end="",
        )


def _remote_path_join(a, *b):
    if not a:
        a = "./"
    result = a.rstrip("/")
    for x in b:
        result += "/" + x.strip("/")
    return result


def _remote_path_dirname(a):
    a = a.rsplit("/", 1)
    if len(a) == 1:
        return ""
    else:
        return a[0]


def _remote_path_basename(a):
    return a.rsplit("/", 1)[-1]


def do_filesystem_cp(state, src, dest, multiple, check_hash=False):
    if dest.startswith(":"):
        dest_no_slash = dest.rstrip("/" + os.path.sep + (os.path.altsep or ""))
        dest_exists = state.transport.fs_exists(dest_no_slash[1:])
        dest_isdir = dest_exists and state.transport.fs_isdir(dest_no_slash[1:])

        # A trailing / on dest forces it to be a directory.
        if dest != dest_no_slash:
            if not dest_isdir:
                raise CommandError("cp: destination is not a directory")
            dest = dest_no_slash
    else:
        dest_exists = os.path.exists(dest)
        dest_isdir = dest_exists and os.path.isdir(dest)

    if multiple:
        if not dest_exists:
            raise CommandError("cp: destination does not exist")
        if not dest_isdir:
            raise CommandError("cp: destination is not a directory")

    # Download the contents of source.
    try:
        if src.startswith(":"):
            data = state.transport.fs_readfile(src[1:], progress_callback=show_progress_bar)
            filename = _remote_path_basename(src[1:])
        else:
            with open(src, "rb") as f:
                data = f.read()
            filename = os.path.basename(src)
    except IsADirectoryError:
        raise CommandError("cp: -r not specified; omitting directory")

    # Write back to dest.
    if dest.startswith(":"):
        # If the destination path is just the directory, then add the source filename.
        if dest_isdir:
            dest = ":" + _remote_path_join(dest[1:], filename)

        # Skip copy if the destination file is identical.
        if check_hash:
            try:
                remote_hash = state.transport.fs_hashfile(dest[1:], "sha256")
                source_hash = hashlib.sha256(data).digest()
                # remote_hash will be None if the device doesn't support
                # hashlib.sha256 (and therefore won't match).
                if remote_hash == source_hash:
                    print("Up to date:", dest[1:])
                    return
            except OSError:
                pass

        # Write to remote.
        state.transport.fs_writefile(dest[1:], data, progress_callback=show_progress_bar)
    else:
        # If the destination path is just the directory, then add the source filename.
        if dest_isdir:
            dest = os.path.join(dest, filename)

        # Write to local file.
        with open(dest, "wb") as f:
            f.write(data)


def do_filesystem_recursive_cp(state, src, dest, multiple, check_hash):
    # Ignore trailing / on both src and dest. (Unix cp ignores them too)
    src = src.rstrip("/" + os.path.sep + (os.path.altsep if os.path.altsep else ""))
    dest = dest.rstrip("/" + os.path.sep + (os.path.altsep if os.path.altsep else ""))

    # If the destination directory exists, then we copy into it. Otherwise we
    # use the destination as the target.
    if dest.startswith(":"):
        dest_exists = state.transport.fs_exists(dest[1:])
    else:
        dest_exists = os.path.exists(dest)

    # Recursively find all files to copy from a directory.
    # `dirs` will be a list of dest split paths.
    # `files` will be a list of `(dest split path, src joined path)`.
    dirs = []
    files = []

    # For example, if src=/tmp/foo, with /tmp/foo/x.py and /tmp/foo/a/b/c.py,
    # and if the destination directory exists, then we will have:
    #   dirs = [['foo'], ['foo', 'a'], ['foo', 'a', 'b']]
    #   files = [(['foo', 'x.py'], '/tmp/foo/x.py'), (['foo', 'a', 'b', 'c.py'], '/tmp/foo/a/b/c.py')]
    # If the destination doesn't exist, then we will have:
    #   dirs = [['a'], ['a', 'b']]
    #   files = [(['x.py'], '/tmp/foo/x.py'), (['a', 'b', 'c.py'], '/tmp/foo/a/b/c.py')]

    def _list_recursive(base, src_path, dest_path, src_join_fun, src_isdir_fun, src_listdir_fun):
        src_path_joined = src_join_fun(base, *src_path)
        if src_isdir_fun(src_path_joined):
            if dest_path:
                dirs.append(dest_path)
            for entry in src_listdir_fun(src_path_joined):
                _list_recursive(
                    base,
                    src_path + [entry],
                    dest_path + [entry],
                    src_join_fun,
                    src_isdir_fun,
                    src_listdir_fun,
                )
        else:
            files.append(
                (
                    dest_path,
                    src_path_joined,
                )
            )

    if src.startswith(":"):
        src_dirname = [_remote_path_basename(src[1:])]
        dest_dirname = src_dirname if dest_exists else []
        _list_recursive(
            _remote_path_dirname(src[1:]),
            src_dirname,
            dest_dirname,
            src_join_fun=_remote_path_join,
            src_isdir_fun=state.transport.fs_isdir,
            src_listdir_fun=lambda p: [x.name for x in state.transport.fs_listdir(p)],
        )
    else:
        src_dirname = [os.path.basename(src)]
        dest_dirname = src_dirname if dest_exists else []
        _list_recursive(
            os.path.dirname(src),
            src_dirname,
            dest_dirname,
            src_join_fun=os.path.join,
            src_isdir_fun=os.path.isdir,
            src_listdir_fun=os.listdir,
        )

    # If no directories were encountered then we must have just had a file.
    if not dirs:
        return do_filesystem_cp(state, src, dest, multiple, check_hash)

    def _mkdir(a, *b):
        try:
            if a.startswith(":"):
                state.transport.fs_mkdir(_remote_path_join(a[1:], *b))
            else:
                os.mkdir(os.path.join(a, *b))
        except FileExistsError:
            pass

    # Create the destination if necessary.
    if not dest_exists:
        _mkdir(dest)

    # Create all sub-directories relative to the destination.
    for d in dirs:
        _mkdir(dest, *d)

    # Copy all files, in sorted order to help it be deterministic.
    files.sort()
    for dest_path_split, src_path_joined in files:
        if src.startswith(":"):
            src_path_joined = ":" + src_path_joined

        if dest.startswith(":"):
            dest_path_joined = ":" + _remote_path_join(dest[1:], *dest_path_split)
        else:
            dest_path_joined = os.path.join(dest, *dest_path_split)

        do_filesystem_cp(state, src_path_joined, dest_path_joined, False, check_hash)


def do_filesystem_recursive_rm(state, path, args):
    if state.transport.fs_isdir(path):
        if state.transport.mounted:
            r_cwd = state.transport.eval("os.getcwd()")
            abs_path = os.path.normpath(
                os.path.join(r_cwd, path) if not os.path.isabs(path) else path
            )
            if isinstance(state.transport, SerialTransport) and abs_path.startswith(
                f"{SerialTransport.fs_hook_mount}/"
            ):
                raise CommandError(
                    f"rm -r not permitted on {SerialTransport.fs_hook_mount} directory"
                )
        for entry in state.transport.fs_listdir(path):
            do_filesystem_recursive_rm(state, _remote_path_join(path, entry.name), args)
        if path:
            try:
                state.transport.fs_rmdir(path)
                if args.verbose:
                    print(f"removed directory: '{path}'")
            except OSError as e:
                if e.errno != errno.EINVAL:  # not vfs mountpoint
                    raise CommandError(
                        f"rm -r: cannot remove :{path} {os.strerror(e.errno) if e.errno else ''}"
                    ) from e
                if args.verbose:
                    print(f"skipped: '{path}' (vfs mountpoint)")
    else:
        state.transport.fs_rmfile(path)
        if args.verbose:
            print(f"removed: '{path}'")


def human_size(size, decimals=1):
    for unit in ["B", "K", "M", "G", "T"]:
        if size < 1024.0 or unit == "T":
            break
        size /= 1024.0
    return f"{size:.{decimals}f}{unit}" if unit != "B" else f"{int(size)}"


def do_filesystem_tree(state, path, args):
    """Print a tree of the device's filesystem starting at path."""
    connectors = ("├── ", "└── ")

    def _tree_recursive(path, prefix=""):
        entries = state.transport.fs_listdir(path)
        entries.sort(key=lambda e: e.name)
        for i, entry in enumerate(entries):
            connector = connectors[1] if i == len(entries) - 1 else connectors[0]
            is_dir = entry.st_mode & 0x4000  # Directory
            size_str = ""
            # most MicroPython filesystems don't support st_size on directories, reduce clutter
            if entry.st_size > 0 or not is_dir:
                if args.size:
                    size_str = f"[{entry.st_size:>9}]  "
                elif args.human:
                    size_str = f"[{human_size(entry.st_size):>6}]  "
            print(f"{prefix}{connector}{size_str}{entry.name}")
            if is_dir:
                _tree_recursive(
                    _remote_path_join(path, entry.name),
                    prefix + ("    " if i == len(entries) - 1 else "│   "),
                )

    if not path or path == ".":
        path = state.transport.exec("import os;print(os.getcwd())").strip().decode("utf-8")
    if not (path == "." or state.transport.fs_isdir(path)):
        raise CommandError(f"tree: '{path}' is not a directory")
    if args.verbose:
        print(f":{path} on {state.transport.device_name}")
    else:
        print(f":{path}")
    _tree_recursive(path)


def do_filesystem(state, args):
    state.ensure_raw_repl()
    state.did_action()

    command = args.command[0]
    paths = args.path

    if command == "cat":
        # Don't do verbose output for `cat` unless explicitly requested.
        verbose = args.verbose is True
    else:
        verbose = args.verbose is not False

    if command == "cp":
        # Note: cp requires the user to specify local/remote explicitly via
        # leading ':'.

        # The last argument must be the destination.
        if len(paths) <= 1:
            raise CommandError("cp: missing destination path")
        cp_dest = paths[-1]
        paths = paths[:-1]
    else:
        # All other commands implicitly use remote paths. Strip the
        # leading ':' if the user included them.
        paths = [path[1:] if path.startswith(":") else path for path in paths]

    # ls and tree implicitly lists the cwd.
    if command in ("ls", "tree") and not paths:
        paths = [""]

    try:
        # Handle each path sequentially.
        for path in paths:
            if verbose:
                if command == "cp":
                    print("{} {} {}".format(command, path, cp_dest))
                else:
                    print("{} :{}".format(command, path))

            if command == "cat":
                state.transport.fs_printfile(path)
            elif command == "ls":
                for result in state.transport.fs_listdir(path):
                    print(
                        "{:12} {}{}".format(
                            result.st_size, result.name, "/" if result.st_mode & 0x4000 else ""
                        )
                    )
            elif command == "mkdir":
                state.transport.fs_mkdir(path)
            elif command == "rm":
                if args.recursive:
                    do_filesystem_recursive_rm(state, path, args)
                else:
                    state.transport.fs_rmfile(path)
            elif command == "rmdir":
                state.transport.fs_rmdir(path)
            elif command == "touch":
                state.transport.fs_touchfile(path)
            elif command.endswith("sum") and command[-4].isdigit():
                digest = state.transport.fs_hashfile(path, command[:-3])
                print(digest.hex())
            elif command == "cp":
                if args.recursive:
                    do_filesystem_recursive_cp(
                        state, path, cp_dest, len(paths) > 1, not args.force
                    )
                else:
                    do_filesystem_cp(state, path, cp_dest, len(paths) > 1, not args.force)
            elif command == "tree":
                do_filesystem_tree(state, path, args)
    except OSError as er:
        raise CommandError("{}: {}: {}.".format(command, er.strerror, os.strerror(er.errno)))
    except TransportError as er:
        raise CommandError("Error with transport:\n{}".format(er.args[0]))


def do_edit(state, args):
    state.ensure_raw_repl()
    state.did_action()

    if not os.getenv("EDITOR"):
        raise CommandError("edit: $EDITOR not set")
    for src in args.files:
        src = src.lstrip(":")
        dest_fd, dest = tempfile.mkstemp(suffix=os.path.basename(src))
        try:
            print("edit :%s" % (src,))
            state.transport.fs_touchfile(src)
            data = state.transport.fs_readfile(src, progress_callback=show_progress_bar)
            with open(dest_fd, "wb") as f:
                f.write(data)
            if os.system('%s "%s"' % (os.getenv("EDITOR"), dest)) == 0:
                with open(dest, "rb") as f:
                    state.transport.fs_writefile(
                        src, f.read(), progress_callback=show_progress_bar
                    )
        finally:
            os.unlink(dest)


def _do_execbuffer(state, buf, follow):
    state.ensure_raw_repl()
    state.did_action()

    try:
        state.transport.exec_raw_no_follow(buf)
        if follow:
            ret, ret_err = state.transport.follow(timeout=None, data_consumer=stdout_write_bytes)
            if ret_err:
                stdout_write_bytes(ret_err)
                sys.exit(1)
    except TransportError as er:
        raise CommandError(er.args[0])
    except KeyboardInterrupt:
        sys.exit(1)


def do_exec(state, args):
    _do_execbuffer(state, args.expr[0], args.follow)


def do_eval(state, args):
    buf = "print(" + args.expr[0] + ")"
    _do_execbuffer(state, buf, True)


def do_run(state, args):
    filename = args.path[0]
    try:
        with open(filename, "rb") as f:
            buf = f.read()
    except OSError:
        raise CommandError(f"could not read file '{filename}'")
    _do_execbuffer(state, buf, args.follow)


def _parse_program_spec(spec):
    # "module[:method]", method defaults to "main" as advertised in --help.
    parts = spec.split(":")
    if len(parts) > 2 or not parts[0] or not parts[-1]:
        raise CommandError(f"invalid program {spec!r}: expected 'module[:method]'")
    module = parts[0]
    method = parts[1] if len(parts) == 2 else "main"
    if module.endswith(".py") or "/" in module or "\\" in module:
        raise CommandError(f"invalid program {spec!r}: expected an import name, not a path")
    return module, method


def _debug_boot_script(module, method, port):
    # Raw REPL exec has no OS argv, so sys.argv is injected here to preserve
    # mpy_launch_debugpy.py's own argv contract ([module] [method] [port]).
    # sys.argv is rebound in place (not reassigned): the `sys` module dict is
    # read-only on MicroPython, so `sys.argv = [...]` raises AttributeError.
    # port=None omits the argv element so the device applies its own default
    # port instead of the host choosing one.
    script = pkgutil.get_data(__package__, "mpy_launch_debugpy.py").decode()
    argv = ["mpy_launch_debugpy.py", module, method]
    if port is not None:
        argv.append(str(port))
    preamble = "import sys\nsys.argv[:] = [{}]\n".format(", ".join(repr(a) for a in argv))
    return preamble + script


_POLL_S = 0.2  # read_until() poll cadence for both the handshake scan and the error drain below


def _mpdbg_error(transport, rest):
    # The raw REPL frames output as <stdout> \x04 <exception> \x04>, so the
    # traceback follows the marker that ends stdout: collect it, since a script
    # that dies on import prints nothing to stdout and the exception is the
    # only useful thing to report. `rest` is the raw bytes already read past
    # the marker, not text decoded by the handshake scan, so a non-ASCII
    # exception message isn't put through a second decode/encode round trip.
    eof = b"\x04"
    deadline = time.monotonic() + 1
    while eof not in rest and time.monotonic() < deadline:
        try:
            rest += transport.read_until(1, eof, timeout=_POLL_S, timeout_overall=_POLL_S)
        except Exception:
            break
    text = rest.partition(eof)[0].decode(errors="replace").strip()
    return f"; device error: {text}" if text else ""


def _read_mpdbg_ready(
    transport, timeout, control_kind=mpdebug_handshake.CONTROL_KIND_SERIAL, known_host=None
):
    # Boot script output is normal print()s until its one handshake line;
    # echo everything else and stop at the line carrying the JSON payload.
    # read_chunk polls in short windows rather than blocking for the whole
    # `timeout` so a device that exits without a trailing newline (raw REPL's
    # `\x04\x04` with no `\n`) is caught within one poll instead of stalling.
    # Each poll is clamped to what's left of `timeout` so the wait can't
    # overshoot it by a full poll window.
    deadline = time.monotonic() + timeout
    # An incremental decoder carries an incomplete multi-byte UTF-8 sequence
    # across poll boundaries instead of decoding each raw chunk in isolation,
    # which would turn a split character into two separate replacement chars.
    decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
    raw = bytearray()  # mirrors what's been decoded, for _mpdbg_error below

    def read_chunk():
        poll = max(0.0, min(_POLL_S, deadline - time.monotonic()))
        chunk = transport.read_until(1, b"\n", timeout=poll, timeout_overall=poll)
        raw.extend(chunk)
        return decoder.decode(chunk)

    def on_eof(rest):
        # Recover the bytes after the marker from `raw` rather than
        # re-encoding `rest` (see _mpdbg_error).
        return _mpdbg_error(transport, bytes(raw[raw.index(b"\x04") + 1 :]))

    try:
        return mpdebug_handshake.read_handshake(
            read_chunk,
            timeout,
            control_kind,
            known_host=known_host,
            on_line=lambda line: stdout_write_bytes(line.encode()),
            eof="\x04",  # raw-REPL end-of-output marker: script exited, nothing more coming
            on_eof=on_eof,
        )
    except mpdebug_handshake.HandshakeError as er:
        raise CommandError(str(er))


def _check_requires(resolved, caps):
    if resolved is not None and resolved.requires:
        missing = [c for c in resolved.requires if caps.get(c) is not True]
        if missing:
            raise CommandError(
                f"target {resolved.name!r} requires {', '.join(missing)}, which this "
                f"firmware does not provide (probed caps: {caps})"
            )


def _report_debug_result(handshake):
    # flush=True: a caller reading this over a pipe (s7.1's extension, a test
    # harness) must see it as soon as it's printed, not whenever Python's
    # block-buffering (the default for a non-tty stdout) next drains.
    host, port, caps = handshake["host"], handshake["port"], handshake["caps"]
    print("debug server listening on {}:{}".format(host, port), flush=True)
    print("capabilities:", caps, flush=True)
    # The device's own MPDBG-READY line (raw bind address, possibly a
    # wildcard) is consumed by the handshake parser and never echoed; re-emit
    # it with the resolved host as the one line a tool watching this
    # process's stdout (e.g. s7.1's extension) can parse.
    print(
        mpdebug_handshake.PREFIX + json.dumps({"host": host, "port": port, "caps": caps}),
        flush=True,
    )
    return handshake


def _start_dap_log(dap_log_arg, handshake, bind_port=0):
    # Neither transport puts the byte stream through mpremote, so logging
    # means interposing a proxy in front of the device's real endpoint and
    # reporting *its* host/port instead - a client that got the device's own
    # endpoint would attach straight past the logger.
    # dap_log_arg is True for --dap-log with no --dap-log-file, else the path.
    path = dap_log_arg if isinstance(dap_log_arg, str) else dap_log.default_log_path()
    try:
        logger = dap_log.DapLogger(path)
    except OSError as er:
        raise CommandError(f"--dap-log could not open {path!r}: {er}") from None
    try:
        proxy = dap_log.DapProxy(handshake["host"], handshake["port"], logger, bind_port=bind_port)
    except OSError as er:
        logger.close()
        raise CommandError(f"--dap-log could not bind a proxy port: {er}") from None
    proxy.start()
    print(f"logging DAP traffic to {path!r}", flush=True)
    reported = dict(handshake, host=proxy.host, port=proxy.port)
    return proxy, reported


def _dap_log_ports(port, dap_log_arg):
    """Split a single `--port` between the device and the `--dap-log` proxy.

    Without `--dap-log`, `port` is what the device binds, unchanged. With
    it, `--port` names the endpoint a client connects to (so a launch.json
    pinning a port still goes through the logger): the device gets a freshly
    reserved port of its own, and the requested port becomes the proxy's
    bind port instead of an OS-assigned one. Returns (device_port,
    proxy_bind_port).

    With no `--port`, the device is still moved off its own default rather
    than left there: otherwise it stays reachable on the conventional port
    while the proxy sits somewhere else, and a client aimed at that
    conventional port connects straight to the device and is logged nowhere.
    Moving it means such a client fails to connect instead - loudly wrong
    rather than quietly unlogged - and the endpoint to use is the one
    reported.
    """
    if not dap_log_arg:
        return port, 0
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        device_port = s.getsockname()[1]
    return device_port, (port or 0)


def _resolve_unix_binary(resolved):
    env_path = os.environ.get("MPY_DEBUG_FIRMWARE")
    if env_path:
        if not os.path.isfile(env_path):
            raise CommandError(f"MPY_DEBUG_FIRMWARE={env_path!r} does not exist")
        return env_path

    firmware = resolved.firmware if resolved is not None else None
    if not firmware:
        raise CommandError(
            "no unix debug binary found: set MPY_DEBUG_FIRMWARE to a built "
            "micropython binary, or build one (e.g. `make -C ports/unix`) and "
            "point the target's 'firmware' at it in mpdebug.toml"
        )
    if firmware == "system":
        found = shutil.which("micropython")
        if not found:
            raise CommandError(
                f"target {resolved.name!r} firmware is 'system' but no 'micropython' is on PATH"
            )
        return found
    # A relative path (one containing a separator) is resolved against the
    # config file's directory, not the cwd, so the same mpdebug.toml works
    # regardless of where mpremote is invoked from - matching how the config
    # file itself is found. A bare name with no separator that isn't a file
    # relative to the cwd is assumed to be a firmware.toml variant id (e.g.
    # "fw-f9d7c96b96"), which this tool cannot fetch - firmware/firmware.toml
    # and its verify/fetch machinery are part of the wrapper repo, out of
    # reach from inside micropython/tools/mpremote.
    if os.sep in firmware and not os.path.isabs(firmware):
        config_path = find_config()
        if config_path:
            firmware = os.path.join(os.path.dirname(config_path), firmware)
    elif os.sep not in firmware and not os.path.isfile(firmware):
        raise CommandError(
            f"target {resolved.name!r} firmware {firmware!r} names a "
            "firmware-manifest variant, which this tool cannot fetch; build it "
            "(e.g. `make -C ports/unix`), or set MPY_DEBUG_FIRMWARE to a built "
            "micropython binary"
        )
    if not os.path.isfile(firmware):
        raise CommandError(f"target {resolved.name!r} firmware {firmware!r} does not exist")
    return firmware


# ports/unix/mpconfigport.h's MICROPY_PY_SYS_PATH_DEFAULT: what the unix port
# puts on sys.path when MICROPYPATH is unset. Setting MICROPYPATH at all
# suppresses these, so they are appended explicitly below rather than relied
# on implicitly - debugpy may live in any of them (frozen, mip-installed) or
# in a path the caller already put on MICROPYPATH.
_UNIX_SYS_PATH_DEFAULT = (".frozen", "~/.micropython/lib", "/usr/lib/micropython")


def _unix_env():
    # MICROPYPATH is rebuilt rather than inherited verbatim: the target
    # module's project directory goes on the front, and the port's own
    # defaults go on the back, with whatever the caller already set kept in
    # between - so this always reaches the project's modules and never
    # silently drops the caller's or the port's own module locations.
    config_path = find_config()
    project_dir = os.path.dirname(config_path) if config_path else os.getcwd()
    env = dict(os.environ)
    caller_parts = [p for p in env.get("MICROPYPATH", "").split(":") if p]
    parts = [project_dir] + caller_parts + list(_UNIX_SYS_PATH_DEFAULT)
    seen = set()
    deduped = []
    for p in parts:
        if p not in seen:
            seen.add(p)
            deduped.append(p)
    env["MICROPYPATH"] = ":".join(deduped)
    return env


def _reap(proc):
    # terminate -> kill ladder: an unreaped subprocess keeps its debug-server
    # port bound, and the next run collides with it. SIGINT is ignored for
    # the duration so a second Ctrl-C during the ladder can't abandon it
    # half-done.
    if proc.poll() is not None:
        return
    try:
        old_handler = signal.signal(signal.SIGINT, signal.SIG_IGN)
    except ValueError:
        old_handler = None  # not called from the main thread
    try:
        proc.terminate()
        try:
            proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=2)
    finally:
        if old_handler is not None:
            signal.signal(signal.SIGINT, old_handler)


_UNIX_EOF_MARKER = "\x00mpremote-debug-unix-eof\x00"  # never appears in real program output


def _do_debug_unix(resolved, module, method, port, timeout, dap_log_arg, dap_log_bind_port):
    proxy = None  # set once the handshake is in, if --dap-log was given
    try:
        # The non-blocking fd handling below is POSIX-only, unlike the rest of
        # mpremote, which supports Windows.
        import fcntl
    except ImportError:
        raise CommandError(
            "'debug' with a unix target needs a POSIX host (no fcntl here)"
        ) from None

    binary = _resolve_unix_binary(resolved)

    # The boot script ships as a package resource, not a path into this repo
    # (s5.1); write it out so the subprocess, which needs a real file to
    # run, can read it.
    tmpdir = tempfile.mkdtemp(prefix="mpremote-debug-unix-")
    try:
        script_path = os.path.join(tmpdir, "mpy_launch_debugpy.py")
        with open(script_path, "wb") as f:
            f.write(pkgutil.get_data(__package__, "mpy_launch_debugpy.py"))
    except Exception:
        shutil.rmtree(tmpdir, ignore_errors=True)
        raise

    argv = [binary, script_path, module, method]
    if port is not None:
        argv.append(str(port))

    try:
        proc = subprocess.Popen(
            argv, env=_unix_env(), stdout=subprocess.PIPE, stderr=subprocess.STDOUT
        )
    except OSError as er:
        shutil.rmtree(tmpdir, ignore_errors=True)
        raise CommandError(f"failed to launch {binary!r}: {er}")

    def cleanup():
        # SIGINT ignored for the whole ladder, not just _reap's: a second
        # Ctrl-C landing during rmtree must not abandon cleanup half-done.
        try:
            old_handler = signal.signal(signal.SIGINT, signal.SIG_IGN)
        except ValueError:
            old_handler = None  # not called from the main thread
        try:
            _reap(proc)
            if proxy is not None:
                proxy.close()
            shutil.rmtree(tmpdir, ignore_errors=True)
        finally:
            if old_handler is not None:
                signal.signal(signal.SIGINT, old_handler)

    # A live child now exists, so every signal that would otherwise kill this
    # process outright has to reap it first: SIGTERM (what Node's
    # child.kill(), `timeout`, systemd and CI teardown send) and SIGHUP (what
    # the kernel sends the foreground group when a terminal window closes on a
    # running session). Turned into SystemExit so they land in the same
    # BaseException handler below as any other escape path.
    _reaping_signals = [signal.SIGTERM]
    if hasattr(signal, "SIGHUP"):  # POSIX-only, keep Windows imports clean
        _reaping_signals.append(signal.SIGHUP)
    old_handlers = {}
    for _sig in _reaping_signals:
        try:
            old_handlers[_sig] = signal.signal(_sig, lambda *_a: sys.exit(1))
        except ValueError:
            pass  # not called from the main thread
    try:
        # Everything from here on runs against a live child: any exception
        # that isn't handled below (a raw OSError out of a read, a
        # BrokenPipeError from a downstream reader going away, ...) must
        # still reap it rather than leaving it bound to its port forever.
        try:
            fl = fcntl.fcntl(proc.stdout.fileno(), fcntl.F_GETFL)
            fcntl.fcntl(proc.stdout.fileno(), fcntl.F_SETFL, fl | os.O_NONBLOCK)
            decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
            deadline = time.monotonic() + timeout
            ended = False

            def read_chunk():
                nonlocal ended
                if ended:
                    return ""
                poll = max(0.0, min(_POLL_S, deadline - time.monotonic()))
                try:
                    chunk = os.read(proc.stdout.fileno(), 4096)
                except BlockingIOError:
                    time.sleep(poll)
                    return ""
                if chunk == b"":
                    # A pipe read only ever returns b"" at true EOF (every write
                    # end closed) - the subprocess exited without printing a
                    # handshake.
                    ended = True
                    return _UNIX_EOF_MARKER
                return decoder.decode(chunk)

            def on_eof(rest):
                try:
                    code = proc.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    return "; process closed stdout without exiting"
                return f"; process exited with code {code}"

            try:
                handshake = mpdebug_handshake.read_handshake(
                    read_chunk,
                    timeout,
                    mpdebug_handshake.CONTROL_KIND_UNIX,
                    on_line=lambda line: stdout_write_bytes(line.encode()),
                    eof=_UNIX_EOF_MARKER,
                    on_eof=on_eof,
                )
            except mpdebug_handshake.HandshakeError as er:
                raise CommandError(str(er)) from None

            _check_requires(resolved, handshake["caps"])

            # The interpreter compiles the whole script before executing any of
            # it (same as CPython), so by the time a handshake or an error has
            # come back the temp file has already been fully read and is no
            # longer needed.
            shutil.rmtree(tmpdir, ignore_errors=True)

            if dap_log_arg:
                proxy, reported = _start_dap_log(dap_log_arg, handshake, dap_log_bind_port)
            else:
                reported = handshake
            _report_debug_result(reported)

            # Unlike the serial/network paths, where the firmware runs
            # independently of the host tool, mpremote owns this child: stay
            # attached to its console until it exits on its own (the debug
            # session ran to completion) or the user ends it with Ctrl-C.
            fcntl.fcntl(proc.stdout.fileno(), fcntl.F_SETFL, fl)
            while True:
                chunk = os.read(proc.stdout.fileno(), 4096)
                if not chunk:
                    break
                stdout_write_bytes(chunk)
            # EOF means every write end of the pipe closed, which normally
            # means the child has exited too; wait() picks up its exit status
            # so a crashing debuggee is reported as a failure, not silently
            # swallowed.
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pass
        except KeyboardInterrupt:
            cleanup()
            sys.exit(1)
        except BrokenPipeError:
            cleanup()
            sys.exit(1)
        except BaseException:
            cleanup()
            raise

        if proxy is not None:
            proxy.close()
        _reap(proc)
        if proc.returncode:
            sys.exit(proc.returncode)
        return reported
    finally:
        for _sig, _old in old_handlers.items():
            signal.signal(_sig, _old)


def do_debug(state, args):
    resolved = resolve_target(args.target)

    program_spec = args.program
    if program_spec is None:
        program_spec = (resolved.program if resolved is not None else None) or "target:main"
    module, method = _parse_program_spec(program_spec)

    # Both checked here rather than after a full raw-REPL round trip.
    if args.port == 0:
        raise CommandError(
            "--port 0 is rejected: it asks the system to choose, which the "
            "device can only report back through getsockname(), and no port "
            "in this tree binds it"
        )
    if args.port is not None and not 1 <= args.port <= 65535:
        raise CommandError(f"--port must be between 1 and 65535, got {args.port}")

    if args.dap_log_file is not None and not args.dap_log:
        raise CommandError("--dap-log-file requires --dap-log")
    # False (no logging), True (log to the default path), or an explicit path.
    dap_log_arg = (args.dap_log_file or True) if args.dap_log else False
    # With --dap-log, --port pins the proxy's (client-facing) port instead of
    # the device's; the device gets a freshly reserved port of its own.
    device_port, dap_log_bind_port = _dap_log_ports(args.port, dap_log_arg)

    is_unix = resolved.kind == "unix" if resolved is not None else args.target == "unix"
    if is_unix:
        state.did_action()
        # Reports the endpoint and supervises the child itself (unlike the
        # serial/network path below, mpremote owns this process).
        return _do_debug_unix(
            resolved, module, method, device_port, args.timeout, dap_log_arg, dap_log_bind_port
        )

    if resolved is None:
        # No mpdebug.toml matched (or none exists): args.target is a literal
        # connect string, as it was before named targets existed.
        if args.target == "list":
            raise CommandError("target 'list' is not a debuggable device")
        connect_device = args.target
        warn_if_tty_device(connect_device, "device")
    else:
        connect_device = resolved.device or "auto"
        warn_if_tty_device(connect_device, f"target {resolved.name!r}")

    if state.transport is None or state.transport.device_name != connect_device:
        try:
            do_connect(state, device=connect_device)
        except CommandError as er:
            # A name that is not a configured target is handed to the transport
            # as a connect string, so a mistyped target name surfaces here.
            raise CommandError(f"{er}{target_hint(args.target)}") from None
    state.ensure_raw_repl()
    state.did_action()

    try:
        state.transport.exec_raw_no_follow(_debug_boot_script(module, method, device_port))
        print("waiting for the device to report its debug-server endpoint...", flush=True)
        # A pty peer is a local process by construction (a unix build or QEMU
        # behind a pty pair), so a wildcard bind on it is reachable at the
        # loopback address. Anything else gets no known_host: a
        # socket://host:port or rfc2217://host:port host may be the device
        # itself (esp-link and similar) or a bridge in front of it (ser2net),
        # and guessing wrong points the client at the wrong machine.
        known_host = "127.0.0.1" if getattr(state.transport, "is_pty", False) else None
        handshake = _read_mpdbg_ready(state.transport, timeout=args.timeout, known_host=known_host)
    except TransportError as er:
        raise CommandError(er.args[0])
    except KeyboardInterrupt:
        sys.exit(1)

    _check_requires(resolved, handshake["caps"])

    if not dap_log_arg:
        return _report_debug_result(handshake)

    # Unlike the plain report-and-return above: the device runs independently
    # of mpremote on this path, but the proxy --dap-log inserts does not, so
    # returning now would tear down the very thing the client is about to
    # connect to. Stay attached until the one client session it serves ends,
    # or the user/environment ends it first.
    proxy, reported = _start_dap_log(dap_log_arg, handshake, dap_log_bind_port)
    _report_debug_result(reported)
    print("staying attached to run the --dap-log proxy; Ctrl-C ends it", flush=True)

    def cleanup():
        try:
            old_handler = signal.signal(signal.SIGINT, signal.SIG_IGN)
        except ValueError:
            old_handler = None  # not called from the main thread
        try:
            proxy.close()
        finally:
            if old_handler is not None:
                signal.signal(signal.SIGINT, old_handler)

    # Same reap-on-every-exit-path ladder as _do_debug_unix's child, applied
    # to the proxy instead of a subprocess.
    _reaping_signals = [signal.SIGTERM]
    if hasattr(signal, "SIGHUP"):
        _reaping_signals.append(signal.SIGHUP)
    old_handlers = {}
    for _sig in _reaping_signals:
        try:
            old_handlers[_sig] = signal.signal(_sig, lambda *_a: sys.exit(1))
        except ValueError:
            pass  # not called from the main thread
    try:
        try:
            while not proxy.wait(_POLL_S):
                pass
        except KeyboardInterrupt:
            cleanup()
            sys.exit(1)
        except BaseException:
            cleanup()
            raise
        cleanup()
        return reported
    finally:
        for _sig, _old in old_handlers.items():
            signal.signal(_sig, _old)


def do_mount(state, args):
    state.ensure_raw_repl()
    path = args.path[0]
    state.transport.mount_local(path, unsafe_links=args.unsafe_links)
    print(f"Local directory {path} is mounted at /remote")


def do_umount(state, path):
    state.ensure_raw_repl()
    state.transport.umount_local()


def do_resume(state, _args=None):
    state._auto_soft_reset = False


def do_soft_reset(state, _args=None):
    state.ensure_raw_repl(soft_reset=True)
    state.did_action()


def do_rtc(state, args):
    state.ensure_raw_repl()
    state.did_action()

    state.transport.exec("import machine")

    if args.set:
        import datetime

        now = datetime.datetime.now()
        timetuple = "({}, {}, {}, {}, {}, {}, {}, {})".format(
            now.year,
            now.month,
            now.day,
            now.weekday(),
            now.hour,
            now.minute,
            now.second,
            now.microsecond,
        )
        state.transport.exec("machine.RTC().datetime({})".format(timetuple))
    else:
        print(state.transport.eval("machine.RTC().datetime()"))


def _do_romfs_query_partition(transport, rom_id):
    transport.exec(f"dev=vfs.rom_ioctl(2,{rom_id})")
    if transport.eval("isinstance(dev,int) and dev<0"):
        raise CommandError(f"ROMFS{rom_id} partition not found on device")

    has_object = transport.eval("hasattr(dev,'ioctl')")
    if has_object:
        rom_block_count = transport.eval("dev.ioctl(4,0)")
        rom_block_size = transport.eval("dev.ioctl(5,0)")
        rom_size = rom_block_count * rom_block_size
    else:
        rom_size = transport.eval("len(dev)")
        rom_block_size = transport.eval(f"vfs.rom_ioctl(6,{rom_id})")
        if rom_block_size <= 0:
            rom_block_size = rom_size

    print(
        f"ROMFS{rom_id} partition has size {rom_size} bytes ({rom_size // rom_block_size} blocks of {rom_block_size} bytes each)"
    )

    return has_object, rom_size, rom_block_size


def _do_romfs_query(state, args):
    state.ensure_raw_repl()
    state.did_action()

    # Detect the romfs and get its associated device.
    state.transport.exec("import vfs")
    if not state.transport.eval("hasattr(vfs,'rom_ioctl')"):
        print("ROMFS is not enabled on this device")
        return
    num_rom_partitions = state.transport.eval("vfs.rom_ioctl(1)")
    if num_rom_partitions <= 0:
        print("No ROMFS partitions available")
        return

    for rom_id in range(num_rom_partitions):
        _do_romfs_query_partition(state.transport, rom_id)
        romfs = state.transport.eval("bytes(memoryview(dev)[:12])")
        print(f"  Raw contents: {romfs.hex(':')} ...")
        if not romfs.startswith(b"\xd2\xcd\x31"):
            print("  Not a valid ROMFS")
        else:
            size = 0
            for value in romfs[3:]:
                size = (size << 7) | (value & 0x7F)
                if not value & 0x80:
                    break
            print(f"  ROMFS image size: {size}")


def _do_romfs_build(state, args):
    state.did_action()

    if args.path is None:
        raise CommandError("romfs build: source path not given")

    input_directory = args.path

    if args.output is None:
        output_file = input_directory + ".romfs"
    else:
        output_file = args.output

    romfs = make_romfs(input_directory, mpy_cross=args.mpy)

    print(f"Writing {len(romfs)} bytes to output file {output_file}")
    with open(output_file, "wb") as f:
        f.write(romfs)


def _do_romfs_deploy(state, args):
    state.ensure_raw_repl()
    state.did_action()
    transport = state.transport

    if args.path is None:
        raise CommandError("romfs deploy: source path not given")

    rom_id = args.partition
    romfs_filename = args.path

    # Read in or create the ROMFS filesystem image.
    if os.path.isfile(romfs_filename) and romfs_filename.endswith((".img", ".romfs")):
        with open(romfs_filename, "rb") as f:
            romfs = f.read()
    else:
        romfs = make_romfs(romfs_filename, mpy_cross=args.mpy)
    print(f"Image size is {len(romfs)} bytes")

    # Detect the ROMFS partition and get its associated device.
    state.transport.exec("import vfs")
    if not state.transport.eval("hasattr(vfs,'rom_ioctl')"):
        raise CommandError("ROMFS is not enabled on this device")
    has_object, rom_size, rom_block_size = _do_romfs_query_partition(transport, rom_id)

    # Check if ROMFS image is valid
    if not romfs.startswith(VfsRomWriter.ROMFS_HEADER):
        print("Invalid ROMFS image")
        sys.exit(1)

    # Check if ROMFS filesystem image will fit in the target partition.
    if len(romfs) > rom_size:
        print("ROMFS image is too big for the target partition")
        sys.exit(1)

    # Prepare ROMFS partition for writing.
    transport.exec("import vfs\ntry:\n vfs.umount('/rom')\nexcept:\n pass")
    chunk_size = 4096
    if has_object:
        for offset in range(0, len(romfs), rom_block_size):
            print(f"\rPreparing at offset {offset}", end="")
            transport.exec(f"dev.ioctl(6,{offset // rom_block_size})")
        chunk_size = min(chunk_size, rom_block_size)
    else:
        if rom_block_size < rom_size:
            offset = 0
            while offset < len(romfs):
                print(f"\rPreparing at offset {offset}", end="")
                remain = min(len(romfs) - offset, 32768)
                prepare = (remain + rom_block_size - 1) // rom_block_size * rom_block_size
                rom_min_write = transport.eval(f"vfs.rom_ioctl(3,{rom_id},{offset},{prepare})")
                offset += prepare
        else:
            print("\rPreparing at offset 0", end="")
            rom_min_write = transport.eval(f"vfs.rom_ioctl(3,{rom_id},{len(romfs)})")
        chunk_size = max(chunk_size, rom_min_write)
    print()

    # Detect capabilities of the device to use the fastest method of transfer.
    has_bytes_fromhex = transport.eval("hasattr(bytes,'fromhex')")
    try:
        transport.exec("from binascii import a2b_base64")
        has_a2b_base64 = True
    except TransportExecError:
        has_a2b_base64 = False
    try:
        transport.exec("from io import BytesIO")
        transport.exec("from deflate import DeflateIO,RAW")
        has_deflate_io = True
    except TransportExecError:
        has_deflate_io = False

    # Deploy the ROMFS filesystem image to the device.
    for offset in range(0, len(romfs), chunk_size):
        romfs_chunk = romfs[offset : offset + chunk_size]
        romfs_chunk += bytes(chunk_size - len(romfs_chunk))
        if has_deflate_io:
            # Needs: binascii.a2b_base64, io.BytesIO, deflate.DeflateIO.
            compressor = zlib.compressobj(wbits=-9)
            romfs_chunk_compressed = compressor.compress(romfs_chunk)
            romfs_chunk_compressed += compressor.flush()
            buf = binascii.b2a_base64(romfs_chunk_compressed).strip()
            transport.exec(f"buf=DeflateIO(BytesIO(a2b_base64({buf})),RAW,9).read()")
        elif has_a2b_base64:
            # Needs: binascii.a2b_base64.
            buf = binascii.b2a_base64(romfs_chunk)
            transport.exec(f"buf=a2b_base64({buf})")
        elif has_bytes_fromhex:
            # Needs: bytes.fromhex.
            buf = romfs_chunk.hex()
            transport.exec(f"buf=bytes.fromhex('{buf}')")
        else:
            # Needs nothing special.
            transport.exec("buf=" + repr(romfs_chunk))
        print(f"\rWriting at offset {offset}", end="")
        if has_object:
            transport.exec(
                f"dev.writeblocks({offset // rom_block_size},buf,{offset % rom_block_size})"
            )
        else:
            transport.exec(f"vfs.rom_ioctl(4,{rom_id},{offset},buf)")

    # Complete writing.
    if not has_object:
        transport.eval(f"vfs.rom_ioctl(5,{rom_id})")

    print()
    print("ROMFS image deployed")


def do_romfs(state, args):
    if args.command[0] == "query":
        _do_romfs_query(state, args)
    elif args.command[0] == "build":
        _do_romfs_build(state, args)
    elif args.command[0] == "deploy":
        _do_romfs_deploy(state, args)
    else:
        raise CommandError(
            f"romfs: '{args.command[0]}' is not a command; pass romfs --help for a list"
        )
