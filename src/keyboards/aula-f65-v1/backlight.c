#include "backlight.h"
#include "backlight_tables.h"
#include "led.h"
#include "kbdef.h"
#include "settings.h"
#include "f65_rf.h"
#include "f65_power.h"
#include "tick.h"
#include <stddef.h>
#include <stdint.h>
#include <stdbool.h>

// The F65 V1's lighting effects 0-17 (6, 9 and 14 are not in the Fn + \\ cycle)
// and side-light effects, as the keyboard's original firmware shows them: for
// the same settings, keys and milliseconds every frame (led_fb8) is the same
// (tests/test_f65_backlight.py compares them in the simulator).
//
// How it runs:
//  - Only the 16 key columns are kept (columns 16-20 have no LED on this board).
//  - The effects paint every key cell; the indicators cover theirs (led.h
//    overrides), so a cell an indicator lets go shows the effect at once. Row 0
//    (the side lights) is the effects' reserved row.
//  - The direction flag of the per-effect settings has no key; it is always 0.
//  - A call reads the milliseconds since the last one (rf_ms), and its work runs
//    one unit per main-loop pass (a column, a ring, a few cells), so no pass gets
//    long; a call ends before the next one starts. When the work of a step is
//    longer than its period the effect runs slower.
//  - Effect 8 reads its position tables past their end; cells outside the frame
//    arrays count as 0.
//  - The arithmetic is done the 8051's cheap way: v * g / 80 as v * bri >> 2,
//    c * k / 255 as (x + (x >> 8) + 1) >> 8, rand % 192 from 8-bit remainders,
//    rand itself 8 steps at a time from tables (all exact).

_Static_assert(offsetof(user_settings_t, bl_magic) == SETTINGS_LEGACY_LEN, "the lighting fields come last");
_Static_assert(offsetof(user_settings_t, bl_cfg) + 2 * BL_EFFECTS == offsetof(user_settings_t, sl_effect), "bl_cfg");

#define NC    LED_KEY_COLS // 16
#define NCELL (NC * 6)     // cell k = col * 6 + row

extern uint8_t       matrix[MATRIX_COLS]; // src/smk/matrix.c: the debounced keys, a byte per column
extern volatile bool matrix_updated;      // ... set by each scan until matrix_task has looked

// --- per-cell state (cols 0-15) ------------------------------------
static __xdata uint8_t f2r[NCELL], f2g[NCELL], f2b[NCELL]; // the colour last given to a cell
static __xdata uint8_t lv[NCELL];                          // its level 0-32 (effect 15: palette index)
static __xdata uint8_t held[NC];                           // effect 8 stars rising / effect 12 keys held
static __xdata uint8_t f_lf[NC], f_rt[NC];                 // fronts moving left / right (4, 7, 13)
static __xdata uint8_t f_up[NC], f_dn[NC];                 // fronts moving up / down
static __xdata uint8_t drops[NC];                          // effect 5
static __xdata uint8_t sn_r, sn_c, sn_d, sn_e;             // effect 10's walker
static __xdata uint8_t ph;                                 // phase of effects 1, 2, 3, 11, 15, 16, 17
static __xdata uint8_t ec9;                                // effect 8 spawn counter
static __xdata uint8_t e13_cnt, e13_tog;
static __xdata uint8_t rnd_state[4]; // least significant byte first

// --- the task's state ------------------------------------------------------------------
static __xdata uint16_t c0303; // 1 ms step counter (saturating)
static __xdata uint8_t  c097e; // 1 ms sub-step counter
static __xdata uint8_t  cur_eff, cur_col, cur_bri, cur_spd;
static __xdata uint8_t  w_col, gb, w_spd;  // this call's colour, brightness, speed
static __xdata uint8_t  need_init, inited; // 0x24.1, 0x24.2
static __xdata uint8_t  key_k;             // the key this call took, 0xFF none
static __xdata uint8_t  ring[10], ring_wr, ring_rd;
static __xdata uint8_t  hue; // step scratch
#define cr led_rgb[0]        // the colour
#define cg led_rgb[1]
#define cb led_rgb[2]
static __xdata uint8_t rowc[6 * 3]; // effects 3 and 16: the colours of this step

// The call as a list of operations; a multi-unit one does one unit per pass.
#define OP_SIDE  1
#define OP_CLEAR 2 // fill 0, a column per unit
#define OP_INIT  3 // the case handler: init, key
#define OP_GATE  4 // the gates: which sub-step and step run
#define OP_DECAY 5 // a column per unit
#define OP_FADE8 6 // effect 8, a column per unit
#define OP_STEP  7 // the effect's step, units by effect
static __xdata uint8_t ops[7], n_ops, op_i, op_u, op_units, cleared;

// --- side lights ------------------------------------------------------------
static __xdata uint8_t s_ms, s_phase, s_reset;

// --- keys, power, saving -----------------------------------------------------------
static __xdata uint8_t  seen[MATRIX_COLS];
static __xdata uint16_t last_ms, pending_ms, tick_ms, scans_seen;
static __xdata uint16_t idle_ms, idle_at, save_at;
static __xdata uint8_t  running, save_due, batt_display, changed;

// v * [0, 20, 40, 60, 80][bri] / 80 = v * bri / 4
#define GAIN(v) ((uint8_t)((uint16_t)((uint8_t)(v) * gb) >> 2))

// ---------------------------------------------------------------- primitives
static __xdata uint8_t *cfg(uint8_t e)
{
    return &user_settings.bl_cfg[(uint8_t)(e * 2)];
}

