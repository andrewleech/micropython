/*
 * This file is part of the MicroPython project, http://micropython.org/
 *
 * The MIT License (MIT)
 *
 * Copyright (c) 2024-2026 Angus Gratton
 *
 * Permission is hereby granted, free of charge, to any person obtaining a copy
 * of this software and associated documentation files (the "Software"), to deal
 * in the Software without restriction, including without limitation the rights
 * to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
 * copies of the Software, and to permit persons to whom the Software is
 * furnished to do so, subject to the following conditions:
 *
 * The above copyright notice and this permission notice shall be included in
 * all copies or substantial portions of the Software.
 *
 * THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
 * IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
 * FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
 * AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
 * LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
 * OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
 * THE SOFTWARE.
 */

// This file is never compiled standalone, it's included directly from
// extmod/machine_can.c via MICROPY_PY_MACHINE_CAN_INCLUDEFILE.
#include <stdbool.h>
#include "extmod/machine_can_port.h"
#include "can.h"
#include "irq.h"
#include "py/runtime.h"
#include "py/mperrno.h"
#include "py/mphal.h"
#include "py/gc.h"

#if MICROPY_HW_ENABLE_FDCAN
#define CAN_BRP_MIN 1
#define CAN_BRP_MAX 512
#define CAN_FD_BRS_BRP_MIN 1
#define CAN_FD_BRS_BRP_MAX 32
#define CAN_FILTERS_STD_EXT_SEPARATE 1

#else // Classic bxCAN
#define CAN_BRP_MIN 1
#define CAN_BRP_MAX 1024
#define CAN_FILTERS_STD_EXT_SEPARATE 0
#endif

#define TX_EMPTY UINT32_MAX


// A single frame buffered in the software receive ring (struct machine_can_port
// below). Fixed size so the ring can be indexed arithmetically and the receive
// interrupt handler never allocates. flags and len are narrower than the
// mp_uint_t/size_t types used elsewhere for the same values, since a CAN_MSG_FLAG_*
// combination fits in a handful of bits and a frame is at most MP_CAN_MAX_LEN (64)
// bytes; a ring entry is 72 bytes with FD's MP_CAN_MAX_LEN, so a large rxbuf is a
// correspondingly sized one-off allocation at CAN.init() time (rxbuf=256 is 18 KB).
typedef struct _can_rx_ring_entry_t {
    mp_uint_t id;
    uint16_t flags;
    uint8_t len;
    uint8_t data[MP_CAN_MAX_LEN];
} can_rx_ring_entry_t;

struct machine_can_port {
    CAN_HandleTypeDef h;
    uint32_t tx[CAN_TX_QUEUE_LEN];  // ID stored in each hardware tx buffer, or TX_EMPTY if empty
    bool irq_state_pending;
    bool error_passive;

    // Software receive ring, allocated once by machine_can_port_init() when
    // self->rxbuf_len is non-zero, and freed by machine_can_port_deinit().
    // NULL and zero length when rxbuf is 0 (the default), in which case
    // recv() reads directly from the hardware FIFO exactly as it did before
    // this ring existed.
    //
    // head and tail are each written from more than one context:
    // machine_can_irq_handler() advances head from the ISR, and
    // machine_can_port_recv() both advances tail and, via can_rx_ring_fill(),
    // advances head, from ordinary (non-ISR) context with the CAN interrupt
    // masked for the duration (see IRQ_PRI_CAN in machine_can_port_recv()
    // and machine_can_port_restart()). machine_can_port_update_counters() and
    // machine_can_port_irq_flags() only read the pair to report an
    // approximate pending count and are not synchronised against the ISR.
    can_rx_ring_entry_t *rx_ring;
    mp_uint_t rx_ring_len;   // Ring capacity in frames, 0 if disabled
    mp_uint_t rxring_head;     // Count of frames ever written to a RingIO sink
    mp_uint_t rxring_dropped;  // Frames the RingIO sink had no room for
    bool rxring_lost;          // A drop is owed to the next stored frame
    mp_uint_t rx_ring_head;  // Count of frames ever pushed
    mp_uint_t rx_ring_tail;  // Count of frames ever popped
};

