// The AULA F65 V1 radio module link. Behaviour and byte layouts follow the V1
// stock firmware; each block names the stock routine it follows (see f65_rf.h).
// The Thaolia/smk_aulaF75 port (GPL-2.0) of the same module family is the
// model for the structure; where the F65 stock differs, the stock wins.

#include "f65_rf.h"
#include "kbdef.h"
#include "sh68f90.h"
#include "interrupts.h"
#include "keyboard.h"
#include "delay.h"
#include "watchdog.h"
#include "usbhw.h"
#include <string.h>

#ifndef RF_EUART0
#    error "f65_rf.c needs RF_EUART0 (the EUART0 vector is claimed here)"
#endif

// ------------------------------------------------------------------ 1 ms tick
//
// PWM4 as a timer only, exactly as the stock 0xAEAD: PWM40CON = 0x03 (Fsys/8),
// period 3000 → 1 ms, then PWM40CON = 0xC3 (run + period interrupt). Bit 3
// (output) stays 0 and PWM41/42 and the duties are never written, so no pin is
// driven (PWM4's pins are key columns here). The ISR clears the period flag
// (bit 5) as the stock does at 0x80C6.

#define PWM4_CON_SETUP 0x03
#define PWM4_CON_RUN   0xC3
#define PWM4_PERIOD    3000u
#define PWM4_FLAG      0x20

static volatile uint16_t       ms_now;
static volatile __data uint8_t ms_lo; // ms_now's low byte, one instruction to read anywhere
static volatile uint16_t       idle_ms;
static volatile uint16_t       idle_s;
static volatile uint16_t       usb_quiet_ms; // since the last USB interrupt or key (stock 0x02E6)

// Receive buffers: the ISR fills one bank while the main loop parses the
// frames waiting in the others. A frame ends when P4.7 is high (the module
// is idle) with bytes received; the 1 ms tick and the main loop's polls look
// at it (the stock samples it on every 100 µs scan slot, 0x6C18). Up to
// RX_BANKS - 1 frames wait for the parser (rf_task, once per main-loop pass,
// about 0.5 ms with the backlight running): with two banks a second frame
// that ended before the pass took the first was dropped (build-6 review,
// B6R-6: 2 of 40 phases in the simulator; the stock parses its one buffer
// from an interrupt, so it is never far behind). A frame beyond
// the waiting ones is dropped, and the next frame received from the start.
#define RX_MAX   23 // the stock's IDATA 0x54-0x6A
#define RX_BANKS 3

static __xdata uint8_t                  rx_buf0[RX_MAX];
static __xdata uint8_t                  rx_buf1[RX_MAX];
static __xdata uint8_t                  rx_buf2[RX_MAX];
static __xdata uint8_t *const __code    rx_bank_ptr[RX_BANKS] = {rx_buf0, rx_buf1, rx_buf2};
static volatile __data uint8_t          rx_idx;     // bytes received into the filling bank
static volatile __data uint8_t          rx_bank;    // the bank being filled
static volatile __data uint8_t          rx_ready_n; // frames waiting for the parser
static __xdata uint8_t *volatile __data rx_wr;      // = rx_bank_ptr[rx_bank] (no multiply in the ISR)
static volatile __xdata uint8_t         rx_len[RX_BANKS];
static __xdata uint8_t                  rx_ready_rd; // the bank of the oldest waiting frame

// A frame has ended (P4.7 high, bytes received). Interrupts off around it: the
// 1 ms tick and rx_take_done (main loop) both run it.
#define RX_FRAME_DONE()                   \
    do {                                  \
        if (rx_ready_n < RX_BANKS - 1) {  \
            rx_len[rx_bank] = rx_idx;     \
            rx_ready_n++;                 \
            if (++rx_bank == RX_BANKS) {  \
                rx_bank = 0;              \
            }                             \
            rx_wr = rx_bank_ptr[rx_bank]; \
        }                                 \
        rx_idx = 0;                       \
    } while (0)
static volatile __data uint8_t wake_pulse;  // 1 ms ticks until P0.2 is released
static volatile __bit          tx_busy;     // a frame is going out (EUART0 below)
static volatile __data uint8_t tx_end_tick; // ms_lo when the last frame ended
#define TX_GAP_TICKS 2                      // ticks from the end of a frame to the next (> 1 ms)

void pwm4_ms_tick_interrupt_handler(void) __interrupt(_INT_PWM4)
{
    PWM40CON &= (uint8_t)~PWM4_FLAG;
    ms_now++;
    ms_lo++;
#ifdef USB_IRQ_ACTIVITY
    // The stock clears its counter in the USB interrupt (0x954B) and counts it
    // up on the 1 ms tick; the wired sleep waits for 10 s of it (0x8BC1).
    if (usb_irq_activity) {
        usb_irq_activity = 0;
        usb_quiet_ms     = 0;
    } else if (usb_quiet_ms != 0xFFFF) {
        usb_quiet_ms++;
    }
#endif
    if (++idle_ms >= 1001) { // the stock counts 1001 ticks per idle second (0x0F51)
        idle_ms = 0;
        if (idle_s != 0xFFFF) {
            idle_s++;
        }
    }

    if (wake_pulse && !--wake_pulse) {
        if (!tx_busy) {
            P0CR &= (uint8_t)~0x04;
            P0_2        = 1;
            tx_end_tick = ms_lo; // the next frame keeps the gap after the pulse too
        }
    }

    // Frame boundary: the module has stopped sending (P4.7 back high).
    if (P4_7 && rx_idx) {
        __critical
        {
            RX_FRAME_DONE();
        }
    }
}

