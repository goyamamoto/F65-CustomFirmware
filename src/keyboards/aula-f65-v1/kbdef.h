#pragma once

#include "sh68f90.h"
#include "keycodes.h"
#include <stdint.h>
#include <stdbool.h>

// EPOMAKER x AULA F65, V1 ("Fn-Ctrl" version: USB 258A:010C, bcdDevice 0x0500,
// product "Gaming Keyboard"). The pin map comes from static analysis of the
// stock V1 image (docs/keyboards/aula-f65-v1.md), not from the PCB. The V2
// ("Alt-Fn") board drives its columns from other pins: do not write this image
// to a V2.

// Stock keymap tables (0xB400 in the image, copied to 0xCC00-0xD3FF at runtime)
// are indexed by scan slot * 6 + row bit, with 6 row bits. Row bit 0 (P7.0) is
// masked off in the stock scan (ORL A,#0xC1 at 0x6B8D) and its table row holds
// no physical key, so this port scans the 5 key rows only: R0-R4 here are the
// stock row bits 1-5.
#define MATRIX_ROWS 5
#define MATRIX_COLS 16

// Row Pin Bits (inputs, pulled up, low when a key on the driven column is down)
#define KB_R0_P7_1 _P7_1
#define KB_R1_P7_2 _P7_2
#define KB_R2_P7_3 _P7_3
#define KB_R3_P5_3 _P5_3
#define KB_R4_P5_4 _P5_4

// Row Pins
#define KB_R0 P7_1
#define KB_R1 P7_2
#define KB_R2 P7_3
#define KB_R3 P5_3
#define KB_R4 P5_4

// Column Pin Bits, in the stock scan order (jump table at 0x6ABD, active low).
// The stock scan also drives P4.6 and P7.4 as slots 16 and 17, but its keymap
// has no key there (they carry LED current only); they are left idle high.
#define KB_C0_P6_0  _P6_0
#define KB_C1_P6_1  _P6_1
#define KB_C2_P6_2  _P6_2
#define KB_C3_P6_3  _P6_3
#define KB_C4_P6_4  _P6_4
#define KB_C5_P6_5  _P6_5
#define KB_C6_P6_6  _P6_6
#define KB_C7_P6_7  _P6_7
#define KB_C8_P5_0  _P5_0
#define KB_C9_P5_1  _P5_1
#define KB_C10_P5_2 _P5_2
#define KB_C11_P5_7 _P5_7
#define KB_C12_P4_0 _P4_0
#define KB_C13_P4_2 _P4_2
#define KB_C14_P4_3 _P4_3
#define KB_C15_P4_5 _P4_5

// Column Pins
#define KB_C0  P6_0
#define KB_C1  P6_1
#define KB_C2  P6_2
#define KB_C3  P6_3
#define KB_C4  P6_4
#define KB_C5  P6_5
#define KB_C6  P6_6
#define KB_C7  P6_7
#define KB_C8  P5_0
#define KB_C9  P5_1
#define KB_C10 P5_2
#define KB_C11 P5_7
#define KB_C12 P4_0
#define KB_C13 P4_2
#define KB_C14 P4_3
#define KB_C15 P4_5

#define KB_C_P4_MASK (uint8_t)(KB_C12_P4_0 | KB_C13_P4_2 | KB_C14_P4_3 | KB_C15_P4_5)
#define KB_C_P5_MASK (uint8_t)(KB_C8_P5_0 | KB_C9_P5_1 | KB_C10_P5_2 | KB_C11_P5_7)
#define KB_C_P6_MASK (uint8_t)0xFF

// LED-only column slots of the stock scan (idle high = LEDs off).
#define LED_COL16_P4_6 _P4_6
#define LED_COL17_P7_4 _P7_4

// Backlight anodes: P1.0-P1.5, P2.0-P2.5, P3.0-P3.5 are the stock firmware's
// 18 PWM outputs (PWM20-25, PWM10-15, PWM00-05). The stock init drives them low
// and only a PWM pulse on one of them, with a column low, lights an LED. The
// latches stay 0 (dark whenever the PWM is off); led.c drives the PWM.
#define LED_P1_MASK (uint8_t)0x3F
#define LED_P2_MASK (uint8_t)0x3F
#define LED_P3_MASK (uint8_t)0x3F

