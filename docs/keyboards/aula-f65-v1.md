# EPOMAKER x AULA F65 (V1, "Fn-Ctrl")

## Specs

- MCU: SinoWealth SH68F90 family ([SH68F90](../platforms/sh68f90.md)); the stock image decodes with smk's `sh68f90.h`. Stock USB ID 258a:010c, manufacturer "BY Tech", product "Gaming Keyboard", bcdDevice 0x0500
- Only the V1 ("Fn-Ctrl" firmware, Fn + Left Ctrl swaps the F-row). The V2 ("Alt-Fn", product "AULA F65", VIA) scans its columns on other pins; do not write this image to a V2
- Matrix: 5 rows × 16 columns, 67 keys. Rows P7.1-P7.3, P5.3, P5.4 (inputs, pulled up); columns P6.0-P6.7, P5.0-P5.2, P5.7, P4.0, P4.2, P4.3, P4.5 (outputs, active low). The stock scan has a sixth row bit on P7.0 that it masks off and two more column slots (P4.6, P7.4) with no key in its keymap; they carry LED current only
- Backlight: LED matrix sharing the columns, anodes on the 18 PWM pins P1.0-P1.5, P2.0-P2.5, P3.0-P3.5 (6 LED rows × RGB), one column per 100 µs slot, 20 slots per frame. This port drives the stock effects, the side lights and the status indicators (see [LEDs](#leds))
- Connection switch (3 positions): P0.5 low = 2.4 GHz, P0.4 low = Bluetooth, both high = USB (middle)
- Radio: a module on EUART0 (TXD P5.5, RXD P5.6, ~260870 baud). P0.2 is the MCU's send request (low around each frame it sends, and a short wake pulse); P4.7 is low while the module sends. The module chip is not identified (BK3632 is a guess from the Aula F75)
- Power: P4.4 reads high with USB power; P7.6 is driven to its inverse, as the stock does. P7.7 (pulled up) reads low while charging. P4.1 and P7.5 are held low as in the stock init; P4.1 goes high before a sleep. With the switch in the middle, unplugging USB cuts the MCU's power (on the unit nothing lights with the stock firmware), so replugging is a power-on reset. In the wireless positions the battery keeps the MCU powered

The pin map and the radio protocol come from static analysis of the stock V1 image and from running it in the simulator, not from the PCB. The protocol notes are the author's (not published); what the firmware does is written in this document and in the comments of `f65_rf.c` and `f65_power.c`.

## SMK Supported Features

- [x] Key Scan (checked on the board with the wired build: ansi and usjis, all keys, the usjis IME keys, Fn+A / Fn+S kept across a replug, Fn+Tab US-JIS)
- [x] Wireless: Bluetooth (3 slots) and 2.4 GHz through the module (simulator only; not yet checked on the board)
- [x] Sleep in the wireless positions and in the middle position without a USB host (simulator only)
- [x] Remote wakeup (simulator only): while the host has suspended the bus and enabled remote wakeup, a key going down sets USBCON.WKUP once, 8 matrix scans after the suspend (`USB_WAKE_ON_KEY`); not while the host itself is resuming the bus
- [x] Status indicators, the stock V1 LED scheme with the Air75 timing (checked on the board with build-4c, wired: Caps, the Fn-held keys, charging); since build-5 also the link and pairing keys, the battery display (Fn + B), the low-battery warning and Caps Lock over the radio (simulator only), see [LEDs](#leds)
- [x] Every LED off before either sleep, back after the wake (simulator only)
- [x] The stock key-backlight effects (1-17 of the Fn + \\ cycle), the side lights, brightness, speed and colour, saved (build-6, simulator only: every effect frame for frame against the stock image), see [Backlight](#backlight)

## Connection switch

- Read every 10 ms; a position counts after 10 samples in a row (~100 ms). The middle position wins over 2.4 GHz, which wins over Bluetooth, as on the stock
- Into 2.4 GHz or Bluetooth: every key is released on the radio side, USB is switched off - ENUSB, the D+ pull-up (SW1CON) and the USB interrupt, so the host sees the keyboard unplugged at once and nothing on the bus brings USB back (`usb_init` also refuses outside the wired position, `USB_INIT_GATE`, as the stock's 0xAF16 does). EUART0's interrupt goes on and the module is told the link: `01 01 00 00 00 53` (2.4 GHz) or `01 01 00 0n 00 ck` (Bluetooth slot n). (The stock clears ENUSB only; its pull-up stays on)
- Into the middle position: every key is released, the module is told `01 0E 00 00 00 46` and `01 0B 00 00 00 49`, EUART0's interrupt goes off and USB starts over with the pull-up (an attach for the host). USB is back about 0.1 s after the switch settles
- Every transport change forgets a link-select or pairing request that has not gone out yet and a pairing hold in progress
- The position and the Bluetooth slot are saved with the settings. At power-up the saved position holds until the switch has been read ten times; USB is enabled at boot only when the saved position is the middle one (`BOARD_USB_BOOT_GATE`). A board saved in a wireless position and powered in the middle position tells the module `0E` / `0B(0)` once and then comes up on USB
- The two Bluetooth names go to the module on every boot, in every position, as on the stock: profile 0 "AULA F65 BT5.0 ", profile 1 "AULA F65 BT3.0 "

## Radio link

As the stock V1 firmware, with the differences listed below. Frames to the module are `01 <cmd> … <0x55 − sum of the others>`.

- EUART0 set up value for value as the stock (SCON 0x50, SBRT 0xFFFB, SFINE 0x0C, SSTAT) at interrupt priority 3 (IPH1/IPL1 ES0 bits; `RF_EUART0`). A byte arrives every 38 µs and smk's matrix scan runs ~320 µs in the Timer2 interrupt; at the default priority the scan loses bytes (reproduced in the simulator). The interrupt clears the FE / RXOV / TXCOL flags on every entry
- A frame from the module is what arrives while P4.7 is low; it is taken when P4.7 is high again. That is looked at from the matrix scan at every column (the Timer2 interrupt, ~320 µs with the main loop stopped, the same place the stock looks from its 100 µs interrupt, 0x6C18), on the 1 ms tick (PWM4 as a timer, as the stock; `PWM4_MS_TICK`), from the LED subframe (the same interrupt), on every main-loop pass with the indicator render, its LED-table audit and the backlight looking as they go, and before a send restarts the receive buffer; the longest stretch between two looks, wherever they come from, is ~80 µs in the simulator with effect 15 at full brightness and status frames arriving. Three receive banks: up to two taken frames wait for the parser (once per main-loop pass, ~0.5 ms with the backlight running) while the third receives, so two frames with P4.7 high between them for 100 µs or more are both parsed wherever the gap falls (`tests/test_f65_backlight.py`, 40 phases at 100-800 µs; build-6 had two banks and no look from the scan, and lost one or two phases of 40 at every gap). As the stock parser, only the first frame of a P4.7 envelope counts and its first byte says what it is; an envelope that starts with anything else is dropped whole
- P0.2 (the send request) goes low before the first start bit about as long as the stock's code takes there (measured in the simulator: long report 55.0 µs, link-select 28.6, name 28.9, short report 21.8, control and ack 11.3; the stock's shortest there: 34.9, 5.0, 23.5, 17.2, 3.4-5.0), and stays high at least 1 ms between two frames
- Status `02 06 00 LED slot state battL battH x ck` (answer to `01 06 00 00 00 4E`, sent every 200 ms): when the module reports another slot (2.4 GHz: slot 0) or state 0, the link is selected again; state 3 = connected. Byte 3 is the host's LED state (Caps Lock …), stored like the USB LED report (the Caps Lock indicator shows it). Every sixth status frame runs the battery
- Announce `03 …` → `01 F0 00 00 00 64`; container `08 …` (the vendor driver's protocol) → `01 F1 00 00 00 63` (none for sub-command 8), nothing else: no container sub-command is acted on, factory reset (06) included. `01 04 …` (the stock's hidden test mode) is never sent
- A send waits for the frame in flight to finish, the 1 ms gap and P4.7 high (the acks only for the first two). A stuck send (no UART interrupt for three status requests) is abandoned and P0.2 released, as the stock does. An announce restarts the 200 ms of the status request (0x07A1)

## Reports over the radio

- Keyboard: the stock long frame, `01 02 mods k1 … k5 00 bitmap[16] 00 00 00 00 ck`. Held keys sit in press order in the five slots (a release closes the gap); a sixth key goes into the bitmap (bit n = usage n) until it is released. smk's report is 6KRO, so a seventh key is not sent (the stock has no NKRO switch on the radio either)
- Consumer and system: the short frame `01 03 usage_lo usage_hi sys 00 … ck`
- After the last key goes up: three times an ErrorRollOver frame (key 1 = 0x01) and an empty one, so a lost release cannot leave a key stuck
- Bluetooth: one report every 8 ms (125 Hz), also for the release frames a transport change or a pairing sends at once; 2.4 GHz: on the 2 ms pump, and with the 1 ms gap after a 1.2 ms long frame that is one long frame per 4 ms (the stock: per 2 ms). After a wake the first 8 pump passes send nothing
- smk sends a report per key change (the stock one per scan), so keys pressed within one scan go out as separate frames

## Keys

- **Fn + Q / W / E** (Bluetooth position only): a short press selects slot 1 / 2 / 3 (the frame goes out on release); held 3 s it pairs that slot (`01 01 01 0n 00 ck`)
- **Fn + R** (2.4 GHz position only): held 3 s it pairs the dongle (`01 01 01 00 00 52`); a short press does nothing
- The stock has them on Fn + E / R / T and Fn + Q; they sit on Q / W / E and R here as on other makers' boards (and the NuPhy Air75). Fn + T sends nothing
- In the other positions these keys send nothing. The hold is timed on the 1 ms tick. The key of the link blinks while it pairs or reconnects; while Fn is held only the active link's key lights: Y white (USB), R green (2.4 GHz), the slot's key blue (Bluetooth) (see [LEDs](#leds))
- **Fn + B** held: the battery level on 1 … 0 (wireless positions on battery, as the stock); sends nothing
- Lighting (`ansi`, `usjis`; see [Backlight](#backlight)): **Fn + \\** next effect, **Fn + ]** next colour, **Fn + [** backlight and side lights on / off, **Fn + ↑ / ↓** brightness, **Fn + → / ←** speed faster / slower (the side lights follow), **Fn + /** next side effect, **Fn + .** next side colour, **Fn + ,** next side brightness. None sends anything; under Fn the arrows are these keys, not arrows (before build-6 the Fn layer passed them through)
- Win layer (default) as printed: `Ctrl Win Alt Space Fn Ctrl ← ↓ →` on the bottom row
- Mac layer: the stock Mac table's modifiers, `Ctrl Option Command Space Fn Ctrl`. **Fn + A** selects Win and **Fn + S** selects Mac (saved; default Win); the key for the mode already in force changes nothing and writes no flash
- Number row under Fn, with Right Shift as a second Fn:

  | | Fn + 1 … = | Fn + Right Shift + 1 … = |
  |---|---|---|
  | Win | F1 … F12 | the stock Win Fn row: Brightness −, Brightness +, Alt+Tab, Alt+Esc, WWW Home, Mail, Previous, Play/Pause, Next, Mute, Volume −, Volume + |
  | Mac | Apple's order: Brightness −, Brightness +, Mission Control, Launchpad, (nothing), (nothing), Previous, Play/Pause, Next, Mute, Volume −, Volume + | F1 … F12 |

  Right Shift under Fn picks the other set and is not Shift while Fn is held. Pressed after Fn it does not reach the host while Fn is held. Pressed before Fn it is Shift until Fn goes down (the host sees that Shift press and release), then taken out. When Fn goes up with Right Shift still held, Shift is not put back at once (the host would see a Shift tap on its own when both keys go up); it comes back just before the next ordinary key, if Right Shift is still held, and goes out again if Fn is pressed again. US-JIS sees exactly the Shift the report carries. A Shift or Alt this code adds for a key of its own (the `~`, Alt+Tab, Alt+Esc) is added only if the user is not holding it and taken away only if added. Right Shift alone, or with ordinary keys, is Shift; Left Shift under Fn stays Shift. Consumer usages go out on USB and as the radio's short frame; the consumer report descriptor goes up to 0x2FF (`CONSUMER_USAGE_MAX`) for Mission Control 0x29F and Launchpad 0x2A0
- Fn layer otherwise: Esc = `` ` `` (Shift or Right Shift gives `~`), Del = Insert, PgUp = `` ` `` (stock), End = Home, U / I / O = PrtSc / ScrLk / Pause, Tab = US-JIS on/off (`usjis`; plain Tab in `ansi`), B = battery display, M = nothing, the lighting keys above. The other positions are transparent
- Settings reset: hold Fn + Backspace, then press Fn + V. Puts US-JIS off, the Win layer and the lighting defaults back (the saved position and slot stay). Under Fn, Backspace and V send nothing
- `usjis` only: the keys on both sides of Space are mod-taps for the Japanese IME. Win: left = Alt held / Muhenkan (INT5) tapped, right = Right Ctrl held / Henkan (INT4) tapped. Mac: left = Command / Eisu (LANG2), right = Right Command / Kana (LANG1). Fn and the stock Right Ctrl swap places, so the bottom row reads `Caps Win/Opt Alt*/Cmd* Space Ctrl*/Cmd* Fn ← ↓ →`
- No Fn chord waits for a timeout: Fn is a plain momentary layer. A key goes up with the keycode it went down with, whatever Fn did meanwhile (`LATCH_KEYCODES`)
- Everything above works the same over USB and over the radio
- USB: 258a:010c (the stock ID, so `sinowisp -d aula-f75` finds the running board), manufacturer "SMK", product "AULA F65 (SMK)", 6KRO boot-protocol report (`NKRO_DEFAULT_OFF`)
- Feature reports on interface 1 (`USB_FEATURE_REPORT_STRICT`): ID 5 with `05 75` enters the ISP bootloader (what sinowisp sends); ID 5 with anything else is acknowledged and ignored; any other ID is stalled

US-JIS works as on the [NuPhy Air75](nuphy-air75.md#us-jis), in the Win layer only, over USB and the radio alike.

## Battery

- The module reports the cell as a raw value; percent = (raw − 715) × 10 / 22, capped at 100. The display follows the stock's rules (0x7D42): the first value after a link-select, an announce or USB power going away is shown as it is; on battery it falls 1 % per 20/10/5/2 passes (by the distance in tens), jumps down when 30 or more above the target and jumps up when the target is 30 or more higher; with USB power it rises the same way, jumps up at 20 or more, drops at once when 20 or more above, shows 99 instead of 100 while charging (P7.7 low for 0.2 s), and goes from 95-99 to 100 once charging has stopped (P7.7 high for 2 s). A pass runs on every sixth status frame. Each change goes to the module as `01 0D pp 00 00 ck`
- USB power is P4.4 high for 3 samples (30 ms), gone after 3 low samples
- Low-battery cutoff (wireless positions): raw below 737 for 2 s stops new key reports, turns every LED off and puts the board to sleep, again every 2 s while it lasts. USB power, or raw above 912 for 1 s, ends it
- Low-battery warning (wireless positions, on battery, not during the cutoff): raw below 781 for 24 samples (0.24 s) sets it; it stays until raw is above 912 for 1 s or USB power comes (stock 0x31A1-0x31D1). The Fn key blinks red while it holds
- Battery display: Fn + B held in a wireless position on battery lights keys 1 … 0, one per step the raw value reaches (737, 758, 781, 802, 824, 846, 868, 890, 912, 934), green from 802 up, red below (stock 0x4199 / 0x3245)

## Sleep

- Wireless positions, after this long without a key: connected (state 3) 61 s; pairing 61 s; reconnecting 21 s in Bluetooth, 11 s in 2.4 GHz; never before the module has sent a status frame. (The stock takes the connected timeout from a setting only its vendor tool changes; this port uses the factory value, 2 → 30 × 2 + 1 s.) Container frames and status changes count as activity
- Before sleeping every LED goes off (PWM stopped, the anode latches 0, every column released, as the stock's 0xAD49 before its parking), then the module is told `01 0C 08 07 00 39` then `01 0B 00 00 00 49` when connected, else `01 0B 01 00 00 48`. Then EUART0 off, the pins parked as the stock parks them (in the simulator, P0 and P4-P7 with their direction and pull-up registers, the wake sources and USBCON equal the stock's at the power-down instruction: key columns and both switch pins driven low, rows pulled-up inputs, P4.1 high, P7.6 low), INT4 armed for the keys, clocks and regulator off, power-down
- A key wakes it; the LEDs come back once the wake is done, after the stock PWM set-up has run again (`led_wake`, as the stock runs 0x6313 after its wake). USB stays off after a wireless wake, the USB interrupt and the pull-up included (the stock turns those two back on with ENUSB off); USBCON.WKUP is set only when the key interrupt woke it (0x1063). EUART0 comes back, P0.2 is pulsed low for 1-2 ms to wake the module (the stock: 0-2 ms), reports hold for 8 pump passes. Nothing is re-sent; the next status frame re-selects the link if it has to
- Asleep in a wireless position, moving the switch does nothing until a key wakes the board (only the keys wake it); then the switch is read and, in the middle, USB comes back
- Middle position with USB power but no host that has configured the board (a charger, or a host that stopped after a bus reset): after more than 10 s without a USB interrupt or a key (the stock's counter, cleared in the USB interrupt, 0x954B / 0x8BC1) it turns every LED off and sleeps as the stock's 0x9A5E (key or USB event wakes it), and USB and the LEDs start over after the wake. A bus reset and the enumeration that follows are USB interrupts, so a host rebooting after a long idle is served. A configured board stays awake while the host suspends the bus (remote wakeup above); the stock also sleeps then, keeping the USB state

## Recovery

- **Esc held at power-up** (`BOOT_ESCAPE`): the firmware jumps to the ISP bootloader right after `clock_init()`, before USB, the settings and anything of the radio; `sinowisp` then finds the bootloader (0603:1020). The check needs all 16 samples over about 8 ms to read pressed. Only Esc is checked; at runtime Fn+Esc is the grave key
- Power-up means the middle position: there, unplugging USB removes the MCU's power. In a wireless position the battery keeps it running, so replugging is not a reset. **From any state: move the switch to the middle, unplug, hold Esc, plug in**
- Moving the switch to the middle brings USB back with a running firmware, and sinowisp's report 5 then enters the bootloader. In a wireless position USB is off, so sinowisp cannot reach the board. A board asleep in a wireless position needs a key first (see Sleep)
- Wait about 3 s after unplugging before plugging in again when the replug is meant as a power-on reset (the supply has to fall)
- A hang in a wireless position: switch to the middle and unplug (the power goes), then plug in again (holding Esc for the bootloader)
- The watchdog is kicked by the main loop only: `delay_us` does not kick it (`DELAY_NO_WATCHDOG_KICK`; `delay_ms`, used in the main loop only, kicks every 250 µs of its loop, and so do the bounded waits of the radio, the USB endpoint drain, each LED cell the indicator render writes, and each flash erase and program, `FLASH_OP_WATCHDOG_KICK`, as the stock at 0x94A9), so a main loop that hangs while the interrupts run (the matrix scan calls `delay_us`) is not kept alive. The PLL lock wait at power-up gives up after 20000 polls and switches anyway (`CLOCK_PLL_WAIT_BOUND`; the stock never polls PLLSTA, it switches after a fixed delay). The longest stretch without a kick in the simulator at the board's scan rhythm (the scan takes about 40 % of the core), with the LEDs lit, through module-busy flushes, pairing, the battery display, both sleeps, switch changes, 30 Caps Lock toggles and a held USB endpoint, is 1.8 ms (USB 1.4 ms, 2.4 GHz and Bluetooth 1.7 with the backlight, from reset to the first renders 1.8 since build-6's led_init clears more XRAM; build-5b: 1.5; the stock's: 8.2 ms); the tests hold it under 3 ms. Whether the SH68F90's watchdog is enabled at all is a code option the image does not set; in the simulator a hung main loop is reset and Esc held through the reset reaches the bootloader. The power-off path above does not depend on it, so no runtime ISP chord is added
- If neither the running firmware's report 5 nor Esc at power-up works, only the ICP programming interface is left, and the F65's ICP pads are not known

## Stack

`--stack-auto`, IDATA 0x25-0xFF (219 bytes). The static bound, walked for the wireless build before the LEDs were merged (the relocated listing from each entry, one re-entry of `process_keycode` for the keys this code sends itself counted): main 94 bytes (usjis; ansi 82) + the deepest level-0 interrupt, USB, 49 + EUART0 at level 3, 7 = 150; the build-3 review put it at about 178 of 220 counting more re-entry. The LED subframe runs in the Timer2 interrupt, also level 0, so it never nests with USB; the indicator render adds a few calls to the main loop's depth. The simulator measures a wireless session with container bursts, the Fn-row keys, a sleep and a wake, then a SETUP handled during the Alt+Tab key's re-entry, under half the room; and with the LEDs lit and USB requests, 68 of 219 bytes.

## Differences from the stock firmware

- After a link-select the supervisor waits 600 ms before selecting again (the stock re-selects on every mismatching status frame, which can cut a pairing request short). A link-select that cannot go out waits (the stock drops it) and a waiting pairing request is never replaced by a plain select
- The acks wait for a frame in flight (the stock sends them over it). P4.7 is sampled from the matrix scan at every column, at every LED subframe, on every main-loop pass and every 1 ms (the stock: every 100 µs), two taken frames can wait for the parser, and a frame that ends as a send starts is parsed (the stock loses it: in the simulator it drops a status frame now and then that way)
- P0.2 stays high at least 1 ms between frames (the stock's gaps are as short as 17 µs in places), so 2.4 GHz long frames go out at most one per 4 ms
- A link-select or pairing request waiting for the module and a pairing hold are forgotten on every transport change
- USB in the wireless positions: the D+ pull-up is off too, and the USB interrupt stays off after a wireless wake (the stock clears ENUSB only)
- No `08` container frames are sent (battery / charge, layer / OS, readbacks: the vendor driver's protocol), and none received is acted on
- The ErrorRollOver frames wait until every key is up (the stock sends them once the five slots are empty, even with a sixth key still in the bitmap)
- The release frames of a transport change or a pairing go out before the next control frame (on the stock the short one can follow it)
- The battery cap does not wrap (the stock's cap tests only the low byte: raw 1279 and above wraps)
- The connected sleep timeout is fixed at the factory 61 s
- The middle position sleeps after 10 s without a key when no host has configured the board (the stock: ~10 s without a USB interrupt)
- The link keys sit where this port selects the links: R = 2.4 GHz (the stock: Q), Q / W / E = Bluetooth 1-3 (the stock: E / R / T); the blink colours and rhythms are the stock's; while Fn is held R is green and the Bluetooth slot's key blue (the stock: white)
- The Fn-held link key needs the module to report the selected link (the stock checks only its "connected" latch, which can be left from the previous slot for a moment); the 3 s solid ends early if the link drops meanwhile
- No white Y for 3 s after switching to the middle position (stock 0x2d.5)
- Lighting keys: the colour key is Fn + ] (stock Fn + Tab), side effect Fn + / (stock Fn + Right Shift), side colour Fn + . (stock Fn + /), side brightness Fn + , (stock Fn + .); Fn + [ turns all lighting off (not on the stock); the side speed follows the key speed (the stock has a side speed key, Fn + ,). No power-on sweep, no per-key custom slot (18), no Fn + G test mode
- The effects paint the cells the indicators hold too (the indicators cover them); on the stock the effects skip them, and a cell an indicator lets go shows the old frame until the next repaint
- Backlight levels are the stock's scaled into DUTY2 ≤ 0x0400 (the stock goes to 0x04C1); default brightness 2 of 4 (stock 4)
- On battery the backlight goes dark 30 s after the last key (the stock: 8 s, armed by the host only); the settings are saved 3 s after the last change (the stock: when Fn goes up)
- In the low-battery cutoff every LED is dark (the stock stops repainting and leaves what was lit)
- The battery display ends when USB power comes or the switch leaves the wireless positions while Fn + B is held (the stock checks that only at the press)

## Layouts

Three layouts share the board code: `ansi` is plain US ANSI as printed; `usjis` adds US-JIS (`USJIS`), the IME mod-taps (`TAP_HOLD`), swaps Caps Lock and Left Ctrl, and swaps Fn with Right Ctrl; `leddiag` is `ansi` with the LED diagnostic instead of the indicators (`LED_DIAG`, see [LEDs](#leds)).

## LEDs

The hardware and the register values are the stock V1 firmware's (addresses below; the evidence and the full cell map are in the author's analysis notes, not published); the timing is smk's, as on the NuPhy Air75, which drives its LEDs from the same MCU's PWM on shared columns and is proven on hardware:

- PWM banks 0-2 (PWM00-05, PWM10-15, PWM20-25) at Fsys/2 = 12 MHz, period 0x04B0 (1200 counts = 100 µs), drive the anodes of 6 LED rows × R/G/B; the key columns are the cathodes. The set-up at start is the stock init write for write (0x6313: CON of channels 1-5 = 0x08, period, DUTY1 = DUTY2 = the channel's phase 0xB4-0xC5, then CON = 0x89 per bank). The stop is the stock 0xAD49 (every CON = 0x01; the pins fall back to their latches, which stay 0).
- smk's Timer2 tick alternates a matrix scan and an LED subframe (`tick.c`). The scan stops the PWM and releases every column first (`indicators_pwm_disable`), so no key row is read with the PWM on or an LED column low. The subframe after it (`indicators_update_step`) moves to the next of 20 slots; if the slot's column has something lit, it loads that column's 18 DUTY2 values in the stock order (0x6C45: PWM21 20 22 | 24 23 25 | 11 10 12 | 14 13 15 | 04 03 05 | 01 00 02), selects the column and then starts the banks in the Air75's order: each bank started (CON0 0x89), then its channels 1-5 routed (CON 0x08), so no channel is routed to its pin while its bank is stopped. The column stays lit until the next scan, about 400 µs (4 PWM periods, the last one cut by the scan). DUTY1 stays at the phase, so every pulse starts at least 15 µs after the banks start.
- The dwell is bounded by the Timer2 interrupt that runs the next scan (a level-0 interrupt, re-armed by the subframe for ~400 µs), not by a hardware timer that stops the PWM: the scan starts once that interrupt is taken, after a level-0 interrupt in service (USB), and EUART0 at level 3 delays it only by its own few microseconds (measured in the simulator with the module sending back to back: at most 386 µs with the PWM running and a column low). If the scans stop altogether while the main loop runs, `led_stall_check` holds every LED dark once no scan has come for 5 ms on the 1 ms tick (`LED_HOLD_STALL`), and lets them light again when the scans come back. `LED_SUBFRAMES_PER_SCAN` other than 1 is a build error.
- The subframe clears TF2 once it has re-armed Timer2 (`indicators_update_step`). The Timer2 interrupt that starts a subframe clears TF2 at its top, but Timer2 is still running on the scan's ~100 µs reload until the subframe re-arms it a few cycles later; an overflow of that reload in between left TF2 set, the interrupt came again at once and was taken for the next scan, and that subframe's column was lit for ~13 µs instead of ~385 µs. Where the scan ends on that grid is fixed by the code and the chip's timing, and the delay before the handler (a main-loop critical section) varies, so near the edge it hit now and then, on whichever column was showing: with Fn held, one lit key at a time blinked off every few seconds (build-5 on the board). Right on the edge it would have repeated every subframe. The simulator's test sweeps that phase over a whole grid period in every position
- There is no LED interrupt, no interrupt priority change for the LEDs (EUART0 at 3 is the radio's), and no interrupts-off in the LED path apart from the one-time set-up at start. No LED code is reachable from any interrupt handler but the Timer2 tick, and from that only the scan's stop and the subframe (a test walks the linked image's call graph). build-4 used the stock scheme instead: a PWM0 period interrupt at priority 2 that switched columns every 100 µs and nested over the USB interrupt. On the board that build froze the host's input while typing with Caps Lock toggles (build-2 did not), so build-4c moved to the Air75 timing.
- Slot n = column n for n < 16; slots 16-19 select nothing (the stock drives P4.6 and P7.4 in 16 and 17; whether LEDs sit there is unknown, so this port never drives them low). A slot whose column has nothing lit selects no column and leaves the PWM off.
- Cells follow the stock frame buffer: column, LED row (0 = side lights, 1-5 = key rows = smk row + 1), channel (keys: 0 red, 1 green, 2 blue; side lights: 0 blue, 1 red, 2 green). An indicator's "on" is DUTY2 = 0x0400, "off" the phase, as the stock indicators; the backlight's levels are below 0x0400 (see [Backlight](#backlight)).
- Each LED is lit one subframe (≤ 5 periods) per 20 subframes (~15 ms): about 3 % of the periods, against the stock indicators' 1 in 20 (5 %). The backlight uses the same subframes, so at the same DUTY2 it is about half as bright as the stock's (which shows a column 1 period in 20).
- Keys are debounced across scans (`MATRIX_DEBOUNCE_SCANS` = 4, ~3 ms): a key changes state only once its new state has been read in 4 consecutive scans, as the stock does (0x1DB7, a counter per key). Bouncing contacts give one press and one release.

Hard limits, each enforced in code and checked in `tests/test_f65_led.py` against a trace of the simulated PWM and pins:

| Limit | Enforced by |
| --- | --- |
| DUTY1 ≤ DUTY2 ≤ 0x0400 | the backlight's DUTY2 = phase + 3 v + (58 v >> 8) ≤ 0x03FB for any 8-bit value v (`led_px`, `led_flush_row`); indicators 0x0400; `led_set_duty` (leddiag) clamps; the set-up holds the phases; `led_audit` (one column every ~10 ms) turns off any entry found out of range and reloads the column |
| At most one column low while the PWM runs, never switched while it runs | the subframe selects the column with the banks stopped, then starts them; the scan and every stop release all columns after stopping the banks |
| PWM off, every column released, for key-row reads, settings saves (flash erase / program), the report-5 ISP jump, USB suspend (wired position), both sleeps, stalled scans, the low-battery cutoff | `led_hold` reasons: `LED_HOLD_SCAN`, `_FLASH` (`settings_save_pre`), `_ISP` (`usb_isp_prepare`, via `USB_ISP_PREPARE` in `usb.c`), `_SUSPEND` (`kb_update`, in the middle position only), `_SLEEP` (`f65_sleep_wireless` / `f65_sleep_wired`, before the parking; released by `led_wake` after the wake, which first runs the stock PWM set-up again), `_STALL` (`led_stall_check`), `_CUTOFF` (the indicators). The boot escape runs before `led_init`; before it, the PWM registers are only put back to their reset value (next row) |
| Nothing left driving the anodes after a start without a reset (a jump to 0) | `STARTUP_LED_OFF` in `startup.c`, the first code to run: interrupts off (EA), PWM00-25 CON = 0, the PWM0 interrupt off, P1-P3 inputs. SDCC's own init writes `__XPAGE`, SFR 0xA0, which is P3 (anodes PWM00-05) on this part |
| P1-P3 latches stay 0 | written 0 at init and on every stop (`user_matrix_sinks_off`), never set |
| A column is never selected longer than one slot | one LED subframe: the next scan's Timer2 interrupt (level 0; see above) stops the banks and releases it; `led_stall_check` if the scans stop |
| P4.6 / P7.4 never driven low | no slot selects them; every stop drives them high |
| An LED is on at most as much as the stock indicators (0x0400 in 1 of 20 periods) | `LED_SLOTS` = 20 (static assert): ≤ 5 periods per 20 subframes |

Indicators (`indicators.c`, `ansi` and `usjis`), over the backlight (a cell an indicator holds shows the indicator, lit or dark; the others the backlight), after the stock painter 0x3108 (addresses from the author's analysis notes, not published), with the link keys where this port selects the links. Every "on" is DUTY2 = 0x0400 per channel. The timers run on a 10 ms tick from the 1 ms PWM4 counter and compare as the stock does. The render works once per 1 ms tick (the other main-loop passes stay as short as without LEDs) and catches its 10 ms clock up by at most 10 ticks:

| Key | Colour | When |
| --- | --- | --- |
| the key that sends Caps Lock | white | the host's Caps Lock LED: the USB LED report, or byte 3 of the module's status frame over the radio, there only while the link is connected (0x34EA). `ansi`: the Caps key; `usjis`: the stock Left Ctrl position |
| Fn | red | charging: USB power (P4.4 high 3 samples) and P7.7 low 20 samples (cleared after 200 high), the stock 0x9E26 sampling every 10 ms |
| Fn | red, blinking (250 ms on, 250 ms off) | low battery: a wireless position on battery, raw below 781 for 24 samples (0x31D3); not while the battery display shows |
| Y | white | Fn held, the middle position (USB); while Fn is held the other link keys (Q, W, E, R) are dark |
| R | green | Fn held, 2.4 GHz connected |
| Q / W / E | blue | Fn held, Bluetooth slot 1 / 2 / 3 connected (only the active slot's key; the others stay dark) |
| R (2.4 GHz, cyan), Q / W / E (Bluetooth slot 1-3, blue) | blinking, toggled every 80 ms | the module reports pairing (status state 1), with or without Fn |
| same | blinking, toggled every 380 ms | reconnecting (state 2) |
| same | solid for 3 s | from the moment the link is connected (state 3), then off (green / blue while Fn is held) |
| Tab | green = US-JIS on, red = off | Fn held, and ~1 s after a change (`usjis` only) |
| A / S | white | Fn held: the Win / Mac layer in force |
| 1 … 0 | green (raw ≥ 802) or red | Fn + B held, a wireless position on battery: one key per step 737, 758, 781, 802, 824, 846, 868, 890, 912, 934 the raw value reaches (0x3245); every other indicator is dark meanwhile, as on the stock (it clears every cell at the press, 0x41B4, and paints nothing else, 0x34D6) |

The blinks are dark between the flashes (the key is held by the indicator). The link key is the key of the slot the module reports (status byte 4), once the module has reported the selected link since this port last selected one (0x2d.1); after that it follows the slot the module reports, as on the stock. In the low-battery cutoff every LED is dark (`LED_HOLD_CUTOFF`).

The rhythms are checked against the stock image run in the same simulator (its frame buffer sampled every millisecond): pairing 80 ms, reconnecting 380 ms, the 3 s solid, the low-battery 250 ms; the battery display against the stock painter run directly, at every step's edge. Sleep gate for the board: in Bluetooth with the host's Caps Lock on, the Caps LED is lit; after the idle timeout (61 s connected) it goes dark; a key brings it back as the link reconnects. That the LEDs go dark is the visible sign that the MCU went to sleep.

`leddiag` lights one channel of one cell at a time, dimly (DUTY2 = phase + 128), nothing until the first key: Fn + → / Fn + ← = next / previous cell (Esc, Tab, Caps, Left Shift, Left Ctrl, Y, Enter, Up, Fn, →, side lights B0 and A0), Fn + ↑ = next colour (red → green → blue), Fn + PgDn = dim / the indicator level (0x0400), Fn + ↓ = types what should be lit, e.g. `esc red pwm24 p1.4 dim`. Dim is faint: look in a dark room. It is for checking on the board what the static analysis could only infer:

- Inferred, not checked on hardware: the pin map (PWM0n = P3.n, PWM1n = P2.n, PWM2n = P1.n, from the smk Air60 / Air75 and F75 ports), that the anodes are active high (on while the PWM pin is high and the column low), the 12 MHz PWM clock, and that the physical colours are the firmware's. (DUTY2 is written while the banks are stopped, as on the Air75, so no assumption about when the chip latches it is needed.) A wrong pin map or colour order only gives wrong colours at the same drive; the limits above hold whatever the map.
- Not known: whether columns 16 / 17 (P4.6 / P7.4) carry LEDs; this port never selects them.

### Backlight

`backlight.c` (`ansi`, `usjis`) is the stock V1 lighting, ported from its effect task 0x1108 and side-light task 0x5872 (the specification with pseudocode and test vectors is in the author's analysis notes, not published; the tables in `backlight_tables.h` are the stock image's bytes, generated from it). The effects write an 8-bit frame, `led_fb8`, in the stock fb8 layout; for the same settings, keys and milliseconds it equals the stock's fb8 (the simulator runs both side by side, `tests/test_f65_backlight.py`).

| Fn + \\ | Effect | Colour 0-6 / 7 | Keys |
| --- | --- | --- | --- |
| 0 | off | | |
| 1 | static, rings filling in from U | fixed / rainbow rings | |
| 2 | breathing | fixed / a rainbow across the columns | |
| 3 | rainbow cycle, all keys one colour | (none) | |
| 4 | reactive line: fronts run left and right from the key | fixed / random | yes |
| 5 | rain | fixed / random | |
| 7 | reactive ripple (a diamond from the key) | fixed / random | yes |
| 8 | twinkle | fixed / random | |
| 10 | serpentine sweep | fixed / random | |
| 11 | colour wave across the columns (default) | brightness wave / rainbow wave | |
| 12 | reactive: the key lights and fades after release | fixed / random | yes |
| 13 | automatic ripple from column 6 | fixed / random | |
| 15 | gradient cycle from a fixed pattern | (none) | |
| 16 | rainbow by rows | (none) | |
| 17 | moving rings from U | brightness rings / rainbow rings | |

- As the stock: brightness 0-4 (v × bri / 4; 0 = dark), speed 0-4 per effect from the stock's table of that effect (effect 1 has none), colours R G B Y M C W and 7 = rainbow / random (not for 3, 15-17), each setting kept per effect; the direction flag (host only on the stock) is always 0. Not ported: the per-key custom slot (18) and the Fn + G test mode; the power-on sweep.
- Side lights (row 0, pairs (i, 0) and (9 + i, 0), slot order B, R, G): off, rainbow wave, rainbow cycle, static colour, breathing; colour only for static and breathing; brightness 0-4 (Fn + , cycles, 0 = off); their speed follows the key speed (key 0-1 slow, 2-3 medium, 4 fast = the stock's side speeds 0, 1, 2).
- Fn + [ turns the backlight and side lights off and on (not on the stock; its Fn + \\ cycle still has effect 0 = off); while off the other lighting keys do nothing.
- Defaults: effect 11, colour 7, brightness 2 (of 4), speed 3 for every effect; side lights rainbow wave, brightness 2.
- DUTY2 = phase + 3 v + (58 v >> 8) for a value v (the stock's v × 4 + phase scaled into the 0x0400 cap: ≤ 0x03FB).
- Two layers make the table (`led.h`): the backlight frame and an override per cell for the indicators. `led_px` (assembly) writes a painted cell's three entries directly; `led_flush` reloads a column when an override, the blank state or a column's lit flag changes.
- The stock task runs once per main-loop pass on 1 ms counters; here a call takes the milliseconds since the last one the same way, and its work runs one unit per main-loop pass: at most two cells, the start of a call or a small step, with P4.7 looked at after every cell and before the key-change scan (the longest stretch without a look, over the whole image with effect 15 at full brightness, is ~80 µs in the simulator; the matrix scan, which stops the main loop for ~320 µs, looks at every column). No work on a pass where a scan waits for `matrix_task`. When a step is longer than its period the effect runs slower, as the stock does when its loop falls behind.
- On battery (a wireless position without USB power) the backlight and side lights go dark 30 s after the last key; the next key lights them again and still reaches the host. On low battery (the Fn blink) they are off, as on the stock; during the battery display (Fn + B) they are dark. The indicators work throughout; everything is dark in both sleeps.
- Saved in the settings record (after the older fields: a record of the older length still loads, its settings kept and the lighting at its defaults): on / off, the effect, brightness, speed and colour per effect, the side effect, colour and brightness. A change is written once, 3 s after the last change.

## Building, testing and flashing

```sh
meson setup build-release --buildtype=release --werror
meson compile -C build-release aula-f65-v1_usjis_smk.hex aula-f65-v1_ansi_smk.hex
sinowisp write -d aula-f75 --force build-release/aula-f65-v1_usjis_smk.hex
```

The simulator tests need the simulator built from this tree's `tools/ucsim` (model version 8: EUART0 with byte timing and priorities, the P0 / P4 pins, PWM4, key contacts, INT4 wake from power-down, USB interrupts on the flags a test raises for bus events, the longest watchdog-kick gap, the D+ pull-up logged, the PWM banks with the LED trace, the real-rate Timer2, the dual DPTR, an EP1 IN hold and a PLL lock hold, each flash operation logged with the cycles since the last watchdog kick, each new longest kick gap of 1.5 ms or more logged with where it began and ended, the real-rate Timer2's TF2 as a real flag and a Timer2 arm delay that moves the Timer2 grid against the code (`test_f65_indicators.py` sweeps it), a [PWMC]-only trace mode; `nix build .#ucsim-sh68f90`, or `SMK_UCSIM` pointing at a build) and a debug build (`meson setup build`), or `SMK_F65_FIRMWARE` / `SMK_F65_ANSI_FIRMWARE` pointing at other images:

```sh
python3 -m unittest discover -s tests -p test_f65.py         # board tests (both layouts)
python3 -m unittest discover -s tests -p test_f65_usjis.py   # US-JIS on the F65 matrix
python3 -m unittest discover -s tests -p test_f65_radio.py   # switch, radio link, reports, keys, battery, sleep, recovery
python3 -m unittest discover -s tests -p test_f65_fnrow.py   # the number row under Fn and Right Shift
python3 -m unittest discover -s tests -p test_f65_led.py     # LEDs (SMK_F65_LEDDIAG_FIRMWARE for leddiag)
python3 -m unittest discover -s tests -p test_f65_indicators.py   # the LEDs with the radio, sleep, the watchdog
python3 -m unittest discover -s tests -p test_f65_backlight.py    # the backlight against the stock, its keys, saving, the LED limits with effects
```

The radio tests drive the board model of `tests/f65_radio.py`: the switch, USB power, the charging pin, the module's ready line (its edges fall between two ticks) and its UART with byte timing, and a scripted module; every scenario also checks P0.2's lead and gap. With `SMK_F65_STOCK_IMAGE` (the official `AULA_F65_V1_FN_Ctrl_firmware.bin`) the stock image runs in the same simulator and board and is the oracle: the names, the switch entries, typing and the release repeat, the pairing holds, the supervisor and the acks, the battery frames, the sleep frames and timeouts, and the pins at power-down and the wake pulse must match it. The same image checks the keymap tables, and `SMK_F65_STOCK_DUMP` (a full readout; sinowisp on PATH for its offline `convert`) the chain through the F65's own bootloader.


`test_f65_led.py` reads the PWM model's trace (`tools/ucsim/sh68f90.cc`, `cl_sh68f90_pwm`). The model normally raises the scan tick every 30000 cycles; tests that need the board's scan rhythm (about 4 PWM periods between scans) switch it to Timer2's real rate (xram 0x1f52) and restore the real `delay_us`. With `SMK_F65_STOCK_IMAGE` it also checks the PWM start / stop register sequences and the phases against the stock image, and runs the stock image in the same simulator to compare what it lights with what the port lights for the same states (Caps Lock, charging, the Fn-held link). `SMK_F65_PAINTER_ORACLE` / `SMK_F65_LED_MAP` (the author's `painter_oracle.json` / `led_map.json`, not published) check the copies of their values the test carries.

`test_f65_indicators.py` runs the LED trace on the radio board: the link and pairing keys, the battery display and the low-battery warning (the stock image and its painter as the oracles, `SMK_F65_PAINTER_ORACLE` checked when given), Caps Lock from the module, the LEDs across both sleeps, the watchdog kick gap at the board's rhythm in every position, the LED limits and the column dwell while the module sends back to back, the call graph (no LED code in an interrupt but the Timer2 tick), and the review items (the stall backstop, the endpoint-drain and flash kicks, the bounded PLL wait).

`meson test -C <builddir>` runs every test file against that build directory's images and fails if one is missing (`SMK_TESTS_STRICT`), the stock image and the readout included: pass them with `-Df65_stock_image=<bin>` and `-Df65_stock_dump=<bin>`.

## Flashing

Unplug every other 258a:010c device first (many keyboards share the ID and sinowisp takes the last match) and set the switch to the middle. The way back is the unit's own readout taken before the first write (`sinowisp read -d aula-f75 -s full`), which keeps the settings pages; the vendor updater's image is the factory state. After writing, read the board back the same way and compare it with the image (the settings sector, 0xEC00-0xEFFF, differs once the firmware has saved settings, such as the switch position). The README gives the step-by-step procedure.
