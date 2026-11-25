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

#ifdef __MINGW32__
#if !defined(_WIN32_WINNT) || _WIN32_WINNT < 0x0600
#undef _WIN32_WINNT
#define _WIN32_WINNT 0x0600
#endif
#endif

#include "py/runtime.h"
#include "py/mphal.h"
#include "py/mpthread.h"
#include "py/stream.h"
#include "py/mperrno.h"

#include <errno.h>
#include <stdio.h>
#include <stdlib.h>
#include <sys/time.h>
#include <winsock2.h>
#include <windows.h>
#include <unistd.h>
#include <bcrypt.h>

HANDLE std_in = NULL;
HANDLE con_out = NULL;
DWORD orig_mode = 0;

static void assure_stdin_handle() {
    if (!std_in) {
        std_in = GetStdHandle(STD_INPUT_HANDLE);
        assert(std_in != INVALID_HANDLE_VALUE);
    }
}

static void assure_conout_handle() {
    if (!con_out) {
        con_out = CreateFile("CONOUT$", GENERIC_READ | GENERIC_WRITE,
            FILE_SHARE_READ | FILE_SHARE_WRITE,
            NULL, OPEN_EXISTING, 0, 0);
        assert(con_out != INVALID_HANDLE_VALUE);
    }
}

void mp_hal_stdio_mode_raw(void) {
    assure_stdin_handle();
    GetConsoleMode(std_in, &orig_mode);
    DWORD mode = orig_mode;
    mode &= ~ENABLE_ECHO_INPUT;
    mode &= ~ENABLE_LINE_INPUT;
    mode &= ~ENABLE_PROCESSED_INPUT;
    SetConsoleMode(std_in, mode);
}

void mp_hal_stdio_mode_orig(void) {
    assure_stdin_handle();
    SetConsoleMode(std_in, orig_mode);
}

// Handler to be installed by SetConsoleCtrlHandler, currently used only to handle Ctrl-C.
// This handler has to be installed just once (this has to be done elsewhere in init code).
// Previous versions of the mp_hal code would install a handler whenever Ctrl-C input is
// allowed and remove the handler again when it is not. That is not necessary though (1),
// and it might introduce problems (2) because console notifications are delivered to the
// application in a separate thread.
// (1) mp_hal_set_interrupt_char effectively enables/disables processing of Ctrl-C via the
// ENABLE_PROCESSED_INPUT flag so in raw mode console_sighandler won't be called.
// (2) if mp_hal_set_interrupt_char would remove the handler while Ctrl-C was issued earlier,
// the thread created for handling it might not be running yet so we'd miss the notification.
BOOL WINAPI console_sighandler(DWORD evt) {
    if (evt == CTRL_C_EVENT) {
        if (MP_STATE_MAIN_THREAD(mp_pending_exception) == MP_OBJ_FROM_PTR(&MP_STATE_VM(mp_kbd_exception))) {
            // this is the second time we are called, so die straight away
            exit(1);
        }
        mp_sched_keyboard_interrupt();
        return TRUE;
    }
    return FALSE;
}

void mp_hal_set_interrupt_char(char c) {
    assure_stdin_handle();
    if (c == CHAR_CTRL_C) {
        DWORD mode;
        GetConsoleMode(std_in, &mode);
        mode |= ENABLE_PROCESSED_INPUT;
        SetConsoleMode(std_in, mode);
    } else {
        DWORD mode;
        GetConsoleMode(std_in, &mode);
        mode &= ~ENABLE_PROCESSED_INPUT;
        SetConsoleMode(std_in, mode);
    }
}

void mp_hal_move_cursor_back(uint pos) {
    if (!pos) {
        return;
    }
    assure_conout_handle();
    CONSOLE_SCREEN_BUFFER_INFO info;
    GetConsoleScreenBufferInfo(con_out, &info);
    info.dwCursorPosition.X -= (short)pos;
    // Move up a line if needed.
    while (info.dwCursorPosition.X < 0) {
        info.dwCursorPosition.X = info.dwSize.X + info.dwCursorPosition.X;
        info.dwCursorPosition.Y -= 1;
    }
    // Caller requested to move out of the screen. That's not possible so just clip,
    // it's the caller's responsibility to not let this happen.
    if (info.dwCursorPosition.Y < 0) {
        info.dwCursorPosition.X = 0;
        info.dwCursorPosition.Y = 0;
    }
    SetConsoleCursorPosition(con_out, info.dwCursorPosition);
}

