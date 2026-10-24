#include "mousekey.h"

#ifdef MOUSEKEY_ENABLE

#    include "kbdef.h"
#    include "keycodes.h"
#    include "report.h"
#    include "host.h"

#    ifndef MOUSEKEY_MS
#        error "MOUSEKEY_ENABLE needs MOUSEKEY_MS(), the board's 16-bit millisecond clock"
#    endif
uint16_t MOUSEKEY_MS(void);

// QMK's defaults for the accelerated mouse keys (ms, steps, counts), except
// MOUSEKEY_INTERVAL: 16 ms here, 20 in QMK, so the steps come a little faster.
#    ifndef MOUSEKEY_DELAY
#        define MOUSEKEY_DELAY 10
#    endif
#    ifndef MOUSEKEY_INTERVAL
#        define MOUSEKEY_INTERVAL 16
#    endif
#    ifndef MOUSEKEY_MOVE_DELTA
#        define MOUSEKEY_MOVE_DELTA 8
#    endif
#    ifndef MOUSEKEY_MAX_SPEED
#        define MOUSEKEY_MAX_SPEED 10
#    endif
#    ifndef MOUSEKEY_TIME_TO_MAX
#        define MOUSEKEY_TIME_TO_MAX 30
#    endif
#    ifndef MOUSEKEY_WHEEL_DELAY
#        define MOUSEKEY_WHEEL_DELAY 10
#    endif
#    ifndef MOUSEKEY_WHEEL_INTERVAL
#        define MOUSEKEY_WHEEL_INTERVAL 80
#    endif
#    ifndef MOUSEKEY_WHEEL_DELTA
#        define MOUSEKEY_WHEEL_DELTA 1
#    endif
#    ifndef MOUSEKEY_WHEEL_MAX_SPEED
#        define MOUSEKEY_WHEEL_MAX_SPEED 8
#    endif
#    ifndef MOUSEKEY_WHEEL_TIME_TO_MAX
#        define MOUSEKEY_WHEEL_TIME_TO_MAX 40
#    endif

// The held movement and wheel keys.
#    define MK_UP       0x01
#    define MK_DOWN     0x02
#    define MK_LEFT     0x04
#    define MK_RIGHT    0x08
#    define MK_MOVE     0x0F
#    define MK_WH_UP    0x10
#    define MK_WH_DOWN  0x20
#    define MK_WH_LEFT  0x40
#    define MK_WH_RIGHT 0x80
#    define MK_WHEEL    0xF0

static __xdata uint8_t        held;    // MK_ bits
static __xdata uint8_t        buttons; // bit n = KC_MS_BTN1 + n
static __xdata uint8_t        accel;   // bit n = KC_MS_ACCEL0 + n
static __xdata uint8_t        move_repeat;
static __xdata uint8_t        wheel_repeat;
static __xdata uint16_t       move_last; // MOUSEKEY_MS() at the last step
static __xdata uint16_t       wheel_last;
static __xdata report_mouse_t mouse_report;

// The step: the accel key's share of the maximum, else growing with the repeats.
static uint8_t step_unit(uint8_t repeat, uint8_t delta, uint8_t max_speed, uint8_t time_to_max)
{
    const uint16_t max = (uint16_t)delta * max_speed;
    uint16_t       unit;
    if (accel & 0x01) {
        unit = max / 4;
    } else if (accel & 0x02) {
        unit = max / 2;
    } else if (accel & 0x04) {
        unit = max;
    } else if (repeat == 0) {
        unit = delta;
    } else if (repeat >= time_to_max) {
        unit = max;
    } else {
        unit = (uint16_t)(max * repeat / time_to_max);
    }
    if (unit > 127) {
        return 127;
    }
    return unit ? (uint8_t)unit : 1;
}

// Both axes at once: each times 1/sqrt(2) (181/256), at least 1.
static int8_t diagonal(int8_t v)
{
    const uint8_t m = (uint8_t)(v < 0 ? -v : v);
    uint8_t       d = (uint8_t)(((uint16_t)m * 181u) >> 8);
    if (!d) {
        d = 1;
    }
    return v < 0 ? (int8_t)-d : (int8_t)d;
}

// +unit for `plus` alone, -unit for `minus` alone, 0 for both or neither.
static int8_t axis(uint8_t plus, uint8_t minus, uint8_t unit)
{
    const uint8_t p = held & plus;
    const uint8_t m = held & minus;
    if (p && !m) {
        return (int8_t)unit;
    }
    if (m && !p) {
        return (int8_t)-unit;
    }
    return 0;
}

static void send(int8_t x, int8_t y, int8_t v, int8_t h)
{
    mouse_report.report_id = REPORT_ID_MOUSE;
    mouse_report.buttons   = buttons;
    mouse_report.x         = x;
    mouse_report.y         = y;
    mouse_report.v         = v;
    mouse_report.h         = h;
    host_mouse_send(&mouse_report);
}

