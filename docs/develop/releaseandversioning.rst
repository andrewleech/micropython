.. _releaseandversioning:

Release and versioning policy
=============================

This chapter states what a MicroPython version number means, how breaking changes are managed, and
what to expect for release cadence and support. MicroPython has maintained API compatibility across
its ``1.x`` line for close to a decade and stages breaking changes behind a compile-time gate; this
page writes that practice down so users can rely on it.

Versioning
----------

MicroPython releases are numbered ``MAJOR.MINOR.MICRO`` (for example, ``1.30.0``). Development
builds carry a ``-preview`` suffix.

* **The** ``1.x`` **line is a compatibility line.** MicroPython has stayed on major version ``1``
  for its entire history. Within ``1.x``, the project aims to avoid breaking the public
  Python-level API that existing programs depend on. This is deliberate, not a stalled version
  number.
* **Breaking changes are deferred to the next major version (2.0)** and are not merged loosely into
  the release line. See below.
* MicroPython also aims to keep as much compatibility as practical with CPython at the language
  level, which is a separate goal from release-to-release API stability.

This is a demonstrated-practice commitment, not a strict per-symbol semantic-versioning contract.
The project does not guarantee that every internal or C-level symbol is stable across minor
releases; the compatibility aim applies to the documented public API used by application code.

Breaking changes and the 2.0 preview
-------------------------------------

Breaking API changes slated for the next major release are collected behind a single compile-time
option, ``MICROPY_PREVIEW_VERSION_2``, which defaults to off. A build sees either the current
stable ``1.x`` API or, by enabling the flag, the in-progress 2.0 API; the ``1.x`` behaviour is kept
as a live parallel code path until 2.0 is released.

Concretely:

* Deprecated identifiers remain compiled in on the default ``1.x`` path and are removed only under
  the flag. Examples include legacy ``machine.Pin`` methods, legacy network constants, and the
  CPython-compatible code-object attributes.
* The planned ``os`` to ``vfs`` function move (documented in the `2.0 migration guide
  <https://docs.micropython.org/en/latest/reference/micropython2_migration.html>`_) is staged
  behind the same gate.
* The preview state is queryable at runtime via ``sys.implementation._v2``.

Development of 2.0 is tracked openly through GitHub milestones and the roadmap discussion issues.
Breaking changes destined for 2.0 are recorded in the migration guide as they are introduced, so
users can prepare before 2.0 ships. When 2.0 approaches release, preview builds will be published
alongside ``1.x`` builds.

What this means for you
~~~~~~~~~~~~~~~~~~~~~~~~

* **On** ``1.x``\ **:** upgrading to a newer ``1.x`` release should not require rewriting working
  application code, barring the rare documented exception. Report a regression if one breaks you.
* **Preparing for 2.0:** consult the migration guide, and optionally build with
  ``MICROPY_PREVIEW_VERSION_2`` enabled to test your code against the upcoming API before it lands.

Release cadence
---------------

MicroPython releases are feature-driven rather than tied to a fixed published calendar: a release
is made when a body of work is ready, not on a set date. Each release is accompanied by release
notes and a changelog
(`micropython-ChangeLog.txt <https://micropython.org/resources/micropython-ChangeLog.txt>`_)
summarising new features, fixes, and any behaviour changes.

Support and keeping a device patched
-------------------------------------

MicroPython maintains a single evolving release line, not parallel long-term-support branches.
Security and bug fixes land on the current development branch and ship in the next release. Fixes
are not backported onto older ``1.x`` releases.

For a deployed product this means:

* Patching a device means upgrading to a newer release, not applying a backported fix to a pinned
  older version.
* Products with long field lifetimes should plan for periodic firmware updates against current
  releases, and should track the `changelog
  <https://micropython.org/resources/micropython-ChangeLog.txt>`_ and `security advisories
  <https://github.com/micropython/micropython/security/advisories>`_.
* If your regulatory environment requires a supported, fix-backported baseline for a fixed number
  of years, that obligation rests with you as the device manufacturer, because the project does not
  maintain an LTS branch.

Ports and hardware support
--------------------------

Port support levels are documented separately in :ref:`support_tiers` (Tiers 1, 2, 3 and M). That
page is the authority for what "supported" means for a given board, including which tiers carry a
stable Python API and which warrant a patch release for regressions. This policy does not restate
it.
