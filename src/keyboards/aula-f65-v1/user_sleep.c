#include "user_sleep.h"
#include "led.h"

#ifdef SLEEP_ENABLE

// smk's generic sleep is not used here: the board's own sleep paths
// (f65_power.c, the stock's 0x0F91 in the wireless positions and 0x9A5E in
// the middle position without a host) run from the 10 ms slow tick. A
// configured board stays awake while the host suspends the bus.
user_sleep_mode_t user_sleep_supported(void)
{
    return USER_SLEEP_NONE;
}

// Not called (USER_SLEEP_NONE): the board's own sleep paths hold the LEDs dark
// themselves (f65_power.c). Kept dark-first in case smk's sleep is ever used.
void user_sleep_prepare(void)
{
    led_hold(LED_HOLD_SLEEP);
}

void user_sleep_wake(void)
{
    led_release(LED_HOLD_SLEEP);
}

#endif // SLEEP_ENABLE