// rand: a 32-bit Galois LFSR shifted 16 times (two table steps of 8).
static uint16_t rnd(void)
{
    __xdata uint8_t *s = rnd_state;
    if (!(s[0] | s[1] | s[2] | s[3])) {
        s[2] = 0xA5; // 0xA5A50000
        s[3] = 0xA5;
    }
    for (uint8_t i = 0; i < 2; i++) {
        const uint8_t lo = s[0];
        s[0]             = s[1] ^ bl_rnd0[lo];
        s[1]             = s[2] ^ bl_rnd1[lo];
        s[2]             = s[3] ^ bl_rnd2[lo];
        s[3]             = bl_rnd3[lo];
    }
    return (uint16_t)((uint16_t)(s[1] & 0x7F) << 8 | s[0]);
}

// x % 192 for x <= 0x7FFF (192 = 3 * 64; 256 = 1 mod 3)
static uint8_t mod192(uint16_t x)
{
    const uint8_t hi = (uint8_t)(x >> 8);
    const uint8_t q  = (uint8_t)((uint8_t)(hi << 2) | (uint8_t)((uint8_t)x >> 6)); // (x >> 6) & 0xFF
    uint8_t       s  = (uint8_t)((uint8_t)(q % 3) + (uint8_t)(hi >> 6));           // + (x >> 14)
    if (s >= 3) {
        s -= 3;
    }
    return (uint8_t)((uint8_t)(s << 6) | ((uint8_t)x & 63));
}

// c * k / 255 exact for c, k <= 255
static uint8_t div255(uint16_t x)
{
    return (uint8_t)((uint16_t)(x + (x >> 8) + 1) >> 8);
}

static void scale255(uint8_t k)
{
    cr = div255((uint16_t)(cr * k));
    cg = div255((uint16_t)(cg * k));
    cb = div255((uint16_t)(cb * k));
}

static void gain_rgb(void)
{
    cr = GAIN(cr);
    cg = GAIN(cg);
    cb = GAIN(cb);
}

// Key cell k (row 1-5), colour cr, cg, cb: led_fb8 and the duty table (led_px).
static void px(uint8_t k)
{
    led_px(k);
    rf_rx_poll(); // P4.7 at every cell (a frame boundary of the radio module)
}

static void wheel(uint8_t i)
{
    const uint8_t         k3 = 3; // (an 8 x 8 multiply)
    const __code uint8_t *w  = &bl_wheel[(uint16_t)(i * k3)];
    cr                       = w[0];
    cg                       = w[1];
    cb                       = w[2];
}

static void coltab(void) // the effect's colour 0-6 (every effect row the same)
{
    const __code uint8_t *c = &bl_coltab[(uint8_t)(w_col * 3)];
    cr                      = c[0];
    cg                      = c[1];
    cb                      = c[2];
}

// A cell's colour: the effect's colour, or colour 7 a random wheel entry.
static void pick(void)
{
    if (w_col == 7) {
        wheel(mod192(rnd()));
    } else {
        coltab();
    }
}

// The phase steps down, 0 wraps to mod - 1.
static uint8_t a11a(uint8_t mod)
{
    const uint8_t d = (cur_eff == 10) ? 20 : (cur_eff == 11 || cur_eff == 17) ? 2 : 1;
    ph              = (uint8_t)(ph - d);
    if (ph >= mod) {
        ph = (uint8_t)(mod - 1);
    }
    return ph;
}

static uint8_t hue_add(uint8_t h, uint8_t d, uint8_t mod) // h + d, wrapped (8 bit, - mod)
{
    h = (uint8_t)(h + d);
    if (h >= mod) {
        h = (uint8_t)(h - mod);
    }
    return h;
}

// Light a cell at level 32.
static void light(uint8_t row, uint8_t col)
{
    if (col >= 21) {
        return;
    }
    pick(); // the random colour is drawn even for a cell that is not shown
    if (col >= NC) {
        return;
    }
    const uint8_t k = (uint8_t)(col * 6 + row);
    f2r[k]          = cr;
    f2g[k]          = cg;
    f2b[k]          = cb;
    lv[k]           = 0x20;
    if (row) {
        gain_rgb();
        px(k);
    }
}

static void light_pos(uint8_t i) // a position ([row * 21 + col])
{
    light(bl_pos_row[i], bl_pos_col[i]);
}

// ---------------------------------------------------------------- column units
// A unit of a column operation is two rows of one column (48 per operation),
// so a main-loop pass paints at most two cells: u_col = u / 3, u_row = (u % 3) * 2.
static __xdata uint8_t u_col, u_row;

static void unit_rows(uint8_t u)
{
    u_col = (uint8_t)(u / 3);
    u_row = (uint8_t)((uint8_t)(u % 3) * 2);
}

// Clear two rows of a column: key cells black, fb2 and level 0 (every row).
static void clear_col(uint8_t col)
{
    cr = cg = cb = 0;
    uint8_t k    = (uint8_t)(col * 6 + u_row);
    for (uint8_t row = u_row; row < (uint8_t)(u_row + 2); row++, k++) {
        f2r[k] = 0;
        f2g[k] = 0;
        f2b[k] = 0;
        lv[k]  = 0;
        if (row) {
            px(k);
        }
    }
}

