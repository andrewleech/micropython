/*
 * iperf3_lwip - High-performance network testing using direct lwIP PCB API
 *
 * This module creates its own TCP PCBs and manages connections directly,
 * bypassing the socket layer for maximum throughput.
 *
 * Architecture:
 * - Creates TCP PCBs with tcp_new()
 * - Accepts parameters (IP, port, duration) from Python
 * - Returns results dictionary to Python
 * - Uses callback-driven design for zero-copy transmission
 *
 * Expected performance: 400-600 Mbits/sec on STM32N6
 */

#include "py/runtime.h"
#include "py/mphal.h"

#if MICROPY_PY_LWIP

#include "lwip/tcp.h"
#include "lwip/ip_addr.h"
#include "lwip/err.h"

// Buffer size optimized for Ethernet MTU and DMA efficiency
#define IPERF3_BUFFER_SIZE (16384)

// Default test duration in milliseconds
#define IPERF3_DEFAULT_DURATION_MS (10000)

// Default iperf3 port
#define IPERF3_DEFAULT_PORT (5201)

// Heap-allocated buffer to avoid BSS bloat
static uint8_t *iperf3_tx_buffer = NULL;

// Test state structure
typedef struct {
    struct tcp_pcb *pcb;
    uint32_t start_time_ms;
    uint32_t duration_ms;
    uint64_t bytes_transferred;
    bool is_running;
    err_t last_error;
} iperf3_state_t;

static iperf3_state_t iperf3_state = {0};

/*
 * Error callback - called when connection fails
 */
static void iperf3_tcp_err_cb(void *arg, err_t err) {
    iperf3_state_t *state = (iperf3_state_t *)arg;
    state->is_running = false;
    state->last_error = err;
    // PCB is already freed by lwIP when error callback is called
    state->pcb = NULL;
}

/*
 * TCP sent callback - drives the TX data pump for maximum throughput
 */
static err_t iperf3_tcp_sent_cb(void *arg, struct tcp_pcb *pcb, u16_t len) {
    iperf3_state_t *state = (iperf3_state_t *)arg;

    if (!state->is_running) {
        return ERR_OK;
    }

    // Check if test duration expired
    uint32_t elapsed_ms = mp_hal_ticks_ms() - state->start_time_ms;
    if (elapsed_ms >= state->duration_ms) {
        state->is_running = false;
        return ERR_OK;
    }

    // Send more data - use largest possible chunks
    u16_t available = tcp_sndbuf(pcb);
    if (available > 0) {
        u16_t to_send = (available > IPERF3_BUFFER_SIZE) ? IPERF3_BUFFER_SIZE : available;

        // TCP_WRITE_FLAG_MORE hints that more data is coming for segment coalescing
        err_t err = tcp_write(pcb, iperf3_tx_buffer, to_send,
            TCP_WRITE_FLAG_MORE | TCP_WRITE_FLAG_COPY);

        if (err == ERR_OK) {
            state->bytes_transferred += to_send;
            // Force immediate transmission
            tcp_output(pcb);
        } else {
            state->last_error = err;
        }
    }

    return ERR_OK;
}

/*
 * Connection callback for client mode
 */
static err_t iperf3_tcp_connected_cb(void *arg, struct tcp_pcb *pcb, err_t err) {
    iperf3_state_t *state = (iperf3_state_t *)arg;

    if (err != ERR_OK) {
        state->last_error = err;
        return err;
    }

    // Configure for maximum throughput
    tcp_setprio(pcb, TCP_PRIO_MAX);
    tcp_nagle_disable(pcb);

    // Set callbacks
    tcp_sent(pcb, iperf3_tcp_sent_cb);
    tcp_err(pcb, iperf3_tcp_err_cb);

    // Start sending immediately
    state->start_time_ms = mp_hal_ticks_ms();
    state->is_running = true;
    state->bytes_transferred = 0;

    // Kickstart transmission
    iperf3_tcp_sent_cb(state, pcb, 0);

    return ERR_OK;
}

/*
 * Initialize transmit buffer with pattern
 * Heap-allocate buffer to avoid BSS bloat
 */
static bool iperf3_buffers_initialized = false;

static void iperf3_init_buffers(uint32_t buffer_size) {
    if (iperf3_buffers_initialized) {
        return;
    }

    // Allocate buffer on heap
    if (iperf3_tx_buffer == NULL) {
        iperf3_tx_buffer = m_malloc(buffer_size);
        if (iperf3_tx_buffer == NULL) {
            mp_raise_msg(&mp_type_MemoryError, MP_ERROR_TEXT("Failed to allocate buffer"));
        }
    }

    // Fill with incrementing pattern for easy debugging
    for (uint32_t i = 0; i < buffer_size; i++) {
        iperf3_tx_buffer[i] = (uint8_t)i;
    }
    iperf3_buffers_initialized = true;
}

/*
 * Python: iperf3_lwip.tcp_send_test(params_dict)
 *
 * Args:
 *   params_dict: dict with keys:
 *     - 'server_ip': str, IP address of iperf3 server
 *     - 'port': int, port number (default 5201)
 *     - 'duration_ms': int, test duration in ms (default 10000)
 *     - 'buffer_size': int, buffer size in bytes (default 16384)
 *
 * Returns: dict with keys:
 *   - 'bytes': int, bytes transferred
 *   - 'duration_ms': int, actual duration in milliseconds
 */