void rf_ms_tick_init(void)
{
    PWM40CON  = PWM4_CON_SETUP;
    PWM4PERDH = (uint8_t)(PWM4_PERIOD >> 8);
    PWM4PERDL = (uint8_t)(PWM4_PERIOD & 0xFF);
    PWM40CON  = PWM4_CON_RUN;
    IEN1 |= _EPWM4;
}

uint16_t rf_ms(void)
{
    uint16_t t;
    __critical
    {
        t = ms_now;
    }
    return t;
}

void rf_note_activity(void)
{
    __critical
    {
        idle_s       = 0;
        idle_ms      = 0;
        usb_quiet_ms = 0; // a key clears the stock's USB counter too (0xAFFA)
    }
}

uint16_t rf_usb_quiet_ms(void)
{
    uint16_t q;
    __critical
    {
        q = usb_quiet_ms;
    }
    return q;
}

uint16_t rf_idle_seconds(void)
{
    uint16_t s;
    __critical
    {
        s = idle_s;
    }
    return s;
}

// ------------------------------------------------------------------ EUART0
//
// ISR 0xA26A: RI first, else TI (a pending flag re-enters); the error flags
// FE / RXOV / TXCOL (SCON[7:5] with SSTAT) are cleared on every entry. It calls
// nothing, so it shares no overlay with the main loop or the other ISRs it
// preempts at priority 3.

#define TX_MAX 32

static __xdata uint8_t         tx_buf[TX_MAX];
static volatile __data uint8_t tx_idx;
static volatile __data uint8_t tx_last;
static volatile __bit          uart_irq_seen; // stock IDATA 0x30 = 0 on every UART interrupt

void rf_euart0_interrupt_handler(void) __interrupt(_INT_EUART0)
{
    if (RI) {
        RI = 0;
        if (rx_idx < RX_MAX) {
            rx_wr[rx_idx] = SBUF;
            rx_idx++;
        }
    } else if (TI) {
        TI = 0;
        if (tx_idx >= tx_last) {
            tx_busy     = 0;
            tx_end_tick = ms_lo;
            P0CR &= (uint8_t)~0x04; // release the send request: an input again (no pull-up on P0.2
                                    // - P0PCR as the stock sets it; the level is the module's)
            P0_2 = 1;
        } else {
            tx_idx++;
            SBUF = tx_buf[tx_idx];
        }
    }
    if (SCON & 0xE0) {
        SCON &= 0x1F;
    }
    uart_irq_seen = 1;
}

// 0xAED0, value for value, then EUART0 at priority 3 as 0xB108 sets it
// (IPH1 = 0x42 / IPL1 = 0x41 on the stock; only the ES0 bits here: smk leaves
// its other interrupts at level 0). Without it a byte arrives every 38 µs while
// the matrix scan runs for ~320 µs in the Timer2 ISR, and the scan eats bytes.
void rf_uart_init(void)
{
    IEN1 &= (uint8_t)~_ES0;
    SBRTH = 0;
    SBRTL = 0;
    SFINE = 0;
    SCON  = 0;
    SCON  = 0x50;
    SBRTH = 0xFF;
    SBRTL = 0xFB;
    SFINE = 0x0C;
    TI    = 0;
    RI    = 0;
    PCON |= _SSTAT;
    IPH1 |= _ES0;
    IPL1 |= _ES0;
    tx_busy     = 0;
    tx_end_tick = (uint8_t)(ms_lo - TX_GAP_TICKS);
    rx_idx      = 0;
    rx_bank     = 0;
    rx_ready_n  = 0;
    rx_ready_rd = 0;
    rx_wr       = rx_buf0;
    IEN1 |= _ES0;
}

// Before a wireless sleep (0x0FC9-0x0FCD): ES0 off, SCON &= 0xBF. With SSTAT
// set, bit 6 is the RXOV flag; REN stays as it is, as on the stock.
void rf_uart_off(void)
{
    IEN1 &= (uint8_t)~_ES0;
    SCON &= 0xBF;
}

// The send request: latch low, make it an output, latch low again (0xA84C).
static void p02_request(void)
{
    P0_2 = 0;
    P0CR |= 0x04;
    P0_2 = 0;
}

static __xdata uint16_t pump_at;

// After a wireless wake (0x109C): P0.2 low, released by the next parser pass,
// which on the stock runs in an interrupt 0-2 ms later while the main code
// waits in delay(20). Here the second 1 ms tick releases it (1-2 ms).
void rf_wake_pulse(void)
{
    p02_request();
    wake_pulse = 2;
    pump_at    = rf_ms(); // nor does the main loop's pass free it earlier
}

// A busy wait of `us` microseconds that the tests do not stub (they stub
// delay_us / delay_ms): 24 cycles per loop at 24 MHz on the 1T core, as
// delay.c counts them (19 NOPs, DJNZ taken 5 cycles). It saves what it uses:
// SDCC cannot see into the asm and keeps the caller's values in registers
// across the call.
static void rf_spin_us(uint8_t us) __naked
{
    (void)us;
    // clang-format off
    __asm
        push    acc
        push    ar7
        mov     a, dpl
        jz      00099$
        mov     r7, a
00001$:
        nop
        nop
        nop
        nop
        nop
        nop
        nop
        nop
        nop
        nop
        nop
        nop
        nop
        nop
        nop
        nop
        nop
        nop
        nop
        djnz    r7, 00001$
00099$:
        pop     ar7
        pop     acc
        ret
    __endasm;
    // clang-format on
}