// Decay two rows of a column: every cell not held at fb2 * level / 32, then the level down.
static void decay_col(uint8_t col)
{
    const uint8_t h   = held[col];
    const uint8_t e13 = (cur_eff == 13);
    uint8_t       k   = (uint8_t)(col * 6 + u_row);
    uint8_t       m   = (uint8_t)(1u << u_row);
    for (uint8_t row = u_row; row < (uint8_t)(u_row + 2); row++, k++, m <<= 1) {
        if (h & m) {
            continue;
        }
        const uint8_t l = lv[k];
        if (row) {
            cr = GAIN((uint16_t)(f2r[k] * l) >> 5);
            cg = GAIN((uint16_t)(f2g[k] * l) >> 5);
            cb = GAIN((uint16_t)(f2b[k] * l) >> 5);
            px(k);
        }
        if (e13) {
            lv[k] = (l >= 2) ? (uint8_t)(l - 2) : 0;
        } else if (l) {
            lv[k] = (uint8_t)(l - 1);
        }
    }
    rf_rx_poll();
}

// Effect 8, two rows of a column: paint with the level, then held
// stars rise to 32 and fall.
static void fade8_col(uint8_t col)
{
    uint8_t k = (uint8_t)(col * 6 + u_row);
    uint8_t m = (uint8_t)(1u << u_row);
    for (uint8_t row = u_row; row < (uint8_t)(u_row + 2); row++, k++, m <<= 1) {
        uint8_t l = lv[k];
        if (row) {
            cr = GAIN((uint16_t)(f2r[k] * l) >> 5);
            cg = GAIN((uint16_t)(f2g[k] * l) >> 5);
            cb = GAIN((uint16_t)(f2b[k] * l) >> 5);
            px(k);
        }
        if (held[col] & m) {
            if (++l >= 33) {
                l = 32;
                held[col] &= (uint8_t)~m;
            }
        } else if (l) {
            l--;
        }
        lv[k] = l;
    }
    rf_rx_poll();
}

// ---------------------------------------------------------------- steps
// Up to five new stars; unit n = attempt n.
static void spawn8(uint8_t n)
{
    if (n == 0) {
        ec9++;
    }
    const uint16_t s   = (uint16_t)(rnd() + ec9);
    const uint8_t  idx = (uint8_t)((int16_t)s % 132); // signed remainder, low byte
    const uint8_t  col = bl_pos_col[idx];
    if (col >= 16) {
        return;
    }
    const uint8_t row = bl_pos_row[idx];
    rf_rx_poll();
    const uint8_t  ci = mod192(rnd());
    const uint16_t k  = (uint16_t)(col * 6) + row; // past cell 95 outside the arrays
    if (k < NCELL && lv[(uint8_t)k]) {
        return;
    }
    const uint8_t bit = (row < 8) ? (uint8_t)(1u << row) : 0;
    if (held[col] & bit) {
        return;
    }
    held[col] |= bit;
    if (row == 0 || k >= NCELL) {
        return;
    }
    if (w_col == 7) {
        wheel(ci);
    } else {
        coltab();
    }
    f2r[(uint8_t)k] = cr;
    f2g[(uint8_t)k] = cg;
    f2b[(uint8_t)k] = cb;
}

// Effect 5, a drop per column index falling one row per step; unit i = column index i.
static void step5(uint8_t i)
{
    if (i == 0) {
        const uint8_t j = (uint8_t)rnd() & 15;
        if (drops[j] == 0xFF) {
            drops[j] = 0;
        }
    }
    const uint8_t y = drops[i];
    if (y >= 6) {
        drops[i] = 0xFF;
        return;
    }
    light_pos((uint8_t)(y * 21 + i));
    drops[i] = (uint8_t)(y + 1);
}

// Effect 10, one cell per step on a serpentine over rows 1-5.
static void step10(void)
{
    light_pos((uint8_t)(sn_r * 21 + sn_c));
    if (sn_d) {
        if (++sn_c > 15) {
            sn_d = 0;
            sn_c = 15;
            sn_r = sn_e ? (uint8_t)(sn_r + 1) : (uint8_t)(sn_r - 1);
        }
    } else {
        if (--sn_c > 15) {
            sn_d = 0xFF;
            sn_c = 0;
            sn_r = sn_e ? (uint8_t)(sn_r + 1) : (uint8_t)(sn_r - 1);
        }
    }
    if (sn_r > 5) {
        sn_e = (uint8_t)~sn_e;
        sn_r = 4;
    } else if (sn_r == 0) {
        sn_e = (uint8_t)~sn_e;
        sn_r = 2;
    }
}

// Fronts: paint one column of the front masks.
static void front_col(uint8_t c)
{
    uint8_t m = (uint8_t)((uint8_t)(f_dn[c] | f_up[c] | f_lf[c] | f_rt[c]) >> u_row);
    for (uint8_t r = u_row, i = (uint8_t)(u_row * 21 + c); r < (uint8_t)(u_row + 2); r++, i += 21, m >>= 1) {
        // i = row * 21 + c; bits 6, 7 never shown
        if (m & 1) {
            light_pos(i);
            rf_rx_poll();
        }
    }
}

// Fronts: move every front one cell on; vertical fronts seed horizontal ones.
static void front_move(void)
{
    for (uint8_t c = 0; c < 16; c++) {
        f_up[c] >>= 1;
        f_dn[c] <<= 1;
    }
    rf_rx_poll();
    for (uint8_t c = 0; c < 15; c++) {
        f_lf[c] = f_lf[c + 1];
    }
    for (uint8_t c = 15; c; c--) {
        f_rt[c] = f_rt[c - 1];
    }
    rf_rx_poll();
    f_lf[15] = 0;
    f_rt[0]  = 0;
    rf_rx_poll();
    for (uint8_t c = 0; c < 16; c++) {
        const uint8_t v = (uint8_t)(f_up[c] | f_dn[c]);
        f_lf[c] |= v;
        f_rt[c] |= v;
    }
    rf_rx_poll();
    if (cur_eff == 13 && ++e13_cnt > 5) {
        e13_cnt         = 0;
        const uint8_t b = e13_tog ? 0x01 : 0x20;
        e13_tog ^= 1;
        f_up[6] |= b;
        f_dn[6] |= b;
        f_lf[6] |= b;
        f_rt[6] |= b;
    }
}

