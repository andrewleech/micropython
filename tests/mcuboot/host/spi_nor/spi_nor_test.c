/*
 * This file is part of the MicroPython project, http://micropython.org/
 *
 * The MIT License (MIT)
 *
 * Copyright (c) 2026 Andrew Leech
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

#include <stdio.h>
#include <string.h>
#include <sys/wait.h>
#include <unistd.h>

#include "fake_flash.h"
#include "spi_nor.h"

// Tests of the chip model through the real drivers/memory/spiflash.c: what the driver sends is
// what the chip does, one fake_flash operation per command, and a cut leaves the chip in the
// states the fake_flash device model describes.

static int failures;

#define CHECK(c) \
    do { \
        if (!(c)) { \
            printf("FAIL %s:%d: %s\n", __FILE__, __LINE__, #c); \
            failures++; \
        } \
    } while (0)

static void chip(uint32_t size, bool scatter) {
    fake_flash_dev_cfg_t cfg;
    spi_nor_dev_cfg(&cfg, size, scatter);
    CHECK(fake_flash_init(1, &cfg) == 0);
    CHECK(host_port_flash_attach_spi_nor(0) == 0);
}

static bool all_equal(uint32_t off, uint32_t len, uint8_t v) {
    uint8_t b[8192];
    fake_flash_peek(0, off, b, len);
    for (uint32_t i = 0; i < len; i++) {
        if (b[i] != v) {
            return false;
        }
    }
    return true;
}

// Runs fn in a child that the injected cut ends, as the sweeps run a boot.
static void cut_child(uint32_t k, fake_flash_cut_mode_t mode, uint32_t tear, void (*fn)(void)) {
    pid_t pid = fork();
    if (pid == 0) {
        fake_flash_arm(k, mode, tear, 12345);
        fn();
        _exit(0);
    }
    int st = 0;
    waitpid(pid, &st, 0);
    CHECK(WIFEXITED(st) && WEXITSTATUS(st) == FAKE_FLASH_CUT_EXIT);
}

static void test_commands(void) {
    printf("one operation per page program, erase block and magic invalidation\n");
    chip(64 * 1024, false);
    CHECK(mcuboot_port_flash_erase(0, 0, 8192) == 0);
    uint8_t data[20], back[20];
    for (int i = 0; i < 20; i++) {
        data[i] = (uint8_t)(0x40 + i);
    }
    CHECK(mcuboot_port_flash_write(0, 250, data, 20) == 0);
    CHECK(mcuboot_port_flash_read(0, 250, back, 20) == 0 && memcmp(data, back, 20) == 0);
    const fake_flash_op_t *t = fake_flash_trace();
    // An erase goes from the top block down and programs the last 16 bytes of a block to zero first.
    CHECK(fake_flash_trace_count() == 6);
    CHECK(t[0].kind == FAKE_FLASH_OP_WRITE && t[0].off == 8176 && t[0].len == 16);
    CHECK(t[1].kind == FAKE_FLASH_OP_ERASE && t[1].off == 4096 && t[1].len == 4096);
    CHECK(t[2].kind == FAKE_FLASH_OP_WRITE && t[2].off == 4080 && t[2].len == 16);
    CHECK(t[3].kind == FAKE_FLASH_OP_ERASE && t[3].off == 0 && t[3].len == 4096);
    CHECK(t[4].kind == FAKE_FLASH_OP_WRITE && t[4].off == 250 && t[4].len == 6);
    CHECK(t[5].kind == FAKE_FLASH_OP_WRITE && t[5].off == 256 && t[5].len == 14);
    CHECK(fake_flash_stats()->violations == 0);
}

static void test_nor_programming(void) {
    printf("programming only clears bits\n");
    chip(64 * 1024, false);
    uint8_t a = 0xf0, b = 0x0f, v;
    CHECK(mcuboot_port_flash_write(0, 100, &a, 1) == 0);
    CHECK(mcuboot_port_flash_write(0, 100, &b, 1) == 0);
    CHECK(mcuboot_port_flash_read(0, 100, &v, 1) == 0 && v == 0x00);
    CHECK(fake_flash_stats()->overprograms == 1);
    CHECK(mcuboot_port_flash_erase(0, 0, 4096) == 0);
    CHECK(mcuboot_port_flash_read(0, 100, &v, 1) == 0 && v == 0xff);
}

static void test_32bit_addresses(void) {
    printf("32 bit addressing above 16 MiB\n");
    chip(32 * 1024 * 1024, false);
    uint32_t off = 0x1800000;
    uint8_t data[300], back[300];
    for (int i = 0; i < 300; i++) {
        data[i] = (uint8_t)i;
    }
    CHECK(mcuboot_port_flash_erase(0, off, 4096) == 0);
    CHECK(mcuboot_port_flash_write(0, off + 10, data, 300) == 0);
    CHECK(mcuboot_port_flash_read(0, off + 10, back, 300) == 0 && memcmp(data, back, 300) == 0);
    CHECK(fake_flash_stats()->violations == 0);
}

static void program16(void) {
    uint8_t zeros[16] = {0};
    mcuboot_port_flash_write(0, 0x100, zeros, 16);
}

static void test_cut_program(void) {
    printf("a program cut leaves a torn byte\n");
    for (int scatter = 0; scatter < 2; scatter++) {
        chip(64 * 1024, scatter);
        cut_child(1, FAKE_FLASH_CUT_DURING, 128, program16);
        CHECK(all_equal(0x100, 8, 0x00));
        uint8_t rest[7];
        fake_flash_peek(0, 0x109, rest, 7);
        bool untouched = true;
        for (int i = 0; i < 7; i++) {
            untouched = untouched && rest[i] == 0xff;
        }
        CHECK(untouched == !scatter);
    }
    chip(64 * 1024, false);
    cut_child(1, FAKE_FLASH_CUT_BETWEEN, 128, program16);
    CHECK(all_equal(0x100, 8, 0x00) && all_equal(0x108, 8, 0xff));
}

static void erase_sector(void) {
    mcuboot_port_flash_erase(0, 4096, 4096);
}

// The 16 bytes bootutil takes for a valid trailer magic, at the end of a block, and a flag before.
static const uint8_t magic[16] = {0x77, 0xc2, 0x95, 0xf3, 0x60, 0xd2, 0xef, 0x7f, 0x35, 0x52, 0x50, 0x0f, 0x2c, 0xb6, 0x79, 0x80};

static bool magic_valid(void) {
    uint8_t b[16];
    fake_flash_peek(0, 8176, b, 16);
    return memcmp(b, magic, 16) == 0;
}

static bool flag_erased(void) {
    uint8_t b;
    fake_flash_peek(0, 8160, &b, 1);
    return b == 0xff;
}

static void test_cut_erase(void) {
    printf("an erase cut leaves a partly erased sector\n");
    for (int scatter = 0; scatter < 2; scatter++) {
        chip(64 * 1024, scatter);
        uint8_t zeros[4096] = {0};
        fake_flash_poke(0, 4096, zeros, 4096);
        cut_child(2, FAKE_FLASH_CUT_DURING, 128, erase_sector);
        CHECK(all_equal(4096, 2048, 0xff));
        uint8_t b[4096];
        fake_flash_peek(0, 4096, b, 4096);
        bool programmed = true;
        for (int i = 2049; i < 4096; i++) {
            programmed = programmed && b[i] == 0x00;
        }
        CHECK(programmed == !scatter);
    }
}

static void test_interrupted_erase_keeps_no_valid_magic(void) {
    printf("an interrupted erase never leaves a valid magic behind erased flags\n");
    fake_flash_cut_mode_t modes[] = {FAKE_FLASH_CUT_BEFORE, FAKE_FLASH_CUT_AFTER, FAKE_FLASH_CUT_BETWEEN, FAKE_FLASH_CUT_DURING};
    for (int scatter = 0; scatter < 2; scatter++) {
        for (unsigned m = 0; m < 4; m++) {
            for (uint32_t tear = 0; tear < 256; tear += 51) {
                // The trailer block, then the same cut at the program that comes before the erase
                // (op 1) and at the erase (op 2) of the top block.
                for (uint32_t k = 1; k <= 2; k++) {
                    chip(64 * 1024, scatter);
                    uint8_t flag = 0x01;
                    CHECK(mcuboot_port_flash_write(0, 8160, &flag, 1) == 0);
                    CHECK(mcuboot_port_flash_write(0, 8176, magic, 16) == 0);
                    fake_flash_counter_reset();
                    pid_t pid = fork();
                    if (pid == 0) {
                        fake_flash_arm(k, modes[m], tear, 77 + tear);
                        mcuboot_port_flash_erase(0, 4096, 4096);
                        _exit(0);
                    }
                    int st = 0;
                    waitpid(pid, &st, 0);
                    // Magic and flag: valid with the flag, or not valid; never valid without the flag.
                    CHECK(!magic_valid() || !flag_erased());
                }
            }
        }
    }
}

static void test_refused_commands(void) {
    printf("commands a chip ignores count as violations\n");
    chip(64 * 1024, false);
    spi_nor_t n;
    CHECK(spi_nor_init(&n, 0) == 0);
    CHECK(fake_flash_stats()->violations == 0);
    const mp_qspi_proto_t *p = n.config.bus.u_qspi.proto;
    void *bus = n.config.bus.u_qspi.data;
    uint8_t buf[16] = {0};
    p->ioctl(bus, MP_QSPI_IOCTL_BUS_ACQUIRE, 0);
    // A program without write enable.
    p->write_cmd_addr_data(bus, 0x02, 0, 1, buf);
    CHECK(fake_flash_stats()->violations == 1 && all_equal(0, 16, 0xff));
    // A program that crosses a page.
    p->write_cmd_data(bus, 0x06, 0, 0);
    p->write_cmd_addr_data(bus, 0x02, 250, 16, buf);
    CHECK(fake_flash_stats()->violations == 2 && all_equal(240, 32, 0xff));
    // A command while the chip sleeps.
    p->write_cmd_data(bus, 0xb9, 0, 0);
    uint32_t sr;
    p->read_cmd(bus, 0x05, 1, &sr);
    CHECK(fake_flash_stats()->violations == 3);
    p->write_cmd_data(bus, 0xab, 0, 0);
    p->read_cmd(bus, 0x05, 1, &sr);
    CHECK(fake_flash_stats()->violations == 3 && (sr & 2) != 0);
    p->ioctl(bus, MP_QSPI_IOCTL_BUS_RELEASE, 0);
    // A command outside a bus acquire.
    p->read_cmd(bus, 0x05, 1, &sr);
    CHECK(fake_flash_stats()->violations == 4);
}

int main(void) {
    test_commands();
    test_nor_programming();
    test_32bit_addresses();
    test_cut_program();
    test_cut_erase();
    test_interrupted_erase_keeps_no_valid_magic();
    test_refused_commands();
    printf(failures == 0 ? "RESULT PASS\n" : "RESULT FAIL (%d)\n", failures);
    return failures != 0;
}
