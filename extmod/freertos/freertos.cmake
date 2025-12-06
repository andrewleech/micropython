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
# Usage in port CMakeLists.txt:
#   if(MICROPY_PY_THREAD)
#       set(FREERTOS_DIR ${MICROPY_DIR}/lib/FreeRTOS-Kernel)
#       include(${MICROPY_DIR}/extmod/freertos/freertos.cmake)
#       target_link_libraries(${MICROPY_TARGET} micropython_freertos)
#       # Port must also add architecture-specific port sources
#   endif()
#
# Required variables:
#   FREERTOS_DIR - Path to FreeRTOS-Kernel root directory
#
# Optional variables:
#   FREERTOS_HEAP_SRC - Path to heap implementation (default: heap_4.c, set empty to disable)
#
# Port responsibilities:
#   - Provide FreeRTOSConfig.h in port include path
#   - Add architecture-specific portable sources and includes:
#       target_sources(${MICROPY_TARGET} PRIVATE ${FREERTOS_DIR}/portable/GCC/ARM_CM4F/port.c)
#       target_include_directories(${MICROPY_TARGET} PRIVATE ${FREERTOS_DIR}/portable/GCC/ARM_CM4F)
#   - Implement vApplicationGetIdleTaskMemory() callback

if(MICROPY_PY_THREAD)

if(NOT DEFINED FREERTOS_DIR)
    message(FATAL_ERROR "FREERTOS_DIR must be set to use FreeRTOS threading backend")
endif()

add_library(micropython_freertos INTERFACE)

# MicroPython FreeRTOS backend sources
target_sources(micropython_freertos INTERFACE
    ${CMAKE_CURRENT_LIST_DIR}/mpthreadport.c
    ${CMAKE_CURRENT_LIST_DIR}/mp_freertos_hal.c
)

# Optional service framework
if(MICROPY_FREERTOS_SERVICE_TASKS)
    target_sources(micropython_freertos INTERFACE
        ${CMAKE_CURRENT_LIST_DIR}/mp_freertos_service.c
    )
endif()

# FreeRTOS kernel sources
target_sources(micropython_freertos INTERFACE
    ${FREERTOS_DIR}/tasks.c
    ${FREERTOS_DIR}/queue.c
    ${FREERTOS_DIR}/list.c
    ${FREERTOS_DIR}/timers.c
    ${FREERTOS_DIR}/event_groups.c
    ${FREERTOS_DIR}/stream_buffer.c
)

# FreeRTOS memory management (port can override by setting FREERTOS_HEAP_SRC before include)
# heap_4 recommended for fragmentation handling; set to empty string to disable
if(NOT DEFINED FREERTOS_HEAP_SRC)
    set(FREERTOS_HEAP_SRC ${FREERTOS_DIR}/portable/MemMang/heap_4.c)
endif()
if(FREERTOS_HEAP_SRC)
    target_sources(micropython_freertos INTERFACE ${FREERTOS_HEAP_SRC})
endif()

# Include directories
target_include_directories(micropython_freertos INTERFACE
    ${CMAKE_CURRENT_LIST_DIR}
    ${FREERTOS_DIR}/include
)

endif() # MICROPY_PY_THREAD
