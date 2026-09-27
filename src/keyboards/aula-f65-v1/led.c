#include "led.h"
#include "kbdef.h"
#include "indicators.h"
#include "settings.h"
#include "usb.h"
#include "user_matrix.h"
#include "tick.h"
#include "f65_rf.h"
#include <stdint.h>
#include <stdbool.h>

// The column dwell below (a subframe's column stays lit only until the next
// scan, one subframe between two scans) holds only with smk's default of one
// LED subframe per scan (tick.c).
#ifdef LED_SUBFRAMES_PER_SCAN
#    if LED_SUBFRAMES_PER_SCAN != 1
#        error "aula-f65-v1: the LED column dwell needs LED_SUBFRAMES_PER_SCAN == 1"
#    endif
#endif

// LED engine for the aula-f65-v1. The hardware and the register values are the
// stock V1 firmware's (addresses below; the facts and the cell map are in
// the author's analysis notes, not published); the timing is smk's, as
// on the NuPhy Air75 (hardware-proven on the same MCU and core):
//
//   PWM banks 0-2 (PWM00-05 / 10-15 / 20-25, 1200 counts at Fsys/2 = 100 us)
//   drive the 18 LED anodes (6 rows x R/G/B); the key columns sink them. The
//   Timer2 tick (tick.c) alternates a matrix scan and an LED subframe. The scan
//   stops the PWM and releases every column first (indicators_pwm_disable), so
//   no key row is read with the PWM on. The subframe after it
//   (indicators_update_step) moves to the next of 20 slots and, if that slot's
//   column has something lit, loads its 18 DUTY2 values (stock order, loader
//   0x6C45), selects the column and only then starts the banks: the column is
//   lit for the rest of the subframe until the next scan stops it. Slots 16-19
//   and unlit columns show nothing.
//
//   The dwell is bounded by the Timer2 interrupt that runs the next scan, not
//   by a hardware timer that stops the PWM: the subframe re-arms Timer2 for
//   ~400 us (4 PWM periods), and the scan starts as soon as the level-0 Timer2
//   interrupt is taken - after the interrupt in service, if any (USB at level
//   0; EUART0 at level 3 only delays it by its own few microseconds). If the
//   scans stop altogether, the main loop's led_stall_check holds the LEDs dark
//   within ~5 ms.
//
//   There is no LED interrupt: everything runs in the Timer2 interrupt, at the
//   same priority as USB, and nothing in the LED path turns interrupts off
//   (the one exception: the first PWM set-up at start, ~35 us). build-4 had a
//   PWM0 period interrupt at priority 2 that nested over the USB interrupt; on
//   the board that build froze the host's input while typing with Caps Lock
//   toggles, where build-2 (no LED code at all) did not.
//
// Per LED: at most one subframe (<= 5 PWM periods at DUTY2 <= 0x0400) per 20
// subframes, i.e. per ~15 ms: about 3 % of the periods at most, below the stock
// indicators' 1 period in 20 (5 %).

// Stock phase table (CODE 0x2605), j = row * 3 + c: DUTY1 of the channel slot j
// is loaded into, and DUTY2 for "off".
static __code const uint8_t led_phase_tab[LED_CHANNELS] = {
    0xB5, 0xB4, 0xB6, 0xB8, 0xB7, 0xB9, 0xBB, 0xBA, 0xBC, 0xBE, 0xBD, 0xBF, 0xC1, 0xC0, 0xC2, 0xC4, 0xC3, 0xC5,
};

// Duty table in the stock frame buffer's layout (0x05A2 + column * 36 + j * 2,
// DUTY2 big-endian).
static __xdata uint8_t led_fb[LED_KEY_COLS][LED_CHANNELS * 2];
static __xdata uint8_t led_lit_col[LED_KEY_COLS]; // column has a channel above its phase
// Which of a column's 18 channels are above their phase (bit j % 8 of byte j / 8):
// led_set_duty keeps led_lit_col from these in constant time.
static __xdata uint8_t led_chan_on[LED_KEY_COLS][3];

