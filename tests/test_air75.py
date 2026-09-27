#!/usr/bin/env python3
"""nuphy-air75 board tests, driven through the patched uCsim simulator.

Covers what differs from the nuphy-air60: the boot-time ISP escape (Esc held at
power-up), the 6-row matrix with its F-row on P7.0, and the keymap. Run from the
repo root after building the firmware:

    meson compile -C build nuphy-air75_usjis_smk.hex nuphy-air75_ansi_smk.hex
    python3 -m unittest discover -s tests -p test_air75.py    # or: python3 tests/test_air75.py

SMK_AIR75_FIRMWARE overrides the .hex. TestStockBootloaderChain also needs the
stock ISP bootloader, which this repository does not ship: point
SMK_AIR75_STOCK_JTAG at a physical-layout dump of an Air75 (only 0xF000-0xFFFF
is used) and keep sinowisp on PATH; without them those tests are skipped.
"""

import os
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from sim import REPO_ROOT, Sim, load_symbols, skip_or_fail
from devices import Air60Sim, KeyMatrix, P5, P7

# The usjis layout carries every feature; most tests run on it. The ansi
# layout (no US-JIS, no mod-taps, stock Caps/Ctrl) has its own key map tests.
AIR75_FW = os.environ.get("SMK_AIR75_FIRMWARE") or str(REPO_ROOT / "build" / "nuphy-air75_usjis_smk.hex")
AIR75_ANSI_FW = os.environ.get("SMK_AIR75_ANSI_FIRMWARE") or str(REPO_ROOT / "build" / "nuphy-air75_ansi_smk.hex")

ISP_ENTRY = 0xFF00
ISP_KEYS_OK = 0xFF0A      # stock bootloader: A/B keys matched, ISP mode starts
ISP_KEYS_BAD = 0xFF36     # stock bootloader: keys wrong, back to the firmware
BL_MARKER_OK = 0xF053     # stock bootloader: LJMP found at 0xEFFB, run the firmware
BL_NO_MARKER = 0xF029     # stock bootloader: no LJMP at 0xEFFB, stay in ISP mode
IE = 0xA8

# Rows R0-R3 = P7.0-P7.3, R4-R5 = P5.3-P5.4 (src/keyboards/nuphy-air75/kbdef.h).
ROW_PIN = {0: (P7, 0), 1: (P7, 1), 2: (P7, 2), 3: (P7, 3), 4: (P5, 3), 5: (P5, 4)}

# HID usages / modifier bits used below.
MOD_LCTL, MOD_LSFT, MOD_LALT, MOD_LGUI = 0x01, 0x02, 0x04, 0x08
MOD_RCTL, MOD_RSFT, MOD_RALT, MOD_RGUI = 0x10, 0x20, 0x40, 0x80


def _need_firmware():
    if not Path(AIR75_FW).exists():
        skip_or_fail(f"no nuphy-air75 firmware at {AIR75_FW}")
    reason = Sim(AIR75_FW).available()   # not Sim(): that looks for build/'s Air60 image
    if reason:
        skip_or_fail(reason)


class Air75KeyMatrix(KeyMatrix):
    """KeyMatrix with the Air75 row pins, plus the OS switch (P5.6, 1 = Mac)."""

    def __init__(self):
        super().__init__()
        self.os_mac = True

    def inject(self, sess):
        port_cache = {}
        low_rows = set()
        for (c, r) in self.pressed:
            if self._col_driven_low(sess, c, port_cache):
                low_rows.add(r)
        level = {P5: 0xFF, P7: 0xFF}
        if not self.usb_mode:
            level[P5] &= ~0x20
        if not self.os_mac:
            level[P5] &= ~0x40
        for r in low_rows:
            port, bit = ROW_PIN[r]
            level[port] &= ~(1 << bit)
        sess.set_pin(P7, level[P7])
        sess.set_pin(P5, level[P5])


