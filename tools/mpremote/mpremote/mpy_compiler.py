#!/usr/bin/env python3
#
# This file is part of the MicroPython project, http://micropython.org/
#
# The MIT License (MIT)
#
# Copyright (c) 2024 Andrew Leech
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

import os
import shutil
from pathlib import Path
from typing import Optional


# Architecture mapping from device arch_idx to mpy-cross -march parameter
ARCH_MAP = {
    1: "x86",
    2: "x64",
    3: "armv6",
    4: "armv6m",
    5: "armv7m",
    6: "armv7em",
    7: "armv7emsp",
    8: "armv7emdp",
    9: "xtensa",
    10: "xtensawin",
    11: "hazard3riscv",
}

# Default resource limits
DEFAULT_CACHE_MAX_SIZE_MB = 100
DEFAULT_MAX_FILE_SIZE_KB = 1024


class MpyCrossCompiler:
    """Wrapper for mpy-cross compilation with caching.

    Provides automatic .py to .mpy compilation using the mpy_cross Python
    module, with file-based caching to avoid recompilation.
    """

    def __init__(
        self,
        arch: str,
        mpy_version: int,
        cache_dir: Optional[Path] = None,
        verbose: bool = False,
        cache_max_size_mb: Optional[int] = None,
        max_file_size_kb: Optional[int] = None,
    ):
        """Initialize the compiler.

        Args:
            arch: Target architecture (e.g., "armv7m", "xtensa")
            mpy_version: MPY version from device (for --compat flag)
            cache_dir: Directory for cached .mpy files (default: /tmp/mpremote_mpy_cache)
            verbose: Enable verbose logging
            cache_max_size_mb: Maximum cache size in MB (default: 100)
            max_file_size_kb: Maximum file size to compile in KB (default: 1024)
        """
        self.arch = arch
        self.mpy_version = mpy_version
        self.cache_dir = cache_dir or Path("/tmp/mpremote_mpy_cache")
        self.verbose = verbose
        self.max_cache_size = (
            cache_max_size_mb * 1024 * 1024
            if cache_max_size_mb
            else DEFAULT_CACHE_MAX_SIZE_MB * 1024 * 1024
        )
        self.max_file_size = (
            max_file_size_kb * 1024 if max_file_size_kb else DEFAULT_MAX_FILE_SIZE_KB * 1024
        )
        self.enabled = self._check_mpy_cross_available()

        # Create cache directory if it doesn't exist
        if self.enabled:
            try:
                self.cache_dir.mkdir(parents=True, exist_ok=True)
            except OSError:
                # If cache directory creation fails, disable caching
                if self.verbose:
                    print(f"Warning: Could not create cache directory {self.cache_dir}")
                self.enabled = False

    def _check_mpy_cross_available(self) -> bool:
        """Check if mpy-cross Python package is available.

        Returns:
            True if mpy_cross module can be imported, False otherwise
        """
        try:
            import mpy_cross

            return True
        except ImportError:
            if self.verbose:
                print("Auto-MPY disabled: mpy_cross module not available")
            return False

    def _generate_cache_key(self, py_path: Path) -> str:
        """Generate cache key from file path, mtime, and architecture.

        Args:
            py_path: Path to the Python source file

        Returns:
            Cache key string suitable for use as filename
        """
        mtime = py_path.stat().st_mtime
        cache_key = f"{py_path}_{mtime}_{self.arch}".replace("/", "_").replace(".", "_")
        return cache_key

    def compile(self, py_path: Path, output_path: Path) -> bool:
        """Compile .py to .mpy with error handling.

        Args:
            py_path: Path to source .py file
            output_path: Path where compiled .mpy should be written

        Returns:
            True if compilation succeeded, False otherwise
        """
        if not self.enabled:
            return False

        # Check file size before compiling
        try:
            file_size = py_path.stat().st_size
            if file_size > self.max_file_size:
                if self.verbose:
                    print(f"File too large to compile: {py_path} ({file_size} bytes)")
                return False
        except OSError as e:
            if self.verbose:
                print(f"Failed to stat file {py_path}: {e}")
            return False

        try:
            import mpy_cross

            # Build command line arguments for mpy-cross
            args = [
                f"-march={self.arch}",
                "-o",
                str(output_path),
                str(py_path),
            ]

            if self.verbose:
                print(f"Compiling {py_path.name} for {self.arch}...")

            # Run mpy-cross compilation
            proc = mpy_cross.run(*args)
            returncode = proc.wait()
            return returncode == 0

        except Exception as e:
            # Log error but don't crash - allow fallback to .py
            if self.verbose:
                print(f"Compilation failed for {py_path}: {e}")
            return False

    def get_cached_mpy(self, py_path: Path) -> Optional[Path]:
        """Check if cached .mpy exists and is up-to-date.

        Cache key is based on: (source path, mtime, architecture)

        Args:
            py_path: Path to source .py file

        Returns:
            Path to cached .mpy file if valid, None otherwise
        """
        if not self.enabled:
            return None

        try:
            cache_key = self._generate_cache_key(py_path)
            cached_file = self.cache_dir / f"{cache_key}.mpy"

            if cached_file.exists():
                if self.verbose:
                    print(f"Using cached .mpy for {py_path.name}")
                return cached_file

        except OSError:
            # If stat fails or cache check fails, just return None
            pass

        return None

    def _manage_cache_size(self):
        """Remove oldest cache files if cache exceeds size limit."""
        if not self.cache_dir.exists():
            return

        # Get all .mpy files in cache with their sizes and mtimes
        cache_files = [
            (f, f.stat().st_size, f.stat().st_mtime) for f in self.cache_dir.glob("*.mpy")
        ]

        total_size = sum(size for _, size, _ in cache_files)

        if total_size > self.max_cache_size:
            # Sort by mtime (oldest first)
            cache_files.sort(key=lambda x: x[2])

            # Remove oldest files until under limit
            for file_path, size, _ in cache_files:
                if total_size <= self.max_cache_size:
                    break
                try:
                    file_path.unlink()
                    total_size -= size
                    if self.verbose:
                        print(f"Removed from cache: {file_path.name}")
                except OSError:
                    pass

    def save_to_cache(self, py_path: Path, mpy_path: Path) -> None:
        """Save compiled .mpy to cache.

        Args:
            py_path: Path to source .py file (for cache key generation)
            mpy_path: Path to compiled .mpy file to cache
        """
        if not self.enabled:
            return

        try:
            cache_key = self._generate_cache_key(py_path)
            cache_path = self.cache_dir / f"{cache_key}.mpy"

            # Ensure cache directory exists
            cache_path.parent.mkdir(parents=True, exist_ok=True)

            # Copy to cache
            shutil.copy2(mpy_path, cache_path)

            if self.verbose:
                print(f"Cached .mpy for {py_path.name}")

            # Manage cache size after saving
            self._manage_cache_size()

        except (OSError, IOError) as e:
            # Cache save failure is non-fatal
            if self.verbose:
                print(f"Failed to cache {py_path.name}: {e}")
