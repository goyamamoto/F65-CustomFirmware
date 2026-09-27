# NuPhy Air75 (v1)

## Specs

- MCU: BYK916 ([SH68F90A](../platforms/sh68f90.md)), USB 05ac:024f
- Matrix: 6 rows × 16 columns. The columns are the nuphy-air60's; rows R0-R3 are P7.0-P7.3 and R4-R5 are P5.3/P5.4, so the Air60's rows R0-R4 are the Air75's R1-R5 and the F-row on P7.0 is new
- Backlight: per-key RGB driven straight from the MCU, like the Air60. 16 PWM columns plus 7 RGB rows: the six key rows (R0 on P4.1/P6.0/P4.0 is new) and the Air60's underglow row on P1.1-P1.3, which drives the Air75's side lights. The stock firmware also runs a 17th column on PWM24 (P1.4) but never lights it
- Switches: OS mode on P5.6 (high in the Mac position) and connection mode on P5.5, the same pins as the Air60
- Wireless: the same bit-banged link as the Air60 (CS P7.4, SCK P4.7, MOSI P0.7, MISO P0.6, MOT P0.5, ACK P4.2) with the same frames and commands. The chip marking has not been checked
- Power pins the Air60 does not have: P7.5 reads high while USB power is present, P7.7 (pulled up) reads low while charging, and the stock firmware drives P7.6 to the inverse of P7.5. What P7.6 does is not known; this port mirrors it the same way

The pin map comes from static analysis of the stock firmware, not from the PCB.

## SMK Supported Features

- [x] Key Scan (every key checked on an Air75 with a Mac host, and in the simulator)
- [x] RGB Matrix (every key and the side lights, checked on an Air75; settings persist across power cycles)
- [x] Wireless: Bluetooth (BLE) and the 2.4G dongle both work on an Air75 with a Mac host; over BLE it pairs and types, the battery level shows, and the Apple fn byte works. Bluetooth 1 is the default link (after a flash or a factory reset)
- [x] Sleep (the Air60 parking sequence plus the stock Air75 extras): on Bluetooth after about 5 minutes idle, waking on a key and reconnecting; over USB when the host sleeps, resuming with it. Checked on an Air75

## Boot escape

Hold Esc while the board powers up (plug in USB, or move the power switch) and the firmware jumps to the ISP bootloader before USB or anything else starts; `sinowisp` then finds the bootloader directly. It runs right after `clock_init()` because the bootloader's 0xFF00 entry does not set the clock. All 16 samples over about 8 ms must read pressed, so a bounce does not trigger it. Only the C0/R0 position (Esc) is checked.

This exists because the SH68F90A bootloader has no power-on key check of its own: it runs the firmware whenever the LJMP marker at 0xEFFB is present, so a firmware that breaks USB would otherwise need the hardware programming interface.

## Layouts

Two layouts share the board code: `ansi` is plain US ANSI; `usjis` adds US-JIS (`USJIS`), the IME mod-taps beside Space (`TAP_HOLD`) and swaps Caps Lock and Left Ctrl. The layout-level `defines` in `src/keyboards/nuphy-air75/meson.build` switch the features on; the Apple fn byte, the boot escape and the Bluetooth names are keyboard-level and in both.

## Key map

