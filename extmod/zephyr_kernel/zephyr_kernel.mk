# Zephyr RTOS Kernel Integration for MicroPython
# This file provides centralized Zephyr kernel integration that can be
# included by any MicroPython port wanting to use Zephyr threading.

# Architecture must be defined by the including port
ifndef ZEPHYR_ARCH
$(error ZEPHYR_ARCH must be defined by port before including zephyr_kernel.mk - e.g., posix, arm, xtensa, riscv)
endif

# Zephyr paths
ZEPHYR_BASE := $(TOP)/lib/zephyr
ZEPHYR_KERNEL := $(TOP)/extmod/zephyr_kernel
ZEPHYR_GEN := $(ZEPHYR_KERNEL)/generated/zephyr

# Zephyr include paths (order matters - generated/ first for overrides)
ZEPHYR_INC := \
	-I$(ZEPHYR_KERNEL)/generated \
	-I$(ZEPHYR_BASE)/include \
	-I$(ZEPHYR_BASE)/include/zephyr \
	-I$(ZEPHYR_BASE)/kernel/include \
	-I$(ZEPHYR_KERNEL)

# Note: POSIX arch headers excluded - causes struct member conflicts
# Architecture-specific includes would go here for real embedded targets
# -I$(ZEPHYR_BASE)/arch/$(ZEPHYR_ARCH)/include

# Zephyr CFLAGS
# Note: -Wno-error allows warnings without stopping the build (POC)
# -Wno-macro-redefined suppresses ISR_FLAG_DIRECT redefinition warnings
ZEPHYR_CFLAGS := -include $(ZEPHYR_KERNEL)/zephyr_config.h -Wno-error -Wno-macro-redefined

# Generated header files
ZEPHYR_GEN_HEADERS := \
	$(ZEPHYR_GEN)/version.h \
	$(ZEPHYR_GEN)/syscalls/log_msg.h

# POSIX architecture uses static offsets.h stub (no real offsets needed)
# Other architectures would generate offsets.h from offsets.c
ifneq ($(ZEPHYR_ARCH),posix)
ZEPHYR_GEN_HEADERS += $(ZEPHYR_GEN)/offsets.h
endif

# Offsets source file (architecture-specific)
ZEPHYR_OFFSETS_C := $(ZEPHYR_BASE)/arch/$(ZEPHYR_ARCH)/core/offsets/offsets.c

# Check if offsets.c exists for this architecture
ifeq ($(wildcard $(ZEPHYR_OFFSETS_C)),)
$(error Zephyr offsets file not found for architecture $(ZEPHYR_ARCH): $(ZEPHYR_OFFSETS_C))
endif

# Rule to compile offsets.c to object file
$(BUILD)/zephyr_offsets.o: $(ZEPHYR_OFFSETS_C) $(ZEPHYR_GEN)/version.h
	@echo "CC (Zephyr offsets) $<"
	$(Q)$(CC) $(CFLAGS) $(ZEPHYR_INC) $(ZEPHYR_CFLAGS) -c -o $@ $<

# Rule to generate offsets.h from offsets.o using Zephyr's official script
$(ZEPHYR_GEN)/offsets.h: $(BUILD)/zephyr_offsets.o
	@echo "GEN (Zephyr) $@"
	$(Q)python3 $(ZEPHYR_BASE)/scripts/build/gen_offset_header.py \
		-i $< \
		-o $@

# Rule to generate version.h from Zephyr VERSION file
$(ZEPHYR_GEN)/version.h: $(ZEPHYR_BASE)/VERSION $(ZEPHYR_KERNEL)/gen_zephyr_version.py
	@echo "GEN (Zephyr) $@"
	$(Q)python3 $(ZEPHYR_KERNEL)/gen_zephyr_version.py \
		-i $< \
		-o $@

