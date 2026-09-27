#include "led.h"
#include "kbdef.h"
#include "indicators.h"
#include "keyboard.h"
#include "layout.h"
#include "settings.h"
#include "tick.h"
#include "watchdog.h"
#include "f65_rf.h"
#include "f65_power.h"
#include "backlight.h"
#ifdef USJIS
#    include "usjis.h"
#endif

// Status indicators of the aula-f65-v1: a key lights at the stock indicator
// level (DUTY2 = 0x0400 per channel on, the phase off) in the stock indicator
// colours, over the backlight (backlight.c): a cell an indicator holds shows
// the indicator (lit or, where noted, dark), the others the backlight (led.h).
// The rules follow the stock painter 0x3108 (the author's analysis notes, not
// published), with the link keys where this port selects the
// links.
//
//   Caps   white        host Caps Lock LED on (USB report, or the module's
//                       status frame over the radio: then only while the link
//                       is connected, 0x34EA), on the key that sends Caps Lock
//                       (stock: Caps; usjis: the stock Left Ctrl position)
//   Fn     red          charging (USB power 3 samples, P7.7 low 20 samples;
//                       f65_rf.c, stock 0x9E26), on the Fn key
//          red blink    low battery (wireless, on battery: raw < 781 for 24
//                       samples, sticky; toggles every 250 ms, stock 0x31D3);
//                       the backlight is off meanwhile, as on the stock
//   while Fn is held (only the active link's key; the other link keys dark):
//     Y         white   the link is USB
//     R         green   2.4 GHz, connected
//     Q / W / E blue    Bluetooth slot 1 / 2 / 3, connected
//     A / S     white   Win / Mac base layer
//     Tab       green / red  US-JIS on / off (usjis; also ~1 s after a change)
//   with or without Fn (stock 0x35C5-0x36D3, 0x9EAC / 0x93E7; the key of the
//   slot the module reports, R for 2.4 GHz in cyan, Q / W / E in blue):
//     pairing        blink, toggled every 80 ms (dark between)
//     reconnecting   blink, toggled every 380 ms (dark between)
//     connected      solid for 3 s, then off (green / blue while Fn is held)
//   Fn + B held (wireless, on battery; stock 0x4199 / 0x3245): keys 1 .. 0
//     show the battery, one key per step of 737, 758, 781, 802, 824, 846, 868,
//     890, 912, 934 the raw value reaches; green from 802 up, else red. As on
//     the stock (which clears every cell at the press, 0x41B4, and paints
//     nothing else meanwhile, 0x34D6) every other indicator and the backlight
//     are dark meanwhile.
//   Low-battery cutoff (raw < 737 for 2 s): everything dark (LED_HOLD_CUTOFF).
//
// The time base is the stock's: a 10 ms tick from the 1 ms PWM4 counter
// (rf_ms), counters in ms compared as the stock compares them.

extern uint8_t action_layer; // src/smk/matrix.c: the momentary (Fn) layer, 0 when none

// Matrix scans per audit / Tab tick: a scan comes every ~0.75 ms (systick.c:
// the ~350 us scan slot, then a 400 us LED subframe), so 13 scans are ~10 ms.
#define IND_SCANS_PER_TICK 13
#define IND_TAB_TICKS      100 // ~1 s

#define IND_TICK_MS      10
#define IND_PAIR_MS      80   // stock 0x35EE: >= 0x50
#define IND_RECONNECT_MS 380  // stock 0x361B: >= 0x17C
#define IND_SOLID_MS     3000 // stock 0x3685: > 0x0BB8
#define IND_LOW_MS       240  // stock 0x31DE: > 0xF0

// Colours as a set of channels; IND_OFF gives the cell back to the backlight,
// IND_DARK holds it dark.
#define IND_OFF   0x00
#define IND_DARK  0x80
#define IND_RED   0x01
#define IND_GREEN 0x02
#define IND_BLUE  0x04
#define IND_CYAN  0x06
#define IND_WHITE 0x07

#define IND_BATT_KEYS 10

enum {
    IND_LK_Y, // the link keys: Y (USB), Q / W / E (Bluetooth slots 1-3), R (2.4 GHz)
    IND_LK_Q,
    IND_LK_W,
    IND_LK_E,
    IND_LK_R,
#ifdef USJIS
    IND_TAB,
#endif
    IND_WIN,  // A
    IND_MAC,  // S
    IND_CAPS, // the Caps Lock key
    IND_FN,   // the Fn key: charging / low battery
    IND_BATT, // keys 1 .. 0: IND_BATT + 0 .. 9
    IND_COUNT = IND_BATT + IND_BATT_KEYS,
};

// LED cells (column, LED row = smk row + 1); 0xFF = none.
static __xdata uint8_t ind_col[IND_COUNT];
static __xdata uint8_t ind_row[IND_COUNT];
static __xdata uint8_t ind_shown[IND_COUNT];

// Battery display steps (stock 0x3245-0x34A7).
static __code const uint16_t batt_steps[IND_BATT_KEYS] = {737, 758, 781, 802, 824, 846, 868, 890, 912, 934};
#define IND_BATT_GREEN 802

