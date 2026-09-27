#!/usr/bin/env python3
"""US-JIS and the IME mod-taps on the aula-f65-v1 `usjis` layout, through the
real scan path in the patched uCsim simulator.

The substitution itself is the shared src/smk/usjis.c, covered rule by rule
on the Air75 in test_air75_usjis.py; this file checks it on the F65 matrix:
the C01-C20 table (with the grave key on Fn+Esc), identity when disabled and
in Mac mode, Shift policy S02, Ctrl kept, the Fn+Tab toggle and its saved
setting, and the mod-taps on both sides of Space, with Fn one key right of them.

    meson compile -C build aula-f65-v1_usjis_smk.hex
    python3 -m unittest discover -s tests -p test_f65_usjis.py
"""

import unittest
from pathlib import Path

from sim import load_symbols, skip_or_fail
from test_f65 import F65_FW, F65Sim, _need, ESC, RIGHT_OF_SPACE

RSFT, LCTL, TAB = (13, 3), (0, 2), (0, 1)   # Left Ctrl is swapped onto the Caps position
LSFT = (0, 3)
KEY = {
    "2": (2, 0), "6": (6, 0), "7": (7, 0), "8": (8, 0), "9": (9, 0), "0": (10, 0),
    "MINS": (11, 0), "EQL": (12, 0), "LBRC": (11, 1), "RBRC": (12, 1), "BSLS": (13, 1),
    "SCLN": (10, 2), "QUOT": (11, 2), "A": (1, 2),
}
HID = {
    "GRV": 0x35, "2": 0x1F, "6": 0x23, "7": 0x24, "8": 0x25, "9": 0x26, "0": 0x27, "MINS": 0x2D,
    "EQL": 0x2E, "LBRC": 0x2F, "RBRC": 0x30, "BSLS": 0x31, "NUHS": 0x32, "SCLN": 0x33, "QUOT": 0x34,
    "INT1": 0x87, "INT3": 0x89, "A": 0x04,
}
PHYS_SHIFT, ADDED_SHIFT, CTRL = 0x20, 0x02, 0x01

# C01..C20: (US key, with Shift, JIS key, JIS Shift); the grave key is Fn+Esc.
RULES = [
    ("GRV", 1, "EQL", 1), ("2", 1, "LBRC", 0), ("6", 1, "EQL", 0), ("7", 1, "6", 1),
    ("8", 1, "QUOT", 1), ("9", 1, "8", 1), ("0", 1, "9", 1), ("MINS", 1, "INT1", 1),
    ("EQL", 0, "MINS", 1), ("EQL", 1, "SCLN", 1), ("LBRC", 0, "RBRC", 0), ("LBRC", 1, "RBRC", 1),
    ("RBRC", 0, "NUHS", 0), ("RBRC", 1, "NUHS", 1), ("BSLS", 0, "INT1", 0), ("BSLS", 1, "INT3", 1),
    ("SCLN", 1, "QUOT", 0), ("QUOT", 0, "7", 1), ("QUOT", 1, "2", 1), ("GRV", 0, "LBRC", 1),
]


def rep(mods, *keys):
    return [mods, 0] + (sorted(HID[k] for k in keys) + [0] * 6)[:6]


class Case(unittest.TestCase):
    RULES_PER_SESSION = 4

    @classmethod
    def setUpClass(cls):
        _need(F65_FW)
        if "usjis_process_record" not in load_symbols(Path(F65_FW).with_suffix(".map")):
            skip_or_fail(f"{F65_FW} is built without USJIS")

    def session(self, enabled=True, mac=False):
        kb = F65Sim()
        self.addCleanup(kb.close)
        kb.boot(mac=mac)
        kb.set_setting("usjis", 1 if enabled else 0)
        return kb

    def rule_sessions(self, enabled=True, mac=False):
        for i in range(0, len(RULES), self.RULES_PER_SESSION):
            kb = F65Sim()
            try:
                kb.boot(mac=mac)
                kb.set_setting("usjis", 1 if enabled else 0)
                for rule in RULES[i:i + self.RULES_PER_SESSION]:
                    yield kb, rule
            finally:
                kb.close()

    @staticmethod
    def type_us(kb, us, shift):
        """Press the US key with or without Right Shift and return the report
        where it goes down; then release everything. The grave key is Fn+Esc,
        shifted with Left Shift (Right Shift under Fn is the second Fn and has
        its own test)."""
        sh = LSFT if us == "GRV" else RSFT
        if shift:
            kb.down(sh)
        if us == "GRV":
            kb.down(kb.fn)
            reps = kb.down(ESC)
            kb.up(ESC)
            kb.up(kb.fn)
        else:
            reps = kb.down(KEY[us])
            kb.up(KEY[us])
        if shift:
            kb.up(sh)
        return reps


class TestTable(Case):
    def test_c01_to_c20(self):
        for kb, (us, shift, jis, oshift) in self.rule_sessions():
            with self.subTest(rule=(us, shift)):
                self.assertEqual(self.type_us(kb, us, shift)[-1:], [rep(ADDED_SHIFT if oshift else 0, jis)])

    def test_unsubstituted_keys_pass_through(self):
        kb = self.session()
        self.assertEqual(kb.down(KEY["A"]), [rep(0, "A")])
        self.assertEqual(kb.up(KEY["A"]), [rep(0)])


