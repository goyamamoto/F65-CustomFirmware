#pragma once

#include "sh68f90.h"

void systick_interrupt_handler(void) __interrupt(_INT_TIMER2);
void usb_interrupt_handler(void) __interrupt(_INT_USB);
void int4_interrupt_handler(void) __interrupt(_INT_INT4);
#ifdef DEBUG_SINK_UART
void uart_interrupt_handler(void) __interrupt(_INT_EUART0);
#endif
#ifdef RF_EUART0
#    ifdef DEBUG_SINK_UART
#        error "RF_EUART0: EUART0 carries the radio link, it cannot be the debug sink too"
#    endif
// A board whose radio module sits on EUART0 defines this handler.
void rf_euart0_interrupt_handler(void) __interrupt(_INT_EUART0);
#endif
void pwm_interrupt_handler(void) __interrupt(_INT_PWM0);
#ifdef PWM4_MS_TICK
// A board that runs PWM4 as a 1 ms tick (as the AULA F65 stock firmware does) defines this handler.
void pwm4_ms_tick_interrupt_handler(void) __interrupt(_INT_PWM4);
#    define UNUSED_INTERRUPTS_PWM4(X)
#else
#    define UNUSED_INTERRUPTS_PWM4(X) X(pwm4, _INT_PWM4, IEN1, _EPWM4)
#endif

#if defined(DEBUG_SINK_UART) || defined(RF_EUART0)
#    define UNUSED_INTERRUPTS_EUART0(X)
#else
#    define UNUSED_INTERRUPTS_EUART0(X) X(euart0, _INT_EUART0, IEN1, _ES0)
#endif

#define UNUSED_INTERRUPTS(X)         \
    X(int3, _INT_INT3, IEN0, _EX3)   \
    X(int2, _INT_INT2, IEN0, _EX2)   \
    X(scm, _INT_SCM, IEN0, _ESCM)    \
    X(lpd, _INT_LPD, IEN0, _ELPD)    \
    X(spi, _INT_SPI, IEN0, _ESPI)    \
    X(pwm1, _INT_PWM1, IEN1, _EPWM1) \
    X(pwm2, _INT_PWM2, IEN1, _EPWM2) \
    X(pwm3, _INT_PWM3, IEN1, _EPWM3) \
    UNUSED_INTERRUPTS_PWM4(X)        \
    UNUSED_INTERRUPTS_EUART0(X)

#define UNUSED_INTERRUPT_DECL(name, vector, ien, bit) void name##_unused_interrupt_handler(void) __interrupt(vector);
UNUSED_INTERRUPTS(UNUSED_INTERRUPT_DECL)
#undef UNUSED_INTERRUPT_DECL

void interrupts_task(void);
