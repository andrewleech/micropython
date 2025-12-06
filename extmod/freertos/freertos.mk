# This file is part of the MicroPython project, http://micropython.org/
#
# The MIT License (MIT)
#
# Copyright (c) 2025 MicroPython Contributors
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

# FreeRTOS threading backend for MicroPython
#
# Usage in port Makefile:
#   ifeq ($(MICROPY_PY_THREAD),1)
#   FREERTOS_DIR = $(TOP)/lib/FreeRTOS-Kernel
#   include $(TOP)/extmod/freertos/freertos.mk
#   # Port must also add architecture-specific port.c (e.g., ARM_CM4F/port.c)
#   endif
#
# Required variables:
#   FREERTOS_DIR - Path to FreeRTOS-Kernel root directory
#
# Optional variables:
#   FREERTOS_HEAP_C - Path to heap implementation (default: heap_4.c)
#   FREERTOS_PORT_DIR - Path to architecture-specific port directory
#
# Port responsibilities:
#   - Provide FreeRTOSConfig.h in port include path
#   - Set FREERTOS_PORT_DIR and add port.c + include path:
#       FREERTOS_PORT_DIR = $(FREERTOS_DIR)/portable/GCC/ARM_CM4F
#       SRC_C += $(FREERTOS_PORT_DIR)/port.c
#       CFLAGS_MOD += -I$(FREERTOS_PORT_DIR)
#   - Implement vApplicationGetIdleTaskMemory() callback

ifeq ($(MICROPY_PY_THREAD),1)

# Validate required variable
ifndef FREERTOS_DIR
$(error FREERTOS_DIR must be set to use FreeRTOS threading backend)
endif

# MicroPython FreeRTOS backend sources
SRC_FREERTOS_C += extmod/freertos/mpthreadport.c
SRC_FREERTOS_C += extmod/freertos/mp_freertos_hal.c

# Optional service framework
ifeq ($(MICROPY_FREERTOS_SERVICE_TASKS),1)
SRC_FREERTOS_C += extmod/freertos/mp_freertos_service.c
endif

# FreeRTOS kernel sources
SRC_FREERTOS_C += $(addprefix $(FREERTOS_DIR)/, \
    tasks.c \
    queue.c \
    list.c \
    timers.c \
    event_groups.c \
    stream_buffer.c \
    )

# FreeRTOS memory management (port can override by setting FREERTOS_HEAP_C before include)
# heap_4 recommended for fragmentation handling; set to empty to disable
ifndef FREERTOS_HEAP_C
FREERTOS_HEAP_C = $(FREERTOS_DIR)/portable/MemMang/heap_4.c
endif
ifneq ($(FREERTOS_HEAP_C),)
SRC_FREERTOS_C += $(FREERTOS_HEAP_C)
endif

# Include directories
INC_FREERTOS += -I$(TOP)/extmod/freertos
INC_FREERTOS += -I$(FREERTOS_DIR)/include

# Add to module sources
SRC_MOD += $(SRC_FREERTOS_C)
CFLAGS_MOD += $(INC_FREERTOS)

endif # MICROPY_PY_THREAD
