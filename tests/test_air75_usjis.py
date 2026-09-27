#!/usr/bin/env python3
"""US-JIS substitution on the nuphy-air75, driven through the patched uCsim
simulator with the real scan path (the key matrix is emulated test-side).

The cases mirror the host tests of the QMK version (qmk-nuphy
common/core/usjis.c): the C01-C20 table, identity when disabled or in Mac
mode, the Shift policy scenarios S02/S03/S05/S11-S14 report by report, Ctrl
kept through a substitution, the Fn+Tab toggle with its saved setting and
indicator, and a toggle postponed while a key is held. smk only sends a
report when it differs from the last one, so the expected sequences are the
QMK ones with consecutive duplicates removed.

Run from the repo root after building the firmware:

    meson compile -C build nuphy-air75_usjis_smk.hex
    python3 -m unittest discover -s tests -p test_air75_usjis.py
"""

import re
import unittest

from pathlib import Path

from sim import load_symbols, skip_or_fail
from test_air75 import AIR75_FW, Air75Sim, _need_firmware

# Air75 matrix positions (col, row), from the default layout.
LSFT, RSFT, LCTL = (0, 4), (13, 4), (0, 3)   # Left Ctrl is swapped onto the Caps position
FN, TAB = (9, 5), (0, 2)
KEY = {
    "GRV": (0, 1), "2": (2, 1), "6": (6, 1), "7": (7, 1), "8": (8, 1), "9": (9, 1), "0": (10, 1),
    "MINS": (11, 1), "EQL": (12, 1), "LBRC": (11, 2), "RBRC": (12, 2), "BSLS": (13, 2),
    "SCLN": (10, 3), "QUOT": (11, 3), "A": (1, 3),
}
# HID usages.
HID = {
    "GRV": 0x35, "2": 0x1F, "6": 0x23, "7": 0x24, "8": 0x25, "9": 0x26, "0": 0x27, "MINS": 0x2D,
    "EQL": 0x2E, "LBRC": 0x2F, "RBRC": 0x30, "BSLS": 0x31, "NUHS": 0x32, "SCLN": 0x33, "QUOT": 0x34,
    "INT1": 0x87, "INT3": 0x89, "A": 0x04,
}
PHYS_SHIFT, ADDED_SHIFT, CTRL = 0x20, 0x02, 0x01   # Right Shift pressed, Left Shift added

# C01..C20: (US key, with Shift, JIS key, JIS Shift)
RULES = [
    ("GRV", 1, "EQL", 1), ("2", 1, "LBRC", 0), ("6", 1, "EQL", 0), ("7", 1, "6", 1),
    ("8", 1, "QUOT", 1), ("9", 1, "8", 1), ("0", 1, "9", 1), ("MINS", 1, "INT1", 1),
    ("EQL", 0, "MINS", 1), ("EQL", 1, "SCLN", 1), ("LBRC", 0, "RBRC", 0), ("LBRC", 1, "RBRC", 1),
    ("RBRC", 0, "NUHS", 0), ("RBRC", 1, "NUHS", 1), ("BSLS", 0, "INT1", 0), ("BSLS", 1, "INT3", 1),
    ("SCLN", 1, "QUOT", 0), ("QUOT", 0, "7", 1), ("QUOT", 1, "2", 1), ("GRV", 0, "LBRC", 1),
]

NVM_BASE = 0xEC00              # settings sector: 5A A5 <len> <payload> <checksum>
USJIS_OFFSET = 9               # user_settings.usjis_enabled after the nine LED/RF bytes


class UsjisSim(Air75Sim):
    MAX_HITS = 192             # row reads to wait for a report (a full scan is 32: two samples per column)
    TAIL_HITS = 32             # one more scan after a report, to catch a follow-up
    def boot_usjis(self, enabled, mac=False):
        self.boot(usb=True, mac=mac)
        self.mark_usb_configured()
        self.cmd("set mem xram 0x%x 0x%02x" % (self._a("user_settings") + USJIS_OFFSET, 1 if enabled else 0))

    def step(self, max_hits=None):
        """Run the scan until a new EP1 report appears (plus one more scan) or
        max_hits row reads pass; return the new reports as (mods, sorted keys)."""
        before = len(self.ep1_reports())
        self.brk(self._a("user_matrix_read_rows"))
        tail = None
        for i in range(max_hits or self.MAX_HITS):
            self.run()
            self.matrix.inject(self)
            if tail is None:
                if i % 8 == 7 and len(self.ep1_reports()) > before:
                    tail = self.TAIL_HITS
            else:
                tail -= 1
                if tail == 0:
                    break
        self.cmd("delete")
        return [(r[0], sorted(k for k in r[2:7] if k)) for r in self.ep1_reports()[before:]]

    def down(self, key):
        self.matrix.press(*key)
        return self.step()

    def up(self, key):
        self.matrix.release(*key)
        return self.step()

    def usjis_enabled(self):
        return self.get_xram(self._a("user_settings") + USJIS_OFFSET)[0]

    def get_rom(self, addr, n):
        out = self.cmd("dump rom 0x%x 0x%x" % (addr, addr + n - 1))
        vals = []
        for line in out.splitlines():
            m = re.match(r"\s*0x[0-9a-fA-F]+\s+((?:[0-9a-fA-F]{2} )+)", line)
            if m:
                vals += [int(x, 16) for x in m.group(1).split()]
        return vals[:n]