// Paint the gained cr, cg, cb (or rowc per row) on every key cell of matrix column u.
static void paint_keys(uint8_t u, bool per_row)
{
    uint8_t i = (uint8_t)(u * 6 + u_row);
    for (uint8_t row = u_row; row < (uint8_t)(u_row + 2); row++, i++) {
        const uint8_t v = bl_keypos[i];
        if (v == 0xFF || !(v & 7)) {
            continue;
        }
        if (per_row) {
            const __xdata uint8_t *c = &rowc[(uint8_t)(row * 3)];
            cr                       = c[0];
            cg                       = c[1];
            cb                       = c[2];
        }
        px((uint8_t)((v >> 3) * 6 + (v & 7)));
    }
}

// Paint position column c in the gained cr, cg, cb.
static void paint_pos_col(uint8_t c)
{
    for (uint8_t i = (uint8_t)(u_row * 21 + c), n = 2; n; i += 21, n--) {
        const uint8_t col = bl_pos_col[i];
        const uint8_t row = bl_pos_row[i];
        if (col < 16 && row) {
            px((uint8_t)(col * 6 + row));
        }
    }
}

// Rings around U; units 5 g .. 5 g + 4 = ring g, 3 cells each.
static void rings_unit(uint8_t u)
{
    const uint8_t g    = (uint8_t)(u / 5);
    const uint8_t part = (uint8_t)(u % 5);
    if (part == 0) {
        if (g == 0) {
            hue = (cur_eff != 1) ? a11a(0xC0) : 0;
        }
        if (w_col == 7) {
            wheel(hue);
        } else {
            coltab();
            if (cur_eff != 1) {
                scale255(bl_scale17[hue]);
            }
        }
        gain_rgb();
        rowc[0] = cr;
        rowc[1] = cg;
        rowc[2] = cb;
        hue     = hue_add(hue, 13, 0xC0);
    }
    cr                       = rowc[0];
    cg                       = rowc[1];
    cb                       = rowc[2];
    const uint8_t         i0 = (uint8_t)(part * 3);
    const __code uint8_t *r  = &bl_rings[(uint8_t)(g * 13)];
    for (uint8_t i = i0; i < (uint8_t)(i0 + 3) && i < 13; i++) {
        const uint8_t v = r[i];
        if (v == 0xFF || !(v & 7)) {
            continue; // row 0: reserved (tested on the ring's own cell)
        }
        const uint8_t p = (uint8_t)((v & 7) * 21 + (v >> 3));
        const uint8_t c = bl_pos_col[p];
        if (c < 16) {
            px((uint8_t)(c * 6 + bl_pos_row[p]));
        }
    }
}

// The step of the effect, unit u of op_units.
static void step_unit(uint8_t u)
{
    const uint8_t first = (u % 3) == 0; // the first unit of a column
    if (cur_eff != 1 && cur_eff != 17) {
        unit_rows(u);
    }
    switch (cur_eff) {
        case 1:
        case 17:
            rings_unit(u);
            break;
        case 2: // breathing; colour 7 a fixed rainbow across the columns
            if (u == 0) {
                a11a(0x80);
                hue = 0;
            }
            if (first) {
                if (w_col < 7) {
                    coltab();
                } else {
                    wheel(hue);
                    const uint8_t t = cr; // R <- byte 2, B <- byte 0
                    cr              = cb;
                    cb              = t;
                }
                scale255(bl_breath[ph]);
                gain_rgb();
                rowc[0] = cr;
                rowc[1] = cg;
                rowc[2] = cb;
                hue     = hue_add(hue, 11, 0xC0);
            }
            cr = rowc[0];
            cg = rowc[1];
            cb = rowc[2];
            for (uint8_t row = u_row, i = (uint8_t)(u_row * 21 + u_col); row < (uint8_t)(u_row + 2); row++, i += 21) {
                if (bl_keypos[(uint8_t)(u_col * 6 + row)] == 0xFF) {
                    continue;
                }
                const uint8_t pr = bl_pos_row[i];
                if (pr) {
                    px((uint8_t)(bl_pos_col[i] * 6 + pr));
                }
            }
            break;
        case 3: // one colour round the wheel
            if (u == 0) {
                wheel(a11a(0xC0));
                gain_rgb();
                rowc[0] = cr;
                rowc[1] = cg;
                rowc[2] = cb;
            }
            cr = rowc[0];
            cg = rowc[1];
            cb = rowc[2];
            paint_keys(u_col, false);
            break;
        case 4:
        case 7:
        case 13:
            front_col(u_col);
            if (u == 47) {
                front_move();
            }
            break;
        case 5:
            step5(u); // (16 units: one drop each)
            break;
        case 8:
            spawn8(u);
            break;
        case 10:
            step10();
            break;
        case 11: // a wave across the 16 position columns
            if (u == 0) {
                hue = a11a((w_col == 7) ? 192 : 96);
            }
            if (first) {
                if (w_col == 7) {
                    wheel(hue);
                    hue = hue_add(hue, 7, 192);
                } else {
                    coltab();
                    scale255(bl_scale17[hue]);
                    hue = hue_add(hue, 10, 96);
                }
                gain_rgb();
                rowc[0] = cr;
                rowc[1] = cg;
                rowc[2] = cb;
            }
            cr = rowc[0];
            cg = rowc[1];
            cb = rowc[2];
            paint_pos_col(u_col);
            break;
        case 15: // every cell one palette entry on
        {
            uint8_t k = (uint8_t)(u_col * 6 + u_row);
            for (uint8_t r = u_row, i = (uint8_t)(u_row * 21 + u_col); r < (uint8_t)(u_row + 2); r++, k++, i += 21) {
                uint8_t v = (uint8_t)(lv[k] - 1);
                if (v >= 128) {
                    v = 127;
                }
                lv[k]             = v;
                ph                = v;
                const uint8_t col = bl_pos_col[i];
                const uint8_t row = bl_pos_row[i];
                if (col < 16 && row) {
                    const uint8_t         k3 = 3;
                    const __code uint8_t *g  = &bl_grad15[(uint16_t)(v * k3)];
                    cr                       = GAIN(g[0]);
                    cg                       = GAIN(g[1]);
                    cb                       = GAIN(g[2]);
                    px((uint8_t)(col * 6 + row));
                }
            }
            break;
        }
        case 16: // a wheel colour per row
            if (u == 0) {
                uint8_t h = a11a(0xC0);
                for (uint8_t r = 0; r < 18; r += 3) {
                    wheel(h);
                    gain_rgb();
                    h                      = hue_add(h, 0x19, 0xC0);
                    rowc[r]                = cr;
                    rowc[(uint8_t)(r + 1)] = cg;
                    rowc[(uint8_t)(r + 2)] = cb;
                    rf_rx_poll();
                }
            }
            paint_keys(u_col, true);
            break;
    }
}

