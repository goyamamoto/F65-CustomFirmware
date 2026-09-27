#include "kbdef.h"
#include "layout.h"
#include "user_layout.h"
#include "report.h"
#include "kb.h"
#include <stdint.h>

// clang-format off

// Key positions follow the stock keymap table (col*6+row, 0xC800/0xCC00 in the
// stock image). Kxx_y = column xx, row y. The ANSI board leaves R1C14, R2C14,
// R3C12, R4C11, R4C12 and R5C3/4/6/7/11/12 unpopulated.
#define LAYOUT_75( \
    K00_0, K01_0, K02_0, K03_0, K04_0, K05_0, K06_0, K07_0, K08_0, K09_0, K10_0, K11_0, K12_0, K13_0, K14_0, K15_0, \
    K00_1, K01_1, K02_1, K03_1, K04_1, K05_1, K06_1, K07_1, K08_1, K09_1, K10_1, K11_1, K12_1, K13_1,        K15_1, \
    K00_2, K01_2, K02_2, K03_2, K04_2, K05_2, K06_2, K07_2, K08_2, K09_2, K10_2, K11_2, K12_2, K13_2,        K15_2, \
    K00_3, K01_3, K02_3, K03_3, K04_3, K05_3, K06_3, K07_3, K08_3, K09_3, K10_3, K11_3,        K13_3,        K15_3, \
    K00_4, K01_4, K02_4, K03_4, K04_4, K05_4, K06_4, K07_4, K08_4, K09_4, K10_4,               K13_4, K14_4, K15_4, \
    K00_5, K01_5, K02_5,               K05_5,               K08_5, K09_5, K10_5,               K13_5, K14_5, K15_5  \
) { \
    { K00_0, K01_0, K02_0, K03_0, K04_0, K05_0, K06_0, K07_0, K08_0, K09_0, K10_0, K11_0, K12_0, K13_0, K14_0, K15_0 }, \
    { K00_1, K01_1, K02_1, K03_1, K04_1, K05_1, K06_1, K07_1, K08_1, K09_1, K10_1, K11_1, K12_1, K13_1, KC_NO, K15_1 }, \
    { K00_2, K01_2, K02_2, K03_2, K04_2, K05_2, K06_2, K07_2, K08_2, K09_2, K10_2, K11_2, K12_2, K13_2, KC_NO, K15_2 }, \
    { K00_3, K01_3, K02_3, K03_3, K04_3, K05_3, K06_3, K07_3, K08_3, K09_3, K10_3, K11_3, KC_NO, K13_3, KC_NO, K15_3 }, \
    { K00_4, K01_4, K02_4, K03_4, K04_4, K05_4, K06_4, K07_4, K08_4, K09_4, K10_4, KC_NO, KC_NO, K13_4, K14_4, K15_4 }, \
    { K00_5, K01_5, K02_5, KC_NO, KC_NO, K05_5, KC_NO, KC_NO, K08_5, K09_5, K10_5, KC_NO, KC_NO, K13_5, K14_5, K15_5 }  \
}

#define _MAC_BL 0
#define _WIN_BL 1
#define _MAC_FL 2
#define _WIN_FL 3

#define FN_MAC MO(_MAC_FL)
#define FN_WIN MO(_WIN_FL)

