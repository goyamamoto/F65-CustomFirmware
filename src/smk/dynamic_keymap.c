// SPDX-License-Identifier: GPL-2.0-or-later
// The VIA keymap store. The interface (get / set by position and VIA's byte
// buffer, two bytes per key with the high byte first) follows QMK's
// quantum/dynamic_keymap.c, Copyright 2017 Jason Williams (Wilba).

#include "dynamic_keymap.h"
#include "layout.h"
#include "keycodes.h"
#include "settings.h"
#include "tick.h"
#include "flash.h"
#include "nvm_hw.h"
#include "watchdog.h"
#include <stdbool.h>
#include <stdint.h>

#define DK_KEYS ((uint16_t)DYNAMIC_KEYMAP_LAYER_COUNT * MATRIX_ROWS * MATRIX_COLS)

// The record in flash: a header, the keymap as it is in XRAM (little-endian
// keycodes, layer / row / column order), a 16-bit sum of the keymap's bytes.
// The two magic bytes are programmed last, so a record cut short by a power
// loss is never taken: the built-in keymap is used instead.
#define DK_MAGIC0     0x56u // 'V'
#define DK_MAGIC1     0x4Bu // 'K'
#define DK_HDR        8u    // magic[2], layers, rows, cols, 0, defaults sum (lo, hi)
#define DK_PAYLOAD_AT (DYNAMIC_KEYMAP_ADDR + DK_HDR)
#define DK_SUM_AT     (DK_PAYLOAD_AT + DK_KEYS * 2u)
#define DK_RECORD_LEN (DK_HDR + DK_KEYS * 2u + 2u)

#define DK_CHUNK 128u

#ifndef DYNAMIC_KEYMAP_SAVE_SCANS
// About 0.5 s on the F65 (a scan every ~1.4 ms in USB mode, longer with the
// LED subframes): VIA writes a whole layout in many commands, saved once.
#    define DYNAMIC_KEYMAP_SAVE_SCANS 400u
#endif

_Static_assert((DYNAMIC_KEYMAP_ADDR % DYNAMIC_KEYMAP_SECTOR_SIZE) == 0, "the keymap store starts on a flash sector");
_Static_assert((DYNAMIC_KEYMAP_SIZE % DYNAMIC_KEYMAP_SECTOR_SIZE) == 0, "the keymap store is whole sectors");
_Static_assert(DYNAMIC_KEYMAP_ADDR + DYNAMIC_KEYMAP_SIZE <= NVM_BASE, "the keymap store stays below the settings sector");
_Static_assert(DK_RECORD_LEN <= DYNAMIC_KEYMAP_SIZE, "the keymap record fits its sectors");

static __xdata uint16_t dk_map[DYNAMIC_KEYMAP_LAYER_COUNT][MATRIX_ROWS][MATRIX_COLS];
static __xdata uint8_t  dk_hdr[DK_HDR];
static bool             dk_dirty;
static uint16_t         dk_changed_at;

#define DK_BYTES ((__xdata uint8_t *)dk_map)

// The keymap's loops kick the watchdog: at power-up they run before the main
// loop's first kick.
static uint16_t dk_payload_sum(void)
{
    uint16_t sum = 0;
    watchdog_kick();
    for (uint16_t i = 0; i < DK_KEYS * 2u; i++) {
        sum += DK_BYTES[i];
    }
    watchdog_kick();
    return sum;
}

// A sum of the built-in keymap: a record saved by an image with other
// defaults (another layout) is not taken.
static uint16_t dk_defaults_sum(void)
{
    uint16_t sum = 0;
    for (uint8_t layer = 0; layer < DYNAMIC_KEYMAP_LAYER_COUNT; layer++) {
        watchdog_kick();
        for (uint8_t row = 0; row < MATRIX_ROWS; row++) {
            for (uint8_t col = 0; col < MATRIX_COLS; col++) {
                const uint16_t k = keymaps[layer][row][col];
                sum += (uint8_t)k;
                sum += (uint8_t)(k >> 8);
            }
        }
    }
    return sum;
}