// True when the receive interrupt has a sink to fill: either the port's own
// ring, read back through recv(), or a RingIO the application reads directly.
static inline bool can_rx_sink_active(machine_can_obj_t *self) {
    return self->port->rx_ring_len > 0 || self->rxring != NULL;
}


// Convert the port agnostic CAN mode to the ST mode
static uint32_t can_port_mode(machine_can_mode_t mode) {
    switch (mode) {
        case MP_CAN_MODE_NORMAL:
            return CAN_MODE_NORMAL;
        case MP_CAN_MODE_SLEEP:
            return CAN_MODE_SILENT; // Sleep is not an operating mode for ST's peripheral
        case MP_CAN_MODE_LOOPBACK:
            return CAN_MODE_LOOPBACK;
        case MP_CAN_MODE_SILENT:
            return CAN_MODE_SILENT;
        case MP_CAN_MODE_SILENT_LOOPBACK:
            return CAN_MODE_SILENT_LOOPBACK;
        default:
            assert(0); // Mode should have been checked already
            return CAN_MODE_NORMAL;
    }
}

static int machine_can_port_f_clock(const machine_can_obj_t *self) {
    return (int)can_get_source_freq();
}

static bool machine_can_port_supports_mode(const machine_can_obj_t *self, machine_can_mode_t mode) {
    return mode < MP_CAN_MODE_MAX;
}

static mp_uint_t machine_can_port_max_data_len(mp_uint_t flags) {
    #if MICROPY_HW_ENABLE_FDCAN
    if (flags & CAN_MSG_FLAG_FD_F) {
        return 64;
    }
    #endif
    return 8;
}

static void machine_can_port_init(machine_can_obj_t *self) {
    if (!self->port) {
        self->port = m_new(struct machine_can_port, 1);
    }
    memset(self->port, 0, sizeof(struct machine_can_port));
    for (int i = 0; i < CAN_TX_QUEUE_LEN; i++) {
        self->port->tx[i] = TX_EMPTY;
    }

    if (self->rxbuf_len > 0) {
        self->port->rx_ring = m_new(can_rx_ring_entry_t, self->rxbuf_len);
        self->port->rx_ring_len = self->rxbuf_len;
    }

    bool res = can_init(&self->port->h,
        self->can_idx + 1, // Convert 0-based index to 1-based 'can_id' for lower layer
        CAN_TX_QUEUE,
        can_port_mode(self->mode),
        self->brp,
        self->sjw,
        self->tseg1,
        self->tseg2,
        false); // auto_restart not currently exposed

    if (!res) {
        mp_raise_msg(&mp_type_OSError, MP_ERROR_TEXT("CAN init failed"));
    }

    if (can_rx_sink_active(self)) {
        // The sink is filled by the receive interrupt (can_rx_ring_fill(),
        // called from machine_can_irq_handler()), not by can_receive()
        // calls from Python. That interrupt is otherwise off until the user
        // requests IRQ_RX callbacks via CAN.irq(), so without this the ring
        // never receives anything: enable it unconditionally here.
        for (can_rx_fifo_t fifo = CAN_RX_FIFO0; fifo <= CAN_RX_FIFO1; fifo++) {
            can_enable_rx_interrupts(&self->port->h, fifo, true);
        }
    }
}

static void machine_can_port_cancel_all_tx(machine_can_obj_t *self) {
    struct machine_can_port *port = self->port;
    can_disable_tx_interrupts(&port->h);
    for (int i = 0; i < CAN_TX_QUEUE_LEN; i++) {
        can_cancel_transmit(&port->h, i);
        port->tx[i] = TX_EMPTY;
    }
}

static void machine_can_port_deinit(machine_can_obj_t *self) {
    machine_can_port_cancel_all_tx(self);
    can_deinit(&self->port->h);
    if (self->port->rx_ring != NULL) {
        m_del(can_rx_ring_entry_t, self->port->rx_ring, self->port->rx_ring_len);
        self->port->rx_ring = NULL;
        self->port->rx_ring_len = 0;
    }
}

