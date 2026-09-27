#include "led.h"
#include "kbdef.h"
#include "indicators.h"
#include "report.h"
#include "usb.h"
#include <stdint.h>
#include <stdbool.h>

// LED diagnostic (layout `leddiag` only): lights one channel of one cell at a
// time, dimly (DUTY2 = phase + 128, about 1/7 of the indicator level), to check
// on the board what static analysis could only infer: the PWM channel -> pin
// map, the anode polarity and the colours. Nothing is lit until the first key.
//
//   Fn + Right / Left  next / previous cell
//   Fn + Up            next colour (red -> green -> blue)
//   Fn + Down          types what should be lit, e.g. "esc red pwm24 p1.4 dim "
//   Fn + PgDn          dim (phase + 128) / indicator level (0x0400)
//
// Between two matrix scans the PWM runs about 4 periods, so each LED gets
// roughly a quarter of the stock's average on-time: "dim" is faint, best seen
// in a dark room; the indicator level is what the ansi / usjis images use.
//
// The cells cover every LED row (1-5 on column 0, plus keys in other columns)
// and two side-light cells, so every one of the 18 channels can be lit.

#define LED_DIAG_WIDTH 128

typedef struct {
    uint8_t     col;
    uint8_t     row; // LED row: 0 side lights, 1-5 = smk row + 1
    const char *name;
} diag_cell_t;

static __code const diag_cell_t diag_cells[] = {
    {0, 1, "esc"},    {0, 2, "tab"}, {0, 3, "caps"},  {0, 4, "lshift"}, {0, 5, "lctrl"},   {6, 2, "y"},
    {13, 3, "enter"}, {14, 4, "up"}, {9, 5, "fn"},    {15, 5, "right"}, {0, 0, "side b0"}, {9, 0, "side a0"},
};
#define DIAG_CELLS (uint8_t)(sizeof(diag_cells) / sizeof(diag_cells[0]))

// Channel (PWM bank/number, as hex digits) that slot channel j loads: the stock
// loader order (0x6C45). Pin: PWM0n = P3.n, PWM1n = P2.n, PWM2n = P1.n (inferred).
static __code const uint8_t diag_pwm[LED_CHANNELS] = {
    0x21, 0x20, 0x22, 0x24, 0x23, 0x25, 0x11, 0x10, 0x12, 0x14, 0x13, 0x15, 0x04, 0x03, 0x05, 0x01, 0x00, 0x02,
};

static __code const char *const diag_colour_names[3] = {"red", "green", "blue"};

#define DIAG_NONE 0xFF

static uint8_t diag_cell = DIAG_NONE; // index into diag_cells
static uint8_t diag_colour;           // 0 red, 1 green, 2 blue
static uint8_t lit_col, lit_row, lit_c;
static bool    lit;
static bool    diag_full; // indicator level instead of dim
static bool    dirty;

static __xdata char diag_text[48];
static uint8_t      diag_len, diag_pos;
static bool         diag_key_down;

void indicators_init()
{
    led_init();
}

void indicators_usjis_changed(bool enabled)
{
    enabled;
}

void indicators_battery_show(bool on)
{
    on; // no battery display in the diagnostic
}

// Slot channel of a colour: keys 0 red / 1 green / 2 blue; the side lights
// (row 0) 1 red / 2 green / 0 blue (stock writer 0x6FFB).
static uint8_t diag_channel(uint8_t row, uint8_t colour)
{
    if (row == 0) {
        return (uint8_t)((colour + 1) % 3);
    }
    return colour;
}

static void diag_put(char ch)
{
    if (diag_len < sizeof(diag_text)) {
        diag_text[diag_len++] = ch;
    }
}

static void diag_puts(const char *s)
{
    while (*s) {
        diag_put(*s++);
    }
}

static char diag_hex(uint8_t n)
{
    return (char)(n < 10 ? '0' + n : 'a' + n - 10);
}

static void diag_describe(void)
{
    if (diag_len != diag_pos) {
        return; // still typing the last one
    }
    diag_len = diag_pos = 0;
    if (diag_cell == DIAG_NONE) {
        diag_puts("none ");
        return;
    }
    const diag_cell_t *cell = &diag_cells[diag_cell];
    const uint8_t      pwm  = diag_pwm[(uint8_t)(cell->row * 3 + diag_channel(cell->row, diag_colour))];
    diag_puts(cell->name);
    diag_put(' ');
    diag_puts(diag_colour_names[diag_colour]);
    diag_puts(" pwm");
    diag_put(diag_hex(pwm >> 4));
    diag_put(diag_hex(pwm & 0x0F));
    diag_puts(" p");
    diag_put(diag_hex((uint8_t)(3 - (pwm >> 4))));
    diag_put('.');
    diag_put(diag_hex(pwm & 0x0F));
    diag_puts(diag_full ? " full " : " dim ");
}

bool led_diag_process_record(uint16_t keycode, bool pressed)
{
    switch (keycode) {
        case DIAG_NEXT:
            if (pressed) {
                diag_cell = (diag_cell == DIAG_NONE || diag_cell + 1 >= DIAG_CELLS) ? 0 : (uint8_t)(diag_cell + 1);
                dirty     = true;
            }
            return false;
        case DIAG_PREV:
            if (pressed) {
                diag_cell = (diag_cell == DIAG_NONE || diag_cell == 0) ? (uint8_t)(DIAG_CELLS - 1) : (uint8_t)(diag_cell - 1);
                dirty     = true;
            }
            return false;
        case DIAG_COLOR:
            if (pressed) {
                if (diag_cell == DIAG_NONE) {
                    diag_cell = 0;
                } else {
                    diag_colour = (uint8_t)((diag_colour + 1) % 3);
                }
                dirty = true;
            }
            return false;
        case DIAG_NAME:
            if (pressed) {
                diag_describe();
            }
            return false;
        case DIAG_LEVEL:
            if (pressed) {
                diag_full = !diag_full;
                dirty     = true;
            }
            return false;
        default:
            return true;
    }
}

static uint8_t diag_keycode(char ch)
{
    if (ch >= 'a' && ch <= 'z') {
        return (uint8_t)(KC_A + (ch - 'a'));
    }
    if (ch >= '1' && ch <= '9') {
        return (uint8_t)(KC_1 + (ch - '1'));
    }
    if (ch == '0') {
        return KC_0;
    }
    if (ch == '.') {
        return KC_DOT;
    }
    return KC_SPC;
}

void indicators_render()
{
    led_stall_check();

    if (dirty) {
        dirty = false;
        if (lit) {
            led_set_duty(lit_col, lit_row, lit_c, 0);
            lit = false;
        }
        if (diag_cell != DIAG_NONE) {
            lit_col = diag_cells[diag_cell].col;
            lit_row = diag_cells[diag_cell].row;
            lit_c   = diag_channel(lit_row, diag_colour);
            led_set_duty(lit_col, lit_row, lit_c, diag_full ? LED_DUTY_ON : (uint16_t)(led_phase(lit_row, lit_c) + LED_DIAG_WIDTH));
            lit = true;
        }
    }

    // One report per pass: the key of the next character down, then up.
    if (diag_pos < diag_len && usb_is_configured()) {
        const uint8_t kc = diag_keycode(diag_text[diag_pos]);
        if (!diag_key_down) {
            add_key(kc);
            diag_key_down = true;
        } else {
            del_key(kc);
            diag_key_down = false;
            diag_pos++;
        }
        send_keyboard_report();
    }
}
