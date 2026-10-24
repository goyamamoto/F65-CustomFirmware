# EPOMAKER x AULA F65 (V1, "Fn-Ctrl")

## Specs

- MCU: SinoWealth SH68F90 family ([SH68F90](../platforms/sh68f90.md)). Original USB ID 258a:010c, manufacturer "BY Tech", product "Gaming Keyboard", bcdDevice 0x0500
- This port runs on the V1 ("Fn-Ctrl" firmware, Fn + Left Ctrl swaps the F-row). The V2 ("Alt-Fn", product "AULA F65", VIA) uses the same 16 key columns and rows, does not use the V1's two LED-only columns (P4.6, P7.4) and has P0.3 as an output. The port has not been run on a V2
- Matrix: 5 rows × 16 columns, 67 keys. Rows P7.1-P7.3, P5.3, P5.4 (inputs, pulled up); columns P6.0-P6.7, P5.0-P5.2, P5.7, P4.0, P4.2, P4.3, P4.5 (outputs, active low). P7.0 is a sixth row line with no key; P4.6 and P7.4 are LED-only columns
- Backlight: an LED matrix sharing the columns, anodes on the 18 PWM pins P1.0-P1.5, P2.0-P2.5, P3.0-P3.5 (6 LED rows × RGB). See [LEDs](#leds)
- Connection switch (3 positions): P0.5 low = 2.4 GHz, P0.4 low = Bluetooth, both high = USB (middle)
- Radio: a module on EUART0 (TXD P5.5, RXD P5.6, ~260870 baud). P0.2 is the MCU's send request (low around each frame it sends, and a short wake pulse); P4.7 is low while the module sends. The module chip is not identified
- Power: P4.4 reads high with USB power; P7.6 is driven to its inverse. P7.7 (pulled up) reads low while charging. P4.1 and P7.5 are held low while running; P4.1 goes high before a sleep. With the switch in the middle, unplugging USB cuts the MCU's power, so replugging is a power-on reset. In the wireless positions the battery keeps the MCU powered

## Status

Checked on the board (macOS hosts):

- Keys, both layouts, the `usjis` IME keys, US-JIS, Fn + A / Fn + S kept across a replug
- USB, Bluetooth (three slots) and the 2.4 GHz dongle, pairing, the battery display, sleep and wake in the wireless positions
- The backlight effects, colours, brightness, speed, side lights and the status indicators
- Both ways into the bootloader: sinowisp's report from the running firmware, and Esc held at power-up
- VIA with `usjis-via`: the keymap read and changed from VIA, kept across a replug, back to the built-in keymap; a modified key (Shift+D on the Fn layer) held while Fn is released first leaves no Shift behind
- With `usjis-via`, a layer-tap key on Space (`LT(3, KC_SPC)`) and mouse keys on its layer, over USB, Bluetooth and 2.4 GHz
- The Bluetooth names per slot: "AULA F65-1" at power-up, also on a Mac that had paired under the earlier name, without pairing again, "AULA F65-2" paired after Fn + W, back to "AULA F65-1" with Fn + Q

Checked in the simulator only: remote wakeup (a key sets USBCON.WKUP once while the host has suspended the bus and enabled remote wakeup, `USB_WAKE_ON_KEY`), the middle-position sleep without a configured host, the `ansi-via` image, and what changed after the board check: a key let go while a layer-tap key is undecided, a held mouse key as activity, mouse button changes while the radio queue is full or the USB bus is suspended, and slot names retried when the module is busy.

## Layouts

Five layouts share the board code:

| Layout | What it is |
| --- | --- |
| `ansi` | US ANSI as printed |
| `usjis` | `ansi` plus US-JIS (`USJIS`), the IME mod-taps (`TAP_HOLD`), Caps Lock and Left Ctrl swapped, Fn and Right Ctrl swapped |
| `ansi-via` | `ansi` with the keymap editable from VIA (see [VIA](#via)) |
| `usjis-via` | `usjis` with the keymap editable from VIA |
| `leddiag` | `ansi` with an LED diagnostic instead of the indicators (`LED_DIAG`, see [LEDs](#leds)) |

## Keys

- Win layer (default) as printed: `Ctrl Win Alt Space Fn Ctrl ← ↓ →` on the bottom row
- Mac layer: `Ctrl Option Command Space Fn Ctrl`. Fn + A selects Win and Fn + S selects Mac (saved; default Win); the key for the mode already in force changes nothing and writes no flash
- Fn + Q / W / E (Bluetooth position only): a short press selects slot 1 / 2 / 3 (the frame goes out on release); held 3 s it pairs that slot
- Fn + R (2.4 GHz position only): held 3 s it pairs the dongle; a short press does nothing. Fn + T sends nothing
- In the other positions these keys send nothing. The key of the link blinks while it pairs or reconnects; while Fn is held only the active link's key lights: Y white (USB), R green (2.4 GHz), the slot's key blue (Bluetooth)
- Fn + B held: the battery level on 1 … 0 (wireless positions on battery); sends nothing
- Lighting (`ansi`, `usjis` and the VIA layouts): Fn + \\ next effect, Fn + ] next colour, Fn + [ backlight and side lights on / off, Fn + ↑ / ↓ brightness, Fn + → / ← speed faster / slower (the side lights follow), Fn + / next side effect, Fn + . next side colour, Fn + , next side brightness. None sends anything; under Fn the arrows are these keys
- Number row under Fn, with Right Shift as a second Fn:

  | | Fn + 1 … = | Fn + Right Shift + 1 … = |
  |---|---|---|
  | Win | F1 … F12 | Brightness −, Brightness +, Alt+Tab, Alt+Esc, WWW Home, Mail, Previous, Play/Pause, Next, Mute, Volume −, Volume + |
  | Mac | Apple's order: Brightness −, Brightness +, Mission Control, Launchpad, (nothing), (nothing), Previous, Play/Pause, Next, Mute, Volume −, Volume + | F1 … F12 |

  Right Shift under Fn picks the other set and is not Shift while Fn is held. Pressed after Fn it does not reach the host while Fn is held. Pressed before Fn it is Shift until Fn goes down (the host sees that Shift press and release), then taken out. When Fn goes up with Right Shift still held, Shift comes back just before the next ordinary key, if Right Shift is still held. US-JIS sees exactly the Shift the report carries. A Shift or Alt added for a key of its own (the `~`, Alt+Tab, Alt+Esc) is added only if the user is not holding it and taken away only if added. Right Shift alone, or with ordinary keys, is Shift; Left Shift under Fn stays Shift. The consumer report descriptor goes up to 0x2FF (`CONSUMER_USAGE_MAX`) for Mission Control 0x29F and Launchpad 0x2A0
- Fn layer otherwise: Esc = `` ` `` (Shift or Right Shift gives `~`), Del = Insert, PgUp = `` ` ``, End = Home, U / I / O = PrtSc / ScrLk / Pause, Tab = US-JIS on / off (`usjis`; plain Tab in `ansi`), B = battery display, M = nothing, the lighting keys above. The other positions are transparent
- Settings reset: hold Fn + Backspace, then press Fn + V. US-JIS off, the Win layer and the lighting defaults (the saved position and slot stay); on the VIA layouts also the built-in keymap. Under Fn, Backspace and V send nothing
- `usjis` only: the keys on both sides of Space are mod-taps for the Japanese IME. Win: left = Alt held / Muhenkan (INT5) tapped, right = Right Ctrl held / Henkan (INT4) tapped. Mac: left = Command / Eisu (LANG2), right = Right Command / Kana (LANG1). Fn and the printed Right Ctrl swap places, so the bottom row reads `Caps Win/Opt Alt*/Cmd* Space Ctrl*/Cmd* Fn ← ↓ →`
- No Fn chord waits for a timeout: Fn is a plain momentary layer. A key goes up with the keycode it went down with, whatever Fn did meanwhile (`LATCH_KEYCODES`)
- Keys are debounced across scans (`MATRIX_DEBOUNCE_SCANS` = 4): a key changes state only once its new state has been read in 4 consecutive scans (~5.7 ms)
- Everything above works the same over USB and over the radio
- USB: 258a:010c (the original ID, so `sinowisp -d aula-f75` finds the running board), manufacturer "SMK", product "AULA F65 (SMK)", 6KRO boot-protocol report (`NKRO_DEFAULT_OFF`)
- Feature reports on the interface with the ISP report (`USB_FEATURE_REPORT_STRICT`): ID 5 with `05 75` enters the ISP bootloader (what sinowisp sends); ID 5 with anything else is acknowledged and ignored; any other ID is stalled

US-JIS works as on the [NuPhy Air75](nuphy-air75.md#us-jis), in the Win layer only, over USB and the radio alike.

## Connection switch

- Read every 10 ms; a position counts after 10 samples in a row (~100 ms). The middle position wins over 2.4 GHz, which wins over Bluetooth
- Into 2.4 GHz or Bluetooth: every key is released on the radio side and USB is switched off: ENUSB, the D+ pull-up (SW1CON) and the USB interrupt, so the host sees the keyboard unplugged at once and nothing on the bus brings USB back (`usb_init` also refuses outside the wired position, `USB_INIT_GATE`). EUART0's interrupt goes on and the module is told the link
- Into the middle position: every key is released, the module is told to stop, EUART0's interrupt goes off and USB starts over with the pull-up (an attach for the host), about 0.1 s after the switch settles
- Every transport change forgets a link-select or pairing request that has not gone out yet and a pairing hold in progress
- The position and the Bluetooth slot are saved with the settings. At power-up the saved position holds until the switch has been read ten times; USB is enabled at boot only when the saved position is the middle one (`BOARD_USB_BOOT_GATE`)
- Bluetooth names, one pair per slot: "AULA F65-1" (BT5.0, profile 0) and "AULA F65-1 3.0" (BT3.0, profile 1) for slot 1, and so on (`RF_NAME_SLOT_BASE`). The saved slot's names go out on every boot, a slot's again on the press of its key (Fn + Q / W / E) when the module has another slot's, so a hold that pairs follows them by 3 s and a select by at least 32 ms (`rf_name_slot`). If the module is too busy to take them on the press, they go out once it is free while the key is held, and the pairing request comes 3 s after them; a release tries once more and then selects the slot either way A Mac paired under the earlier name showed the new one after the next power-up; other hosts may keep a name until they pair again

## Radio link

Frames to the module are `01 <cmd> … <0x55 − sum of the others>`.

- EUART0 at interrupt priority 3 (the ES0 bits of IPH1 / IPL1; `RF_EUART0`). A byte arrives every 38 µs and the matrix scan runs ~320 µs in the Timer2 interrupt, so at the default priority the scan would lose bytes. The interrupt clears the FE / RXOV / TXCOL flags on every entry
- A frame from the module is what arrives while P4.7 is low; it is taken when P4.7 is high again. P4.7 is looked at from the matrix scan at every column, on the 1 ms tick (PWM4 as a timer, `PWM4_MS_TICK`), from the LED subframes and on every main-loop pass; the longest stretch between two looks is ~80 µs in the simulator with the heaviest backlight effect. Three receive banks: up to two frames wait for the parser while the third receives, so two frames 100 µs apart are both parsed. Only the first frame of a P4.7 envelope counts and its first byte says what it is
- P0.2 (the send request) goes low before the first start bit and stays high at least 1 ms between two frames
- Status `02 06 00 LED slot state battL battH x ck` (answer to a status request every 200 ms): when the module reports another slot or state 0, the link is selected again; state 3 = connected. Byte 3 is the host's LED state (Caps Lock), stored like the USB LED report. Every sixth status frame updates the battery level
- After a link-select the supervisor waits 600 ms before selecting again, so a pairing request is not cut short. A link-select that cannot go out waits, and a waiting pairing request is not replaced by a plain select
- Announce and container frames from the module are acknowledged; no container sub-command is acted on (factory reset included)
- A send waits for the frame in flight, the 1 ms gap and P4.7 high (the acks only for the first two). A stuck send (no UART interrupt for three status requests) is abandoned and P0.2 released

## Reports over the radio

- Keyboard: the long frame, `01 02 mods k1 … k5 00 bitmap[16] 00 00 00 00 ck`. Held keys sit in press order in the five slots (a release closes the gap); a sixth key goes into the bitmap (bit n = usage n) until it is released. The report is 6KRO, so a seventh key is not sent
- Consumer, system and (VIA layouts) mouse: the short frame `01 03 usage_lo usage_hi sys btn xL xH yL yH wheel pan ck`; x and y as 16 bits, little-endian, within ±127; all mouse bytes 0 in the other layouts
- After every key is up: three times an ErrorRollOver frame (key 1 = 0x01) and an empty one, so a lost release cannot leave a key stuck
- Bluetooth: one report every 8 ms (125 Hz); 2.4 GHz: one long frame per 4 ms at most (the 2 ms pump and the 1 ms gap after a 1.2 ms frame). After a wake the first 8 pump passes send nothing
- A report goes out per key change, so keys pressed within one scan go out as separate frames

## Battery

- The module reports the cell as a raw value; percent = (raw − 715) × 10 / 22, capped at 100. The first value after a link-select, an announce or USB power going away is shown as it is; on battery the shown value falls 1 % per 20/10/5/2 passes (by the distance in tens), jumps down when 30 or more above the target and up when the target is 30 or more higher; with USB power it rises the same way, jumps up at 20 or more, drops at once when 20 or more above, shows 99 instead of 100 while charging (P7.7 low for 0.2 s), and goes from 95-99 to 100 once charging has stopped (P7.7 high for 2 s). Each change goes to the module as `01 0D pp 00 00 ck`
- USB power is P4.4 high for 3 samples (30 ms), gone after 3 low samples
- Low-battery cutoff (wireless positions): raw below 737 for 2 s stops new key reports, turns every LED off and puts the board to sleep, again every 2 s while it lasts. USB power, or raw above 912 for 1 s, ends it
- Low-battery warning (wireless positions, on battery, not during the cutoff): raw below 781 for 24 samples (0.24 s) sets it; it stays until raw is above 912 for 1 s or USB power comes. The Fn key blinks red while it holds
- Battery display: Fn + B held in a wireless position on battery lights keys 1 … 0, one per step the raw value reaches (737, 758, 781, 802, 824, 846, 868, 890, 912, 934), green from 802 up, red below

## Sleep

- Wireless positions, after this long without a key: connected 61 s; pairing 61 s; reconnecting 21 s in Bluetooth, 11 s in 2.4 GHz; never before the module has sent a status frame. Container frames and status changes count as activity, and in the VIA layouts a held mouse key while the link is connected (not while reconnecting or pairing)
- Before sleeping every LED goes off (PWM stopped, the anode latches 0, every column released), the module is told the board sleeps, EUART0 goes off, the pins are parked (key columns and both switch pins driven low, rows pulled-up inputs, P4.1 high, P7.6 low), INT4 is armed for the keys, and the clocks and the regulator go off
- A key wakes it; the LEDs come back once the PWM set-up has run again (`led_wake`). USB stays off after a wireless wake. EUART0 comes back, P0.2 is pulsed low for 1-2 ms to wake the module, and reports hold for 8 pump passes. The next status frame re-selects the link if it has to
- Asleep in a wireless position, moving the switch does nothing until a key wakes the board; then the switch is read and, in the middle, USB comes back
- Middle position with USB power but no host that has configured the board (a charger, or a host that stopped after a bus reset): after more than 10 s without a USB interrupt or a key it turns every LED off and sleeps (a key or a USB event wakes it), and USB and the LEDs start over after the wake. A configured board stays awake while the host suspends the bus (remote wakeup)

## Recovery

- Esc held at power-up (`BOOT_ESCAPE`): the firmware jumps to the ISP bootloader right after `clock_init()`, before USB, the settings and the radio; `sinowisp` then finds the bootloader (0603:1020). All 16 samples over about 8 ms must read pressed. Only Esc is checked; at runtime Fn + Esc is the grave key
- Power-up means the middle position: there, unplugging USB removes the MCU's power. In a wireless position the battery keeps it running, so replugging is not a reset. From any state: move the switch to the middle, unplug, hold Esc, plug in
- With the switch in the middle and the firmware running, sinowisp's report 5 enters the bootloader. In a wireless position USB is off, so sinowisp cannot reach the board; a board asleep in a wireless position needs a key first
- Wait about 3 s after unplugging before plugging in again when the replug is meant as a power-on reset
- The watchdog is kicked by the main loop only: `delay_us` does not kick it (`DELAY_NO_WATCHDOG_KICK`); `delay_ms`, the bounded waits of the radio, the USB endpoint drain, the LED rendering and each flash erase and program (`FLASH_OP_WATCHDOG_KICK`) kick as they go, so a main loop that hangs while the interrupts run is not kept alive. The PLL lock wait at power-up gives up after 20000 polls and switches anyway (`CLOCK_PLL_WAIT_BOUND`). The longest stretch without a kick in the simulator at the board's scan rhythm is about 2 ms; the tests hold it under 3 ms
- If neither the running firmware's report 5 nor Esc at power-up works, only the ICP programming interface is left; the F65's ICP pads are not known

## Stack

`--stack-auto`, IDATA 0x25-0xFF (219 bytes). The deepest the simulator measures, with the LEDs lit, USB requests, the radio and VIA commands, is about 90 bytes.

## VIA

The `ansi-via` and `usjis-via` images take VIA's raw HID commands, so [VIA](https://usevia.app) can change the keymap over USB (the middle position of the switch; in the wireless positions USB is off). The keymap VIA sets is used over Bluetooth and 2.4 GHz as well.

- VIA needs the definition `src/keyboards/aula-f65-v1/via.json` (in VIA: Settings, Show Design tab, then Design, Load). The V2's own VIA definition has the same USB ID but another matrix and other custom keys; it does not fit these images
- The four layers are the layout's: 0 Win base, 1 Mac base, 2 Win Fn, 3 Mac Fn. Fn + A / Fn + S choose the base layer; the Fn key (`MO`) of the base layer chooses the Fn layer of the same OS
- Win / Mac is only the base layer; what layers 2 and 3 hold, and which of them a base layer's layer key reaches, is the keymap's. To the board both are Fn layers, whichever key holds them: while one is in force the Fn-held indicators show (the link keys, A / S, Tab), and Backspace and V, where the layer gives those keys, are the settings-reset chord and send nothing. A held Right Shift is taken back from the host while any layer key (MO, or a layer-tap key held as its layer) is down. `OS_WIN` and `OS_MAC` switch the mode on any layer and position (the indicator stays on A and S); keep one of each where the other base layer reaches it. The Fn-row keys give F-keys or media keys by the Win / Mac mode, on whichever layer they are
- Keycodes the firmware acts on: the basic keys and modifiers, modified keys (`LCTL(KC_C)` and the like), mod-taps with one modifier (`MT`, `LCTL_T` and the like), `MO(n)` and layer-taps `LT(n, kc)` on a base layer, system and consumer keys, the mouse keys, and the board's own keys, which the definition lists as custom keys (US-JIS, Win / Mac, the link keys, the battery display, the Fn-row keys, the lighting keys) in the order of `custom_keycodes` in `kbdef.h`. Other keycodes VIA offers (toggles, one-shot keys, macros) send nothing. `MO` or `LT` of a layer past 3 gives no keys under it
- A layer-tap key is undecided while it is down, and the other keys wait meanwhile. It is its layer after the tapping term (the mod-taps' term: 285 matrix scans, about 0.4 s), or as soon as a key that went down after it comes up while it is still held; released first, it is a tap of its key. The keys that waited then go out in their order, under the layer or after the tap, so a fast roll from the layer-tap key into the next letter types both. A key that went down before the layer-tap key and comes up meanwhile is released at once if it sends a key of its own (a basic key, a system or consumer key); every other key waits for the decision (modifiers, modified keys, mod-taps, layer keys, mouse keys and the board's own keys), since it can change what the waiting keys send. Eight key events wait at most: a ninth makes it the layer. A layer-tap key among the waiting keys is undecided in turn, so two layer-tap keys rolled are two taps; its tapping term counts from when it comes out of the queue
- Mouse keys (`src/smk/mousekey.c`), as QMK's accelerated mouse keys with its defaults except the interval (16 ms here, 20 in QMK): a movement key moves 8 at once, again after 10 ms, then every 16 ms, the step growing to 80 over 30 steps (about 0.5 s); two directions move diagonally at the same speed. The wheel keys step 1 at once, again after 10 ms, then every 80 ms, up to 8. Buttons 1-8 go down and up with their keys; `KC_MS_ACCEL0` / `1` / `2` held fix the speed at a quarter, a half or the whole of the maximum. Over USB they go out as a mouse report (report ID 4 on interface 1: buttons, x, y, wheel, pan, relative); over Bluetooth and 2.4 GHz in the radio module's short report frame, which carries the buttons, x and y, the wheel and the pan, up to 127 a frame; while the module is busy, the mouse frames already queued (up to four: the last two of the six slots stay for key reports, so a key tapped meanwhile still goes out) go out first, then at most two frames' worth more; the rest is dropped. Button changes are kept apart from the movement (up to four), so a click made meanwhile still goes out as a press and a release. Over USB nothing is sent while the host has suspended the bus; a button change made meanwhile goes out once the bus runs again
- Like an MO key, a layer-tap key is taken from the base layer (one placed on layer 2 or 3 does nothing) even while another layer is on, so what the Fn layer has at its position is out of reach (with one on Backspace, or on V with a tap key other than V, the settings-reset chord too; VIA's keymap reset, Esc held at power-up and sinowisp still work)
- US-JIS translates the keys a basic keycode sends, as on `usjis`; a modified key goes out as it is (`LSFT(KC_2)` reaches the host as Shift + 2)
- The definition has no lighting menu and the images report no macros: the lighting stays on its Fn keys
- VIA's key tester reads the debounced matrix; the uptime VIA reads is in milliseconds (the 1 ms tick)
- A modified key pressed when the report already holds six keys sends neither its key nor its modifiers
- Two keys that send the same basic key (two `KC_C`, or `KC_C` and `LCTL(KC_C)`) share it in the report: releasing one releases C for both. Two modified keys with the same basic key keep it until the last one is up
- The layer key ends its layer when the key that went down comes up, even if VIA changed the keymap or Fn + A / Fn + S the base layer meanwhile. With two layer keys (MO or layer-taps held) the last one pressed holds the layer, and its release ends the layer even while the other is still down

The keymap is kept in flash, in the two sectors below the settings (0xE800-0xEBFF; the code ends below 0xE800):

- The changes are written once, about 0.5 s (400 matrix scans) after the last one, so a whole layout that VIA loads in many commands is one write: both sectors erased, the record programmed, its two magic bytes last. A replug within that time loses the last changes. While the record is written the LEDs are dark and a key pressed and released within that time is not seen
- At power-up the record is used if its magic, its size (4 layers, 5 × 16), a sum of the built-in keymap and the keymap's own sum match; otherwise the built-in keymap is used. A record cut short by a power loss lacks its magic, and a record written by the other layout's image has another built-in sum: both give the built-in keymap
- Back to the built-in keymap: VIA's keymap reset, the settings reset (Fn + Backspace held, then Fn + V, on the keys the keymap has there), or writing any image (sinowisp erases the flash). The built-in keymap is kept as no record: the two sectors are erased
- XRAM: the keymap's working copy takes 640 bytes; the release images use 3910 (`usjis-via`) / 3854 (`ansi-via`) of 4096 bytes

USB with VIA (`VIA_ENABLE`, `src/smk/usb.c`):

- Interface 0 is VIA's raw HID: usage page 0xFF60, usage 0x61, 32-byte input and output reports without a report ID, on EP2 IN and EP2 OUT. A report is answered from the main loop (`usb_task`); EP2 OUT is armed again only after the answer is out
- Interface 1 is the keyboard (boot subclass) with the system, consumer, mouse, ISP and console reports, each with its report ID, on EP1 IN (16-byte packets). The keyboard report has report ID 3; under the boot protocol (SET_PROTOCOL 0) it goes out as the bare 8 bytes and the system, consumer, mouse and console reports are not sent. The Caps Lock LED report is taken with or without its ID. There is no NKRO report (22 bytes do not fit EP1)
- `sinowisp -d aula-f75` finds report 5 on interface 1. On macOS, sinowisp's terminal needs Input Monitoring to open that interface (it carries the keyboard). Esc held at power-up does not depend on it

## LEDs

The LED timing is smk's, as on the NuPhy Air75, which drives its LEDs from the same MCU's PWM on shared columns:

- PWM banks 0-2 (PWM00-05, PWM10-15, PWM20-25) at Fsys/2 = 12 MHz, period 1200 counts (100 µs), drive the anodes of 6 LED rows × R/G/B; the key columns are the cathodes. Pin map: PWM0n = P3.n, PWM1n = P2.n, PWM2n = P1.n; an anode is lit while its PWM pin is high and its column low
- The Timer2 tick runs a matrix scan, then five LED subframes of 200 µs (`LED_SUBFRAMES_PER_SCAN`, `SYSTICK_LED_SUBFRAME_RELOAD`), then the next scan: a scan every ~1.4 ms, the 20 slots in ~5.9 ms. The scan stops the PWM and releases every column first (`indicators_pwm_disable`), so no key row is read with the PWM on; a subframe that follows a lit subframe stops it the same way first (`led_pwm_off`). A subframe (`indicators_update_step`) moves to the next of 20 slots; if the slot's column has something lit, it loads that column's 18 DUTY2 values, selects the column and then starts the banks. The column stays lit until the next Timer2 interrupt
- The dwell is bounded by the next Timer2 interrupt (level 0, re-armed for 200 µs), not by a hardware timer that stops the PWM. If the scans stop while the main loop runs, `led_stall_check` holds every LED dark once no scan has come for 5 ms (`LED_HOLD_STALL`)
- There is no LED interrupt and no interrupts-off in the LED path apart from the one-time set-up at start. No LED code is reachable from any interrupt handler but the Timer2 tick (a test walks the linked image's call graph)
- Slot n = column n for n < 16; slots 16-19 select nothing. The LED-only columns P4.6 / P7.4 are never driven low
- Cells: column, LED row (0 = side lights, 1-5 = key rows = smk row + 1), channel (keys: 0 red, 1 green, 2 blue; side lights: 0 blue, 1 red, 2 green). An indicator's "on" is DUTY2 = 0x0400, "off" the channel's phase; the backlight's levels are below 0x0400
- Each LED is lit one subframe (≤ 3 PWM periods) per 20 subframes (~5.9 ms), about 3.4 % of the time, ~170 Hz

Hard limits, each enforced in code and checked in `tests/test_f65_led.py` against a trace of the simulated PWM and pins:

| Limit | Enforced by |
| --- | --- |
| DUTY1 ≤ DUTY2 ≤ 0x0400 | the backlight's DUTY2 = phase + 3 v + (58 v >> 8) ≤ 0x03FB for any 8-bit value v (`led_px`, `led_flush_row`); indicators 0x0400; `led_set_duty` (leddiag) clamps; `led_audit` (one column every ~10 ms) turns off any entry found out of range |
| At most one column low while the PWM runs, never switched while it runs | the subframe selects the column with the banks stopped, then starts them; the scan and every stop release all columns after stopping the banks |
| PWM off, every column released, for key-row reads, flash writes, the report-5 ISP jump, USB suspend (wired position), both sleeps, stalled scans, the low-battery cutoff | `led_hold` reasons: `LED_HOLD_SCAN`, `_FLASH`, `_ISP` (`USB_ISP_PREPARE`), `_SUSPEND`, `_SLEEP` (released by `led_wake`), `_STALL`, `_CUTOFF`. The boot escape runs before `led_init` |
| Nothing left driving the anodes after a start without a reset | `STARTUP_LED_OFF` in `startup.c`, the first code to run: interrupts off, PWM00-25 CON = 0, the PWM0 interrupt off, P1-P3 inputs |
| P1-P3 latches stay 0 | written 0 at init and on every stop, never set |
| A column is never selected longer than one slot | the next Timer2 interrupt stops the banks and releases it; `led_stall_check` if the scans stop |
| P4.6 / P7.4 never driven low | no slot selects them; every stop drives them high |
| An LED is lit at most one subframe in 20 | `LED_SLOTS` = 20 (static assert) |

Indicators (`indicators.c`), over the backlight (a cell an indicator holds shows the indicator, lit or dark; the others the backlight). The timers run on a 10 ms tick from the 1 ms PWM4 counter; the render works once per 1 ms tick:

| Key | Colour | When |
| --- | --- | --- |
| the key that sends Caps Lock | white | the host's Caps Lock LED: the USB LED report, or the module's status frame over the radio, there only while the link is connected. `ansi`: Caps; `usjis`: the Left Ctrl position |
| Fn | red | charging: USB power and P7.7 low 20 samples (cleared after 200 high) |
| Fn | red, blinking (250 ms) | low battery (see [Battery](#battery)); not while the battery display shows |
| Y | white | Fn held, the middle position (USB); the other link keys are dark |
| R | green | Fn held, 2.4 GHz connected |
| Q / W / E | blue | Fn held, Bluetooth slot 1 / 2 / 3 connected (only the active slot's key) |
| R (2.4 GHz, cyan), Q / W / E (Bluetooth, blue) | blinking, toggled every 80 ms | pairing, with or without Fn |
| same | blinking, toggled every 380 ms | reconnecting |
| same | solid for 3 s | from the moment the link is connected, then off (green / blue while Fn is held) |
| Tab | green = US-JIS on, red = off | Fn held, and ~1 s after a change (`usjis` only) |
| A / S | white | Fn held: the Win / Mac layer in force |
| 1 … 0 | green (raw ≥ 802) or red | Fn + B held (see [Battery](#battery)); every other LED is dark meanwhile |

The blinks are dark between the flashes. The link key follows the slot the module reports, once the module has reported the selected link. In the low-battery cutoff every LED is dark (`LED_HOLD_CUTOFF`).

`leddiag` lights one channel of one cell at a time, dimly (DUTY2 = phase + 128), nothing until the first key: Fn + → / Fn + ← = next / previous cell, Fn + ↑ = next colour (red → green → blue), Fn + PgDn = dim / the indicator level, Fn + ↓ = types what should be lit, e.g. `esc red pwm24 p1.4 dim`.

### Backlight

`backlight.c` reproduces the F65 V1's lighting effects; `backlight_tables.h` holds the original firmware's tables. The effects write an 8-bit frame, `led_fb8`; `tests/test_f65_backlight.py` compares it frame by frame with the original firmware run in the simulator, when that image is given (`SMK_F65_STOCK_IMAGE`).

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

- Brightness 0-4 (0 = dark), speed 0-4 per effect (effect 1 has none), colours R G B Y M C W and 7 = rainbow / random (not for 3, 15-17), each setting kept per effect
- Side lights (LED row 0): off, rainbow wave, rainbow cycle, static colour, breathing; colour only for static and breathing; brightness 0-4 (Fn + , cycles, 0 = off); their speed follows the key speed
- Fn + [ turns the backlight and side lights off and on; while off the other lighting keys do nothing
- Defaults: effect 11, colour 7, brightness 2 (of 4), speed 3 for every effect; side lights rainbow wave, brightness 2
- DUTY2 = phase + 3 v + (58 v >> 8) for a value v (≤ 0x03FB)
- Two layers make the duty table (`led.h`): the backlight frame and an override per cell for the indicators
- A call takes the milliseconds since the last one, and its work runs one unit per main-loop pass (at most two cells, the start of a call or a small step), with P4.7 looked at after every cell. When a step is longer than its period the effect runs slower
- On battery (a wireless position without USB power) the backlight and side lights go dark 30 s after the last key; the next key lights them again and still reaches the host. On low battery they are off; during the battery display they are dark. Everything is dark in both sleeps
- Saved in the settings record: on / off, the effect, brightness, speed and colour per effect, the side effect, colour and brightness. A change is written once, 3 s after the last change; the backlight blinks once at that moment (the flash write)

## Differences from the original firmware

- The link keys: R = 2.4 GHz, Q / W / E = Bluetooth 1-3 (the original: Q and E / R / T); while Fn is held R is green and the Bluetooth slot's key blue
- The lighting keys: colour Fn + ], side effect Fn + /, side colour Fn + ., side brightness Fn + ,; Fn + [ turns all lighting off. The side speed follows the key speed
- Not included: the vendor driver's protocol (no configuration from the vendor's software, no per-key custom lighting), the Fn + G test mode, the power-on light sweep, and the white Y shown for 3 s after switching to USB
- The backlight is about two thirds as bright as the original at the maximum; the default brightness is 2 of 4
- On battery the backlight goes dark 30 s after the last key; the connected sleep timeout is 61 s
- USB in the wireless positions: the D+ pull-up and the USB interrupt are off too, so the host sees the keyboard unplugged
- In the low-battery cutoff every LED is dark
- The VIA layouts: the keymap is edited from VIA with this repository's definition (`via.json`)

## Building, testing and flashing

```sh
meson setup build-release --buildtype=release --werror
meson compile -C build-release aula-f65-v1_usjis_smk.hex aula-f65-v1_ansi_smk.hex aula-f65-v1_usjis-via_smk.hex aula-f65-v1_ansi-via_smk.hex
sinowisp write -d aula-f75 --force build-release/aula-f65-v1_usjis_smk.hex
```

The simulator tests need the simulator built from this tree's `tools/ucsim` (`nix build .#ucsim-sh68f90`, the macOS toolchain script, or `SMK_UCSIM` pointing at a build) and a debug build (`meson setup build`), or `SMK_F65_FIRMWARE` / `SMK_F65_ANSI_FIRMWARE` pointing at other images:

```sh
python3 -m unittest discover -s tests -p test_f65.py              # board tests (both layouts)
python3 -m unittest discover -s tests -p test_f65_usjis.py        # US-JIS on the F65 matrix
python3 -m unittest discover -s tests -p test_f65_radio.py        # switch, radio link, reports, keys, battery, sleep, recovery
python3 -m unittest discover -s tests -p test_f65_fnrow.py        # the number row under Fn and Right Shift
python3 -m unittest discover -s tests -p test_f65_led.py          # LEDs (SMK_F65_LEDDIAG_FIRMWARE for leddiag)
python3 -m unittest discover -s tests -p test_f65_indicators.py   # the LEDs with the radio, sleep, the watchdog
python3 -m unittest discover -s tests -p test_f65_backlight.py    # the backlight, its keys, saving, the LED limits with effects
python3 -m unittest discover -s tests -p test_f65_via.py          # the VIA images: USB, VIA's commands, the keymap in flash, via.json
```

`test_f65_via.py` takes `SMK_F65_VIA_FIRMWARE` (`usjis-via`) and `SMK_F65_ANSI_VIA_FIRMWARE` (`ansi-via`). The other F65 test files run on the VIA images too when `SMK_F65_FIRMWARE` / `SMK_F65_ANSI_FIRMWARE` point at them.

Some checks compare with the original firmware run in the same simulator and board model. They need the image from the vendor's updater (`SMK_F65_STOCK_IMAGE`, not distributed here) and, for the bootloader chain, a full readout of a board (`SMK_F65_STOCK_DUMP`); without them those checks are skipped. `meson test -C <builddir>` runs every test file against that build directory's images and fails if one is missing (`SMK_TESTS_STRICT`); pass the original image and the readout with `-Df65_stock_image=<bin>` and `-Df65_stock_dump=<bin>`.

## Flashing

Unplug every other 258a:010c device first (many keyboards share the ID and sinowisp takes the last match) and set the switch to the middle. The way back is the board's own readout taken before the first write (`sinowisp read -d aula-f75 -s full`), which keeps its settings. After writing, a readout compared with the image differs only in the settings sector (0xEC00-0xEDFF) and, on the VIA layouts, the keymap sectors (0xE800-0xEBFF) once the firmware has saved something. The README gives the step-by-step procedure.