// P0.2 high between two frames: at least TX_GAP_TICKS - 1 whole ticks (1 ms).
// The stock's gaps are at most a few hundred microseconds shorter in places;
// a frame right after another is where a missed request would cost a frame.
static bool gap_ok(void)
{
    return (uint8_t)(ms_lo - tx_end_tick) >= TX_GAP_TICKS;
}

// Blocking senders (names, control frames, the drain): wait for the gap, at
// most ~4 ms (the tick may not be running at boot). The main-loop waits here
// kick the watchdog as they go (delay_us does not; DELAY_NO_WATCHDOG_KICK);
// no interrupt ever does.
static void wait_gap(void)
{
    for (uint8_t n = 0; n < 40u && !gap_ok(); n++) {
        watchdog_kick();
        rf_spin_us(100);
    }
}

// P0.2 low to the first start bit, per frame type, as long as the stock's code
// takes there (in the simulator: long report 60 us, name 24-49, link-select 30,
// short report and container 17, control and ack 3-5).
static uint8_t lead_us(uint8_t cmd)
{
    switch (cmd) {
        case 0x02:
            return 60;
        case 0x01:
        case 0x09:
            return 30;
        case 0x03:
            return 22;
        default:
            return 10;
    }
}

// From the matrix scan (user_matrix_read_rows, in the Timer2 interrupt at
// every column, ~20 µs apart): the scan runs ~320 µs with the main loop
// stopped, so a gap between two frames that falls inside it was seen by
// nobody and the two frames ran together (build-6 review, B6R-6; the stock
// samples P4.7 from its 100 µs interrupt, 0x6C18). EUART0 (priority 3)
// nests over the scan: interrupts off around the framing, as everywhere.
void rf_rx_frame_isr(void)
{
    if (P4_7 && rx_idx) {
        __critical
        {
            RX_FRAME_DONE();
        }
    }
}

// Frames a buffer the module finished (P4.7 back high) before anything
// discards it; main-loop copy of the PWM4 tick's framing.
static void rx_take_done(void)
{
    if (!P4_7) {
        return;
    }
    __critical
    {
        if (P4_7 && rx_idx) {
            RX_FRAME_DONE();
        }
    }
}

// 0xA848: bytes 0..len-2 are in tx_buf; the last byte gets 0x55 minus the sum
// of all the others (the 01 header included). Starting a send also restarts
// the receive buffer, as the stock does (0xA872) - the gated senders only send
// while the module is idle, and a frame the module has just finished is
// handed to the parser first.
static void tx_start(uint8_t len, bool restart_rx)
{
    uint8_t sum = 0x55;
    for (uint8_t i = 0; i < (uint8_t)(len - 1); i++) {
        sum -= tx_buf[i];
    }
    tx_buf[len - 1] = sum;

    if (restart_rx) {
        rx_take_done();
    }
    tx_busy = 1;
    p02_request();
    if (restart_rx) {
        rx_idx = 0;
    }
    tx_last = (uint8_t)(len - 1);
    tx_idx  = 0;
    rf_spin_us(lead_us(tx_buf[1]));
    SBUF = tx_buf[0];
}

// The gate of the stock's control and report senders: EUART0 on, no send in
// flight, the module idle (P4.7 high), and the gap since the last frame.
static bool gate_open(void)
{
    return (IEN1 & _ES0) && !tx_busy && P4_7 && gap_ok();
}

static void frame_begin(uint8_t cmd)
{
    memset(tx_buf, 0, TX_MAX);
    tx_buf[0] = 0x01;
    tx_buf[1] = cmd;
}

// A 6-byte control frame 01 <cmd> <p2> <p3> 00 <sum>, gated.
static bool send_short(uint8_t cmd, uint8_t p2, uint8_t p3)
{
    if (!gate_open()) {
        return false;
    }
    frame_begin(cmd);
    tx_buf[2] = p2;
    tx_buf[3] = p3;
    tx_start(6, true);
    return true;
}

bool rf_send_ctrl(uint8_t cmd, uint8_t p2, uint8_t p3, uint16_t wait_ms)
{
    // Waits in 100 µs steps, so it ends even if the 1 ms tick were not running.
    for (uint16_t n = 0; !send_short(cmd, p2, p3); n++) {
        if (n >= (uint16_t)(wait_ms * 10u)) {
            return false;
        }
        watchdog_kick();
        rf_spin_us(100);
    }
    return true;
}

// Acks F0 / F1 (0xAA62): not gated by P4.7 on the stock. Here they only wait
// for a send in flight to finish, so they never overwrite it.
static __bit           ack_pending;
static __xdata uint8_t ack_cmd;

static void send_ack(uint8_t cmd)
{
    ack_cmd     = cmd;
    ack_pending = 1;
}

static void ack_pump(void)
{
    if (ack_pending && (IEN1 & _ES0) && !tx_busy && gap_ok()) {
        ack_pending = 0;
        frame_begin(ack_cmd);
        tx_start(6, false); // the stock's ack sender leaves the receive index alone
    }
}

