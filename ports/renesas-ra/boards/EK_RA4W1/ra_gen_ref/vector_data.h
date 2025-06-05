/* generated vector header file - do not edit */
        #ifndef VECTOR_DATA_H
        #define VECTOR_DATA_H
        #ifdef __cplusplus
        extern "C" {
        #endif
                /* Number of interrupts allocated */
        #ifndef VECTOR_DATA_IRQ_COUNT
        #define VECTOR_DATA_IRQ_COUNT    (7)
        #endif
        /* ISR prototypes */
        void r_icu_isr(void);
        void gpt_counter_overflow_isr(void);
        void agt_int_isr(void);
        void usbfs_interrupt_handler(void);
        void usbfs_d0fifo_handler(void);
        void usbfs_d1fifo_handler(void);
        void usbfs_resume_handler(void);

        /* Vector table allocations */
        #define VECTOR_NUMBER_ICU_IRQ8 ((IRQn_Type) 0) /* ICU IRQ8 (External pin interrupt 8) */
        #define ICU_IRQ8_IRQn          ((IRQn_Type) 0) /* ICU IRQ8 (External pin interrupt 8) */
        #define VECTOR_NUMBER_GPT1_COUNTER_OVERFLOW ((IRQn_Type) 1) /* GPT1 COUNTER OVERFLOW (Overflow) */
        #define GPT1_COUNTER_OVERFLOW_IRQn          ((IRQn_Type) 1) /* GPT1 COUNTER OVERFLOW (Overflow) */
        #define VECTOR_NUMBER_AGT0_INT ((IRQn_Type) 2) /* AGT0 INT (AGT interrupt) */
        #define AGT0_INT_IRQn          ((IRQn_Type) 2) /* AGT0 INT (AGT interrupt) */
        #define VECTOR_NUMBER_USBFS_INT ((IRQn_Type) 3) /* USBFS INT (USBFS interrupt) */
        #define USBFS_INT_IRQn          ((IRQn_Type) 3) /* USBFS INT (USBFS interrupt) */
        #define VECTOR_NUMBER_USBFS_FIFO_0 ((IRQn_Type) 4) /* USBFS FIFO 0 (DMA transfer request 0) */
        #define USBFS_FIFO_0_IRQn          ((IRQn_Type) 4) /* USBFS FIFO 0 (DMA transfer request 0) */
        #define VECTOR_NUMBER_USBFS_FIFO_1 ((IRQn_Type) 5) /* USBFS FIFO 1 (DMA transfer request 1) */
        #define USBFS_FIFO_1_IRQn          ((IRQn_Type) 5) /* USBFS FIFO 1 (DMA transfer request 1) */
        #define VECTOR_NUMBER_USBFS_RESUME ((IRQn_Type) 6) /* USBFS RESUME (USBFS resume interrupt) */
        #define USBFS_RESUME_IRQn          ((IRQn_Type) 6) /* USBFS RESUME (USBFS resume interrupt) */
        #ifdef __cplusplus
        }
        #endif
        #endif /* VECTOR_DATA_H */