# Enable/disable modules and 3rd-party libs to be included in interpreter

# Build 32-bit binaries on a 64-bit host
# MICROPY_FORCE_32BIT = 0 # Now controlled by Kconfig: CONFIG_MICROPY_FORCE_32BIT

# This variable can take the following values:
#  0 - no readline, just simple stdin input
#  1 - use MicroPython version of readline
# MICROPY_USE_READLINE = 1 # Now controlled by Kconfig: CONFIG_MICROPY_USE_READLINE_MPY

# btree module using Berkeley DB 1.xx
MICROPY_PY_BTREE = 1

# _thread module using pthreads
# MICROPY_PY_THREAD = 1 # Now controlled by Kconfig: CONFIG_MICROPY_PY_THREAD

# Subset of CPython termios module
# MICROPY_PY_TERMIOS = 1 # Now controlled by Kconfig: CONFIG_MICROPY_PY_TERMIOS

# Subset of CPython socket module
# MICROPY_PY_SOCKET = 1 # Now controlled by Kconfig: CONFIG_MICROPY_PY_SOCKET

# ffi module requires libffi (libffi-dev Debian package)
# MICROPY_PY_FFI = 1 # Now controlled by Kconfig: CONFIG_MICROPY_PY_FFI

# ssl module requires one of the TLS libraries below
# MICROPY_PY_SSL = 1 # Now controlled by Kconfig: CONFIG_MICROPY_PY_SSL
# axTLS has minimal size but implements only a subset of modern TLS
# functionality, so may have problems with some servers.
# MICROPY_SSL_AXTLS = 0 # Now controlled by Kconfig: CONFIG_MICROPY_SSL_AXTLS
# mbedTLS is more up to date and complete implementation, but also
# more bloated.
# MICROPY_SSL_MBEDTLS = 1 # Now controlled by Kconfig: CONFIG_MICROPY_SSL_MBEDTLS

# jni module requires JVM/JNI
# MICROPY_PY_JNI = 0 # Now controlled by Kconfig: CONFIG_MICROPY_PY_JNI

# Avoid using system libraries, use copies bundled with MicroPython
# as submodules (currently affects only libffi).
# MICROPY_STANDALONE ?= 0 # Now controlled by Kconfig: CONFIG_MICROPY_STANDALONE

MICROPY_ROM_TEXT_COMPRESSION = 1 # Keep this? Or Kconfig?

MICROPY_VFS_FAT = 1 # Keep this? Or Kconfig?
MICROPY_VFS_LFS1 = 1 # Keep this? Or Kconfig?
MICROPY_VFS_LFS2 = 1 # Keep this? Or Kconfig?