static uint8_t step_units(void)
{
    switch (cur_eff) {
        case 1:
            return (uint8_t)(ph * 5); // the rings lit so far (1-9), 5 units each
        case 17:
            return 45;
        case 5:
            return 16;
        case 8:
            return 5;
        case 10:
            return 1;
        default:
            return 48;
    }
}

// ---------------------------------------------------------------- side lights
// The side lights' speed follows the key speed: 0-1 slow, 2-3 medium, 4 fast.
static __code const uint8_t side_speed_of[5] = {0, 0, 1, 1, 2};

// Pair i = cells (9 + i, 0) and (i, 0), slot order B, R, G;
// cr, cg, cb already gained.
static void side_pair(uint8_t i)
{
    const uint8_t r = cr; // slot order B, R, G
    cr              = cb;
    cb              = cg;
    cg              = r;
    px((uint8_t)((9 + i) * 6));
    px((uint8_t)(i * 6));
    cg = cb; // back to R, G, B for the next pair
    cb = cr;
    cr = r;
}

// The side phase, direction 0 (no key sets it)
static void side_step(uint8_t mod)
{
    if (--s_phase >= mod) {
        s_phase = (uint8_t)(mod - 1);
    }
}

// c * bri / 4
static void side_gain(void)
{
    const uint8_t g = user_settings.sl_brightness;
    cr              = (uint8_t)((uint16_t)(cr * g) >> 2);
    cg              = (uint8_t)((uint16_t)(cg * g) >> 2);
    cb              = (uint8_t)((uint16_t)(cb * g) >> 2);
}

static void side_colour(void)
{
    const __code uint8_t *c = &bl_side_col[(uint8_t)(user_settings.sl_colour * 3)];
    cr                      = c[0];
    cg                      = c[1];
    cb                      = c[2];
}

static void side_call(void);

// The side lights in units: unit 0 the gate and the colour (and pair 0), units 1-6 the
// other pairs (side_units: 7 when the step paints, else 1).
static __xdata uint8_t s_all, s_p, s_units, s_rgb[3];

static void side_unit(uint8_t u)
{
    if (u == 0) {
        side_call();
        return;
    }
    if (s_all) {
        cr = s_rgb[0];
        cg = s_rgb[1];
        cb = s_rgb[2];
    } else {
        wheel(s_p);
        s_p = hue_add(s_p, 9, 192);
        side_gain();
    }
    side_pair(u);
}

static void side_call(void)
{
    const uint8_t e  = user_settings.sl_effect;
    uint8_t       sp = (uint8_t)(cfg(user_settings.bl_effect)[1] >> 4);
    if (sp > 4) {
        sp = 4;
    }
    const uint8_t t   = bl_side_t[side_speed_of[sp]];
    bool          all = false;

    s_units = 1;
    if (s_reset) {
        s_reset = 0;
        s_phase = 0;
    }
    switch (e) {
        case 0:
        case 3:
            if (s_ms > 90) {
                if (e) {
                    side_colour();
                    side_gain();
                } else {
                    cr = cg = cb = 0;
                }
                all  = true;
                s_ms = 0;
            }
            break;
        case 1: // rainbow wave: pair i at phase + 9 i (pairs 1-6 in the next units)
            if (s_ms > t) {
                side_step(192);
                wheel(s_phase);
                s_p = hue_add(s_phase, 9, 192);
                side_gain();
                side_pair(0);
                s_all   = 0;
                s_units = 7;
                s_ms    = 0;
            }
            break;
        case 2: // rainbow cycle
            if (s_ms > t) {
                side_step(192);
                wheel(s_phase);
                side_gain();
                all  = true;
                s_ms = 0;
            }
            break;
        case 4: // breathing
            if (s_ms > t) {
                side_step(128);
                side_colour();
                scale255(bl_breath[s_phase]);
                side_gain();
                all  = true;
                s_ms = 0;
            }
            break;
    }
    if (all) { // one colour on every pair (pairs 1-6 in the next units)
        side_pair(0);
        s_rgb[0] = cr;
        s_rgb[1] = cg;
        s_rgb[2] = cb;
        s_all    = 1;
        s_units  = 7;
    }
}