static mp_int_t machine_can_port_send(machine_can_obj_t *self, mp_uint_t id, const byte *data, size_t data_len, mp_uint_t flags) {
    int idx_empty = -1; // Empty transmit buffer, where no later index has the same ID message in it

    // Scan through the current transmit queue to find an eligible buffer for transmit
    for (int i = 0; i < CAN_TX_QUEUE_LEN; i++) {
        uint32_t tx_id = self->port->tx[i];
        if (tx_id == TX_EMPTY) {
            // This slot is empty
            if (idx_empty == -1) {
                // Still have to keep scanning as we might see a later message with the same ID,
                idx_empty = i;
            }
        } else if (tx_id == id && !(flags & CAN_MSG_FLAG_UNORDERED)) {
            // Can't queue a second message with the same ID and guarantee order

            // (Undocumented hardware limitation - CANFD reference suggests
            // messages with the same ID are sent in buffer index order but
            // testing shows not always the case at least on STM32H7! Unsure if
            // also a limitation of bxCAN or STM32G4, but these only have 3 TX
            // buffers so inserting in buffer index order is likely to run out
            // of buffers relatively quickly anyway...)

            // Note: currently the driver considers a Standard and an Extended
            // ID with the same numeric value to be the same ID... could fix
            // this, although it's a relatively uncommon case.
            return -1;
        }
    }

    if (idx_empty == -1) {
        // No space in transmit queue
        return -1;
    }

    CanTxMsgTypeDef tx = {
        #if MICROPY_HW_ENABLE_FDCAN
        .MessageMarker = 0,
        .ErrorStateIndicator = FDCAN_ESI_ACTIVE,
        .TxEventFifoControl = FDCAN_NO_TX_EVENTS,
        .Identifier = id, // Range checked by caller
        .IdType = (flags & CAN_MSG_FLAG_EXT_ID) ? FDCAN_EXTENDED_ID : FDCAN_STANDARD_ID,
        .TxFrameType = (flags & CAN_MSG_FLAG_RTR) ? FDCAN_REMOTE_FRAME : FDCAN_DATA_FRAME,
        .FDFormat = (flags & CAN_MSG_FLAG_FD_F) ? FDCAN_FD_CAN : FDCAN_CLASSIC_CAN,
        .BitRateSwitch = (flags & CAN_MSG_FLAG_BRS) ? FDCAN_BRS_ON : FDCAN_BRS_OFF,
        .DataLength = data_len, // Converted inside can_transmit_buf_index
        #else // Classic
        .StdId = (flags & CAN_MSG_FLAG_EXT_ID) ? 0 : id,
        .ExtId = (flags & CAN_MSG_FLAG_EXT_ID) ? id : 0,
        .IDE = (flags & CAN_MSG_FLAG_EXT_ID) ? CAN_ID_EXT : CAN_ID_STD,
        .RTR = (flags & CAN_MSG_FLAG_RTR) ? CAN_RTR_REMOTE : CAN_RTR_DATA,
        .DLC = data_len,
        #endif
    };
    #if !MICROPY_HW_ENABLE_FDCAN
    assert(data_len <= sizeof(tx.Data)); // Also checked by caller
    memcpy(tx.Data, data, data_len);
    #endif

    HAL_StatusTypeDef err = can_transmit_buf_index(&self->port->h, idx_empty, &tx, data);
    if (err != HAL_OK) {
        return -1;
    }
    self->port->tx[idx_empty] = id;

    return idx_empty;
}

static bool machine_can_port_cancel_send(machine_can_obj_t *self, mp_uint_t idx) {
    return can_cancel_transmit(&self->port->h, idx);
}

// Decode a received frame header into the port-agnostic id/flags/len fields.
// CanRxMsgTypeDef is different for Classic CAN vs FDCAN.
static void can_decode_rx_msg(const CanRxMsgTypeDef *msg, mp_uint_t *id, mp_uint_t *flags, size_t *dlen) {
    #if MICROPY_HW_ENABLE_FDCAN
    *flags = ((msg->IdType == FDCAN_EXTENDED_ID) ? CAN_MSG_FLAG_EXT_ID : 0) |
        ((msg->RxFrameType == FDCAN_REMOTE_FRAME) ? CAN_MSG_FLAG_RTR : 0);
    *id = msg->Identifier;
    *dlen = msg->DataLength; // Lower layer has converted to bytes already
    #else
    *flags = (msg->IDE ? CAN_MSG_FLAG_EXT_ID : 0) |
        (msg->RTR ? CAN_MSG_FLAG_RTR : 0);
    *id = msg->IDE ? msg->ExtId : msg->StdId;
    *dlen = msg->DLC;
    #endif
}

