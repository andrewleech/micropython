"""Static analysis and SBOM tooling for MicroPython firmware projects.

Installed with pip into the analysed project's tools directory rather than carried by the
project. A project installs this package from its MicroPython checkout and calls these entry
points; it holds its own analysis policy and nothing else.
"""

__all__ = ["codechecker_coverage", "compdb", "coverage", "cppcheck_inputs", "fanalyzer", "install"]