class Air75Sim(Air60Sim):
    def __init__(self, firmware=None):
        super().__init__(firmware or AIR75_FW)
        self.matrix = Air75KeyMatrix()

    def _xdata_static(self, module, name):
        """As Air60Sim's, but a release build's .map has no static symbols (SDCC
        writes them only with --debug, which also changes the code), so fall back
        to the linker's relocated listing <image>.ihx.p/<module>.rst."""
        try:
            return super()._xdata_static(module, name)
        except KeyError:
            rst = Path(self.firmware).with_suffix(".ihx.p") / (module + ".rst")
            if rst.exists():
                pat = re.compile(r"^\s+([0-9A-F]{6})\s+\d+ _%s:$" % re.escape(name))
                with open(rst) as f:
                    for line in f:
                        m = pat.match(line)
                        if m:
                            return int(m.group(1), 16)
            raise

    def reset_fast(self, p5=0xFF, p7=0xFF):
        """Reset with the calibrated busy-wait delays stubbed out, and present the
        given external pin levels on P5/P7 from the first instruction on."""
        self.cmd("reset")
        self.cmd("set mem rom 0x%x 0x22" % self._a("delay_us"))   # RET
        self.cmd("set mem rom 0x%x 0x22" % self._a("delay_ms"))   # RET
        self.set_pin(P5, p5)
        self.set_pin(P7, p7)

    def boot(self, usb=True, mac=True):
        self.matrix.usb_mode = usb
        self.matrix.os_mac = mac
        p5 = 0xFF
        if not usb:
            p5 &= ~0x20
        if not mac:
            p5 &= ~0x40
        self.reset_fast(p5=p5)
        self.brk(self._a("kb_update_switches"))
        self.run()
        self.cmd("delete")

    def run_until(self, *addrs):
        for a in addrs:
            self.brk(a)
        out = self.run()
        self.cmd("delete")
        return self.stopped_at(out)

    def regs(self):
        out = self.cmd("info registers")
        acc = re.findall(r"\bACC= 0x([0-9a-fA-F]+)", out)
        b = re.findall(r"\bB= 0x([0-9a-fA-F]+)", out)
        return (int(acc[-1], 16) if acc else None, int(b[-1], 16) if b else None)

    def tap(self, *keys):
        """Press keys (col, row) one after another, each held while the next goes
        down (so a layer key takes effect first, as it would by hand). Return the
        last EP1 report seen, then release everything and let the release
        report go out."""
        reps = []
        for k in keys:
            self.matrix.press(*k)
            reps = self.scan_for_report()
        pressed = reps[-1] if reps else None
        self.matrix.clear()
        self.scan_for_report()
        return pressed


def read_ihex(path):
    """{address: byte} for every data record of an Intel HEX file."""
    data = {}
    base = 0
    with open(path) as f:
        for line in f:
            rec = bytes.fromhex(line.strip()[1:])
            n, addr, typ = rec[0], (rec[1] << 8) | rec[2], rec[3]
            if typ == 0:
                for i in range(n):
                    data[base + addr + i] = rec[4 + i]
            elif typ == 4:
                base = ((rec[4] << 8) | rec[5]) << 16
    return data