// Drain both hardware RX FIFOs into the software receive ring, stopping once a
// FIFO is empty or the ring is full. Called from the receive interrupt handler
// (to move frames out of the 3-deep hardware FIFO before it overflows) and
// from machine_can_port_recv() (to refill the ring after a consumer pop frees
// space). Never allocates.
//
// Each iteration checks can_is_rx_pending() before calling can_receive(), so
// can_receive() is only ever called when a frame is already known to be
// waiting. This is required for correctness, not just an optimisation:
// can_receive(..., timeout_ms=0) on an empty FIFO enters a wait loop that
// calls mp_event_wait_ms(), which runs the scheduler, on ports using the
// classic bxCAN driver (can.c); doing that from this function's ISR caller
// would run arbitrary Python from inside a hardware interrupt. The FDCAN
// driver (fdcan.c) does not have this problem, but the pending check avoids
// relying on that difference between the two lower layers.
//
// A FIFO whose interrupt is left masked here (because the ring filled up
// while frames were still pending) is re-armed on the next call, once
// machine_can_port_recv() has popped an entry.
static void can_rx_ring_fill(machine_can_obj_t *self) {
    struct machine_can_port *port = self->port;
    CAN_HandleTypeDef *can = &port->h;

    for (can_rx_fifo_t fifo = CAN_RX_FIFO0; fifo <= CAN_RX_FIFO1; fifo++) {
        // A RingIO sink takes the frame straight from the interrupt, so the
        // application never makes a call per frame to collect it.
        if (self->rxring != NULL) {
            // Always taken off the controller, whether or not it fits: stopping
            // here with frames still pending would leave this interrupt masked
            // below, and nothing on this path calls back into the driver to
            // re-arm it, so reception would stop for good once the ring filled.
            while (can_is_rx_pending(can, fifo)) {
                uint8_t rec[MACHINE_CAN_RX_RECORD_SIZE];
                CanRxMsgTypeDef msg;
                int res = can_receive(can, fifo, &msg, &rec[MACHINE_CAN_RX_RECORD_HEADER], 0);
                assert(res == 0);
                (void)res;
                mp_uint_t id;
                mp_uint_t flags;
                size_t len;
                can_decode_rx_msg(&msg, &id, &flags, &len);
                rec[0] = id & 0xff;
                rec[1] = (id >> 8) & 0xff;
                rec[2] = (id >> 16) & 0xff;
                rec[3] = (id >> 24) & 0xff;
                rec[4] = flags & 0xff;
                rec[5] = (flags >> 8) & 0xff;
                rec[6] = (uint8_t)len;
                // Frames lost because the ring was full are reported on the
                // next one that fits, which is how a reader learns of them
                // without a second channel to ask over.
                rec[7] = port->rxring_lost ? 1 : 0;
                memset(&rec[MACHINE_CAN_RX_RECORD_HEADER + len], 0, MP_CAN_MAX_LEN - len);
                // Whole record or nothing: put_bytes refuses rather than
                // writing part of one, so the reader cannot desynchronise.
                if (ringbuf_put_bytes(self->rxring, rec, MACHINE_CAN_RX_RECORD_SIZE) == 0) {
                    port->rxring_lost = false;
                    port->rxring_head++;
                } else {
                    port->rxring_lost = true;
                    port->rxring_dropped++;
                }
            }
            if (!can_is_rx_pending(can, fifo)) {
                can_enable_rx_interrupts(can, fifo, true);
            }
            continue;
        }
        while (port->rx_ring_head - port->rx_ring_tail < port->rx_ring_len
               && can_is_rx_pending(can, fifo)) {
            can_rx_ring_entry_t *entry = &port->rx_ring[port->rx_ring_head % port->rx_ring_len];
            CanRxMsgTypeDef msg;
            int res = can_receive(can, fifo, &msg, entry->data, 0);
            assert(res == 0); // A frame is known to be pending, so this cannot time out
            (void)res;
            mp_uint_t flags;
            size_t len;
            can_decode_rx_msg(&msg, &entry->id, &flags, &len);
            entry->flags = (uint16_t)flags;
            entry->len = (uint8_t)len;
            port->rx_ring_head++;
        }
        if (!can_is_rx_pending(can, fifo)) {
            // Re-enable the interrupt this function itself depends on: it is
            // only ever called with the ring active, so this is unconditional
            // regardless of whether the user also wants IRQ_RX callbacks.
            can_enable_rx_interrupts(can, fifo, true);
        }
    }
}

