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
#ifdef MOUSEKEY_ENABLE
#    include "mousekey.h"
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

#ifdef VIA_ENABLE
// Where the layer key (MO) that is down went down, 0xFF for none: its release
// ends the layer even if VIA changed the keymap or the base layer changed
// while it was held.
static uint8_t layer_key_row = 0xFF;
static uint8_t layer_key_col;
#endif

#if defined(VIA_ENABLE) && defined(TAP_HOLD)
#    define LAYER_TAP_KEYS
#    include "tick.h"
// A layer-tap key (LT(layer, kc), which VIA offers) on a base layer: tapped it
// sends kc, held it is the layer, as MO. It is undecided while it is down, and
// the key events after it wait in order. Of those, the first that decides it
// counts: its own release makes it a tap, the release of a key that went down
// after it makes it the layer (QMK's PERMISSIVE_HOLD); so do the tapping term
// and a ninth waiting event. The waiting events then go on in their order,
// under the layer or after the tap; a layer-tap key among them is undecided in
// turn, with the events after it waiting for it.
#    define LT_QUEUE 8
static __xdata uint8_t  lt_pending; // a layer-tap key is undecided
static __xdata uint8_t  lt_row;
static __xdata uint8_t  lt_col;
static __xdata uint16_t lt_keycode;
static __xdata uint16_t lt_since; // scan count at key-down
static __xdata uint8_t  lt_len;
static __xdata uint8_t  lt_queue[LT_QUEUE]; // the waiting events: pressed << 7 | row << 4 | col
_Static_assert(MATRIX_ROWS <= 8 && MATRIX_COLS <= 16, "a waiting event is one byte");
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

    const uint16_t overlay = KEYMAP_KEYCODE(action_layer, row, col);
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

#ifdef VIA_ENABLE
    // A modified key (LCTL(KC_C) and the like, which VIA offers): its
    // modifiers go out with the key and are taken back with it.
    if (IS_QK_MODS(qcode) && IS_BASIC_KEYCODE(QK_MODS_GET_BASIC_KEYCODE(qcode))) {
        const uint8_t m    = QK_MODS_GET_MODS(qcode);
        const uint8_t mods = (m & 0x10) ? (uint8_t)((m & 0x0F) << 4) : m;
        const uint8_t key  = (uint8_t)QK_MODS_GET_BASIC_KEYCODE(qcode);
        if (pressed) {
            add_key(key);
            if (is_key_pressed(&keyboard_report, key)) { // not when the report is full
                add_mod_key(key, mods);
            }
        } else if (!del_mod_key(key, mods)) {
            del_key(key); // the last modified key with this basic key
        }
        send_keyboard_report();
        return;
    }
#endif

    if (IS_SYSTEM_KEYCODE(qcode)) {
        host_system_send(pressed ? keycode_to_system(qcode) : 0);
        return;
    }

    if (IS_CONSUMER_KEYCODE(qcode)) {
        host_consumer_send(pressed ? keycode_to_consumer(qcode) : 0);
        return;
    }

#ifdef MOUSEKEY_ENABLE
    if (IS_MOUSE_KEYCODE(qcode)) {
        if (pressed) {
            mousekey_on(qcode);
        } else {
            mousekey_off(qcode);
        }
    }
#endif
}

#ifdef LAYER_TAP_KEYS
static void process_key_state(uint8_t row, uint8_t col, bool pressed);

// A layer-tap key held: its layer, as an MO key's press (process_key_state);
// its release ends the layer there.
static void lt_layer_on(uint8_t row, uint8_t col, uint8_t layer)
{
    action_layer  = layer;
    layer_key_row = row;
    layer_key_col = col;
#    ifdef LATCH_KEYCODES
    latched_keycode[row][col] = KC_NO; // released as nothing if another layer key took over
#    endif
#    ifdef APPLE_FN
    report_apple_fn_hold(kb_layer_is_apple_fn(action_layer));
#    endif
#    ifdef KB_FN_LAYER_HOOK
    kb_fn_layer(true);
#    endif
}

// The undecided layer-tap key becomes its layer, or a tap of its key.
static void lt_decide(bool hold)
{
    lt_pending = 0;
    if (hold) {
        lt_layer_on(lt_row, lt_col, QK_LAYER_TAP_GET_LAYER(lt_keycode));
    } else {
        const uint8_t tap = QK_LAYER_TAP_GET_TAP_KEYCODE(lt_keycode);
        process_keycode(tap, true);
        process_keycode(tap, false);
    }
}

// Takes waiting event i out of the queue.
static void lt_take(uint8_t i)
{
    lt_len--;
    for (; i < lt_len; i++) {
        lt_queue[i] = lt_queue[i + 1];
    }
}

// While a layer-tap key is undecided: whether a release goes on at once
// instead of waiting. It must be that of a key that went down before the
// layer-tap key: its press is not among the first `upto` waiting events (the
// ones before it), and it is not the layer-tap key itself or the layer key in
// force; such a release decides nothing, and held until the decision a fast
// key repeat on the host would repeat its key. Only keys that send a key of
// their own go on at once: the basic keys and the system and consumer keys.
// Every other key waits - modifiers, modified keys, mod-taps, layer keys,
// mouse keys and the board's own keys - since what it does can change what the
// keys waiting after it send.
static bool lt_release_now(uint8_t row, uint8_t col, uint8_t upto)
{
    const uint8_t ev = (uint8_t)((uint8_t)(row << 4) | col);

    if (lt_pending && row == lt_row && col == lt_col) {
        return false; // its own release decides it
    }
    for (uint8_t i = 0; i < upto; i++) {
        if (lt_queue[i] == (uint8_t)(0x80 | ev)) {
            return false; // went down after it
        }
    }
    if (row == layer_key_row && col == layer_key_col) {
        return false;
    }
#    ifdef LATCH_KEYCODES
    const uint16_t k = latched_keycode[row][col];
#    else
    const uint16_t k = resolve_keycode(KEYMAP_KEYCODE(default_layer, row, col), row, col);
#    endif
    return IS_BASIC_KEYCODE(k) || IS_SYSTEM_KEYCODE(k) || IS_CONSUMER_KEYCODE(k);
}

