#ifndef SEGGER_RTT_CONF_H
#define SEGGER_RTT_CONF_H

// Minimal configuration for RTT
#define SEGGER_RTT_MAX_NUM_UP_BUFFERS             (3)
#define SEGGER_RTT_MAX_NUM_DOWN_BUFFERS           (3)
#define BUFFER_SIZE_UP                            (1024)
#define BUFFER_SIZE_DOWN                          (16)
#define SEGGER_RTT_PRINTF_BUFFER_SIZE             (64u)
#define SEGGER_RTT_MODE_DEFAULT                   2  // SEGGER_RTT_MODE_NO_BLOCK_SKIP
#define SEGGER_RTT_MEMCPY_USE_BYTELOOP            0

#endif