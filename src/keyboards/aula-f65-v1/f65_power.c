// AULA F65 V1: transport selection (the three-position switch), USB on/off,
// and the sleep paths, following the V1 stock firmware (stock addresses in
// the comments; the protocol notes are in the author's analysis notes, not
// published).

#include "f65_power.h"
#include "f65_rf.h"
#include "kbdef.h"
#include "sh68f90.h"
#include "usb.h"
#include "usbdef.h"
#include "settings.h"
#include "user_init.h"
#include "delay.h"
#include "tick.h"
#include "watchdog.h"
#include "report.h"
#include "power.h"
#include "led.h"

extern usb_device_state_t usb_device_state; // src/smk/usb.c

// The stock's delay(n) is about 0.64 ms per unit (0xB019).
#define STOCK_DELAY_10  6
#define STOCK_DELAY_20  13
#define STOCK_DELAY_200 128

// Sleep timeout while connected: (30 * S + 1) s with S the stock setting at
// flash 0xC618; the factory value is 2 (61 s).
#define SLEEP_SETTING_S 2
// Wired position: the stock's check at 0x8BC1 - more than 10 000 ms without a
// USB interrupt or a key (the USB interrupt clears the counter, 0x954B).
#define WIRED_QUIET_MS 10001u
// USB detached this long before it is enabled again (the host sees a replug).
#define REATTACH_MS 20

static __xdata uint8_t transport; // 0x031A
static __xdata uint8_t bt_slot;   // 0x0318, 1..3
static __xdata uint8_t deb_usb, deb_24g, deb_bt;

uint8_t f65_transport(void)
{
    return transport;
}

uint8_t f65_bt_slot(void)
{
    return bt_slot;
}

// The saved transport and slot live in user_settings.rf_link (bits 5:4 the
// transport, bits 1:0 the slot), so the settings record keeps its length.
void f65_load_link(uint8_t saved)
{
    transport = (uint8_t)((saved >> 4) & 0x03);
    if (transport > RF_BT) {
        transport = RF_USB;
    }
    bt_slot = saved & 0x03;
    if (bt_slot == 0) {
        bt_slot = 1;
    }
    rf_set_slot(bt_slot);
}

static void save_link(void)
{
    user_settings.rf_link = (uint8_t)((transport << 4) | bt_slot);
    settings_mark_dirty();
}

// Wireless positions. The stock (0xEC69) clears ENUSB only and leaves the D+
// pull-up (SW1CON) and the USB interrupt on; here the pull-up goes too, so the
// host sees the keyboard unplugged at once, and the USB interrupt is off, so
// nothing on the bus can bring USB back (usb_init is gated as well, as the
// stock's 0xAF16 returns in a wireless transport).
static void usb_off(void)
{
    IEN1 &= (uint8_t)~_EUSB;
    USBCON &= (uint8_t)~(_ENUSB | _SW1CON);
    usb_device_state = USB_DEVICE_STATE_DEFAULT;
}

// USB from scratch: if the pull-up is still on (it never is after usb_off),
// take it off long enough for the host to see a detach, then usb_init.
static void usb_reattach(void)
{
    if (USBCON & (_ENUSB | _SW1CON)) {
        USBCON &= (uint8_t)~(_ENUSB | _SW1CON);
        delay_ms(REATTACH_MS);
    }
    usb_init();
}

// 0xEC6F: release everything on the radio side, delay(10).
static void flush_radio(void)
{
    rf_flush_reports();
    rf_drain_reports();
    delay_ms(STOCK_DELAY_10);
}

// 0xEC8F: link-select, then forget the synced state (0x2d.1) and re-arm the
// battery display.
static void select_and_resync(uint8_t flag, uint8_t slot)
{
    rf_link_select(flag, slot);
    rf_link_resync();
}

// 0x8125: into the wired position.
static void enter_usb(void)
{
    rf_link_cancel();
    f65_pair_cancel();
    rf_forget_connection();
    flush_radio();
    rf_send_ctrl(0x0E, 0, 0, 20);
    delay_ms(STOCK_DELAY_10);
    rf_send_ctrl(0x0B, 0, 0, 20);
    delay_ms(STOCK_DELAY_10);
    rf_wait_tx_idle();
    transport = RF_USB;
    rf_set_transport(RF_USB);
    IEN1 &= (uint8_t)~_ES0;
    usb_reattach();
    delay_ms(STOCK_DELAY_200);
    save_link();
    rf_note_activity();
    rf_sleep_request_clear();
}

// 0x8161 / 0x81A2: into 2.4 GHz or Bluetooth.
static void enter_wireless(uint8_t t)
{
    if (transport == RF_USB) {
        rf_flush_reports();
        delay_ms(STOCK_DELAY_20);
    }
    if (transport == t) {
        return;
    }
    transport = t;
    if (t == RF_BT && (bt_slot < 1 || bt_slot > 3)) {
        bt_slot = 1;
    }
    rf_set_slot(bt_slot);
    rf_set_transport(t);
    rf_link_cancel(); // a request for the previous transport is void
    f65_pair_cancel();
    IEN1 |= _ES0;
    usb_off();
    flush_radio();
    select_and_resync(0, (t == RF_BT) ? bt_slot : 0);
    save_link();
    rf_note_activity();
    rf_sleep_request_clear();
}

