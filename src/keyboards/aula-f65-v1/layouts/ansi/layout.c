#include "kbdef.h"
#include "layout.h"
#include "user_layout.h"
#include "report.h"
#include "kb.h"
#include <stdint.h>

// clang-format off

// Key positions follow the stock V1 keymap table (0xB400 in the image, index =
// column * 6 + row bit); Kcc_r = column cc, row r, with r = stock row bit - 1.
// The physical 65% layout (67 keys) is that of the manual and the V2 VIA JSON,
// which uses the same logical positions. R0C14, R1C14, R2C12, R2C14, R3C11,
// R3C12 and R4C3/4/6/7/8/11/12 carry no key.
#define LAYOUT_65( \
    K00_0, K01_0, K02_0, K03_0, K04_0, K05_0, K06_0, K07_0, K08_0, K09_0, K10_0, K11_0, K12_0, K13_0,        K15_0, \
    K00_1, K01_1, K02_1, K03_1, K04_1, K05_1, K06_1, K07_1, K08_1, K09_1, K10_1, K11_1, K12_1, K13_1,        K15_1, \
    K00_2, K01_2, K02_2, K03_2, K04_2, K05_2, K06_2, K07_2, K08_2, K09_2, K10_2, K11_2,        K13_2,        K15_2, \
    K00_3, K01_3, K02_3, K03_3, K04_3, K05_3, K06_3, K07_3, K08_3, K09_3, K10_3,               K13_3, K14_3, K15_3, \
    K00_4, K01_4, K02_4,               K05_4,                             K09_4, K10_4,        K13_4, K14_4, K15_4  \
) { \
    { K00_0, K01_0, K02_0, K03_0, K04_0, K05_0, K06_0, K07_0, K08_0, K09_0, K10_0, K11_0, K12_0, K13_0, KC_NO, K15_0 }, \
    { K00_1, K01_1, K02_1, K03_1, K04_1, K05_1, K06_1, K07_1, K08_1, K09_1, K10_1, K11_1, K12_1, K13_1, KC_NO, K15_1 }, \
    { K00_2, K01_2, K02_2, K03_2, K04_2, K05_2, K06_2, K07_2, K08_2, K09_2, K10_2, K11_2, KC_NO, K13_2, KC_NO, K15_2 }, \
    { K00_3, K01_3, K02_3, K03_3, K04_3, K05_3, K06_3, K07_3, K08_3, K09_3, K10_3, KC_NO, KC_NO, K13_3, K14_3, K15_3 }, \
    { K00_4, K01_4, K02_4, KC_NO, KC_NO, K05_4, KC_NO, KC_NO, KC_NO, K09_4, K10_4, KC_NO, KC_NO, K13_4, K14_4, K15_4 }  \
}

#define _WIN_BL 0
#define _MAC_BL 1
#define _WIN_FL 2
#define _MAC_FL 3

#define FN_WIN MO(_WIN_FL)
#define FN_MAC MO(_MAC_FL)

