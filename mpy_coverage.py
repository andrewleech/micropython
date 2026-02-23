# MicroPython settrace-based code coverage tracer

import sys
import json

_executed = {}
_executable = {}
_arcs = {}
_last_line_stack = []  # Stack of (filename, last_line) for nested calls
_include = None
_exclude = None
_collect_executable = False
_collect_arcs = False
_seen_codes = set()
_test_script = None


def _should_trace(filename):
    if "mpy_coverage" in filename:
        return False
    if _include is not None:
        if not any(pattern in filename for pattern in _include):
            return False
    if _exclude is not None:
        if any(pattern in filename for pattern in _exclude):
            return False
    return True


def _local_trace(frame, event, arg):
    if event == "line":
        filename = frame.f_code.co_filename
        lineno = frame.f_lineno
        if filename not in _executed:
            _executed[filename] = set()
        _executed[filename].add(lineno)
        if _collect_arcs and _last_line_stack:
            entry = _last_line_stack[-1]
            if filename not in _arcs:
                _arcs[filename] = set()
            if entry[1] is not None and entry[0] == filename:
                _arcs[filename].add((entry[1], lineno))
            _last_line_stack[-1] = (filename, lineno)
    elif event == "return" and _collect_arcs:
        filename = frame.f_code.co_filename
        if _last_line_stack:
            entry = _last_line_stack.pop()
            if entry[1] is not None and entry[0] == filename:
                if filename not in _arcs:
                    _arcs[filename] = set()
                _arcs[filename].add((entry[1], -frame.f_code.co_firstlineno))
    return _local_trace


def _global_trace(frame, event, arg):
    if event == "call":
        filename = frame.f_code.co_filename
        if not _should_trace(filename):
            return None

        if _collect_executable:
            code_id = id(frame.f_code)
            if code_id not in _seen_codes:
                _seen_codes.add(code_id)
                if filename not in _executable:
                    _executable[filename] = set()
                try:
                    for start, end, line_no in frame.f_code.co_lines():
                        if line_no > 0:
                            _executable[filename].add(line_no)
                except (AttributeError, TypeError):
                    pass

        if _collect_arcs:
            if filename not in _arcs:
                _arcs[filename] = set()
            # Entry arc: from negative first line to the actual first line
            _arcs[filename].add((-frame.f_code.co_firstlineno, frame.f_lineno))
            _last_line_stack.append((filename, frame.f_lineno))

        return _local_trace
    return None


def start(
    include=None, exclude=None, collect_executable=False, collect_arcs=False, test_script=None
):
    global _include, _exclude, _collect_executable, _collect_arcs, _test_script
    _executed.clear()
    _executable.clear()
    _arcs.clear()
    _last_line_stack.clear()
    _seen_codes.clear()
    _include = include
    _exclude = exclude
    _collect_executable = collect_executable
    _collect_arcs = collect_arcs
    _test_script = test_script
    sys.settrace(_global_trace)


def stop():
    sys.settrace(None)


def get_data():
    data = {"executed": {}}
    if _test_script is not None:
        data["_metadata"] = {"test_script": _test_script}
    for filename, lines in _executed.items():
        data["executed"][filename] = sorted(list(lines))
    if _collect_executable:
        data["executable"] = {}
        for filename, lines in _executable.items():
            data["executable"][filename] = sorted(list(lines))
    if _collect_arcs:
        data["arcs"] = {}
        for filename, arcs in _arcs.items():
            data["arcs"][filename] = sorted([list(a) for a in arcs])
    return data


def export_json(path=None):
    data = get_data()
    json_str = json.dumps(data)
    if path is None:
        print("---MPY_COV_START---")
        print(json_str)
        print("---MPY_COV_END---")
    else:
        with open(path, "w") as f:
            f.write(json_str)
            f.write("\n")


class coverage:
    def __init__(
        self,
        include=None,
        exclude=None,
        collect_executable=False,
        collect_arcs=False,
        test_script=None,
    ):
        self.include = include
        self.exclude = exclude
        self.collect_executable = collect_executable
        self.collect_arcs = collect_arcs
        self.test_script = test_script

    def __enter__(self):
        start(
            self.include,
            self.exclude,
            self.collect_executable,
            self.collect_arcs,
            self.test_script,
        )
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        stop()