static uint16_t ind_last_scan;
static uint8_t  ind_tab_ticks;
static uint16_t ind_last_ms;
static uint16_t ind_render_ms;
static uint16_t blink_ms, solid_ms, low_ms;
static bool     blink_on, solid, low_on, batt_show;

// The key of `keycode` in `layer`, as an LED cell.
static void ind_find(uint8_t ind, uint8_t layer, uint16_t keycode, bool momentary)
{
    ind_col[ind] = 0xFF;
    for (uint8_t row = 0; row < MATRIX_ROWS; row++) {
        for (uint8_t col = 0; col < MATRIX_COLS; col++) {
            const uint16_t k = keymaps[layer][row][col];
            if (momentary ? IS_QK_MOMENTARY(k) : k == keycode) {
                ind_col[ind] = col;
                ind_row[ind] = (uint8_t)(row + 1);
                return;
            }
        }
    }
}

static void ind_at(uint8_t ind, uint8_t col, uint8_t smk_row)
{
    ind_col[ind] = col;
    ind_row[ind] = (uint8_t)(smk_row + 1);
}

void indicators_init()
{
    led_init();
    backlight_init();

    // Stock positions (V1 keymap table 0xB400: column, row bit = smk row + 1).
    ind_at(IND_LK_Y, 6, 1);
    ind_at(IND_LK_Q, 1, 1);
    ind_at(IND_LK_W, 2, 1);
    ind_at(IND_LK_E, 3, 1);
    ind_at(IND_LK_R, 4, 1);
#ifdef USJIS
    ind_at(IND_TAB, 0, 1); // Tab
#endif
    ind_at(IND_WIN, 1, 2); // A
    ind_at(IND_MAC, 2, 2); // S
    for (uint8_t i = 0; i < IND_BATT_KEYS; i++) {
        ind_at((uint8_t)(IND_BATT + i), (uint8_t)(i + 1), 0); // 1 .. 0
    }
    // Where the layout puts them (the base layers agree).
    ind_find(IND_CAPS, 0, KC_CAPS, false);
    ind_find(IND_FN, 0, 0, true);

    for (uint8_t i = 0; i < IND_COUNT; i++) {
        ind_shown[i] = IND_OFF;
    }
    ind_last_scan = tick_scans();
    ind_last_ms   = rf_ms();
    ind_render_ms = (uint16_t)(ind_last_ms - 1u);
}

void indicators_usjis_changed(bool enabled)
{
    enabled;
    ind_tab_ticks = IND_TAB_TICKS;
}

void indicators_battery_show(bool on)
{
    batt_show = on;
}

// One cell in one colour (keys: slot channel 0 red, 1 green, 2 blue), dark
// (IND_DARK), or back to the backlight (IND_OFF). The duty table follows in
// led_flush.
static void ind_show(uint8_t ind, uint8_t rgb)
{
    const uint8_t col = ind_col[ind];
    if (col == 0xFF || ind_shown[ind] == rgb) {
        return;
    }
    ind_shown[ind] = rgb;
    led_cell_override(col, ind_row[ind], rgb ? (uint8_t)(rgb & IND_WHITE) : LED_OVR_NONE);
}

// The 10 ms tick: the stock's blink and solid timers.
static void ind_tick(uint8_t state, bool low)
{
    // Pairing / reconnecting blink (0x35E9 / 0x3616): toggle when the counter
    // reaches the state's period; the counter runs in every state.
    if (blink_ms < 0xFF00u) {
        blink_ms += IND_TICK_MS;
    }
    if ((state == RF_STATE_PAIRING && blink_ms >= IND_PAIR_MS) || (state == RF_STATE_RECONNECTING && blink_ms >= IND_RECONNECT_MS)) {
        blink_ms = 0;
        blink_on = !blink_on;
    }
    // Connected: solid from the latch going on (0x3666), off after 3 s.
    if (rf_take_connect_event()) {
        solid    = true;
        solid_ms = 0;
    } else if (solid) {
        solid_ms += IND_TICK_MS;
        if (solid_ms > IND_SOLID_MS || state != RF_STATE_CONNECTED) {
            solid = false;
        }
    }
    // Low battery (0x31D3): toggle when the counter passes 240 ms; reset when
    // not blinking.
    if (low) {
        low_ms += IND_TICK_MS;
        if (low_ms > IND_LOW_MS) {
            low_ms = 0;
            low_on = !low_on;
        }
    } else {
        low_ms = 0;
        low_on = false;
    }
}

