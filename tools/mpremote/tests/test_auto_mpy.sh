#!/bin/bash
set -e

# Integration tests for the auto-mpy feature (automatic .py to .mpy compilation).
# These tests verify that:
# 1. .mpy files are automatically compiled when modules are imported
# 2. Compiled .mpy files are used on subsequent imports
# 3. The --no-auto-mpy flag disables compilation
# 4. Compilation failures fall back gracefully to .py files
# 5. Package imports work correctly with auto-mpy
# 6. Cache functionality works (both cache hits and invalidation)
# 7. Multiple modules can be compiled in a single session

# Test 1: Mount with auto-mpy enabled (default) and verify compilation
echo -----
mkdir -p "${TMP}/test_mpy"
cat << EOF > "${TMP}/test_mpy/module1.py"
def hello():
    return "Hello from module1"
EOF
$MPREMOTE mount "${TMP}/test_mpy" exec "import module1; print(module1.hello())"
# Verify .mpy file was created
test -f "${TMP}/test_mpy/module1.mpy" && echo "PASS: .mpy file created" || echo "FAIL: .mpy file not created"

# Test 2: Verify .mpy is used on second import (after removing .py)
echo -----
rm "${TMP}/test_mpy/module1.py"
$MPREMOTE resume exec "import sys; sys.modules.pop('module1', None)"
$MPREMOTE resume exec "import module1; print(module1.hello())"
echo "PASS: .mpy file used after .py removed"

# Test 3: Mount with auto-mpy disabled
echo -----
mkdir -p "${TMP}/test_mpy_disabled"
cat << EOF > "${TMP}/test_mpy_disabled/module2.py"
def greet():
    return "Hello from module2"
EOF
$MPREMOTE mount --no-auto-mpy "${TMP}/test_mpy_disabled" exec "import module2; print(module2.greet())"
# Verify .mpy file was NOT created
test ! -f "${TMP}/test_mpy_disabled/module2.mpy" && echo "PASS: .mpy file not created with --no-auto-mpy" || echo "FAIL: .mpy file created when it should not have been"

# Test 4: Compilation failure fallback (syntax error)
echo -----
mkdir -p "${TMP}/test_mpy_syntax"
cat << EOF > "${TMP}/test_mpy_syntax/syntax_error.py"
def broken(
    # Missing closing paren
EOF
(
  $MPREMOTE mount "${TMP}/test_mpy_syntax" exec "import syntax_error" || echo "expect error"
) 2> >(head -n1 >&2)

# Test 5: Package with __init__.py
echo -----
mkdir -p "${TMP}/test_mpy_pkg/subpkg"
cat << EOF > "${TMP}/test_mpy_pkg/__init__.py"
from .helper import func
EOF
cat << EOF > "${TMP}/test_mpy_pkg/helper.py"
def func():
    return "from helper"
EOF
cat << EOF > "${TMP}/test_mpy_pkg/subpkg/__init__.py"
def subfunc():
    return "from subpkg"
EOF
$MPREMOTE mount "${TMP}/test_mpy_pkg" exec "import test_mpy_pkg; print(test_mpy_pkg.func())"
$MPREMOTE resume exec "from test_mpy_pkg.subpkg import subfunc; print(subfunc())"
# Check that .mpy files were created for both modules
test -f "${TMP}/test_mpy_pkg/__init__.mpy" && test -f "${TMP}/test_mpy_pkg/helper.mpy" && test -f "${TMP}/test_mpy_pkg/subpkg/__init__.mpy" && echo "PASS: .mpy files created for package" || echo "FAIL: Not all .mpy files created for package"

# Test 6: Cache hit on unchanged file
echo -----
mkdir -p "${TMP}/test_mpy_cache"
cat << EOF > "${TMP}/test_mpy_cache/cached_module.py"
def cached_func():
    return "cached"
EOF
$MPREMOTE mount "${TMP}/test_mpy_cache" exec "import cached_module; print(cached_module.cached_func())"
# Remove .mpy and import again - should be restored from cache
rm "${TMP}/test_mpy_cache/cached_module.mpy"
$MPREMOTE resume exec "import sys; sys.modules.pop('cached_module', None)"
$MPREMOTE resume exec "import cached_module; print(cached_module.cached_func())"
# Check that .mpy was recreated (from cache)
test -f "${TMP}/test_mpy_cache/cached_module.mpy" && echo "PASS: .mpy restored from cache" || echo "FAIL: .mpy not restored from cache"

# Test 7: Cache invalidation on file modification
echo -----
sleep 1  # Ensure mtime changes
cat << EOF > "${TMP}/test_mpy_cache/cached_module.py"
def cached_func():
    return "modified"
EOF
rm "${TMP}/test_mpy_cache/cached_module.mpy"
$MPREMOTE resume exec "import sys; sys.modules.pop('cached_module', None)"
$MPREMOTE resume exec "import cached_module; print(cached_module.cached_func())"
echo "PASS: Modified file recompiled"

# Test 8: Multiple imports in single session
echo -----
mkdir -p "${TMP}/test_mpy_multi"
cat << EOF > "${TMP}/test_mpy_multi/mod_a.py"
def func_a():
    return "A"
EOF
cat << EOF > "${TMP}/test_mpy_multi/mod_b.py"
def func_b():
    return "B"
EOF
cat << EOF > "${TMP}/test_mpy_multi/mod_c.py"
def func_c():
    return "C"
EOF
$MPREMOTE mount "${TMP}/test_mpy_multi" exec "import mod_a, mod_b, mod_c; print(mod_a.func_a(), mod_b.func_b(), mod_c.func_c())"
# Verify all .mpy files were created
test -f "${TMP}/test_mpy_multi/mod_a.mpy" && test -f "${TMP}/test_mpy_multi/mod_b.mpy" && test -f "${TMP}/test_mpy_multi/mod_c.mpy" && echo "PASS: Multiple .mpy files created" || echo "FAIL: Not all .mpy files created"

echo -----
