#include <stdint.h>
#include <stdbool.h>
#include "keycodes.h"
#include "kbdef.h"
#include "keyboard.h"
#include "layout.h"
#include "settings.h"
#include "debug.h"
#include "report.h"
#include "usb.h"
#include "indicators.h"
#include "tick.h"
#include "f65_rf.h"
#include "f65_power.h"
#include "host.h"
#include "matrix.h"
#include "led.h"
#ifdef F65_LIGHTING
#    include "backlight.h"
#endif

#ifdef USJIS
#    include "usjis.h"
#endif

// Reports go to USB in the wired position and to the radio module in the
// 2.4 GHz and Bluetooth positions (f65_rf.c, f65_power.c).

extern uint8_t action_layer; // src/smk/matrix.c: the momentary (Fn) layer, 0 when none

static bool fn_held(void)
{
    return layout_is_fn_layer(action_layer);
}

// The F65 has no OS switch, so the Win / Mac choice is a saved setting (Win by
// default, like the stock firmware). It takes effect once the settings are
// loaded (indicators_validate_settings, called from main) and on OS_WIN / OS_MAC.
static void kb_apply_os_mode(void)
{
    const bool is_mac = user_settings.os_mac != 0;

    set_default_layer(layout_os_base_layer(is_mac));
    dprintf(is_mac ? "MAC_MODE\r\n" : "WIN_MODE\r\n");
#ifdef USJIS
    usjis_set_win_mode(!is_mac);
#endif
}

// Before usb_init() (BOARD_USB_BOOT_GATE): USB comes up at boot only when the
// saved transport is the wired one; in a wireless position the stock never
// enables USB at boot either (main 0x8B1F). The switch debounce then decides.
// usb_init() (USB_INIT_GATE): only in the wired position, as the stock's
// 0xAF16 returns in a wireless transport - a bus reset or a pull-up event
// there must not bring USB back.
bool kb_usb_allowed(void)
{
    return f65_transport() == RF_USB;
}

bool kb_usb_at_boot(void)
{
    if (!settings_load()) {
        user_settings.rf_link = RF_LINK_DEFAULT;
    }
    f65_load_link(user_settings.rf_link);
    return f65_transport() == RF_USB;
}

void kb_init()
{
    kb_apply_os_mode(); // settings are still zero here: Win until they are loaded
    // As the stock main (0x8B4A-0x8B60): the 1 ms tick, EUART0, the two
    // Bluetooth names on every boot, then EUART0 off again in the wired position.
    rf_ms_tick_init();
    rf_uart_init();
    rf_send_names();
    if (f65_transport() == RF_USB) {
        IEN1 &= (uint8_t)~_ES0;
    }
}

// The board's own settings, and the lighting's (backlight.c).
void indicators_apply_defaults()
{
    user_settings.os_mac  = 0;
    user_settings.rf_link = RF_LINK_DEFAULT;
#ifdef USJIS
    user_settings.usjis_enabled = 0;
#endif
#ifdef F65_LIGHTING
    backlight_defaults();
#endif
}

void indicators_validate_settings()
{
    if (user_settings.os_mac > 1) {
        user_settings.os_mac = 0;
    }
#ifdef USJIS
    if (user_settings.usjis_enabled > 1) {
        user_settings.usjis_enabled = 0;
    }
#endif
#ifdef F65_LIGHTING
    backlight_validate(); // a record from before the lighting: its defaults
#endif
    kb_apply_os_mode();
    f65_load_link(user_settings.rf_link);
}

// The stock's 10 ms slow tick (0x8F51): the connection switch, the battery
// low / cutoff counting and the sleep conditions.
#define SLOW_TICK_MS 10

void kb_update_switches()
{
    static uint16_t slow_at;

    // The stock firmware keeps P7.6 at the inverse of the USB-power pin P4.4.
    PWR_AUX_OUT = !USB_PWR_DET;

    const uint16_t now = rf_ms();
    if ((uint16_t)(now - slow_at) < SLOW_TICK_MS) {
        return;
    }
    slow_at = now;
    f65_switch_tick();
    rf_power_tick(USB_PWR_DET, !CHG_STAT, f65_transport() != RF_USB);
    f65_sleep_tick();
}