void mp_hal_erase_line_from_cursor(uint n_chars_to_erase) {
    assure_conout_handle();
    CONSOLE_SCREEN_BUFFER_INFO info;
    GetConsoleScreenBufferInfo(con_out, &info);
    DWORD written;
    FillConsoleOutputCharacter(con_out, ' ', n_chars_to_erase, info.dwCursorPosition, &written);
    FillConsoleOutputAttribute(con_out, info.wAttributes, n_chars_to_erase, info.dwCursorPosition, &written);
}

typedef struct item_t {
    WORD vkey;
    const char *seq;
} item_t;

// map virtual key codes to key sequences known by MicroPython's readline implementation
static item_t keyCodeMap[] = {
    {VK_UP, "[A"},
    {VK_DOWN, "[B"},
    {VK_RIGHT, "[C"},
    {VK_LEFT, "[D"},
    {VK_HOME, "[H"},
    {VK_END, "[F"},
    {VK_DELETE, "[3~"},
    {0, ""} // sentinel
};

// likewise, but with Ctrl key down
static item_t ctrlKeyCodeMap[] = {
    {VK_LEFT, "b"},
    {VK_RIGHT, "f"},
    {VK_DELETE, "d"},
    {VK_BACK, "\x7F"},
    {0, ""} // sentinel
};

static const char *cur_esc_seq = NULL;

static int esc_seq_process_vk(WORD vk, bool ctrl_key_down) {
    for (item_t *p = (ctrl_key_down ? ctrlKeyCodeMap : keyCodeMap); p->vkey != 0; ++p) {
        if (p->vkey == vk) {
            cur_esc_seq = p->seq;
            return 27; // ESC, start of escape sequence
        }
    }
    return 0; // nothing found
}

static int esc_seq_chr() {
    if (cur_esc_seq) {
        const char c = *cur_esc_seq++;
        if (c) {
            return c;
        }
        cur_esc_seq = NULL;
    }
    return 0;
}

// Console input remains on the calling thread so the existing REPL key handling
// and console-mode changes retain their original behaviour.
static int console_stdin_rx_chr(void) {
    const int ret = esc_seq_chr();
    if (ret) {
        return ret;
    }
    assure_stdin_handle();
    BOOL status;
    DWORD num_read;
    INPUT_RECORD rec;
    for (;;) {
        MP_THREAD_GIL_EXIT();
        status = ReadConsoleInput(std_in, &rec, 1, &num_read);
        MP_THREAD_GIL_ENTER();
        if (!status || !num_read) {
            return CHAR_CTRL_C; // EOF, ctrl-D
        }
        if (rec.EventType != KEY_EVENT || !rec.Event.KeyEvent.bKeyDown) { // only want key down events
            continue;
        }
        const bool ctrl_key_down = (rec.Event.KeyEvent.dwControlKeyState & LEFT_CTRL_PRESSED) ||
            (rec.Event.KeyEvent.dwControlKeyState & RIGHT_CTRL_PRESSED);
        const int ret = esc_seq_process_vk(rec.Event.KeyEvent.wVirtualKeyCode, ctrl_key_down);
        if (ret) {
            return ret;
        }
        const char c = rec.Event.KeyEvent.uChar.AsciiChar;
        if (c) { // plain ascii char, return it
            return c;
        }
    }
}

// Synchronous Windows pipes cannot be included in WSAPoll.  Two native workers
// bridge them to bounded queues and signal the existing scheduler wake socket.
// Their storage is process-lifetime: a reader can remain blocked in ReadFile at
// shutdown, and must never touch freed state.
#define STDIO_QUEUE_SIZE (16384)
typedef struct {
    char data[STDIO_QUEUE_SIZE];
    size_t head, count;
    HANDLE thread;
    bool eof, failed;
} stdio_queue_t;