const uint16_t keymaps[][MATRIX_ROWS][MATRIX_COLS] = {
    /* Keymap _WIN_BL: Windows base layer (the default), as printed.
     * ,---------------------------------------------------------------.
     * |Esc|  1|  2|  3|  4|  5|  6|  7|  8|  9|  0|  -|  =|  Bksp |Del|
     * |---------------------------------------------------------------|
     * |Tab  |  Q|  W|  E|  R|  T|  Y|  U|  I|  O|  P|  [|  ]|    \|PgU|
     * |---------------------------------------------------------------|
     * |Caps  |  A|  S|  D|  F|  G|  H|  J|  K|  L|  ;|  '|  Enter |PgD|
     * |---------------------------------------------------------------|
     * |Shift   |  Z|  X|  C|  V|  B|  N|  M|  ,|  .|  /|Shift |Up |End|
     * |---------------------------------------------------------------|
     * |Ctl |Win |Alt |          Space            | Fn|Ctl|Lef|Dow|Rig|
     * `---------------------------------------------------------------'
     */
    [_WIN_BL] = LAYOUT_65(
        KC_ESC,  KC_1,    KC_2,    KC_3,    KC_4,    KC_5,    KC_6,    KC_7,    KC_8,    KC_9,    KC_0,    KC_MINS, KC_EQL,  KC_BSPC,          KC_DEL,
        KC_TAB,  KC_Q,    KC_W,    KC_E,    KC_R,    KC_T,    KC_Y,    KC_U,    KC_I,    KC_O,    KC_P,    KC_LBRC, KC_RBRC, KC_BSLS,          KC_PGUP,
        KC_CAPS, KC_A,    KC_S,    KC_D,    KC_F,    KC_G,    KC_H,    KC_J,    KC_K,    KC_L,    KC_SCLN, KC_QUOT,          KC_ENT,           KC_PGDN,
        KC_LSFT, KC_Z,    KC_X,    KC_C,    KC_V,    KC_B,    KC_N,    KC_M,    KC_COMM, KC_DOT,  KC_SLSH,                   KC_RSFT, KC_UP,   KC_END,
        KC_LCTL, KC_LGUI, KC_LALT,                   KC_SPC,                                      FN_WIN,  KC_RCTL,          KC_LEFT, KC_DOWN, KC_RGHT
    ),

    /* Keymap _MAC_BL: Mac base layer (Fn + S; Fn + A goes back to Win), the stock Mac table's modifiers:
     * the key right of Left Ctrl is Option, the next one Command.
     * |Ctl |Opt |Cmd |          Space            | Fn|Ctl|Lef|Dow|Rig|
     */
    [_MAC_BL] = LAYOUT_65(
        KC_ESC,  KC_1,    KC_2,    KC_3,    KC_4,    KC_5,    KC_6,    KC_7,    KC_8,    KC_9,    KC_0,    KC_MINS, KC_EQL,  KC_BSPC,          KC_DEL,
        KC_TAB,  KC_Q,    KC_W,    KC_E,    KC_R,    KC_T,    KC_Y,    KC_U,    KC_I,    KC_O,    KC_P,    KC_LBRC, KC_RBRC, KC_BSLS,          KC_PGUP,
        KC_CAPS, KC_A,    KC_S,    KC_D,    KC_F,    KC_G,    KC_H,    KC_J,    KC_K,    KC_L,    KC_SCLN, KC_QUOT,          KC_ENT,           KC_PGDN,
        KC_LSFT, KC_Z,    KC_X,    KC_C,    KC_V,    KC_B,    KC_N,    KC_M,    KC_COMM, KC_DOT,  KC_SLSH,                   KC_RSFT, KC_UP,   KC_END,
        KC_LCTL, KC_LALT, KC_LGUI,                   KC_SPC,                                      FN_MAC,  KC_RCTL,          KC_LEFT, KC_DOWN, KC_RGHT
    ),

    /* Keymap _WIN_FL / _MAC_FL: function layer (hold Fn), the stock V1 Fn keys
     * with the link and battery keys of this port.
     * ,---------------------------------------------------------------.
     * |  `| F1| F2| F3| F4| F5| F6| F7| F8| F9|F10|F11|F12| (reset)|Ins|  (Win; Mac: media)
     * |---------------------------------------------------------------|
     * |     |BT1|BT2|BT3|24G|(n)|   |PSc|ScL|Pau|   |BL |Col| Eff |  `|
     * |---------------------------------------------------------------|
     * |      |Win|Mac|   |   |   |   |   |   |   |   |   |        |   |
     * |---------------------------------------------------------------|
     * |        |   |   |   |(r)|Bat|   |  -|SBr|SCo|SEf|  Alt |Br+|Hom|
     * |---------------------------------------------------------------|
     * |    |    |    |                           |   |   |Sp-|Br-|Sp+|
     * `---------------------------------------------------------------'
     * The number row gives F1..F12 in Win mode and media keys in Mac mode;
     * with Right Shift (Alt) also held, the other set (kb.c: frow()):
     *   Mac media: BrightnessDown BrightnessUp MissionControl Launchpad - -
     *              Previous Play Next Mute VolumeDown VolumeUp
     *   Win media: BrightnessDown BrightnessUp Alt+Tab Alt+Esc WWWHome Mail
     *              Previous Play Next Mute VolumeDown VolumeUp
     * Right Shift under Fn never reaches the host as Shift.
     * Bat = the battery level on 1 .. 0 while held (wireless, on battery), sends nothing
     * Lighting (backlight.c): BL = backlight and side lights on / off, Eff = next
     * effect, Col = next colour, Br+ / Br- = brightness, Sp+ / Sp- = speed (the
     * side lights' too), SEf / SCo / SBr = side effect / colour / brightness.
     * Under Fn the arrows are lighting keys and do not send arrows.
     * `  = grave (Shift, or Right Shift under Fn, gives ~)   Win / Mac = base layer (saved)   - = nothing
     * BT1-3 = Bluetooth slot (only in the Bluetooth position): selected on
     * release, hold 3 s to pair it; 24G = 2.4 GHz: hold 3 s to pair the dongle
     * (only in the 2.4 GHz position); (n) = T sends nothing
     * (reset) / (r): Fn + Backspace held, then Fn + V resets the settings
     * (kb.c, SETTINGS_RESET_HOLD_KEY / SETTINGS_RESET_KEY); both positions stay
     * transparent here and send nothing under Fn.
     */
    [_WIN_FL] = LAYOUT_65(
        KC_GRV,  FR_1,    FR_2,    FR_3,    FR_4,    FR_5,    FR_6,    FR_7,    FR_8,    FR_9,    FR_10,   FR_11,   FR_12,   _______,          KC_INS,
        _______, LNK_BT1, LNK_BT2, LNK_BT3, LNK_24G, KC_NO,   _______, KC_PSCR, KC_SCRL, KC_PAUS, _______, BL_TOG,  BL_COL,  BL_EFF,           KC_GRV,
        _______, OS_WIN,  OS_MAC,  _______, _______, _______, _______, _______, _______, _______, _______, _______,          _______,          _______,
        _______, _______, _______, _______, _______, BAT_SHOW,_______, KC_NO,   SL_BRI,  SL_COL,  SL_EFF,                    FN_ALT,  BL_BRI_UP, KC_HOME,
        _______, _______, _______,                   _______,                                     _______, _______,          BL_SPD_DN, BL_BRI_DN, BL_SPD_UP
    ),

    [_MAC_FL] = LAYOUT_65(
        KC_GRV,  FR_1,    FR_2,    FR_3,    FR_4,    FR_5,    FR_6,    FR_7,    FR_8,    FR_9,    FR_10,   FR_11,   FR_12,   _______,          KC_INS,
        _______, LNK_BT1, LNK_BT2, LNK_BT3, LNK_24G, KC_NO,   _______, KC_PSCR, KC_SCRL, KC_PAUS, _______, BL_TOG,  BL_COL,  BL_EFF,           KC_GRV,
        _______, OS_WIN,  OS_MAC,  _______, _______, _______, _______, _______, _______, _______, _______, _______,          _______,          _______,
        _______, _______, _______, _______, _______, BAT_SHOW,_______, KC_NO,   SL_BRI,  SL_COL,  SL_EFF,                    FN_ALT,  BL_BRI_UP, KC_HOME,
        _______, _______, _______,                   _______,                                     _______, _______,          BL_SPD_DN, BL_BRI_DN, BL_SPD_UP
    ),
};

// clang-format on

uint8_t layout_os_base_layer(bool is_mac)
{
    return is_mac ? _MAC_BL : _WIN_BL;
}

bool layout_is_fn_layer(uint8_t layer)
{
    return layer == _WIN_FL || layer == _MAC_FL;
}

bool layout_process_record(uint16_t keycode, bool key_pressed)
{
    keycode;
    key_pressed;
    return true;
}