// ---------------------------------------------------------------- the call
static void ring_reset(void)
{
    ring_rd = 0;
    ring_wr = 0;
    for (uint8_t i = 0; i < 10; i++) {
        ring[i] = 0xFF;
    }
}

static void push(uint8_t op)
{
    ops[n_ops++] = op;
}

static void fronts_clear(void)
{
    for (uint8_t c = 0; c < NC; c++) {
        f_lf[c] = 0;
        f_rt[c] = 0;
        f_up[c] = 0;
        f_dn[c] = 0;
    }
}

// The case handler's init (when 0x24.1) and the key it took.
static void do_init(void)
{
    const uint8_t e = cur_eff;
    if (need_init) {
        need_init = 0;
        inited    = (e != 6 && e != 9 && e != 14);
        switch (e) {
            case 1:
            case 2:
            case 3:
            case 11:
            case 16:
            case 17:
                ph = 0;
                break;
            case 4:
            case 7:
                ec9 = 0;
                fronts_clear();
                break;
            case 13: // a seed at (6, 0)
                ec9 = 0;
                fronts_clear();
                f_up[6] = f_dn[6] = f_lf[6] = f_rt[6] = 0x01;
                e13_tog                               = 0;
                e13_cnt                               = 0;
                break;
            case 5:
                for (uint8_t i = 0; i < NC; i++) {
                    drops[i] = 0xFF;
                }
                break;
            case 10:
                sn_r = 1;
                sn_c = 0;
                sn_d = 0xFF;
                sn_e = 0xFF;
                break;
            case 15: // the seed pattern
                for (uint8_t k = 0; k < NCELL; k++) {
                    lv[k] = bl_seed15[k];
                    if ((k & 15) == 15) {
                        rf_rx_poll();
                    }
                }
                break;
        }
    }
    if (key_k != 0xFF) {
        const uint8_t v   = bl_keypos[key_k];
        const uint8_t row = v & 7;
        const uint8_t col = (uint8_t)((v >> 3) & 0x1F);
        key_k             = 0xFF;
        if (e == 12) { // the key's own cell
            if (v != 0xFF) {
                light_pos((uint8_t)(row * 21 + col));
            }
        } else if (col < NC) { // fronts from the key
            const uint8_t bit = (uint8_t)(1u << row);
            f_lf[col] |= bit;
            f_rt[col] |= bit;
            if (e == 7) {
                f_up[col] |= bit;
                f_dn[col] |= bit;
            }
        }
    }
}

// The gates: the sub-step and the step due this call.
static void do_gate(void)
{
    if (!inited) {
        return;
    }
    const uint8_t e = cur_eff;
    const uint8_t t = bl_speed[(uint8_t)(e * 5 + w_spd)];
    switch (e) {
        case 4:
        case 7:
        case 10:
            if (c097e > 15) {
                c097e = 0;
                push(OP_DECAY);
            }
            break;
        case 13:
            if (c097e > 2) {
                c097e = 0;
                push(OP_DECAY);
            }
            break;
        case 5:
            if (c097e > 10) {
                c097e = 0;
                push(OP_DECAY);
            }
            break;
        case 8:
            if (c097e >= 16) {
                c097e = 0;
                push(OP_FADE8);
            }
            break;
    }
    if (c0303 >= t) {
        c0303 = 0;
        if (e == 0) {
            return; // 0: clears every 100 ms; nothing else paints the key cells
        }
        if (e == 12) {
            push(OP_DECAY);
            return;
        }
        if (e == 1 && ph < 9) {
            ph++;
        }
        push(OP_STEP);
    }
}

// The start of a call: the milliseconds and the settings, the key, the change
// path; the rest is queued as operations.
static void start_call(void)
{
    const uint16_t n = pending_ms;
    pending_ms       = 0;
    c0303            = ((uint16_t)(0xFFFFu - c0303) < n) ? 0xFFFFu : (uint16_t)(c0303 + n);
    c097e += (uint8_t)n;
    s_ms += (uint8_t)n;

    const uint8_t          e = user_settings.bl_effect;
    const __xdata uint8_t *s = cfg(e);
    const uint8_t          m = e ? 4 : 0; // 0 for the off effect
    const uint8_t          b = (s[0] > m) ? m : s[0];
    w_spd                    = (uint8_t)(s[1] >> 4);
    if (w_spd > m) {
        w_spd = m;
    }
    w_col = s[1] & 0x0F;

    n_ops   = 0;
    op_i    = 0;
    op_u    = 0;
    cleared = 0;
    push(OP_SIDE);

    bool key = false;
    if (e == 4 || e == 7 || e == 12) {
        const uint8_t k = ring[ring_rd];
        if (k < 0x7E) {
            key_k         = k;
            ring[ring_rd] = 0xFF;
            key           = true;
            ring_rd       = (ring_rd == 9) ? 0 : (uint8_t)(ring_rd + 1);
            c0303         = 0;
            c097e         = 0;
        }
    }
    if (key || e != cur_eff || w_col != cur_col || b != cur_bri || w_spd != cur_spd) {
        if (e != cur_eff || w_col != cur_col) {
            cur_eff   = e;
            cur_col   = w_col;
            need_init = 1;
            inited    = 0;
            for (uint8_t c = 0; c < NC; c++) {
                held[c] = 0;
            }
            push(OP_CLEAR);
            cleared = 1;
        }
        if ((b != cur_bri || w_spd != cur_spd) && (e == 1 || e == 8)) {
            need_init = 1;
        }
        cur_bri = b;
        cur_spd = w_spd;
        if (e == 8 && need_init && !cleared) {
            push(OP_CLEAR);
        }
        push(OP_INIT);
    }
    gb = b;
    push(OP_GATE);
}

