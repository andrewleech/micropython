CIPERF_MOD_DIR := $(USERMOD_DIR)

# Add C file to build
SRC_USERMOD += $(CIPERF_MOD_DIR)/ciperf.c

# Include paths
CFLAGS_USERMOD += -I$(CIPERF_MOD_DIR)

# Performance optimization flags
# -O3: Maximum optimization for speed
# -funroll-loops: Unroll loops for better performance
# -finline-functions: Inline functions aggressively
CFLAGS_USERMOD += -O3 -funroll-loops -finline-functions
