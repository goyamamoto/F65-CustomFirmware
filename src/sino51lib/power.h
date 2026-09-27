#pragma once

typedef enum {
    POWERDOWN_KEEP_USB_ALIVE,
    POWERDOWN_RELEASE_USB,
} powerdown_mode_t;

// the caller must arm a wake source first or the core never comes back.
void power_enter_powerdown(powerdown_mode_t mode);

#ifdef POWER_INT4_WOKE_API
#    include <stdbool.h>
// True once after the key-wake interrupt (INT4) has fired; clears it.
bool power_take_int4_woke(void);
#endif
