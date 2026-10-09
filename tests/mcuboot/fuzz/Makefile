# Fuzz targets and host tests for the fsload readers (shared/mcuboot/src/fsload*.c, vfs_*.c,
# gzstream.c).
#
#   make fuzz-build              build the libFuzzer targets (clang with ASan and UBSan)
#   make fuzz-corpus             write the seed corpora (gen_corpus.py)
#   make fuzz-run T=fat S=600    run one target for S seconds with the seed corpus
#   make fuzz-all S=600          run every target in parallel
#   make test                    build and run the host tests with gcc
#
# libFuzzer needs clang. Without a local clang the fuzz targets are built and run in the Docker
# image named by DOCKER_IMAGE (any image with clang and libclang_rt.fuzzer; the Zephyr CI image
# has both), with the host user and group and the repository mounted at its host path. Set CLANG
# to a local clang to skip Docker.

TOP := $(abspath $(dir $(lastword $(MAKEFILE_LIST)))/../../..)
HERE := $(TOP)/tests/mcuboot/fuzz
BUILD ?= $(HERE)/build

DOCKER_IMAGE ?= ghcr.io/zephyrproject-rtos/ci:v0.29.2
DOCKER = docker run --rm -v "$(TOP):$(TOP)" -w "$(TOP)" --user "$(shell id -u):$(shell id -g)" $(DOCKER_IMAGE)
CLANG ?= $(DOCKER) /usr/lib/llvm-20/bin/clang

# Policy of the build: swap (swap using offset) or single.
POLICY ?= swap

SRC_DIR := $(TOP)/shared/mcuboot/src
FSLOAD_SRC := \
	$(SRC_DIR)/fsload_stream.c \
	$(SRC_DIR)/vfs_raw.c \
	$(SRC_DIR)/vfs_fat.c \
	$(SRC_DIR)/vfs_lfs.c \
	$(SRC_DIR)/gzstream.c

LIB_SRC := \
	$(TOP)/lib/oofatfs/ff.c \
	$(TOP)/lib/oofatfs/ffunicode.c \
	$(TOP)/lib/littlefs/lfs2.c \
	$(TOP)/lib/littlefs/lfs2_util.c \
	$(TOP)/lib/uzlib/adler32.c \
	$(TOP)/lib/uzlib/crc32.c \
	$(TOP)/lib/uzlib/header.c \
	$(TOP)/lib/uzlib/tinflate.c

# What the targets that run the whole of fsload need on top.
RUN_SRC := \
	$(SRC_DIR)/fsload.c \
	$(SRC_DIR)/update.c \
	$(SRC_DIR)/mcuboot_crc32.c \
	$(TOP)/shared/tinyusb/mboot/common/mboot_elem.c \
	$(HERE)/harness/stub_fuzz.c

HARNESS_SRC := \
	$(HERE)/harness/host_env.c \
	$(SRC_DIR)/flash_map_backend.c

INC := -I$(HERE)/config -I$(HERE)/harness \
	-I$(TOP)/shared/mcuboot/include \
	-I$(TOP)/shared/tinyusb/mboot/include \
	-I$(TOP)/lib/mcuboot/boot/bootutil/include \
	-I$(TOP)/lib/mcuboot/boot/bootutil/src \
	-I$(TOP)

# Library configuration of the fsload readers: FAT read only with the static LFN buffer, littlefs2
# read only without malloc, assertions and logging off, paranoid uzlib checks.
LIB_DEFS := -DFFCONF_H=\"mcuboot_ffconf.h\" \
	-DLFS2_NO_MALLOC -DLFS2_NO_DEBUG -DLFS2_NO_WARN -DLFS2_NO_ERROR -DLFS2_NO_ASSERT -DLFS2_READONLY \
	-DUZLIB_CONF_PARANOID_CHECKS=1

POLICY_DEF := $(if $(filter single,$(POLICY)),-DFUZZ_POLICY_SINGLE,)

FUZZ_CFLAGS := -std=gnu99 -O1 -g -fno-omit-frame-pointer $(INC) $(LIB_DEFS) $(POLICY_DEF) -DHAVE_ZLIB \
	-fsanitize=fuzzer-no-link,address,undefined -fno-sanitize-recover=undefined
