// Copyright 2026 goyamamoto
// SPDX-License-Identifier: GPL-2.0-or-later
//
// US-JIS substitution.
//
// A host set to the Japanese keyboard layout reads the US keys ` ~ @ ^ & *
// ( ) _ = + [ { ] } \ | : ' " differently from their printing. When the mode
// is enabled and the OS switch is on Win, the twenty chords below are
// replaced by the JIS chord that produces the printed character. Everything
// else is left alone.
//
// Rules (from the zmk-kb1-usjis specification):
//  - The decision is made once, when the key goes down, from the key and
//    the physical Shift state at that moment. Ctrl, Alt and GUI are ignored
//    for the decision and kept.
//  - A substituted key stays held on the host until the physical key is
//    released; the release uses what was decided at press.
//  - While at least one substituted key is held, the report's Shift follows
//    the most recently pressed non-modifier key: a substituted key asks for
//    Shift or no Shift, an unsubstituted key gets the physical Shift keys.
//    Releasing the newest key returns to the previous key's request; a
//    physical Shift change re-applies the same policy.
//  - The Shift for a key event is set before the report that carries the
//    event is sent, so the host sees the key and its Shift together: the
//    host picks the character from the report where the key goes down.
//  - Once no substituted key is held, Shift follows the physical keys again.
//  - A mode change while any non-modifier key is held is postponed until
//    the last one is released, so one key never sees both modes.
//
// smk has no post-processing hook, so the policy is put in place before an
// unsubstituted event is handed back to the normal path, and physical Shift
// events are handled here whenever a substituted key is held.

#include "usjis.h"

#ifndef USJIS
typedef int usjis_disabled_placeholder_t;
#else

#    include "kb.h"
#    include "keycodes.h"
#    include "report.h"
#    include "settings.h"

#    define USJIS_MAX_HELD 12

// Zero is "nothing pending", so the cleared startup state needs no initialiser.
#    define PENDING_NONE   0
#    define PENDING_OFF    1
#    define PENDING_ON     2

typedef struct {
    uint8_t key;         // basic keycode of the physical key (identifies it)
    uint8_t out;         // basic keycode reported to the host
    bool    substituted; // out differs from the key, the report is ours
    bool    shift;       // Shift this key wants in the report
} usjis_held_t;

typedef struct {
    uint8_t in;    // US basic keycode
    bool    shift; // with Shift
    uint8_t out;   // JIS basic keycode
    bool    oshift;
} usjis_rule_t;

// C01..C20. JIS positions: ^ = KC_EQL, @ = KC_LBRC, [ = KC_RBRC, ] = KC_NUHS,
// : = KC_QUOT, backslash/underscore = KC_INT1, yen = KC_INT3.
static __code const usjis_rule_t rules[] = {
    {KC_GRV, true, KC_EQL, true},     // C01 ~
    {KC_2, true, KC_LBRC, false},     // C02 @
    {KC_6, true, KC_EQL, false},      // C03 ^
    {KC_7, true, KC_6, true},         // C04 &
    {KC_8, true, KC_QUOT, true},      // C05 *
    {KC_9, true, KC_8, true},         // C06 (
    {KC_0, true, KC_9, true},         // C07 )
    {KC_MINS, true, KC_INT1, true},   // C08 _
    {KC_EQL, false, KC_MINS, true},   // C09 =
    {KC_EQL, true, KC_SCLN, true},    // C10 +
    {KC_LBRC, false, KC_RBRC, false}, // C11 [
    {KC_LBRC, true, KC_RBRC, true},   // C12 {
    {KC_RBRC, false, KC_NUHS, false}, // C13 ]
    {KC_RBRC, true, KC_NUHS, true},   // C14 }
    {KC_BSLS, false, KC_INT1, false}, // C15 backslash
    {KC_BSLS, true, KC_INT3, true},   // C16 |
    {KC_SCLN, true, KC_QUOT, false},  // C17 :
    {KC_QUOT, false, KC_7, true},     // C18 '
    {KC_QUOT, true, KC_2, true},      // C19 "
    {KC_GRV, false, KC_LBRC, true},   // C20 `
};

#    define RULE_COUNT (uint8_t)(sizeof(rules) / sizeof(rules[0]))

static usjis_held_t held[USJIS_MAX_HELD];
static uint8_t      held_count;
static uint8_t      phys_shift;   // MOD_BIT(KC_LSFT) | MOD_BIT(KC_RSFT) as pressed
static uint8_t      pending_mode; // PENDING_NONE / PENDING_OFF / PENDING_ON
static bool         win_mode;

bool usjis_is_enabled(void)
{
    return user_settings.usjis_enabled != 0;
}

void usjis_set_win_mode(bool win)
{
    win_mode = win;
}

bool usjis_is_active(void)
{
    return usjis_is_enabled() && win_mode;
}

