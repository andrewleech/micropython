.. _develop-kconfig:

Kconfig Integration
===================

**Important Note:** This Kconfig system is intended as an *optional overlay* to the existing `mpconfig*.h` and build variable system. It allows users to easily customize builds locally using `make menuconfig`. The primary configuration mechanism for MicroPython remains the existing system. The goal is not (currently) to fully replace the existing system, but to provide a user-friendly alternative for common overrides.

MicroPython uses the Kconfig system, widely used by projects like the Linux kernel and Zephyr, to manage build-time configuration options.

Goals
-----

*   **User-Friendly Customization:** Provide a standard, interactive interface (e.g., ``make menuconfig``) for users to *optionally* override default MicroPython features and port-specific settings for their local builds.
*   **Discoverability:** Make common configuration options easily discoverable with help text integrated into the configuration interface.
*   **Structured Options:** Organize Kconfig options in a hierarchy mirroring the project structure.
*   **Integration:** Ensure Kconfig selections (`CONFIG_...`) correctly override the base configuration derived from `mpconfigport.h`, `mpconfigboard.h`, and Make/CMake variables where Kconfig options exist.

Prerequisites
-------------

To use the Kconfig menu interface (``make menuconfig``), you need to install the Kconfig frontend tools. The installation command depends on your operating system:

*   **Debian/Ubuntu:**

    .. code-block:: bash

        sudo apt-get install kconfig-frontends

*   **Fedora:**

    .. code-block:: bash

        sudo dnf install kconfig-frontends

*   **Arch Linux:** (Requires an AUR helper like ``yay`` or ``paru``)

    .. code-block:: bash

        # Using yay
        yay -S kconfig-frontends

        # Or using paru
        # paru -S kconfig-frontends

*   **macOS (using Homebrew):**

    .. code-block:: bash

        brew install kconfig-frontends

Alternatively, tools like Zephyr's ``kconfiglib`` might also work, potentially requiring adjustments to the ``KCONFIG_PREFIX`` variable in the top-level Makefile.

Basic Usage
-----------

1.  **Navigate to the root** of the MicroPython repository.
2.  **Run the configuration interface:**

    .. code-block:: bash

        make menuconfig

3.  **Navigate the menus:** Use arrow keys, Enter to enter submenus, Space to toggle boolean options or cycle choices.
4.  **Get Help:** Select an option and press ``?`` or ``h`` to view its help text.
5.  **Save Configuration:** Exit the interface and choose 'Yes' to save the configuration to the ``.config`` file in the repository root.
6.  **Build MicroPython:** The build system (Make or CMake) will automatically detect the ``.config`` file and generate necessary header files (``build/mpconfig_kconfig.h``) and build variable includes (``build/autoconf.mk``, ``build/config.cmake``) to apply the selected configuration during the build process.

   .. code-block:: bash

       make PORT=<your_port> [BOARD=<your_board>]

Structure
---------

Kconfig files are organized hierarchically:

*   ``Kconfig`` (root): Top-level entry point, sources other files.
*   ``py/Kconfig``: Core MicroPython runtime options.
*   ``extmod/Kconfig``: Options for external modules.
*   ``drivers/Kconfig``: Options for shared drivers.
*   ``ports/<port>/Kconfig``: Port-specific options.
*   ``ports/<port>/boards/<board>/Kconfig``: Board-specific options (can override port/core settings).

.. note::

   This Kconfig integration is currently under development. Not all configuration options have been migrated yet. The old system of using ``mpconfigport.h`` and Make/CMake variables still exists alongside Kconfig for many options.