class TestImage(unittest.TestCase):
    """Checks on the .hex that will be written with `sinowisp write --force`."""

    FLASH_CFG_ADDR = 0xEC00   # settings sector; the marker sector and bootloader follow

    @classmethod
    def setUpClass(cls):
        if not Path(AIR75_FW).exists():
            skip_or_fail(f"no nuphy-air75 firmware at {AIR75_FW}")
        cls.data = read_ihex(AIR75_FW)

    def test_code_stays_below_the_settings_sector(self):
        top = max(self.data)
        self.assertLess(top, self.FLASH_CFG_ADDR,
                        f"code reaches 0x{top:04x}; the settings, marker and bootloader start at 0x{self.FLASH_CFG_ADDR:04x}")

    def test_reset_vector_is_an_ljmp(self):
        self.assertEqual(self.data.get(0), 0x02, "sinowisp relocates the LJMP at 0x0000 to 0xEFFB")

    def test_keyboard_descriptor_carries_apple_fn(self):
        """Five keycode slots, then Usage Page 0xFF / Usage 0x03 (one byte), as in
        the stock firmware's descriptor at 0x6783."""
        sym = load_symbols(Path(AIR75_FW).with_suffix(".map"))
        base = sym["hid_report_desc_keyboard"]
        desc = bytes(self.data.get(base + i, 0) for i in range(80))
        keys = bytes([0x75, 0x08, 0x95, 0x05, 0x81, 0x00])
        fn = bytes([0x05, 0xFF, 0x09, 0x03, 0x75, 0x08, 0x95, 0x01, 0x81, 0x02])
        self.assertIn(keys + fn, desc, f"descriptor: {desc.hex(' ')}")

    def test_boot_escape_is_linked(self):
        sym = load_symbols(Path(AIR75_FW).with_suffix(".map"))
        self.assertIn("user_boot_escape", sym)
        self.assertIn("isp_jump", sym)


class TestBootEscape(unittest.TestCase):
    """Esc (C0 x R0) held at power-up must reach the ISP bootloader entry with
    the bootloader's keys loaded, before USB is initialised; otherwise the boot
    must continue normally."""

    @classmethod
    def setUpClass(cls):
        _need_firmware()

    def setUp(self):
        self.kb = Air75Sim()

    def tearDown(self):
        self.kb.close()

    def test_esc_held_jumps_to_isp_before_usb_init(self):
        kb = self.kb
        kb.reset_fast(p7=0xFF & ~0x01)          # R0 low: Esc (or any F-row key) held
        stop = kb.run_until(ISP_ENTRY, kb._a("usb_init"), kb._a("kb_update_switches"))
        self.assertEqual(stop, ISP_ENTRY, "Esc held at power-up must jump to 0xFF00 first")
        acc, b = kb.regs()
        self.assertEqual((acc, b), (0x5A, 0xA5), "isp_jump() loads A=0x5A, B=0xA5 for the bootloader")
        self.assertEqual(kb.get_sfr(IE) & 0x80, 0, "interrupts must be off when entering the bootloader")

    def test_escape_runs_after_the_clock_is_up(self):
        """The bootloader's 0xFF00 entry does not set the clock, so the escape
        must come after clock_init()."""
        kb = self.kb
        kb.reset_fast(p7=0xFF & ~0x01)
        stop = kb.run_until(kb._a("clock_init"), kb._a("user_boot_escape"))
        self.assertEqual(stop, kb._a("clock_init"))
        stop = kb.run_until(kb._a("user_boot_escape"), ISP_ENTRY)
        self.assertEqual(stop, kb._a("user_boot_escape"))

    def test_no_key_boots_normally(self):
        kb = self.kb
        kb.reset_fast()
        stop = kb.run_until(ISP_ENTRY, kb._a("kb_update_switches"))
        self.assertEqual(stop, kb._a("kb_update_switches"))

    def test_bounce_does_not_escape(self):
        """All 16 samples must read pressed: release after 8 and boot on."""
        kb = self.kb
        kb.reset_fast(p7=0xFF & ~0x01)
        stop = kb.run_until(kb._a("user_boot_escape"))
        self.assertEqual(stop, kb._a("user_boot_escape"))
        kb.brk(kb._a("delay_us"))              # one call per sample, before the read
        for _ in range(8):
            kb.run()
        kb.set_pin(P7, 0xFF)                   # released before the 9th sample
        kb.cmd("delete")
        stop = kb.run_until(ISP_ENTRY, kb._a("kb_update_switches"))
        self.assertEqual(stop, kb._a("kb_update_switches"))