# Rule to generate empty log_msg.h stub (logging disabled)
$(ZEPHYR_GEN)/syscalls/log_msg.h:
	@echo "GEN (Zephyr) $@"
	$(Q)mkdir -p $(dir $@)
	$(Q)echo '/* Auto-generated log_msg syscalls stub - logging disabled (CONFIG_LOG=0) */' > $@
	$(Q)echo '#ifndef ZEPHYR_SYSCALLS_LOG_MSG_H' >> $@
	$(Q)echo '#define ZEPHYR_SYSCALLS_LOG_MSG_H' >> $@
	$(Q)echo '/* Empty - not needed with CONFIG_LOG=0 */' >> $@
	$(Q)echo '#endif /* ZEPHYR_SYSCALLS_LOG_MSG_H */' >> $@

# Minimal Zephyr kernel source files (threading-only subset)
# Excludes files requiring device tree or full hardware support
# Note: init.c, idle.c excluded (require full initialization and PM support)
ZEPHYR_KERNEL_SRC_C := \
	$(ZEPHYR_BASE)/kernel/thread.c \
	$(ZEPHYR_BASE)/kernel/sched.c \
	$(ZEPHYR_BASE)/kernel/mutex.c \
	$(ZEPHYR_BASE)/kernel/sem.c \
	$(ZEPHYR_BASE)/kernel/condvar.c \
	$(ZEPHYR_BASE)/kernel/priority_queues.c \
	$(ZEPHYR_BASE)/kernel/timeout.c \
	$(ZEPHYR_BASE)/kernel/timer.c \
	$(ZEPHYR_BASE)/kernel/thread_monitor.c \
	$(ZEPHYR_BASE)/kernel/errno.c \
	$(ZEPHYR_BASE)/kernel/version.c \
	$(ZEPHYR_BASE)/lib/os/thread_entry.c \
	$(ZEPHYR_BASE)/lib/utils/rb.c

# MicroPython-Zephyr integration
ZEPHYR_MP_SRC_C := \
	$(ZEPHYR_KERNEL)/kernel/mpthread_zephyr.c

# Architecture-specific files (defined per-architecture below)
ZEPHYR_ARCH_SRC_C :=

# POSIX architecture (for Unix/native simulation)
ifeq ($(ZEPHYR_ARCH),posix)
# POSIX arch files - using NSI (Native Simulator Infrastructure) for threading
ZEPHYR_ARCH_SRC_C += \
	$(ZEPHYR_BASE)/arch/posix/core/swap.c \
	$(ZEPHYR_BASE)/arch/posix/core/thread.c \
	$(ZEPHYR_BASE)/arch/posix/core/irq.c \
	$(ZEPHYR_BASE)/arch/posix/core/cpuhalt.c \
	$(ZEPHYR_BASE)/arch/posix/core/fatal.c \
	$(ZEPHYR_KERNEL)/posix_nsi_board.c \
	$(ZEPHYR_BASE)/scripts/native_simulator/common/src/nct.c

# Add POSIX arch include paths (generated headers and arch headers)
ZEPHYR_INC += -I$(ZEPHYR_KERNEL)/generated/zephyr/arch/posix
ZEPHYR_INC += -I$(ZEPHYR_BASE)/arch/posix/include

# Add NSI include paths for nct.c and our NSI board layer
ZEPHYR_INC += -I$(ZEPHYR_BASE)/scripts/native_simulator/common/src/include
ZEPHYR_INC += -I$(ZEPHYR_BASE)/scripts/native_simulator/common/src
endif

# ARM architecture (for STM32, nRF, etc.) - future
ifeq ($(ZEPHYR_ARCH),arm)
# TBD: ARM-specific files
endif

# Export for ports to use
export ZEPHYR_INC
export ZEPHYR_CFLAGS
export ZEPHYR_GEN_HEADERS
export ZEPHYR_KERNEL_SRC_C
export ZEPHYR_ARCH_SRC_C
export ZEPHYR_MP_SRC_C
