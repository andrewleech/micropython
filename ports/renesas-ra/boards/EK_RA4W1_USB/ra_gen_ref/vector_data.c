/* generated vector source file - do not edit */
        #include "bsp_api.h"
/* Do not build these data structures if no interrupts are currently allocated because IAR will have build errors. */
        #if VECTOR_DATA_IRQ_COUNT > 0
BSP_DONT_REMOVE const fsp_vector_t g_vector_table[BSP_ICU_VECTOR_MAX_ENTRIES] BSP_PLACE_IN_SECTION(BSP_SECTION_APPLICATION_VECTORS) =
{
    [0] = r_icu_isr,                     /* ICU IRQ8 (External pin interrupt 8) */
    [1] = gpt_counter_overflow_isr,         /* GPT1 COUNTER OVERFLOW (Overflow) */
    [2] = agt_int_isr,         /* AGT0 INT (AGT interrupt) */
    [3] = usbfs_interrupt_handler,         /* USBFS INT (USBFS interrupt) */
    [4] = usbfs_d0fifo_handler,         /* USBFS FIFO 0 (DMA transfer request 0) */
    [5] = usbfs_d1fifo_handler,         /* USBFS FIFO 1 (DMA transfer request 1) */
    [6] = usbfs_resume_handler,         /* USBFS RESUME (USBFS resume interrupt) */
};
        #if BSP_FEATURE_ICU_HAS_IELSR
const bsp_interrupt_event_t g_interrupt_event_link_select[BSP_ICU_VECTOR_MAX_ENTRIES] =
{
    [0] = BSP_PRV_VECT_ENUM(EVENT_ICU_IRQ8, GROUP0),        /* ICU IRQ8 (External pin interrupt 8) */
    [1] = BSP_PRV_VECT_ENUM(EVENT_GPT1_COUNTER_OVERFLOW, GROUP1),        /* GPT1 COUNTER OVERFLOW (Overflow) */
    [2] = BSP_PRV_VECT_ENUM(EVENT_AGT0_INT, GROUP2),        /* AGT0 INT (AGT interrupt) */
    [3] = BSP_PRV_VECT_ENUM(EVENT_USBFS_INT, GROUP3),        /* USBFS INT (USBFS interrupt) */
    [4] = BSP_PRV_VECT_ENUM(EVENT_USBFS_FIFO_0, GROUP4),        /* USBFS FIFO 0 (DMA transfer request 0) */
    [5] = BSP_PRV_VECT_ENUM(EVENT_USBFS_FIFO_1, GROUP5),        /* USBFS FIFO 1 (DMA transfer request 1) */
    [6] = BSP_PRV_VECT_ENUM(EVENT_USBFS_RESUME, GROUP6),        /* USBFS RESUME (USBFS resume interrupt) */
};
        #endif
        #endif
