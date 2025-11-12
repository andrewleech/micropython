/*
 * iperf3-compatible Network Performance Test Module for MicroPython
 *
 * This module provides iperf3-compatible network testing by using lwIP TCP/UDP API directly.
 * Compatible with standard iperf3 client/server for interoperability testing.
 *
 * Based on ciperf module, extended with iperf3 protocol support.
 *
 * Expected performance: 500-800 Mbits/sec on STM32N6 @ 800MHz
 */

#include "py/runtime.h"
#include "py/mphal.h"

#if MICROPY_PY_LWIP

#include "lwip/tcp.h"
#include "lwip/udp.h"
#include "lwip/ip_addr.h"
#include "lwip/err.h"

// Buffer size optimized for Ethernet MTU and DMA efficiency
// 16KB provides good balance between memory usage and throughput
#define IPERF3_BUFFER_SIZE (16384)

// Test duration in milliseconds
#define IPERF3_DEFAULT_DURATION_MS (10000)

// Port number (5201 is standard iperf3)
#define IPERF3_DEFAULT_PORT (5201)

// Default UDP bandwidth (100 Mbits/sec)
#define IPERF3_DEFAULT_UDP_BANDWIDTH (100000000)

// UDP packet size (1470 bytes fits in Ethernet MTU with headers)
#define IPERF3_UDP_PACKET_SIZE (1470)

// iperf3 UDP packet header (12 bytes, compatible with iperf3)
typedef struct __attribute__((packed)) {
    uint32_t sec;      // Timestamp seconds (network byte order)
    uint32_t usec;     // Timestamp microseconds (network byte order)
    uint32_t seq;      // Sequence number (network byte order)
    uint8_t data[];    // Payload data
} iperf3_udp_packet_t;

// Performance-critical: heap-allocated buffer to avoid BSS bloat
static uint8_t *iperf3_tx_buffer = NULL;

typedef struct {
    struct tcp_pcb *pcb;
    struct tcp_pcb *listen_pcb;  // Track listen socket separately
    uint32_t start_time_ms;
    uint32_t duration_ms;
    uint64_t bytes_transferred;
    bool is_server;
    bool is_running;
    err_t last_error;
} iperf3_state_t;

static iperf3_state_t iperf3_state = {0};

// UDP-specific state for jitter and loss tracking
typedef struct {
    struct udp_pcb *pcb;
    uint32_t start_time_ms;
    uint32_t duration_ms;
    uint64_t bytes_transferred;
    uint32_t packets_sent;
    uint32_t packets_received;
    uint32_t packets_lost;
    uint32_t expected_seq;
    float jitter;
    uint32_t prev_transit;
    bool is_running;
    err_t last_error;
} iperf3_udp_state_t;

static iperf3_udp_state_t iperf3_udp_state = {0};

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
 * CRITICAL PERFORMANCE: This callback is called from lwIP context
 * Must be fast and non-blocking
 * Uses TCP_WRITE_FLAG_MORE for zero-copy where possible
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
    // CRITICAL: tcp_sndbuf returns available send buffer size
    u16_t available = tcp_sndbuf(pcb);
    if (available > 0) {
        u16_t to_send = (available > IPERF3_BUFFER_SIZE) ? IPERF3_BUFFER_SIZE : available;

        // PERFORMANCE: TCP_WRITE_FLAG_MORE hints that more data is coming
        // This allows lwIP to coalesce segments for efficiency
        err_t err = tcp_write(pcb, iperf3_tx_buffer, to_send,
            TCP_WRITE_FLAG_MORE | TCP_WRITE_FLAG_COPY);

        if (err == ERR_OK) {
            state->bytes_transferred += to_send;
            // CRITICAL: tcp_output() forces immediate transmission
            // Essential for maximum throughput
            tcp_output(pcb);
        } else {
            state->last_error = err;
        }
    }

    return ERR_OK;
}

/*
 * CRITICAL PERFORMANCE: Receive callback
 * Must acknowledge data quickly to maintain TCP window
 */