static INIT_ONCE stdio_once = INIT_ONCE_STATIC_INIT;
static CRITICAL_SECTION stdio_lock;
static CONDITION_VARIABLE stdio_changed;
static stdio_queue_t stdin_queue, stdout_queue;
static bool stdio_stopping;
static bool stdin_is_console, stdout_is_pipe;
static HANDLE stdout_handle;

static BOOL CALLBACK stdio_init_once(PINIT_ONCE once, PVOID parameter, PVOID *context) {
    (void)once; (void)parameter; (void)context;
    InitializeCriticalSection(&stdio_lock);
    InitializeConditionVariable(&stdio_changed);
    if (!std_in) {
        std_in = GetStdHandle(STD_INPUT_HANDLE);
    }
    DWORD mode;
    stdin_is_console = GetConsoleMode(std_in, &mode);
    stdout_handle = GetStdHandle(STD_OUTPUT_HANDLE);
    stdout_is_pipe = GetFileType(stdout_handle) == FILE_TYPE_PIPE;
    return TRUE;
}

static void stdio_init(void) {
    InitOnceExecuteOnce(&stdio_once, stdio_init_once, NULL, NULL);
}

// Called with stdio_lock held; shutdown takes the same lock before closing the
// wake handles, so a late pipe completion cannot signal a closed handle.
static void stdio_notify(void) {
    WakeAllConditionVariable(&stdio_changed);
    if (!stdio_stopping) {
        mp_hal_signal_event();
    }
}

static DWORD WINAPI stdin_worker(void *arg) {
    (void)arg;
    char buf[4096];
    for (;;) {
        EnterCriticalSection(&stdio_lock);
        while (stdin_queue.count == STDIO_QUEUE_SIZE && !stdio_stopping) {
            SleepConditionVariableCS(&stdio_changed, &stdio_lock, INFINITE);
        }
        if (stdio_stopping) {
            LeaveCriticalSection(&stdio_lock);
            return 0;
        }
        DWORD want = STDIO_QUEUE_SIZE - stdin_queue.count;
        if (want > sizeof(buf)) {
            want = sizeof(buf);
        }
        LeaveCriticalSection(&stdio_lock);
        DWORD n = 0;
        BOOL ok = ReadFile(std_in, buf, want, &n, NULL);
        DWORD error = ok ? ERROR_SUCCESS : GetLastError();
        EnterCriticalSection(&stdio_lock);
        if (stdio_stopping) {
            LeaveCriticalSection(&stdio_lock);
            return 0;
        }
        if (!ok || n == 0) {
            stdin_queue.eof = ok || error == ERROR_BROKEN_PIPE || error == ERROR_HANDLE_EOF
                || error == ERROR_NO_DATA;
            stdin_queue.failed = !stdin_queue.eof;
            stdio_notify();
            LeaveCriticalSection(&stdio_lock);
            return 0;
        }
        size_t tail = (stdin_queue.head + stdin_queue.count) % STDIO_QUEUE_SIZE;
        size_t first = STDIO_QUEUE_SIZE - tail;
        if (first > n) {
            first = n;
        }
        memcpy(stdin_queue.data + tail, buf, first);
        memcpy(stdin_queue.data, buf + first, n - first);
        stdin_queue.count += n;
        stdio_notify();
        LeaveCriticalSection(&stdio_lock);
    }
}