static bool machine_can_port_recv(machine_can_obj_t *self, void *data, size_t *dlen, mp_uint_t *id, mp_uint_t *flags, mp_uint_t *errors) {
    struct machine_can_port *port = self->port;

    if (port->rx_ring_len == 0) {
        // No software ring requested: read directly from the hardware FIFO.
        CAN_HandleTypeDef *can = &port->h;
        CanRxMsgTypeDef msg;

        for (can_rx_fifo_t fifo = CAN_RX_FIFO0; fifo <= CAN_RX_FIFO1; fifo++) {
            if (can_receive(can, fifo, &msg, data, 0) == 0) {
                can_decode_rx_msg(&msg, id, flags, dlen);

                *errors = self->rx_error_flags;
                self->rx_error_flags = 0;

                // Re-enable any interrupts that were disabled in RX IRQ handlers
                can_enable_rx_interrupts(can, fifo, self->mp_irq_trigger & MP_CAN_IRQ_RX);

                return true;
            }
        }
        return false;
    }

    // machine_can_irq_handler() also advances rx_ring_head, from the CAN
    // controller's interrupt, and can otherwise preempt this function at any
    // point since it runs from ordinary (non-ISR) context. Raising the IRQ
    // priority ceiling for the pop and the refill excludes that interrupt for
    // the duration, so head, tail and the popped entry are each touched by
    // one context at a time.
    uint32_t basepri = raise_irq_pri(IRQ_PRI_CAN);

    if (port->rx_ring_head == port->rx_ring_tail) {
        restore_irq_pri(basepri);
        return false; // Ring empty
    }

    can_rx_ring_entry_t *entry = &port->rx_ring[port->rx_ring_tail % port->rx_ring_len];
    *id = entry->id;
    *flags = entry->flags;
    *dlen = entry->len;
    memcpy(data, entry->data, entry->len);
    port->rx_ring_tail++;

    // Popping an entry frees ring space: refill from the hardware FIFOs and
    // re-enable any receive interrupts that were masked because the ring was full.
    can_rx_ring_fill(self);

    restore_irq_pri(basepri);

    *errors = self->rx_error_flags;
    self->rx_error_flags = 0;

    return true;
}

static void machine_can_update_irqs(machine_can_obj_t *self) {
    uint16_t triggers = self->mp_irq_trigger;

    // The receive interrupt also drives the software ring (can_rx_ring_fill()),
    // so it stays enabled whenever the ring is active even if the user has no
    // IRQ_RX callback registered.
    bool want_rx_notify = (triggers & MP_CAN_IRQ_RX) || can_rx_sink_active(self);

    for (can_rx_fifo_t fifo = CAN_RX_FIFO0; fifo <= CAN_RX_FIFO1; fifo++) {
        if (want_rx_notify) {
            can_enable_rx_interrupts(&self->port->h, fifo, true);
        } else {
            can_disable_rx_interrupts(&self->port->h, fifo);
        }
    }

    // Note: TX complete interrupt is always enabled to manage the internal queue state
}

static void machine_can_port_clear_filters(machine_can_obj_t *self) {
    #if MICROPY_HW_ENABLE_FDCAN
    for (int f = 0; f < CAN_HW_MAX_STD_FILTER; f++) {
        can_clearfilter(&self->port->h, f, false);
    }
    for (int f = 0; f < CAN_HW_MAX_EXT_FILTER; f++) {
        can_clearfilter(&self->port->h, f, true);
    }
    #else
    int bank_offs = (self->can_idx == 1) ? CAN_HW_MAX_FILTER : 0; // CAN2 filters index after CAN1
    for (int f = 0; f < CAN_HW_MAX_FILTER; f++) {
        can_clearfilter(&self->port->h, f + bank_offs, CAN_HW_MAX_FILTER);
    }
    #endif
}