// Names 0x09 (0xA02D): 01 09 <profile> 0F <15 characters> 00 x12 <sum>, 32
// bytes, sent at boot without gating, each followed by delay(50) ≈ 32 ms.
// Profile 0 is the BT5.0 name, profile 1 the BT3.0 name.
static const __code char name_bt50[15] = {'A', 'U', 'L', 'A', ' ', 'F', '6', '5', ' ', 'B', 'T', '5', '.', '0', ' '};
static const __code char name_bt30[15] = {'A', 'U', 'L', 'A', ' ', 'F', '6', '5', ' ', 'B', 'T', '3', '.', '0', ' '};

// Waits for a send in flight to finish (32 bytes take ~1.2 ms), bounded by a
// loop count so it cannot hang.
void rf_wait_tx_idle(void)
{
    for (uint16_t n = 0; tx_busy && n < 60000u; n++) {
        watchdog_kick();
    }
}

static void send_name(uint8_t profile, const __code char *name)
{
    rf_wait_tx_idle();
    wait_gap();
    frame_begin(0x09);
    tx_buf[2] = profile;
    tx_buf[3] = 15;
    for (uint8_t i = 0; i < 15; i++) {
        tx_buf[4 + i] = (uint8_t)name[i];
    }
    tx_start(32, true);
    delay_ms(32);
}

void rf_send_names(void)
{
    send_name(0, name_bt50);
    send_name(1, name_bt30);
    rf_wait_tx_idle();
}

// ------------------------------------------------------------ link state

static __xdata uint8_t mod_slot;  // status [4] (0x09BA)
static __xdata uint8_t mod_state; // status [5] (0x09AE)
static __bit           synced;    // 0x2d.1: a matching status frame since the last select
static __bit           status_seen;
static __bit           conn_latch;    // 0x0E3C: "connected" latched from status 3
static __bit           conn_event;    // the latch went on (the indicators' 3 s solid, stock 0x26.1)
static __xdata uint8_t idle_factor;   // 0x0C35
static __xdata uint8_t status_frames; // IDATA 0x18: battery pass every 6th
static __xdata uint8_t cur_transport; // what rf_task was last called with
static __xdata uint8_t cur_slot;

// Link-select waiting to go out (the stock drops it when the gate is shut and
// relies on the next status frame; here it waits, and a pairing request is
// never replaced by a plain select - review defect 3).
static __bit           link_pending;
static __xdata uint8_t link_flag;
static __xdata uint8_t link_slot;

// Hold-off after a link-select: the supervisor does not re-select on the next
// status frames for this long. The stock has none (it re-selects on every
// mismatching status frame); this keeps a just-sent pairing request from being
// followed at once by a plain select while the module has not switched state
// yet (review defect 4).
#define RESELECT_HOLDOFF_MS 600
static __xdata uint16_t select_sent_at;
static __bit            select_holdoff;

void rf_link_cancel(void)
{
    link_pending   = 0;
    select_holdoff = 0;
}

void rf_link_select(uint8_t flag, uint8_t slot)
{
    if (link_pending && link_flag && !flag) {
        return;
    }
    link_pending = 1;
    link_flag    = flag;
    link_slot    = slot;
}

static __xdata uint8_t batt_shown; // IDATA 0x17, 0xFF = not set yet
static __bit           batt_dirty; // 0x23.7: 0x0D due

void rf_link_resync(void)
{
    synced        = 0;
    status_frames = 6; // the next status frame runs the battery pass
    batt_shown    = 0xFF;
}

static void link_pump(void)
{
    if (link_pending && gate_open()) {
        send_short(0x01, link_flag, link_slot);
        link_pending   = 0;
        select_sent_at = rf_ms();
        select_holdoff = 1;
    }
}

bool rf_connected_latched(void)
{
    return conn_latch;
}

uint8_t rf_module_state(void)
{
    return mod_state;
}

uint8_t rf_module_slot(void)
{
    return mod_slot;
}

bool rf_synced(void)
{
    return synced;
}

bool rf_take_connect_event(void)
{
    const bool e = conn_event;
    conn_event   = 0;
    return e;
}

bool rf_status_seen(void)
{
    return status_seen;
}

uint8_t rf_idle_factor(void)
{
    return idle_factor;
}

void rf_forget_connection(void)
{
    conn_latch = 0;
}

// ------------------------------------------------------------------ battery
//
// 0x7D42, run on every 6th status frame. The module reports the raw value in
// status [6:7]; the stock's conversion is (raw - 715) * 10 / 22, capped at 100
// (the stock caps the low byte of the quotient only, so raw 1279 and above
// wraps; this cap does not).

#define BATT_EMPTY    715
#define BATT_LOW_CUT  737 // cutoff below this for 2 s
#define BATT_LOW_WARN 781 // warning (red Fn blink) below this for 24 samples
#define BATT_RECOVER  912 // cutoff and warning cleared above this for 1 s

static const __code uint8_t batt_steps[12] = {20, 10, 5, 2, 2, 2, 2, 2, 2, 2, 2, 2}; // 0xAD3D

static __xdata uint16_t batt_raw = 0x03A6; // the stock's boot value
static __xdata uint8_t  rise_count;        // X[0x08C7]
static __xdata uint8_t  fall_count;        // X[0x08DE]
static __bit            ext_power;         // 0x26.0: USB power, 3 samples (0x9E26)
static __bit            charging;          // 0x2D.3: P7.7 low 20 samples, high 200 clears
static __xdata uint8_t  pwr_on_count, pwr_off_count, chg_low_count, chg_high_count;
static __bit            batt_cut; // 0x2D.4
static __xdata uint8_t  cut_count;
static __bit            batt_low;  // 0x2B.3
static __xdata uint8_t  low_count; // X[0x0300]
static __xdata uint8_t  recover_count;
static __bit            sleep_req;

