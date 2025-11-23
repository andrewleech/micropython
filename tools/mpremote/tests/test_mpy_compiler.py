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

import sys
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Add mpremote to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from mpremote.mpy_compiler import ARCH_MAP, MpyCrossCompiler


@pytest.fixture
def temp_test_dir(tmp_path):
    """Create a temporary directory for test files."""
    test_dir = tmp_path / "test_files"
    test_dir.mkdir()
    return test_dir


@pytest.fixture
def test_py_file(temp_test_dir):
    """Create a test Python file."""
    py_file = temp_test_dir / "test_module.py"
    py_file.write_text("def hello():\n    return 'Hello World'\n")
    return py_file


@pytest.fixture
def cache_dir(tmp_path):
    """Create a temporary cache directory."""
    cache = tmp_path / "cache"
    cache.mkdir()
    return cache


class TestArchMapping:
    """Tests for architecture mapping."""

    def test_arch_mapping(self):
        """Test ARCH_MAP is correct for all architectures."""
        # Verify all expected architectures are present
        expected_archs = {
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

        assert ARCH_MAP == expected_archs

    def test_arch_mapping_values_are_valid(self):
        """Test that all architecture values are valid mpy-cross march parameters."""
        # All values should be non-empty strings
        for arch_idx, arch_name in ARCH_MAP.items():
            assert isinstance(arch_idx, int)
            assert isinstance(arch_name, str)
            assert len(arch_name) > 0
            assert " " not in arch_name  # No spaces in arch names


class TestMpyCrossAvailability:
    """Tests for mpy-cross module detection."""

    def test_mpy_cross_not_available(self, cache_dir):
        """Test graceful handling when mpy_cross module missing."""
        with patch.dict("sys.modules", {"mpy_cross": None}):
            # Use a side_effect function that only raises for mpy_cross
            original_import = __import__

            def mock_import(name, *args, **kwargs):
                if name == "mpy_cross":
                    raise ImportError("No module named 'mpy_cross'")
                return original_import(name, *args, **kwargs)

            with patch("builtins.__import__", side_effect=mock_import):
                compiler = MpyCrossCompiler(
                    arch="armv7m", mpy_version=6, cache_dir=cache_dir, verbose=False
                )

                assert compiler.enabled is False
                assert compiler.arch == "armv7m"
                assert compiler.mpy_version == 6

    def test_mpy_cross_available(self, cache_dir):
        """Test successful initialization when mpy_cross is available."""
        mock_mpy_cross = MagicMock()
        with patch.dict("sys.modules", {"mpy_cross": mock_mpy_cross}):
            compiler = MpyCrossCompiler(
                arch="armv7m", mpy_version=6, cache_dir=cache_dir, verbose=False
            )

            assert compiler.enabled is True

    def test_cache_dir_creation_failure(self, tmp_path):
        """Test graceful handling when cache directory cannot be created."""
        # Create a file where the cache dir should be (will cause mkdir to fail)
        blocked_path = tmp_path / "blocked"
        blocked_path.write_text("blocking file")

        mock_mpy_cross = MagicMock()
        with patch.dict("sys.modules", {"mpy_cross": mock_mpy_cross}):
            compiler = MpyCrossCompiler(
                arch="armv7m",
                mpy_version=6,
                cache_dir=blocked_path / "cache",
                verbose=False,
            )

            # Should disable itself if cache dir creation fails
            assert compiler.enabled is False


class TestCacheKeyGeneration:
    """Tests for cache key generation logic."""

    def test_cache_key_generation(self, cache_dir, test_py_file):
        """Test cache key format includes path, mtime, arch."""
        mock_mpy_cross = MagicMock()
        with patch.dict("sys.modules", {"mpy_cross": mock_mpy_cross}):
            compiler = MpyCrossCompiler(
                arch="armv7m", mpy_version=6, cache_dir=cache_dir, verbose=False
            )

            # Get cache key by checking what file would be created
            cached = compiler.get_cached_mpy(test_py_file)
            assert cached is None  # No cache yet

            # Now compile to create cache entry
            output_path = test_py_file.with_suffix(".mpy")
            output_path.write_bytes(b"fake mpy content")

            compiler.save_to_cache(test_py_file, output_path)

            # Check that cache file was created with correct naming
            cache_files = list(cache_dir.glob("*.mpy"))
            assert len(cache_files) == 1

            cache_file = cache_files[0]
            cache_name = cache_file.stem

            # Cache key should contain path components, mtime, and arch
            assert "armv7m" in cache_name
            assert "test_module_py" in cache_name
            # mtime is included as a float, so just check it exists
            assert "_" in cache_name  # Multiple components separated by underscores

    def test_cache_key_different_for_different_arch(self, cache_dir, test_py_file):
        """Test that different architectures create different cache keys."""
        mock_mpy_cross = MagicMock()
        with patch.dict("sys.modules", {"mpy_cross": mock_mpy_cross}):
            compiler1 = MpyCrossCompiler(
                arch="armv7m", mpy_version=6, cache_dir=cache_dir, verbose=False
            )
            compiler2 = MpyCrossCompiler(
                arch="xtensa", mpy_version=6, cache_dir=cache_dir, verbose=False
            )

            output_path = test_py_file.with_suffix(".mpy")
            output_path.write_bytes(b"fake mpy content")

            compiler1.save_to_cache(test_py_file, output_path)
            compiler2.save_to_cache(test_py_file, output_path)

            # Should have two different cache files
            cache_files = list(cache_dir.glob("*.mpy"))
            assert len(cache_files) == 2

            cache_names = [f.stem for f in cache_files]
            assert any("armv7m" in name for name in cache_names)
            assert any("xtensa" in name for name in cache_names)


class TestCaching:
    """Tests for cache hit and miss behavior."""

    def test_cache_hit(self, cache_dir, test_py_file):
        """Test cache retrieval works correctly."""
        mock_mpy_cross = MagicMock()
        with patch.dict("sys.modules", {"mpy_cross": mock_mpy_cross}):
            compiler = MpyCrossCompiler(
                arch="armv7m", mpy_version=6, cache_dir=cache_dir, verbose=False
            )

            # Initially no cache
            cached = compiler.get_cached_mpy(test_py_file)
            assert cached is None

            # Create and save to cache
            output_path = test_py_file.with_suffix(".mpy")
            output_path.write_bytes(b"compiled mpy content")
            compiler.save_to_cache(test_py_file, output_path)

            # Now should get cache hit
            cached = compiler.get_cached_mpy(test_py_file)
            assert cached is not None
            assert cached.exists()
            assert cached.read_bytes() == b"compiled mpy content"

    def test_cache_miss_on_mtime_change(self, cache_dir, test_py_file):
        """Test cache invalidation when file changes."""
        mock_mpy_cross = MagicMock()
        with patch.dict("sys.modules", {"mpy_cross": mock_mpy_cross}):
            compiler = MpyCrossCompiler(
                arch="armv7m", mpy_version=6, cache_dir=cache_dir, verbose=False
            )

            # Create and save to cache
            output_path = test_py_file.with_suffix(".mpy")
            output_path.write_bytes(b"original mpy")
            compiler.save_to_cache(test_py_file, output_path)

            # Verify cache hit
            cached = compiler.get_cached_mpy(test_py_file)
            assert cached is not None

            # Modify the source file (change mtime)
            time.sleep(0.01)  # Ensure mtime changes
            test_py_file.write_text("def hello():\n    return 'Modified'\n")

            # Cache should miss now
            cached_after_change = compiler.get_cached_mpy(test_py_file)
            assert cached_after_change is None

    def test_cache_disabled_when_compiler_disabled(self, cache_dir, test_py_file):
        """Test that cache operations are skipped when compiler is disabled."""
        # Create output path before patching imports
        output_path = test_py_file.with_suffix(".mpy")
        output_path.write_bytes(b"fake mpy")

        with patch.dict("sys.modules", {"mpy_cross": None}):
            # Use a side_effect function that only raises for mpy_cross
            original_import = __import__

            def mock_import(name, *args, **kwargs):
                if name == "mpy_cross":
                    raise ImportError("No module named 'mpy_cross'")
                return original_import(name, *args, **kwargs)

            with patch("builtins.__import__", side_effect=mock_import):
                compiler = MpyCrossCompiler(
                    arch="armv7m", mpy_version=6, cache_dir=cache_dir, verbose=False
                )

                assert compiler.enabled is False

                # get_cached_mpy should return None
                cached = compiler.get_cached_mpy(test_py_file)
                assert cached is None

                # save_to_cache should be no-op
                compiler.save_to_cache(test_py_file, output_path)

                # No cache files should be created
                cache_files = list(cache_dir.glob("*.mpy"))
                assert len(cache_files) == 0


class TestCompilation:
    """Tests for compilation functionality."""

    def test_compilation_success(self, cache_dir, test_py_file):
        """Test successful compilation (mock mpy_cross.run)."""
        mock_mpy_cross = MagicMock()
        mock_proc = MagicMock()
        mock_proc.wait = MagicMock(return_value=0)  # Success
        mock_mpy_cross.run = MagicMock(return_value=mock_proc)

        with patch.dict("sys.modules", {"mpy_cross": mock_mpy_cross}):
            compiler = MpyCrossCompiler(
                arch="armv7m", mpy_version=6, cache_dir=cache_dir, verbose=False
            )

            output_path = test_py_file.with_suffix(".mpy")

            result = compiler.compile(test_py_file, output_path)

            assert result is True
            mock_mpy_cross.run.assert_called_once()

            # Verify arguments passed to mpy_cross.run
            call_args = mock_mpy_cross.run.call_args[0]
            assert "-march=armv7m" in call_args
            assert "-o" in call_args
            assert str(output_path) in call_args
            assert str(test_py_file) in call_args

    def test_compilation_failure(self, cache_dir, test_py_file):
        """Test error handling when compilation fails."""
        mock_mpy_cross = MagicMock()
        mock_proc = MagicMock()
        mock_proc.wait = MagicMock(return_value=1)  # Failure
        mock_mpy_cross.run = MagicMock(return_value=mock_proc)

        with patch.dict("sys.modules", {"mpy_cross": mock_mpy_cross}):
            compiler = MpyCrossCompiler(
                arch="armv7m", mpy_version=6, cache_dir=cache_dir, verbose=False
            )

            output_path = test_py_file.with_suffix(".mpy")

            result = compiler.compile(test_py_file, output_path)

            assert result is False
            mock_mpy_cross.run.assert_called_once()

    def test_compilation_exception(self, cache_dir, test_py_file):
        """Test error handling when compilation raises exception."""
        mock_mpy_cross = MagicMock()
        mock_mpy_cross.run = MagicMock(side_effect=RuntimeError("mpy-cross crashed"))

        with patch.dict("sys.modules", {"mpy_cross": mock_mpy_cross}):
            compiler = MpyCrossCompiler(
                arch="armv7m", mpy_version=6, cache_dir=cache_dir, verbose=False
            )

            output_path = test_py_file.with_suffix(".mpy")

            result = compiler.compile(test_py_file, output_path)

            assert result is False
            mock_mpy_cross.run.assert_called_once()

    def test_compilation_disabled_when_mpy_cross_unavailable(self, cache_dir, test_py_file):
        """Test that compilation is skipped when mpy_cross is not available."""
        # Create output path before patching imports
        output_path = test_py_file.with_suffix(".mpy")

        with patch.dict("sys.modules", {"mpy_cross": None}):
            # Use a side_effect function that only raises for mpy_cross
            original_import = __import__

            def mock_import(name, *args, **kwargs):
                if name == "mpy_cross":
                    raise ImportError("No module named 'mpy_cross'")
                return original_import(name, *args, **kwargs)

            with patch("builtins.__import__", side_effect=mock_import):
                compiler = MpyCrossCompiler(
                    arch="armv7m", mpy_version=6, cache_dir=cache_dir, verbose=False
                )

                result = compiler.compile(test_py_file, output_path)

                assert result is False


class TestVerboseMode:
    """Tests for verbose logging."""

    def test_verbose_logging_on_compilation(self, cache_dir, test_py_file, capsys):
        """Test that verbose mode logs compilation messages."""
        mock_mpy_cross = MagicMock()
        mock_proc = MagicMock()
        mock_proc.wait = MagicMock(return_value=0)
        mock_mpy_cross.run = MagicMock(return_value=mock_proc)

        with patch.dict("sys.modules", {"mpy_cross": mock_mpy_cross}):
            compiler = MpyCrossCompiler(
                arch="armv7m", mpy_version=6, cache_dir=cache_dir, verbose=True
            )

            output_path = test_py_file.with_suffix(".mpy")
            compiler.compile(test_py_file, output_path)

            captured = capsys.readouterr()
            assert "Compiling" in captured.out
            assert "test_module.py" in captured.out
            assert "armv7m" in captured.out

    def test_verbose_logging_on_mpy_cross_unavailable(self, cache_dir, capsys):
        """Test that verbose mode logs when mpy_cross is unavailable."""
        with patch.dict("sys.modules", {"mpy_cross": None}):
            # Use a side_effect function that only raises for mpy_cross
            original_import = __import__

            def mock_import(name, *args, **kwargs):
                if name == "mpy_cross":
                    raise ImportError("No module named 'mpy_cross'")
                return original_import(name, *args, **kwargs)

            with patch("builtins.__import__", side_effect=mock_import):
                MpyCrossCompiler(arch="armv7m", mpy_version=6, cache_dir=cache_dir, verbose=True)

                captured = capsys.readouterr()
                assert "Auto-MPY disabled" in captured.out
                assert "mpy_cross module not available" in captured.out

    def test_verbose_logging_on_cache_hit(self, cache_dir, test_py_file, capsys):
        """Test that verbose mode logs cache hits."""
        mock_mpy_cross = MagicMock()
        with patch.dict("sys.modules", {"mpy_cross": mock_mpy_cross}):
            compiler = MpyCrossCompiler(
                arch="armv7m", mpy_version=6, cache_dir=cache_dir, verbose=True
            )

            # Create cache entry
            output_path = test_py_file.with_suffix(".mpy")
            output_path.write_bytes(b"fake mpy")
            compiler.save_to_cache(test_py_file, output_path)

            # Clear captured output
            capsys.readouterr()

            # Now get from cache
            compiler.get_cached_mpy(test_py_file)

            captured = capsys.readouterr()
            assert "Using cached .mpy" in captured.out
            assert "test_module.py" in captured.out


class TestEdgeCases:
    """Tests for edge cases and error conditions."""

    def test_nonexistent_file(self, cache_dir):
        """Test handling of non-existent source file."""
        mock_mpy_cross = MagicMock()
        with patch.dict("sys.modules", {"mpy_cross": mock_mpy_cross}):
            compiler = MpyCrossCompiler(
                arch="armv7m", mpy_version=6, cache_dir=cache_dir, verbose=False
            )

            nonexistent = Path("/nonexistent/file.py")

            # get_cached_mpy should handle gracefully
            cached = compiler.get_cached_mpy(nonexistent)
            assert cached is None

    def test_verbose_cache_dir_creation_failure(self, tmp_path, capsys):
        """Test verbose logging when cache directory creation fails."""
        # Create a file where the cache dir should be (will cause mkdir to fail)
        blocked_path = tmp_path / "blocked"
        blocked_path.write_text("blocking file")

        mock_mpy_cross = MagicMock()
        with patch.dict("sys.modules", {"mpy_cross": mock_mpy_cross}):
            compiler = MpyCrossCompiler(
                arch="armv7m",
                mpy_version=6,
                cache_dir=blocked_path / "cache",
                verbose=True,
            )

            captured = capsys.readouterr()
            assert "Warning: Could not create cache directory" in captured.out
            assert compiler.enabled is False

    def test_verbose_compilation_exception(self, cache_dir, test_py_file, capsys):
        """Test verbose logging when compilation raises exception."""
        mock_mpy_cross = MagicMock()
        mock_mpy_cross.run = MagicMock(side_effect=RuntimeError("Test error"))

        with patch.dict("sys.modules", {"mpy_cross": mock_mpy_cross}):
            compiler = MpyCrossCompiler(
                arch="armv7m", mpy_version=6, cache_dir=cache_dir, verbose=True
            )

            output_path = test_py_file.with_suffix(".mpy")
            result = compiler.compile(test_py_file, output_path)

            captured = capsys.readouterr()
            assert "Compilation failed for" in captured.out
            assert "test_module.py" in captured.out
            assert result is False

    def test_cache_save_failure(self, cache_dir, test_py_file, capsys):
        """Test verbose logging when cache save fails."""
        mock_mpy_cross = MagicMock()
        with patch.dict("sys.modules", {"mpy_cross": mock_mpy_cross}):
            compiler = MpyCrossCompiler(
                arch="armv7m", mpy_version=6, cache_dir=cache_dir, verbose=True
            )

            output_path = test_py_file.with_suffix(".mpy")
            output_path.write_bytes(b"fake mpy")

            # Make cache directory read-only to cause failure
            cache_dir.chmod(0o444)

            try:
                compiler.save_to_cache(test_py_file, output_path)

                captured = capsys.readouterr()
                assert "Failed to cache" in captured.out
                assert "test_module.py" in captured.out
            finally:
                # Restore permissions for cleanup
                cache_dir.chmod(0o755)

    def test_special_characters_in_path(self, cache_dir, tmp_path):
        """Test handling of special characters in file paths."""
        mock_mpy_cross = MagicMock()
        mock_proc = MagicMock()
        mock_proc.wait = MagicMock(return_value=0)
        mock_mpy_cross.run = MagicMock(return_value=mock_proc)

        with patch.dict("sys.modules", {"mpy_cross": mock_mpy_cross}):
            compiler = MpyCrossCompiler(
                arch="armv7m", mpy_version=6, cache_dir=cache_dir, verbose=False
            )

            # Create file with spaces and special chars
            special_dir = tmp_path / "test dir" / "sub-dir"
            special_dir.mkdir(parents=True)
            special_file = special_dir / "file with spaces.py"
            special_file.write_text("def test(): pass\n")

            output_path = special_file.with_suffix(".mpy")

            result = compiler.compile(special_file, output_path)

            # Should handle gracefully
            assert result is True
            mock_mpy_cross.run.assert_called_once()

    def test_multiple_arch_caching(self, cache_dir, test_py_file):
        """Test that multiple architectures can coexist in cache."""
        mock_mpy_cross = MagicMock()
        with patch.dict("sys.modules", {"mpy_cross": mock_mpy_cross}):
            compilers = [
                MpyCrossCompiler(arch="armv7m", mpy_version=6, cache_dir=cache_dir),
                MpyCrossCompiler(arch="xtensa", mpy_version=6, cache_dir=cache_dir),
                MpyCrossCompiler(arch="armv6m", mpy_version=6, cache_dir=cache_dir),
            ]

            output_path = test_py_file.with_suffix(".mpy")
            output_path.write_bytes(b"fake mpy")

            # Save to cache with all compilers
            for compiler in compilers:
                compiler.save_to_cache(test_py_file, output_path)

            # Should have 3 different cache files
            cache_files = list(cache_dir.glob("*.mpy"))
            assert len(cache_files) == 3

            # Each compiler should find its own cache
            for compiler in compilers:
                cached = compiler.get_cached_mpy(test_py_file)
                assert cached is not None
                assert compiler.arch in cached.name
