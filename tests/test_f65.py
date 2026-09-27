#!/usr/bin/env python3
"""aula-f65-v1 board tests, driven through the patched uCsim simulator.

Covers the EPOMAKER x AULA F65 (V1) port: the image layout for sinowisp, USB
enumeration with the board's descriptors, feature reports (ISP as sinowisp
sends it, other IDs), remote wakeup, the boot-time ISP escape (Esc held at
power-up), the pins after boot against the stock init, every key of the
5 x 16 matrix, the Fn layer (Fn+Esc = grave, F-keys, the settings-reset chord
that sends nothing), keys held across Fn, the Win / Mac keys (Fn+A / Fn+S),
flash writes confined to the settings sector, and (with SMK_F65_STOCK_IMAGE)
the port against the stock V1 keymap tables. Run from the repo root after building:

    meson compile -C build aula-f65-v1_usjis_smk.hex aula-f65-v1_ansi_smk.hex
    python3 -m unittest discover -s tests -p test_f65.py

SMK_F65_FIRMWARE (usjis) and SMK_F65_ANSI_FIRMWARE (ansi) override the images.
TestStockBootloaderChain also needs the stock ISP bootloader, which this
repository does not ship: point SMK_F65_STOCK_DUMP at a full 64 KB readout of an
F65 (only 0xF000-0xFFFF is used) and keep sinowisp on PATH; without them those
tests are skipped.
"""

import os
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from sim import REPO_ROOT, Sim, load_symbols, skip_or_fail, get_descriptor, DESC_DEVICE, DESC_CONFIGURATION, DESC_STRING
from devices import KeyMatrix, P5, P7
from test_air75 import Air75Sim, read_ihex
from f65_radio import RadioBoard

F65_FW = os.environ.get("SMK_F65_FIRMWARE") or str(REPO_ROOT / "build" / "aula-f65-v1_usjis_smk.hex")
F65_ANSI_FW = os.environ.get("SMK_F65_ANSI_FIRMWARE") or str(REPO_ROOT / "build" / "aula-f65-v1_ansi_smk.hex")

ISP_ENTRY = 0xFF00
ISP_KEYS_OK = 0xFF0A      # stock bootloader: A/B keys matched, ISP mode starts
ISP_KEYS_BAD = 0xFF36     # stock bootloader: keys wrong, back to the firmware
BL_MARKER_OK = 0xF053     # stock bootloader: LJMP found at 0xEFFB, run the firmware
BL_NO_MARKER = 0xF029     # stock bootloader: no LJMP at 0xEFFB, stay in ISP mode
IE = 0xA8
P4, P6 = 0xB0, 0xC0

# src/keyboards/aula-f65-v1/kbdef.h
COL_PIN = {0: (P6, 0), 1: (P6, 1), 2: (P6, 2), 3: (P6, 3), 4: (P6, 4), 5: (P6, 5), 6: (P6, 6), 7: (P6, 7),
           8: (P5, 0), 9: (P5, 1), 10: (P5, 2), 11: (P5, 7), 12: (P4, 0), 13: (P4, 2), 14: (P4, 3), 15: (P4, 5)}
ROW_PIN = {0: (P7, 1), 1: (P7, 2), 2: (P7, 3), 3: (P5, 3), 4: (P5, 4)}

MOD_LCTL, MOD_LSFT, MOD_LALT, MOD_LGUI = 0x01, 0x02, 0x04, 0x08
MOD_RCTL, MOD_RSFT, MOD_RALT, MOD_RGUI = 0x10, 0x20, 0x40, 0x80

ESC, BSPC, DEL, V, M, A, S = (0, 0), (13, 0), (15, 0), (4, 3), (7, 3), (1, 2), (2, 2)
# Fn is right of Space in `ansi` (as printed); `usjis` puts the right IME
# mod-tap there and Fn one key further right.
FN_ANSI, FN_USJIS, RIGHT_OF_SPACE = (9, 4), (10, 4), (9, 4)
LSFT, RSFT = (0, 3), (13, 3)

# Every key of the Win base layer of the ansi layout: position -> HID usage
# (non-modifiers) or modifier bit. Positions are those of the stock V1 keymap
# table (col, stock row bit - 1).
ANSI_WIN_KEYS = {
    (0, 0): 0x29, (1, 0): 0x1E, (2, 0): 0x1F, (3, 0): 0x20, (4, 0): 0x21, (5, 0): 0x22, (6, 0): 0x23,
    (7, 0): 0x24, (8, 0): 0x25, (9, 0): 0x26, (10, 0): 0x27, (11, 0): 0x2D, (12, 0): 0x2E, (13, 0): 0x2A,
    (15, 0): 0x4C,
    (0, 1): 0x2B, (1, 1): 0x14, (2, 1): 0x1A, (3, 1): 0x08, (4, 1): 0x15, (5, 1): 0x17, (6, 1): 0x1C,
    (7, 1): 0x18, (8, 1): 0x0C, (9, 1): 0x12, (10, 1): 0x13, (11, 1): 0x2F, (12, 1): 0x30, (13, 1): 0x31,
    (15, 1): 0x4B,
    (0, 2): 0x39, (1, 2): 0x04, (2, 2): 0x16, (3, 2): 0x07, (4, 2): 0x09, (5, 2): 0x0A, (6, 2): 0x0B,
    (7, 2): 0x0D, (8, 2): 0x0E, (9, 2): 0x0F, (10, 2): 0x33, (11, 2): 0x34, (13, 2): 0x28, (15, 2): 0x4E,
    (1, 3): 0x1D, (2, 3): 0x1B, (3, 3): 0x06, (4, 3): 0x19, (5, 3): 0x05, (6, 3): 0x11, (7, 3): 0x10,
    (8, 3): 0x36, (9, 3): 0x37, (10, 3): 0x38, (14, 3): 0x52, (15, 3): 0x4D,
    (5, 4): 0x2C, (13, 4): 0x50, (14, 4): 0x51, (15, 4): 0x4F,
}
ANSI_WIN_MODS = {(0, 3): MOD_LSFT, (13, 3): MOD_RSFT, (0, 4): MOD_LCTL, (1, 4): MOD_LGUI, (2, 4): MOD_LALT,
                 (10, 4): MOD_RCTL}
EMPTY = [(14, 0), (14, 1), (12, 2), (14, 2), (11, 3), (12, 3), (3, 4), (4, 4), (6, 4), (7, 4), (8, 4),
         (11, 4), (12, 4)]

NVM_BASE = 0xEC00           # settings sector (platform: 0xF000 - marker sector - 0x200); 5A A5 <len> <payload> <sum>
NVM_END = 0xEE00

# Stock V1 GPIO init (0xA57B-0xA5D5): PxCR, PxPCR and the latches, P0..P7.
STOCK_CR = [0x04, 0x3F, 0x3F, 0x3F, 0x6F, 0x87, 0xFF, 0x70]
STOCK_PCR = [0xF8, 0x3F, 0x3F, 0x3F, 0x6F, 0x9F, 0xFF, 0xDF]
STOCK_LATCH = [0x04, 0x00, 0x00, 0x00, 0x6D, 0x87, 0xFF, 0x10]
PORT_SFR = [0x80, 0x90, 0x98, 0xA0, 0xB0, 0x88, 0xC0, 0xF8]
CR_SFR = [0xE1, 0xE2, 0xE3, 0xE4, 0xE5, 0xE6, 0xE7, 0xD1]
PCR_SFR = [0xE9, 0xEA, 0xEB, 0xEC, 0xED, 0xEE, 0xEF, 0xD9]
USBCON, WKUP = 0x91, 0x02