static uint8_t batt_target(void)
{
    if (batt_raw < BATT_EMPTY) {
        return 0;
    }
    uint16_t pct = (uint16_t)((uint32_t)(batt_raw - BATT_EMPTY) * 10u / 22u);
    return (pct >= 100) ? 100 : (uint8_t)pct;
}

// 0x7D42-0x7E73, step for step. The first value after a re-arm (0xFF) is the
// target itself. With USB power the shown value only rises (one step per
// 20/10/5/2 passes by the distance in tens, a jump when 20 or more below),
// drops at once when 20 or more above, reads 99 instead of 100 while
// charging, and 95-99 snap to 100 once charging has stopped. On battery it
// only falls (the same steps, a jump when 30 or more above) and jumps up when
// the target is 30 or more higher.
static void battery_pass(void)
{
    const uint8_t tgt = batt_target();

    if (batt_shown == 0xFF) {
        batt_shown = tgt;
        batt_dirty = 1;
    }
    if (ext_power) {
        fall_count = 0;
        if (tgt > batt_shown) {
            const uint8_t diff = (uint8_t)(tgt - batt_shown);
            if (++rise_count >= batt_steps[diff / 10]) {
                rise_count = 0;
                batt_shown++;
                if (diff >= 20) {
                    batt_shown = tgt;
                }
                if (batt_shown == 100) {
                    batt_shown = charging ? 99 : 100;
                }
                batt_dirty = 1;
            }
        } else {
            rise_count = 0;
            if ((uint8_t)(batt_shown - tgt) >= 20) {
                batt_shown = tgt;
                batt_dirty = 1;
            }
        }
        // 0x7DF9: charged (not charging any more) and 95-99 shown: 100.
        if (!charging && batt_shown >= 95 && batt_shown <= 99) {
            batt_shown = 100;
            batt_dirty = 1;
        }
    } else {
        rise_count = 0;
        if (tgt >= batt_shown) {
            fall_count = 0;
            if ((uint8_t)(tgt - batt_shown) >= 30) { // 0x7E5D
                batt_shown = tgt;
                batt_dirty = 1;
            }
        } else {
            const uint8_t diff = (uint8_t)(batt_shown - tgt);
            if (++fall_count >= batt_steps[diff / 10]) {
                fall_count = 0;
                if (batt_shown) {
                    batt_shown--;
                }
                if (diff >= 30) {
                    batt_shown = tgt;
                }
                batt_dirty = 1;
            }
        }
    }
}

uint8_t rf_battery_percent(void)
{
    return (batt_shown == 0xFF) ? batt_target() : batt_shown;
}

bool rf_battery_cutoff(void)
{
    return batt_cut;
}

bool rf_external_power(void)
{
    return ext_power;
}

bool rf_charging(void)
{
    return charging;
}

bool rf_battery_low(void)
{
    return batt_low;
}

uint16_t rf_battery_raw(void)
{
    return batt_raw;
}

// Every 10 ms. 0x9E26: USB power (P4.4) counts after 3 samples; when it goes
// away the display is re-armed (I[0x17] = 0xFF, I[0x18] = 0); P7.7 low for 20
// samples = charging, high for 200 = not. 0x3108: with USB power the cutoff
// is off; in a wireless position raw < 737 for 2 s → cutoff (no key reports,
// sleep, again every 2 s), raw > 912 for 1 s clears it.
void rf_power_tick(bool usb_power, bool chg_pin_low, bool wireless)
{
    if (usb_power) {
        if (pwr_on_count != 0xFF) {
            pwr_on_count++;
        }
        pwr_off_count = 0;
    } else {
        pwr_on_count = 0;
        if (pwr_off_count != 0xFF) {
            pwr_off_count++;
        }
    }
    if (pwr_on_count >= 3 && !ext_power) {
        ext_power = 1;
    }
    if (pwr_off_count >= 3 && ext_power) {
        ext_power     = 0;
        status_frames = 0;
        batt_shown    = 0xFF;
    }
    if (chg_pin_low) {
        if (chg_low_count != 0xFF) {
            chg_low_count++;
        }
        chg_high_count = 0;
    } else {
        chg_low_count = 0;
        if (chg_high_count != 0xFF) {
            chg_high_count++;
        }
    }
    if (chg_low_count >= 20) {
        charging = 1;
    }
    if (chg_high_count >= 200) {
        charging = 0;
    }

    if (ext_power) {
        batt_cut  = 0;
        batt_low  = 0; // 0x3138
        low_count = 0;
        return;
    }
    if (!wireless) {
        return;
    }
    if (batt_raw < BATT_LOW_CUT || batt_cut) {
        if (++cut_count >= 200) {
            cut_count = 0;
            batt_cut  = 1;
            sleep_req = 1; // repeats every 2 s while the cutoff holds
        }
    } else {
        cut_count = 0;
    }
    if (batt_raw > BATT_RECOVER) {
        if (++recover_count >= 100) {
            recover_count = 0;
            batt_cut      = 0;
            batt_low      = 0; // 0x3193-0x3198
        }
    } else {
        recover_count = 0;
    }
    // 0x31A1-0x31D1: not during the cutoff; raw < 781 for 24 samples in a row
    // sets the warning, which stays until the recovery above or USB power.
    if (batt_cut) {
        return;
    }
    if (batt_raw < BATT_LOW_WARN) {
        if (low_count != 0xFF) {
            low_count++;
        }
        if (low_count >= 24) {
            batt_low = 1;
        }
    } else {
        low_count = 0;
    }
}