// The backlight layer, the indicator overrides and the columns to reload (led.h).
__xdata uint8_t          led_fb8[LED_KEY_COLS][LED_ROWS][3];
__xdata uint8_t          led_dirty[LED_KEY_COLS];
__xdata uint8_t          led_rgb[3];
static __xdata uint8_t   led_ovr[LED_KEY_COLS][LED_ROWS];
static __xdata uint8_t   led_flush_at;
static __xdata uint8_t   led_any_dirty; // some led_dirty flag may be set (led_flush looks)
static __xdata uint8_t   led_blank;

static __data uint8_t          led_slot;
static volatile __data uint8_t led_holds;

_Static_assert(LED_SLOTS >= 20, "fewer than 20 slots would light an LED longer than the stock indicators");
_Static_assert(LED_SLOTS > LED_KEY_COLS, "every slot >= LED_KEY_COLS selects no column");

// Every column the LEDs share, released (high): the 16 key columns and the stock
// LED-only columns P4.6 / P7.4, which this port never drives low.
#define LED_COLS_OFF()                                  \
    do {                                                \
        P6 = KB_C_P6_MASK;                              \
        P5 |= KB_C_P5_MASK;                             \
        P4 |= (uint8_t)(KB_C_P4_MASK | LED_COL16_P4_6); \
        P7 |= LED_COL17_P7_4;                           \
    } while (0)

uint16_t led_phase(uint8_t row, uint8_t c)
{
    return led_phase_tab[(uint8_t)(row * 3 + c)];
}

// The stock "PWM off" (0xAD49, used before sleep): every channel's CON = 0x01,
// so the banks stop and the anode pins fall back to their port latches (0).
// Then every column released.
static void led_pwm_off(void)
{
    PWM00CON = 0x01;
    PWM01CON = 0x01;
    PWM02CON = 0x01;
    PWM03CON = 0x01;
    PWM04CON = 0x01;
    PWM05CON = 0x01;
    PWM10CON = 0x01;
    PWM11CON = 0x01;
    PWM12CON = 0x01;
    PWM13CON = 0x01;
    PWM14CON = 0x01;
    PWM15CON = 0x01;
    PWM20CON = 0x01;
    PWM21CON = 0x01;
    PWM22CON = 0x01;
    PWM23CON = 0x01;
    PWM24CON = 0x01;
    PWM25CON = 0x01;

    user_matrix_sinks_off(); // P1-P3 latches 0
    LED_COLS_OFF();
}

// One channel as the stock init (0x6313) sets it: DUTY1 = DUTY2 = phase.
#define LED_PWM_PHASE(ch, phase) \
    do {                         \
        ch##DUTY1H = 0;          \
        ch##DUTY1L = (phase);    \
        ch##DUTY2H = 0;          \
        ch##DUTY2L = (phase);    \
    } while (0)

