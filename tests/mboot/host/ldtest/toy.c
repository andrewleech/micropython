// A toy application image for the linker script demonstration of the Makefile: a vector table
// first in the image, a function and some data.

#include <stdint.h>

extern uint8_t toy_stack_top[];
extern void toy_start(void);

__attribute__((section(".isr_vector"), used)) const uintptr_t toy_vectors[2] = {
    (uintptr_t)toy_stack_top,
    (uintptr_t)toy_start,
};

volatile uint32_t toy_counter = 5;

void toy_start(void) {
    toy_counter++;
    for (;;) {
    }
}