// Runs the waiting events in order while no layer-tap key is undecided; while
// one is, decides it by the first waiting event that can, or leaves them.
static void lt_run(void)
{
    while (lt_len) {
        if (lt_pending) {
            // waiting releases of keys that went down before this layer-tap
            // key (their presses went on before it was pressed or decided)
            for (uint8_t r = 0; r < lt_len;) {
                const uint8_t ev = lt_queue[r];
                if (!(ev & 0x80) && lt_release_now((uint8_t)((ev >> 4) & 0x07), (uint8_t)(ev & 0x0F), r)) {
                    lt_take(r);
                    process_key_state((uint8_t)((ev >> 4) & 0x07), (uint8_t)(ev & 0x0F), false);
                } else {
                    r++;
                }
            }
            if (!lt_len) {
                return;
            }
            const uint8_t self = (uint8_t)((uint8_t)(lt_row << 4) | lt_col); // its own release
            bool          hold = false;
            uint8_t       i;
            for (i = 0; i < lt_len && !hold; i++) {
                const uint8_t ev = lt_queue[i];
                if (ev == self) {
                    break;
                }
                if (!(ev & 0x80)) {
                    // a release: of a key that went down after the layer-tap key?
                    for (uint8_t j = 0; j < i; j++) {
                        if (lt_queue[j] == (uint8_t)(0x80 | ev)) {
                            hold = true;
                        }
                    }
                }
            }
            if (hold) {
                lt_decide(true);
            } else if (i < lt_len) {
                lt_take(i);
                lt_decide(false);
            } else {
                return; // still undecided
            }
            continue;
        }
        const uint8_t ev = lt_queue[0];
        lt_take(0);
        process_key_state((uint8_t)((ev >> 4) & 0x07), (uint8_t)(ev & 0x0F), ev & 0x80);
    }
}

// A key event from the matrix: straight on, or into the queue while a
// layer-tap key is undecided.
static void lt_feed(uint8_t row, uint8_t col, bool pressed)
{
    if (!lt_pending && !lt_len) {
        process_key_state(row, col, pressed);
        return;
    }
    if (!pressed && lt_release_now(row, col, lt_len)) {
        process_key_state(row, col, false);
        return;
    }
    if (lt_len == LT_QUEUE) {
        lt_decide(true); // a ninth waiting event
        lt_run();
    }
    lt_queue[lt_len++] = (uint8_t)((pressed ? 0x80 : 0) | (uint8_t)(row << 4) | col);
    lt_run();
}
#endif

static void process_key_state(uint8_t row, uint8_t col, bool pressed)
{
    const uint16_t base = KEYMAP_KEYCODE(default_layer, row, col);

#ifdef LAYER_TAP_KEYS
    if (pressed && IS_QK_LAYER_TAP(base)) {
        tap_hold_process(row, col, base, true); // another key: pending mod-taps become holds
        lt_pending = 1;
        lt_row     = row;
        lt_col     = col;
        lt_keycode = base;
        lt_since   = tick_scans();
        return;
    }
#endif

#ifdef VIA_ENABLE
    if (pressed ? IS_QK_MOMENTARY(base) : (row == layer_key_row && col == layer_key_col)) {
#else
    if (IS_QK_MOMENTARY(base)) {
#endif
#ifdef TAP_HOLD
        if (pressed) {
            tap_hold_process(row, col, base, true); // Fn is another key too: pending mod-taps become holds
        }
#endif
        if (pressed) {
            action_layer = QK_MOMENTARY_GET_LAYER(base);
#ifdef VIA_ENABLE
            layer_key_row = row;
            layer_key_col = col;
#    ifdef LATCH_KEYCODES
            latched_keycode[row][col] = KC_NO; // released as nothing if another layer key took over
#    endif
#endif
#ifdef APPLE_FN
            report_apple_fn_hold(kb_layer_is_apple_fn(action_layer));
#endif
#ifdef KB_FN_LAYER_HOOK
            kb_fn_layer(true);
#endif
        } else {
            clear_keys();
            action_layer = 0;
#ifdef VIA_ENABLE
            layer_key_row = 0xFF;
#endif
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
// Per-key debounce (a counter per key, the change taken after it has held for a
// few scans): a key changes state only
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

#ifdef VIA_ENABLE
uint16_t matrix_get_row(uint8_t row)
{
    const matrix_col_t mask  = (matrix_col_t)(1u << row);
    uint16_t           value = 0;
    for (uint8_t col = 0; col < MATRIX_COLS; col++) {
        if (matrix[col] & mask) {
            value |= (uint16_t)1u << col;
        }
    }
    return value;
}
#endif

uint8_t matrix_task()
{
#ifdef TAP_HOLD
    tap_hold_task();
#endif
#ifdef LAYER_TAP_KEYS
    if (lt_pending && (uint16_t)(tick_scans() - lt_since) >= TAPPING_TERM_SCANS) {
        lt_decide(true);
        lt_run();
    }
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
#ifdef LAYER_TAP_KEYS
                lt_feed(row, col, key_pressed);
#else
                process_key_state(row, col, key_pressed);
#endif
            }
        }

        matrix_previous[col] = current_col;
    }

    return matrix_changed;
}