bool rf_sleep_requested(void)
{
    return sleep_req;
}

void rf_sleep_request_clear(void)
{
    sleep_req = 0;
}

// ------------------------------------------------------------------ receive

#define RX_STATUS_LEN 10
#define RX_ACK_LEN    6
#define RX_BULK_LEN   22

static uint8_t sum_of(const __xdata uint8_t *p, uint8_t n)
{
    uint8_t s = 0x55;
    for (uint8_t i = 0; i < n; i++) {
        s -= p[i];
    }
    return s;
}

// 0x35C5, the status LED state machine: it keeps the "connected" latch and
// the idle-time factor the sleep timeout uses; it runs once synced.
static void status_states(uint8_t state, uint8_t slot)
{
    const bool was = conn_latch;
    if (state == RF_STATE_PAIRING) {
        idle_factor = 6;
        conn_latch  = 0;
    } else if (state == RF_STATE_RECONNECTING) {
        idle_factor = slot ? 2 : 1;
        conn_latch  = 0;
    } else if (state == RF_STATE_CONNECTED) {
        conn_latch = 1;
    }
    if (was != conn_latch) {
        rf_note_activity(); // status transitions reset the idle counter (0x3653, 0x3666)
        if (conn_latch) {
            conn_event = 1; // 0x3666-0x3672: connected now, the link key solid for 3 s
        }
    }
}

static void on_status(const __xdata uint8_t *f)
{
    // 02 06 00 <host LEDs> <slot> <state> <batt lo> <batt hi> <x> <sum>
    mod_slot    = f[4];
    mod_state   = f[5];
    batt_raw    = (uint16_t)f[6] | ((uint16_t)f[7] << 8);
    status_seen = 1;

    if (cur_transport == RF_USB) {
        return;
    }
    keyboard_set_led_state(f[3]); // the same byte the USB LED report sets (0x0F42)

    const uint8_t want = (cur_transport == RF_BT) ? cur_slot : 0;
    if (mod_slot != want || mod_state == RF_STATE_IDLE) {
        if (select_holdoff && (uint16_t)(rf_ms() - select_sent_at) < RESELECT_HOLDOFF_MS) {
            // wait: the module may not have switched yet
        } else {
            select_holdoff = 0;
            rf_link_select(0, want);
        }
    } else {
        if (!synced) {
            conn_latch = 0;
            rf_note_activity();
        }
        synced = 1;
    }
    if (synced) {
        status_states(mod_state, mod_slot);
    }
    if (++status_frames >= 6) {
        status_frames = 0;
        battery_pass();
    }
}

static __xdata uint16_t probe_at; // the status request's 200 ms (0x0153)

// One received frame: the bytes of one P4.7 envelope. As the stock parser
// (0x0608), only the first byte says what it is and only that frame is
// looked at; an envelope that does not start with a known frame is dropped
// whole.
static void parse(const __xdata uint8_t *f, uint8_t len)
{
    if (f[0] == 0x02) {
        // status 02 06 00 ...; the module's acks of our frames (02 cmd ...)
        // need nothing
        if (len >= RX_STATUS_LEN && f[1] == 0x06 && f[2] == 0x00 && sum_of(f, 9) == f[9]) {
            on_status(f);
        }
    } else if (f[0] == 0x03) {
        // announce: no checksum on the stock (0x079E); restarts the status
        // request period (0x07A1), acked, battery display re-armed
        probe_at      = rf_ms();
        status_frames = 6;
        batt_shown    = 0xFF;
        send_ack(0xF0);
    } else if (f[0] == 0x08 && len >= RX_BULK_LEN && sum_of(f, 21) == f[21]) {
        // Driver-protocol container. Acked as the stock (F1, except
        // sub-command 8, per-key RGB streaming), with the idle counter
        // cleared and a pending sleep cancelled. Nothing is acted on: the
        // configuration writes are not implemented, and 06 (factory reset)
        // is never.
        if ((f[2] & 0x7F) != 0x08) {
            send_ack(0xF1);
            rf_note_activity();
            sleep_req = 0;
        }
    }
}

// ------------------------------------------------------------------ reports
//
// Queue of 6 slots of 28 bytes (stock XDATA 0x0C59): byte 0 is the command
// (02 long, 03 short), bytes 1..27 the payload.
//
// Long frame (30 bytes): 01 02 mods k1 k2 k3 k4 k5 00 bm[16] 00 00 00 00 sum.
// Five key slots; a sixth key goes into bm, a 128-bit bitmap with bit n =
// usage n; no report ID. smk's 6KRO report is placed into it as the stock
// places its keys (place_keys).
// Short frame (13 bytes): 01 03 cons_lo cons_hi sys 00 00 00 00 00 00 00 sum;
// sys bit 0 power down, bit 1 sleep, bit 2 wake.

#define Q_SLOTS   6
#define Q_SIZE    28
#define LONG_LEN  30
#define SHORT_LEN 13

static __xdata uint8_t q[Q_SLOTS][Q_SIZE];
static __xdata uint8_t q_head, q_tail, q_count;

static __xdata report_keyboard_t kbd_now; // the keyboard report as smk built it
// The long frame's keys as the stock keeps them: held keys in press order in
// the five slots (a release closes the gap), keys beyond five in the bitmap,
// where they stay until released.
static __xdata uint8_t  slot_key[5];
static __xdata uint8_t  over_key[KEYBOARD_REPORT_KEYS];
static __xdata uint16_t cons_now; // consumer usage
static __xdata uint8_t  sys_now;  // system bits
static __bit            long_dirty;
static __bit            short_dirty;