class TestMatrixAndKeymap(unittest.TestCase):
    """Real scan path (Timer2 ISR -> matrix scan -> HID report on EP1) with the
    Air75 matrix emulated test-side. Positions are from the stock keymap."""

    @classmethod
    def setUpClass(cls):
        _need_firmware()

    def _session(self, mac):
        kb = Air75Sim()
        self.addCleanup(kb.close)
        kb.boot(usb=True, mac=mac)
        kb.mark_usb_configured()
        return kb

    def assertKey(self, kb, key, mods, code):
        keys = (key,) if isinstance(key[0], int) else key
        rpt = kb.tap(*keys)
        self.assertIsNotNone(rpt, f"key {key} produced no EP1 report")
        self.assertEqual((rpt[0], rpt[2]), (mods, code), f"key {key}: report {rpt}")

    def test_win_layer_keys(self):
        kb = self._session(mac=False)
        cases = [
            ((0, 0), 0x29), ((1, 0), 0x3A), ((12, 0), 0x45), ((13, 0), 0x46), ((14, 0), 0x49), ((15, 0), 0x4C),
            ((0, 1), 0x35), ((1, 1), 0x1E), ((13, 1), 0x2A), ((15, 1), 0x4B),
            ((0, 2), 0x2B), ((1, 2), 0x14), ((13, 2), 0x31), ((15, 2), 0x4E),
            ((1, 3), 0x04), ((11, 3), 0x34), ((13, 3), 0x28), ((15, 3), 0x4A),
            ((1, 4), 0x1D), ((10, 4), 0x38), ((14, 4), 0x52), ((15, 4), 0x4D),
            ((5, 5), 0x2C), ((13, 5), 0x50), ((14, 5), 0x51), ((15, 5), 0x4F),
            ((0, 5), 0x39),                                   # Caps Lock, swapped with Left Ctrl
        ]
        for key, code in cases:
            with self.subTest(key=key):
                self.assertKey(kb, key, 0x00, code)

    def test_win_layer_modifiers(self):
        kb = self._session(mac=False)
        # (2, 5) and (8, 5) are the IME mod-taps, see TestImeKeys; Left Ctrl is on (0, 3).
        cases = [((0, 4), MOD_LSFT), ((13, 4), MOD_RSFT), ((0, 3), MOD_LCTL), ((1, 5), MOD_LGUI),
                 ((10, 5), MOD_RCTL)]
        for key, mod in cases:
            with self.subTest(key=key):
                self.assertKey(kb, key, mod, 0x00)

    def test_mac_layer_swaps_modifiers(self):
        kb = self._session(mac=True)
        for key, mod in [((1, 5), MOD_LALT), ((0, 3), MOD_LCTL)]:
            with self.subTest(key=key):
                self.assertKey(kb, key, mod, 0x00)

    def test_mac_fn_gives_f_keys(self):
        kb = self._session(mac=True)
        self.assertKey(kb, ((9, 5), (1, 0)), 0x00, 0x3A)    # Fn + F1 position -> F1