// The main loop calls this on every pass; it does its work once per 1 ms tick,
// so the other passes stay short (the radio's receive framing looks at P4.7 on
// every pass, f65_rf.c).
void indicators_render()
{
    // Every pass: a unit of backlight work and a column of the duty table, with
    // P4.7 looked at around them; neither on a pass where a key changed.
    rf_rx_poll();
    if (backlight_task()) {
        rf_rx_poll();
        led_flush();
    }
    rf_rx_poll();

    const uint16_t ms = rf_ms();
    if (ms == ind_render_ms) {
        return;
    }
    ind_render_ms = ms;
    rf_rx_poll(); // P4.7 as often as the main loop's other passes look at it
    // At most 10 ticks to catch up (the first render comes after the USB
    // enumeration wait, up to 4 s after indicators_init; a long wait in the
    // main loop): the blink phases skip, no long stretch without a kick.
    if ((uint16_t)(ms - ind_last_ms) > (uint16_t)(10u * IND_TICK_MS)) {
        ind_last_ms = (uint16_t)(ms - 10u * IND_TICK_MS);
    }

    led_stall_check();

    const uint16_t now = tick_scans();
    if ((uint16_t)(now - ind_last_scan) >= IND_SCANS_PER_TICK) {
        ind_last_scan = now;
        if (ind_tab_ticks) {
            ind_tab_ticks--;
        }
        led_audit(); // one column per tick: the whole table every ~160 ms
        rf_rx_poll();
    }

    const uint8_t transport = f65_transport();
    const bool    wireless  = transport != RF_USB;
    const bool    fn        = layout_is_fn_layer(action_layer);
    const bool    ext       = rf_external_power();
    const uint8_t state     = rf_module_state();
    const bool    show_batt = batt_show && wireless && !ext;
    const bool    low_blink = wireless && !ext && rf_battery_low() && !show_batt;

    while ((uint16_t)(ms - ind_last_ms) >= IND_TICK_MS) {
        ind_last_ms += IND_TICK_MS;
        ind_tick(state, low_blink);
    }
    if (!wireless) {
        solid = false;
    }

    // Low-battery cutoff: every LED dark until it ends (the stock stops painting).
    if (wireless && rf_battery_cutoff()) {
        if (!(led_holds_get() & LED_HOLD_CUTOFF)) {
            led_hold(LED_HOLD_CUTOFF);
        }
    } else if (led_holds_get() & LED_HOLD_CUTOFF) {
        led_release(LED_HOLD_CUTOFF);
    }

    const bool fn_keys = fn && !show_batt; // the stock skips them during the battery display
    backlight_battery_display(show_batt);

    rf_rx_poll();

    // The link: its key shows it; while Fn is held the other link keys are dark.
    uint8_t active = 0xFF, rgb = IND_OFF;
    if (!wireless) {
        active = IND_LK_Y;
        rgb    = fn_keys ? IND_WHITE : IND_OFF;
    } else if (!show_batt && rf_synced() && rf_module_slot() <= 3) {
        const uint8_t slot   = rf_module_slot();
        const uint8_t colour = slot ? IND_BLUE : IND_CYAN;
        active               = slot ? (uint8_t)(IND_LK_Q + slot - 1) : IND_LK_R; // Q W E = slots 1-3, R = 2.4 GHz
        if (state == RF_STATE_PAIRING || state == RF_STATE_RECONNECTING) {
            rgb = blink_on ? colour : IND_DARK;
        } else if (state == RF_STATE_CONNECTED) {
            if (solid) {
                rgb = colour;
            } else if (fn_keys && rf_connected_latched()) {
                rgb = slot ? IND_BLUE : IND_GREEN; // Bluetooth blue, 2.4 GHz green (USB: Y white)
            }
        }
    }
    const uint8_t others = fn_keys ? IND_DARK : IND_OFF;
    for (uint8_t i = IND_LK_Y; i <= IND_LK_R; i++) {
        ind_show(i, (i == active && rgb) ? rgb : others);
    }

#ifdef USJIS
    ind_show(IND_TAB, (fn_keys || (ind_tab_ticks && !show_batt)) ? (usjis_is_enabled() ? IND_GREEN : IND_RED) : IND_OFF);
#endif
    ind_show(IND_WIN, (fn_keys && !user_settings.os_mac) ? IND_WHITE : IND_OFF);
    ind_show(IND_MAC, (fn_keys && user_settings.os_mac) ? IND_WHITE : IND_OFF);
    ind_show(IND_CAPS, ((keyboard_state.led_state & (1 << 1)) && (!wireless || rf_connected_latched()) && !show_batt) ? IND_WHITE : IND_OFF);

    uint8_t fn_rgb = IND_OFF;
    if (ext) {
        fn_rgb = rf_charging() ? IND_RED : IND_OFF;
    } else if (low_blink) {
        fn_rgb = low_on ? IND_RED : IND_DARK;
    }
    ind_show(IND_FN, fn_rgb);

    rf_rx_poll();

    // Fn + B: the battery on 1 .. 0.
    const uint16_t raw    = rf_battery_raw();
    const uint8_t  colour = (raw >= IND_BATT_GREEN) ? IND_GREEN : IND_RED;
    for (uint8_t i = 0; i < IND_BATT_KEYS; i++) {
        ind_show((uint8_t)(IND_BATT + i), (show_batt && raw >= batt_steps[i]) ? colour : IND_OFF);
    }
}
