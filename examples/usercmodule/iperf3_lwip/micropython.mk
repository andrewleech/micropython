IPERF3_LWIP_MOD_DIR := $(USERMOD_DIR)

# Add C file to build
SRC_USERMOD += $(IPERF3_LWIP_MOD_DIR)/iperf3_lwip.c

# Include paths
CFLAGS_USERMOD += -I$(IPERF3_LWIP_MOD_DIR)

# Optimize for size instead of aggressive optimization.
# Aggressive flags (-O3, -funroll-loops, -finline-functions) can cause issues
# with static buffers on memory-constrained embedded systems.
CFLAGS_USERMOD += -Os  # Optimize for size, safe for embedded
