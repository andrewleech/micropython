MCU_SERIES = MIMXRT1011
MCU_VARIANT = MIMXRT1011DAE5A

MICROPY_FLOAT_IMPL = single
MICROPY_HW_FLASH_TYPE = qspi_nor_flash
MICROPY_HW_FLASH_SIZE = 0x1000000  # 16MB
MICROPY_HW_FLASH_CLK = kFlexSpiSerialClk_100MHz
MICROPY_HW_FLASH_QE_CMD = 0x31
MICROPY_HW_FLASH_QE_ARG = 0x02

USE_UF2_BOOTLOADER = 1

# Opt the board into the mboot bootloader.  Use `make USE_MBOOT=0 ...` to
# build firmware that targets BootROM/UF2 directly without the mboot stage.
USE_MBOOT ?= 1
ifeq ($(USE_MBOOT),1)
# mboot occupies the first 64 KiB of QSPI flash; the application must be
# linked to start above this reservation.
MBOOT_FLASH_SIZE ?= 0x10000
endif

JLINK_PATH ?= /media/RT1010-EVK/
JLINK_COMMANDER_SCRIPT = $(BUILD)/script.jlink

ifdef JLINK_IP
JLINK_CONNECTION_SETTINGS = -IP $(JLINK_IP)
else
JLINK_CONNECTION_SETTINGS = -USB
endif

deploy_jlink: $(BUILD)/firmware.hex
	$(Q)$(TOUCH) $(JLINK_COMMANDER_SCRIPT)
	$(ECHO) "ExitOnError 1" > $(JLINK_COMMANDER_SCRIPT)
	$(ECHO) "speed auto" >> $(JLINK_COMMANDER_SCRIPT)
	$(ECHO) "r" >> $(JLINK_COMMANDER_SCRIPT)
	$(ECHO) "st" >> $(JLINK_COMMANDER_SCRIPT)
	$(ECHO) "loadfile \"$(realpath $(BUILD)/firmware.hex)\"" >> $(JLINK_COMMANDER_SCRIPT)
	$(ECHO) "qc" >> $(JLINK_COMMANDER_SCRIPT)
	$(JLINK_PATH)JLinkExe -device $(MCU_VARIANT) -if SWD $(JLINK_CONNECTION_SETTINGS) -CommanderScript $(JLINK_COMMANDER_SCRIPT)

deploy: $(BUILD)/firmware.bin
	cp $< $(JLINK_PATH)