def rep(mods, *keys):
    return (mods, sorted(HID[k] for k in keys))


class UsjisCase(unittest.TestCase):
    # On macOS a long session gets slow (every simulator round trip starts to
    # take ~0.1 s after a few dozen key events), so long walks use a fresh
    # session every RULES_PER_SESSION rules.
    RULES_PER_SESSION = 4

    @classmethod
    def setUpClass(cls):
        _need_firmware()
        if "usjis_process_record" not in load_symbols(Path(AIR75_FW).with_suffix(".map")):
            skip_or_fail(f"{AIR75_FW} is built without USJIS")

    def session(self, enabled=True, mac=False):
        kb = UsjisSim()
        self.addCleanup(kb.close)
        kb.boot_usjis(enabled, mac)
        return kb

    def rule_sessions(self, enabled=True, mac=False):
        """Yield (session, rule) for every rule, with a fresh session per chunk."""
        for i in range(0, len(RULES), self.RULES_PER_SESSION):
            kb = UsjisSim()
            try:
                kb.boot_usjis(enabled, mac)
                for rule in RULES[i:i + self.RULES_PER_SESSION]:
                    yield kb, rule
            finally:
                kb.close()

    def play(self, kb, events):
        """events: list of ("down"|"up", key); returns all reports in order."""
        reps = []
        for action, key in events:
            reps += kb.down(key) if action == "down" else kb.up(key)
        return reps


class TestTable(UsjisCase):
    def test_c01_to_c20(self):
        for kb, (us, shift, jis, oshift) in self.rule_sessions():
            with self.subTest(rule=(us, shift)):
                if shift:
                    kb.down(RSFT)
                reps = kb.down(KEY[us])
                self.assertEqual(reps[-1:], [rep(ADDED_SHIFT if oshift else 0, jis)])
                kb.up(KEY[us])
                if shift:
                    kb.up(RSFT)

    def test_unsubstituted_keys_pass_through(self):
        kb = self.session()
        self.assertEqual(kb.down(KEY["A"]), [rep(0, "A")])
        self.assertEqual(kb.up(KEY["A"]), [rep(0)])
        kb.down(RSFT)
        self.assertEqual(kb.down(KEY["A"]), [rep(PHYS_SHIFT, "A")])
        self.assertEqual(kb.up(KEY["A"]), [rep(PHYS_SHIFT)])
        self.assertEqual(kb.up(RSFT), [rep(0)])


class TestIdentity(UsjisCase):
    """S01: disabled, and Mac mode, change nothing."""

    def check_identity(self, enabled, mac):
        for kb, (us, shift, _, _) in self.rule_sessions(enabled, mac):
            with self.subTest(rule=(us, shift)):
                if shift:
                    kb.down(RSFT)
                self.assertEqual(kb.down(KEY[us])[-1:], [rep(PHYS_SHIFT if shift else 0, us)])
                kb.up(KEY[us])
                if shift:
                    kb.up(RSFT)

    def test_disabled_on_win(self):
        self.check_identity(enabled=False, mac=False)

    def test_enabled_on_mac(self):
        self.check_identity(enabled=True, mac=True)