const uint16_t keymaps[][MATRIX_ROWS][MATRIX_COLS] = {
    /* Keymap _MAC_BL: Mac base layer, laid out like an Apple keyboard. The F-row
     * sends F1-F4 and F7-F12 and Fn is the Apple fn key, so macOS decides what
     * they do: media functions by default, F-keys with Fn (or the other way round
     * with "Use F1, F2, etc. keys as standard function keys"). F5/F6 dim and
     * brighten the backlight (Fn gives F5/F6), as in the stock firmware. PrtSc /
     * Ins sit where the stock firmware has its screenshot and assistant keys.
     * Caps Lock and Left Ctrl are swapped. Cm* = Command when held; a tap sends
     * LANG2 (Eisu) on the left and LANG1 (Kana) on the right, for the Japanese IME.
     * ,---------------------------------------------------------------.
     * |Esc| F1| F2| F3| F4|Br-|Br+| F7| F8| F9|F10|F11|F12|PSc|Ins|Del|
     * |---------------------------------------------------------------|
     * | ` |  1|  2|  3|  4|  5|  6|  7|  8|  9|  0|  -|  =|  Bksp |PgU|
     * |---------------------------------------------------------------|
     * |Tab  |  Q|  W|  E|  R|  T|  Y|  U|  I|  O|  P|  [|  ]|    \|PgD|
     * |---------------------------------------------------------------|
     * |Ctrl  |  A|  S|  D|  F|  G|  H|  J|  K|  L|  ;|  '|  Enter |Hom|
     * |---------------------------------------------------------------|
     * |Shift   |  Z|  X|  C|  V|  B|  N|  M|  ,|  .|  /|Shift |Up |End|
     * |---------------------------------------------------------------|
     * |Cap|Opt|Cm*|          Space          |Cm*| Fn|Ctl|Lef|Dow|Rig|
     * `---------------------------------------------------------------'
     */
    [_MAC_BL] = LAYOUT_75(
        KC_ESC,  KC_F1,   KC_F2,   KC_F3,   KC_F4,   BRI_DN,  BRI_UP,  KC_F7,   KC_F8,   KC_F9,   KC_F10,  KC_F11,  KC_F12,  KC_PSCR, KC_INS,  KC_DEL,
        KC_GRV,  KC_1,    KC_2,    KC_3,    KC_4,    KC_5,    KC_6,    KC_7,    KC_8,    KC_9,    KC_0,    KC_MINS, KC_EQL,  KC_BSPC,          KC_PGUP,
        KC_TAB,  KC_Q,    KC_W,    KC_E,    KC_R,    KC_T,    KC_Y,    KC_U,    KC_I,    KC_O,    KC_P,    KC_LBRC, KC_RBRC, KC_BSLS,          KC_PGDN,
        KC_LCTL, KC_A,    KC_S,    KC_D,    KC_F,    KC_G,    KC_H,    KC_J,    KC_K,    KC_L,    KC_SCLN, KC_QUOT,          KC_ENT,           KC_HOME,
        KC_LSFT, KC_Z,    KC_X,    KC_C,    KC_V,    KC_B,    KC_N,    KC_M,    KC_COMM, KC_DOT,  KC_SLSH,                   KC_RSFT, KC_UP,   KC_END,
        KC_CAPS, KC_LALT, LGUI_T(KC_LNG2),                    KC_SPC,                    RGUI_T(KC_LNG1), FN_MAC, KC_RCTL,   KC_LEFT, KC_DOWN, KC_RGHT
    ),

    /* Keymap _WIN_BL: Windows base layer. The F-row sends F1-F12; hold Fn for
     * the media functions. Caps Lock and Left Ctrl are swapped. Al* = Alt when
     * held; a tap sends Muhenkan (INT5) on the left and Henkan (INT4) on the right.
     * ,---------------------------------------------------------------.
     * |Esc| F1| F2| F3| F4| F5| F6| F7| F8| F9|F10|F11|F12|PSc|Ins|Del|
     * |---------------------------------------------------------------|
     * | (number, letter and shift rows as on the Mac layer)           |
     * |---------------------------------------------------------------|
     * |Cap|Win|Al*|          Space          |Al*| Fn|Ctl|Lef|Dow|Rig|
     * `---------------------------------------------------------------'
     */
    [_WIN_BL] = LAYOUT_75(
        KC_ESC,  KC_F1,   KC_F2,   KC_F3,   KC_F4,   KC_F5,   KC_F6,   KC_F7,   KC_F8,   KC_F9,   KC_F10,  KC_F11,  KC_F12,  KC_PSCR, KC_INS,  KC_DEL,
        KC_GRV,  KC_1,    KC_2,    KC_3,    KC_4,    KC_5,    KC_6,    KC_7,    KC_8,    KC_9,    KC_0,    KC_MINS, KC_EQL,  KC_BSPC,          KC_PGUP,
        KC_TAB,  KC_Q,    KC_W,    KC_E,    KC_R,    KC_T,    KC_Y,    KC_U,    KC_I,    KC_O,    KC_P,    KC_LBRC, KC_RBRC, KC_BSLS,          KC_PGDN,
        KC_LCTL, KC_A,    KC_S,    KC_D,    KC_F,    KC_G,    KC_H,    KC_J,    KC_K,    KC_L,    KC_SCLN, KC_QUOT,          KC_ENT,           KC_HOME,
        KC_LSFT, KC_Z,    KC_X,    KC_C,    KC_V,    KC_B,    KC_N,    KC_M,    KC_COMM, KC_DOT,  KC_SLSH,                   KC_RSFT, KC_UP,   KC_END,
        KC_CAPS, KC_LGUI, LALT_T(KC_INT5),                    KC_SPC,                    RALT_T(KC_INT4), FN_WIN, KC_RCTL,   KC_LEFT, KC_DOWN, KC_RGHT
    ),

    /* Keymap _MAC_FL / _WIN_FL: function layer (hold Fn). The lighting and link
     * keys sit where the nuphy-air60 port has them, one row lower because of the
     * Air75's F-row.
     * ,---------------------------------------------------------------.
     * |Rst|   |   |   |   | F5| F6|   |   |   |   |   |   |   |   |   |  (Mac: the rest via the Apple fn key)
     * |Rst|BrD|BrU|   |   |BlD|BlU|Prv|Ply|Nxt|Mut|VoD|VoU|   |   |   |  (Win)
     * |---------------------------------------------------------------|
     * |   |   |   |   |   |   |   |   |   |   |   |   |   |       |   |
     * |---------------------------------------------------------------|
     * |UJIS |BT1|BT2|BT3|24G|   |   |   |   |   |   |BFl|BOn|  BOf|   |
     * |---------------------------------------------------------------|
     * |      |   |   |   |   |   |   |   |   |   |   |   |        |   |
     * |---------------------------------------------------------------|
     * |        |   |   |   |FRs|   |   |   |Sp-|Sp+| UL|      |Br+|   |
     * |---------------------------------------------------------------|
     * |   |   |   |                         |   |   |   |Fx-|Br-|Fx+|
     * `---------------------------------------------------------------'
     * Rst = reset (hold)   UJIS = US-JIS on/off   BT1/BT2/BT3 = Bluetooth slots   24G = 2.4 GHz
     * BFl/BOn/BOf = battery indicator flash / on / off     FRs = factory reset
     * Sp-/Sp+ = animation speed   UL = underglow mode   Br-/Br+ = brightness
     * Fx-/Fx+ = effect prev / next
     */
    [_MAC_FL] = LAYOUT_75(
        RST_HLD, _______, _______, _______, _______, KC_F5,   KC_F6,   _______, _______, _______, _______, _______, _______, _______, _______, _______,
        _______, _______, _______, _______, _______, _______, _______, _______, _______, _______, _______, _______, _______, _______,          _______,
        USJIS_TOG, LNK_BT1, LNK_BT2, LNK_BT3, LNK_24G, _______, _______, _______, _______, _______, _______, BAT_FL,  BAT_ON,  BAT_OFF,          _______,
        _______, _______, _______, _______, _______, _______, _______, _______, _______, _______, _______, _______,          _______,          _______,
        _______, _______, _______, _______, FCT_RST, _______, _______, _______, SPD_DN,  SPD_UP,  UL_MODE,                   _______, BRI_UP,  _______,
        _______, _______, _______,                            _______,                   _______, _______, _______,          FX_PREV, BRI_DN,  FX_NEXT
    ),

    [_WIN_FL] = LAYOUT_75(
        RST_HLD, KC_BRID, KC_BRIU, _______, _______, BL_DOWN, BL_UP,   KC_MRWD, KC_MPLY, KC_MFFD, KC_MUTE, KC_VOLD, KC_VOLU, _______, _______, _______,
        _______, _______, _______, _______, _______, _______, _______, _______, _______, _______, _______, _______, _______, _______,          _______,
        USJIS_TOG, LNK_BT1, LNK_BT2, LNK_BT3, LNK_24G, _______, _______, _______, _______, _______, _______, BAT_FL,  BAT_ON,  BAT_OFF,          _______,
        _______, _______, _______, _______, _______, _______, _______, _______, _______, _______, _______, _______,          _______,          _______,
        _______, _______, _______, _______, FCT_RST, _______, _______, _______, SPD_DN,  SPD_UP,  UL_MODE,                   _______, BRI_UP,  _______,
        _______, _______, _______,                            _______,                   _______, _______, _______,          FX_PREV, BRI_DN,  FX_NEXT
    ),
};

// clang-format on

uint8_t layout_os_base_layer(bool is_mac)
{
    return is_mac ? _MAC_BL : _WIN_BL;
}

bool kb_layer_is_apple_fn(uint8_t layer)
{
    return layer == _MAC_FL;
}

bool layout_process_record(uint16_t keycode, bool key_pressed)
{
    keycode;
    key_pressed;
    return true;
}