class TestAppleFn(unittest.TestCase):
    """Mac mode: Fn is the Apple fn key (byte 7 of the keyboard report), sent
    only together with a key so that a lone Fn press cannot trigger macOS's
    Globe action. Win mode never sets it."""

    FN, F1, F5, RIGHT = (9, 5), (1, 0), (5, 0), (15, 5)

    @classmethod
    def setUpClass(cls):
        _need_firmware()

    def _session(self, mac):
        kb = Air75Sim()
        self.addCleanup(kb.close)
        kb.boot(usb=True, mac=mac)
        kb.mark_usb_configured()
        return kb

    @staticmethod
    def _run(kb, hits=120):
        """Run the scan for a fixed number of row reads; return the EP1 reports
        that appeared meanwhile."""
        before = len(kb.ep1_reports())
        kb.brk(kb._a("user_matrix_read_rows"))
        for _ in range(hits):
            kb.run()
            kb.matrix.inject(kb)
        kb.cmd("delete")
        return kb.ep1_reports()[before:]

    def test_fn_f1_announces_fn_then_sends_the_key(self):
        kb = self._session(mac=True)
        kb.matrix.press(*self.FN)
        self.assertEqual(self._run(kb), [], "Fn alone must not send anything")
        kb.matrix.press(*self.F1)
        self.assertEqual(self._run(kb), [[0, 0, 0, 0, 0, 0, 0, 1], [0, 0, 0x3A, 0, 0, 0, 0, 1]])
        kb.matrix.release(*self.F1)
        self.assertEqual(self._run(kb), [[0, 0, 0, 0, 0, 0, 0, 1]])
        kb.matrix.release(*self.FN)
        self.assertEqual(self._run(kb), [[0, 0, 0, 0, 0, 0, 0, 0]])

    def test_fn_with_a_lighting_key_sends_no_fn(self):
        kb = self._session(mac=True)
        kb.matrix.press(*self.FN)
        kb.matrix.press(*self.RIGHT)             # FX_NEXT: handled on the board, no HID key
        reps = self._run(kb, hits=200)
        kb.matrix.clear()
        reps += self._run(kb)
        self.assertFalse([r for r in reps if r[7]], f"no report may carry fn: {reps}")

    def test_mac_f5_is_backlight_and_fn_f5_is_f5(self):
        kb = self._session(mac=True)
        kb.matrix.press(*self.F5)
        self.assertEqual(self._run(kb), [], "F5 alone dims the backlight, no HID key")
        kb.matrix.clear()
        self._run(kb)
        kb.matrix.press(*self.FN)
        self._run(kb)
        kb.matrix.press(*self.F5)
        self.assertEqual(self._run(kb)[-1], [0, 0, 0x3E, 0, 0, 0, 0, 1])

    def test_win_mode_never_sets_fn(self):
        kb = self._session(mac=False)
        kb.matrix.press(*self.F1)
        self.assertEqual(self._run(kb)[-1], [0, 0, 0x3A, 0, 0, 0, 0, 0])
        kb.matrix.clear()
        self._run(kb)
        kb.matrix.press(*self.FN)
        kb.matrix.press(*self.F1)                # Win Fn layer: brightness (consumer), not EP1
        reps = self._run(kb, hits=200)
        self.assertFalse([r for r in reps if r[7]], f"Win mode must not send fn: {reps}")


class TestImeKeys(unittest.TestCase):
    """The keys beside Space are mod-taps: a tap sends the IME key, holding past
    the tapping term or pressing another key meanwhile makes them the modifier."""

    LEFT, RIGHT, C = (2, 5), (8, 5), (3, 4)
    LNG1, LNG2, INT4, INT5 = 0x90, 0x91, 0x8A, 0x8B

    @classmethod
    def setUpClass(cls):
        _need_firmware()

    def _session(self, mac):
        kb = Air75Sim()
        self.addCleanup(kb.close)
        kb.boot(usb=True, mac=mac)
        kb.mark_usb_configured()
        return kb

    _run = staticmethod(TestAppleFn._run)

    def _age_pending(self, kb):
        """Move the scan clock past the tapping term (285 scans) without
        running them all."""
        addr = kb._xdata_static("tick", "scans")
        lo, hi = kb.get_xram(addr, 2)
        now = (lo | (hi << 8)) + 400
        kb.cmd("set mem xram 0x%x 0x%02x 0x%02x" % (addr, now & 0xFF, (now >> 8) & 0xFF))

    def test_tap_sends_the_ime_keys(self):
        for mac, left, right in [(True, self.LNG2, self.LNG1), (False, self.INT5, self.INT4)]:
            kb = self._session(mac)
            for key, code in [(self.LEFT, left), (self.RIGHT, right)]:
                with self.subTest(mac=mac, key=key):
                    kb.matrix.press(*key)
                    self.assertEqual(self._run(kb), [], "nothing until the key is released")
                    kb.matrix.release(*key)
                    self.assertEqual(self._run(kb), [[0, 0, code, 0, 0, 0, 0, 0], [0] * 8])

    def test_hold_past_the_term_is_the_modifier(self):
        for mac, mod in [(True, MOD_LGUI), (False, MOD_LALT)]:
            with self.subTest(mac=mac):
                kb = self._session(mac)
                kb.matrix.press(*self.LEFT)
                self._run(kb)
                self._age_pending(kb)
                self.assertEqual(self._run(kb), [[mod, 0, 0, 0, 0, 0, 0, 0]])
                kb.matrix.release(*self.LEFT)
                self.assertEqual(self._run(kb), [[0] * 8], "release drops the modifier, no IME key")

    def test_other_key_makes_it_a_hold(self):
        kb = self._session(mac=True)
        kb.matrix.press(*self.RIGHT)
        self._run(kb)
        kb.matrix.press(*self.C)                   # Cmd+C typed quickly
        self.assertEqual(self._run(kb), [[MOD_RGUI, 0, 0, 0, 0, 0, 0, 0], [MOD_RGUI, 0, 0x06, 0, 0, 0, 0, 0]])
        kb.matrix.clear()
        reps = self._run(kb)
        self.assertEqual(reps[-1], [0] * 8)
        self.assertFalse([r for r in reps if self.LNG1 in r[2:7]], f"no IME key after a hold: {reps}")