def _rst(fw, module):
    """The linker's relocated listing of one module (debug and release builds both keep it)."""
    path = Path(fw).with_suffix(".ihx.p") / (module + ".rst")
    if not path.exists():
        skip_or_fail(f"no linker listing {path}")
    return path.read_text().splitlines()


def field_offset(fw, module, func):
    """Offset of the first user_settings field `func` touches, from its code in
    the linker listing: `mov dptr,#(_user_settings + 0x000a)`. None if absent."""
    inside = False
    for line in _rst(fw, module):
        if re.search(r"\s_%s:$" % re.escape(func), line):
            inside = True
            continue
        if inside:
            m = re.search(r"\(_user_settings \+ 0x([0-9a-fA-F]+)\)", line)
            if m:
                return int(m.group(1), 16)
            if re.search(r"\s_\w+:$", line):
                break
    return None


def settings_offsets(fw):
    """{'os_mac': n, 'rf_link': n, 'usjis': n or None} for this image."""
    has_usjis = (Path(fw).with_suffix(".ihx.p") / "usjis.rst").exists() and \
        "usjis_process_record" in load_symbols(Path(fw).with_suffix(".map"))
    return {"os_mac": field_offset(fw, "kb", "kb_apply_os_mode"),
            "rf_link": field_offset(fw, "kb", "kb_usb_at_boot"),
            "usjis": field_offset(fw, "usjis", "usjis_is_enabled") if has_usjis else None}


def bit_addr(fw, module, name):
    """Bit-space address of a __bit variable, from the linker listing."""
    for line in _rst(fw, module):
        m = re.match(r"^\s+([0-9A-F]{6})\s+\d+ _%s::?$" % re.escape(name), line)
        if m:
            return int(m.group(1), 16)
    raise KeyError(name)


def _need(fw):
    if not Path(fw).exists():
        skip_or_fail(f"no aula-f65-v1 firmware at {fw}")
    reason = Sim(fw).available()
    if reason:
        skip_or_fail(reason)


class F65KeyMatrix(KeyMatrix):
    """The F65 V1 key matrix: columns on P6/P5/P4, rows on P7.1-3 / P5.3-4."""

    def _col_driven_low(self, sess, col, port_cache):
        sfr, bit = COL_PIN[col]
        v = port_cache.get(sfr)
        if v is None:
            v = port_cache[sfr] = sess.get_sfr(sfr) or 0
        return (v & (1 << bit)) == 0

    def inject(self, sess):
        port_cache = {}
        low_rows = set()
        for (c, r) in self.pressed:
            if self._col_driven_low(sess, c, port_cache):
                low_rows.add(r)
        level = {P5: 0xFF, P7: 0xFF}
        for r in low_rows:
            port, bit = ROW_PIN[r]
            level[port] &= ~(1 << bit)
        sess.set_pin(P7, level[P7])
        sess.set_pin(P5, level[P5])