// Connection switch, 3 positions (stock 0x80E4, pulled-up inputs):
// P0.5 low = 2.4 GHz, P0.4 low = Bluetooth, both high = USB (middle); read
// every 10 ms by f65_power.c (f65_switch_tick).
#define CONN_SW_24G P0_5
#define CONN_SW_BT  P0_4

// Power pins, mirrored from the stock firmware. P4.4 (input, no pull-up) reads
// high while USB power is present; the stock main loop drives P7.6 to its
// inverse (0x9E26). P7.7 (pulled up) reads low while charging. P4.1 and P7.5
// are outputs held low while running (P4.1 goes high before the stock enters
// sleep); what they switch is not known.
#define USB_PWR_DET P4_4
#define PWR_AUX_OUT P7_6
#define CHG_STAT    P7_7

// Radio module (f65_rf.c): EUART0 TXD P5.5 / RXD P5.6 at ~260870 baud, P0.2
// pulled low around each frame the MCU sends (the send request), P4.7 low
// while the module sends (high = module idle).

// user_settings.rf_link: bits 5:4 the transport (0 USB, 1 2.4 GHz, 2
// Bluetooth), bits 1:0 the Bluetooth slot; the stock default is USB, slot 1.
#define RF_LINK_DEFAULT 0x01

// Boot escape: the key held while the board powers up that jumps to the ISP
// bootloader (user_boot_escape). Matrix position (column, row): Esc.
#define BOOT_ESCAPE_KEY_COL 0
#define BOOT_ESCAPE_KEY_ROW 0

// Settings reset: hold Fn + SETTINGS_RESET_HOLD_KEY, then press Fn +
// SETTINGS_RESET_KEY (kb.c). Both are base-layer keycodes whose Fn-layer
// position must stay transparent; under Fn they send nothing.
#define SETTINGS_RESET_HOLD_KEY KC_BSPC
#define SETTINGS_RESET_KEY      KC_V

uint8_t layout_os_base_layer(bool is_mac);
bool    layout_is_fn_layer(uint8_t layer);

enum custom_keycodes {
    USJIS_TOG = SAFE_RANGE, // US-JIS on/off (persisted; only in force in Win mode)
    OS_WIN,               // Fn + A: Win base layer (persisted)
    OS_MAC,               // Fn + S: Mac base layer (persisted)
    LNK_24G,              // Fn + R: 2.4 GHz only - hold 3 s to pair the dongle
    LNK_BT1,              // Fn + Q / W / E: Bluetooth only - slot 1 / 2 / 3,
    LNK_BT2,              //   selected on release; hold 3 s to pair that slot
    LNK_BT3,
    FN_ALT,               // Right Shift under Fn: the alternate number-row set, no Shift
    FR_1,                 // Fn + 1 .. = : F1..F12 or media (kb.c, by OS mode and Right Shift)
    FR_2,
    FR_3,
    FR_4,
    FR_5,
    FR_6,
    FR_7,
    FR_8,
    FR_9,
    FR_10,
    FR_11,
    FR_12,
    BAT_SHOW,             // Fn + B: the battery level on 1 .. 0 while held (wireless, on battery); sends nothing
    // Lighting (backlight.c; ansi / usjis), on the stock's Fn keys where it has them:
    BL_TOG,    // Fn + [: backlight and side lights on / off (not on the stock)
    BL_EFF,    // Fn + \: next key effect (0 = off, 1-17 without 6, 9, 14)
    BL_COL,    // Fn + ]: next colour (R G B Y M C W, 7 = rainbow / random); the stock's Fn + Tab
    BL_BRI_UP, // Fn + Up / Down: brightness 0-4
    BL_BRI_DN,
    BL_SPD_UP, // Fn + Right / Left: speed 0-4 (faster / slower), the side lights' too
    BL_SPD_DN,
    SL_EFF,    // Fn + /: next side effect (off, rainbow wave, rainbow cycle, static, breathing); the stock's Fn + Right Shift
    SL_COL,    // Fn + .: next side colour (static and breathing); the stock's Fn + /
    SL_BRI,    // Fn + ,: next side brightness 0-4; the stock's Fn + .
#ifdef LED_DIAG
    DIAG_NEXT,  // leddiag: next cell
    DIAG_PREV,  // leddiag: previous cell
    DIAG_COLOR, // leddiag: next colour
    DIAG_NAME,  // leddiag: type what is lit
    DIAG_LEVEL, // leddiag: dim / indicator level
#endif

    KB_SAFE_RANGE,
};