static DWORD WINAPI stdout_worker(void *arg) {
    (void)arg;
    EnterCriticalSection(&stdio_lock);
    for (;;) {
        while (stdout_queue.count == 0 && !stdio_stopping) {
            SleepConditionVariableCS(&stdio_changed, &stdio_lock, INFINITE);
        }
        if (stdio_stopping) {
            break;
        }
        DWORD n = STDIO_QUEUE_SIZE - stdout_queue.head;
        if (n > stdout_queue.count) {
            n = stdout_queue.count;
        }
        const char *data = stdout_queue.data + stdout_queue.head;
        LeaveCriticalSection(&stdio_lock);
        DWORD written = 0;
        BOOL ok = WriteFile(stdout_handle, data, n, &written, NULL);
        EnterCriticalSection(&stdio_lock);
        if (!ok || written == 0) {
            stdout_queue.failed = true;
            stdio_notify();
            break;
        }
        stdout_queue.head = (stdout_queue.head + written) % STDIO_QUEUE_SIZE;
        stdout_queue.count -= written;
        stdio_notify();
    }
    LeaveCriticalSection(&stdio_lock);
    return 0;
}

static bool stdio_start(stdio_queue_t *queue, LPTHREAD_START_ROUTINE worker) {
    if (queue->thread == NULL && !queue->failed) {
        queue->thread = CreateThread(NULL, 0, worker, NULL, 0, NULL);
        if (queue->thread == NULL) {
            queue->failed = true;
        }
    }
    return !queue->failed;
}

mp_uint_t mp_hal_stdio_read(void *buf, mp_uint_t size, int *errcode) {
    if (size == 0) {
        return 0;
    }
    stdio_init();
    if (stdin_is_console) {
        MP_THREAD_GIL_EXIT();
        int n = read(STDIN_FILENO, buf, size);
        MP_THREAD_GIL_ENTER();
        if (n < 0) {
            *errcode = errno;
            return MP_STREAM_ERROR;
        }
        return n;
    }
    EnterCriticalSection(&stdio_lock);
    stdio_start(&stdin_queue, stdin_worker);
    if (stdin_queue.count == 0) {
        if (stdin_queue.failed) {
            *errcode = MP_EIO;
            size = MP_STREAM_ERROR;
        } else if (!stdin_queue.eof) {
            *errcode = MP_EAGAIN;
            size = MP_STREAM_ERROR;
        } else {
            size = 0;
        }
    } else {
        if (size > stdin_queue.count) {
            size = stdin_queue.count;
        }
        size_t first = STDIO_QUEUE_SIZE - stdin_queue.head;
        if (first > size) {
            first = size;
        }
        memcpy(buf, stdin_queue.data + stdin_queue.head, first);
        memcpy((char *)buf + first, stdin_queue.data, size - first);
        stdin_queue.head = (stdin_queue.head + size) % STDIO_QUEUE_SIZE;
        stdin_queue.count -= size;
        WakeAllConditionVariable(&stdio_changed);
    }
    LeaveCriticalSection(&stdio_lock);
    return size;
}

mp_uint_t mp_hal_stdio_write(const void *buf, mp_uint_t size, int *errcode) {
    if (size == 0) {
        return 0;
    }
    stdio_init();
    if (!stdout_is_pipe) {
        MP_THREAD_GIL_EXIT();
        int n = write(STDOUT_FILENO, buf, size);
        MP_THREAD_GIL_ENTER();
        if (n < 0) {
            *errcode = errno;
            return MP_STREAM_ERROR;
        }
        return n;
    }
    EnterCriticalSection(&stdio_lock);
    if (!stdio_start(&stdout_queue, stdout_worker)) {
        *errcode = MP_EPIPE;
        size = MP_STREAM_ERROR;
    } else if (stdout_queue.count == STDIO_QUEUE_SIZE) {
        *errcode = MP_EAGAIN;
        size = MP_STREAM_ERROR;
    } else {
        size_t available = STDIO_QUEUE_SIZE - stdout_queue.count;
        if (size > available) {
            size = available;
        }
        size_t tail = (stdout_queue.head + stdout_queue.count) % STDIO_QUEUE_SIZE;
        size_t first = STDIO_QUEUE_SIZE - tail;
        if (first > size) {
            first = size;
        }
        memcpy(stdout_queue.data + tail, buf, first);
        memcpy(stdout_queue.data, (const char *)buf + first, size - first);
        stdout_queue.count += size;
        WakeAllConditionVariable(&stdio_changed);
    }
    LeaveCriticalSection(&stdio_lock);
    return size;
}