// The stock PWM set-up, write for write (0x6313-0x64FA): per bank, CON of
// channels 1-5 = 0x08, period 0x04B0, DUTY1 = DUTY2 = phase per channel; then
// the three banks enabled with CON = 0x89 (Fsys/2). No column is selected, and
// DUTY2 = phase, so it is dark. The stock's period interrupt (0x90D7) is not
// enabled: nothing here uses it. DUTY1 and the periods stay from here on.
static void led_pwm_setup(void)
{
    PWM01CON  = 0x08;
    PWM02CON  = 0x08;
    PWM03CON  = 0x08;
    PWM04CON  = 0x08;
    PWM05CON  = 0x08;
    PWM0PERDH = 0x04;
    PWM0PERDL = 0xB0;
    LED_PWM_PHASE(PWM00, 0xC3);
    LED_PWM_PHASE(PWM01, 0xC4);
    LED_PWM_PHASE(PWM02, 0xC5);
    LED_PWM_PHASE(PWM03, 0xC0);
    LED_PWM_PHASE(PWM04, 0xC1);
    LED_PWM_PHASE(PWM05, 0xC2);

    PWM11CON  = 0x08;
    PWM12CON  = 0x08;
    PWM13CON  = 0x08;
    PWM14CON  = 0x08;
    PWM15CON  = 0x08;
    PWM1PERDH = 0x04;
    PWM1PERDL = 0xB0;
    LED_PWM_PHASE(PWM10, 0xBA);
    LED_PWM_PHASE(PWM11, 0xBB);
    LED_PWM_PHASE(PWM12, 0xBC);
    LED_PWM_PHASE(PWM13, 0xBD);
    LED_PWM_PHASE(PWM14, 0xBE);
    LED_PWM_PHASE(PWM15, 0xBF);

    PWM21CON  = 0x08;
    PWM22CON  = 0x08;
    PWM23CON  = 0x08;
    PWM24CON  = 0x08;
    PWM25CON  = 0x08;
    PWM2PERDH = 0x04;
    PWM2PERDL = 0xB0;
    LED_PWM_PHASE(PWM20, 0xB4);
    LED_PWM_PHASE(PWM21, 0xB5);
    LED_PWM_PHASE(PWM22, 0xB6);
    LED_PWM_PHASE(PWM23, 0xB7);
    LED_PWM_PHASE(PWM24, 0xB8);
    LED_PWM_PHASE(PWM25, 0xB9);

    PWM00CON = 0x89;
    PWM10CON = 0x89;
    PWM20CON = 0x89;
}

void led_init(void)
{
    led_holds = LED_HOLD_STOPPED;
    led_slot  = 0;

    for (uint8_t col = 0; col < LED_KEY_COLS; col++) {
        for (uint8_t j = 0; j < LED_CHANNELS; j++) {
            led_fb[col][(uint8_t)(j * 2)]     = 0;
            led_fb[col][(uint8_t)(j * 2 + 1)] = led_phase_tab[j];
        }
        led_lit_col[col]    = 0;
        led_chan_on[col][0] = 0;
        led_chan_on[col][1] = 0;
        led_chan_on[col][2] = 0;
        for (uint8_t row = 0; row < LED_ROWS; row++) {
            led_ovr[col][row]    = LED_OVR_NONE;
            led_fb8[col][row][0] = 0;
            led_fb8[col][row][1] = 0;
            led_fb8[col][row][2] = 0;
        }
        led_dirty[col] = 0;
    }
    led_flush_at  = 0;
    led_any_dirty = 0;
    led_blank     = 0;
}

uint8_t led_holds_get(void)
{
    return led_holds;
}

// A hold stops the PWM and releases the columns; while any is set, no subframe
// lights anything. Setting / clearing a hold is one instruction (orl / anl on a
// __data byte), so the Timer2 interrupt sees it whole; a subframe that ran just
// before is undone by the stop that follows.
void led_hold(uint8_t reason)
{
    led_holds |= reason;
    led_pwm_off();
}

void led_release(uint8_t reason)
{
    led_holds &= (uint8_t)~reason;
}

// --- hooks: every place the LEDs must be dark ---------------------------------

// main(), once the settings are loaded and USB has enumerated.
void indicators_start()
{
    __critical
    {
        led_pwm_setup(); // the stock sequence, not split by a scan
    }
    led_release(LED_HOLD_STOPPED);
}

// The matrix scan (src/smk/matrix.c) brackets its sweep with these: no key-row
// read with the PWM running or an LED column low. The PWM stays off after the
// scan; the next LED subframe starts it for the column it shows.
void indicators_pwm_disable()
{
    led_hold(LED_HOLD_SCAN);
}

void indicators_pwm_enable()
{
    led_release(LED_HOLD_SCAN);
}