// 0x80E4, every ~10 ms: P0.5 low = 2.4 GHz, P0.4 low = Bluetooth, both high =
// USB; 10 samples in a row; wired has priority, then 2.4 GHz, then Bluetooth.
void f65_switch_tick(void)
{
    if (!CONN_SW_24G) {
        deb_24g++;
        deb_bt  = 0;
        deb_usb = 0;
    } else if (!CONN_SW_BT) {
        deb_bt++;
        deb_24g = 0;
        deb_usb = 0;
    } else {
        deb_usb++;
        deb_24g = 0;
        deb_bt  = 0;
    }
    if (deb_usb >= 10) {
        deb_usb = 0;
        if (transport != RF_USB) {
            enter_usb();
        }
    } else if (deb_24g >= 10) {
        deb_24g = 0;
        enter_wireless(RF_24G);
    } else if (deb_bt >= 10) {
        deb_bt = 0;
        enter_wireless(RF_BT);
    }
}

// ------------------------------------------------------------------ keys
//
// Fn+Q/W/E (Bluetooth slots) and Fn+R (2.4 GHz); the stock has them on
// Fn+E/R/T and Fn+Q. Behaviour as the stock handler 0x4012 / release
// 0x42E4 / hold 0x8920: a short press selects (Bluetooth: on release), a
// 3 s hold pairs; the key of the other wireless mode, and all four in the
// wired position, do nothing.

#define PAIR_HOLD_MS 3000

static __bit            pair_armed; // 0x2c.5
static __xdata uint16_t pair_since;

void f65_pair_cancel(void)
{
    pair_armed = 0;
}

void f65_link_key(uint8_t slot, bool pressed)
{
    if (slot == 0) { // Fn+R, 2.4 GHz
        if (pressed) {
            if (transport == RF_24G) {
                pair_since = rf_ms();
                pair_armed = 1;
            }
        } else {
            pair_armed = 0;
        }
        return;
    }
    if (pressed) {
        if (transport != RF_BT) {
            return;
        }
        if (slot != bt_slot) {
            bt_slot = slot;
            rf_set_slot(slot);
            IEN1 |= _ES0;
            usb_off();
            rf_flush_reports();
            save_link();
        }
        pair_since = rf_ms();
        pair_armed = 1;
    } else {
        if (transport == RF_BT && pair_armed) {
            select_and_resync(0, bt_slot);
        }
        pair_armed = 0;
    }
}

void f65_pair_tick(void)
{
    if (transport == RF_USB) {
        pair_armed = 0; // a hold carried into the wired position is over
        return;
    }
    if (!pair_armed || (uint16_t)(rf_ms() - pair_since) < PAIR_HOLD_MS) {
        return;
    }
    pair_armed = 0;
    rf_forget_connection();
    flush_radio();
    if (transport == RF_BT) {
        select_and_resync(1, bt_slot);
    } else if (transport == RF_24G) {
        select_and_resync(1, 0);
    }
}

// ------------------------------------------------------------------ sleep

// 0x006E and what follows it in 0x0F91: rows inputs with pull-up; the key
// columns, the LED-only columns P4.6 / P7.4 and P0.5-P0.7 driven low; P0.0 /
// P0.1 and P0.4-P0.7 low without pull-up (both switch pins); P7.7 low;
// P4.1 = 1, P7.6 = 0. P0.2 / P0.3 and the UART pins are left alone.
static void park_pins(bool wireless)
{
    P7CR &= (uint8_t)~0x0F;
    P7PCR |= 0x0F;
    P5CR &= (uint8_t)~0x18;
    P5PCR |= 0x18;

    P6 = 0x00;
    P5 &= (uint8_t)~0x87;
    P4 &= (uint8_t)~0x6D;
    P7 &= (uint8_t)~0x10;
    P0 &= (uint8_t)~0xE0;
    P6CR = 0xFF;
    P5CR |= 0x87;
    P4CR |= 0x6D;
    P7CR |= 0x10;
    P0CR |= 0xE0;

    if (wireless) {
        P0PCR &= 0xFC;
        P0CR |= 0x03;
        P0 &= 0xFC;
        P0PCR &= 0x0F;
        P0CR |= 0xF0;
        P0 &= 0x0F;
        P7PCR &= 0x7F;
        P7CR |= 0x80;
        P7 &= 0x7F;
    }
    P4_1 = 1;
    if (wireless) {
        P7_6 = 0;
    }
}

static void wake_clock(void)
{
    // 0xA8CD: watchdog, interrupts off, wake sources off, oscillator and PLL on.
    watchdog_kick();
    EA   = 0;
    EXF0 = 0;
    EXF1 = 0;
    IENC = 0;
    IEN0 &= (uint8_t)~_EX4;
    CLKCON |= _HFON;
    PLLCON |= _PLLON;
    delay_us(40);
    watchdog_kick();
    PLLCON = _PLLON | _PLLFS;
    CLKCON = _HFON | _FS;
    USBIF1 &= (uint8_t)~0x02;
    USBCON &= (uint8_t)~_GOSUSP;
}