#if MICROPY_HW_ENABLE_FDCAN
static void machine_can_port_set_filter(machine_can_obj_t *self, int filter_idx, mp_uint_t can_id, mp_uint_t mask, mp_uint_t flags) {
    int max_idx = (flags & CAN_MSG_FLAG_EXT_ID) ? CAN_HW_MAX_EXT_FILTER : CAN_HW_MAX_STD_FILTER;
    if (filter_idx >= max_idx) {
        mp_raise_ValueError(MP_ERROR_TEXT("too many filters for this ID type"));
    }
    if (flags & ~CAN_MSG_FLAG_EXT_ID) {
        mp_raise_ValueError(MP_ERROR_TEXT("flags")); // Only supported flag is for extended ID
    }

    FDCAN_FilterTypeDef filter = {
        .IdType = (flags & CAN_MSG_FLAG_EXT_ID) ? FDCAN_EXTENDED_ID : FDCAN_STANDARD_ID,
        // FDCAN counts standard and extended id filters separately, but this is
        // already accounted for in filter_idx due to CAN_FILTERS_STD_EXT_SEPARATE.
        .FilterIndex = filter_idx,
        .FilterType = FDCAN_FILTER_MASK,
        // Round-robin between FIFO1 and FIFO0
        .FilterConfig = (filter_idx & 1) ? FDCAN_FILTER_TO_RXFIFO1 : FDCAN_FILTER_TO_RXFIFO0,
        .FilterID1 = can_id,
        .FilterID2 = mask,
    };

    int r = HAL_FDCAN_ConfigFilter(&self->port->h, &filter);
    assert(r == HAL_OK);
    (void)r;
}
#else
static void machine_can_port_set_filter(machine_can_obj_t *self, int filter_idx, mp_uint_t can_id, mp_uint_t mask, mp_uint_t flags) {
    if (filter_idx >= CAN_HW_MAX_FILTER) {
        mp_raise_ValueError(MP_ERROR_TEXT("too many filters"));
    }
    if (flags & ~CAN_MSG_FLAG_EXT_ID) {
        mp_raise_ValueError(MP_ERROR_TEXT("flags")); // Only supported flag is for extended ID
    }

    if (self->can_idx == 1) {
        filter_idx += CAN_HW_MAX_FILTER;  // CAN2 filters index after CAN1
    }

    CAN_FilterConfTypeDef filter = {
        .FilterActivation = ENABLE,
        .FilterScale = CAN_FILTERSCALE_32BIT,
        .FilterMode = CAN_FILTERMODE_IDMASK,
        .FilterNumber = filter_idx,
        // Apply the filters round-robin to each FIFO, as each filter in bxCAN is
        // associated with only one FIFO.
        .FilterFIFOAssignment = filter_idx % 2,
        .BankNumber = CAN_HW_MAX_FILTER, // Assign same number of filters to CAN2 as CAN1
    };

    // This somewhat corresponds to STM32 RM Figure 342 "Filter bank scale
    // configuration", although the Reference Manual makes 32-bit mask filters look
    // a lot more complex than they are, then the ST HAL makes it even more
    // complex by only supporting filter configuration via 16-bit halfwords
    // which are re-assembled to full words inside the HAL...
    if (flags & CAN_MSG_FLAG_EXT_ID) {
        filter.FilterIdLow = (can_id << 3) | CAN_ID_EXT;
        filter.FilterIdHigh = can_id >> 13;
        filter.FilterMaskIdLow = (mask << 3) | CAN_ID_EXT;
        filter.FilterMaskIdHigh = mask >> 13;
    } else {
        filter.FilterIdLow = 0;
        filter.FilterIdHigh = can_id << 5;
        filter.FilterMaskIdLow = CAN_ID_EXT; // Set to require CAN_ID_EXT unset in message
        filter.FilterMaskIdHigh = mask << 5;
    }

    int r = HAL_CAN_ConfigFilter(&self->port->h, &filter);
    assert(r == HAL_OK); // Params should be verified before passing to HAL
    (void)r;
}
#endif // MICROPY_HW_ENABLE_FDCAN