bool mp_hal_stdio_has_signal(int fd) {
    stdio_init();
    return (fd == STDIN_FILENO && !stdin_is_console)
        || (fd == STDOUT_FILENO && stdout_is_pipe);
}

mp_uint_t mp_hal_stdio_poll_fd(int fd, mp_uint_t flags) {
    stdio_init();
    mp_uint_t ret = 0;
    EnterCriticalSection(&stdio_lock);
    if (fd == STDIN_FILENO) {
        if (stdin_is_console) {
            if ((flags & MP_STREAM_POLL_RD) && (cur_esc_seq || WaitForSingleObject(std_in, 0) == WAIT_OBJECT_0)) {
                ret |= MP_STREAM_POLL_RD;
            }
        } else {
            if (!stdio_start(&stdin_queue, stdin_worker)) {
                ret |= MP_STREAM_POLL_ERR;
            }
            if ((flags & MP_STREAM_POLL_RD) && (stdin_queue.count || stdin_queue.eof)) {
                ret |= MP_STREAM_POLL_RD;
            }
            if (stdin_queue.eof && stdin_queue.count == 0) {
                ret |= MP_STREAM_POLL_HUP;
            }
        }
    } else if (fd == STDOUT_FILENO && stdout_is_pipe) {
        if (!stdio_start(&stdout_queue, stdout_worker)) {
            ret |= MP_STREAM_POLL_ERR;
        } else if ((flags & MP_STREAM_POLL_WR) && stdout_queue.count < STDIO_QUEUE_SIZE) {
            ret |= MP_STREAM_POLL_WR;
        }
    } else if (flags & MP_STREAM_POLL_WR) {
        ret |= MP_STREAM_POLL_WR;
    }
    LeaveCriticalSection(&stdio_lock);
    return ret;
}

int mp_hal_stdio_flush(void) {
    stdio_init();
    EnterCriticalSection(&stdio_lock);
    MP_THREAD_GIL_EXIT();
    while (stdout_queue.count && !stdout_queue.failed) {
        SleepConditionVariableCS(&stdio_changed, &stdio_lock, INFINITE);
    }
    bool failed = stdout_queue.failed;
    LeaveCriticalSection(&stdio_lock);
    MP_THREAD_GIL_ENTER();
    return failed ? -1 : 0;
}

int mp_hal_stdin_rx_chr(void) {
    stdio_init();
    if (stdin_is_console) {
        return console_stdin_rx_chr();
    }
    char c;
    int err;
    for (;;) {
        mp_uint_t n = mp_hal_stdio_read(&c, 1, &err);
        if (n == 1) {
            return (unsigned char)c;
        }
        if (n == 0 || err != MP_EAGAIN) {
            return CHAR_CTRL_C;
        }
        EnterCriticalSection(&stdio_lock);
        MP_THREAD_GIL_EXIT();
        if (!stdin_queue.count && !stdin_queue.eof && !stdin_queue.failed) {
            SleepConditionVariableCS(&stdio_changed, &stdio_lock, INFINITE);
        }
        LeaveCriticalSection(&stdio_lock);
        MP_THREAD_GIL_ENTER();
    }
}

mp_uint_t mp_hal_stdout_tx_strn(const char *str, size_t len) {
    size_t done = 0;
    while (done < len) {
        int err = 0;
        mp_uint_t n = mp_hal_stdio_write(str + done, len - done, &err);
        if (n != MP_STREAM_ERROR) {
            done += n;
        } else if (err != MP_EAGAIN) {
            break;
        } else {
            EnterCriticalSection(&stdio_lock);
            MP_THREAD_GIL_EXIT();
            if (stdout_queue.count == STDIO_QUEUE_SIZE && !stdout_queue.failed) {
                SleepConditionVariableCS(&stdio_changed, &stdio_lock, INFINITE);
            }
            LeaveCriticalSection(&stdio_lock);
            MP_THREAD_GIL_ENTER();
        }
    }
    return done;
}

