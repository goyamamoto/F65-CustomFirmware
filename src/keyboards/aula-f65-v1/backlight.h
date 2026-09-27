#pragma once

#include <stdint.h>
#include <stdbool.h>

// Key backlight and side lights of the aula-f65-v1 (backlight.c): the stock V1
// effects, ported from the stock engine (the effect specification is in the
// author's analysis notes, not published), into led_fb8 (led.h).

#define BL_EFFECTS    18 // stock effect numbers 0-17 (0 off; 6, 9 and 14 are not in the cycle)
#define BL_MAGIC      0xB6
#define BL_SAVE_MS    3000  // a change is saved once, this long after the last one
#define BL_IDLE_OFF_MS 30000 // on battery: dark this long after the last key

void backlight_init(void);
// Main loop, every pass (indicators_render): at most one unit of effect work
// (a column, a ring, a few cells). False when a key changed this pass: the
// caller then leaves the LED table for the next pass too, so matrix_task gets
// to the report sooner.
bool backlight_task(void);
// indicators_render, once per ms: the battery display (Fn + B) takes every cell.
void backlight_battery_display(bool on);

void backlight_defaults(void);
void backlight_validate(void);
// The lighting keys (kb.c); false when `keycode` was one of them.
bool backlight_process_record(uint16_t keycode, bool pressed);