class TestBluetoothNames(unittest.TestCase):
    """Bluetooth 1 is the default link, and every Bluetooth slot gets its own
    name ("Air75-<slot>" over BLE, "Air75-<slot> BT3.0" over classic), read back
    from the frames smk hands to the BK3632 driver."""

    @classmethod
    def setUpClass(cls):
        _need_firmware()

    def _collect_names(self, kb, stop, max_frames=40):
        names = []
        xfer, buf = kb._a("bb_spi_xfer"), kb._a("rf_tx_buf")
        kb.brk(xfer)
        kb.brk(stop)
        for _ in range(max_frames):
            out = kb.run()
            if kb.stopped_at(out) == stop:
                break
            frame = kb.get_xram(buf, 32)
            if frame[:3] == [0xAA, 0x1D, 0x08]:
                names.append((frame[3], bytes(frame[5:5 + frame[4]]).decode("ascii")))
        kb.cmd("delete")
        return names

    def test_boot_defaults_to_bluetooth_1_and_names_it(self):
        kb = Air75Sim()
        self.addCleanup(kb.close)
        kb.reset_fast(p5=0xFF & ~0x20)             # connection switch on wireless
        names = self._collect_names(kb, kb._a("kb_update_switches"))
        self.assertIn((0, "Air75-1"), names)
        self.assertIn((1, "Air75-1 BT3.0"), names)
        self.assertNotIn("SMK", " ".join(n for _, n in names))
        rf_link = kb.get_xram(kb._a("user_settings") + 8, 1)[0]
        self.assertEqual(rf_link, 1, "the default link is Bluetooth 1")

    def test_switching_slot_renames_first(self):
        kb = Air75Sim()
        self.addCleanup(kb.close)
        kb.boot(usb=False)
        kb.matrix.press(9, 5)                      # Fn
        TestAppleFn._run(kb)
        kb.matrix.press(2, 2)                      # Fn+W = Bluetooth 2
        kb.brk(kb._a("user_matrix_read_rows"))
        names = []
        xfer, buf = kb._a("bb_spi_xfer"), kb._a("rf_tx_buf")
        kb.brk(xfer)
        for _ in range(400):
            out = kb.run()
            if kb.stopped_at(out) == xfer:
                frame = kb.get_xram(buf, 32)
                if frame[:3] == [0xAA, 0x1D, 0x08]:
                    names.append((frame[3], bytes(frame[5:5 + frame[4]]).decode("ascii")))
                    if len(names) == 2:
                        break
            else:
                kb.matrix.inject(kb)
        kb.cmd("delete")
        self.assertEqual(names, [(0, "Air75-2"), (1, "Air75-2 BT3.0")])


