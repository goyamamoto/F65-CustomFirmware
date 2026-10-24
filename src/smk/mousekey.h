#pragma once

#include <stdint.h>
#include <stdbool.h>

// Mouse keys (KC_MS_UP .. KC_MS_ACCEL2 in keycodes.h), for boards that set
// MOUSEKEY_ENABLE and give MOUSEKEY_MS(), a 16-bit millisecond clock.
//
// As QMK's accelerated mouse keys with its default settings, except a step
// interval of 16 ms where QMK has 20 (mousekey.c): a movement key
// moves the pointer one step at once, again after MOUSEKEY_DELAY ms, then every
// MOUSEKEY_INTERVAL ms, the step growing from MOUSEKEY_MOVE_DELTA to
// MOUSEKEY_MOVE_DELTA * MOUSEKEY_MAX_SPEED over MOUSEKEY_TIME_TO_MAX steps; two
// directions at once move diagonally at the same speed. The wheel keys work
// the same way with the MOUSEKEY_WHEEL_ settings. Held, KC_MS_ACCEL0 / 1 / 2
// fix the speed at a quarter, a half or all of the maximum. The buttons go
// down and up with their keys. Each step and each button change is one mouse
// report (host_mouse_send).

void mousekey_on(uint16_t keycode);
void mousekey_off(uint16_t keycode);

// Called from the main loop: the repeated steps.
void mousekey_task(void);

// Every mouse key up (the board releases everything): the movement stops, and
// the buttons that were down go up in one report.
void mousekey_clear(void);

// One report with the buttons as they are and no movement (the USB code sends
// it after a suspend that swallowed a button change).
void mousekey_send_buttons(void);

// True while a movement, wheel or button key is down (a drag or a scroll in
// progress counts as activity for a board's idle timer).
bool mousekey_active(void);