static void move_step(void)
{
    const uint8_t u = step_unit(move_repeat, MOUSEKEY_MOVE_DELTA, MOUSEKEY_MAX_SPEED, MOUSEKEY_TIME_TO_MAX);
    int8_t        x = axis(MK_RIGHT, MK_LEFT, u);
    int8_t        y = axis(MK_DOWN, MK_UP, u);
    if (x && y) {
        x = diagonal(x);
        y = diagonal(y);
    }
    if (x || y) {
        send(x, y, 0, 0);
    }
}

static void wheel_step(void)
{
    const uint8_t u = step_unit(wheel_repeat, MOUSEKEY_WHEEL_DELTA, MOUSEKEY_WHEEL_MAX_SPEED, MOUSEKEY_WHEEL_TIME_TO_MAX);
    int8_t        v = axis(MK_WH_UP, MK_WH_DOWN, u);
    int8_t        h = axis(MK_WH_RIGHT, MK_WH_LEFT, u);
    if (v && h) {
        v = diagonal(v);
        h = diagonal(h);
    }
    if (v || h) {
        send(0, 0, v, h);
    }
}

// The MK_ bit of a movement or wheel key, 0 for the others.
static uint8_t held_bit(uint16_t keycode)
{
    switch (keycode) {
        case KC_MS_UP:
            return MK_UP;
        case KC_MS_DOWN:
            return MK_DOWN;
        case KC_MS_LEFT:
            return MK_LEFT;
        case KC_MS_RIGHT:
            return MK_RIGHT;
        case KC_MS_WH_UP:
            return MK_WH_UP;
        case KC_MS_WH_DOWN:
            return MK_WH_DOWN;
        case KC_MS_WH_LEFT:
            return MK_WH_LEFT;
        case KC_MS_WH_RIGHT:
            return MK_WH_RIGHT;
        default:
            return 0;
    }
}

void mousekey_on(uint16_t keycode)
{
    const uint8_t bit = held_bit(keycode);
    if (bit & MK_MOVE) {
        held |= bit;
        move_last = MOUSEKEY_MS();
        move_step();
    } else if (bit) {
        held |= bit;
        wheel_last = MOUSEKEY_MS();
        wheel_step();
    } else if (keycode >= KC_MS_BTN1 && keycode <= KC_MS_BTN8) {
        buttons |= (uint8_t)(1u << (keycode - KC_MS_BTN1));
        send(0, 0, 0, 0);
    } else if (keycode >= KC_MS_ACCEL0 && keycode <= KC_MS_ACCEL2) {
        accel |= (uint8_t)(1u << (keycode - KC_MS_ACCEL0));
    }
}

void mousekey_off(uint16_t keycode)
{
    const uint8_t bit = held_bit(keycode);
    if (bit) {
        held &= (uint8_t)~bit;
        if (!(held & MK_MOVE)) {
            move_repeat = 0;
        }
        if (!(held & MK_WHEEL)) {
            wheel_repeat = 0;
        }
    } else if (keycode >= KC_MS_BTN1 && keycode <= KC_MS_BTN8) {
        buttons &= (uint8_t)~(1u << (keycode - KC_MS_BTN1));
        send(0, 0, 0, 0);
    } else if (keycode >= KC_MS_ACCEL0 && keycode <= KC_MS_ACCEL2) {
        accel &= (uint8_t)~(1u << (keycode - KC_MS_ACCEL0));
    }
}

void mousekey_task(void)
{
    if (!held) {
        return;
    }
    const uint16_t now = MOUSEKEY_MS();
    if ((held & MK_MOVE) && (uint16_t)(now - move_last) >= (move_repeat ? MOUSEKEY_INTERVAL : MOUSEKEY_DELAY)) {
        if (move_repeat != 0xFF) {
            move_repeat++;
        }
        move_last = now;
        move_step();
    }
    if ((held & MK_WHEEL) && (uint16_t)(now - wheel_last) >= (wheel_repeat ? MOUSEKEY_WHEEL_INTERVAL : MOUSEKEY_WHEEL_DELAY)) {
        if (wheel_repeat != 0xFF) {
            wheel_repeat++;
        }
        wheel_last = now;
        wheel_step();
    }
}

void mousekey_clear(void)
{
    held         = 0;
    accel        = 0;
    move_repeat  = 0;
    wheel_repeat = 0;
    if (buttons) {
        buttons = 0;
        send(0, 0, 0, 0);
    }
}

void mousekey_send_buttons(void)
{
    send(0, 0, 0, 0);
}

bool mousekey_active(void)
{
    return held || buttons;
}

#endif // MOUSEKEY_ENABLE
