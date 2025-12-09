Software Bill of Materials (SBOM)
=================================

This document describes MicroPython's approach to Software Bill of Materials
(SBOM) generation and license compliance using SPDX identifiers.

Overview
--------

MicroPython uses the `SPDX <https://spdx.dev/>`_ standard for machine-readable
license identification, aligned with the approach used by the Linux kernel and
Zephyr RTOS. The implementation follows the `REUSE Specification 3.3
<https://reuse.software/spec-3.3/>`_ from the Free Software Foundation Europe.

SPDX License Headers
--------------------

All source files should include SPDX headers near the top of the file.

C/C++ Files
~~~~~~~~~~~

.. code-block:: c

    // SPDX-FileCopyrightText: 2013-2025 Damien P. George
    // SPDX-License-Identifier: MIT

For files with multiple copyright holders:

.. code-block:: c

    // SPDX-FileCopyrightText: 2013-2025 Damien P. George
    // SPDX-FileCopyrightText: 2020 Other Contributor
    // SPDX-License-Identifier: MIT

Python Files
~~~~~~~~~~~~

.. code-block:: python

    # SPDX-FileCopyrightText: 2013-2025 Damien P. George
    # SPDX-License-Identifier: MIT

Shell Scripts
~~~~~~~~~~~~~

After the shebang line:

.. code-block:: bash

    #!/bin/bash
    # SPDX-FileCopyrightText: 2013-2025 Damien P. George
    # SPDX-License-Identifier: MIT

Header Placement
~~~~~~~~~~~~~~~~

- C/C++ files: First line of the file (before include guards)
- Python/Shell: After shebang (if present), otherwise first line
- The SPDX header should appear within the first 20 lines

Third-Party Code
----------------

Code in ``lib/`` Submodules
~~~~~~~~~~~~~~~~~~~~~~~~~~~

Git submodules under ``lib/`` are treated as separate projects per the REUSE
specification. They do not require modification to add SPDX headers.

License information for submodules is documented in the repository ``LICENSE``
file, which contains a tree structure showing the license for each component.

Vendored Third-Party Files
~~~~~~~~~~~~~~~~~~~~~~~~~~

For third-party code copied directly into the repository (not via submodule),
the original license headers should be preserved. If the file lacks SPDX
headers, document the license in the ``LICENSE`` file and optionally add a
``.license`` companion file.

Files Without Comment Support
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

For binary files or files that cannot contain comments, create a companion
``.license`` file with the same base name:

.. code-block:: text

    logo.png
    logo.png.license  # Contains SPDX headers

Build-Time SBOM Generation
--------------------------

MicroPython supports generating SBOMs that reflect the actual code compiled
into a specific firmware build, not just the entire source tree.

Architecture
~~~~~~~~~~~~

The build-time SBOM generator uses two data sources:

1. **Linker map file** (``firmware.map``): Lists all object files and archive
   members linked into the final binary
2. **Source file scanning**: Extracts SPDX headers from the source files
   corresponding to linked objects

This approach ensures the SBOM only contains components actually present in
the firmware, excluding unused libraries and platform-specific code.

Usage
~~~~~

After building firmware::

    make BOARD=<BOARD>
    python tools/mkspdx.py --build-dir ports/<port>/build-<BOARD> \
        --output firmware.spdx

The tool:

1. Parses the linker map file to identify compiled source files
2. Traces object files back to their source files
3. Scans source files for ``SPDX-License-Identifier`` tags
4. Extracts copyright information using the ``reuse`` library
5. Generates an SPDX document listing all components

Output Formats
~~~~~~~~~~~~~~

- ``--format spdx-tv``: SPDX tag-value format (default)
- ``--format spdx-json``: SPDX JSON format
- ``--format cyclonedx``: CycloneDX JSON format

Handling Missing SPDX Headers
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Files without SPDX headers are still included in the SBOM with:

- File hash (SHA256)
- Path information
- Empty license field (marked as ``NOASSERTION``)

For submodule code, the generator falls back to license information from the
``LICENSE`` file's documented license tree.

CI and Pre-commit Integration
-----------------------------

REUSE Compliance Checking
~~~~~~~~~~~~~~~~~~~~~~~~~

The ``reuse`` tool validates that all files have proper SPDX headers.

Pre-commit Hook
~~~~~~~~~~~~~~~

Add to ``.pre-commit-config.yaml``:

.. code-block:: yaml

    repos:
      - repo: https://github.com/fsfe/reuse-tool
        rev: v5.0.2
        hooks:
          - id: reuse

This runs ``reuse lint`` on every commit to catch missing headers.

GitHub Actions
~~~~~~~~~~~~~~

The CI workflow ``.github/workflows/spdx_compliance.yml`` runs on all pull
requests to verify:

1. All new/modified files have SPDX headers
2. License expressions are valid
3. Referenced licenses exist in ``LICENSES/`` directory

Local Validation
~~~~~~~~~~~~~~~~

Run compliance check locally::

    pip install reuse
    reuse lint

Or check specific files::

    reuse lint --include "py/*.c"

LICENSES Directory
------------------

The ``LICENSES/`` directory contains the full text of all licenses used in
the project. Each file is named using the SPDX license identifier:

.. code-block:: text

    LICENSES/
        MIT.txt
        BSD-3-Clause.txt
        Apache-2.0.txt
        ...

Download license texts using::

    reuse download MIT
    reuse download BSD-3-Clause

Tooling Reference
-----------------

reuse
~~~~~

The `reuse tool <https://github.com/fsfe/reuse-tool>`_ provides:

- ``reuse lint``: Check compliance
- ``reuse annotate``: Add headers to files
- ``reuse spdx``: Generate SPDX document
- ``reuse download``: Download license texts

Installation::

    pip install reuse

mkspdx.py
~~~~~~~~~

MicroPython's build-time SBOM generator (``tools/mkspdx.py``) provides:

- Linker map file parsing
- Object-to-source file mapping
- SPDX header extraction
- Integration with the ``reuse`` library for copyright extraction
- Multiple output formats

Migration Guide
---------------

Adding SPDX Headers to Existing Files
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

For bulk addition to existing files::

    # Add headers to all C files in py/
    reuse annotate --license MIT \
        --copyright "Damien P. George" \
        --year 2013-2025 \
        py/*.c py/*.h

    # Review changes before committing
    git diff

For files with existing copyright blocks, manually convert the header format
while preserving the original copyright information.

Gradual Migration
~~~~~~~~~~~~~~~~~

New and modified files should include SPDX headers. The CI check can initially
run in warning-only mode, with enforcement enabled once coverage is sufficient.

References
----------

- `SPDX Specification <https://spdx.github.io/spdx-spec/>`_
- `REUSE Specification 3.3 <https://reuse.software/spec-3.3/>`_
- `SPDX License List <https://spdx.org/licenses/>`_
- `Linux Kernel Licensing Rules <https://www.kernel.org/doc/html/latest/process/license-rules.html>`_
- `Zephyr SBOM Documentation <https://docs.zephyrproject.org/latest/develop/west/zephyr-cmds.html>`_
