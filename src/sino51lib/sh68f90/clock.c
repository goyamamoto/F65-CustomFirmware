#include "clock.h"
#include "sh68f90.h"
#include "delay.h"
#include "watchdog.h"

void clock_init()
{
    CLKCON = _HFON;
    PLLCON = _PLLON;

#ifdef CLOCK_PLL_WAIT_BOUND
    // Bounded (a board that must reach its boot escape whatever the PLL
    // does): if PLLSTA never sets, switch over after CLOCK_PLL_WAIT_BOUND
    // polls anyway - the AULA F65 stock never polls PLLSTA at all, it switches
    // after a fixed delay (0xB06D-0xB084).
    for (uint16_t n = 0; n < (uint16_t)(CLOCK_PLL_WAIT_BOUND) && !(PLLCON & _PLLSTA); n++) {
        watchdog_kick();
    }
#else
    while (!(PLLCON & _PLLSTA)) {
        watchdog_kick();
    }
#endif

    PLLCON |= _PLLFS;
    CLKCON |= _FS;
}

static void hf_oscillator_settle(void)
{
    for (uint8_t i = 0; i < 200; i++) {
        // clang-format off
        __asm
            nop
        __endasm;
        // clang-format on
    }
}

void clock_wake_restart()
{
    // PLLSTA trips before a cold-started oscillator is USB-stable; settle by delay instead.
    __critical
    {
        CLKCON |= _HFON;
        PLLCON |= _PLLON;
        watchdog_kick();

        hf_oscillator_settle();

        PLLCON = _PLLFS | _PLLON;
        CLKCON = _FS | _HFON;
        watchdog_kick();
    }
}
