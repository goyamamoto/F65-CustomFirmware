#pragma once

#include <stdint.h>
#include <stdbool.h>

// LED engine of the aula-f65-v1 (led.c): the stock V1 hardware and register
// values, smk's timing (as on the NuPhy Air75; docs/keyboards/aula-f65-v1.md,
// "LEDs"). The 18 PWM channels are the anodes of 6 LED rows x R/G/B, the key
// columns are the cathodes; each LED subframe of the Timer2 tick shows one slot
// (a column) until the next matrix scan, 20 slots per frame.
//
// Cells are addressed as the stock frame buffer does: column 0-15 (the key
// column), LED row 0-5 (row 0 = the side lights, rows 1-5 = key rows = smk
// matrix row + 1) and slot channel 0-2 (keys: 0 red, 1 green, 2 blue; side
// lights: 0 blue, 1 red, 2 green).

#define LED_KEY_COLS 16
#define LED_ROWS     6
#define LED_CHANNELS 18 // LED_ROWS x 3

// Slots per frame. 16 key columns plus 4 slots that select no column, as the
// stock (0x6B79: 20 slots, of which 18 and 19 select nothing; this port never
// selects the stock's LED-only columns 16/17, P4.6/P7.4, either). A slot is one
// LED subframe (~400 us, <= 5 PWM periods) in every ~750 us, so an LED is on at
// most ~5 periods in ~150: below the stock indicators' 1 period in 20. Never
// fewer slots.
#define LED_SLOTS 20

// DUTY2 limits: the pulse runs from DUTY1 (the channel's phase, 0xB4-0xC5) to
// DUTY2. The stock indicators use 0x0400 for "on" and the phase for "off".
#define LED_DUTY_ON  0x0400u
#define LED_DUTY_MAX 0x0400u

// Why the LEDs are held off (bits of a mask; the PWM runs only when none is set).
#define LED_HOLD_STOPPED 0x01 // not started yet (indicators_start clears it)
#define LED_HOLD_SCAN    0x02 // matrix scan in progress (indicators_pwm_disable)
#define LED_HOLD_FLASH   0x04 // settings save: flash erase / program
#define LED_HOLD_SUSPEND 0x08 // USB suspend
#define LED_HOLD_SLEEP   0x10 // the wireless and the wired sleep (f65_power.c), until the wake is done
#define LED_HOLD_ISP     0x20 // about to jump to the ISP bootloader (never released)
#define LED_HOLD_STALL   0x40 // the matrix scans have stopped (led_stall_check)
#define LED_HOLD_CUTOFF  0x80 // low-battery cutoff (indicators.c): everything dark, as the stock

void     led_init(void);
void     led_wake(void); // after a sleep: the stock PWM set-up again, then LED_HOLD_SLEEP released
void     led_hold(uint8_t reason);
void     led_release(uint8_t reason);
void     led_suspend(bool on);
uint8_t  led_holds_get(void);
uint16_t led_phase(uint8_t row, uint8_t c);

// Sets one channel; the value is clamped to [phase, LED_DUTY_MAX] (0 = off).
// (led_diag.c; the lit images go through the layers below.)
void led_set_duty(uint8_t col, uint8_t row, uint8_t c, uint16_t duty);

// Two layers make the duty table (ansi / usjis):
//  - led_fb8: the backlight (backlight.c), 0-255 per slot channel as the stock
//    frame buffer fb8 (0x0155; row 0 = the side lights, slot order B, R, G).
//    A value v becomes DUTY2 = phase + 3 v + (58 v >> 8) (~3.22 v), at most
//    0x03FB: the stock's v * 4 + phase scaled into the 0x0400 cap.
//  - an override per cell (the indicators): the channels in `rgb` at
//    LED_DUTY_ON, the others off, whatever the backlight holds; LED_OVR_NONE
//    gives the cell back to the backlight.
// Writers mark the columns they change; led_flush loads one marked column into
// the duty table per call (main loop). While blank, the backlight counts as 0.
#define LED_OVR_NONE 0xFF
extern __xdata uint8_t led_fb8[LED_KEY_COLS][LED_ROWS][3];
extern __xdata uint8_t led_dirty[LED_KEY_COLS];   // columns to load (a writer sets the flag)
extern __xdata uint8_t led_rgb[3];                // led_px's colour (slot channels 0-2)
void                   led_px(uint8_t k) __naked; // cell k = column * 6 + row in led_rgb (led.c)
void                   led_mark_col(uint8_t col);
void                   led_mark_all(void);
void                   led_cell_override(uint8_t col, uint8_t row, uint8_t rgb);
void                   led_set_blank(bool blank);
bool                   led_flush(void);
// Every channel of the table back to its phase (all off).
void led_clear(void);
// Turns off the entries out of [phase, LED_DUTY_MAX] in the next column of the
// table, one column per call (a guard against a corrupted table); true if there was one.
bool led_audit(void);
// Main loop, every pass: holds the LEDs dark while the matrix scans have not
// advanced for LED_STALL_MS (the column a subframe selected would otherwise
// stay lit until the next scan); releases the hold once they advance again.
#define LED_STALL_MS 5
void led_stall_check(void);

// indicators.c (ansi / usjis) or led_diag.c (leddiag) also define
// indicators_init / indicators_render.
void indicators_usjis_changed(bool enabled);                  // a US-JIS change, to show on Tab
void indicators_battery_show(bool on);                        // Fn+B held: the battery level on 1..0
bool led_diag_process_record(uint16_t keycode, bool pressed); // leddiag: the diagnostic keys
