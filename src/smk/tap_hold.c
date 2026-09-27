#include "tap_hold.h"

#ifdef TAP_HOLD

#    include "matrix.h"
#    include "keycodes.h"
#    include "tick.h"

// There is no millisecond clock, so the tapping term counts matrix scans
// (tick_scans()). 285 scans were meant as QMK's 200 ms at ~0.7 ms per scan;
// at the ~1.4 ms per scan later measured on an Air75 (wireless) they are
// about 0.4 s. The Air75 keeps 285, which felt right on the board; a board
// can set its own TAPPING_TERM_SCANS.
#    ifndef TAPPING_TERM_SCANS
#        define TAPPING_TERM_SCANS 285
#    endif

#    define TAP_HOLD_KEYS 2 // mod-tap keys that can be down at the same time

typedef enum {
    TH_FREE = 0,
    TH_PENDING, // down, not decided yet
    TH_HOLD,    // decided: acting as its modifier
} th_state_t;

typedef struct {
    uint8_t  state;
    uint8_t  row;
    uint8_t  col;
    uint16_t keycode;
    uint16_t since; // scan count at key-down
} th_key_t;

static th_key_t keys[TAP_HOLD_KEYS];

// The modifier keycode (KC_LEFT_CTRL .. KC_RIGHT_GUI) for a mod-tap key with one modifier.
static uint16_t hold_keycode(uint16_t keycode)
{
    const uint8_t mods = QK_MOD_TAP_GET_MODS(keycode);
    uint8_t       kc   = KC_LEFT_CTRL;
    if (mods & MOD_LGUI & 0x0F) {
        kc += 3;
    } else if (mods & MOD_LALT & 0x0F) {
        kc += 2;
    } else if (mods & MOD_LSFT & 0x0F) {
        kc += 1;
    }
    if (mods & 0x10) {
        kc += 4; // right-hand modifier
    }
    return kc;
}

static void resolve_hold(uint8_t i)
{
    keys[i].state = TH_HOLD;
    process_keycode(hold_keycode(keys[i].keycode), true);
}

// Index of the tracked key at (row, col), or TAP_HOLD_KEYS. (An index rather
// than a pointer: SDCC mis-compares some pointers with 0.)
static uint8_t find(uint8_t row, uint8_t col)
{
    for (uint8_t i = 0; i < TAP_HOLD_KEYS; i++) {
        if (keys[i].state != TH_FREE && keys[i].row == row && keys[i].col == col) {
            return i;
        }
    }
    return TAP_HOLD_KEYS;
}

bool tap_hold_process(uint8_t row, uint8_t col, uint16_t keycode, bool pressed)
{
    const uint8_t k = find(row, col);

    if (pressed) {
        // Another key going down makes every pending mod-tap key a hold first,
        // so the host sees the modifier before that key.
        for (uint8_t i = 0; i < TAP_HOLD_KEYS; i++) {
            if (keys[i].state == TH_PENDING && i != k) {
                resolve_hold(i);
            }
        }
        if (!IS_QK_MOD_TAP(keycode)) {
            return false;
        }
        for (uint8_t i = 0; i < TAP_HOLD_KEYS; i++) {
            if (keys[i].state == TH_FREE) {
                keys[i].state   = TH_PENDING;
                keys[i].row     = row;
                keys[i].col     = col;
                keys[i].keycode = keycode;
                keys[i].since   = tick_scans();
                return true;
            }
        }
        // More mod-tap keys down than tracked: this one is a plain modifier.
        process_keycode(hold_keycode(keycode), true);
        return true;
    }

    if (k == TAP_HOLD_KEYS) {
        if (IS_QK_MOD_TAP(keycode)) {
            process_keycode(hold_keycode(keycode), false); // the untracked overflow case above
            return true;
        }
        return false;
    }

    if (keys[k].state == TH_PENDING) {
        const uint8_t tap = QK_MOD_TAP_GET_TAP_KEYCODE(keys[k].keycode);
        process_keycode(tap, true);
        process_keycode(tap, false);
    } else {
        process_keycode(hold_keycode(keys[k].keycode), false);
    }
    keys[k].state = TH_FREE;
    return true;
}

void tap_hold_task(void)
{
    const uint16_t now = tick_scans();
    for (uint8_t i = 0; i < TAP_HOLD_KEYS; i++) {
        if (keys[i].state == TH_PENDING && (uint16_t)(now - keys[i].since) >= TAPPING_TERM_SCANS) {
            resolve_hold(i);
        }
    }
}

#endif // TAP_HOLD