void mp_hal_stdout_tx_strn_cooked(const char *str, size_t len) {
    mp_hal_stdout_tx_strn(str, len);
}

void mp_hal_stdout_tx_str(const char *str) {
    mp_hal_stdout_tx_strn(str, strlen(str));
}

mp_uint_t mp_hal_ticks_ms(void) {
    struct timeval tv;
    gettimeofday(&tv, NULL);
    return tv.tv_sec * 1000 + tv.tv_usec / 1000;
}

mp_uint_t mp_hal_ticks_us(void) {
    struct timeval tv;
    gettimeofday(&tv, NULL);
    return tv.tv_sec * 1000000 + tv.tv_usec;
}

mp_uint_t mp_hal_ticks_cpu(void) {
    LARGE_INTEGER value;
    QueryPerformanceCounter(&value);
    #ifdef _WIN64
    return value.QuadPart;
    #else
    return value.LowPart;
    #endif
}

uint64_t mp_hal_time_ns(void) {
    struct timeval tv;
    gettimeofday(&tv, NULL);
    return (uint64_t)tv.tv_sec * 1000000000ULL + (uint64_t)tv.tv_usec * 1000ULL;
}

void msec_sleep(double msec) {
    if (msec < 0.0) {
        msec = 0.0;
    }
    SleepEx((DWORD)msec, TRUE);
}

// The wake event.  Auto-reset, so a wait consumes it atomically, a raise with
// nothing waiting latches for the next wait, and repeated raises coalesce.
static HANDLE wake_event = NULL;

// A loopback UDP socket connected to itself, so a raise can also end a WSAPoll() wait,
// which can't wait on a HANDLE.  Sending to it is what makes it readable; the
// select module polls it alongside its own sockets and drains it afterwards.
// INVALID_SOCKET if it couldn't be created, in which case the select module falls
// back to bounded waits.
static SOCKET wake_sock = INVALID_SOCKET;
// Serialise draining, signalling and closure.  A signal after a drain must
// enqueue a new datagram, never be consumed by the preceding drain.
static SRWLOCK wake_lock = SRWLOCK_INIT;
static bool wake_sock_pending;

static void wake_socket_init(void) {
    WSADATA wsa;
    if (WSAStartup(MAKEWORD(2, 2), &wsa) != 0) {
        return;
    }
    SOCKET s = socket(AF_INET, SOCK_DGRAM, IPPROTO_UDP);
    if (s == INVALID_SOCKET) {
        return;
    }
    struct sockaddr_in addr = {0};
    addr.sin_family = AF_INET;
    addr.sin_addr.s_addr = htonl(INADDR_LOOPBACK);
    int len = sizeof(addr);
    u_long nonblocking = 1;
    if (bind(s, (struct sockaddr *)&addr, sizeof(addr)) != 0
        || getsockname(s, (struct sockaddr *)&addr, &len) != 0
        || connect(s, (struct sockaddr *)&addr, sizeof(addr)) != 0
        || ioctlsocket(s, FIONBIO, &nonblocking) != 0) {
        closesocket(s);
        return;
    }
    wake_sock = s;
}

uintptr_t mp_hal_wake_socket(void) {
    return (uintptr_t)wake_sock;
}

void mp_hal_wake_socket_drain(void) {
    AcquireSRWLockExclusive(&wake_lock);
    if (wake_sock != INVALID_SOCKET) {
        char buf[16];
        while (recv(wake_sock, buf, sizeof(buf), 0) > 0) {
        }
        wake_sock_pending = false;
    }
    ReleaseSRWLockExclusive(&wake_lock);
}

void mp_hal_wake_event_init(void) {
    wake_socket_init();
    wake_event = CreateEvent(NULL, FALSE, FALSE, NULL);
    if (wake_event == NULL) {
        fprintf(stderr, "FATAL: cannot create wake event: %lu\n", (unsigned long)GetLastError());
        exit(1);
    }
}