// A settings save erases and programs flash with interrupts off (flash.c): the
// PWM must not run, nor a column stay selected, across it.
void settings_save_pre(void)
{
    led_hold(LED_HOLD_FLASH);
}

void settings_save_post(void)
{
    led_release(LED_HOLD_FLASH);
}

// f65_power.c, at the end of both wakes (LED_HOLD_SLEEP still set): the stock
// runs its PWM set-up (0x6313) again after a wake, and so does this - the
// PWM registers are not assumed to survive the power-down (clock and
// regulator off). Dark as at start: DUTY2 = phase, no column selected; then
// the next subframes light again.
void led_wake(void)
{
    __critical
    {
        led_pwm_setup();
    }
    led_release(LED_HOLD_SLEEP);
}

// usb_task(), right before the report-5 jump to the ISP bootloader (no return).
// The boot-escape jump needs nothing: it runs before led_init.
void usb_isp_prepare(void)
{
    led_hold(LED_HOLD_ISP);
}

// kb_update(): dark while the bus is suspended (the stock turns the PWM off on
// USB suspend, 0x9A67).
void led_suspend(bool on)
{
    if (on == ((led_holds & LED_HOLD_SUSPEND) != 0)) {
        return;
    }
    if (on) {
        led_hold(LED_HOLD_SUSPEND);
    } else {
        led_release(LED_HOLD_SUSPEND);
    }
}

// --- the LED subframe (Timer2 interrupt, right after a scan) ------------------

// Loads one column's 18 DUTY2 values in the stock order (0x6C45: PWM21 20 22 |
// 24 23 25 | 11 10 12 | 14 13 15 | 04 03 05 | 01 00 02), high byte first.
static void led_load(const __xdata uint8_t *p)
{
    PWM21DUTY2H = p[0];
    PWM21DUTY2L = p[1];
    PWM20DUTY2H = p[2];
    PWM20DUTY2L = p[3];
    PWM22DUTY2H = p[4];
    PWM22DUTY2L = p[5];
    PWM24DUTY2H = p[6];
    PWM24DUTY2L = p[7];
    PWM23DUTY2H = p[8];
    PWM23DUTY2L = p[9];
    PWM25DUTY2H = p[10];
    PWM25DUTY2L = p[11];
    PWM11DUTY2H = p[12];
    PWM11DUTY2L = p[13];
    PWM10DUTY2H = p[14];
    PWM10DUTY2L = p[15];
    PWM12DUTY2H = p[16];
    PWM12DUTY2L = p[17];
    PWM14DUTY2H = p[18];
    PWM14DUTY2L = p[19];
    PWM13DUTY2H = p[20];
    PWM13DUTY2L = p[21];
    PWM15DUTY2H = p[22];
    PWM15DUTY2L = p[23];
    PWM04DUTY2H = p[24];
    PWM04DUTY2L = p[25];
    PWM03DUTY2H = p[26];
    PWM03DUTY2L = p[27];
    PWM05DUTY2H = p[28];
    PWM05DUTY2L = p[29];
    PWM01DUTY2H = p[30];
    PWM01DUTY2L = p[31];
    PWM00DUTY2H = p[32];
    PWM00DUTY2L = p[33];
    PWM02DUTY2H = p[34];
    PWM02DUTY2L = p[35];
}

static void led_col_select(uint8_t col)
{
    switch (col) {
        case 0:
            KB_C0 = 0;
            break;
        case 1:
            KB_C1 = 0;
            break;
        case 2:
            KB_C2 = 0;
            break;
        case 3:
            KB_C3 = 0;
            break;
        case 4:
            KB_C4 = 0;
            break;
        case 5:
            KB_C5 = 0;
            break;
        case 6:
            KB_C6 = 0;
            break;
        case 7:
            KB_C7 = 0;
            break;
        case 8:
            KB_C8 = 0;
            break;
        case 9:
            KB_C9 = 0;
            break;
        case 10:
            KB_C10 = 0;
            break;
        case 11:
            KB_C11 = 0;
            break;
        case 12:
            KB_C12 = 0;
            break;
        case 13:
            KB_C13 = 0;
            break;
        case 14:
            KB_C14 = 0;
            break;
        case 15:
            KB_C15 = 0;
            break;
    }
}

