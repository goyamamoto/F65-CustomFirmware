#include "matrix.h"
#ifdef TAP_HOLD
#    include "tap_hold.h"
#endif
#include "report.h"
#include "debug.h"
#include "layout.h"
#include "user_layout.h"
#include "kb.h"
#ifdef USJIS
#    include "usjis.h"
#endif
#include "user_matrix.h"
#include "kbdef.h"
#include "host.h"
#include "delay.h"
#include "indicators.h"
#include "sleep.h"
#include <stdlib.h>
#include <stdbool.h>

typedef uint8_t matrix_col_t;

matrix_col_t matrix[MATRIX_COLS];
matrix_col_t matrix_previous[MATRIX_COLS];

volatile bool matrix_updated;

uint8_t action_layer;

uint8_t default_layer;

#ifdef LATCH_KEYCODES
// The keycode each key went down with, so its release undoes exactly that even
// if Fn or the base layer changed in between (a key held across Fn would
// otherwise be released as its other-layer keycode and stay stuck on the host).
static __xdata uint16_t latched_keycode[MATRIX_ROWS][MATRIX_COLS];
#endif

void matrix_init()
{
    action_layer   = 0;
    default_layer  = 0;
    matrix_updated = false;

    for (int i = 0; i < MATRIX_COLS; i++) {
        matrix[i]          = 0;
        matrix_previous[i] = 0;
    }
}

void set_default_layer(uint8_t layer)
{
    default_layer = layer;
}

static uint16_t resolve_keycode(uint16_t base, uint8_t row, uint8_t col)
{
    if (!action_layer) {
        return base;
    }

    const uint16_t overlay = keymaps[action_layer][row][col];
    return (overlay == KC_TRANSPARENT) ? base : overlay;
}

static void send_keycode(uint16_t qcode, bool pressed)
{
    if (IS_MODIFIER_KEYCODE(qcode)) {
        if (pressed) {
            add_mods(MOD_BIT((uint8_t)(qcode & 0xFF)));
        } else {
            del_mods(MOD_BIT((uint8_t)(qcode & 0xFF)));
        }
        send_keyboard_report();
        return;
    }

    if (IS_BASIC_KEYCODE(qcode)) {
        if (pressed) {
            add_key((uint8_t)(qcode & 0xFF));
        } else {
            del_key((uint8_t)(qcode & 0xFF));
        }
        send_keyboard_report();
        return;
    }

    if (IS_SYSTEM_KEYCODE(qcode)) {
        host_system_send(pressed ? keycode_to_system(qcode) : 0);
        return;
    }

    if (IS_CONSUMER_KEYCODE(qcode)) {
        host_consumer_send(pressed ? keycode_to_consumer(qcode) : 0);
        return;
    }
}

static void process_key_state(uint8_t row, uint8_t col, bool pressed)
{
    const uint16_t base = keymaps[default_layer][row][col];

    if (IS_QK_MOMENTARY(base)) {
#ifdef TAP_HOLD
        if (pressed) {
            tap_hold_process(row, col, base, true); // Fn is another key too: pending mod-taps become holds
        }
#endif
        if (pressed) {
            action_layer = QK_MOMENTARY_GET_LAYER(base);
#ifdef APPLE_FN
            report_apple_fn_hold(kb_layer_is_apple_fn(action_layer));
#endif
#ifdef KB_FN_LAYER_HOOK
            kb_fn_layer(true);
#endif
        } else {
            clear_keys();
            action_layer = 0;
#ifdef USJIS
            usjis_clear(); // the keys it tracked are gone from the report
#endif
#ifdef APPLE_FN
            report_apple_fn_hold(false);
#endif
#ifdef KB_FN_LAYER_HOOK
            kb_fn_layer(false);
#endif
#ifdef LATCH_KEYCODES
            send_keyboard_report(); // the host must see the cleared keys go up now
#endif
        }
        return;
    }

#ifdef LATCH_KEYCODES
    uint16_t qcode;
    if (pressed) {
        qcode                     = resolve_keycode(base, row, col);
        latched_keycode[row][col] = qcode;
    } else {
        qcode = latched_keycode[row][col];
    }
#else
    const uint16_t qcode = resolve_keycode(base, row, col);
#endif

#ifdef TAP_HOLD
    if (tap_hold_process(row, col, qcode, pressed)) {
        return;
    }
#endif

    process_keycode(qcode, pressed);
}

