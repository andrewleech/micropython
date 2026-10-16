/* Shared SRAM handoff and bootloader RAM windows for MIMXRT1052 and RT1176. */
#if defined(CPU_MIMXRT1052DVL6B)
MBOOT_SRAM_START = 0x20200000;
MBOOT_SRAM_END = 0x20240000;
#elif defined(CPU_MIMXRT1176DVMAA_cm7)
MBOOT_SRAM_START = 0x20240000;
MBOOT_SRAM_END = 0x20340000;
#else
#error "mboot_sram.ld.S: unsupported MIMXRT Mboot target"
#endif

MBOOT_BACKUP_START = MBOOT_SRAM_END - 0x800;
MBOOT_REQ_START = MBOOT_SRAM_END - 0x400;
MBOOT_REQ_SIZE = 0x400;
mboot_req_start = MBOOT_REQ_START;
mboot_req_end = MBOOT_REQ_START + MBOOT_REQ_SIZE;

ASSERT(MBOOT_BACKUP_START + 0x400 == MBOOT_REQ_START, "Mboot backup/request RAM reservation is inconsistent")
ASSERT(mboot_req_end == MBOOT_SRAM_END, "Mboot request RAM is not at the end of OCRAM")
