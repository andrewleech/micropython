#!/usr/bin/env python3
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

"""Per-class convergence table for a power-cut sweep.

Input: the JSON-line records of host_sweep (one `trace` record with the
operations of the cuttable boot and one `trial` record per cut), or the same
records produced from a hardware run by tests/mcuboot/fi_sweep.py.

A trial passes when its outcome is "ok": every boot after the cut booted a
valid old or new image, the device settled (a boot without flash operations) and
the settled state is an allowed one for the scenario (host_sweep.c).

A class passes when it has at least one trial in a gated mode and all gated
trials of the class pass.
"""

import collections

import phases

MODE_ORDER = ("before", "after", "between", "tear")


def build(lay, records, gate_modes, op_labels=None):
    traces = [r for r in records if r.get("type") == "trace"]
    trials = [r for r in records if r.get("type") == "trial"]
    notes = []
    if not traces:
        return {
            "pass": False,
            "ops": 0,
            "classes": [],
            "failures": [],
            "outcomes": {},
            "notes": ["no trace record"],
            "modes": [],
            "gate": list(gate_modes),
            "trials": 0,
        }
    ops = traces[0]["ops"]
    for t in traces[1:]:
        if t["ops"] != ops:
            notes.append("operation traces of the runs differ")
    labels = op_labels if op_labels is not None else phases.label_trace(lay, ops)
    label_of = {op["seq"]: cls for op, cls in zip(ops, labels)}
    modes = [m for m in MODE_ORDER if any(t["mode"] == m for t in trials)]

    classes = collections.OrderedDict(
        (c, {"name": c, "ops": 0, "cells": {m: [0, 0] for m in modes}}) for c in phases.CLASSES
    )
    for cls in labels:
        classes[cls]["ops"] += 1
    failures = []
    outcomes = {m: collections.Counter() for m in modes}
    for t in trials:
        cls = label_of.get(t["k"], "P_OTHER")
        t["class"] = cls
        cell = classes[cls]["cells"][t["mode"]]
        cell[1] += 1
        outcomes[t["mode"]][t["outcome"]] += 1
        if t["outcome"] == "ok":
            cell[0] += 1
        else:
            failures.append(t)

    ok = True
    rows = []
    for c in classes.values():
        if c["ops"] == 0:
            continue
        gated = [c["cells"][m] for m in modes if m in gate_modes]
        total = sum(g[1] for g in gated)
        good = sum(g[0] for g in gated)
        if c["name"] == "P_OTHER":
            c["verdict"] = "FAIL (unclassified operations)"
        elif total == 0:
            c["verdict"] = "FAIL (no cuts)"
        elif good != total:
            c["verdict"] = "FAIL"
        else:
            c["verdict"] = "ok"
        if c["verdict"] != "ok":
            ok = False
        rows.append(c)
    gated_failures = [f for f in failures if f["mode"] in gate_modes]
    if gated_failures:
        ok = False
    if notes:
        ok = False
    return {
        "pass": ok,
        "ops": len(ops),
        "classes": rows,
        "failures": failures,
        "outcomes": outcomes,
        "notes": notes,
        "modes": modes,
        "gate": list(gate_modes),
        "trials": len(trials),
        "fail_closed": sum(1 for t in trials if t["outcome"] == "ok" and t.get("final") == 0),
    }


def _cell(c, mode):
    ok, total = c["cells"][mode]
    return "-" if total == 0 else "%d/%d" % (ok, total)


def describe(t):
    op = t["op"]
    s = "k=%d %s%s %s dev%d off=0x%x len=0x%x %s -> %s" % (
        t["k"],
        t["mode"],
        (" tear=%d" % t["tear"] + (" rep=%d" % t["rep"] if t.get("rep") else ""))
        if t["mode"] in ("between", "tear")
        else "",
        op["kind"],
        op["dev"],
        op["off"],
        op["len"],
        t.get("class", "?"),
        t["outcome"],
    )
    if t.get("halt"):
        s += " (%s)" % t["halt"]
    s += " final=%s boots=%d" % (t.get("final"), t["boots"])
    return s


def render(title, rep, max_failures=12):
    lines = []
    modes = rep["modes"]
    lines.append(
        "== %s: %d flash operations in the cuttable boot, %d trials =="
        % (title, rep["ops"], rep["trials"])
    )
    head = "%-16s %5s" % ("class", "ops")
    for m in modes:
        head += " %9s" % (m if m in rep["gate"] else m + "*")
    head += "  verdict"
    lines.append(head)
    tot = {m: [0, 0] for m in modes}
    for c in rep["classes"]:
        row = "%-16s %5d" % (c["name"], c["ops"])
        for m in modes:
            row += " %9s" % _cell(c, m)
            tot[m][0] += c["cells"][m][0]
            tot[m][1] += c["cells"][m][1]
        row += "  " + c["verdict"]
        lines.append(row)
    row = "%-16s %5d" % ("total", rep["ops"])
    for m in modes:
        row += " %9s" % ("%d/%d" % tuple(tot[m]))
    lines.append(row)
    if any(m not in rep["gate"] for m in modes):
        lines.append("* informational, not part of the verdict")
    for m in modes:
        bad = {k: v for k, v in rep["outcomes"][m].items() if k != "ok"}
        if bad:
            lines.append(
                "%s outcomes: %s" % (m, ", ".join("%s %d" % kv for kv in sorted(bad.items())))
            )
    shown = 0
    for f in rep["failures"]:
        if shown >= max_failures:
            lines.append("... %d more failing trials" % (len(rep["failures"]) - shown))
            break
        lines.append("  " + describe(f))
        shown += 1
    if rep.get("fail_closed"):
        lines.append(
            "fail closed: %d of %d trials ended without a valid image (recovery front end, nothing started)"
            % (rep["fail_closed"], rep["trials"])
        )
    for n in rep["notes"]:
        lines.append("note: " + n)
    lines.append("scenario verdict: %s" % ("PASS" if rep["pass"] else "FAIL"))
    return "\n".join(lines)