// Empty function here.
static void machine_can_port_set_filter_done(machine_can_obj_t *self) {
}

static machine_can_state_t machine_can_port_get_state(machine_can_obj_t *self) {
    // machine_can_port.h defines MP_CAN_STATE_xxx enums, verify they all match
    // numerically with stm32 can.h CAN_STATE_xxx enums
    MP_STATIC_ASSERT((int)MP_CAN_STATE_STOPPED == (int)CAN_STATE_STOPPED);
    MP_STATIC_ASSERT((int)MP_CAN_STATE_ACTIVE == (int)CAN_STATE_ERROR_ACTIVE);
    MP_STATIC_ASSERT((int)MP_CAN_STATE_WARNING == (int)CAN_STATE_ERROR_WARNING);
    MP_STATIC_ASSERT((int)MP_CAN_STATE_PASSIVE == (int)CAN_STATE_ERROR_PASSIVE);
    MP_STATIC_ASSERT((int)MP_CAN_STATE_BUS_OFF == (int)CAN_STATE_BUS_OFF);
    return (machine_can_state_t)can_get_state(&self->port->h);
}

static void machine_can_port_update_counters(machine_can_obj_t *self) {
    can_counters_t hw_counters;
    struct machine_can_port *port = self->port;
    machine_can_counters_t *counters = &self->counters;

    can_get_counters(&port->h, &hw_counters);

    counters->tec = hw_counters.tec;
    counters->rec = hw_counters.rec;
    counters->tx_pending = hw_counters.tx_pending;
    counters->rx_pending = hw_counters.rx_fifo0_pending + hw_counters.rx_fifo1_pending;
    if (port->rx_ring_len > 0) {
        // Include frames already moved out of the hardware FIFOs and into the
        // software receive ring, so rx_pending still reflects the total number
        // of frames waiting to be read by recv().
        counters->rx_pending += port->rx_ring_head - port->rx_ring_tail;
    }

    // Other fields in 'counters' are updated from ISR directly
}

static mp_obj_t machine_can_port_get_additional_timings(machine_can_obj_t *self, mp_obj_t optional_arg) {
    return mp_const_none;
}

static void machine_can_port_restart(machine_can_obj_t *self) {
    // extmod layer has already checked CAN is initialised
    struct machine_can_port *port = self->port;
    machine_can_port_cancel_all_tx(self);
    can_restart(&port->h);
    port->irq_state_pending = false;

    // Masked against machine_can_irq_handler(), which also writes rx_ring_head
    // via can_rx_ring_fill(): otherwise a frame delivered by the controller
    // between these two writes could be counted as pending against a ring
    // that this function is resetting to empty.
    uint32_t basepri = raise_irq_pri(IRQ_PRI_CAN);
    port->rx_ring_head = 0;
    port->rx_ring_tail = 0;
    restore_irq_pri(basepri);
}

static bool clear_complete_transfer(machine_can_obj_t *self, int *index, bool *is_success) {
    *index = can_get_transmit_finished(&self->port->h, is_success);
    if (*index == -1) {
        return false;
    }
    self->port->tx[*index] = TX_EMPTY;

    return true;
}

static mp_uint_t machine_can_port_irq_flags(machine_can_obj_t *self) {
    mp_uint_t flags = 0;
    CAN_HandleTypeDef *can = &self->port->h;

    if (self->mp_irq_trigger & MP_CAN_IRQ_STATE && self->port->irq_state_pending) {
        flags |= MP_CAN_IRQ_STATE;
        self->port->irq_state_pending = false;
    }

    // Check for RX
    if (self->mp_irq_trigger & MP_CAN_IRQ_RX) {
        if (self->port->rx_ring_len > 0) {
            // Frames already moved into the software ring no longer show up as
            // pending in the hardware FIFOs, so check the ring instead.
            if (self->port->rx_ring_head != self->port->rx_ring_tail) {
                flags |= MP_CAN_IRQ_RX;
            }
        } else {
            for (can_rx_fifo_t fifo = CAN_RX_FIFO0; fifo <= CAN_RX_FIFO1; fifo++) {
                if (can_is_rx_pending(can, fifo)) {
                    flags |= MP_CAN_IRQ_RX;
                }
            }
        }
    }

    // Check for TX done
    if (self->mp_irq_trigger & MP_CAN_IRQ_TX) {
        bool is_success = false;
        int index;
        if (clear_complete_transfer(self, &index, &is_success)) {
            flags |= (mp_uint_t)(index << MP_CAN_IRQ_IDX_SHIFT) | MP_CAN_IRQ_TX;
            if (!is_success) {
                flags |= MP_CAN_IRQ_TX_FAILED;
            }
        }
    }

    return flags;
}

