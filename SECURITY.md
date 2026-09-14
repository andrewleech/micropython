# Security Policy

This document explains how to report a security vulnerability in MicroPython and what to expect in
response. It covers the MicroPython core (`py/`, `extmod/`, `shared/`), the maintained ports under
`ports/`, and `mpy-cross`.

Issues originating in third-party code vendored under `lib/` (eg mbedtls) or in a vendor SDK
(eg ESP-IDF) should be reported to that project first. Several MicroPython-attributed CVEs have
been dependency issues of this kind (CVE-2025-59438 in mbedtls, CVE-2020-12638 in the ESP-IDF
Wi-Fi stack). We still want to know about them, so we can pull in the fix and advise users, but the
upstream project is where the fix is coordinated. Code you have modified from an official release
is out of scope.

## Reporting a vulnerability

**Please do not report security vulnerabilities through public GitHub issues, Discussions, or
Discord.**

Report privately through one of:

- **GitHub private vulnerability reporting**: open a draft advisory at
  <https://github.com/micropython/micropython/security/advisories/new>. This keeps the report
  private and gives us a place to work on the fix with you. This is the preferred route.
- **Email**: security@micropython.org. To encrypt the report, our PGP key is available on request.

If you are unsure whether an issue is a vulnerability, use the private route. We would rather
receive a report that turns out to be a non-issue than have a real one disclosed publicly before a
fix exists.

### What to include

A good report lets us reproduce and assess the issue quickly. Please include:

- The affected version (shown in the startup banner after boot or a soft reset, `Ctrl-D`, in the
  REPL) or commit hash, and whether you are running an official build or a modified one.
- The affected port, board, and any non-default build configuration
  (`mpconfigport.h` / `mpconfigboard.h` options, native modules, `MICROPY_PREVIEW_VERSION_2`).
- The reproducing code pasted directly into the report, not linked to an external repository (which
  may change), and minimised to the smallest snippet that still triggers the fault.
- For sanitizer-based reports, build with ASan configured correctly, including
  `--param asan-use-after-return=0` in `CFLAGS_EXTRA` and `LDFLAGS_EXTRA`, and paste the full
  sanitizer trace.
- The impact: what an attacker gains, and what access they need to trigger it (eg the ability to
  run arbitrary Python at the REPL, load an untrusted `.mpy`, or supply input to a specific API).
- Any mitigation or workaround you are aware of.

Please avoid destructive proof-of-concept payloads. Reporting is expected to follow the project
[Code of Conduct](CODEOFCONDUCT.md).

## Scope and threat model

MicroPython's memory-safety guarantee applies to the default, managed Python code path. The
following are, by design, outside the managed sandbox, and issues arising solely from their use are
generally not treated as vulnerabilities in MicroPython itself:

- `machine.mem8/16/32` and other direct memory/register access.
- `@micropython.native`, `@micropython.viper`, and inline assembly
  (`@micropython.asm_thumb`, `@micropython.asm_rv32`).
- Native C extension modules (`dynruntime.h` / `.mpy` native modules).

Code using any of these has the same memory-safety profile as C, and securing it is the firmware
author's responsibility. A memory-safety defect in the interpreter's own C runtime, meaning the
compiler, object model, VM dispatch, VFS, or peripheral bindings, reachable from ordinary managed
Python or from untrusted input to a managed API is in scope.

We are particularly interested in reports where untrusted input reaches a managed API and causes
memory corruption, and in any path that lets untrusted data escape the managed model without the
firmware author having opted out of it.

## What to expect after reporting

The project is maintained by a small team. The timelines below are best-effort targets, not
contractual guarantees. We will:

1. Acknowledge your report within 5 business days.
2. Triage it, confirming the issue, determining affected versions and assessing severity, then share
   our assessment with you.
3. Develop and review a fix privately in the GitHub advisory, keeping you informed and credited
   (see below).
4. Coordinate disclosure: publish the fix, the advisory, and, where warranted, a CVE.

### Where issues are handled and recorded

Not every security issue is embargoed:

- Lower-severity, locally-triggered issues (eg a crash that requires running untrusted Python on
  the device) are frequently triaged, fixed, and discussed in the open on the public issue tracker
  under the [`security-related`](https://github.com/micropython/micropython/issues?q=label%3Asecurity-related)
  label. That label is the project's de-facto public record of security issues to date.
- Readily-exploitable or high-impact issues are handled privately in a GitHub security advisory
  under embargo (below) until a fix is ready.

If you are unsure which category a finding falls into, use the private route and we will decide
during triage. When we judge an issue does not need an embargo, we may move it to the public
tracker so the fix can be reviewed in the open.

### Disclosure and embargo

We practise coordinated disclosure, and ask that you give us a reasonable opportunity to fix an
issue before disclosing it publicly. Our target embargo is at most 90 days from acknowledgement;
we aim to release a fix well inside that window and will agree a public disclosure date with you. If
an issue is being actively exploited, we may bring disclosure forward.

### CVEs

Where an issue warrants one, a CVE will be assigned. MicroPython is not currently a CVE Numbering
Authority, so a CVE is requested through a Root such as MITRE. Most MicroPython CVEs to date have
been assigned by third parties without project involvement; this policy brings assignment under
project coordination.

### Credit

We credit reporters in the published advisory unless you ask us not to. We do not run a paid
bug-bounty programme.

## Supported versions

Security fixes are applied to the latest release and the current development branch. MicroPython
maintains a single evolving release line rather than parallel long-term-support branches; see the
[release and versioning policy](https://docs.micropython.org/en/latest/develop/releaseandversioning.html)
for what this means for keeping a deployed device patched. Track the latest release.
