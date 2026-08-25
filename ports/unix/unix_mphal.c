/*
 * This file is part of the MicroPython project, http://micropython.org/
 *
 * The MIT License (MIT)
 *
 * Copyright (c) 2015 Damien P. George
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

#include <unistd.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <errno.h>
#include <sys/time.h>
#include <sys/select.h>
#include <fcntl.h>

#include "py/mphal.h"
#include "py/mpthread.h"
#include "py/runtime.h"
#include "extmod/misc.h"

#ifdef __linux__
#include <sys/eventfd.h>
// An eventfd is read and written as a 64-bit counter.
typedef uint64_t wake_event_token_t;
#else
// The self-pipe carries one byte per wakeup.
typedef uint8_t wake_event_token_t;
#endif

// The wake event: a Linux eventfd, or a self-pipe elsewhere.  Reading
// wake_event_fd drains it, writing wake_event_wr_fd raises it; on Linux both
// are the same descriptor, and both are negative when not initialised.
// Only exists where something waits on it; with per-thread wake objects every waiter has
// its own and this pair has no reader (see mp_hal_wake_event_init()).
#if !MICROPY_HAL_HAS_WAKE_OBJ
static int wake_event_fd = -1;
static int wake_event_wr_fd = -1;
#endif

#ifndef __linux__
static int set_cloexec_nonblock(int fd) {
    // pipe2() would do this atomically at creation but is absent on macOS.
    int flags = fcntl(fd, F_GETFL, 0);
    if (flags < 0 || fcntl(fd, F_SETFL, flags | O_NONBLOCK) != 0) {
        return -1;
    }
    return fcntl(fd, F_SETFD, FD_CLOEXEC);
}
#endif

#if defined(MICROPY_UNIX_COVERAGE)
// Test hook: while set, this port reports that it cannot create a wake primitive. It makes
// the degraded claim below reachable without exhausting the process's real descriptors,
// which a test cannot do without also breaking the harness around it.
bool mp_hal_wake_obj_force_open_failure;
#endif

// Opens one wake primitive: a Linux eventfd (rd == wr) or a self-pipe elsewhere. Returns
// false with the outputs untouched if none can be created, leaving the caller to decide
// what that failure means: fatal where it happens before any user code has run, degraded
// to a backing-less object otherwise (mp_hal_wake_obj_this_thread()).
static bool open_wake_fds(int *rd_out, int *wr_out) {
    int rd = -1;
    int wr = -1;
    #if defined(MICROPY_UNIX_COVERAGE)
    if (mp_hal_wake_obj_force_open_failure) {
        errno = EMFILE;
        return false;
    }
    #endif
    #ifdef __linux__
    rd = eventfd(0, EFD_CLOEXEC | EFD_NONBLOCK);
    wr = rd;
    #else
    int fds[2];
    if (pipe(fds) == 0) {
        if (set_cloexec_nonblock(fds[0]) == 0 && set_cloexec_nonblock(fds[1]) == 0) {
            rd = fds[0];
            wr = fds[1];
        } else {
            close(fds[0]);
            close(fds[1]);
        }
    }
    #endif
    if (rd < 0) {
        return false;
    }
    if (rd >= FD_SETSIZE) {
        // Waiting on it uses select(), and FD_SET() is undefined from here up. Closed
        // rather than kept, so a descriptor too high to wait on does not hold a slot that
        // would let a later, lower-numbered claim succeed.
        if (wr != rd) {
            close(wr);
        }
        close(rd);
        return false;
    }
    *rd_out = rd;
    *wr_out = wr;
    return true;
}

static void wake_event_drain(int fd) {
    // Empty it fully or the next wait returns at once: an eventfd reads its
    // counter in one go, a self-pipe holds a byte per raise.
    char buf[64];
    while (read(fd, buf, sizeof(buf)) > 0) {
    }
}

static void wake_fd_raise(int fd) {
    if (fd < 0) {
        return;
    }
    // Reachable from a signal handler, so no allocation, and errno is
    // preserved.  A failed write means the event is already raised.
    int errno_save = errno;
    wake_event_token_t token = 1;
    ssize_t ret = write(fd, &token, sizeof(token));
    (void)ret;
    errno = errno_save;
}

#if MICROPY_HAL_HAS_WAKE_OBJ

// A thread's claimed wake object. Nodes are allocated on demand by
// mp_hal_wake_obj_this_thread() and never freed or unlinked: they form an append-only
// list so mp_hal_wake_obj_signal_all() can walk it from a signal handler, with no lock
// and nothing that could vanish or move underneath it. Thread exit only clears .claimed
// (mp_hal_wake_obj_release_this_thread()), returning the node for the next thread to
// claim rather than closing its descriptors; see mp_hal_wake_event_deinit() for where
// they are actually closed.
struct _mp_hal_wake_obj_t {
    int rd_fd;
    int wr_fd; // the same descriptor as rd_fd for an eventfd
    volatile bool claimed;
    mp_hal_wake_obj_t *next;
};

// Head of the append-only wake object list. A push publishes a fully-initialised node
// with a release store here, and every read, whether walking from the head to claim a
// returned node or to broadcast a raise, pairs it with an acquire load: the object is
// reachable from another thread or a signal handler with no mutex in between (a handler
// cannot take one), so the ordering has to come from this variable itself on every hop,
// not from incidentally being inside the atomic section below.
static mp_hal_wake_obj_t *wake_obj_list_head;

// This thread's claimed node, cached after the first claim so every later wait finds it
// with no atomic section and no scan.
static __thread mp_hal_wake_obj_t *tls_wake_obj;

// Handed out when a claim cannot be serviced, so mp_hal_wake_obj_this_thread() keeps its
// promise of a valid object without one having been created. Static, because the failures
// it stands in for are exactly no memory and no descriptors. Never linked into the list
// and never claimed, so mp_hal_wake_obj_signal_all() cannot reach it and no other thread
// can be handed it. Its negative descriptors make every operation on it degrade through
// code that already exists: raising hits wake_fd_raise()'s fd < 0 guard, a negative fd in
// a pollfd is a POSIX-defined no-op with revents zeroed, and mp_hal_wake_event_wait_tv()
// already falls through to its untimed wait when the descriptor is negative.
static mp_hal_wake_obj_t wake_obj_degraded = {
    .rd_fd = -1,
    .wr_fd = -1,
    .claimed = false,
    .next = NULL,
};

#endif

void mp_hal_wake_event_init(void) {
    #if !MICROPY_HAL_HAS_WAKE_OBJ
    // Only opened where something waits on it. With per-thread wake objects every waiter
    // has its own, so both the raise in mp_hal_signal_event() and the wait in
    // mp_hal_wake_event_wait_tv() take their wake-object branch and this descriptor would
    // sit open for the life of the process with no reader.
    if (!open_wake_fds(&wake_event_fd, &wake_event_wr_fd)) {
        fprintf(stderr, "FATAL: cannot create wake event: %s\n", strerror(errno));
        exit(1);
    }
    #else
    // Claims the main thread's wake object here, where no user code has run and so no file
    // descriptor of the caller's choosing has ever existed. That property is categorical:
    // the number this object is handed cannot be one a poll set still holds a stale
    // registration for, because there has been nothing to register. Claimed after user
    // code starts, it gets whatever number the kernel hands out then, which can be one a
    // closed-but-still-registered fd used to hold, and poll() resolves that stale entry
    // against this object instead of reporting POLLNVAL for it.
    //
    // Only the main thread can have that property, so every other thread claims on its
    // first blocking wait instead. Claiming earlier in such a thread's life is
    // monotonically safer, fewer intervening closes meaning fewer numbers to collide
    // with, but only marginally: the descriptor churn that creates the hazard happens
    // before the thread exists, so an earlier claim buys a smaller window and never the
    // guarantee. The price would be an eventfd per thread whether or not it ever blocks,
    // each one bounded by FD_SETSIZE because mp_hal_wake_event_wait_tv() waits with
    // select().
    //
    // Fatal here, where a claim elsewhere degrades: this runs before any user code, so
    // there is no caller to report to and no timeout to fall back on, and a main thread
    // that cannot be woken makes the rest of the process's behaviour undefined rather
    // than merely late.
    if (mp_hal_wake_obj_this_thread() == &wake_obj_degraded) {
        fprintf(stderr, "FATAL: cannot create wake event: %s\n", strerror(errno));
        exit(1);
    }
    #endif
}

void mp_hal_wake_event_deinit(void) {
    #if !MICROPY_HAL_HAS_WAKE_OBJ
    // Cleared before closing, so a concurrent raise is dropped rather than
    // written to a reused descriptor.
    int rd = wake_event_fd;
    int wr = wake_event_wr_fd;
    wake_event_fd = -1;
    wake_event_wr_fd = -1;
    if (wr != rd) {
        close(wr);
    }
    close(rd);
    #else
    // The only place a wake object's descriptors are closed: a thread that claimed one
    // never closes it at exit, only returns it (mp_hal_wake_obj_release_this_thread()),
    // so every node ever pushed is still open right up to this walk. A raise racing this
    // teardown is the same clear-before-close race accepted for the shared event above,
    // occurring at most once for the whole process rather than once per thread exit,
    // since nothing exits a thread past this point.
    for (mp_hal_wake_obj_t *w = __atomic_load_n(&wake_obj_list_head, __ATOMIC_ACQUIRE); w != NULL; w = w->next) {
        w->claimed = false;
        int rd = w->rd_fd;
        int wr = w->wr_fd;
        w->rd_fd = -1;
        w->wr_fd = -1;
        if (wr != rd) {
            close(wr);
        }
        close(rd);
    }
    #endif
}

void mp_hal_signal_event(void) {
    #if MICROPY_HAL_HAS_WAKE_OBJ
    // Every thread that has claimed a wake object needs this: it may carry a scheduled
    // exception or other queued work that thread must act on, and it may be the only
    // notice a SOURCE_SIGNAL entry that thread is polling gets. No separate raise to the
    // shared wake event: every thread with a stake in one claims its own object instead
    // (mp_hal_wake_event_wait_tv()), so that descriptor has no reader left to wake here.
    // See mp_hal_wake_obj_signal_all()'s own doc comment for why this is safe to call
    // unconditionally from anywhere this function itself may be called.
    mp_hal_wake_obj_signal_all();
    #else
    // No per-thread objects on this build (MICROPY_PY_THREAD is off): the shared wake
    // event is this process's only thread, so it is also that thread's only consumer,
    // and remains the mechanism.
    wake_fd_raise(wake_event_wr_fd);
    #endif
}

#if MICROPY_HAL_HAS_WAKE_OBJ

mp_hal_wake_obj_t *mp_hal_wake_obj_this_thread(void) {
    if (tls_wake_obj != NULL) {
        return tls_wake_obj;
    }
    // Claiming happens once per thread, never once per wait, so this atomic section is
    // never on the poll loop's hot path. The main thread claims from
    // mp_hal_wake_event_init(); every other thread reaches here on its first blocking
    // wait. It serialises this scan and push against every other claim, release and push
    // (writer-versus-writer), but a raise reaches this list from a signal handler that
    // cannot take it, which is why the head pointer itself still goes through an explicit
    // acquire/release below rather than relying on the section's mutex for that.
    mp_uint_t atomic_state = MICROPY_BEGIN_ATOMIC_SECTION();
    mp_hal_wake_obj_t *w = NULL;
    for (mp_hal_wake_obj_t *n = __atomic_load_n(&wake_obj_list_head, __ATOMIC_ACQUIRE); n != NULL; n = n->next) {
        if (!n->claimed) {
            n->claimed = true;
            w = n;
            break;
        }
    }
    if (w == NULL) {
        // No returned node to reuse: allocate one. Never freed and never unlinked, so
        // storage grows to the peak number of concurrently-blocking threads and stops
        // there; a thread that later exits returns its node instead of shrinking this.
        w = malloc(sizeof(mp_hal_wake_obj_t));
        if (w == NULL || !open_wake_fds(&w->rd_fd, &w->wr_fd)) {
            free(w);
            MICROPY_END_ATOMIC_SECTION(atomic_state);
            // Degraded rather than fatal, because what the caller loses depends on what it
            // is waiting for and only one case cannot absorb it. A wait with a timeout
            // still returns on that timeout, and a thread whose only stake is running
            // scheduled callbacks still runs them, late, when its own wait expires. The
            // one case with nothing left to end it is an indefinite wait a SOURCE_SIGNAL
            // entry was to have ended, and extmod/modselect.c raises there rather than
            // blocking forever.
            //
            // Not cached in tls_wake_obj: a claim that failed on a transient shortage
            // succeeds once the shortage passes, and a thread that kept the degraded
            // object would stay degraded for its whole life instead. The retry costs one
            // scan and one failed allocation per wait, and only while degraded.
            return &wake_obj_degraded;
        }
        w->claimed = true;
        w->next = __atomic_load_n(&wake_obj_list_head, __ATOMIC_ACQUIRE);
        __atomic_store_n(&wake_obj_list_head, w, __ATOMIC_RELEASE);
    }
    // Latch it before returning, whether this claim just reused a returned node or
    // published a freshly allocated one: a raise landing between the claim taking effect
    // and this thread's first wait would otherwise find a claimed object with nothing
    // written to it, and be lost, since mp_hal_wake_obj_signal_all() only ever writes to
    // what it finds already claimed. For a freshly allocated node this also closes the
    // narrower window between the node existing and its claim becoming visible from
    // wake_obj_list_head. Seeding costs one spurious wake on this thread's first block,
    // absorbed by its condition recheck, rather than widening every future raise into a
    // walk-and-write-all to close this window on every claim.
    wake_fd_raise(w->wr_fd);
    MICROPY_END_ATOMIC_SECTION(atomic_state);
    tls_wake_obj = w;
    return w;
}

void mp_hal_wake_obj_release_this_thread(void) {
    if (tls_wake_obj == NULL) {
        return;
    }
    mp_uint_t atomic_state = MICROPY_BEGIN_ATOMIC_SECTION();
    tls_wake_obj->claimed = false;
    MICROPY_END_ATOMIC_SECTION(atomic_state);
    tls_wake_obj = NULL;
}

void mp_hal_wake_obj_drain(mp_hal_wake_obj_t *w) {
    int fd = w->rd_fd;
    if (fd >= 0) {
        wake_event_drain(fd);
    }
}

// Raises every currently claimed object. Called from mp_hal_signal_event(), so it must be
// exactly as safe to call as that already is: reachable from a hard ISR, a second core, a
// non-MicroPython RTOS task or a POSIX signal handler. It allocates nothing, takes no
// lock and no atomic section (mutation of .claimed is confined to claim and release,
// neither of which this function performs), and dereferences no caller-supplied pointer.
// The list itself is safe to walk unlocked because it is append-only: nothing is ever
// unlinked, and the acquire load pairs with the release store that publishes each node
// (mp_hal_wake_obj_this_thread()), so every field of a node this walk reaches is the
// value its owning thread set before publishing it, never a partial write in progress.
// errno is saved and restored once for the whole walk rather than once per write(),
// since every write() shares the same failure mode (the event is already raised) and
// none of them needs to be told apart from the others.
void mp_hal_wake_obj_signal_all(void) {
    int errno_save = errno;
    for (mp_hal_wake_obj_t *w = __atomic_load_n(&wake_obj_list_head, __ATOMIC_ACQUIRE); w != NULL; w = w->next) {
        if (!w->claimed) {
            continue;
        }
        wake_fd_raise(w->wr_fd);
    }
    errno = errno_save;
}

#if MICROPY_HAL_WAKE_OBJ_HAS_POSIX_FD
int mp_hal_wake_obj_posix_fd(mp_hal_wake_obj_t *w) {
    // Re-read for the same reason mp_hal_wake_event_fd() does: a thread outliving
    // mp_hal_wake_event_deinit() can reach this with no descriptor to give.
    return w->rd_fd;
}
#endif

#endif // MICROPY_HAL_HAS_WAKE_OBJ

int mp_hal_wake_event_wait_tv(struct timeval *tv) {
    // Only the thread entitled to run scheduled callbacks has a stake in a live wake
    // source here: draining it is how that thread learns queued work exists, so nothing
    // else may claim credit for the same raise. A sleeper never registers a poll entry,
    // so no other thread's wait depends on this call being woken early either; reaching
    // this point with no such entitlement, a thread falls through to the bare select()
    // below and runs for its full duration regardless of an unrelated raise, even though
    // that raise reaches every *claimed* wake object (mp_hal_wake_obj_signal_all());
    // such a thread simply never claims one here.
    #if MICROPY_HAL_HAS_WAKE_OBJ
    if (mp_sched_thread_can_run_callbacks()) {
        mp_hal_wake_obj_t *w = mp_hal_wake_obj_this_thread();
        int fd = w->rd_fd;
        if (fd >= 0) {
            fd_set rfds;
            FD_ZERO(&rfds);
            FD_SET(fd, &rfds);
            int ret = select(fd + 1, &rfds, NULL, NULL, tv);
            if (ret > 0) {
                mp_hal_wake_obj_drain(w);
                errno = EINTR;
                return -1;
            }
            return ret;
        }
    }
    #else
    // No per-thread objects on this build: the shared wake event is this process's only
    // thread, so it is also that thread's only consumer.
    if (mp_sched_thread_can_run_callbacks() && wake_event_fd >= 0) {
        int fd = wake_event_fd;
        fd_set rfds;
        FD_ZERO(&rfds);
        FD_SET(fd, &rfds);
        int ret = select(fd + 1, &rfds, NULL, NULL, tv);
        if (ret > 0) {
            wake_event_drain(fd);
            errno = EINTR;
            return -1;
        }
        return ret;
    }
    #endif
    // Reached with no live wake source to wait on, either because this thread has no
    // entitlement to one or because its claim could not be serviced and it holds the
    // backing-less object. Waits out `tv` and cannot be woken early in either case.
    //
    // A caller passing NULL therefore waits forever. Every caller on this port passes a
    // finite `tv`: MP_HAL_WAKE_EVENT_FOREVER reaches here only via
    // mp_event_wait_indefinite(), which on unix is used by btstack rather than by
    // time.sleep() or by select.poll(), the latter running its own poll() set and never
    // reaching this function. That is a pre-existing property of the untimed wait rather
    // than something the degraded object introduces, and it is fixed in the waiter.
    return select(0, NULL, NULL, NULL, tv);
}

#if !MICROPY_HAL_HAS_WAKE_OBJ
// The shared event's descriptor, for a poll set to sleep on alongside its own entries.
// Only where per-thread wake objects are unavailable: with them, extmod/modselect.c
// injects this thread's own object instead and these have no caller.
int mp_hal_wake_event_fd(void) {
    // Re-read for the same reason the wait does: a thread outliving the main one
    // can reach this after deinitialisation, when there is no descriptor to give.
    return wake_event_fd;
}

void mp_hal_wake_event_drain(void) {
    int fd = wake_event_fd;
    if (fd >= 0) {
        wake_event_drain(fd);
    }
}
#endif

void mp_hal_wake_event_wait_ms(mp_uint_t timeout_ms) {
    if (timeout_ms == MP_HAL_WAKE_EVENT_FOREVER) {
        mp_hal_wake_event_wait_tv(NULL);
        return;
    }
    struct timeval tv = {timeout_ms / 1000, (timeout_ms % 1000) * 1000};
    mp_hal_wake_event_wait_tv(&tv);
}

#if defined(__GLIBC__) && defined(__GLIBC_PREREQ)
#if __GLIBC_PREREQ(2, 25)
#include <sys/random.h>
#define _HAVE_GETRANDOM
#endif
#endif

#ifndef _WIN32
#include <signal.h>

static void sighandler(int signum) {
    if (signum == SIGINT) {
        #if MICROPY_ASYNC_KBD_INTR
        #if MICROPY_PY_THREAD_GIL
        // Since signals can occur at any time, we may not be holding the GIL when
        // this callback is called, so it is not safe to raise an exception here
        #error "MICROPY_ASYNC_KBD_INTR and MICROPY_PY_THREAD_GIL are not compatible"
        #endif
        mp_obj_exception_clear_traceback(MP_OBJ_FROM_PTR(&MP_STATE_VM(mp_kbd_exception)));
        sigset_t mask;
        sigemptyset(&mask);
        // On entry to handler, its signal is blocked, and unblocked on
        // normal exit. As we instead perform longjmp, unblock it manually.
        sigprocmask(SIG_SETMASK, &mask, NULL);
        nlr_raise(MP_OBJ_FROM_PTR(&MP_STATE_VM(mp_kbd_exception)));
        #else
        if (MP_STATE_MAIN_THREAD(mp_pending_exception) == MP_OBJ_FROM_PTR(&MP_STATE_VM(mp_kbd_exception))) {
            // this is the second time we are called, so die straight away
            exit(1);
        }
        mp_sched_keyboard_interrupt();
        #endif
    }
}
#endif

void mp_hal_set_interrupt_char(char c) {
    // configure terminal settings to (not) let ctrl-C through
    if (c == CHAR_CTRL_C) {
        #ifndef _WIN32
        // enable signal handler
        struct sigaction sa;
        sa.sa_flags = 0;
        sa.sa_handler = sighandler;
        sigemptyset(&sa.sa_mask);
        sigaction(SIGINT, &sa, NULL);
        #endif
    } else {
        #ifndef _WIN32
        // disable signal handler
        struct sigaction sa;
        sa.sa_flags = 0;
        sa.sa_handler = SIG_DFL;
        sigemptyset(&sa.sa_mask);
        sigaction(SIGINT, &sa, NULL);
        #endif
    }
}

#if MICROPY_USE_READLINE == 1

#include <termios.h>

static struct termios orig_termios;

void mp_hal_stdio_mode_raw(void) {
    // save and set terminal settings
    tcgetattr(0, &orig_termios);
    static struct termios termios;
    termios = orig_termios;
    termios.c_iflag &= ~(BRKINT | ICRNL | INPCK | ISTRIP | IXON);
    termios.c_cflag = (termios.c_cflag & ~(CSIZE | PARENB)) | CS8;
    termios.c_lflag = 0;
    termios.c_cc[VMIN] = 1;
    termios.c_cc[VTIME] = 0;
    tcsetattr(0, TCSANOW, &termios);
}

void mp_hal_stdio_mode_orig(void) {
    // restore terminal settings
    tcsetattr(0, TCSANOW, &orig_termios);
}

#endif

#if MICROPY_PY_OS_DUPTERM
static int call_dupterm_read(size_t idx) {
    nlr_buf_t nlr;
    if (nlr_push(&nlr) == 0) {
        mp_obj_t read_m[3];
        mp_load_method(MP_STATE_VM(dupterm_objs[idx]), MP_QSTR_read, read_m);
        read_m[2] = MP_OBJ_NEW_SMALL_INT(1);
        mp_obj_t res = mp_call_method_n_kw(1, 0, read_m);
        if (res == mp_const_none) {
            return -2;
        }
        mp_buffer_info_t bufinfo;
        mp_get_buffer_raise(res, &bufinfo, MP_BUFFER_READ);
        if (bufinfo.len == 0) {
            mp_printf(&mp_plat_print, "dupterm: EOF received, deactivating\n");
            MP_STATE_VM(dupterm_objs[idx]) = MP_OBJ_NULL;
            return -1;
        }
        nlr_pop();
        return *(byte *)bufinfo.buf;
    } else {
        // Temporarily disable dupterm to avoid infinite recursion
        mp_obj_t save_term = MP_STATE_VM(dupterm_objs[idx]);
        MP_STATE_VM(dupterm_objs[idx]) = NULL;
        mp_printf(&mp_plat_print, "dupterm: ");
        mp_obj_print_exception(&mp_plat_print, nlr.ret_val);
        MP_STATE_VM(dupterm_objs[idx]) = save_term;
    }

    return -1;
}
#endif

int mp_hal_stdin_rx_chr(void) {
    #if MICROPY_PY_OS_DUPTERM
    // TODO only support dupterm one slot at the moment
    if (MP_STATE_VM(dupterm_objs[0]) != MP_OBJ_NULL) {
        int c;
        do {
            c = call_dupterm_read(0);
        } while (c == -2);
        if (c == -1) {
            goto main_term;
        }
        if (c == '\n') {
            c = '\r';
        }
        return c;
    }
main_term:;
    #endif

    unsigned char c;
    ssize_t ret;
    MP_HAL_RETRY_SYSCALL(ret, read(STDIN_FILENO, &c, 1), {});
    if (ret == 0) {
        c = 4; // EOF, ctrl-D
    } else if (c == '\n') {
        c = '\r';
    }
    return c;
}

mp_uint_t mp_hal_stdout_tx_strn(const char *str, size_t len) {
    ssize_t ret;
    MP_HAL_RETRY_SYSCALL(ret, write(STDOUT_FILENO, str, len), {});
    mp_uint_t written = ret < 0 ? 0 : ret;
    int dupterm_res = mp_os_dupterm_tx_strn(str, len);
    if (dupterm_res >= 0) {
        written = MIN((mp_uint_t)dupterm_res, written);
    }
    return written;
}

// cooked is same as uncooked because the terminal does some postprocessing
void mp_hal_stdout_tx_strn_cooked(const char *str, size_t len) {
    mp_hal_stdout_tx_strn(str, len);
}

void mp_hal_stdout_tx_str(const char *str) {
    mp_hal_stdout_tx_strn(str, strlen(str));
}

#ifndef mp_hal_ticks_ms
mp_uint_t mp_hal_ticks_ms(void) {
    #if (defined(_POSIX_TIMERS) && _POSIX_TIMERS > 0) && defined(_POSIX_MONOTONIC_CLOCK)
    struct timespec tv;
    clock_gettime(CLOCK_MONOTONIC, &tv);
    return tv.tv_sec * 1000 + tv.tv_nsec / 1000000;
    #else
    struct timeval tv;
    gettimeofday(&tv, NULL);
    return tv.tv_sec * 1000 + tv.tv_usec / 1000;
    #endif
}
#endif

#ifndef mp_hal_ticks_us
mp_uint_t mp_hal_ticks_us(void) {
    #if (defined(_POSIX_TIMERS) && _POSIX_TIMERS > 0) && defined(_POSIX_MONOTONIC_CLOCK)
    struct timespec tv;
    clock_gettime(CLOCK_MONOTONIC, &tv);
    return tv.tv_sec * 1000000 + tv.tv_nsec / 1000;
    #else
    struct timeval tv;
    gettimeofday(&tv, NULL);
    return tv.tv_sec * 1000000 + tv.tv_usec;
    #endif
}
#endif

#ifndef mp_hal_time_ns
uint64_t mp_hal_time_ns(void) {
    struct timeval tv;
    gettimeofday(&tv, NULL);
    return (uint64_t)tv.tv_sec * 1000000000ULL + (uint64_t)tv.tv_usec * 1000ULL;
}
#endif

#ifndef mp_hal_delay_ms
void mp_hal_delay_ms(mp_uint_t ms) {
    if (ms) {
        mp_uint_t start = mp_hal_ticks_ms();
        mp_uint_t elapsed = 0;
        // The wait can return early, so loop until the time is up.
        while (elapsed < ms) {
            mp_event_wait_ms(ms - elapsed);
            elapsed = mp_hal_ticks_ms() - start;
        }
    } else {
        mp_handle_pending(true);
    }
}
#endif

void mp_hal_get_random(size_t n, uint8_t *buf) {
    #ifdef _HAVE_GETRANDOM
    RAISE_ERRNO(getrandom(buf, n, 0), errno);
    #else
    int fd = open("/dev/random", O_RDONLY);
    RAISE_ERRNO(fd, errno);
    RAISE_ERRNO(read(fd, buf, n), errno);
    close(fd);
    #endif
}