class TestIdentity(Case):
    def check_identity(self, enabled, mac):
        for kb, (us, shift, _, _) in self.rule_sessions(enabled, mac):
            with self.subTest(rule=(us, shift)):
                mods = (0x02 if us == "GRV" else PHYS_SHIFT) if shift else 0
                self.assertEqual(self.type_us(kb, us, shift)[-1:], [rep(mods, us)])

    def test_tilde_with_right_shift_under_fn_is_substituted(self):
        """Fn + Right Shift + Esc is ~ too, and goes through US-JIS as ~."""
        kb = self.session(enabled=True)
        for first in (RSFT, kb.fn):
            with self.subTest(first=first):
                second = kb.fn if first == RSFT else RSFT
                kb.down(first)
                kb.down(second)
                reps = kb.down(ESC)
                self.assertEqual(reps[-1:], [rep(ADDED_SHIFT, "EQL")])   # C01: ~ on a JIS host
                kb.up(ESC)
                kb.up(second)
                kb.up(first)
                kb.matrix.clear()
                kb.step()

    def test_disabled_on_win(self):
        self.check_identity(enabled=False, mac=False)

    def test_enabled_on_mac(self):
        self.check_identity(enabled=True, mac=True)


class TestShiftAndCtrl(Case):
    def test_s02(self):
        kb = self.session()
        reps = kb.down(RSFT) + kb.down(KEY["2"]) + kb.up(KEY["2"]) + kb.up(RSFT)
        self.assertEqual(reps, [rep(PHYS_SHIFT), rep(0, "LBRC"), rep(PHYS_SHIFT), rep(0)])

    def test_ctrl_is_kept(self):
        kb = self.session()
        reps = kb.down(LCTL) + kb.down(KEY["LBRC"]) + kb.up(KEY["LBRC"]) + kb.up(LCTL)
        self.assertEqual(reps, [rep(CTRL), rep(CTRL, "RBRC"), rep(CTRL), rep(0)])


class TestToggle(Case):
    def test_fn_tab_toggles_and_saves(self):
        kb = self.session(enabled=False)
        reps = kb.down(kb.fn) + kb.down(TAB) + kb.up(TAB) + kb.up(kb.fn)
        self.assertEqual([r for r in reps if any(r[2:])], [], "Fn+Tab types nothing")
        self.assertEqual(kb.setting("usjis"), 1)
        rec = kb.record()
        self.assertEqual(rec[kb.off["usjis"]], 1, f"settings record: {rec}")
        self.assertEqual(self.type_us(kb, "2", 1)[-1], rep(0, "LBRC"), "substitution is on now")
        kb.down(kb.fn)
        kb.down(TAB)
        kb.up(TAB)
        kb.up(kb.fn)
        self.assertEqual(kb.setting("usjis"), 0)


class TestImeKeys(Case):
    LEFT, RIGHT, C = (2, 4), RIGHT_OF_SPACE, (3, 3)
    LNG1, LNG2, INT4, INT5 = 0x90, 0x91, 0x8A, 0x8B

    def _age_pending(self, kb):
        addr = kb._xdata_static("tick", "scans")
        lo, hi = kb.get_xram(addr, 2)
        now = (lo | (hi << 8)) + 400
        kb.cmd("set mem xram 0x%x 0x%02x 0x%02x" % (addr, now & 0xFF, (now >> 8) & 0xFF))

    def test_tap_sends_the_ime_keys(self):
        for mac, left, right in [(False, self.INT5, self.INT4), (True, self.LNG2, self.LNG1)]:
            kb = self.session(enabled=False, mac=mac)
            for key, code in [(self.LEFT, left), (self.RIGHT, right)]:
                with self.subTest(mac=mac, key=key):
                    self.assertEqual(kb.down(key), [], "nothing until the key is released")
                    self.assertEqual(kb.up(key), [[0, 0, code, 0, 0, 0, 0, 0], [0] * 8])

    def test_hold_past_the_term_is_the_modifier(self):
        for mac, key, mod in [(False, self.LEFT, 0x04), (False, self.RIGHT, 0x10),
                              (True, self.LEFT, 0x08), (True, self.RIGHT, 0x80)]:
            with self.subTest(mac=mac, key=key):
                kb = self.session(enabled=False, mac=mac)
                kb.down(key)
                self._age_pending(kb)
                self.assertEqual(kb.step(), [[mod, 0, 0, 0, 0, 0, 0, 0]])
                self.assertEqual(kb.up(key), [[0] * 8])

    def test_other_key_makes_it_a_hold(self):
        """Right of Space + C typed quickly: Right Ctrl+C on Win, Right Command+C on Mac."""
        for mac, mod in [(False, 0x10), (True, 0x80)]:
            with self.subTest(mac=mac):
                kb = self.session(enabled=False, mac=mac)
                kb.down(self.RIGHT)
                self.assertEqual(kb.down(self.C), [[mod, 0, 0, 0, 0, 0, 0, 0], [mod, 0, 0x06, 0, 0, 0, 0, 0]])
                kb.matrix.clear()
                reps = kb.step()
                self.assertEqual(reps[-1], [0] * 8)
                self.assertFalse([r for r in reps if self.INT4 in r[2:] or self.LNG1 in r[2:]],
                                 f"no IME key after a hold: {reps}")


if __name__ == "__main__":
    unittest.main()