// Forget what is held, so a key that changes meaning with the base layer
// cannot stay stuck on the host.
static void kb_release_all(void)
{
    clear_keys();
    clear_mods();
    send_keyboard_report();
#ifdef USJIS
    usjis_clear();
#endif
}

static void kb_settings_reset(void)
{
#ifdef USJIS
    usjis_request(false);
#endif
    if (user_settings.os_mac) {
        user_settings.os_mac = 0;
        kb_release_all();
        kb_apply_os_mode();
    }
#ifdef F65_LIGHTING
    backlight_defaults();
#endif
    settings_mark_dirty();
    dprintf("settings reset\r\n");
}

#ifdef USJIS
void kb_usjis_mode_changed(bool enabled)
{
    indicators_usjis_changed(enabled); // Tab shows the new mode for a moment
}
#endif

// ------------------------------------------------ number row under Fn
//
// Fn + 1 .. = gives the "row above" the number row: F1..F12 or media keys.
// Mac mode: media by default (Apple order), F-keys with Right Shift; Win mode:
// F-keys by default, media with Right Shift (the stock's Win Fn row, 0xB800
// row bit 0). Right Shift under Fn works like the Fn key of an ordinary
// keyboard: it picks the other set and is not Shift while Fn is held.
//
// Right Shift as Shift (rs_eff) is the one state both the report and US-JIS
// see; every change of it goes through the normal path as a KC_RSFT event:
//  - pressed without Fn: Shift at once (the host sees it before Fn, if Fn
//    follows);
//  - Fn goes down: taken out (a KC_RSFT release);
//  - Fn goes up with Right Shift still held: not put back then (the host
//    would see a lone Shift press and release when both keys go up), but
//    just before the next ordinary key goes down, if still held;
//  - released while not Shift: nothing to release.

#define MEDIA_ALT_TAB 0xF001 // LAlt + Tab (stock 0004002b)
#define MEDIA_ALT_ESC 0xF002 // LAlt + Esc (stock 00040029)
#define FROW_CONSUMER 0x8000 // tag in frow_sent: a consumer usage went out

static const __code uint16_t media_mac[12] = {
    0x070, 0x06F, 0x29F, 0x2A0, 0, 0, 0x0B6, 0x0CD, 0x0B5, 0x0E2, 0x0EA, 0x0E9,
};
static const __code uint16_t media_win[12] = {
    0x070, 0x06F, MEDIA_ALT_TAB, MEDIA_ALT_ESC, 0x223, 0x18A, 0x0B6, 0x0CD, 0x0B5, 0x0E2, 0x0EA, 0x0E9,
};

static bool             rs_phys;          // Right Shift physically held (KC_RSFT or FN_ALT)
static bool             rs_eff;           // ... and Shift in the report and for US-JIS
static bool             rs_pending;       // Fn went up with it held: Shift again with the next key
static bool             synth;            // a key this file sends through process_keycode
static bool             grave_shifted;    // Fn + Right Shift + Esc: ~
static bool             grave_added_lsft; // ... with a Left Shift of our own
static uint8_t          alt_users;        // Alt+Tab / Alt+Esc keys held
static bool             alt_added;        // ... with a Left Alt of our own
static __xdata uint16_t frow_sent[12];    // what each number key sent at its press

static bool alt_active(void)
{
    return rs_phys;
}

static void synth_key(uint16_t kc, bool pressed)
{
    synth = true;
    process_keycode(kc, pressed);
    synth = false;
}

static void rshift_withdraw(void)
{
    if (rs_eff) {
        rs_eff = false;
        synth_key(KC_RSFT, false);
    }
}

void kb_fn_layer(bool held)
{
    if (held) {
        rs_pending = false;
        rshift_withdraw();
    } else if (rs_phys) {
        rs_pending = true;
    }
}