// tick.c's LED subframe. The scan before it left the PWM stopped and every
// column released. Returns true when the rotation wraps (a frame).
bool indicators_update_step(keyboard_state_t *keyboard, uint8_t current_step)
{
    keyboard;
    current_step;

    rf_rx_frame_isr(); // the subframe stops the main loop for ~35 us: the radio's line first (f65_rf.c)

    // tick.c has just re-armed Timer2 for this subframe (systick_arm: TR2 off,
    // reload, TR2 on), so the next overflow is a whole subframe away. Until the
    // re-arm, Timer2 was still running on the scan's short (~100 us) reload,
    // and the handler clears TF2 before tick_dispatch: an overflow of that old
    // reload in between (the handler entered a few cycles before it) leaves TF2
    // set, the Timer2 interrupt comes again as soon as this one returns, and
    // tick_dispatch takes it for the next scan - this subframe's column is lit
    // for ~13 us instead of ~385 us. Where the scan ends on that reload's grid
    // is fixed by the code, and the delay before this handler (a main-loop
    // critical section) varies, so near the edge it happens now and then, on
    // whichever column is showing: the held indicators blinked, one key at a
    // time. Right on the edge it would repeat every subframe. A TF2 set now can
    // only be that stale one.
    TF2 = 0;

    if (++led_slot >= LED_SLOTS) {
        led_slot = 0;
    }
    if (led_holds || led_slot >= LED_KEY_COLS || !led_lit_col[led_slot]) {
        return led_slot == 0;
    }

    // PWM stopped: DUTY2 of the slot, its column, then per bank the Air75's
    // order - the bank started first (CON0 0x89: run, channel 0 routed), then
    // its channels 1-5 routed (CON 0x08). A channel is never routed to its pin
    // while its bank is stopped; the counters start at 0 and DUTY1 = phase
    // (>= 0xB4 counts, 15 us) keeps every pin low well past the ~5 routing
    // writes (the column is already low).
    led_load(led_fb[led_slot]);
    LED_COLS_OFF();
    led_col_select(led_slot);
    PWM00CON = 0x89;
    PWM01CON = 0x08;
    PWM02CON = 0x08;
    PWM03CON = 0x08;
    PWM04CON = 0x08;
    PWM05CON = 0x08;
    PWM10CON = 0x89;
    PWM11CON = 0x08;
    PWM12CON = 0x08;
    PWM13CON = 0x08;
    PWM14CON = 0x08;
    PWM15CON = 0x08;
    PWM20CON = 0x89;
    PWM21CON = 0x08;
    PWM22CON = 0x08;
    PWM23CON = 0x08;
    PWM24CON = 0x08;
    PWM25CON = 0x08;
    return false;
}

// --- backstop: the scans must keep coming ------------------------------------

// A lit subframe leaves its column low with the banks running until the next
// scan's Timer2 interrupt stops them. Should the scans stop (Timer2 masked or
// stuck) while the main loop runs, hold the LEDs dark once no scan has come
// for LED_STALL_MS on the 1 ms tick (PWM4, f65_rf.c); release when they come
// again. (A hang of the main loop itself is the watchdog's.)
void led_stall_check(void)
{
    static uint16_t seen_scans, seen_ms;
    const uint16_t  scans = tick_scans();
    const uint16_t  ms    = rf_ms();

    if (scans != seen_scans) {
        seen_scans = scans;
        seen_ms    = ms;
        if (led_holds & LED_HOLD_STALL) {
            led_release(LED_HOLD_STALL);
        }
    } else if (!(led_holds & LED_HOLD_STALL) && (uint16_t)(ms - seen_ms) >= LED_STALL_MS) {
        led_hold(LED_HOLD_STALL);
    }
}

