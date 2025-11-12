IPERF3_LWIP_MOD_DIR := $(USERMOD_DIR)

# Add C file to build
SRC_USERMOD += $(IPERF3_LWIP_MOD_DIR)/iperf3_lwip.c

# Include paths
CFLAGS_USERMOD += -I$(IPERF3_LWIP_MOD_DIR)

# Optimize for size instead of aggressive optimization.
CFLAGS_USERMOD += -Os