void machine_can_irq_handler(uint can_id,  can_int_t interrupt) {
    assert(can_id > 0);
    machine_can_obj_t *self = MP_STATE_PORT(machine_can_objs)[can_id - 1];
    if (self == NULL || self->port == NULL) {
        // Either pyb.CAN has enabled the interrupt with no machine.CAN ever
        // constructed on this peripheral, or a machine.CAN that was
        // constructed has since been deinitialised. Either way, self->port
        // (dereferenced below for every case except CAN_INT_FIFO_FULL and
        // CAN_INT_FIFO_OVERFLOW) is not available.
        return;
    }
    struct machine_can_port *port = self->port;
    machine_can_counters_t *counters = &self->counters;
    bool call_irq = false;
    bool irq_state = false;

    switch (interrupt) {
        // RX
        case CAN_INT_FIFO_FULL:
            self->rx_error_flags |= CAN_RECV_ERR_FULL;
            break;
        case CAN_INT_FIFO_OVERFLOW:
            self->rx_error_flags |= CAN_RECV_ERR_OVERRUN;
            counters->rx_overruns++;
            break;
        case CAN_INT_MESSAGE_RECEIVED:
            if (can_rx_sink_active(self)) {
                // Drain the hardware FIFOs into the sink here, so the ISR
                // re-enables the RX interrupt itself instead of waiting for
                // Python to call recv(). Only schedule the .irq() callback on
                // the sink's empty-to-non-empty transition: further frames
                // arriving before the callback runs are coalesced into the
                // same wakeup.
                bool was_empty = (self->rxring != NULL)
                    ? (ringbuf_avail(self->rxring) == 0)
                    : (port->rx_ring_head == port->rx_ring_tail);
                can_rx_ring_fill(self);
                bool now_has = (self->rxring != NULL)
                    ? (ringbuf_avail(self->rxring) != 0)
                    : (port->rx_ring_head != port->rx_ring_tail);
                if (was_empty && now_has) {
                    call_irq = call_irq || (self->mp_irq_trigger & MP_CAN_IRQ_RX);
                }
            } else {
                call_irq = call_irq || (self->mp_irq_trigger & MP_CAN_IRQ_RX);
            }
            break;

        // Error states
        case CAN_INT_ERR_WARNING:
            if (!port->error_passive) {
                // Only count entering warning state, not leaving it
                counters->num_warning++;
                irq_state = true;
            }
            port->error_passive = false;
            break;
        case CAN_INT_ERR_PASSIVE:
            counters->num_passive++;
            port->error_passive = true;
            irq_state = true;
            break;
        case CAN_INT_ERR_BUS_OFF:
            counters->num_bus_off++;
            irq_state = true;
            port->error_passive = false;
            break;

        // TX
        case CAN_INT_TX_COMPLETE:
            if (!(self->mp_irq_trigger & MP_CAN_IRQ_TX)) {
                // No TX IRQ, so mark this buffer as free and move on
                int index;
                bool is_success = false;
                clear_complete_transfer(self, &index, &is_success);
            } else {
                // Otherwise, the slot is marked empty after the irq calls flags()
                call_irq = true;
            }
            break;

        default:
            assert(0); // Should be unreachable
    }

    if (irq_state && (self->mp_irq_trigger & MP_CAN_IRQ_STATE)) {
        self->port->irq_state_pending = true;
        call_irq = true;
    }

    if (call_irq) {
        assert(self->mp_irq_obj != NULL); // Can't set mp_irq_trigger otherwise
        mp_irq_handler(self->mp_irq_obj);
    }
}
