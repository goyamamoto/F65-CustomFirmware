#pragma once

// The keymap VIA edits: a working copy in XRAM that the matrix reads, kept in
// the flash sectors at DYNAMIC_KEYMAP_ADDR (meson.build reserves them below the
// settings sector). Without a valid record there, the layout's built-in
// keymaps[] is the keymap.

#include "kbdef.h"
#include <stdbool.h>
#include <stdint.h>

#ifndef DYNAMIC_KEYMAP_LAYER_COUNT
#    error "the board sets DYNAMIC_KEYMAP_LAYER_COUNT (the layers of its keymaps[])"
#endif

// Bytes of the keymap as VIA addresses it: layer, row, column, two bytes each
// (high byte first).
#define DYNAMIC_KEYMAP_BUFFER_SIZE ((uint16_t)DYNAMIC_KEYMAP_LAYER_COUNT * MATRIX_ROWS * MATRIX_COLS * 2u)

void dynamic_keymap_init(void);

// KC_NO for a layer, row or column outside the keymap.
uint16_t dynamic_keymap_keycode(uint8_t layer, uint8_t row, uint8_t col);

void dynamic_keymap_set_keycode(uint8_t layer, uint8_t row, uint8_t col, uint16_t keycode);

// Back to the built-in keymaps[] (saved as such).
void dynamic_keymap_reset(void);

// VIA's bulk access; bytes past the end read as 0 and are not written.
void dynamic_keymap_get_buffer(uint16_t offset, uint8_t size, __xdata uint8_t *data);
void dynamic_keymap_set_buffer(uint16_t offset, uint8_t size, const __xdata uint8_t *data);

// Main loop: writes a changed keymap to flash once it has stayed unchanged for
// DYNAMIC_KEYMAP_SAVE_SCANS matrix scans.
void dynamic_keymap_task(void);

#ifdef KB_KEYMAP_CHANGED_HOOK
// The board, after any change of the keymap (to find its keys again).
void kb_keymap_changed(void);
#endif
