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

If a vendor SDK or other dependency issue affects an official MicroPython build, link the upstream
report or CVE in a public security report, or email it if confidential handling is warranted. We
will assess the affected ports and releases, track the required update, and publish any
MicroPython-specific remediation.

## Reporting a vulnerability

Most security issues should be reported publicly using the [security report form](https://github.com/micropython/micropython/issues/new?template=security.yml).
It applies the `security` label and lets the issue and its fix be discussed in the open.

If you believe an issue is readily exploitable, has high impact, or otherwise needs confidential
handling, email [contact@micropython.org](mailto:contact@micropython.org) instead. Do not disclose
the details publicly while it is handled privately. If you are unsure which route is appropriate,
email us.

### What to include

A good report lets us reproduce and assess the issue quickly. Please include:

- The affected version (shown in the startup banner after boot or a soft reset, `Ctrl-D`, in the
  REPL) or commit hash, and whether you are running an official build or a modified one.
- The affected port, board, and any non-default build configuration
  (`mpconfigport.h` / `mpconfigboard.h` options, native modules, `MICROPY_PREVIEW_VERSION_2`).
- The reproducing code pasted directly into the report, not linked to an external repository (which
  may change), and minimised to the smallest snippet that still triggers the fault.
- For sanitizer-based reports, paste the full sanitizer trace, together with the compiler version and
  the build command and options used to produce it.
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

The timelines below are best-effort targets, not contractual guarantees. We will:

1. Acknowledge your report within 7 days.
2. Triage it, confirming the issue, determining affected versions and assessing severity, then share
   our assessment with you.
3. Develop and review a fix in the open for public reports. For reports made by email, keep details
   confidential while a fix is prepared where appropriate.
4. Publish the fix and, where warranted, request a CVE.

### Where issues are handled and recorded

Security reports normally remain on the public issue tracker under the
[`security`](https://github.com/micropython/micropython/issues?q=label%3Asecurity) label. This is
the project's public record of security issues.

Reports received by email are assessed to decide whether confidential handling is warranted. When
it is not, we may ask the reporter to open a public issue so the fix can be reviewed in the open.

### Disclosure and embargo

For reports handled privately, we practise coordinated disclosure and ask that you give us a
reasonable opportunity to fix the issue before disclosing it publicly. Our target embargo is at
most 90 days from acknowledgement; we aim to release a fix well inside that window and will agree a
public disclosure date with you. If an issue is being actively exploited, we may bring disclosure
forward. Public reports are discussed and fixed in the open.

### CVEs

When a security issue is identified through a public report, private report, testing, normal
development, or retrospective review, we assess whether it warrants a CVE. Where appropriate, we
will request a CVE and include it with the published fix when assigned. An upstream CVE normally
remains the identifier for a dependency vulnerability. MicroPython may request a separate CVE when
its integration, configuration, or release handling creates a distinct vulnerability that users need
to track separately.

### Credit

We credit reporters with the published fix or CVE unless you ask us not to. We do not run a paid
bug-bounty programme.

## Supported versions

Security fixes are applied to the latest release and the current development branch. MicroPython
maintains a single evolving release line rather than parallel long-term-support branches; see the
[release and versioning policy](https://docs.micropython.org/en/latest/develop/releaseandversioning.html)
for what this means for keeping a deployed device patched. Track the latest release.
