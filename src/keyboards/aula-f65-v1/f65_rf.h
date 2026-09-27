#pragma once

// The radio module link of the AULA F65 V1: EUART0 (TXD P5.5, RXD P5.6,
// ~260870 baud), P0.2 = send request (low while the MCU sends), P4.7 = module
// idle (low while the module sends). Everything here follows the V1 stock
// firmware; the addresses in the comments are stock code addresses
// (the protocol notes are in the author's analysis notes, not published).

#include <stdint.h>
#include <stdbool.h>
#include "report.h"

// Transport, as the stock keeps it (XDATA 0x031A).
#define RF_USB 0
#define RF_24G 1
#define RF_BT  2

// Module states in status byte [5] (stock 0x09AE).
#define RF_STATE_IDLE         0
#define RF_STATE_PAIRING      1
#define RF_STATE_RECONNECTING 2
#define RF_STATE_CONNECTED    3

// 1 ms tick (PWM4, as the stock's 0xAEAD / ISR 0x7FBB).
void     rf_ms_tick_init(void);
uint16_t rf_ms(void);

// EUART0 setup (stock 0xAED0) and EUART0 at interrupt priority 3 (stock 0xB108).
void rf_uart_init(void);
void rf_uart_off(void);
void rf_wait_tx_idle(void); // a frame in flight finishes (bounded wait)
// Boot: the two Bluetooth names, ungated, 32 ms apart (stock 0x8B50-0x8B57).
void rf_send_names(void);

// Main-loop work while a wireless transport is in force: parse received
// frames, supervise the link, probe, pump reports, battery.
void rf_task(uint8_t transport);
// Main loop only: frame what the module has finished sending (P4.7 back
// high), for main-loop work long enough to span a short gap between two
// envelopes (the indicator render).
void rf_rx_poll(void);
// Matrix scan only (the Timer2 interrupt, at every column): the same framing,
// so a gap between two frames inside the scan is not missed.
void rf_rx_frame_isr(void);

// Link-select 0x01 (flag 1 = pair). Kept pending until it can go out; a
// pending pairing request is never replaced by a plain select.
void rf_link_select(uint8_t flag, uint8_t slot);
// Forget a link-select or pairing request that has not gone out yet (every
// transport change).
void rf_link_cancel(void);
// What 0xEC8F does after a user-initiated link-select: forget the synced
// state and re-arm the battery display.
void rf_link_resync(void);
void rf_set_slot(uint8_t slot);
void rf_set_transport(uint8_t transport); // before rf_task sees it (the drain's pacing)

// Control frames used by the transport entries and sleep (gated: busy and
// P4.7). Returns false when the frame did not go out within `wait_ms`.
bool rf_send_ctrl(uint8_t cmd, uint8_t p2, uint8_t p3, uint16_t wait_ms);

// Reports. The keyboard report goes out as the stock long frame, consumer and
// system as the short frame; nothing is dropped when the queue is full.
void rf_queue_keyboard(__xdata report_keyboard_t *report);
void rf_queue_extra(__xdata report_extra_t *report);
void rf_flush_reports(void);           // stock 0x27.7 / 0xA464: queue emptied, all released
void rf_drain_reports(void);           // send what is queued (the stock pump runs during its delays)
void rf_report_holdoff(uint8_t pumps); // stock IDATA 0x31 (8 after a wake)

// Link state for the keys, sleep, battery and the indicators.
bool    rf_connected_latched(void);  // stock 0x0E3C
uint8_t rf_module_state(void);       // stock 0x09AE (status [5])
uint8_t rf_module_slot(void);        // stock 0x09BA (status [4])
bool    rf_synced(void);             // stock 0x2d.1: the module reports the selected link
bool    rf_take_connect_event(void); // true once after the "connected" latch went on
bool    rf_status_seen(void);        // a status frame has been parsed since boot
uint8_t rf_idle_factor(void);        // stock 0x0C35: 6 pairing, 2 / 1 reconnecting, 0 never
void    rf_forget_connection(void);  // stock: 0x0E3C = 0 (wired entry, BT pairing)

// Battery (stock 0x7D42, 0x9E26, 0x3108): percent shown, USB power and
// charging, low-battery cutoff.
uint8_t  rf_battery_percent(void);
uint16_t rf_battery_raw(void); // the module's raw value (stock 0x02E9)
bool     rf_battery_cutoff(void);
bool     rf_battery_low(void); // stock 0x2b.3: raw < 781 for 24 samples (sticky)
bool     rf_external_power(void);
bool     rf_charging(void);                                              // stock 0x2d.3: P7.7 low 20 samples, high 200 clears
void     rf_power_tick(bool usb_power, bool chg_pin_low, bool wireless); // every 10 ms

// Test visibility: the P0.2 wake pulse after a wireless wake (stock 0x109C).
void rf_wake_pulse(void);

// Activity (key presses, container frames, status transitions): resets the
// idle seconds counter the sleep timeout uses.
void     rf_note_activity(void);
uint16_t rf_idle_seconds(void);
uint16_t rf_usb_quiet_ms(void);    // since the last USB interrupt or key (stock 0x02E6)
bool     rf_sleep_requested(void); // container frames cancel, cutoff requests
void     rf_sleep_request_clear(void);