static void dk_fill_header(uint16_t defaults_sum)
{
    dk_hdr[0] = DK_MAGIC0;
    dk_hdr[1] = DK_MAGIC1;
    dk_hdr[2] = DYNAMIC_KEYMAP_LAYER_COUNT;
    dk_hdr[3] = MATRIX_ROWS;
    dk_hdr[4] = MATRIX_COLS;
    dk_hdr[5] = 0;
    dk_hdr[6] = (uint8_t)defaults_sum;
    dk_hdr[7] = (uint8_t)(defaults_sum >> 8);
}

static void dk_load_defaults(void)
{
    for (uint8_t layer = 0; layer < DYNAMIC_KEYMAP_LAYER_COUNT; layer++) {
        watchdog_kick();
        for (uint8_t row = 0; row < MATRIX_ROWS; row++) {
            for (uint8_t col = 0; col < MATRIX_COLS; col++) {
                dk_map[layer][row][col] = keymaps[layer][row][col];
            }
        }
    }
}

static bool dk_header_valid(uint16_t defaults_sum)
{
    static __xdata uint8_t found[DK_HDR];
    flash_read_into(NVM_WINDOW, DYNAMIC_KEYMAP_ADDR, found, DK_HDR);
    dk_fill_header(defaults_sum);
    for (uint8_t i = 0; i < DK_HDR; i++) {
        if (found[i] != dk_hdr[i]) {
            return false;
        }
    }
    return true;
}

static bool dk_sum_matches(void)
{
    static __xdata uint8_t sum[2];
    flash_read_into(NVM_WINDOW, DK_SUM_AT, sum, 2);
    const uint16_t want = dk_payload_sum();
    return sum[0] == (uint8_t)want && sum[1] == (uint8_t)(want >> 8);
}

static bool dk_record_matches(void)
{
    for (uint16_t i = 0; i < DK_KEYS * 2u; i += DK_CHUNK) {
        watchdog_kick();
        const uint16_t left = DK_KEYS * 2u - i;
        if (!flash_matches(NVM_WINDOW, (uint16_t)(DK_PAYLOAD_AT + i), DK_BYTES + i, (uint8_t)(left < DK_CHUNK ? left : DK_CHUNK))) {
            return false;
        }
    }
    return true;
}

static bool dk_load_record(void)
{
    const uint16_t defaults_sum = dk_defaults_sum();
    if (!dk_header_valid(defaults_sum)) {
        return false;
    }
    for (uint16_t i = 0; i < DK_KEYS * 2u; i += DK_CHUNK) {
        const uint16_t left = DK_KEYS * 2u - i;
        watchdog_kick();
        flash_read_into(NVM_WINDOW, (uint16_t)(DK_PAYLOAD_AT + i), DK_BYTES + i, (uint8_t)(left < DK_CHUNK ? left : DK_CHUNK));
    }
    return dk_sum_matches();
}

static void dk_erase(void)
{
    for (uint16_t at = 0; at < DYNAMIC_KEYMAP_SIZE; at += DYNAMIC_KEYMAP_SECTOR_SIZE) {
        flash_erase(NVM_WINDOW, (uint16_t)(DYNAMIC_KEYMAP_ADDR + at));
    }
}

static bool dk_is_defaults(void)
{
    for (uint8_t layer = 0; layer < DYNAMIC_KEYMAP_LAYER_COUNT; layer++) {
        watchdog_kick();
        for (uint8_t row = 0; row < MATRIX_ROWS; row++) {
            for (uint8_t col = 0; col < MATRIX_COLS; col++) {
                if (dk_map[layer][row][col] != keymaps[layer][row][col]) {
                    return false;
                }
            }
        }
    }
    return true;
}