FUZZ_LDFLAGS := -fsanitize=fuzzer,address,undefined -lz

TARGETS := fat lfs gz stream run

.PHONY: all fuzz-build fuzz-corpus fuzz-run fuzz-all test test-build testdata measure clean

all: fuzz-build

fuzz-build: $(addprefix $(BUILD)/fuzz_,$(TARGETS)) $(BUILD)/fuzz_run_single

$(BUILD):
	mkdir -p $(BUILD)

# The two policies of the run target are separate binaries.
$(BUILD)/fuzz_run: $(HERE)/fuzz_run.c $(FSLOAD_SRC) $(LIB_SRC) $(RUN_SRC) $(HARNESS_SRC) | $(BUILD)
	$(CLANG) $(FUZZ_CFLAGS) $^ $(FUZZ_LDFLAGS) -o $@

$(BUILD)/fuzz_run_single: $(HERE)/fuzz_run.c $(FSLOAD_SRC) $(LIB_SRC) $(RUN_SRC) $(HARNESS_SRC) | $(BUILD)
	$(CLANG) $(FUZZ_CFLAGS) -DFUZZ_POLICY_SINGLE $^ $(FUZZ_LDFLAGS) -o $@

$(BUILD)/fuzz_%: $(HERE)/fuzz_%.c $(FSLOAD_SRC) $(LIB_SRC) $(SRC_DIR)/fsload.c $(SRC_DIR)/update.c $(SRC_DIR)/mcuboot_crc32.c $(TOP)/shared/tinyusb/mboot/common/mboot_elem.c $(HERE)/harness/stub_fuzz.c $(HARNESS_SRC) | $(BUILD)
	$(CLANG) $(FUZZ_CFLAGS) $^ $(FUZZ_LDFLAGS) -o $@

fuzz-corpus:
	python3 $(HERE)/gen_corpus.py --out $(BUILD)/corpus

# Runs one target. T is fat, lfs, gz, stream, run or run_single; S is the run time in seconds.
T ?= fat
S ?= 600
fuzz-run: fuzz-build
	mkdir -p $(BUILD)/work/$(T) $(BUILD)/artifacts/$(T)
	cd $(TOP) && ASAN_OPTIONS=detect_leaks=1:abort_on_error=0 UBSAN_OPTIONS=print_stacktrace=1 \
		$(BUILD)/fuzz_$(T) -max_total_time=$(S) -max_len=1048576 -rss_limit_mb=4096 -timeout=20 \
		-artifact_prefix=$(BUILD)/artifacts/$(T)/ \
		$(BUILD)/work/$(T) $(wildcard $(BUILD)/corpus/$(subst _single,,$(T))) 2>&1 | tee $(BUILD)/fuzz_$(T).log

fuzz-all: fuzz-build
	for t in $(TARGETS) run_single; do \
		$(MAKE) fuzz-run T=$$t S=$(S) > $(BUILD)/fuzz_$$t.out 2>&1 & \
	done; wait

# ---- host tests (install and refuse cases, power cut sweep) ----
#
# test_fsload links the real bootutil (hash and tinycrypt ECDSA P-256 validation), the shared
# flash map backend and fsload, over the host flash of harness/host_env.c.

BUTIL := $(TOP)/lib/mcuboot/boot/bootutil
TC := $(TOP)/lib/mcuboot/ext/tinycrypt/lib
AS1 := $(TOP)/lib/mcuboot/ext/mbedtls-asn1
PYTHON ?= python3

BOOTUTIL_SRC := $(addprefix $(BUTIL)/src/,bootutil_area.c bootutil_find_key.c bootutil_img_hash.c \
	bootutil_img_security_cnt.c bootutil_loader.c bootutil_misc.c bootutil_public.c boot_record.c caps.c \
	fault_injection_hardening.c image_ecdsa.c image_validate.c tlv.c)
BOOTUTIL_SWAP_SRC := $(addprefix $(BUTIL)/src/,loader.c swap_misc.c swap_offset.c)
CRYPTO_SRC := $(addprefix $(TC)/source/,ecc.c ecc_dsa.c sha256.c utils.c) $(addprefix $(AS1)/src/,asn1parse.c platform_util.c)