// --- the duty table --------------------------------------------------------------

static uint16_t led_entry(uint8_t col, uint8_t j)
{
    return (uint16_t)(((uint16_t)led_fb[col][(uint8_t)(j * 2)] << 8) | led_fb[col][(uint8_t)(j * 2 + 1)]);
}

void led_set_duty(uint8_t col, uint8_t row, uint8_t c, uint16_t duty)
{
    if (col >= LED_KEY_COLS || row >= LED_ROWS || c >= 3) {
        return;
    }
    const uint8_t  j  = (uint8_t)(row * 3 + c);
    const uint16_t ph = led_phase_tab[j];

    // DUTY1 (the phase) <= DUTY2 <= 0x0400, whatever the caller asks for.
    if (duty < ph) {
        duty = ph;
    }
    if (duty > LED_DUTY_MAX) {
        duty = LED_DUTY_MAX;
    }

    const uint8_t bit = (uint8_t)(1u << (j & 7));
    __xdata uint8_t *on = &led_chan_on[col][(uint8_t)(j >> 3)];
    if (duty != ph) {
        *on |= bit;
    } else {
        *on &= (uint8_t)~bit;
    }
    const uint8_t lit = (led_chan_on[col][0] | led_chan_on[col][1] | led_chan_on[col][2]) != 0;

    // The subframe (Timer2 interrupt) reads an entry and the column's flag
    // together: mask only Timer2 while both change (a few instructions; USB is
    // not held up).
    const bool et2 = ET2;
    ET2            = 0;
    led_fb[col][(uint8_t)(j * 2)]     = (uint8_t)(duty >> 8);
    led_fb[col][(uint8_t)(j * 2 + 1)] = (uint8_t)duty;
    led_lit_col[col]                  = lit;
    ET2                               = et2;
}

void led_clear(void)
{
    for (uint8_t col = 0; col < LED_KEY_COLS; col++) {
        for (uint8_t row = 0; row < LED_ROWS; row++) {
            for (uint8_t c = 0; c < 3; c++) {
                led_set_duty(col, row, c, 0);
            }
        }
    }
}

// One column of the table per call (the next one each time), so a call stays short.
bool led_audit(void)
{
    static uint8_t col;
    bool           fixed = false;

    if (++col >= LED_KEY_COLS) {
        col = 0;
    }
    for (uint8_t j = 0; j < LED_CHANNELS; j++) {
        const uint16_t v  = led_entry(col, j);
        const uint16_t ph = led_phase_tab[j];
        if (v < ph || v > LED_DUTY_MAX) {
            fixed = true;
            led_set_duty(col, (uint8_t)(j / 3), (uint8_t)(j % 3), 0); // off
            led_mark_col(col);                                         // then the layers again
        }
        if (j == LED_CHANNELS / 2) {
            rf_rx_poll(); // halfway: a column takes ~100 us, longer than a short gap between two module frames
        }
    }
    return fixed;
}

// --- the layers (led.h) ----------------------------------------------------------

void led_mark_col(uint8_t col)
{
    led_dirty[col] = 1;
    led_any_dirty  = 1;
}

void led_mark_all(void)
{
    for (uint8_t col = 0; col < LED_KEY_COLS; col++) {
        led_dirty[col] = 1;
    }
    led_any_dirty = 1;
}

void led_cell_override(uint8_t col, uint8_t row, uint8_t rgb)
{
    if (col < LED_KEY_COLS && row < LED_ROWS && led_ovr[col][row] != rgb) {
        led_ovr[col][row] = rgb;
        led_mark_col(col);
    }
}