class F65Sim(Air75Sim):
    MAX_HITS = 224             # row reads to wait for a report: 7 scans (a full scan is 32 reads; a key
                               # change counts after MATRIX_DEBOUNCE_SCANS = 4 scans)
    TAIL_HITS = 32

    def __init__(self, firmware=None):
        super().__init__(firmware or F65_FW)
        self.matrix = F65KeyMatrix()
        self.fn = FN_ANSI if "ansi" in Path(self.firmware).name else FN_USJIS
        self.off = settings_offsets(self.firmware)
        self.watch, self.hits = set(), set()   # step(): code addresses to note when reached
        # The board around the MCU: switch in the middle (USB), USB power on,
        # radio module idle (tests/f65_radio.py).
        self.radio = RadioBoard(self)

    # build-6: the backlight runs from power-up. Rigs that look at the LED table
    # as build-5 left it (indicators only) set LIGHTING = False: the lighting is
    # switched off (bl_on = 0, as Fn + [ does) before the first main-loop pass.
    LIGHTING = True

    def lighting_setting(self):
        """Address of user_settings.bl_magic (bl_on is the next byte), None
        without the lighting (leddiag)."""
        if not (Path(self.firmware).with_suffix(".ihx.p") / "backlight.rst").exists():
            return None
        return self._a("user_settings") + field_offset(self.firmware, "backlight", "backlight_defaults")

    def apply_lighting(self):
        a = self.lighting_setting()
        if a is not None and not self.LIGHTING:
            self.cmd("set mem xram 0x%x 0x00" % (a + 1))

    def boot(self, usb=True, mac=False):
        self.reset_fast()
        self.brk(self._a("kb_update_switches"))
        self.run()
        self.cmd("delete")
        self.apply_lighting()
        self.mark_usb_configured()
        if mac:
            self.matrix.press(*self.fn)
            self.step()
            self.matrix.press(*S)
            self.step()
            self.matrix.clear()
            self.step()

    def step(self, max_hits=None):
        """Run the scan until a new EP1 report appears (plus one more scan) or
        max_hits row reads pass; return the new reports. Stops at `watch`
        addresses are noted in `hits`."""
        before = len(self.ep1_reports())
        self.brk(self._a("user_matrix_read_rows"))
        for addr in self.watch:
            self.brk(addr)
        tail = None
        for i in range(max_hits or self.MAX_HITS):
            at = self.stopped_at(self.run())
            if at in self.watch:
                self.hits.add(at)
            self.matrix.inject(self)
            if tail is None:
                if i % 8 == 7 and len(self.ep1_reports()) > before:
                    tail = self.TAIL_HITS
            else:
                tail -= 1
                if tail == 0:
                    break
        self.cmd("delete")
        return self.ep1_reports()[before:]

    def down(self, key):
        self.matrix.press(*key)
        return self.step()

    def up(self, key):
        self.matrix.release(*key)
        return self.step()

    def setting(self, name):
        return self.get_xram(self._a("user_settings") + self.off[name])[0]

    def set_setting(self, name, value):
        self.cmd("set mem xram 0x%x 0x%02x" % (self._a("user_settings") + self.off[name], value))

    def tap(self, *keys):
        """As Air75Sim.tap (press the keys one after another, return the last EP1
        report, release all), but through step() so `watch` sees every stop."""
        for k in keys:
            self.matrix.press(*k)
            self.step()
        reps = self.ep1_reports()
        pressed = reps[-1] if reps else None
        self.matrix.clear()
        self.step()
        return pressed

    def settle(self, passes=6):
        """Run a few main-loop passes (stopping at settings_task), so a pending
        settings save is done; notes `watch` addresses reached meanwhile."""
        task = self._a("settings_task")
        for addr in self.watch | {task}:
            self.brk(addr)
        for _ in range(passes):
            at = self.stopped_at(self.run())
            if at in self.watch:
                self.hits.add(at)
        self.cmd("delete")

    def record(self):
        """The saved settings record's payload, once any pending save is done."""
        self.settle()
        rec = self.get_rom(NVM_BASE, 3 + max(v for v in self.off.values() if v is not None) + 2)
        assert rec[:2] == [0x5A, 0xA5], f"settings record: {rec}"
        return rec[3:]

    def set_bit(self, module, name, value):
        a = bit_addr(self.firmware, module, name)
        byte = self.get_iram(0x20 + a // 8, 1)[0]
        byte = (byte | (1 << (a % 8))) if value else (byte & ~(1 << (a % 8)))
        self.cmd("set mem iram 0x%x 0x%02x" % (0x20 + a // 8, byte))

    def get_bit(self, module, name):
        a = bit_addr(self.firmware, module, name)
        return (self.get_iram(0x20 + a // 8, 1)[0] >> (a % 8)) & 1

    def setup_packet(self, setup):
        """Deliver a SETUP to the running firmware: stage it, raise SETUPIF and
        run until the main loop comes round."""
        self.cmd("set mem xram 0x1100 " + " ".join("0x%02x" % b for b in setup))
        self.set_sfr(0x92, 0x10)                        # USBIF1.SETUPIF
        # first the USB interrupt taking it (had the main loop been interrupted
        # just before usb_task, the one instruction after RETI would reach
        # usb_task before the USB interrupt is taken), then the main loop
        self.brk(0x003B)
        self.run()
        self.cmd("delete")
        self.brk(self._a("usb_task"))
        self.run()
        self.cmd("delete")

    def rom_image(self):
        image = []
        for addr in range(0, 0x10000, 0x800):
            image += self.get_rom(addr, 0x800)
        return image

    def get_rom(self, addr, n):
        out = self.cmd("dump rom 0x%x 0x%x" % (addr, addr + n - 1))
        vals = []
        for line in out.splitlines():
            m = re.match(r"\s*0x[0-9a-fA-F]+\s+((?:[0-9a-fA-F]{2} )+)", line)
            if m:
                vals += [int(x, 16) for x in m.group(1).split()]
        return vals[:n]


def report(mods=0, *keys):
    return [mods, 0] + (list(keys) + [0] * 6)[:6]


class TestImage(unittest.TestCase):
    """Checks on the .hex that will be written with `sinowisp write -d aula-f75 --force`."""

    FLASH_CFG_ADDR = 0xEC00   # settings sector; the marker sector (0xEE00) and bootloader (0xF000) follow

    def _images(self):
        for fw in (F65_FW, F65_ANSI_FW):
            if not Path(fw).exists():
                skip_or_fail(f"no aula-f65-v1 firmware at {fw}")
            yield fw, read_ihex(fw), load_symbols(Path(fw).with_suffix(".map"))

    def test_layout_for_sinowisp(self):
        for fw, data, _ in self._images():
            with self.subTest(fw=Path(fw).name):
                self.assertEqual(min(data), 0)
                self.assertLess(max(data), self.FLASH_CFG_ADDR,
                                f"code reaches 0x{max(data):04x}; settings/marker/bootloader start at 0x{self.FLASH_CFG_ADDR:04x}")
                self.assertEqual(data.get(0), 0x02, "sinowisp relocates the LJMP at 0x0000 to 0xEFFB")

    def test_device_descriptor_in_rom(self):
        """258A:010C, the stock ID, so sinowisp -d aula-f75 finds the running board."""
        for fw, data, _ in self._images():
            with self.subTest(fw=Path(fw).name):
                rom = bytes(data.get(i, 0xFF) for i in range(max(data) + 1))
                self.assertIn(bytes([0x12, 0x01, 0x10, 0x01, 0x00, 0x00, 0x00, 0x08, 0x8A, 0x25, 0x0C, 0x01]), rom)

    def test_boot_escape_isp_and_radio_are_linked(self):
        for fw, _, sym in self._images():
            with self.subTest(fw=Path(fw).name):
                for name in ("user_boot_escape", "isp_jump", "usb_task", "rf_euart0_interrupt_handler",
                             "pwm4_ms_tick_interrupt_handler", "rf_task"):
                    self.assertIn(name, sym)


class TestUsb(unittest.TestCase):
    """Enumeration through the real init path, and the ISP feature report."""

    @classmethod
    def setUpClass(cls):
        _need(F65_FW)
        cls.sim = Sim(F65_FW)

    def test_device_descriptor(self):
        desc = self.sim.reassemble(self.sim.control_in(get_descriptor(DESC_DEVICE)))
        self.assertEqual(len(desc), 18, desc)
        self.assertEqual(desc[:8], [0x12, 0x01, 0x10, 0x01, 0x00, 0x00, 0x00, 0x08])
        self.assertEqual((desc[8] | desc[9] << 8, desc[10] | desc[11] << 8), (0x258A, 0x010C))
        self.assertEqual(desc[14:17], [1, 2, 3], "iManufacturer, iProduct, iSerialNumber")

    def test_configuration_descriptor(self):
        desc = self.sim.reassemble(self.sim.control_in(get_descriptor(DESC_CONFIGURATION)))
        self.assertEqual(desc[:2], [9, DESC_CONFIGURATION])
        self.assertEqual(desc[4], 2, "two interfaces: boot keyboard + extra (with the ISP report)")
        ifaces = [desc[i:i + 9] for i in range(len(desc) - 8) if desc[i] == 9 and desc[i + 1] == 4]
        self.assertEqual([(d[2], d[5], d[6], d[7]) for d in ifaces], [(0, 3, 1, 1), (1, 3, 0, 0)],
                         "IF0 HID boot keyboard, IF1 HID")

    def _string(self, index):
        desc = self.sim.reassemble(self.sim.control_in(get_descriptor(DESC_STRING, index=index)))
        return bytes(desc[2:desc[0]]).decode("utf-16le")

    def test_strings(self):
        self.assertEqual(self._string(1), "SMK")
        self.assertEqual(self._string(2), "AULA F65 (SMK)")

    def test_enumeration_reaches_configured(self):
        from sim import set_address, set_configuration
        out = self.sim.boot_enumerate(set_address(0x2A), set_configuration(1))
        self.assertEqual(self.sim.dump_value(out, self.sim.USB_DEVICE_STATE), self.sim.STATE_CONFIGURED)

    def test_isp_report_jumps_to_bootloader(self):
        out = self.sim.trigger_isp_jump()
        self.assertEqual(self.sim.stopped_at(out), ISP_ENTRY)
        self.assertEqual((self.sim.acc(out), self.sim.reg_b(out)), (0x5A, 0xA5))

    def test_isp_report_needs_05_75(self):
        out = self.sim.trigger_isp_jump(confirm=(0x00, 0x00), run_task=False)
        self.assertEqual(self.sim.stopped_at(out), self.sim.SLED_END)

    # --- SET_REPORT(Feature) exactly as the host sends it -----------------
    EP0CON, SLED = 0x97, 0x900E

    def _set_feature(self, report_id, data, run_task):
        """SET_REPORT(Feature, report_id) on interface 1 with wLength = len(data)
        and the data stage `data`, as hidapi sends a feature report. Returns
        the sim output, with EP0CON dumped before and after the SETUP."""
        sim = self.sim
        setup = [0x21, 0x09, report_id, 0x03, 0x01, 0x00, len(data), 0x00]
        cmds = sim._boot_to_post_init() + [
            "echo ===PRE===", "dump sfr 0x97 0x97",
            sim._set_xram(sim.EP0_OUT_BUF, setup), "set mem sfr 0x92 0x10",
            "break 0x%x" % self.SLED, "run",
            "echo ===SETUP===", "dump sfr 0x97 0x97",
            "echo ===DATA===",
            sim._set_xram(sim.EP0_OUT_BUF, (list(data) + [0] * 8)[:8]), "set mem sfr 0x93 0x10",
            "pc 0x9000", "break 0x%x" % self.SLED, "run",
            "echo ===DONE===",
        ]
        if run_task:
            cmds += ["pc 0x%x" % sim.USB_TASK, "break 0x%x" % ISP_ENTRY, "run"]
        return sim.run(cmds + ["info registers"])

    def test_isp_report_as_sinowisp_sends_it(self):
        """sinowisp: send_feature_report([05 75 00 00 00 00]), wLength 6."""
        out = self._set_feature(0x05, [0x05, 0x75, 0x00, 0x00, 0x00, 0x00], run_task=True)
        self.assertEqual(self.sim.stopped_at(out), ISP_ENTRY)
        self.assertEqual((self.sim.acc(out), self.sim.reg_b(out)), (0x5A, 0xA5))

    def test_report_5_without_the_isp_command_is_acknowledged(self):
        out = self._set_feature(0x05, [0x05, 0x01, 0x00, 0x00, 0x00, 0x00], run_task=False)
        self.assertEqual(self.sim.stopped_at(out), self.SLED, "no jump")
        data = re.split(r"^===DATA===$", out, flags=re.M)[-1]
        self.assertRegex(data, r"\[SIE\] EP0 IN\[\d+\] 0 bytes:", "status stage: a zero-length IN (ACK)")

    def test_unknown_feature_report_is_stalled(self):
        """EP0CON read through the command socket before and after the SETUP
        (one reply per command, so nothing depends on how uCsim echoes a
        batch or on the length of the firmware path)."""
        for report_id in (0x02, 0x03, 0x09):     # (7 is the debug console in debug builds)
            with self.subTest(report_id=report_id):
                kb = F65Sim(F65_FW)
                self.addCleanup(kb.close)
                kb.boot()
                pre = kb.get_sfr(self.EP0CON)
                kb.setup_packet([0x21, 0x09, report_id, 0x03, 0x01, 0x00, 0x06, 0x00])
                post = kb.get_sfr(self.EP0CON)
                self.assertEqual(pre & 0x0A, 0, f"EP0CON before: {pre:#04x}")
                self.assertEqual(post & 0x0A, 0x0A, f"EP0CON after the SETUP: {post:#04x} (IEP0STL|OEP0STL expected)")


class TestBootEscape(unittest.TestCase):
    """Esc (C0 x R0 = P6.0 x P7.1) held at power-up reaches the ISP bootloader
    entry with its keys loaded, before USB is initialised; any other key, or
    none, boots normally."""

    @classmethod
    def setUpClass(cls):
        _need(F65_FW)

    def _boot_with(self, *keys, release_after=None, fw=None):
        kb = F65Sim(fw)
        self.addCleanup(kb.close)
        kb.reset_fast()
        for k in keys:
            kb.matrix.press(*k)
        stop = kb.run_until(kb._a("user_boot_escape"), kb._a("usb_init"))
        self.assertEqual(stop, kb._a("user_boot_escape"), "the escape runs before usb_init")
        rr = kb._a("user_matrix_read_rows")
        targets = (ISP_ENTRY, kb._a("usb_init"))
        kb.brk(rr)
        for t in targets:
            kb.brk(t)
        reads = 0
        while True:
            out = kb.run()
            at = kb.stopped_at(out)
            if at != rr:
                break
            reads += 1
            if release_after is not None and reads > release_after:
                kb.matrix.clear()
            kb.matrix.inject(kb)
        kb.cmd("delete")
        return kb, at

    def test_esc_held_jumps_to_isp_before_usb_init(self):
        for fw in (F65_FW, F65_ANSI_FW):
            with self.subTest(fw=Path(fw).name):
                kb, at = self._boot_with(ESC, fw=fw)
                self.assertEqual(at, ISP_ENTRY)
                self.assertEqual(kb.regs(), (0x5A, 0xA5), "isp_jump() loads A=0x5A, B=0xA5")
                self.assertEqual(kb.get_sfr(IE) & 0x80, 0, "interrupts off when entering the bootloader")

    def test_escape_runs_after_the_clock_is_up(self):
        kb = F65Sim()
        self.addCleanup(kb.close)
        kb.reset_fast()
        self.assertEqual(kb.run_until(kb._a("clock_init"), kb._a("user_boot_escape")), kb._a("clock_init"))

    def test_other_keys_boot_normally(self):
        for keys in [(), ((1, 0),), ((0, 1),), (FN_ANSI,), (FN_USJIS,), (BSPC,)]:
            with self.subTest(keys=keys):
                _, at = self._boot_with(*keys)
                self.assertNotEqual(at, ISP_ENTRY)

    def test_bounce_does_not_escape(self):
        """All 16 samples must read pressed: release after 8."""
        _, at = self._boot_with(ESC, release_after=8)
        self.assertNotEqual(at, ISP_ENTRY)


class KeymapCase(unittest.TestCase):
    FW = F65_FW

    @classmethod
    def setUpClass(cls):
        _need(cls.FW)

    def session(self, mac=False):
        kb = F65Sim(self.FW)
        self.addCleanup(kb.close)
        kb.boot(mac=mac)
        return kb

    def assertTap(self, kb, keys, expected):
        rpt = kb.tap(*keys)
        self.assertIsNotNone(rpt, f"keys {keys} produced no EP1 report")
        self.assertEqual(rpt, expected, f"keys {keys}")


class TestAnsiMatrix(KeymapCase):
    """Every position of the 5 x 16 matrix through the real scan path."""

    FW = F65_ANSI_FW

    def test_every_key(self):
        # Fresh session every few keys: long uCsim sessions slow down on macOS.
        keys = list(ANSI_WIN_KEYS.items())
        for i in range(0, len(keys), 6):
            kb = self.session()
            for key, code in keys[i:i + 6]:
                with self.subTest(key=key):
                    self.assertTap(kb, [key], report(0, code))

    def test_modifiers(self):
        kb = self.session()
        for key, mod in ANSI_WIN_MODS.items():
            with self.subTest(key=key):
                self.assertTap(kb, [key], report(mod))

    def test_empty_positions_send_nothing(self):
        kb = self.session()
        for key in EMPTY:
            kb.matrix.press(*key)
        self.assertEqual(kb.step(), [])

    def test_two_keys_same_row(self):
        kb = self.session()
        kb.matrix.press(1, 1)
        kb.matrix.press(15, 1)
        reps = kb.step()
        self.assertEqual(sorted(reps[-1][2:4]), [0x14, 0x4B])


class TestFnLayer(KeymapCase):
    """The Fn layer in both layouts: grave on Esc, F-keys on the number row, no
    delay, and the settings-reset keys sending nothing."""

    def _both(self):
        for fw in (F65_FW, F65_ANSI_FW):
            kb = F65Sim(fw)
            self.addCleanup(kb.close)
            kb.boot()
            yield fw, kb

    def test_fn_esc_is_grave_and_shift_gives_tilde(self):
        for fw, kb in self._both():
            with self.subTest(fw=Path(fw).name):
                if "usjis" in fw:
                    self.assertEqual(kb.setting("usjis"), 0, "US-JIS is off after a fresh flash")
                self.assertTap(kb, [kb.fn, ESC], report(0, 0x35))
                self.assertTap(kb, [LSFT, kb.fn, ESC], report(MOD_LSFT, 0x35))
                # Right Shift under Fn is the second Fn: ~ with a Shift of the
                # board's own, and never Right Shift itself (test_f65_fnrow.py).
                self.assertTap(kb, [RSFT, kb.fn, ESC], report(MOD_LSFT, 0x35))
                self.assertTap(kb, [kb.fn, RSFT, ESC], report(MOD_LSFT, 0x35))

    def test_esc_alone_is_esc(self):
        kb = self.session()
        self.assertTap(kb, [ESC], report(0, 0x29))

    def test_f_keys_and_nav(self):
        cases = [((c, 0), 0x3A + c - 1) for c in range(1, 13)] + [
            (DEL, 0x49), ((15, 1), 0x35), ((15, 3), 0x4A), ((7, 1), 0x46), ((8, 1), 0x47), ((9, 1), 0x48)]
        for i in range(0, len(cases), 4):          # fresh sessions: long ones slow down on macOS
            kb = self.session()
            for key, code in cases[i:i + 4]:
                with self.subTest(key=key):
                    self.assertTap(kb, [kb.fn, key], report(0, code))

    def test_fn_is_not_deferred(self):
        """The first report after Fn+key carries the key: nothing waits for a
        timeout, and Fn alone sends nothing."""
        kb = self.session()
        self.assertEqual(kb.down(kb.fn), [], "Fn alone sends nothing")
        self.assertEqual(kb.down((1, 0)), [report(0, 0x3A)], "Fn+1 goes out at once as F1")
        self.assertEqual(kb.up((1, 0)), [report()])
        self.assertEqual(kb.down((7, 2)), [report(0, 0x0D)], "a transparent key under Fn is typed at once")
        kb.up((7, 2))
        kb.up(kb.fn)
        self.assertEqual(kb.down(BSPC), [report(0, 0x2A)], "Backspace without Fn")

    def test_fn_backspace_and_fn_v_send_nothing(self):
        for fw, kb in self._both():
            with self.subTest(fw=Path(fw).name):
                reps = []
                for action, key in [("down", kb.fn), ("down", BSPC), ("up", BSPC), ("down", V), ("up", V),
                                    ("up", kb.fn)]:
                    reps += kb.down(key) if action == "down" else kb.up(key)
                self.assertEqual(reps, [], f"Fn+Backspace / Fn+V must not reach the host: {reps}")

    def test_backspace_released_after_fn_sends_nothing(self):
        kb = self.session()
        reps = kb.down(kb.fn) + kb.down(BSPC) + kb.up(kb.fn) + kb.up(BSPC)
        self.assertEqual(reps, [])
        self.assertEqual(kb.down(BSPC), [report(0, 0x2A)], "the next Backspace types normally")


class TestOsKeys(unittest.TestCase):
    """Fn+S selects the Mac base layer and Fn+A the Win one (set keys, as on the
    F65 V2), saved in flash; in both images."""

    def _sessions(self):
        for fw in (F65_FW, F65_ANSI_FW):
            _need(fw)
            kb = F65Sim(fw)
            self.addCleanup(kb.close)
            kb.boot()
            yield fw, kb

    assertTap = KeymapCase.assertTap

    def test_fn_s_mac_fn_a_win_saved_across_reboot(self):
        for fw, kb in self._sessions():
            with self.subTest(fw=Path(fw).name):
                self.assertTap(kb, [(1, 4)], report(MOD_LGUI))
                kb.tap(kb.fn, S)
                self.assertEqual(kb.setting("os_mac"), 1)
                self.assertTap(kb, [(1, 4)], report(MOD_LALT))     # Option
                if kb.off["usjis"] is None:                        # (a mod-tap in usjis)
                    self.assertTap(kb, [(2, 4)], report(MOD_LGUI))  # Command
                self.assertEqual(kb.record()[kb.off["os_mac"]], 1)
                kb.boot()                                          # reset: the settings sector stays
                self.assertEqual(kb.setting("os_mac"), 1, "Mac after a reboot")
                self.assertTap(kb, [(1, 4)], report(MOD_LALT))
                kb.tap(kb.fn, A)
                self.assertEqual(kb.setting("os_mac"), 0)
                self.assertTap(kb, [(1, 4)], report(MOD_LGUI))
                self.assertEqual(kb.record()[kb.off["os_mac"]], 0)
                kb.boot()
                self.assertEqual(kb.setting("os_mac"), 0, "Win after a reboot")

    def test_fn_s_twice_writes_once(self):
        for fw, kb in self._sessions():
            with self.subTest(fw=Path(fw).name):
                kb.tap(kb.fn, S)
                kb.record()                                        # the first save is done
                kb.watch = {kb._a("settings_save"), kb._a("flash_erase")}
                kb.hits = set()
                kb.tap(kb.fn, S)
                kb.settle()
                self.assertEqual(kb.setting("os_mac"), 1)
                self.assertEqual(kb.hits, set(), "no second settings save / flash erase")
                kb.tap(kb.fn, A)
                kb.settle()
                self.assertIn(kb._a("settings_save"), kb.hits, "Fn+A does save")

    def test_fn_m_sends_nothing_and_a_s_type(self):
        for fw, kb in self._sessions():
            with self.subTest(fw=Path(fw).name):
                reps = kb.down(kb.fn) + kb.down(M) + kb.up(M) + kb.up(kb.fn)
                self.assertEqual([r for r in reps if any(r)], [], f"Fn+M: {reps}")
                self.assertEqual(kb.setting("os_mac"), 0)
                self.assertTap(kb, [A], report(0, 0x04))
                self.assertTap(kb, [S], report(0, 0x16))
                self.assertTap(kb, [M], report(0, 0x10))


class TestSettings(KeymapCase):
    """Fn + Backspace held, then Fn + V, puts the settings back to their defaults."""

    def _record(self, kb):
        return kb.record()

    def test_settings_reset_chord(self):
        kb = self.session(mac=True)
        kb.set_setting("usjis", 1)
        self.assertEqual(kb.setting("os_mac"), 1)
        reps = kb.down(kb.fn) + kb.down(BSPC) + kb.down(V) + kb.up(V) + kb.up(BSPC) + kb.up(kb.fn)
        self.assertEqual([r for r in reps if any(r[2:])], [], "the chord types nothing")
        self.assertEqual((kb.setting("os_mac"), kb.setting("usjis")), (0, 0))
        rec = self._record(kb)
        self.assertEqual((rec[kb.off["os_mac"]], rec[kb.off["usjis"]]), (0, 0))
        self.assertTap(kb, [(1, 4)], report(MOD_LGUI))    # back on Win

    def test_mac_mode_survives_a_reboot(self):
        kb = self.session(mac=True)
        self.assertEqual(self._record(kb)[kb.off["os_mac"]], 1)
        kb.matrix.clear()
        kb.boot()                                  # reset: the settings sector stays in flash
        self.assertEqual(kb.setting("os_mac"), 1)
        self.assertTap(kb, [(1, 4)], report(MOD_LALT))

    def test_fn_v_alone_does_not_reset(self):
        kb = self.session(mac=True)
        kb.tap(kb.fn, V)
        self.assertEqual(kb.setting("os_mac"), 1)

    def test_backspace_without_fn_does_not_arm(self):
        kb = self.session(mac=True)
        kb.down(BSPC)
        kb.down(kb.fn)
        kb.down(V)
        kb.matrix.clear()
        kb.step()
        self.assertEqual(kb.setting("os_mac"), 1)


class TestStuckKeys(KeymapCase):
    """A key must go up on the host with the keycode it went down with, whatever
    Fn did in between (keycodes are latched per matrix position)."""

    BASE = {"V": ((4, 3), 0x19), "1": ((1, 0), 0x1E), "Del": ((15, 0), 0x4C), "Esc": ((0, 0), 0x29)}
    UNDER_FN = {"1": 0x3A, "Del": 0x49, "Esc": 0x35}

    def _key_then_fn(self, fw, names):
        """key down, Fn down, key up, Fn up: the key must go up at key up."""
        for i in range(0, len(names), 2):
            kb = F65Sim(fw)
            self.addCleanup(kb.close)
            kb.boot()
            for name in names[i:i + 2]:
                pos, code = self.BASE[name]
                with self.subTest(fw=Path(fw).name, key=name):
                    reps = kb.down(pos)
                    self.assertEqual(reps[-1:], [report(0, code)])
                    kb.down(kb.fn)
                    reps = kb.up(pos)
                    self.assertEqual(reps[-1:], [report()], f"{name} must go up at its release")
                    kb.up(kb.fn)
                    kb.matrix.clear()

    def _fn_then_key(self, fw, names):
        """Fn down, key down, Fn up, key up: the Fn-layer key must go up with Fn."""
        for i in range(0, len(names), 2):
            kb = F65Sim(fw)
            self.addCleanup(kb.close)
            kb.boot()
            for name in names[i:i + 2]:
                pos, _ = self.BASE[name]
                with self.subTest(fw=Path(fw).name, key=name):
                    kb.down(kb.fn)
                    reps = kb.down(pos)
                    self.assertEqual(reps[-1:], [report(0, self.UNDER_FN[name])])
                    reps = kb.up(kb.fn)
                    self.assertEqual(reps[-1:], [report()], "releasing Fn releases the Fn-layer key")
                    self.assertEqual(kb.up(pos), [], "and the key's own release sends nothing more")
                    kb.matrix.clear()

    def test_key_then_fn_ansi(self):
        self._key_then_fn(F65_ANSI_FW, ["V", "1", "Del", "Esc"])

    def test_key_then_fn_usjis(self):
        self._key_then_fn(F65_FW, ["V", "1", "Del", "Esc"])

    def test_fn_then_key_ansi(self):
        self._fn_then_key(F65_ANSI_FW, ["1", "Del", "Esc"])

    def test_fn_then_key_usjis(self):
        self._fn_then_key(F65_FW, ["1", "Del", "Esc"])

    def test_tab_across_fn_usjis(self):
        """Tab is US-JIS on/off under Fn in usjis: a Tab held across Fn still
        goes up as Tab, and Fn+Tab released after Fn toggles once and types nothing."""
        kb = self.session()
        tab = (0, 1)
        self.assertEqual(kb.down(tab)[-1:], [report(0, 0x2B)])
        kb.down(kb.fn)
        self.assertEqual(kb.up(tab)[-1:], [report()], "Tab must go up at its release")
        self.assertEqual(kb.setting("usjis"), 0, "a Tab that went down without Fn toggles nothing")
        reps = kb.up(kb.fn) + kb.down(kb.fn) + kb.down(tab) + kb.up(kb.fn) + kb.up(tab)
        self.assertEqual([r for r in reps if any(r)], [], f"Fn+Tab types nothing: {reps}")
        self.assertEqual(kb.setting("usjis"), 1, "toggled once")


class TestRemoteWakeup(KeymapCase):
    """The board stays awake while the bus is suspended; a key that goes down
    then signals resume (USBCON.WKUP) if the host enabled remote wakeup."""

    SET_FEATURE_REMOTE_WAKEUP = [0x00, 0x03, 0x01, 0x00, 0x00, 0x00, 0x00, 0x00]

    def _suspended(self, allow_wakeup):
        kb = self.session()
        if allow_wakeup:
            kb.setup_packet(self.SET_FEATURE_REMOTE_WAKEUP)
            self.assertEqual(kb.get_bit("usb", "usb_remote_wakeup"), 1, "SET_FEATURE(DEVICE_REMOTE_WAKEUP) taken")
        # The sim raises the USB interrupt only for SETUPIF, so the SUSPIF branch
        # is stood in for by setting usb_suspended directly.
        kb.set_bit("usb", "usb_suspended", 1)
        kb.brk(kb._a("kb_update"))
        kb.run()
        kb.run()                                   # kb_update has seen the suspend
        kb.cmd("delete")
        addr = kb._xdata_static("tick", "scans")   # let the bus idle for longer than the guard
        lo, hi = kb.get_xram(addr, 2)
        now = (lo | (hi << 8)) + 100
        kb.cmd("set mem xram 0x%x 0x%02x 0x%02x" % (addr, now & 0xFF, (now >> 8) & 0xFF))
        self.assertEqual(kb.get_sfr(USBCON) & WKUP, 0, "no resume before a key")
        return kb

    def test_key_press_signals_resume(self):
        kb = self._suspended(allow_wakeup=True)
        kb.matrix.press(1, 2)
        kb.step()
        self.assertEqual(kb.get_sfr(USBCON) & WKUP, WKUP, "USBCON.WKUP after a key in suspend")

    def test_no_resume_when_the_host_did_not_allow_it(self):
        kb = self._suspended(allow_wakeup=False)
        kb.matrix.press(1, 2)
        kb.step()
        self.assertEqual(kb.get_sfr(USBCON) & WKUP, 0)

    def test_no_resume_while_the_host_resumes(self):
        """Review F2: once the host has started resume signalling (RESMIF), a
        key does not set WKUP on top of it; the next suspend re-arms it."""
        kb = self.session()
        kb.setup_packet(self.SET_FEATURE_REMOTE_WAKEUP)
        for flag in (0x02, 0x04):                  # SUSPIF, then RESMIF, through the USB interrupt
            kb.set_sfr(0x92, kb.get_sfr(0x92) | flag)
            kb.brk(kb._a("kb_update"))
            kb.run()
            kb.run()
            kb.cmd("delete")
            self.assertEqual(kb.get_sfr(0x92) & flag, 0, "the ISR took it")
        self.assertEqual(kb.get_bit("usb", "usb_suspended"), 1)
        addr = kb._xdata_static("tick", "scans")
        lo, hi = kb.get_xram(addr, 2)
        now = (lo | (hi << 8)) + 100
        kb.cmd("set mem xram 0x%x 0x%02x 0x%02x" % (addr, now & 0xFF, (now >> 8) & 0xFF))
        kb.matrix.press(1, 2)
        kb.step(96)
        self.assertEqual(kb.get_sfr(USBCON) & WKUP, 0, "no WKUP during the host's resume")

    def test_no_resume_when_not_suspended(self):
        kb = self.session()
        kb.setup_packet(self.SET_FEATURE_REMOTE_WAKEUP)
        kb.tap((1, 2))
        self.assertEqual(kb.get_sfr(USBCON) & WKUP, 0)


class TestPinsAfterBoot(unittest.TestCase):
    """Directions, pull-ups and the output latches after boot are the stock V1 init."""

    def test_pins_match_the_stock_init(self):
        for fw in (F65_FW, F65_ANSI_FW):
            _need(fw)
            kb = F65Sim(fw)
            self.addCleanup(kb.close)
            kb.boot()
            kb.brk(kb._a("usb_task"))                # past the first kb_update_switches()
            kb.run()
            kb.cmd("delete")
            for port in range(8):
                with self.subTest(fw=Path(fw).name, port=port):
                    cr, pcr = kb.get_sfr(CR_SFR[port]), kb.get_sfr(PCR_SFR[port])
                    if port == 0:
                        # P0.2, the radio's send request, is an output (high) only
                        # until the first frame has gone out; after that it is
                        # released to an input, as the stock does (0xA2A6, 0x0F88).
                        self.assertEqual(cr & 0x04, 0, "P0.2 released after the boot names")
                        cr |= 0x04
                    self.assertEqual(cr, STOCK_CR[port], f"P{port}CR")
                    self.assertEqual(pcr, STOCK_PCR[port], f"P{port}PCR")
                    # outputs only: an input bit reads its pin, not the latch
                    mask = cr & (0xBF if port == 7 else 0xFF)   # P7.6 follows !P4.4
                    self.assertEqual(kb.get_sfr(PORT_SFR[port]) & mask, STOCK_LATCH[port] & mask, f"P{port} outputs")
            p4, p7 = kb.get_sfr(PORT_SFR[4]), kb.get_sfr(PORT_SFR[7])
            self.assertEqual((p7 >> 6) & 1, 1 - ((p4 >> 4) & 1), "P7.6 = !P4.4, as the stock main loop")


class TestFlashRange(KeymapCase):
    """A settings save rewrites the settings sector and nothing else."""

    def _save_and_diff(self, kb, action):
        before = kb.rom_image()
        kb.watch, kb.hits = {kb._a("settings_save")}, set()
        action()
        kb.settle()                                # the save is done
        self.assertIn(kb._a("settings_save"), kb.hits, "the action saved the settings")
        after = kb.rom_image()
        return [a for a in range(0x10000) if before[a] != after[a]]

    def test_saves_touch_only_the_settings_sector(self):
        def reset(kb):
            kb.set_setting("usjis", 1)
            kb.set_setting("os_mac", 1)
            kb.down(kb.fn)
            kb.down(BSPC)
            kb.down(V)
            kb.matrix.clear()
            kb.step()
        for name, action in [("Fn+S", lambda kb: kb.tap(kb.fn, S)),
                             ("Fn+Tab", lambda kb: kb.tap(kb.fn, (0, 1))),
                             ("reset", reset)]:
            with self.subTest(save=name):
                kb = self.session()                # fresh: long sessions slow down
                changed = self._save_and_diff(kb, lambda: action(kb))
                self.assertTrue(changed, "the save wrote something")
                outside = [a for a in changed if not NVM_BASE <= a < NVM_END]
                self.assertEqual(outside, [], "writes outside 0x%04x-0x%04x: %s" % (
                    NVM_BASE, NVM_END - 1, " ".join("%04x" % a for a in outside[:16])))


class TestStockKeymap(unittest.TestCase):
    """The port against the stock V1 image itself (factory tables, not a
    readout): keymap tables at 0xB400/0xB600/0xB800/0xBA00 (index slot*6 + row
    bit, 4 bytes per key), the column jump table at 0x6ABD, the row read at
    0x6B81, and the boot-escape key. SMK_F65_STOCK_IMAGE = the official
    AULA_F65_V1_FN_Ctrl_firmware.bin."""

    KBDEF = REPO_ROOT / "src" / "keyboards" / "aula-f65-v1" / "kbdef.h"
    PORT_NAMES = {"P4": P4, "P5": P5, "P6": P6, "P7": P7}

    @classmethod
    def setUpClass(cls):
        img = os.environ.get("SMK_F65_STOCK_IMAGE")
        if not img or not Path(img).exists():
            skip_or_fail("set SMK_F65_STOCK_IMAGE to the official AULA_F65_V1_FN_Ctrl_firmware.bin "
                         "(from the vendor updater; meson: -Df65_stock_image=)")
        cls.stock = Path(img).read_bytes()
        assert len(cls.stock) == 0xF000, "the official image is 61440 bytes (0x0000-0xEFFF)"
        text = cls.KBDEF.read_text()
        cls.kbdef = dict(re.findall(r"^#define\s+(\w+)\s+(\S+)", text, re.M))

    def _entry(self, base, slot, bit):
        a = base + (slot * 6 + bit) * 4
        return int.from_bytes(self.stock[a:a + 4], "big")

    def _pin(self, macro):
        m = re.fullmatch(r"(P\d)_(\d)", self.kbdef[macro])
        return (self.PORT_NAMES[m.group(1)], int(m.group(2)))

    def _port_keymaps(self, fw):
        _need(fw)
        data = read_ihex(fw)
        base = load_symbols(Path(fw).with_suffix(".map"))["keymaps"]
        def key(layer, row, col):
            a = base + ((layer * 5 + row) * 16 + col) * 2
            return data[a] | (data[a + 1] << 8)
        return key

    @staticmethod
    def _expected(entry, fn_layer):
        """The smk keycode for a stock entry, or None for a stock function."""
        if entry == 0:
            return 0
        if entry == 0x0D000000:
            return 0x5220 | fn_layer                       # MO(Fn layer)
        if entry & 0xFFFFFF00 == 0:
            return entry                                    # HID usage
        mods = (entry >> 16) & 0xFF
        if entry & 0xFF00FFFF == 0 and bin(mods).count("1") == 1:
            return 0xE0 + mods.bit_length() - 1              # one modifier
        return None

    def test_column_and_row_pins(self):
        expect(self, self.stock, 0x6AB6, "906abdf8282873")
        for slot in range(16):
            t = (self.stock[0x6ABD + 3 * slot + 1] << 8) | self.stock[0x6ABD + 3 * slot + 2]
            clr = self.stock[t + 2:t + 4]
            self.assertEqual(clr[0], 0xC2, f"slot {slot}: CLR bit")
            b = clr[1]
            stock_pin = ({0xC0: P6, 0x88: P5, 0xB0: P4, 0xF8: P7}[b & 0xF8], b & 7)
            with self.subTest(slot=slot):
                self.assertEqual(COL_PIN[slot], stock_pin, "tests' COL_PIN")
                self.assertEqual(self._pin("KB_C%d" % slot), stock_pin, "kbdef.h KB_C%d" % slot)
        expect(self, self.stock, 0x6B81, "e58825e05430ffe5f8540f4f44c1")
        stock_rows = [(P7, 1), (P7, 2), (P7, 3), (P5, 3), (P5, 4)]   # row bits 1-5; bit 0 (P7.0) forced 1
        for row, pin in enumerate(stock_rows):
            self.assertEqual(ROW_PIN[row], pin)
            self.assertEqual(self._pin("KB_R%d" % row), pin)

    def test_ansi_base_layers_are_the_stock_tables(self):
        key = self._port_keymaps(F65_ANSI_FW)
        for layer, base, fn_layer in [(0, 0xB400, 2), (1, 0xB600, 3)]:
            keys = 0
            for slot in range(18):
                for bit in range(6):
                    e = self._entry(base, slot, bit)
                    if bit == 0 or slot >= 16:
                        if slot >= 16:
                            self.assertEqual(e, 0, f"stock slot {slot} carries no key")
                        continue
                    with self.subTest(table=hex(base), slot=slot, bit=bit):
                        if (slot, bit) == (12, 3):
                            self.assertEqual(e, 0x32, "stock Non-US # (no key on the ANSI board)")
                            self.assertEqual(key(layer, bit - 1, slot), 0)
                            continue
                        self.assertEqual(key(layer, bit - 1, slot), self._expected(e, fn_layer))
                        keys += e != 0
            self.assertEqual(keys, 67, f"{hex(base)}: 67 keys")

    def test_fn_layers_keep_the_stock_keys(self):
        """Where the stock Fn table has a plain key, the port has the same key,
        except Esc (grave by the user's choice; stock: factory reset) and PgDn
        (stock Shift+grave; tilde is Shift+Fn+Esc here)."""
        exceptions = {(0, 1): 0x35, (15, 3): 0x0001}         # port keycodes there (KC_TRNS = 1)
        for fw in (F65_ANSI_FW, F65_FW):
            key = self._port_keymaps(fw)
            for layer, base in [(2, 0xB800), (3, 0xBA00)]:
                fr1 = key(layer, 0, 1)   # FR_1: the number row is F-keys or media by OS mode (kb.c)
                for slot in range(16):
                    for bit in range(1, 6):
                        e = self._entry(base, slot, bit)
                        with self.subTest(fw=Path(fw).name, table=hex(base), slot=slot, bit=bit):
                            if (slot, bit) in exceptions:
                                self.assertEqual(key(layer, bit - 1, slot), exceptions[(slot, bit)])
                            elif bit == 1 and 0x3A <= e <= 0x45:   # F1..F12 on the number row
                                self.assertEqual(key(layer, 0, slot), fr1 + (e - 0x3A))
                            elif e and e & 0xFFFFFF00 == 0:
                                self.assertEqual(key(layer, bit - 1, slot), e)

    def test_usjis_differs_only_where_intended(self):
        """usjis = ansi except Caps/Left Ctrl swapped, the mod-tap left of Space,
        and the key right of Space / Fn swapped with Right Ctrl."""
        ansi, usjis = self._port_keymaps(F65_ANSI_FW), self._port_keymaps(F65_FW)
        intended = {(0, 2), (0, 4), (2, 4), (9, 4), (10, 4)}
        for layer in (0, 1):
            for row in range(5):
                for col in range(16):
                    if (col, row) not in intended:
                        self.assertEqual(usjis(layer, row, col), ansi(layer, row, col), (layer, col, row))

    def test_boot_escape_key_is_stock_esc(self):
        col, row = int(self.kbdef["BOOT_ESCAPE_KEY_COL"]), int(self.kbdef["BOOT_ESCAPE_KEY_ROW"])
        self.assertEqual(self._entry(0xB400, col, row + 1), 0x29, "stock Esc at that slot and row bit")
        self.assertEqual(self._pin("KB_C%d" % col), (P6, 0))
        self.assertEqual(self._pin("KB_R%d" % row), (P7, 1))
        for fw in (F65_ANSI_FW, F65_FW):
            self.assertEqual(self._port_keymaps(fw)(0, row, col), 0x29)


def expect(tc, img, addr, hexbytes):
    tc.assertEqual(img[addr:addr + len(hexbytes) // 2].hex(), hexbytes, f"stock bytes at {addr:#06x}")


class TestStockBootloaderChain(unittest.TestCase):
    """The flash as it will look on the board: the firmware converted by
    sinowisp to the physical layout (reset vector -> 0xF000, the firmware's own
    vector at 0xEFFB) plus the F65's own ISP bootloader at 0xF000-0xFFFF."""

    @classmethod
    def setUpClass(cls):
        _need(F65_FW)
        stock = os.environ.get("SMK_F65_STOCK_DUMP")
        if not stock or not Path(stock).exists():
            skip_or_fail("set SMK_F65_STOCK_DUMP to a full F65 readout (meson: -Df65_stock_dump=)")
        sinowisp = shutil.which("sinowisp")
        if not sinowisp:
            skip_or_fail("sinowisp not on PATH (its offline `convert` builds the flash image)")
        cls.tmp = tempfile.TemporaryDirectory()
        tmp = Path(cls.tmp.name)
        fw_bin = tmp / "fw_jtag.bin"
        subprocess.run([sinowisp, "convert", "-d", "aula-f75", "--direction", "to_jtag",
                        "--output_format", "bin", F65_FW, str(fw_bin)],
                       check=True, capture_output=True)
        image = bytearray(fw_bin.read_bytes().ljust(0xF000, b"\xff"))
        image += Path(stock).read_bytes()[0xF000:0x10000]
        assert len(image) == 0x10000
        cls.image = image
        cls.hex_path = tmp / "aula-f65-v1_chain_smk.hex"
        shutil.copy(Path(F65_FW).with_suffix(".map"), cls.hex_path.with_suffix(".map"))
        rst = Path(F65_FW).with_suffix(".ihx.p")
        if rst.exists():
            shutil.copytree(rst, cls.hex_path.with_suffix(".ihx.p"))
        with open(cls.hex_path, "w") as f:
            for addr in range(0, 0x10000, 16):
                chunk = image[addr:addr + 16]
                rec = bytes([16, addr >> 8, addr & 0xFF, 0]) + chunk
                f.write(":%s%02X\n" % (rec.hex().upper(), (-sum(rec)) & 0xFF))
            f.write(":00000001FF\n")

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def setUp(self):
        self.kb = F65Sim(firmware=str(self.hex_path))

    def tearDown(self):
        self.kb.close()

    def test_image_layout(self):
        self.assertEqual(self.image[0:3], bytes([0x02, 0xF0, 0x00]), "reset vector must enter the bootloader")
        self.assertEqual(self.image[0xEFFB], 0x02, "firmware-enabled marker (LJMP) at 0xEFFB")

    def test_power_on_runs_bootloader_then_firmware(self):
        kb = self.kb
        kb.reset_fast()
        self.assertEqual(kb.run_until(BL_MARKER_OK, BL_NO_MARKER), BL_MARKER_OK)
        self.assertEqual(kb.run_until(ISP_ENTRY, kb._a("kb_update_switches")), kb._a("kb_update_switches"))

    def test_esc_held_is_accepted_by_the_stock_bootloader(self):
        kb = self.kb
        kb.reset_fast(p7=0xFF & ~0x02)        # R0 (P7.1) low: the static level while C0 is scanned
        stop = kb.run_until(ISP_KEYS_OK, ISP_KEYS_BAD, kb._a("kb_update_switches"))
        self.assertEqual(stop, ISP_KEYS_OK, "the bootloader must accept isp_jump() and start ISP mode")

    def test_missing_marker_stays_in_bootloader(self):
        kb = self.kb
        kb.reset_fast()
        kb.cmd("set mem rom 0xeffb 0xff")
        stop = kb.run_until(BL_MARKER_OK, BL_NO_MARKER, kb._a("kb_update_switches"))
        self.assertEqual(stop, BL_NO_MARKER)


if __name__ == "__main__":
    unittest.main()