static uint8_t op_len(uint8_t op)
{
    switch (op) {
        case OP_CLEAR:
        case OP_DECAY:
        case OP_FADE8:
            return 3 * NC;
        case OP_STEP:
            return step_units();
        default:
            return 1;
    }
}

// One unit of the call in progress.
static void run_unit(void)
{
    const uint8_t op = ops[op_i];
    if (op_u == 0) {
        op_units = op_len(op);
    }
    if (op_units) {
        switch (op) {
            case OP_SIDE:
                side_unit(op_u);
                break;
            case OP_CLEAR:
                unit_rows(op_u);
                clear_col(u_col);
                break;
            case OP_INIT:
                do_init();
                break;
            case OP_GATE:
                do_gate();
                break;
            case OP_DECAY:
                unit_rows(op_u);
                decay_col(u_col);
                break;
            case OP_FADE8:
                unit_rows(op_u);
                fade8_col(u_col);
                break;
            case OP_STEP:
                step_unit(op_u);
                break;
        }
    }
    if (op == OP_SIDE && op_u == 0) {
        op_units = s_units; // 7 when the side step paints
    }
    if (++op_u >= op_units) {
        op_u = 0;
        op_i++;
    }
}

// ---------------------------------------------------------------- keys into the effects
static void key_event(uint8_t col, uint8_t row, bool pressed)
{
    const uint8_t e   = user_settings.bl_effect;
    const uint8_t bit = (uint8_t)(1u << row);
    if (e == 12) {
        if (pressed) {
            held[col] |= bit;
        } else {
            held[col] &= (uint8_t)~bit;
        }
    }
    if (e == 4 || e == 7 || e == 12) {
        if (pressed && ring[ring_wr] == 0xFF) {
            ring[ring_wr] = (uint8_t)(col * 6 + row);
            ring_wr       = (ring_wr == 9) ? 0 : (uint8_t)(ring_wr + 1);
        }
    } else if (ring_rd != ring_wr) {
        ring_reset();
    }
}

// ---------------------------------------------------------------- the task
void backlight_init(void)
{
    cur_eff = 0xFF; // the first call takes the change path
    key_k   = 0xFF;
    ring_reset();
    last_ms = rf_ms();
    idle_at = last_ms;
    for (uint8_t c = 0; c < MATRIX_COLS; c++) {
        seen[c] = matrix[c];
    }
}

void backlight_battery_display(bool on)
{
    batt_display = on;
}

static bool on_battery(void)
{
    return f65_transport() != RF_USB && !rf_external_power();
}

bool backlight_task(void)
{
    // A scan has come and matrix_task has not looked yet: no work on this pass,
    // so the main loop gets back to matrix_task (and a key to the host) sooner.
    if (matrix_updated) {
        return false;
    }
    const uint16_t now  = rf_ms();
    bool           keys = false;

    // Keys (the debounced matrix, after each scan): into the reactive effects,
    // and the idle time.
    const uint16_t sc = tick_scans();
    rf_rx_poll(); // after matrix_task's pass without a look (the change scan below is ~35 us)
    for (uint8_t col = (sc == scans_seen) ? MATRIX_COLS : 0; col < MATRIX_COLS; col++) {
        if (col == MATRIX_COLS / 2) {
            rf_rx_poll();
        }
        const uint8_t v  = matrix[col];
        const uint8_t ch = (uint8_t)(v ^ seen[col]);
        if (!ch) {
            continue;
        }
        seen[col] = v;
        keys      = true;
        uint8_t m = 1;
        for (uint8_t row = 1; row <= MATRIX_ROWS; row++, m <<= 1) {
            if (ch & m) {
                const bool pressed = (v & m) != 0;
                if (pressed) {
                    idle_ms = 0;
                    idle_at = now;
                }
                if (running) {
                    key_event(col, row, pressed);
                }
            }
        }
    }

    scans_seen = sc;

    // Once per ms: the idle time, the save, and whether the lighting runs.
    if (now != tick_ms || keys) {
        tick_ms = now;
        // On battery: dark after BL_IDLE_OFF_MS without a key (counted in 10 ms steps).
        const bool batt = on_battery();
        if (!batt) {
            idle_ms = 0;
            idle_at = now;
        } else if ((uint16_t)(now - idle_at) >= 10u) {
            idle_at += 10u;
            if (idle_ms < BL_IDLE_OFF_MS) {
                idle_ms += 10u;
            }
        }
        // A change is saved once, BL_SAVE_MS after the last one.
        if (save_due && (uint16_t)(now - save_at) >= BL_SAVE_MS) {
            save_due = 0;
            settings_mark_dirty();
        }
        const bool run = user_settings.bl_on && !batt_display && idle_ms < BL_IDLE_OFF_MS && !(batt && rf_battery_low()); // otherwise effect 0 and the side lights black
        if (run != running) {
            running = run;
            led_set_blank(!run);
            ring_reset(); // (keys pressed while paused are not kept)
        }
    }
    if (!running) {
        last_ms = now; // paused: an unfinished call finishes after the pause
        return !keys;
    }

    pending_ms += (uint16_t)(now - last_ms);
    last_ms = now;
    if (keys) {
        return false; // a key changed: this pass leaves the time to the report (matrix_task)
    }
    if (op_i >= n_ops) {
        if (!pending_ms && ring[ring_rd] >= 0x7E) {
            return true;
        }
        start_call(); // the work from the next pass on
        return true;
    }
    run_unit();
    return true;
}