// Release repeat (0x302E-0x3083, armed at 0x91AA on a wireless key press):
// after the last release, three times an ErrorRollOver frame then an empty one.
static __bit           rep_armed;
static __bit           rep_ero_out;
static __xdata uint8_t rep_count;

static __xdata uint8_t pump_skip;   // IDATA 0x31
static __data uint8_t  report_tick; // ms_lo when the last report went out

static bool key_held(uint8_t k)
{
    for (uint8_t i = 0; i < KEYBOARD_REPORT_KEYS; i++) {
        if (kbd_now.keys[i] == k) {
            return true;
        }
    }
    return false;
}

static bool key_placed(uint8_t k)
{
    for (uint8_t i = 0; i < 5; i++) {
        if (slot_key[i] == k) {
            return true;
        }
    }
    for (uint8_t i = 0; i < KEYBOARD_REPORT_KEYS; i++) {
        if (over_key[i] == k) {
            return true;
        }
    }
    return false;
}

// kbd_now changed: drop the released keys (closing the gaps), then place the
// new ones after the held ones.
static void place_keys(void)
{
    uint8_t n = 0;
    for (uint8_t i = 0; i < 5; i++) {
        const uint8_t k = slot_key[i];
        slot_key[i]     = 0;
        if (k && key_held(k)) {
            slot_key[n++] = k;
        }
    }
    for (uint8_t i = 0; i < KEYBOARD_REPORT_KEYS; i++) {
        if (over_key[i] && !key_held(over_key[i])) {
            over_key[i] = 0;
        }
    }
    for (uint8_t i = 0; i < KEYBOARD_REPORT_KEYS; i++) {
        const uint8_t k = kbd_now.keys[i];
        if (!k || key_placed(k)) {
            continue;
        }
        if (n < 5) {
            slot_key[n++] = k;
        } else {
            for (uint8_t j = 0; j < KEYBOARD_REPORT_KEYS; j++) {
                if (!over_key[j]) {
                    over_key[j] = k;
                    break;
                }
            }
        }
    }
}

static bool q_put(uint8_t cmd)
{
    if (q_count >= Q_SLOTS) {
        return false; // full: keep the dirty flag and try again (the stock keeps its flags)
    }
    __xdata uint8_t *s = q[q_head];
    memset(s, 0, Q_SIZE);
    s[0] = cmd;
    if (cmd == 0x02) {
        place_keys();
        s[1] = kbd_now.mods;
        for (uint8_t i = 0; i < 5; i++) {
            s[2 + i] = slot_key[i];
        }
        for (uint8_t i = 0; i < KEYBOARD_REPORT_KEYS; i++) {
            const uint8_t k = over_key[i];
            if (k && k < 0x80) {
                s[8 + (k >> 3)] |= (uint8_t)(1u << (k & 7));
            }
        }
    } else {
        s[1] = (uint8_t)(cons_now & 0xFF);
        s[2] = (uint8_t)(cons_now >> 8);
        s[3] = sys_now;
    }
    q_head = (uint8_t)((q_head + 1u) % Q_SLOTS);
    q_count++;
    return true;
}

static void produce(void)
{
    if (long_dirty) {
        if (q_put(0x02)) {
            long_dirty = 0;
        }
    } else if (short_dirty) {
        if (q_put(0x03)) {
            short_dirty = 0;
        }
    }
}

static bool keys_all_up(void)
{
    if (kbd_now.mods) {
        return false;
    }
    for (uint8_t i = 0; i < KEYBOARD_REPORT_KEYS; i++) {
        if (kbd_now.keys[i]) {
            return false;
        }
    }
    return true;
}

static void release_repeat(void)
{
    if (!rep_armed || long_dirty) {
        return;
    }
    if (!rep_ero_out) {
        if (!keys_all_up()) {
            return;
        }
        kbd_now.keys[0] = 0x01; // ErrorRollOver in key 1 (frame byte 3)
        rep_ero_out     = 1;
    } else {
        memset(&kbd_now, 0, sizeof(kbd_now));
        rep_ero_out = 0;
        if (++rep_count >= 3) {
            rep_armed = 0;
        }
    }
    long_dirty = 1;
}

// 0x2EC4: pace, gate, send one slot. 2.4G: every pump (2 ms); Bluetooth: one
// report per 4 pumps (8 ms, 125 Hz).
static void consume(void)
{
    if (pump_skip) {
        pump_skip--;
        if (pump_skip) {
            return;
        }
    }
    if (!gate_open()) {
        return;
    }
    pump_skip = 1;
    if (!q_count) {
        release_repeat(); // the next repeat frame goes out in this pump, not the next
        produce();
    }
    if (q_count) {
        __xdata uint8_t *s   = q[q_tail];
        const uint8_t    len = (s[0] == 0x02) ? LONG_LEN : SHORT_LEN;
        tx_buf[0]            = 0x01;
        for (uint8_t i = 0; i < (uint8_t)(len - 2); i++) {
            tx_buf[1 + i] = s[i];
        }
        tx_start(len, true);
        report_tick = ms_lo;
        q_tail      = (uint8_t)((q_tail + 1u) % Q_SLOTS);
        q_count--;
        if (cur_transport == RF_BT) {
            pump_skip = 4;
        }
    }
}