TEST_SRC := $(HERE)/test_fsload.c $(HERE)/harness/host_env.c $(HERE)/harness/validate_bootutil.c \
	$(FSLOAD_SRC) $(LIB_SRC) $(SRC_DIR)/fsload.c $(SRC_DIR)/update.c $(SRC_DIR)/mcuboot_crc32.c $(SRC_DIR)/record_ring.c $(SRC_DIR)/flash_map_backend.c \

TEST_CFLAGS := -std=gnu99 -O1 -g -w -fno-omit-frame-pointer -I$(TC)/include -I$(AS1)/include $(INC) $(LIB_DEFS) \
	-DMBEDTLS_CONFIG_FILE=\"mbedtls_config_asn1.h\" -fsanitize=address,undefined -fno-sanitize-recover=undefined \
	-ffunction-sections -fdata-sections
TEST_LDFLAGS := -Wl,--gc-sections

$(BUILD)/testdata/cases.txt: $(HERE)/make_testdata.py $(HERE)/containers.py
	$(PYTHON) $(HERE)/make_testdata.py $(BUILD)/testdata

# make_testdata.py also writes the public key table the test binaries link.
$(BUILD)/testdata/pubkey.c: $(BUILD)/testdata/cases.txt
	@test -f $@

testdata: $(BUILD)/testdata/cases.txt

$(BUILD)/test_swap: $(TEST_SRC) $(BOOTUTIL_SWAP_SRC) $(BUILD)/testdata/cases.txt | $(BUILD)
	$(CC) $(TEST_CFLAGS) $(filter %.c,$(TEST_SRC)) $(BOOTUTIL_SWAP_SRC) $(TEST_LDFLAGS) -o $@

$(BUILD)/test_single: $(TEST_SRC) $(BUILD)/testdata/cases.txt | $(BUILD)
	$(CC) $(TEST_CFLAGS) -DFUZZ_POLICY_SINGLE $(filter %.c,$(TEST_SRC)) $(TEST_LDFLAGS) -o $@

test-build: $(BUILD)/test_swap $(BUILD)/test_single

test: test-build
	$(BUILD)/test_swap $(BUILD)/testdata
	$(BUILD)/test_single $(BUILD)/testdata

# Reads of bootutil_img_validate() on a stream, with and without the head and tail windows of
# fsload_stream.c. Run: make measure
MEASURE_FLAGS := -DTRACE_STREAM -DVALIDATE_NO_CLASSIFY -Wl,--wrap=mcuboot_stream_area_read

$(BUILD)/measure_cache: $(TEST_SRC) $(BOOTUTIL_SWAP_SRC) $(HERE)/harness/trace_wrap.c $(BUILD)/testdata/cases.txt | $(BUILD)
	$(CC) $(TEST_CFLAGS) $(MEASURE_FLAGS) $(filter %.c,$(TEST_SRC)) $(BOOTUTIL_SWAP_SRC) $(HERE)/harness/trace_wrap.c $(TEST_LDFLAGS) -o $@

$(BUILD)/measure_nocache: $(TEST_SRC) $(BOOTUTIL_SWAP_SRC) $(HERE)/harness/trace_wrap.c $(BUILD)/testdata/cases.txt | $(BUILD)
	$(CC) $(TEST_CFLAGS) $(MEASURE_FLAGS) -DMCUBOOT_FSLOAD_HEAD_CACHE=0 -DMCUBOOT_FSLOAD_TAIL_CACHE=0 \
		$(filter %.c,$(TEST_SRC)) $(BOOTUTIL_SWAP_SRC) $(HERE)/harness/trace_wrap.c $(TEST_LDFLAGS) -o $@

measure: $(BUILD)/measure_cache $(BUILD)/measure_nocache
	$(BUILD)/measure_cache $(BUILD)/testdata | grep -E "trace|install_fat16 |install_gzip_raw |refused_bad_sig "
	$(BUILD)/measure_nocache $(BUILD)/testdata | grep -E "trace|install_fat16 |install_gzip_raw |refused_bad_sig "

clean:
	rm -rf $(BUILD)
