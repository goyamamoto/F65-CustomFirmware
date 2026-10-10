# EPOMAKER x AULA F65 (V1) custom firmware

[日本語](README.ja.md)

Open-source firmware for the EPOMAKER x AULA F65, first model ("V1", the "Fn-Ctrl" firmware; MCU BYK916 = SinoWealth SH68F90 family). QMK and ZMK do not run on this 8051-based MCU, so this is a port for [smk](https://github.com/carlossless/smk), the SinoWealth 8051 keyboard firmware by Karolis Stasaitis. This repository is smk with the F65 added (and the author's NuPhy Air75 port, which it builds on); everything smk supports still builds from it.

> [!WARNING]
> This replaces the vendor's firmware. Going back to the original firmware needs a backup of your own board (step 3); the vendor's firmware is not distributed here. A firmware that cannot jump back to the bootloader can only be recovered with a hardware programmer (for example an Arduino Nano running [sinodude-serial](https://github.com/carlossless/sinodude)); this port has a boot escape (Esc held at power-up) for that case. Use at your own risk.

> [!CAUTION]
> This firmware is for the V1. The F65 V2 ("Alt-Fn" firmware, USB product "AULA F65") has not been run with it: it has the same key rows and columns but drives its LEDs and one more pin (P0.3) differently. Step 1 tells the two apart. The F65 Pro is a different keyboard.

## What it does

- All 67 keys, Windows and Mac layers (Fn+A / Fn+S, saved), settings kept across power cycles
- USB, Bluetooth (three slots, named "AULA F65 BT5.0") and the 2.4 GHz dongle, through the keyboard's radio module
- The backlight effects (14 of them), colours, brightness, speed and the side lights; the status indicators (Caps Lock, the link keys while Fn is held, pairing, battery level on Fn+B, low battery, charging) shown over the backlight
- Sleep on battery after about a minute idle (a key wakes it), the backlight off after 30 s idle on battery, and a low-battery cutoff
- US-JIS (Fn+Tab, `usjis` layouts): type what the US keycaps show on a host set to the Japanese keyboard layout (Windows layer)
- IME keys on both sides of Space (`usjis` layouts): tap for Eisu/Kana (Mac) or Muhenkan/Henkan (Windows), hold for Command / Alt and Right Ctrl
- VIA (`-via` layouts): the keymap can be changed from [VIA](https://usevia.app) over USB and is kept in flash
- Boot escape: hold Esc while the keyboard powers up and it starts the bootloader instead, before any of this firmware's own USB code runs
- Wired, the keyboard keeps its original USB ID, so `sinowisp` finds it as `aula-f75`

## Pick a layout

Every image has the same hardware support, the boot escape, the Bluetooth names and sleep.

| Layout | Adds | Image |
| --- | --- | --- |
| `ansi` | nothing: US ANSI as printed | `aula-f65-v1_ansi_smk.hex` in `firmware/` |
| `usjis` | US-JIS (Fn+Tab), the IME keys on both sides of Space (Fn and Right Ctrl swap places) | `aula-f65-v1_usjis_smk.hex` in `firmware/` |
| `usjis-via` | `usjis` with the keymap editable from VIA | `aula-f65-v1_usjis-via_smk.hex` in `firmware/` |
| `ansi-via` | `ansi` with the keymap editable from VIA (checked in the simulator only) | build it (see [Building](#building)) |

The VIA layouts start with the same keymap as `ansi` / `usjis`. See [Using VIA](#using-via).

## Supported keyboards

| Keyboard | USB ID | Status |
| --- | --- | --- |
| EPOMAKER x AULA F65, first model (V1, "Fn-Ctrl" firmware), ANSI | 258a:010c, manufacturer "BY Tech", product "Gaming Keyboard" | Works; checked on the board with macOS hosts over USB, Bluetooth and the 2.4 GHz dongle |

Not supported: the F65 V2 (258a:010c too, but product "AULA F65", bcdDevice 0x1005) and the F65 Pro.

## Steps

1. Check the keyboard. Set the switch on the back to the middle (USB) position and plug it in. It shows up as USB ID `258a:010c`, manufacturer "BY Tech", product "Gaming Keyboard" (macOS: System Information > USB). A product name of "AULA F65" means a V2.
2. Install the tools. Install [sinowisp](https://github.com/carlossless/sinowisp) with `cargo install sinowisp` (this needs [Rust](https://rustup.rs/); sinowisp reads and writes the flash through the keyboard's bootloader over USB). On macOS, run it from a terminal app that has Input Monitoring permission (System Settings > Privacy & Security > Input Monitoring); without it the keyboard cannot be opened. Unplug any other keyboard with USB ID 258a:010c (many keyboards share it and sinowisp takes the last match).
3. Back up the original firmware. The files are the way back to it.
   ```sh
   sinowisp read -d aula-f75 f65-stock.hex                 # the firmware, for restoring (step 8)
   sinowisp read -d aula-f75 -s full f65-stock-full.hex    # firmware and bootloader, as an archive
   ```
   Reading the firmware twice and comparing the files shows that the read is stable.
4. Get the firmware. Pick a layout (above) and either use its image in [firmware/aula-f65-v1](firmware/aula-f65-v1) (check it with `shasum -a 256 -c SHA256SUMS`), or build it (see [Building](#building)).
5. Write it. Switch in the middle position, connected by USB.
   ```sh
   sinowisp write -d aula-f75 --force aula-f65-v1_usjis_smk.hex    # or another image
   ```
   `--force` is needed because the image is smaller than the flash; sinowisp fills the rest with zeros, which also resets the settings and a keymap changed in VIA (after every write: Fn+Tab for US-JIS, Fn+S for the Mac layer, if you use them).
6. Check the ways back.
   - With the new firmware running, `sinowisp read -d aula-f75 check.hex` works.
   - Unplug USB (switch in the middle: that cuts the power), wait a few seconds, hold Esc, plug in, and let go of Esc after two seconds: the keyboard shows up as `0603:1020` ("SINO WEALTH" / "Gaming KB"), the bootloader. Unplug and plug in again without Esc to go back to the firmware.

   The second way does not depend on this firmware's USB code. From a wireless position, move the switch to the middle first (the battery keeps the board running otherwise).
7. Use it. See [Key map](#key-map). Pair the dongle with Fn+R held 3 s in the 2.4 GHz position, a Bluetooth slot with Fn+Q / W / E held 3 s in the Bluetooth position.
8. Back to the original firmware at any time (switch in the middle):
   ```sh
   sinowisp write -d aula-f75 f65-stock.hex
   ```

## Key map

| Where | What |
| --- | --- |
| Base layer | US ANSI 65 %, as printed. Windows layer by default; Fn+A selects Windows, Fn+S selects Mac (saved) |
| Keys beside Space | `ansi`: Alt / Fn / Ctrl as printed (Mac: Command / Fn / Ctrl). `usjis`: Fn and Right Ctrl swap places, and the two keys beside Space are IME keys: Windows: left = tap Muhenkan, hold Alt; right = tap Henkan, hold Right Ctrl. Mac: left = tap Eisu, hold Command; right = tap Kana, hold Right Command. They become the modifier when held for about 0.4 s or as soon as another key is pressed |
| Fn + 1 … = | Windows: F1-F12. Mac: brightness, Mission Control, Launchpad, previous / play / next, mute, volume (Apple's order) |
| Fn + Right Shift + 1 … = | The other set: media keys on Windows, F1-F12 on Mac |
| Fn + Esc | `` ` `` (with Shift: `~`) |
| Fn + Tab | `usjis`: US-JIS on / off (saved; Windows layer only). `ansi`: Tab |
| Fn + Q / W / E | Bluetooth slot 1 / 2 / 3 (Bluetooth position): a short press selects it, a 3 s hold pairs it (the key blinks blue) |
| Fn + R | 2.4 GHz: a 3 s hold pairs the dongle (the key blinks) |
| Fn (held) | The active link's key lights: Y white (USB), R green (2.4 GHz), the slot's key blue (Bluetooth) |
| Fn + B (held) | Battery level on the number row, in a wireless position on battery |
| Fn + \\ , ] , [ | Next effect (through off), next colour (7 colours and rainbow), all lighting off / on |
| Fn + ↑ ↓ ← → | Brightness (5 levels) and speed (5 levels; the side lights follow) |
| Fn + / . , | Side lights: next effect, colour, brightness |
| Fn + Del / End / PgUp | Insert / Home / `` ` `` |
| Fn + U / I / O | PrtSc / ScrLk / Pause |
| Fn + Backspace (held), then Fn + V | Reset of the settings (US-JIS off, Windows layer, lighting defaults; on the VIA layouts also the built-in keymap) |

Everything works the same over USB and over the radio. Lighting changes are saved about 3 s after the last change; the whole backlight blinks once at that moment (the flash write).

## Using VIA

With a `-via` image written and the switch in the middle (USB):

1. Open [usevia.app](https://usevia.app) in a browser with WebHID (Chrome, Edge).
2. In Settings, turn on "Show Design tab". In Design, load [via.json](src/keyboards/aula-f65-v1/via.json) from this repository.
3. In Configure, click "Authorize device" and pick "AULA F65 (SMK)".

The four layers are Windows base, Mac base, Windows Fn and Mac Fn. Changes are saved to flash about 0.5 s after the last one and are used over Bluetooth and 2.4 GHz too. VIA can set basic keys, modified keys (such as Ctrl+C), mod-taps, `MO`, media keys and this keyboard's own keys (US-JIS, Win / Mac, the link, battery, Fn-row and lighting keys); layer-taps, toggles, one-shot keys and macros are not supported, and the lighting stays on its Fn keys. Details: [docs/keyboards/aula-f65-v1.md](docs/keyboards/aula-f65-v1.md#via).

## Building

The images in `firmware/` are release builds made with the steps below on macOS (the toolchain script). Built from the same commit with SDCC 4.5.0 on the same platform, they come out byte for byte the same, so you can check a download against your own build. A build on another platform can differ by a few bytes: SDCC's register allocation is not identical across hosts (the CI's Linux build differs from these images in one function), which is why the CI runs the simulator tests on its own release build instead of comparing bytes.

### Release build, step by step

1. Get the source.
   ```sh
   git clone https://github.com/goyamamoto/F65-CustomFirmware.git
   cd F65-CustomFirmware
   ```
2. Install the toolchain. You need SDCC 4.5.0 exactly (other versions stop at smk's `--Werror` or give a different image), meson, ninja and Python 3.
   - With [Nix](https://nixos.org/) (Linux or macOS): run `nix develop` in the repository. It provides everything, including sinowisp and the simulator.
   - On macOS without Nix: with [Homebrew](https://brew.sh/) installed, run [tools/macos/setup-toolchain.sh](tools/macos/setup-toolchain.sh) once. It installs meson and ninja with Homebrew and builds SDCC 4.5.0 and the simulator into `~/.local/smk`, which takes a while. Then, in every new terminal:
     ```sh
     . ~/.local/smk/env.sh
     sdcc --version    # must show 4.5.0
     ```
   - Elsewhere: install meson, ninja, Python 3 and SDCC 4.5.0 (from source if your package manager has another version).
3. Set up a release build, once. `build-release` is the output folder; `--buildtype=release` is what leaves out the debug console and logging.
   ```sh
   meson setup build-release --buildtype=release
   ```
4. Build the layouts you want:
   ```sh
   meson compile -C build-release aula-f65-v1_usjis_smk.hex aula-f65-v1_ansi_smk.hex aula-f65-v1_usjis-via_smk.hex aula-f65-v1_ansi-via_smk.hex
   ```
   The images are written to `build-release/`. After changing the source, run this step again; step 3 is not needed again.
5. Check it (optional, macOS). Unchanged source gives the same files as `firmware/`; each image you built must say `OK`:
   ```sh
   (cd build-release && shasum -a 256 --ignore-missing -c ../firmware/aula-f65-v1/SHA256SUMS)
   ```
6. Write it as in [Steps](#steps) 5 and 6, with the path to your image, for example `sinowisp write -d aula-f75 --force build-release/aula-f65-v1_usjis_smk.hex`.

### Release and debug builds

| | Release | Debug |
| --- | --- | --- |
| Set up with | `meson setup build-release --buildtype=release` | `meson setup build` (meson's default) |
| Meant for | Daily use; the images in `firmware/` | Development |
| HID debug console (`tools/smk-console`) | No | Yes: reports chip IDs, mode changes and settings to the host |
| Logging | No | Yes |
| Source-level simulator tests | Skipped (no `.cdb`) | Yes |

A keyboard sees everything typed on it; the debug build reports to the host over its console, the release build has no console.

### Simulator tests

The tests run the images in a patched uCsim. They need the patched simulator (from `nix develop` or the macOS script) and the layouts built; they use `build/` by default and can be pointed at other images with `SMK_F65_FIRMWARE` (`usjis`) and `SMK_F65_ANSI_FIRMWARE` (`ansi`):

```sh
python3 -m unittest discover -s tests -p test_f65.py            # board tests (both layouts)
python3 -m unittest discover -s tests -p test_f65_usjis.py      # US-JIS on the F65 matrix
python3 -m unittest discover -s tests -p test_f65_radio.py      # switch, radio link, reports, battery, sleep, recovery
python3 -m unittest discover -s tests -p test_f65_fnrow.py      # the number row under Fn and Right Shift
python3 -m unittest discover -s tests -p test_f65_led.py        # the LED engine's limits
python3 -m unittest discover -s tests -p test_f65_indicators.py # indicators with the radio, sleep, the watchdog
python3 -m unittest discover -s tests -p test_f65_backlight.py  # the backlight, its keys, saving, the limits with effects
python3 -m unittest discover -s tests -p test_f65_via.py        # the VIA layouts (SMK_F65_VIA_FIRMWARE, SMK_F65_ANSI_VIA_FIRMWARE)
```

Some checks compare with the original firmware run in the same simulator; they need its image (`SMK_F65_STOCK_IMAGE`, the file inside the vendor's updater, which is not distributed here) and are skipped without it. `meson test -C build-release` runs every test file against that build directory's own images and fails, rather than skips, when an image is missing.

Technical notes for the board (pins, the radio link, the LEDs, the backlight, sleep, recovery, VIA, and the differences from the original firmware) are in [docs/keyboards/aula-f65-v1.md](docs/keyboards/aula-f65-v1.md). The upstream smk README is kept in [docs/README-smk.md](docs/README-smk.md).

## Known limitations

- Writing any image resets the settings (sinowisp erases the flash and writes the image padded with zeros): US-JIS off, Windows layer, lighting defaults, and the keymap VIA changed.
- Not included: the vendor driver's protocol (no per-key custom lighting, no configuration from the vendor's software), the Fn+G test mode, the power-on light sweep, and the white Y shown for 3 s after switching to USB.
- The backlight is dimmer than the original firmware's (about two thirds at the maximum): each LED is lit in one of 20 subframes of 200 µs, with the matrix scan between groups of them. The default brightness is 2 of 4.
- US-JIS works only in the Windows layer: macOS drops the JIS-only keys some of the substitutions need.
- VIA: no macros and no lighting menu; the keymap is changed over USB only (switch in the middle). Two keys that send the same key (for example C and Ctrl+C) share it: releasing one releases it for both.
- Only the ANSI matrix is known; no ISO or JIS F65 exists.

## Changes from upstream smk

Based on smk at [69373bb](https://github.com/carlossless/smk/commit/69373bbb633bd1159f4541f486ff7506563a38ec) (2026-09-16); changes made from 2026-09 to 2026-10. This tree also contains the author's NuPhy Air75 port ([goyamamoto/NuPhyAir-CustomFirmware](https://github.com/goyamamoto/NuPhyAir-CustomFirmware)), which the F65 port builds on.

- New board: `src/keyboards/aula-f65-v1/` (layouts `ansi`, `usjis`, `ansi-via`, `usjis-via` and `leddiag`, an LED diagnostic image; `via.json`), `docs/keyboards/aula-f65-v1.md`, `tests/test_f65*.py`, `tests/f65_devices.py`, `tests/f65_radio.py`. The board's own radio driver (`f65_rf.c`, the module's protocol over EUART0), power and sleep (`f65_power.c`, `user_sleep.c`), LED engine (`led.c`), indicators (`indicators.c`) and backlight effects (`backlight.c`; `backlight_tables.h` holds the original firmware's effect tables)
- Optional features, off unless a board enables them: VIA's raw HID commands and a keymap kept in flash (`VIA_ENABLE`: `src/smk/via.c/.h`, `src/smk/dynamic_keymap.c/.h`, the USB layout in `src/smk/usb.c`), US-JIS (`src/smk/usjis.c/.h`), mod-tap keys (`src/smk/tap_hold.c/.h`), boot escape (`BOOT_ESCAPE`), keys released with the keycode they went down with (`LATCH_KEYCODES`), a matrix debounce over several scans (`MATRIX_DEBOUNCE_SCANS`), a delay that does not kick the watchdog (`DELAY_NO_WATCHDOG_KICK`), a watchdog kick before each flash operation (`FLASH_OP_WATCHDOG_KICK`), a bounded PLL lock wait (`CLOCK_PLL_WAIT_BOUND`), strict feature reports and a USB boot gate, a 1 ms PWM4 tick and EUART0 at interrupt priority 3 for the radio, `defines` per keyboard and per layout (`meson.build`)
- Shared changes: `tick_scans()` (`src/smk/tick.c/.h`), `process_keycode()` split out of the matrix (`src/smk/matrix.c/.h`), settings fields and a fallback for records of an older length (`src/smk/settings.c/.h`), a startup that returns the PWM and LED ports to their reset state (`src/sino51lib/startup.c`), the simulator's SH68F90 model extended with EUART0 byte timing and interrupt priorities, the P0 / P4 pins, PWM4, a PWM trace, key contacts, INT4 wake, the watchdog kick gap, a real-rate Timer2 and EP2 OUT (`tools/ucsim/`), `meson test` per test file and strict (`meson.build`, `tests/`)
- `README.md` replaced by this file; the upstream one moved to `docs/README-smk.md`. New: `README.ja.md`, `firmware/`, `tools/macos/`

## Credits and license

- [smk](https://github.com/carlossless/smk), [sinowisp](https://github.com/carlossless/sinowisp) and [sinodude](https://github.com/carlossless/sinodude) by Karolis Stasaitis, whose work on SinoWealth keyboards made this possible.
- [Thaolia/smk_aulaF75](https://github.com/Thaolia/smk_aulaF75), the smk port for the AULA F75, showed that this family's radio module speaks over EUART0 the way it does.
- VIA's command handling and the keymap interface follow QMK's `quantum/via.c` and `quantum/dynamic_keymap.c` ([QMK](https://github.com/qmk/qmk_firmware), GPL-2.0-or-later); [VIA](https://github.com/the-via/app) is the configurator.
- The US-JIS rules follow [goyamamoto/zmk-kb1-usjis](https://github.com/goyamamoto/zmk-kb1-usjis).
- License: GPL-2.0, as smk ([LICENSE](LICENSE)).

EPOMAKER and AULA are trademarks of their owners. This project is not affiliated with or endorsed by them.
