# Test of the SPI NOR chip model (spi_nor.c) behind the real drivers/memory/spiflash.c.
#
#   make test

TOP := $(abspath ../../../..)
BUILD ?= build
OUT := $(abspath $(BUILD))
HOST := $(abspath ..)

SRC := $(CURDIR)/spi_nor_test.c $(CURDIR)/spi_nor.c $(CURDIR)/port_flash.c $(HOST)/fake_flash.c \
	$(TOP)/drivers/memory/spiflash.c $(TOP)/shared/mcuboot/src/flash_spiflash.c
CFLAGS := -std=gnu99 -O1 -g -Wall -Wextra -Werror -Wno-unused-parameter -DFAKE_FLASH_NO_PORT=1 \
	-I$(CURDIR)/include -I$(TOP) -I$(TOP)/shared/mcuboot/include -I$(HOST) -I$(CURDIR)

.PHONY: test clean
test: $(OUT)/spi_nor_test
	$<

$(OUT)/spi_nor_test: $(SRC) $(wildcard $(CURDIR)/*.h $(HOST)/fake_flash.h)
	mkdir -p $(OUT)
	$(CC) $(CFLAGS) -o $@ $(SRC)

clean:
	rm -rf $(BUILD)