// The keymap as it is now, to flash. The built-in keymap is kept as no record
// at all (the sectors erased); anything else as a record, unless the flash
// already holds it.
static void dk_save(void)
{
    const uint16_t defaults_sum = dk_defaults_sum();
    const bool     valid        = dk_header_valid(defaults_sum);

    if (dk_is_defaults()) {
        if (!valid) {
            return;
        }
        settings_save_pre();
        dk_erase();
        settings_save_post();
        return;
    }
    if (valid && dk_record_matches() && dk_sum_matches()) {
        return;
    }

    settings_save_pre();
    dk_erase();
    // header without the magic, keymap, sum; then the magic
    dk_fill_header(defaults_sum);
    flash_program_from(NVM_WINDOW, DYNAMIC_KEYMAP_ADDR + 2u, dk_hdr + 2, DK_HDR - 2u);
    for (uint16_t i = 0; i < DK_KEYS * 2u; i += DK_CHUNK) {
        const uint16_t left = DK_KEYS * 2u - i;
        flash_program_from(NVM_WINDOW, (uint16_t)(DK_PAYLOAD_AT + i), DK_BYTES + i, (uint8_t)(left < DK_CHUNK ? left : DK_CHUNK));
    }
    static __xdata uint8_t sum[2];
    const uint16_t         payload_sum = dk_payload_sum();
    sum[0]                             = (uint8_t)payload_sum;
    sum[1]                             = (uint8_t)(payload_sum >> 8);
    flash_program_from(NVM_WINDOW, DK_SUM_AT, sum, 2);
    flash_program_from(NVM_WINDOW, DYNAMIC_KEYMAP_ADDR, dk_hdr, 2);
    settings_save_post();
}

static void dk_changed(void)
{
    dk_dirty      = true;
    dk_changed_at = tick_scans();
#ifdef KB_KEYMAP_CHANGED_HOOK
    kb_keymap_changed();
#endif
}

void dynamic_keymap_init(void)
{
    dk_dirty = false;
    if (!dk_load_record()) {
        dk_load_defaults();
    }
}

uint16_t dynamic_keymap_keycode(uint8_t layer, uint8_t row, uint8_t col)
{
    if (layer >= DYNAMIC_KEYMAP_LAYER_COUNT || row >= MATRIX_ROWS || col >= MATRIX_COLS) {
        return KC_NO;
    }
    return dk_map[layer][row][col];
}

void dynamic_keymap_set_keycode(uint8_t layer, uint8_t row, uint8_t col, uint16_t keycode)
{
    if (layer >= DYNAMIC_KEYMAP_LAYER_COUNT || row >= MATRIX_ROWS || col >= MATRIX_COLS) {
        return;
    }
    dk_map[layer][row][col] = keycode;
    dk_changed();
}

void dynamic_keymap_reset(void)
{
    dk_load_defaults();
    dk_changed();
}

// VIA's offset o is key o / 2 in layer / row / column order; the even byte is
// the keycode's high byte. The offset stops at 0xFFFF, so a request near the
// end of the 16-bit range does not wrap back into the keymap.
void dynamic_keymap_get_buffer(uint16_t offset, uint8_t size, __xdata uint8_t *data)
{
    for (uint8_t i = 0; i < size; i++, offset = (offset == 0xFFFFu) ? offset : (uint16_t)(offset + 1u)) {
        if (offset < DYNAMIC_KEYMAP_BUFFER_SIZE) {
            data[i] = DK_BYTES[(offset & ~1u) + ((offset & 1u) ? 0u : 1u)];
        } else {
            data[i] = 0;
        }
    }
}

void dynamic_keymap_set_buffer(uint16_t offset, uint8_t size, const __xdata uint8_t *data)
{
    bool changed = false;
    for (uint8_t i = 0; i < size; i++, offset = (offset == 0xFFFFu) ? offset : (uint16_t)(offset + 1u)) {
        if (offset < DYNAMIC_KEYMAP_BUFFER_SIZE) {
            DK_BYTES[(offset & ~1u) + ((offset & 1u) ? 0u : 1u)] = data[i];
            changed                                              = true;
        }
    }
    if (changed) {
        dk_changed();
    }
}

void dynamic_keymap_task(void)
{
    if (dk_dirty && (uint16_t)(tick_scans() - dk_changed_at) >= DYNAMIC_KEYMAP_SAVE_SCANS) {
        dk_dirty = false;
        dk_save();
    }
}