// ---------------------------------------------------------------- settings
void backlight_defaults(void)
{
    user_settings.bl_magic  = BL_MAGIC;
    user_settings.bl_on     = 1;
    user_settings.bl_effect = 11;
    for (uint8_t e = 0; e < BL_EFFECTS; e++) {
        __xdata uint8_t *s = cfg(e);
        s[0]               = 2;            // brightness: the middle of 0-4
        s[1]               = (3 << 4) | 7; // speed 3, colour 7 (the default)
    }
    user_settings.sl_effect     = 1;
    user_settings.sl_colour     = 0;
    user_settings.sl_brightness = 2;
}

static bool effect_valid(uint8_t e)
{
    return e < BL_EFFECTS && e != 6 && e != 9 && e != 14;
}

void backlight_validate(void)
{
    if (user_settings.bl_magic != BL_MAGIC) {
        backlight_defaults();
        return;
    }
    if (user_settings.bl_on > 1) {
        user_settings.bl_on = 1;
    }
    if (!effect_valid(user_settings.bl_effect)) {
        user_settings.bl_effect = 11;
    }
    for (uint8_t e = 0; e < BL_EFFECTS; e++) {
        __xdata uint8_t *s = cfg(e);
        if (s[0] > 4) {
            s[0] = 2;
        }
        if ((s[1] >> 4) > 4 || (s[1] & 0x0F) > 7) {
            s[1] = (3 << 4) | 7;
        }
    }
    if (user_settings.sl_effect > 4) {
        user_settings.sl_effect = 1;
    }
    if (user_settings.sl_colour > 6) {
        user_settings.sl_colour = 0;
    }
    if (user_settings.sl_brightness > 4) {
        user_settings.sl_brightness = 2;
    }
}

// ---------------------------------------------------------------- keys
static void effect_next(void)
{
    uint8_t e = user_settings.bl_effect;
    do {
        e = (e >= 17) ? 0 : (uint8_t)(e + 1);
    } while (!effect_valid(e));
    user_settings.bl_effect = e;
    changed                 = 1;
}

static void cfg_step(uint8_t which, bool up) // which 0 brightness, 1 speed; 0-4, not past the ends
{
    const uint8_t    e = user_settings.bl_effect;
    __xdata uint8_t *s = cfg(e);
    const uint8_t    m = e ? 4 : 0;
    uint8_t          v = which ? (uint8_t)(s[1] >> 4) : s[0];
    if (up ? v < m : v > 0) {
        v = up ? (uint8_t)(v + 1) : (uint8_t)(v - 1);
        if (which) {
            s[1] = (uint8_t)((s[1] & 0x0F) | (uint8_t)(v << 4));
        } else {
            s[0] = v;
        }
        changed = 1;
    }
}

bool backlight_process_record(uint16_t keycode, bool pressed)
{
    if (keycode < BL_TOG || keycode > SL_BRI) {
        return true;
    }
    if (!pressed) {
        return false;
    }
    const uint8_t    e = user_settings.bl_effect;
    __xdata uint8_t *s = cfg(e);
    changed            = 0;
    if (keycode == BL_TOG) {
        user_settings.bl_on = !user_settings.bl_on;
        changed             = 1;
    } else if (user_settings.bl_on) { // off: the other lighting keys do nothing
        switch (keycode) {
            case BL_EFF:
                effect_next();
                break;
            case BL_COL: // not for 3, 15, 16, 17 (no colour choice); 0 has only colour 0
                if (e != 3 && e < 15) {
                    uint8_t c = (uint8_t)((s[1] & 0x0F) + 1);
                    if (c > (e ? 7 : 0)) {
                        c = 0;
                    }
                    s[1]    = (uint8_t)((s[1] & 0xF0) | c);
                    changed = 1;
                }
                break;
            case BL_BRI_UP:
            case BL_BRI_DN:
                cfg_step(0, keycode == BL_BRI_UP);
                break;
            case BL_SPD_UP:
            case BL_SPD_DN:
                if (e != 1) { // effect 1 has no speed
                    cfg_step(1, keycode == BL_SPD_UP);
                }
                break;
            case SL_EFF:
                user_settings.sl_effect = (user_settings.sl_effect >= 4) ? 0 : (uint8_t)(user_settings.sl_effect + 1);
                s_reset                 = 1;
                changed                 = 1;
                break;
            case SL_COL: // static colour and breathing only
                if (user_settings.sl_effect == 3 || user_settings.sl_effect == 4) {
                    user_settings.sl_colour = (user_settings.sl_colour >= 6) ? 0 : (uint8_t)(user_settings.sl_colour + 1);
                    changed                 = 1;
                }
                break;
            case SL_BRI:
                user_settings.sl_brightness = (user_settings.sl_brightness >= 4) ? 0 : (uint8_t)(user_settings.sl_brightness + 1);
                changed                     = 1;
                break;
        }
    }
    if (changed) {
        save_due = 1;
        save_at  = rf_ms();
    }
    return false;
}