void process_keycode(uint16_t qcode, bool pressed)
{
    if (!kb_process_record(qcode, pressed)) {
        return;
    }

    if (!layout_process_record(qcode, pressed)) {
        return;
    }

    send_keycode(qcode, pressed);
}

#ifdef MATRIX_DEBOUNCE_SCANS
// Per-key debounce, as the stock F65 firmware does it (0x1DB7: a counter per key,
// the change taken after it has held for a few scans): a key changes state only
// once its new state has been read in MATRIX_DEBOUNCE_SCANS consecutive scans, so
// a bouncing contact gives one press and one release. Runs in the scan (the
// Timer2 interrupt); the fast path is a column with no pending change.
#    define MATRIX_ROW_MASK (uint8_t)((1u << MATRIX_ROWS) - 1)
static __xdata uint8_t db_count[MATRIX_COLS][MATRIX_ROWS];
static __xdata uint8_t db_pending[MATRIX_COLS]; // rows with a counter running

static matrix_col_t matrix_debounce(uint8_t col, matrix_col_t raw)
{
    matrix_col_t  state = matrix[col];
    const uint8_t diff  = (uint8_t)((raw ^ state) & MATRIX_ROW_MASK);
    if (!diff && !db_pending[col]) {
        return state;
    }
    uint8_t pending = 0;
    uint8_t mask    = 1;
    for (uint8_t row = 0; row < MATRIX_ROWS; row++, mask <<= 1) {
        if (diff & mask) {
            if (++db_count[col][row] >= MATRIX_DEBOUNCE_SCANS) {
                state ^= mask;
                db_count[col][row] = 0;
            } else {
                pending |= mask;
            }
        } else {
            db_count[col][row] = 0; // back to the debounced state: start over
        }
    }
    db_pending[col] = pending;
    return state;
}
#endif

void matrix_scan_full(void)
{
    indicators_pwm_disable();

    user_matrix_sinks_off();

    user_matrix_scan_pre();
    user_matrix_cols_deselect_all();

    for (uint8_t col = 0; col < MATRIX_COLS; col++) {
        user_matrix_col_select(col);

        delay_us(10); // let the row lines settle before sampling
        const uint8_t sample1 = user_matrix_read_rows();
        delay_us(10);
        const uint8_t sample2 = user_matrix_read_rows();

        if (sample1 == sample2) {
#ifdef MATRIX_DEBOUNCE_SCANS
            matrix[col] = matrix_debounce(col, (matrix_col_t)~sample1);
#else
            matrix[col] = ~sample1;
#endif
        }

        user_matrix_col_deselect(col);
    }

    user_matrix_scan_post();

    indicators_pwm_enable();

    matrix_updated = true;
}

uint8_t matrix_task()
{
#ifdef TAP_HOLD
    tap_hold_task();
#endif

    if (!matrix_updated) {
        return false;
    }

    // Snapshot the scan-written matrix[], then diff it against
    // matrix_previous[]. No lock needed: each column byte reads atomically, so a
    // concurrent scan lands cleanly on one side of the read - at worst a
    // transition is split across two main-loop iterations, never lost.
    matrix_col_t snapshot[MATRIX_COLS];
    matrix_updated = false;
    for (uint8_t i = 0; i < MATRIX_COLS; i++) {
        snapshot[i] = matrix[i];
    }

    bool matrix_changed = false;

    for (uint8_t col = 0; col < MATRIX_COLS; col++) {
        const matrix_col_t current_col = snapshot[col];
        const matrix_col_t col_changes = current_col ^ matrix_previous[col];
        if (!col_changes) {
            continue;
        }
        matrix_changed = true;
        sleep_note_activity(); // a key changed state; reset the inactivity timer

        matrix_col_t row_mask = 1;
        for (uint8_t row = 0; row < MATRIX_ROWS; row++, row_mask <<= 1) {
            if (col_changes & row_mask) {
                const bool key_pressed = current_col & row_mask;
                process_key_state(row, col, key_pressed);
            }
        }

        matrix_previous[col] = current_col;
    }

    return matrix_changed;
}
