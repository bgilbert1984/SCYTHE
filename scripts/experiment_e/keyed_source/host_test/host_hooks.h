#pragma once
// Included by keyed_source.ino when EXP_E_HOST_TEST is defined.
#include <stdint.h>
void host_pin_ref(bool); void host_pin_mon(bool); uint16_t host_tcnt1();
#define FAST_REF(on) host_pin_ref(on)
#define FAST_MON(on) host_pin_mon(on)
#define READ_TCNT1() host_tcnt1()