void led_set_blank(bool blank)
{
    if (led_blank != (uint8_t)blank) {
        led_blank = blank;
        led_mark_all();
    }
}

// One cell (row) of a column from the layers into the duty table; returns its
// lit channels. Each 16-bit entry is written with Timer2 masked (two stores),
// so a subframe never loads half of one.
#define LED_PUT(n, d)                                  \
    do {                                               \
        const uint16_t dd = (d);                       \
        ET2               = 0;                         \
        fb[(n) * 2]       = (uint8_t)(dd >> 8);        \
        fb[(n) * 2 + 1]   = (uint8_t)dd;               \
        ET2               = et2;                       \
    } while (0)
// phase + 3 x + (58 x >> 8), <= 0x03FB (8 x 8 multiplies)
#define LED_DUTY(n) \
    (uint16_t)(led_phase_tab[(uint8_t)(j + (n))] + (uint16_t)(v[n] * k3) + (uint8_t)((uint16_t)(v[n] * k58) >> 8))

static uint8_t led_flush_row(uint8_t col, uint8_t row)
{
    const uint8_t          k3  = 3;
    const uint8_t          k58 = 58;
    const uint8_t          j   = (uint8_t)(row * 3);
    const uint8_t          ov  = led_ovr[col][row];
    __xdata uint8_t       *fb  = &led_fb[col][(uint8_t)(j * 2)];
    const __xdata uint8_t *v   = &led_fb8[col][row][0];
    const bool             et2 = ET2;

    if (ov != LED_OVR_NONE || led_blank) {
        const uint8_t on = (ov == LED_OVR_NONE) ? 0 : ov;
        LED_PUT(0, (on & 1) ? LED_DUTY_ON : led_phase_tab[j]);
        LED_PUT(1, (on & 2) ? LED_DUTY_ON : led_phase_tab[(uint8_t)(j + 1)]);
        LED_PUT(2, (on & 4) ? LED_DUTY_ON : led_phase_tab[(uint8_t)(j + 2)]);
        return on & 7;
    }
    LED_PUT(0, LED_DUTY(0));
    LED_PUT(1, LED_DUTY(1));
    LED_PUT(2, LED_DUTY(2));
    return (uint8_t)(v[0] | v[1] | v[2]);
}

// Main loop: the next marked column (round robin), if any, with P4.7 looked at
// after each cell.
bool led_flush(void)
{
    if (!led_any_dirty) {
        return false;
    }
    uint8_t col = led_flush_at;
    uint8_t n   = LED_KEY_COLS;
    while (!led_dirty[col]) {
        col = (uint8_t)((col + 1) & (LED_KEY_COLS - 1));
        if (!--n) {
            led_any_dirty = 0;
            return false;
        }
    }
    led_dirty[col] = 0;
    led_flush_at   = (uint8_t)((col + 1) & (LED_KEY_COLS - 1));
    uint8_t lit    = 0;
    for (uint8_t row = 0; row < LED_ROWS; row++) {
        lit |= led_flush_row(col, row);
        rf_rx_poll();
    }
    // (led_set_duty keeps the per-channel flags; here the column as a whole)
    led_chan_on[col][0] = led_chan_on[col][1] = led_chan_on[col][2] = lit ? 0xFF : 0;
    led_lit_col[col]                                                = lit;
    return true;
}