// A modifier this file adds for a key of its own only when the user is not
// holding it already, and it is taken away only if it was added here.
static void frow(uint8_t n, bool pressed)
{
    if (pressed) {
        const bool is_mac = user_settings.os_mac != 0;
        if (is_mac == alt_active()) { // Win without / Mac with Right Shift: F-keys
            frow_sent[n] = (uint16_t)(KC_F1 + n);
            synth_key(frow_sent[n], true);
            return;
        }
        const uint16_t u = is_mac ? media_mac[n] : media_win[n];
        frow_sent[n]     = u;
        if (u == MEDIA_ALT_TAB || u == MEDIA_ALT_ESC) {
            if (alt_users++ == 0) {
                alt_added = !(get_mods() & MOD_BIT(KC_LALT));
                if (alt_added) {
                    synth_key(KC_LALT, true);
                }
            }
            synth_key((u == MEDIA_ALT_TAB) ? KC_TAB : KC_ESC, true);
        } else if (u) {
            frow_sent[n] = FROW_CONSUMER | u;
            host_consumer_send(u);
        }
        return;
    }
    const uint16_t s = frow_sent[n];
    frow_sent[n]     = 0;
    if (s == MEDIA_ALT_TAB || s == MEDIA_ALT_ESC) {
        synth_key((s == MEDIA_ALT_TAB) ? KC_TAB : KC_ESC, false);
        if (alt_users && --alt_users == 0 && alt_added) {
            alt_added = false;
            synth_key(KC_LALT, false);
        }
    } else if (s & FROW_CONSUMER) {
        host_consumer_send(0);
    } else if (s) {
        synth_key(s, false);
    }
}

static bool reset_armed;     // SETTINGS_RESET_HOLD_KEY went down under Fn and is held
static bool reset_swallowed; // SETTINGS_RESET_KEY went down under Fn and was not sent

bool kb_process_record(uint16_t keycode, bool key_pressed)
{
    if (key_pressed) {
        rf_note_activity(); // a key press resets the idle seconds (0xAFFA)
        // Low-battery cutoff (0x3108): no new key reaches the host until the
        // battery recovers or external power comes; releases still go out.
        if (f65_transport() != RF_USB && rf_battery_cutoff()) {
            return false;
        }
    }

    // Settings reset: Fn + SETTINGS_RESET_HOLD_KEY held, then Fn +
    // SETTINGS_RESET_KEY. Checked before anything else so neither key reaches
    // the host (or US-JIS) while Fn is down. A release is swallowed only when
    // its press was (the matrix hands back the keycode the key went down with),
    // so a key that went down without Fn is always released on the host.
    if (keycode == SETTINGS_RESET_HOLD_KEY) {
        if (key_pressed && fn_held()) {
            reset_armed = true;
            return false;
        }
        if (!key_pressed && reset_armed) {
            reset_armed = false;
            return false;
        }
    }
    if (!synth) {
        if (keycode == KC_RSFT) { // Right Shift pressed without Fn
            rs_phys    = key_pressed;
            rs_pending = false;
            if (key_pressed) {
                rs_eff = true; // plain Shift: the normal path (and US-JIS) takes it
            } else if (rs_eff) {
                rs_eff = false;
            } else {
                return false; // taken out under Fn: nothing to release
            }
        } else if (keycode == FN_ALT) { // Right Shift pressed under Fn
            rs_phys = key_pressed;
            if (!key_pressed) {
                rs_pending = false;
                rshift_withdraw();
            }
            return false;
        } else if (key_pressed && rs_pending && IS_BASIC_KEYCODE(keycode)) {
            // The first ordinary key after Fn went up with Right Shift held:
            // Shift goes back just before it.
            rs_pending = false;
            if (rs_phys && !fn_held()) {
                rs_eff = true;
                synth_key(KC_RSFT, true);
            }
        }
        // Fn + Right Shift + Esc = ~: the grave key with a Shift of our own
        // (through US-JIS like a real one), as Shift + Fn + Esc gives.
        if (keycode == KC_GRV && key_pressed && fn_held() && alt_active()) {
            grave_shifted    = true;
            grave_added_lsft = !(get_mods() & MOD_BIT(KC_LSFT));
            if (grave_added_lsft) {
                synth_key(KC_LSFT, true);
            }
            synth_key(KC_GRV, true);
            return false;
        }
        if (keycode == KC_GRV && !key_pressed && grave_shifted) {
            grave_shifted = false;
            synth_key(KC_GRV, false);
            if (grave_added_lsft) {
                synth_key(KC_LSFT, false);
            }
            return false;
        }
    }
    if (keycode == SETTINGS_RESET_KEY) {
        if (key_pressed && fn_held()) {
            reset_swallowed = true;
            if (reset_armed) {
                kb_settings_reset();
            }
            return false;
        }
        if (!key_pressed && reset_swallowed) {
            reset_swallowed = false;
            return false;
        }
    }

#ifdef F65_LIGHTING
    if (!backlight_process_record(keycode, key_pressed)) {
        return false;
    }
#endif

#ifdef LED_DIAG
    if (!led_diag_process_record(keycode, key_pressed)) {
        return false;
    }
#endif

#ifdef USJIS
    if (!usjis_process_record(keycode, key_pressed)) {
        return false;
    }
#endif

    switch (keycode) {
#ifdef USJIS
        case USJIS_TOG:
            if (key_pressed) {
                usjis_toggle();
            }
            return false;
#endif
        case FR_1:
        case FR_2:
        case FR_3:
        case FR_4:
        case FR_5:
        case FR_6:
        case FR_7:
        case FR_8:
        case FR_9:
        case FR_10:
        case FR_11:
        case FR_12:
            frow((uint8_t)(keycode - FR_1), key_pressed);
            return false;
        case LNK_24G:
            f65_link_key(0, key_pressed);
            return false;
        case LNK_BT1:
        case LNK_BT2:
        case LNK_BT3:
            f65_link_key((uint8_t)(keycode - LNK_BT1 + 1), key_pressed);
            return false;
        case BAT_SHOW:
            // Shown while held; the stock's rule (0x4199): in a wireless
            // position on battery (indicators.c checks that as it paints).
            indicators_battery_show(key_pressed);
            return false;
        case OS_WIN:
        case OS_MAC:
            // Set keys, as on the F65 V2: the key for the mode already in force
            // changes nothing and writes nothing.
            if (key_pressed && user_settings.os_mac != (keycode == OS_MAC ? 1 : 0)) {
                user_settings.os_mac = (keycode == OS_MAC) ? 1 : 0;
                kb_release_all();
                kb_apply_os_mode();
                settings_mark_dirty();
            }
            return false;
        default:
            return true;
    }
}

