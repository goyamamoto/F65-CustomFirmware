#pragma once

#include <stdint.h>
#include <stdbool.h>

typedef struct {
    uint8_t led_effect;
    uint8_t led_brightness;
    uint8_t led_speed;
    uint8_t led_color;
    uint8_t ul_effect;
    uint8_t ul_brightness;
    uint8_t ul_speed;
    uint8_t battery_indicator_on;
    uint8_t rf_link;
#ifdef USJIS
    uint8_t usjis_enabled;
#endif
#ifdef SETTINGS_OS_MODE
    uint8_t os_mac; // boards without an OS switch: 1 = Mac base layer
#endif
#ifdef SETTINGS_F65_LIGHTING
    // aula-f65-v1 backlight and side lights (backlight.c), after every older
    // field: a record of the older length still loads (settings_load), and
    // bl_magic tells the board to fill these in.
    uint8_t bl_magic;
    uint8_t bl_on;
    uint8_t bl_effect;
    uint8_t bl_cfg[36]; // per effect 0-17: brightness, speed << 4 | colour
    uint8_t sl_effect;
    uint8_t sl_colour;
    uint8_t sl_brightness;
#endif
} user_settings_t;

#ifdef SETTINGS_F65_LIGHTING
#    define SETTINGS_LEGACY_LEN ((uint8_t)(sizeof(user_settings_t) - 42u)) // the record before bl_magic
#endif

extern user_settings_t user_settings;

bool settings_load(void);

void settings_save(void);

void settings_mark_dirty(void);

void settings_task(void);

void settings_save_pre(void);
void settings_save_post(void);

#if DEBUG == 1
void settings_dump(void);
#endif