static void power_down(void)
{
    // clang-format off
    __asm
        nop
    __endasm;
    // clang-format on
    SUSLO = 0x55;
    PCON |= _PD;
    // clang-format off
    __asm
        nop
        nop
        nop
        nop
        nop
        nop
    __endasm;
    // clang-format on
}

// 0x0F91 → power-down → 0x1051. USB stays off throughout (review defect 1):
// the stock turns the USB interrupt and the D+ pull-up back on after the
// wake (0x107B, 0x1081) with ENUSB off; here both stay off, as in usb_off.
void f65_sleep_wireless(void)
{
    // Every LED off first (the stock's 0xAD49 before its 0x006E parking):
    // PWM stopped, the anode latches 0, every column released - before the
    // parking drives the columns low and before the clock stops.
    led_hold(LED_HOLD_SLEEP);
    flush_radio();
    delay_ms(STOCK_DELAY_200);
    delay_ms(STOCK_DELAY_20);
    if (!rf_connected_latched()) {
        rf_send_ctrl(0x0B, 1, 0, 20);
    } else {
        rf_send_ctrl(0x0C, 8, 7, 20);
        delay_ms(STOCK_DELAY_20);
        rf_send_ctrl(0x0B, 0, 0, 20);
    }
    delay_ms(STOCK_DELAY_20);
    rf_wait_tx_idle();

    rf_uart_off();
    EA = 0;
    tick_pause();
    USBCON &= 0x3F;
    park_pins(true);

    EXF1 = 0;
    IENC = 0xF3;
    EXF0 = 0x40;
    IEN0 |= _EX4;
    EA = 1;
    USBCON |= _GOSUSP;
    CLKCON &= (uint8_t)~_FS;
    PLLCON &= (uint8_t)~_PLLFS;
    PLLCON &= (uint8_t)~_PLLON;
    CLKCON &= (uint8_t)~_HFON;
    USBIE1 |= 0x85;
    USBIF1 &= 0x7A;
    IEN1 = 0;
    USBCON &= 0x3B;
    REGCON &= (uint8_t)~_REGEN;
    (void)power_take_int4_woke();
    power_down();

    wake_clock();
    if (power_take_int4_woke()) {
        USBCON |= _WKUP; // a key woke it (0x1063: 0x2F.5 from the INT4 ISR)
    }
    REGCON |= _REGEN;
    delay_us(950);
    watchdog_kick();
    user_gpio_init();
    EA = 1;
    tick_resume();
    rf_ms_tick_init();
    rf_uart_init();
    rf_wake_pulse();
    rf_report_holdoff(8);
    delay_ms(STOCK_DELAY_20);
    rf_note_activity();
    rf_sleep_request_clear();
    led_wake(); // the PWM set up again; the next LED subframe lights
}

// Wired position, no host has configured the board and 10 s without a USB
// interrupt or a key (review defect 11). The power-down is the stock's
// 0x9A5E: the regulator and the oscillator stay on, a key or a USB event
// wakes it. The stock also takes it 10 s into a host's suspend and keeps the
// USB state; here a configured board stays awake through a suspend (remote
// wakeup), so after this wake USB starts over (detach, then usb_init).
void f65_sleep_wired(void)
{
    led_hold(LED_HOLD_SLEEP); // every LED off first, as before the wireless sleep
    EA = 0;
    tick_pause();
    park_pins(false);
    EXF1 = 0;
    IENC = 0xF3;
    EXF0 = 0x40;
    IEN0 |= _EX4;
    EA = 1;
    USBCON |= _GOSUSP;
    CLKCON &= (uint8_t)~_FS;
    PLLCON &= (uint8_t)~_PLLFS;
    PLLCON &= (uint8_t)~_PLLON;
    USBIE1 |= 0x85;
    USBIF1 &= 0x7A;
    IEN1 = _EUSB;
    power_down();

    wake_clock();
    user_gpio_init();
    EA = 1;
    tick_resume();
    rf_ms_tick_init();
    rf_uart_init();
    IEN1 &= (uint8_t)~_ES0;
    usb_reattach();
    rf_note_activity();
    led_wake();
}

// Every ~10 ms: the stock's sleep conditions (0x8F87 wireless, 0x8BC1 wired).
void f65_sleep_tick(void)
{
    const uint16_t idle = rf_idle_seconds();

    if (transport == RF_USB) {
        if (!usb_is_configured() && rf_usb_quiet_ms() >= WIRED_QUIET_MS) {
            f65_sleep_wired();
        }
        return;
    }
    if (rf_sleep_requested()) {
        f65_sleep_wireless();
        return;
    }
    uint16_t limit;
    if (rf_module_state() == RF_STATE_CONNECTED) {
        limit = (uint16_t)SLEEP_SETTING_S * 30u;
    } else {
        limit = (uint16_t)rf_idle_factor() * 10u;
    }
    if (limit && idle >= (uint16_t)(limit + 1u)) {
        f65_sleep_wireless();
    }
}