class TestShiftPolicy(UsjisCase):
    """Every report in order (consecutive duplicates removed, see the module doc)."""

    def test_s02(self):
        kb = self.session()
        self.assertEqual(self.play(kb, [("down", RSFT), ("down", KEY["2"]), ("up", KEY["2"]), ("up", RSFT)]),
                         [rep(PHYS_SHIFT), rep(0, "LBRC"), rep(PHYS_SHIFT), rep(0)])

    def test_s03(self):
        kb = self.session()
        self.assertEqual(self.play(kb, [("down", RSFT), ("down", KEY["2"]), ("up", RSFT), ("up", KEY["2"])]),
                         [rep(PHYS_SHIFT), rep(0, "LBRC"), rep(0)])

    def test_s05(self):
        kb = self.session()
        self.assertEqual(self.play(kb, [("down", RSFT), ("down", KEY["2"]), ("down", KEY["MINS"]),
                                        ("up", KEY["MINS"]), ("up", KEY["2"]), ("up", RSFT)]),
                         [rep(PHYS_SHIFT), rep(0, "LBRC"), rep(ADDED_SHIFT, "LBRC", "INT1"),
                          rep(0, "LBRC"), rep(PHYS_SHIFT), rep(0)])

    def test_s11(self):
        """@ held, then A: A goes down with the physical Shift, so it types "A"."""
        kb = self.session()
        self.assertEqual(self.play(kb, [("down", RSFT), ("down", KEY["2"]), ("down", KEY["A"]),
                                        ("up", KEY["A"]), ("up", KEY["2"]), ("up", RSFT)]),
                         [rep(PHYS_SHIFT), rep(0, "LBRC"), rep(PHYS_SHIFT, "LBRC", "A"),
                          rep(0, "LBRC"), rep(PHYS_SHIFT), rep(0)])

    def test_s12(self):
        """= held, then A: A goes down without the added Shift, so it types "a"."""
        kb = self.session()
        self.assertEqual(self.play(kb, [("down", KEY["EQL"]), ("down", KEY["A"]), ("up", KEY["A"]), ("up", KEY["EQL"])]),
                         [rep(ADDED_SHIFT, "MINS"), rep(0, "MINS", "A"), rep(ADDED_SHIFT, "MINS"), rep(0)])

    def test_s13(self):
        kb = self.session()
        self.assertEqual(self.play(kb, [("down", KEY["EQL"]), ("down", RSFT), ("down", KEY["2"]),
                                        ("up", KEY["2"]), ("up", RSFT), ("up", KEY["EQL"])]),
                         [rep(ADDED_SHIFT, "MINS"), rep(0, "MINS", "LBRC"), rep(ADDED_SHIFT, "MINS"), rep(0)])

    def test_s14(self):
        kb = self.session()
        self.assertEqual(self.play(kb, [("down", RSFT), ("down", KEY["MINS"]), ("up", RSFT), ("up", KEY["MINS"])]),
                         [rep(PHYS_SHIFT), rep(ADDED_SHIFT, "INT1"), rep(0)])

    def test_ctrl_is_kept(self):
        kb = self.session()
        self.assertEqual(self.play(kb, [("down", LCTL), ("down", KEY["LBRC"]), ("up", KEY["LBRC"]), ("up", LCTL)]),
                         [rep(CTRL), rep(CTRL, "RBRC"), rep(CTRL), rep(0)])


class TestToggle(UsjisCase):
    def test_fn_tab_toggles_saves_and_shows(self):
        kb = self.session(enabled=False)
        kb.down(FN)
        kb.down(TAB)
        kb.up(TAB)
        kb.up(FN)
        self.assertEqual(kb.usjis_enabled(), 1)
        sweeps = kb.get_xram(kb._xdata_static("indicators", "usjis_flash_sweeps"))[0]
        self.assertGreater(sweeps, 0, "the side lights show the change")
        kb.step(400)                                   # let settings_task() write the record
        rec = kb.get_rom(NVM_BASE, 3 + USJIS_OFFSET + 2)
        self.assertEqual(rec[:2], [0x5A, 0xA5], f"settings record: {rec}")
        self.assertEqual(rec[3 + USJIS_OFFSET], 1, f"settings record: {rec}")
        kb.down(RSFT)
        self.assertEqual(kb.down(KEY["2"])[-1], rep(0, "LBRC"), "substitution is on now")
        kb.up(KEY["2"])
        kb.up(RSFT)
        kb.down(FN)                                    # and off again
        kb.down(TAB)
        kb.up(TAB)
        kb.up(FN)
        self.assertEqual(kb.usjis_enabled(), 0)

    def test_toggle_while_a_key_is_held_waits_for_its_release(self):
        kb = self.session(enabled=True)
        self.assertEqual(kb.down(KEY["EQL"]), [rep(ADDED_SHIFT, "MINS")])
        kb.down(FN)
        kb.down(TAB)                                   # requested while = is held
        kb.up(TAB)
        self.assertEqual(kb.usjis_enabled(), 1, "postponed while = is held")
        kb.up(KEY["EQL"])                              # still under Fn: the layer passes = through
        self.assertEqual(kb.usjis_enabled(), 0, "applied once the last key is released")
        kb.up(FN)
        kb.down(RSFT)
        self.assertEqual(kb.down(KEY["2"])[-1], rep(PHYS_SHIFT, "2"), "substitution is off now")
        kb.up(KEY["2"])
        kb.up(RSFT)


if __name__ == "__main__":
    unittest.main()