void mp_hal_wake_event_deinit(void) {
    // Flush before stopping the writer. The reader may stay blocked in ReadFile;
    // process-lifetime storage and the stopping flag make a late return safe.
    mp_hal_stdio_flush();
    EnterCriticalSection(&stdio_lock);
    stdio_stopping = true;
    WakeAllConditionVariable(&stdio_changed);
    LeaveCriticalSection(&stdio_lock);
    if (stdout_queue.thread != NULL) {
        MP_THREAD_GIL_EXIT();
        WaitForSingleObject(stdout_queue.thread, INFINITE);
        MP_THREAD_GIL_ENTER();
        CloseHandle(stdout_queue.thread);
    }
    AcquireSRWLockExclusive(&wake_lock);
    HANDLE h = wake_event;
    wake_event = NULL;
    CloseHandle(h);
    SOCKET s = wake_sock;
    wake_sock = INVALID_SOCKET;
    if (s != INVALID_SOCKET) {
        closesocket(s);
    }
    ReleaseSRWLockExclusive(&wake_lock);
}

void mp_hal_signal_event(void) {
    AcquireSRWLockExclusive(&wake_lock);
    if (wake_event != NULL) {
        SetEvent(wake_event);
    }
    if (wake_sock != INVALID_SOCKET && !wake_sock_pending) {
        // Do not latch a failed send: the next signal must be able to retry.
        if (send(wake_sock, "", 1, 0) == 1) {
            wake_sock_pending = true;
        }
    }
    ReleaseSRWLockExclusive(&wake_lock);
}

// INFINITE is the all-ones DWORD, so a finite wait of 49 days stops one
// millisecond short of it and the caller loops for the rest.
static DWORD wake_event_wait_dword(uint64_t ms) {
    return ms < INFINITE ? (DWORD)ms : INFINITE - 1;
}

int mp_hal_wake_event_wait_tv(struct timeval *tv) {
    DWORD ms;
    if (tv == NULL) {
        ms = INFINITE;
    } else {
        // Round up, so a sub-millisecond sleep waits rather than spinning to
        // the deadline; the timer granularity is coarser than that anyway.
        ms = wake_event_wait_dword((uint64_t)tv->tv_sec * 1000 + ((uint64_t)tv->tv_usec + 999) / 1000);
    }
    if (!mp_sched_thread_can_run_callbacks()) {
        SleepEx(ms, TRUE);
        return 0;
    }
    // Anything but a timeout, including WAIT_IO_COMPLETION from the alertable
    // wait, sends the caller back round to recompute what is left.
    if (WaitForSingleObjectEx(wake_event, ms, TRUE) != WAIT_TIMEOUT) {
        errno = EINTR;
        return -1;
    }
    return 0;
}

void mp_hal_wake_event_wait_ms(mp_uint_t timeout_ms) {
    DWORD ms = timeout_ms == MP_HAL_WAKE_EVENT_FOREVER
        ? INFINITE : wake_event_wait_dword(timeout_ms);
    // Only the thread that runs scheduled callbacks may consume the event.
    if (!mp_sched_thread_can_run_callbacks()) {
        SleepEx(ms, TRUE);
        return;
    }
    // Alertable, so queued APCs still run.
    WaitForSingleObjectEx(wake_event, ms, TRUE);
}

#ifdef _MSC_VER
int usleep(__int64 usec) {
    msec_sleep((double)usec / 1000.0);
    return 0;
}
#endif

void mp_hal_delay_ms(mp_uint_t ms) {
    mp_uint_t start = mp_hal_ticks_ms();
    mp_uint_t elapsed = 0;
    // The wait can return early, so loop until the time is up.
    while (elapsed < ms) {
        mp_event_wait_ms(ms - elapsed);
        elapsed = mp_hal_ticks_ms() - start;
    }
}

void mp_hal_get_random(size_t n, uint8_t *buf) {
    NTSTATUS result = BCryptGenRandom(NULL, (unsigned char *)buf, n, BCRYPT_USE_SYSTEM_PREFERRED_RNG);
    if (!BCRYPT_SUCCESS(result)) {
        mp_raise_OSError(errno);
    }
}