class TestBluetoothPairing(unittest.TestCase):
    """A long press on a link key must reach the module as a pairing command
    with no name change right before it (the module drops a pairing command
    that closely follows a name change; build 4 did that and could not pair)."""

    @classmethod
    def setUpClass(cls):
        _need_firmware()

    def _hold_link_key(self, kb):
        kb.boot(usb=False)                         # slot 1 is named at boot
        kb.matrix.press(9, 5)                      # Fn
        TestAppleFn._run(kb)
        kb.matrix.press(1, 2)                      # Fn+Q = Bluetooth 1, held
        held = kb._xdata_static("kb", "link_hold_keycode")
        for _ in range(20):                        # until the press is being timed
            TestAppleFn._run(kb, 40)
            if any(kb.get_xram(held, 2)):
                break
        self.assertTrue(any(kb.get_xram(held, 2)), "the link key press was not registered")

    def _skip_hold_to(self, kb, scans_after_press):
        """Move the scan clock to `scans_after_press` scans after the key-down."""
        since = kb._xdata_static("kb", "link_hold_since")
        lo, hi = kb.get_xram(since, 2)
        target = ((lo | (hi << 8)) + scans_after_press) & 0xFFFF
        clock = kb._xdata_static("tick", "scans")
        kb.cmd("set mem xram 0x%x 0x%02x 0x%02x" % (clock, target & 0xFF, target >> 8))

    def _frames(self, kb, iterations, stop_on_pairing=True):
        xfer, buf, rr = kb._a("bb_spi_xfer"), kb._a("rf_tx_buf"), kb._a("user_matrix_read_rows")
        kb.brk(xfer)
        kb.brk(rr)
        frames = []
        for _ in range(iterations):
            out = kb.run()
            if kb.stopped_at(out) == rr:
                kb.matrix.inject(kb)
                continue
            frame = kb.get_xram(buf, 6)
            frames.append(frame)
            if stop_on_pairing and frame[:4] == [0xAA, 0x03, 0x01, 0x01]:
                break
        kb.cmd("delete")
        return frames

    def test_long_press_pairs_without_renaming(self):
        kb = Air75Sim()
        self.addCleanup(kb.close)
        self._hold_link_key(kb)
        self._skip_hold_to(kb, 4400 - 10)          # LINK_PAIRING_HOLD_SCANS, ~6 s on the board
        frames = self._frames(kb, 3000)
        self.assertIn([0xAA, 0x03, 0x01, 0x01, 0x01], [f[:5] for f in frames], "pairing command for slot 1")
        self.assertFalse([f for f in frames if f[:3] == [0xAA, 0x1D, 0x08]], "no name frame before pairing")

    def test_short_hold_does_not_pair(self):
        kb = Air75Sim()
        self.addCleanup(kb.close)
        self._hold_link_key(kb)
        self._skip_hold_to(kb, 3500)               # ~5 s on the board: not long enough
        frames = self._frames(kb, 600, stop_on_pairing=False)
        self.assertNotIn([0xAA, 0x03, 0x01, 0x01, 0x01], [f[:5] for f in frames])


