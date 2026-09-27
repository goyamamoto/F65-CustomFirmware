#include "settings.h"
#include "nvm.h"
#if DEBUG == 1
#    include "debug.h"
#endif

_Static_assert(sizeof(user_settings_t) <= NVM_CAPACITY, "user_settings_t too large for the settings record");

user_settings_t user_settings;

static bool settings_dirty;

bool settings_load(void)
{
#ifdef SETTINGS_LEGACY_LEN
    // A record of the length before the board's newer fields: its fields, the
    // newer ones cleared (the board fills them in).
    if (nvm_load((__xdata uint8_t *)&user_settings, (uint8_t)sizeof(user_settings))) {
        return true;
    }
    if (!nvm_load((__xdata uint8_t *)&user_settings, SETTINGS_LEGACY_LEN)) {
        return false;
    }
    for (uint8_t i = SETTINGS_LEGACY_LEN; i < (uint8_t)sizeof(user_settings); i++) {
        ((__xdata uint8_t *)&user_settings)[i] = 0;
    }
    return true;
#else
    return nvm_load((__xdata uint8_t *)&user_settings, (uint8_t)sizeof(user_settings));
#endif
}

#if DEBUG == 1
void settings_dump(void)
{
    dprintf("settings le=%02x lb=%02x ls=%02x lc=%02x ue=%02x ub=%02x us=%02x bat=%02x rf=%02x\r\n", user_settings.led_effect, user_settings.led_brightness, user_settings.led_speed, user_settings.led_color, user_settings.ul_effect, user_settings.ul_brightness, user_settings.ul_speed, user_settings.battery_indicator_on, user_settings.rf_link);
#    ifdef USJIS
    dprintf("settings usjis=%02x\r\n", user_settings.usjis_enabled);
#    endif
#    ifdef SETTINGS_OS_MODE
    dprintf("settings os_mac=%02x\r\n", user_settings.os_mac);
#    endif
}
#endif

void settings_save(void)
{
    settings_dirty = false; // clear first: a mark during the write is not lost

#if DEBUG == 1
    settings_dump();
#endif

    settings_save_pre();
    nvm_save((const __xdata uint8_t *)&user_settings, (uint8_t)sizeof(user_settings));
    settings_save_post();
}

void settings_mark_dirty(void)
{
    settings_dirty = true;
}

void settings_task(void)
{
    if (settings_dirty) {
        settings_save();
    }
}