static void apply_mode(bool enable)
{
    if (usjis_is_enabled() == enable) {
        return;
    }
    user_settings.usjis_enabled = enable ? 1 : 0;
    settings_mark_dirty();
    kb_usjis_mode_changed(enable);
}

// The mode after any pending change.
static bool requested_mode(void)
{
    return (pending_mode != PENDING_NONE) ? (pending_mode == PENDING_ON) : usjis_is_enabled();
}

void usjis_request(bool enable)
{
    if (requested_mode() == enable) {
        return;
    }
    if (held_count) {
        pending_mode = enable ? PENDING_ON : PENDING_OFF;
    } else {
        pending_mode = PENDING_NONE;
        apply_mode(enable);
    }
}

void usjis_toggle(void)
{
    usjis_request(!requested_mode());
}

static bool any_substituted_held(void)
{
    for (uint8_t i = 0; i < held_count; i++) {
        if (held[i].substituted) {
            return true;
        }
    }
    return false;
}

// The report's modifiers under the policy: the newest held key decides Shift.
// A substituted key asks for the Shift of its JIS chord; any other key, or no
// key, gets the physical Shift keys as they are.
static uint8_t policy_mods(void)
{
    uint8_t mods = get_mods() & (uint8_t)~MODS_SHIFT_MASK;
    if (held_count && held[held_count - 1].substituted) {
        if (held[held_count - 1].shift) {
            mods |= MOD_BIT(KC_LSFT);
        }
    } else {
        mods |= phys_shift;
    }
    return mods;
}

// Put the policy into the modifier state without sending anything, so that
// the next report, the one carrying the key event, already has it.
static void set_policy_mods(void)
{
    set_mods(policy_mods());
}

static int8_t find_held(uint8_t key)
{
    for (int8_t i = (int8_t)held_count - 1; i >= 0; i--) {
        if (held[i].key == key) {
            return i;
        }
    }
    return -1;
}

static void remove_held(uint8_t index)
{
    for (uint8_t i = index; (uint8_t)(i + 1) < held_count; i++) {
        held[i] = held[i + 1];
    }
    held_count--;
}

// Index of the rule for this chord, or NO_RULE. An index rather than a
// pointer: SDCC treats a __code pointer compared with 0 as always non-null.
#    define NO_RULE    0xFF

static uint8_t lookup(uint8_t key, bool shift)
{
    for (uint8_t i = 0; i < RULE_COUNT; i++) {
        if (rules[i].in == key && rules[i].shift == shift) {
            return i;
        }
    }
    return NO_RULE;
}

static void apply_pending_if_idle(void)
{
    if (held_count == 0 && pending_mode != PENDING_NONE) {
        const bool enable = pending_mode == PENDING_ON;
        pending_mode      = PENDING_NONE;
        apply_mode(enable);
    }
}

void usjis_clear(void)
{
    held_count = 0;
    set_policy_mods(); // back to the physical Shift keys
    apply_pending_if_idle();
}

bool usjis_process_record(uint16_t keycode, bool pressed)
{
    if (keycode == KC_LSFT || keycode == KC_RSFT) {
        const uint8_t bit = MOD_BIT((uint8_t)keycode);
        if (pressed) {
            phys_shift |= bit;
        } else {
            phys_shift &= (uint8_t)~bit;
        }
        if (!any_substituted_held()) {
            return true; // plain Shift, the normal path reports it
        }
        set_policy_mods();
        send_keyboard_report(); // one report per physical Shift event
        return false;
    }

    if (!IS_BASIC_KEYCODE(keycode)) { // KC_A..KC_EXSEL, so no modifiers
        return true;
    }

    const uint8_t key = (uint8_t)keycode;

    if (pressed) {
        if (held_count >= USJIS_MAX_HELD) {
            return true;
        }

        const bool    shift = phys_shift != 0;
        const uint8_t rule  = usjis_is_active() ? lookup(key, shift) : NO_RULE;

        usjis_held_t *h = &held[held_count++];
        h->key          = key;
        h->substituted  = rule != NO_RULE;
        h->out          = (rule != NO_RULE) ? rules[rule].out : key;
        h->shift        = (rule != NO_RULE) ? rules[rule].oshift : shift;

        if (rule == NO_RULE) {
            // The normal path reports the key down; give that report this key's Shift.
            if (any_substituted_held()) {
                set_policy_mods();
            }
            return true;
        }

        set_policy_mods();
        add_key(h->out);
        send_keyboard_report();
        return false;
    }

    const int8_t i = find_held(key);
    if (i < 0) {
        return true;
    }

    const bool    substituted = held[i].substituted;
    const uint8_t out         = held[i].out;
    remove_held((uint8_t)i);

    if (!substituted) {
        // The normal path reports the key up; give that report the remaining keys' Shift.
        if (any_substituted_held()) {
            set_policy_mods();
        }
        apply_pending_if_idle();
        return true;
    }

    set_policy_mods();
    del_key(out);
    send_keyboard_report();
    apply_pending_if_idle();
    return false;
}

#endif // USJIS
