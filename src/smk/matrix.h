#pragma once

#include <stdint.h>
#include <stdbool.h>

void    matrix_init();
uint8_t matrix_task();

void matrix_scan_full();

// Run a keycode through the normal key path (board hooks, layout hooks, report).
void process_keycode(uint16_t qcode, bool pressed);

#ifdef VIA_ENABLE
// The debounced state of a row: bit c set while the key in column c is down.
uint16_t matrix_get_row(uint8_t row);
#endif

#if defined(VIA_ENABLE) && defined(TAP_HOLD)
// 1 while the layer in force is the one a held layer-tap key (LT) gave, 0 under
// an MO key or none.
extern __xdata uint8_t layer_tap_held;
#endif
