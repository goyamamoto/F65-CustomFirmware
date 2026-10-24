#pragma once

#include "kbdef.h"
#include <stdint.h>
#include <stdbool.h>

// Mod-tap keys (MT(), LGUI_T() ... in keycodes.h), for boards that set TAP_HOLD.
//
// A mod-tap key sends its tap keycode when it is released within the tapping
// term with no other key pressed meanwhile, and acts as its modifier otherwise:
// after the tapping term, or as soon as another key goes down while it is held
// (QMK's HOLD_ON_OTHER_KEY_PRESS). The tap and the modifier go through the
// normal key path, so board hooks (US-JIS, ...) see them like any other key.

// There is no millisecond clock, so the tapping term counts matrix scans
// (tick_scans()). 285 scans were meant as QMK's 200 ms at ~0.7 ms per scan;
// at the ~1.4 ms per scan later measured on an Air75 (wireless) they are
// about 0.4 s. The Air75 keeps 285, which felt right on the board; a board
// can set its own TAPPING_TERM_SCANS. The layer-tap keys (matrix.c) use the
// same term.
#ifndef TAPPING_TERM_SCANS
#    define TAPPING_TERM_SCANS 285
#endif

// Called by the matrix for every key event before it is processed; returns
// true when the event belongs to a mod-tap key and was consumed here.
bool tap_hold_process(uint8_t row, uint8_t col, uint16_t keycode, bool pressed);

// Called from the main loop; turns a pending key into a hold once its tapping
// term has passed.
void tap_hold_task(void);