static err_t iperf3_tcp_recv_cb(void *arg, struct tcp_pcb *pcb, struct pbuf *p, err_t err) {
    iperf3_state_t *state = (iperf3_state_t *)arg;

    if (p == NULL) {
        // Connection closed
        state->is_running = false;
        return ERR_OK;
    }

    if (err != ERR_OK) {
        pbuf_free(p);
        state->last_error = err;
        return err;
    }

    // Count received bytes
    state->bytes_transferred += p->tot_len;

    // CRITICAL: tcp_recved() must be called to update TCP window
    // This is essential for maintaining throughput
    tcp_recved(pcb, p->tot_len);

    // Free the pbuf immediately - we don't need to process the data
    // PERFORMANCE: Minimize processing in receive path
    pbuf_free(p);

    // Check test duration
    uint32_t elapsed_ms = mp_hal_ticks_ms() - state->start_time_ms;
    if (elapsed_ms >= state->duration_ms) {
        state->is_running = false;
    }

    return ERR_OK;
}

/*
 * Accept callback for server mode
 */
static err_t iperf3_tcp_accept_cb(void *arg, struct tcp_pcb *newpcb, err_t err) {
    iperf3_state_t *state = (iperf3_state_t *)arg;

    if (err != ERR_OK || newpcb == NULL) {
        return ERR_VAL;
    }

    state->pcb = newpcb;

    // PERFORMANCE: Enable TCP_NODELAY to disable Nagle's algorithm
    // This is critical for throughput testing
    tcp_setprio(newpcb, TCP_PRIO_MAX);
    tcp_nagle_disable(newpcb);

    // Set callbacks
    tcp_arg(newpcb, state);
    tcp_recv(newpcb, iperf3_tcp_recv_cb);
    tcp_err(newpcb, iperf3_tcp_err_cb);

    // Start test
    state->start_time_ms = mp_hal_ticks_ms();
    state->is_running = true;
    state->bytes_transferred = 0;

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

    // PERFORMANCE: Configure for maximum throughput
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
 * PERFORMANCE: Lazy initialization on first use
 * CRITICAL FIX: Heap-allocate buffer to avoid BSS bloat
 */
static bool iperf3_buffers_initialized = false;

static void iperf3_init_buffers(void) {
    if (iperf3_buffers_initialized) {
        return;
    }

    // Allocate buffer on heap
    if (iperf3_tx_buffer == NULL) {
        iperf3_tx_buffer = m_malloc(IPERF3_BUFFER_SIZE);
        if (iperf3_tx_buffer == NULL) {
            mp_raise_msg(&mp_type_MemoryError, MP_ERROR_TEXT("Failed to allocate buffer"));
        }
    }

    // Fill with incrementing pattern for easy debugging
    for (int i = 0; i < IPERF3_BUFFER_SIZE; i++) {
        iperf3_tx_buffer[i] = (uint8_t)i;
    }
    iperf3_buffers_initialized = true;
}

/*
 * TCP server implementation (internal)
 */
static mp_obj_t iperf3_lwip_tcp_server_impl(uint16_t port, uint32_t duration_ms) {
    // Lazy initialization
    iperf3_init_buffers();

    // Create TCP PCB
    struct tcp_pcb *pcb = tcp_new();
    if (pcb == NULL) {
        mp_raise_msg(&mp_type_OSError, MP_ERROR_TEXT("Failed to create TCP PCB"));
    }

    // Bind to port
    err_t err = tcp_bind(pcb, IP_ADDR_ANY, port);
    if (err != ERR_OK) {
        tcp_abort(pcb);
        mp_raise_msg(&mp_type_OSError, MP_ERROR_TEXT("Failed to bind"));
    }

    // Listen
    pcb = tcp_listen(pcb);
    if (pcb == NULL) {
        mp_raise_msg(&mp_type_OSError, MP_ERROR_TEXT("Failed to listen"));
    }

    // Initialize state
    iperf3_state.listen_pcb = pcb;  // Track listen socket
    iperf3_state.pcb = NULL;  // Connection PCB set in accept callback
    iperf3_state.duration_ms = duration_ms;
    iperf3_state.is_server = true;
    iperf3_state.is_running = false;
    iperf3_state.bytes_transferred = 0;
    iperf3_state.last_error = ERR_OK;

    // Set accept callback
    tcp_arg(pcb, &iperf3_state);
    tcp_accept(pcb, iperf3_tcp_accept_cb);

    mp_printf(&mp_plat_print, "Server listening on port %d\n", port);

    // Wait for connection and test completion
    // CRITICAL REVIEW: This blocks Python but necessary for synchronous operation
    // Alternative would be async/await but adds complexity
    while (!iperf3_state.is_running) {
        mp_hal_delay_ms(10);
        MICROPY_EVENT_POLL_HOOK
    }

    mp_printf(&mp_plat_print, "Connected, receiving data...\n");

    // Wait for test to complete
    while (iperf3_state.is_running) {
        mp_hal_delay_ms(100);
        MICROPY_EVENT_POLL_HOOK
    }

    // Calculate results
    uint32_t elapsed_ms = mp_hal_ticks_ms() - iperf3_state.start_time_ms;
    float elapsed_sec = elapsed_ms / 1000.0f;
    float mbytes = iperf3_state.bytes_transferred / (1024.0f * 1024.0f);
    float mbits_per_sec = (iperf3_state.bytes_transferred * 8.0f) / (elapsed_sec * 1000000.0f);

    mp_printf(&mp_plat_print, "\nReceived %.2f MB in %.2f sec = %.2f Mbits/sec\n",
        (double)mbytes, (double)elapsed_sec, (double)mbits_per_sec);

    // Cleanup - close connection if still open
    if (iperf3_state.pcb != NULL) {
        err_t err = tcp_close(iperf3_state.pcb);
        if (err != ERR_OK) {
            tcp_abort(iperf3_state.pcb);
        }
        iperf3_state.pcb = NULL;
    }

    // Close listen socket
    if (iperf3_state.listen_pcb != NULL) {
        err_t err = tcp_close(iperf3_state.listen_pcb);
        if (err != ERR_OK) {
            tcp_abort(iperf3_state.listen_pcb);
        }
        iperf3_state.listen_pcb = NULL;
    }

    return mp_const_none;
}

/*
 * TCP client implementation (internal)
 */
static mp_obj_t iperf3_lwip_tcp_client_impl(const char *host, uint16_t port, uint32_t duration_ms) {
    // Lazy initialization
    iperf3_init_buffers();

    // Parse IP address
    ip_addr_t server_ip;
    if (!ipaddr_aton(host, &server_ip)) {
        mp_raise_msg(&mp_type_ValueError, MP_ERROR_TEXT("Invalid IP address"));
    }

    // Create TCP PCB
    struct tcp_pcb *pcb = tcp_new();
    if (pcb == NULL) {
        mp_raise_msg(&mp_type_OSError, MP_ERROR_TEXT("Failed to create TCP PCB"));
    }

    // Initialize state
    iperf3_state.pcb = pcb;
    iperf3_state.listen_pcb = NULL;  // Not used in client mode
    iperf3_state.duration_ms = duration_ms;
    iperf3_state.is_server = false;
    iperf3_state.is_running = false;
    iperf3_state.bytes_transferred = 0;
    iperf3_state.last_error = ERR_OK;

    // Set callbacks
    tcp_arg(pcb, &iperf3_state);

    // Connect
    mp_printf(&mp_plat_print, "Connecting to %s:%d...\n", host, port);
    err_t err = tcp_connect(pcb, &server_ip, port, iperf3_tcp_connected_cb);
    if (err != ERR_OK) {
        tcp_abort(pcb);
        mp_raise_msg(&mp_type_OSError, MP_ERROR_TEXT("Failed to connect"));
    }

    // Wait for connection
    uint32_t connect_timeout = mp_hal_ticks_ms() + 5000;
    while (!iperf3_state.is_running && mp_hal_ticks_ms() < connect_timeout) {
        mp_hal_delay_ms(10);
        MICROPY_EVENT_POLL_HOOK
        if (iperf3_state.last_error != ERR_OK) {
            tcp_abort(pcb);
            mp_raise_msg(&mp_type_OSError, MP_ERROR_TEXT("Connection failed"));
        }
    }

    if (!iperf3_state.is_running) {
        tcp_abort(pcb);
        mp_raise_msg(&mp_type_OSError, MP_ERROR_TEXT("Connection timeout"));
    }

    mp_printf(&mp_plat_print, "Connected, sending data...\n");

    // Wait for test to complete
    while (iperf3_state.is_running) {
        mp_hal_delay_ms(100);
        MICROPY_EVENT_POLL_HOOK
    }

    // Calculate results
    uint32_t elapsed_ms = mp_hal_ticks_ms() - iperf3_state.start_time_ms;
    float elapsed_sec = elapsed_ms / 1000.0f;
    float mbytes = iperf3_state.bytes_transferred / (1024.0f * 1024.0f);
    float mbits_per_sec = (iperf3_state.bytes_transferred * 8.0f) / (elapsed_sec * 1000000.0f);

    mp_printf(&mp_plat_print, "\nSent %.2f MB in %.2f sec = %.2f Mbits/sec\n",
        (double)mbytes, (double)elapsed_sec, (double)mbits_per_sec);

    // Cleanup - close connection if still open
    if (iperf3_state.pcb != NULL) {
        err_t err = tcp_close(iperf3_state.pcb);
        if (err != ERR_OK) {
            tcp_abort(iperf3_state.pcb);
        }
        iperf3_state.pcb = NULL;
    }

    return mp_const_none;
}

/*
 * UDP receive callback - tracks jitter and packet loss
 */
static void iperf3_udp_recv_cb(void *arg, struct udp_pcb *pcb, struct pbuf *p,
    const ip_addr_t *addr, u16_t port) {
    iperf3_udp_state_t *state = (iperf3_udp_state_t *)arg;

    if (p == NULL) {
        return;
    }

    // Parse iperf3 UDP header (12 bytes)
    if (p->tot_len < sizeof(iperf3_udp_packet_t)) {
        pbuf_free(p);
        return;
    }

    iperf3_udp_packet_t *packet = (iperf3_udp_packet_t *)p->payload;

    // Convert header fields from network byte order
    uint32_t sec = lwip_ntohl(packet->sec);
    uint32_t usec = lwip_ntohl(packet->usec);
    uint32_t seq = lwip_ntohl(packet->seq);

    // Track packets received
    state->packets_received++;
    state->bytes_transferred += p->tot_len;

    // Detect packet loss from sequence gaps
    if (state->expected_seq != 0 && seq != state->expected_seq) {
        if (seq > state->expected_seq) {
            state->packets_lost += (seq - state->expected_seq);
        }
    }
    state->expected_seq = seq + 1;

    // Calculate jitter using RFC 1889 algorithm
    uint32_t arrival_time_us = mp_hal_ticks_ms() * 1000;
    uint32_t sent_time_us = (sec * 1000000) + usec;
    uint32_t transit = arrival_time_us - sent_time_us;

    if (state->prev_transit != 0) {
        int32_t delta = (int32_t)(transit - state->prev_transit);
        if (delta < 0) {
            delta = -delta;
        }
        state->jitter += (delta - state->jitter) / 16.0f;
    }
    state->prev_transit = transit;

    pbuf_free(p);

    // Check test duration
    uint32_t elapsed_ms = mp_hal_ticks_ms() - state->start_time_ms;
    if (elapsed_ms >= state->duration_ms) {
        state->is_running = false;
    }
}

/*
 * UDP server implementation (internal)
 */
static mp_obj_t iperf3_lwip_udp_server_impl(uint16_t port, uint32_t duration_ms) {
    // Create UDP PCB
    struct udp_pcb *pcb = udp_new();
    if (pcb == NULL) {
        mp_raise_msg(&mp_type_OSError, MP_ERROR_TEXT("Failed to create UDP PCB"));
    }

    // Bind to port
    err_t err = udp_bind(pcb, IP_ADDR_ANY, port);
    if (err != ERR_OK) {
        udp_remove(pcb);
        mp_raise_msg(&mp_type_OSError, MP_ERROR_TEXT("Failed to bind"));
    }

    // Initialize state
    iperf3_udp_state.pcb = pcb;
    iperf3_udp_state.duration_ms = duration_ms;
    iperf3_udp_state.bytes_transferred = 0;
    iperf3_udp_state.packets_sent = 0;
    iperf3_udp_state.packets_received = 0;
    iperf3_udp_state.packets_lost = 0;
    iperf3_udp_state.expected_seq = 0;
    iperf3_udp_state.jitter = 0.0f;
    iperf3_udp_state.prev_transit = 0;
    iperf3_udp_state.is_running = false;
    iperf3_udp_state.last_error = ERR_OK;

    // Set receive callback
    udp_recv(pcb, iperf3_udp_recv_cb, &iperf3_udp_state);

    mp_printf(&mp_plat_print, "UDP server listening on port %d\n", port);

    // Start test timer
    iperf3_udp_state.start_time_ms = mp_hal_ticks_ms();
    iperf3_udp_state.is_running = true;

    // Wait for test to complete
    while (iperf3_udp_state.is_running) {
        mp_hal_delay_ms(100);
        MICROPY_EVENT_POLL_HOOK
    }

    // Calculate results
    uint32_t elapsed_ms = mp_hal_ticks_ms() - iperf3_udp_state.start_time_ms;
    float elapsed_sec = elapsed_ms / 1000.0f;
    float mbytes = iperf3_udp_state.bytes_transferred / (1024.0f * 1024.0f);
    float mbits_per_sec = (iperf3_udp_state.bytes_transferred * 8.0f) / (elapsed_sec * 1000000.0f);

    uint32_t total_packets = iperf3_udp_state.packets_received + iperf3_udp_state.packets_lost;
    float loss_percent = (total_packets > 0) ?
        (iperf3_udp_state.packets_lost * 100.0f) / total_packets : 0.0f;
    float jitter_ms = iperf3_udp_state.jitter / 1000.0f;

    mp_printf(&mp_plat_print, "\nReceived %.2f MB in %.2f sec = %.2f Mbits/sec\n",
        (double)mbytes, (double)elapsed_sec, (double)mbits_per_sec);
    mp_printf(&mp_plat_print, "Packets: %lu received, %lu lost (%.2f%%), jitter %.3f ms\n",
        (unsigned long)iperf3_udp_state.packets_received,
        (unsigned long)iperf3_udp_state.packets_lost,
        (double)loss_percent, (double)jitter_ms);

    // Cleanup
    udp_remove(pcb);
    iperf3_udp_state.pcb = NULL;

    return mp_const_none;
}

/*
 * UDP client implementation (internal)
 */
static mp_obj_t iperf3_lwip_udp_client_impl(const char *host, uint16_t port, uint32_t duration_ms, uint32_t bandwidth) {
    // Lazy initialization
    iperf3_init_buffers();

    // Parse IP address
    ip_addr_t server_ip;
    if (!ipaddr_aton(host, &server_ip)) {
        mp_raise_msg(&mp_type_ValueError, MP_ERROR_TEXT("Invalid IP address"));
    }

    // Create UDP PCB
    struct udp_pcb *pcb = udp_new();
    if (pcb == NULL) {
        mp_raise_msg(&mp_type_OSError, MP_ERROR_TEXT("Failed to create UDP PCB"));
    }

    // Connect to server
    err_t err = udp_connect(pcb, &server_ip, port);
    if (err != ERR_OK) {
        udp_remove(pcb);
        mp_raise_msg(&mp_type_OSError, MP_ERROR_TEXT("Failed to connect"));
    }

    // Initialize state
    iperf3_udp_state.pcb = pcb;
    iperf3_udp_state.start_time_ms = mp_hal_ticks_ms();
    iperf3_udp_state.duration_ms = duration_ms;
    iperf3_udp_state.bytes_transferred = 0;
    iperf3_udp_state.packets_sent = 0;
    iperf3_udp_state.is_running = true;
    iperf3_udp_state.last_error = ERR_OK;

    mp_printf(&mp_plat_print, "UDP client sending to %s:%d at %lu bps\n",
        host, port, (unsigned long)bandwidth);

    // Calculate inter-packet delay for bandwidth limiting
    // bandwidth (bits/sec) = packet_size (bytes) * 8 / delay (sec)
    // delay (us) = packet_size * 8 * 1000000 / bandwidth
    uint32_t packet_size = IPERF3_UDP_PACKET_SIZE;
    uint32_t delay_us = (uint32_t)((uint64_t)packet_size * 8 * 1000000 / bandwidth);

    uint32_t seq = 0;

    // Send loop
    while (iperf3_udp_state.is_running) {
        // Check test duration
        uint32_t elapsed_ms = mp_hal_ticks_ms() - iperf3_udp_state.start_time_ms;
        if (elapsed_ms >= duration_ms) {
            break;
        }

        // Allocate pbuf with iperf3 header + data
        struct pbuf *p = pbuf_alloc(PBUF_TRANSPORT, packet_size, PBUF_RAM);
        if (p == NULL) {
            mp_hal_delay_ms(1);
            continue;
        }

        // Fill iperf3 UDP header
        iperf3_udp_packet_t *packet = (iperf3_udp_packet_t *)p->payload;
        uint32_t now_ms = mp_hal_ticks_ms();
        uint32_t sec = now_ms / 1000;
        uint32_t usec = (now_ms % 1000) * 1000;

        packet->sec = lwip_htonl(sec);
        packet->usec = lwip_htonl(usec);
        packet->seq = lwip_htonl(seq);

        // Fill payload with pattern
        size_t header_size = sizeof(iperf3_udp_packet_t);
        if (packet_size > header_size) {
            memcpy(packet->data, iperf3_tx_buffer, packet_size - header_size);
        }

        // Send packet
        err = udp_send(pcb, p);
        pbuf_free(p);

        if (err == ERR_OK) {
            iperf3_udp_state.bytes_transferred += packet_size;
            iperf3_udp_state.packets_sent++;
            seq++;
        } else {
            iperf3_udp_state.last_error = err;
        }

        // Rate limiting
        mp_hal_delay_us(delay_us);
        MICROPY_EVENT_POLL_HOOK
    }

    // Calculate results
    uint32_t elapsed_ms = mp_hal_ticks_ms() - iperf3_udp_state.start_time_ms;
    float elapsed_sec = elapsed_ms / 1000.0f;
    float mbytes = iperf3_udp_state.bytes_transferred / (1024.0f * 1024.0f);
    float mbits_per_sec = (iperf3_udp_state.bytes_transferred * 8.0f) / (elapsed_sec * 1000000.0f);

    mp_printf(&mp_plat_print, "\nSent %.2f MB in %.2f sec = %.2f Mbits/sec\n",
        (double)mbytes, (double)elapsed_sec, (double)mbits_per_sec);
    mp_printf(&mp_plat_print, "Packets sent: %lu\n", (unsigned long)iperf3_udp_state.packets_sent);

    // Cleanup
    udp_remove(pcb);
    iperf3_udp_state.pcb = NULL;

    return mp_const_none;
}

/*
 * Python: iperf3_lwip.transfer(mode, *args)
 *
 * Unified transfer function supporting TCP and UDP modes
 *
 * Modes:
 *   0 or 'tcp_client': TCP client (args: host, port, duration)
 *   1 or 'tcp_server': TCP server (args: port, duration)
 *   2 or 'udp_client': UDP client (args: host, port, duration, bandwidth)
 *   3 or 'udp_server': UDP server (args: port, duration)
 */
static mp_obj_t iperf3_lwip_transfer(size_t n_args, const mp_obj_t *args) {
    if (n_args < 1) {
        mp_raise_TypeError(MP_ERROR_TEXT("mode required"));
    }

    // Parse mode (first argument)
    const char *mode = NULL;
    int mode_int = -1;

    if (mp_obj_is_str(args[0])) {
        mode = mp_obj_str_get_str(args[0]);
    } else if (mp_obj_is_int(args[0])) {
        mode_int = mp_obj_get_int(args[0]);
    } else {
        mp_raise_TypeError(MP_ERROR_TEXT("mode must be int or str"));
    }

    // Dispatch based on mode
    if ((mode && strcmp(mode, "tcp_client") == 0) || mode_int == 0) {
        // TCP client: transfer('tcp_client', host, port, duration)
        if (n_args < 4) {
            mp_raise_TypeError(MP_ERROR_TEXT("tcp_client requires: host, port, duration"));
        }
        const char *host = mp_obj_str_get_str(args[1]);
        uint16_t port = mp_obj_get_int(args[2]);
        uint32_t duration_ms = mp_obj_get_int(args[3]) * 1000;
        return iperf3_lwip_tcp_client_impl(host, port, duration_ms);
    } else if ((mode && strcmp(mode, "tcp_server") == 0) || mode_int == 1) {
        // TCP server: transfer('tcp_server', port, duration)
        if (n_args < 3) {
            mp_raise_TypeError(MP_ERROR_TEXT("tcp_server requires: port, duration"));
        }
        uint16_t port = mp_obj_get_int(args[1]);
        uint32_t duration_ms = mp_obj_get_int(args[2]) * 1000;
        return iperf3_lwip_tcp_server_impl(port, duration_ms);
    } else if ((mode && strcmp(mode, "udp_client") == 0) || mode_int == 2) {
        // UDP client: transfer('udp_client', host, port, duration, bandwidth)
        if (n_args < 5) {
            mp_raise_TypeError(MP_ERROR_TEXT("udp_client requires: host, port, duration, bandwidth"));
        }
        const char *host = mp_obj_str_get_str(args[1]);
        uint16_t port = mp_obj_get_int(args[2]);
        uint32_t duration_ms = mp_obj_get_int(args[3]) * 1000;
        uint32_t bandwidth = mp_obj_get_int(args[4]);
        return iperf3_lwip_udp_client_impl(host, port, duration_ms, bandwidth);
    } else if ((mode && strcmp(mode, "udp_server") == 0) || mode_int == 3) {
        // UDP server: transfer('udp_server', port, duration)
        if (n_args < 3) {
            mp_raise_TypeError(MP_ERROR_TEXT("udp_server requires: port, duration"));
        }
        uint16_t port = mp_obj_get_int(args[1]);
        uint32_t duration_ms = mp_obj_get_int(args[2]) * 1000;
        return iperf3_lwip_udp_server_impl(port, duration_ms);
    } else {
        mp_raise_ValueError(MP_ERROR_TEXT("Invalid mode. Use 0-3 or 'tcp_client', 'tcp_server', 'udp_client', 'udp_server'"));
    }
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(iperf3_lwip_transfer_obj, 1, 5, iperf3_lwip_transfer);

// Module globals
static const mp_rom_map_elem_t iperf3_lwip_module_globals_table[] = {
    { MP_ROM_QSTR(MP_QSTR___name__), MP_ROM_QSTR(MP_QSTR_iperf3_lwip) },
    { MP_ROM_QSTR(MP_QSTR_transfer), MP_ROM_PTR(&iperf3_lwip_transfer_obj) },
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