class TestAnsiLayout(unittest.TestCase):
    """The plain layout: Caps Lock where the stock firmware has it, plain
    Command/Alt beside Space, no US-JIS (Fn+Tab is plain Tab)."""

    @classmethod
    def setUpClass(cls):
        if not Path(AIR75_ANSI_FW).exists():
            skip_or_fail(f"no nuphy-air75 ansi firmware at {AIR75_ANSI_FW}")
        _need_firmware()

    def _session(self, mac):
        kb = Air75Sim(firmware=AIR75_ANSI_FW)
        self.addCleanup(kb.close)
        kb.boot(usb=True, mac=mac)
        kb.mark_usb_configured()
        return kb

    assertKey = TestMatrixAndKeymap.assertKey

    def test_built_without_usjis_and_mod_taps(self):
        sym = load_symbols(Path(AIR75_ANSI_FW).with_suffix(".map"))
        self.assertNotIn("usjis_process_record", sym)
        self.assertNotIn("tap_hold_process", sym)
        self.assertIn("user_boot_escape", sym)
        data = read_ihex(AIR75_ANSI_FW)
        self.assertLess(max(data), TestImage.FLASH_CFG_ADDR)

    def test_win_layer(self):
        kb = self._session(mac=False)
        for key, mods, code in [((0, 3), 0x00, 0x39), ((0, 5), MOD_LCTL, 0x00), ((1, 5), MOD_LGUI, 0x00),
                                ((2, 5), MOD_LALT, 0x00), ((8, 5), MOD_RALT, 0x00), ((1, 3), 0x00, 0x04)]:
            with self.subTest(key=key):
                self.assertKey(kb, key, mods, code)

    def test_mac_layer(self):
        kb = self._session(mac=True)
        for key, mods in [((1, 5), MOD_LALT), ((2, 5), MOD_LGUI), ((8, 5), MOD_RGUI)]:
            with self.subTest(key=key):
                self.assertKey(kb, key, mods, 0x00)

    def test_fn_tab_is_just_tab(self):
        """Fn+Tab is transparent here (no US-JIS toggle), so it types Tab."""
        kb = self._session(mac=False)
        self.assertKey(kb, ((9, 5), (0, 2)), 0x00, 0x2B)


class TestStockBootloaderChain(unittest.TestCase):
    """The flash as it will look on the board: the firmware converted by
    sinowisp to the physical layout (reset vector -> 0xF000, the firmware's own
    vector at 0xEFFB) plus the stock ISP bootloader at 0xF000-0xFFFF."""

    @classmethod
    def setUpClass(cls):
        _need_firmware()
        stock = os.environ.get("SMK_AIR75_STOCK_JTAG")
        if not stock or not Path(stock).exists():
            raise unittest.SkipTest("set SMK_AIR75_STOCK_JTAG to a physical-layout Air75 dump")
        sinowisp = shutil.which("sinowisp")
        if not sinowisp:
            raise unittest.SkipTest("sinowisp not on PATH")
        cls.tmp = tempfile.TemporaryDirectory()
        tmp = Path(cls.tmp.name)
        fw_bin = tmp / "fw_jtag.bin"
        subprocess.run([sinowisp, "convert", "-d", "nuphy-air75", "--direction", "to_jtag",
                        "--output_format", "bin", AIR75_FW, str(fw_bin)],
                       check=True, capture_output=True)
        image = bytearray(fw_bin.read_bytes().ljust(0xF000, b"\xff"))
        image += Path(stock).read_bytes()[0xF000:0x10000]
        assert len(image) == 0x10000
        cls.image = image
        # uCsim loads Intel HEX; the .map next to it keeps the firmware symbols.
        cls.hex_path = tmp / "nuphy-air75_chain_smk.hex"
        shutil.copy(Path(AIR75_FW).with_suffix(".map"), cls.hex_path.with_suffix(".map"))
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
        self.kb = Air75Sim(firmware=str(self.hex_path))

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
        kb.reset_fast(p7=0xFF & ~0x01)
        stop = kb.run_until(ISP_KEYS_OK, ISP_KEYS_BAD, kb._a("kb_update_switches"))
        self.assertEqual(stop, ISP_KEYS_OK, "the bootloader must accept isp_jump() and start ISP mode")

    def test_missing_marker_stays_in_bootloader(self):
        """What an interrupted write leaves behind: no LJMP at 0xEFFB."""
        kb = self.kb
        kb.reset_fast()
        kb.cmd("set mem rom 0xeffb 0xff")
        stop = kb.run_until(BL_MARKER_OK, BL_NO_MARKER, kb._a("kb_update_switches"))
        self.assertEqual(stop, BL_NO_MARKER)


if __name__ == "__main__":
    unittest.main()