void kb_send_report(__xdata report_keyboard_t *report)
{
    if (f65_transport() == RF_USB) {
        usb_send_report(report);
    } else {
        rf_queue_keyboard(report);
    }
}

void kb_send_nkro(__xdata report_nkro_t *report)
{
    if (f65_transport() == RF_USB) {
        usb_send_nkro(report);
    }
    // NKRO_DEFAULT_OFF: the radio carries the 6KRO report only.
}

void kb_send_extra(__xdata report_extra_t *report)
{
    if (f65_transport() == RF_USB) {
        usb_send_extra(report);
    } else {
        rf_queue_extra(report);
    }
}

// Remote wakeup without sleep: the board stays awake while the bus is
// suspended, so a key that goes down then asks the host to resume
// (usb_wake_host). The bus has been idle 3 ms when SUSPIF sets; waiting a few
// more scans keeps to USB's 5 ms of idle before a device may signal resume.
#define WAKE_AFTER_SUSPEND_SCANS 8

extern uint8_t matrix[MATRIX_COLS]; // src/smk/matrix.c, written by the scan

void kb_update()
{
    rf_task(f65_transport());
    f65_pair_tick();
    // Dark while the bus is suspended - in the wired position only: a suspend
    // flag left from before a switch to wireless must not keep the LEDs dark.
    led_suspend(f65_transport() == RF_USB && usb_suspended);
    if (f65_transport() != RF_USB) {
        return;
    }

    static uint8_t  seen[MATRIX_COLS];
    static bool     was_suspended;
    static uint16_t suspended_since;

    bool pressed = false;
    for (uint8_t col = 0; col < MATRIX_COLS; col++) {
        const uint8_t now = matrix[col];
        if (now & (uint8_t)~seen[col]) {
            pressed = true;
        }
        seen[col] = now;
    }

    if (!usb_suspended) {
        was_suspended = false;
        return;
    }
    if (!was_suspended) {
        was_suspended   = true;
        suspended_since = tick_scans();
    }
    if (pressed && (uint16_t)(tick_scans() - suspended_since) >= WAKE_AFTER_SUSPEND_SCANS) {
        usb_wake_host();
    }
}