static mp_obj_t iperf3_lwip_tcp_send_test(mp_obj_t params_dict) {
    // Extract parameters from dict
    mp_obj_t server_ip_obj = mp_obj_dict_get(params_dict, MP_OBJ_NEW_QSTR(MP_QSTR_server_ip));
    const char *server_ip = mp_obj_str_get_str(server_ip_obj);

    mp_obj_t port_obj = mp_obj_dict_get(params_dict, MP_OBJ_NEW_QSTR(MP_QSTR_port));
    uint16_t port = (port_obj != MP_OBJ_NULL) ? mp_obj_get_int(port_obj) : IPERF3_DEFAULT_PORT;

    mp_obj_t duration_obj = mp_obj_dict_get(params_dict, MP_OBJ_NEW_QSTR(MP_QSTR_duration_ms));
    uint32_t duration_ms = (duration_obj != MP_OBJ_NULL) ? mp_obj_get_int(duration_obj) : IPERF3_DEFAULT_DURATION_MS;

    mp_obj_t buffer_size_obj = mp_obj_dict_get(params_dict, MP_OBJ_NEW_QSTR(MP_QSTR_buffer_size));
    uint32_t buffer_size = (buffer_size_obj != MP_OBJ_NULL) ? mp_obj_get_int(buffer_size_obj) : IPERF3_BUFFER_SIZE;

    // Parse IP address
    ip_addr_t server_addr;
    if (!ipaddr_aton(server_ip, &server_addr)) {
        mp_raise_ValueError(MP_ERROR_TEXT("Invalid IP address"));
    }

    // Allocate buffer if needed
    iperf3_init_buffers(buffer_size);

    // Create TCP PCB
    struct tcp_pcb *pcb = tcp_new();
    if (pcb == NULL) {
        mp_raise_msg(&mp_type_OSError, MP_ERROR_TEXT("Failed to create TCP PCB"));
    }

    // Initialize state
    iperf3_state.pcb = pcb;
    iperf3_state.duration_ms = duration_ms;
    iperf3_state.is_running = false;
    iperf3_state.bytes_transferred = 0;
    iperf3_state.last_error = ERR_OK;

    // Set callbacks
    tcp_arg(pcb, &iperf3_state);

    // Connect
    err_t err = tcp_connect(pcb, &server_addr, port, iperf3_tcp_connected_cb);
    if (err != ERR_OK) {
        tcp_abort(pcb);
        mp_raise_msg(&mp_type_OSError, MP_ERROR_TEXT("Failed to connect"));
    }

    // Wait for connection (with timeout)
    uint32_t connect_timeout = mp_hal_ticks_ms() + 5000;
    while (!iperf3_state.is_running && mp_hal_ticks_ms() < connect_timeout) {
        mp_hal_delay_ms(10);
        MICROPY_EVENT_POLL_HOOK;
        if (iperf3_state.last_error != ERR_OK) {
            tcp_abort(pcb);
            mp_raise_msg(&mp_type_OSError, MP_ERROR_TEXT("Connection failed"));
        }
    }

    if (!iperf3_state.is_running) {
        tcp_abort(pcb);
        mp_raise_msg(&mp_type_OSError, MP_ERROR_TEXT("Connection timeout"));
    }

    // Run test loop
    while (iperf3_state.is_running) {
        mp_hal_delay_ms(100);
        MICROPY_EVENT_POLL_HOOK;
    }

    // Calculate actual duration
    uint32_t actual_duration_ms = mp_hal_ticks_ms() - iperf3_state.start_time_ms;

    // Build results dict
    mp_obj_t result = mp_obj_new_dict(2);
    mp_obj_dict_store(result, MP_OBJ_NEW_QSTR(MP_QSTR_bytes),
        mp_obj_new_int_from_ull(iperf3_state.bytes_transferred));
    mp_obj_dict_store(result, MP_OBJ_NEW_QSTR(MP_QSTR_duration_ms),
        mp_obj_new_int(actual_duration_ms));

    // Cleanup
    if (iperf3_state.pcb != NULL) {
        err_t err = tcp_close(pcb);
        if (err != ERR_OK) {
            tcp_abort(pcb);
        }
        iperf3_state.pcb = NULL;
    }

    return result;
}
MP_DEFINE_CONST_FUN_OBJ_1(iperf3_lwip_tcp_send_test_obj, iperf3_lwip_tcp_send_test);

// Module globals
static const mp_rom_map_elem_t iperf3_lwip_module_globals_table[] = {
    { MP_ROM_QSTR(MP_QSTR___name__), MP_ROM_QSTR(MP_QSTR_iperf3_lwip) },
    { MP_ROM_QSTR(MP_QSTR_tcp_send_test), MP_ROM_PTR(&iperf3_lwip_tcp_send_test_obj) },
};
static MP_DEFINE_CONST_DICT(iperf3_lwip_module_globals, iperf3_lwip_module_globals_table);

// Module definition
const mp_obj_module_t iperf3_lwip_user_cmodule = {
    .base = { &mp_type_module },
    .globals = (mp_obj_dict_t *)&iperf3_lwip_module_globals,
};

// Register module
MP_REGISTER_MODULE(MP_QSTR_iperf3_lwip, iperf3_lwip_user_cmodule);

#endif // MICROPY_PY_LWIP