static void pump(void)
{
    produce();
    consume();
}

void rf_queue_keyboard(__xdata report_keyboard_t *report)
{
    bool pressed = (report->mods & (uint8_t)~kbd_now.mods) != 0;
    for (uint8_t i = 0; i < KEYBOARD_REPORT_KEYS && !pressed; i++) {
        if (report->keys[i]) {
            pressed = true;
            for (uint8_t j = 0; j < KEYBOARD_REPORT_KEYS; j++) {
                if (kbd_now.keys[j] == report->keys[i]) {
                    pressed = false;
                    break;
                }
            }
        }
    }
    memcpy(&kbd_now, report, sizeof(kbd_now));
    if (pressed) {
        rep_armed   = 1;
        rep_ero_out = 0;
        rep_count   = 0;
    }
    long_dirty = 1;
    pump(); // the stock pumps once per key event too
}

void rf_queue_extra(__xdata report_extra_t *report)
{
    if (report->report_id == REPORT_ID_CONSUMER) {
        cons_now = report->usage;
    } else if (report->report_id == REPORT_ID_SYSTEM) {
        const uint16_t u = report->usage;
        sys_now          = (u >= 0x81 && u <= 0x83) ? (uint8_t)(1u << (u - 0x81)) : 0;
    } else {
        return;
    }
    short_dirty = 1;
    pump();
}

void rf_report_holdoff(uint8_t pumps)
{
    pump_skip = pumps;
}

// The stock pump keeps running in its Timer2 interrupt through the delay(10)
// that follows a flush (0xEC6F), so the released frames go out before what
// the caller sends next; here the main loop is the pump, so it drains here,
// with the gap between frames and the report pacing (Bluetooth one report per
// 8 ms, 2.4 GHz per 2 ms; one tick more, as the ticks count whole ms), for at
// most ~100 ms.
void rf_drain_reports(void)
{
    const uint8_t pace = (cur_transport == RF_BT) ? 9 : 3;
    for (uint16_t n = 0; n < 1000u && (q_count || long_dirty || short_dirty); n++) {
        produce();
        if (q_count && gate_open() && (uint8_t)(ms_lo - report_tick) >= pace) {
            pump_skip = 0;
            consume();
        }
        watchdog_kick(); // up to ~100 ms here (the module holding P4.7 low)
        rf_spin_us(100);
    }
}

// 0xA464: empty the queue and queue an all-released long and short frame.
void rf_flush_reports(void)
{
    q_head = q_tail = q_count = 0;
    memset(&kbd_now, 0, sizeof(kbd_now));
    memset(slot_key, 0, sizeof(slot_key));
    memset(over_key, 0, sizeof(over_key));
    cons_now    = 0;
    sys_now     = 0;
    rep_armed   = 0;
    rep_ero_out = 0;
    long_dirty  = 1;
    short_dirty = 1;
}

// ------------------------------------------------------------------ task

#define PROBE_MS 200
#define PUMP_MS  2

static __xdata uint8_t stall_probes;

// 0x3FDF: the status request every 100 frame-less parser passes (≈ 200 ms);
// the stuck-send watchdog frees the line after 3 requests without any UART
// interrupt (0x4004).
static void probe(void)
{
    if ((uint16_t)(rf_ms() - probe_at) < PROBE_MS) {
        return;
    }
    probe_at = rf_ms();
    send_short(0x06, 0, 0);
    if (uart_irq_seen) {
        uart_irq_seen = 0;
        stall_probes  = 0;
    } else if (++stall_probes >= 3) {
        stall_probes = 0;
        __critical
        {
            tx_busy     = 0;
            tx_end_tick = ms_lo;
            P0CR &= (uint8_t)~0x04;
            P0_2 = 1;
        }
    }
}

void rf_rx_poll(void)
{
    rx_take_done();
}

void rf_task(uint8_t transport)
{
    cur_transport = transport;

    rx_take_done(); // the main loop looks at P4.7 far more often than the tick
    while (rx_ready_n) {
        // The oldest waiting frame; its bank is freed only after the parse
        // (the receiver fills the bank beyond the waiting ones).
        const uint8_t          b = rx_ready_rd;
        const uint8_t          n = rx_len[b];
        const __xdata uint8_t *f = rx_bank_ptr[b];
        if (transport != RF_USB) {
            parse(f, n);
        }
        __critical
        {
            rx_ready_n--;
        }
        rx_ready_rd = (uint8_t)(b + 1 == RX_BANKS ? 0 : b + 1);
    }
    if (transport == RF_USB) {
        return;
    }

    ack_pump();
    link_pump();
    // 0x7E75: 0x0D with the percent whenever the display changed, including
    // the re-armed display after a link-select or an announce.
    if (batt_dirty && gate_open() && batt_shown != 0xFF) {
        batt_dirty = 0;
        send_short(0x0D, batt_shown, 0);
    }
    if ((uint16_t)(rf_ms() - pump_at) >= PUMP_MS) {
        pump_at = rf_ms();
        pump();
        probe();
        // End of a parser pass (every 2 ms on the stock too): free the send
        // request line when nothing is going out (0x0F88); this also ends the
        // wake pulse.
        if (!tx_busy) {
            P0CR &= (uint8_t)~0x04;
            P0_2 = 1;
        }
    }
}

void rf_set_slot(uint8_t slot)
{
    cur_slot = slot;
}

void rf_set_transport(uint8_t transport)
{
    cur_transport = transport;
}