- Mac layer (OS switch in the Mac position): laid out like an Apple keyboard. The F-row sends F1-F4 and F7-F12, and Fn is the Apple fn key (`APPLE_FN`), so macOS decides what they do: media functions by default, F-keys with Fn, or the other way round with "Use F1, F2, etc. keys as standard function keys". F5/F6 dim and brighten the backlight, and Fn gives F5/F6, as in the stock firmware
- Win layer: the F-row sends F1-F12; Fn gives the media functions. The fn byte is never set
- The two keys between F12 and Del send PrtSc and Insert (macOS shows them as F13 and Help; the stock firmware has screenshot and assistant shortcuts there)
- Caps Lock and Left Ctrl are swapped on both base layers (Left Ctrl sits next to A)
- The keys beside Space are mod-taps for the Japanese IME (`TAP_HOLD`): held, they are Command (Mac) or Alt (Win); tapped, they send LANG2/LANG1 (Eisu/Kana) on the Mac layer and INT5/INT4 (Muhenkan/Henkan) on the Win layer, left/right
- Fn layer: Tab = US-JIS on/off (`usjis`; plain Tab in `ansi`), link keys (Q/W/E = BT1-3, R = 2.4G; hold about 6 s to pair (4400 matrix scans; the stock firmware takes 3-4 s)), battery indicator on `[ ] \`, lighting on the bottom-right cluster and `, . /` — the nuphy-air60 layout one row lower. The factory-reset chord is Fn+Esc held, then Fn+V
- USB strings: manufacturer "SMK", product "Air75 (SMK)"
- Bluetooth names: "Air75-1" .. "Air75-3" over BLE and "Air75-1 BT3.0" .. over classic Bluetooth, one per slot (`RF_BT_NAME`); a host shows which slot it paired with. The name is set just before switching to a slot, so a host that paired under an older name keeps showing it until it pairs again

## Apple fn

The board presents Apple's USB ID 05ac:024f, like the stock firmware, and its radio presents the same ID over Bluetooth, so macOS drives it with its Apple keyboard driver, which turns F1-F12 into media functions unless the Apple fn usage (page 0xFF, usage 0x03) is held. With `APPLE_FN` the keyboard report is five key slots plus that byte, exactly the stock layout, and RF frame byte 9 carries the same value. NKRO stays off so the keys travel in the report that carries fn. That also puts them on the interface that owns the LED output: with NKRO on, macOS never sent the Caps Lock LED report. Checked on an Air75 with a Mac host over USB and Bluetooth.

fn goes out only together with a key pressed under Fn in the Mac layer, never on its own, so Fn plus a lighting key does not trigger macOS's Globe-key action. The stock firmware does send a lone fn.

## Mod-tap (`TAP_HOLD`)

`MT(mod, kc)` / `LGUI_T(kc)` style keys, one modifier per key (`src/smk/tap_hold.c`). A key sends its tap keycode when it is released within the tapping term with no other key pressed meanwhile, and becomes its modifier after the tapping term or as soon as another key (Fn included) goes down while it is held, like QMK's `HOLD_ON_OTHER_KEY_PRESS`. smk has no millisecond clock, so the term counts matrix scans (`tick_scans()`): `TAPPING_TERM_SCANS` 285. That was meant as 200 ms, but at the ~1.4 ms per scan measured on the Air75 (4400 scans took about 6 s) it is about 0.4 s; it is kept because it works well in use. The tap and the modifier go through the normal key path, so US-JIS sees them like other keys.

## US-JIS

With `USJIS` the board types what the US keycaps show on a host whose keyboard layout is set to Japanese (JIS). The twenty US chords that a JIS host reads differently (`` ` ~ @ ^ & * ( ) _ = + [ { ] } \ | : ' " ``) are sent as the JIS chord for the printed character, for example Shift+2 goes out as the JIS `@` key without Shift. Everything else is untouched. The rules are those of the zmk-kb1-usjis specification, as in the QMK version for the NuPhy Air60 V2 (`src/smk/usjis.c`):

- the choice is made when a key goes down, from the key and the physical Shift keys; Ctrl, Alt and GUI are kept;
- a substituted key stays down on the host until it is released;
- while a substituted key is held, Shift follows the most recently pressed key, and it is set in the same report as that key;
- a mode change while a key is held waits until the last key is released.

It only works in the Win layer (OS switch on Win); the Mac layer never substitutes, because macOS drops the JIS-only keys that some of the chords need. Fn+Tab turns it on and off in either layer; the setting is saved with the other settings and survives a power cycle (default off). The status half of the side lights shows the change for about a second: magenta for on, dim white for off.

## Building, testing and flashing

The toolchain is SDCC 4.5.0 (the version smk's `flake.lock` pins) and meson; `nix develop` provides it. On macOS without Nix, `tools/macos/setup-toolchain.sh` builds SDCC 4.5.0 and the patched uCsim into `~/.local/smk` (then `. ~/.local/smk/env.sh`). SDCC 4.6 does not build smk (new warnings under `--Werror`).

```sh
meson setup build
meson compile -C build nuphy-air75_usjis_smk.hex nuphy-air75_ansi_smk.hex
python3 -m unittest discover -s tests -p test_air75.py        # board tests (simulator)
python3 -m unittest discover -s tests -p test_air75_usjis.py  # US-JIS (simulator, a few minutes)
meson compile -C build nuphy-air75_usjis_flash            # sinowisp write -d nuphy-air75 --force
```

`tests/test_air75.py` covers the boot escape, the matrix and keymap, and — when `SMK_AIR75_STOCK_JTAG` points at a stock dump — the whole chain through the stock bootloader: power-on into the firmware, Esc held into ISP mode, and a missing marker staying in the bootloader.

After the first write, check both ways back before anything else: hold Esc and replug (the board must enumerate as 0603:1020), then `sinowisp read` over USB from the running firmware.

## Code Options

Not stored in the flash image, so not read from the stock firmware. The watchdog being enabled by code option (as on the Air60) is what makes a firmware hang reset back into the boot escape.
