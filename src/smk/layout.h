#pragma once

#include "kbdef.h"
#include <stdbool.h>

extern const uint16_t keymaps[][MATRIX_ROWS][MATRIX_COLS];

#ifdef VIA_ENABLE
// The keymap in use: VIA's (dynamic_keymap.c), which starts as keymaps[].
#    include "dynamic_keymap.h"
#    define KEYMAP_KEYCODE(layer, row, col) dynamic_keymap_keycode((layer), (row), (col))
#else
#    define KEYMAP_KEYCODE(layer, row, col) (keymaps[(layer)][(row)][(col)])
#endif

void set_default_layer(uint8_t layer);
