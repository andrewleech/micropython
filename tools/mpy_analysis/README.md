# mpy_analysis -- static analysis and SBOM tooling for MicroPython firmware projects

This package holds the small set of checks no accepted static analysis tool does, for a
firmware project built on MicroPython:

- `sast-compdb` proves a compilation database covers the build it came from, against the
  build's own object files.
- `sast-cppcheck-inputs` derives the predefines, `__has_*` answers, limit macros and platform
  file cppcheck needs from the build's own compiler, per compiler-flag group.
- `sast-cppcheck-coverage` and `sast-codechecker-coverage` fail a run that did not analyse
  every unit of its database, unless the gap is in the project's accepted list.
- `sast-fanalyzer` replays a compilation database under GCC's `-fanalyzer` with an
  analysis-only GCC 15, one SARIF per unit.
- `sast-install-tools` installs the pinned analysers (cppcheck, the Arm GNU Toolchain for
  `-fanalyzer`, CodeChecker and the clang it drives) into a prefix, in the project's own
  build image.

A project installs it from its MicroPython checkout, like mpremote:

    python3 -m pip install --target build/analysis-tools tools/mpy_analysis

and calls the entry points from its build system. The project holds its own analysis policy
(suppressions, accepted coverage gaps) and nothing else; the analysers' own SARIF is what a
CI platform's code scanning receives and gates on.