// One backlight cell k (= column * 6 + row) in led_rgb: into led_fb8 and, unless
// an indicator holds the cell or the backlight is blank, straight into its three
// duty table entries (phase + 3 v + (58 v >> 8), each written with Timer2
// masked). A cell going lit lights its column; a cell going dark leaves the
// column's lit flag to led_flush. In assembly: it runs for every cell an effect
// paints (C: ~4x the time).
void led_px(uint8_t k) __naked
{
    k;
    // clang-format off
    __asm
    mov   a, dpl
    mov   r7, a                     ; k
    mov   dptr, #_led_rgb           ; r4, r5, r6 = the colour
    movx  a, @dptr
    mov   r4, a
    inc   dptr
    movx  a, @dptr
    mov   r5, a
    inc   dptr
    movx  a, @dptr
    mov   r6, a
    mov   a, r7                     ; led_fb8 + 3 k: r3 = the old value (any channel)
    mov   b, #3
    mul   ab
    add   a, #_led_fb8
    mov   dpl, a
    mov   a, b
    addc  a, #(_led_fb8 >> 8)
    mov   dph, a
    movx  a, @dptr
    mov   r3, a
    mov   a, r4
    movx  @dptr, a
    inc   dptr
    movx  a, @dptr
    orl   a, r3
    mov   r3, a
    mov   a, r5
    movx  @dptr, a
    inc   dptr
    movx  a, @dptr
    orl   a, r3
    mov   r3, a
    mov   a, r6
    movx  @dptr, a
    mov   a, r7                     ; held by an indicator, or blank: led_fb8 only
    add   a, #_led_ovr
    mov   dpl, a
    clr   a
    addc  a, #(_led_ovr >> 8)
    mov   dph, a
    movx  a, @dptr
    cpl   a
    jnz   00190$
    mov   dptr, #_led_blank
    movx  a, @dptr
    jnz   00190$
    mov   a, r7                     ; column = k / 6 (on the stack), j = (k % 6) * 3
    mov   b, #6
    div   ab
    push  acc
    mov   a, b
    mov   b, #3
    mul   ab
    mov   r2, a
    mov   a, r7                     ; r1:r0 = led_fb + 6 k
    mov   b, #6
    mul   ab
    add   a, #_led_fb
    mov   r0, a
    mov   a, b
    addc  a, #(_led_fb >> 8)
    mov   r1, a
    mov   a, r4
    lcall 00180$
    mov   a, r5
    lcall 00180$
    mov   a, r6
    lcall 00180$
    pop   acc
    mov   r7, a                     ; column
    mov   a, r4
    orl   a, r5
    orl   a, r6
    jz    00185$
    mov   a, r7                     ; lit: the column lit
    add   a, #_led_lit_col
    mov   dpl, a
    clr   a
    addc  a, #(_led_lit_col >> 8)
    mov   dph, a
    mov   a, #1
    movx  @dptr, a
    ret
00185$:
    mov   a, r3                     ; dark now, lit before: led_flush works the column out
    jz    00190$
    mov   a, r7
    add   a, #_led_dirty
    mov   dpl, a
    clr   a
    addc  a, #(_led_dirty >> 8)
    mov   dph, a
    mov   a, #1
    movx  @dptr, a
    mov   dptr, #_led_any_dirty
    movx  @dptr, a
00190$:
    ret
00180$:                             ; one channel: a = v, r1:r0 the entry, r2 = j
    mov   r7, a
    mov   b, #58
    mul   ab                        ; b = (58 v) >> 8
    mov   a, r2
    mov   dptr, #_led_phase_tab
    movc  a, @a+dptr
    add   a, b                      ; phase + (58 v >> 8) <= 0xFE
    xch   a, r7
    mov   b, #3
    mul   ab                        ; 3 v
    add   a, r7
    mov   r7, a                     ; low byte
    clr   a
    addc  a, b                      ; high byte
    mov   dpl, r0
    mov   dph, r1
    jbc   0xa8, 00181$                ; ET2 (IEN0 bit 0: bit address 0xA8)
    movx  @dptr, a
    inc   dptr
    mov   a, r7
    movx  @dptr, a
    sjmp  00182$
00181$:
    movx  @dptr, a                  ; Timer2 masked across the two bytes
    inc   dptr
    mov   a, r7
    movx  @dptr, a
    setb  0xa8
00182$:
    inc   dptr
    mov   r0, dpl
    mov   r1, dph
    inc   r2
    ret
    __endasm;
    // clang-format on
}
