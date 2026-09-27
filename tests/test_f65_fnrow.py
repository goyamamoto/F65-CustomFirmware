#!/usr/bin/env python3
"""aula-f65-v1: the number row under Fn, with Right Shift as a second Fn.

    Mac mode: Fn + 1 .. =           media (Apple's order), Fn + Right Shift + 1 .. = F1 .. F12
    Win mode: Fn + 1 .. =           F1 .. F12, Fn + Right Shift + 1 .. = media (the stock Win Fn row)

Right Shift under Fn picks the other set and is not Shift while Fn is held
(pressed before Fn it is Shift until Fn goes down; still held when Fn goes up,
Shift comes back just before the next ordinary key, so the host never sees a
Shift tap on its own); alone, or with ordinary keys, it is Shift. Left Shift
under Fn stays Shift. Fn + Right Shift + Esc is ~ as Shift + Fn + Esc.
Consumer usages go out on USB (EP2, report 2) here and as the radio's short
frame in test_f65_radio.py.

    python3 -m unittest discover -s tests -p test_f65_fnrow.py
"""

import os
import re
import unittest
from pathlib import Path

from sim import skip_or_fail
from test_f65 import (F65Sim, F65_FW, F65_ANSI_FW, _need, report, read_ihex, ESC, LSFT, RSFT, A, S,
                      MOD_LSFT, MOD_RSFT, MOD_LALT)

NUM = [(n, 0) for n in range(1, 13)]           # 1 2 3 4 5 6 7 8 9 0 - =
MEDIA_MAC = [0x070, 0x06F, 0x29F, 0x2A0, None, None, 0x0B6, 0x0CD, 0x0B5, 0x0E2, 0x0EA, 0x0E9]
ALT_TAB, ALT_ESC = ("key", MOD_LALT, 0x2B), ("key", MOD_LALT, 0x29)
# The stock Win Fn table's row bit 0 (0xB800 + slot * 24): 02 = consumer usage,
# 0004xxxx = Left Alt + key.
STOCK_WIN_ROW0 = ["02000070", "0200006f", "0004002b", "00040029", "02000223", "0200018a",
                  "020000b6", "020000cd", "020000b5", "020000e2", "020000ea", "020000e9"]
MEDIA_WIN = [0x070, 0x06F, ALT_TAB, ALT_ESC, 0x223, 0x18A, 0x0B6, 0x0CD, 0x0B5, 0x0E2, 0x0EA, 0x0E9]


def ep2(kb):
    """Consumer reports (report ID 2 + usage, little-endian) the SIE logged."""
    out = []
    for m in re.findall(r"\[SIE\] EP2 IN \d+ bytes:((?: [0-9a-f]{2})*)", kb.stderr_text()):
        b = [int(x, 16) for x in m.split()]
        if b and b[0] == 0x02:
            out.append(b[1] | (b[2] << 8))
    return out


class RowCase(unittest.TestCase):
    FW = F65_ANSI_FW

    @classmethod
    def setUpClass(cls):
        _need(cls.FW)

    def session(self, mac):
        kb = F65Sim(self.FW)
        self.addCleanup(kb.close)
        kb.boot(mac=mac)
        return kb

    def press_fn_rshift(self, kb, order):
        """Fn and Right Shift down in `order` ('fn' first or 'rshift' first);
        returns the reports that came out."""
        reps = []
        for k in ((kb.fn, RSFT) if order == "fn" else (RSFT, kb.fn)):
            reps += kb.down(k)
        return reps

    def tap_number(self, kb, n):
        """Tap the n-th number key (0-based) and return (keyboard reports,
        consumer usages) it produced."""
        c0 = len(ep2(kb))
        reps = kb.down(NUM[n]) + kb.up(NUM[n])
        return reps, ep2(kb)[c0:]

    def check_set(self, kb, want, alt):
        """Each of the 12 keys gives what `want` lists: an int = consumer
        usage (press, then 0), None = nothing, ('key', mods, usage) = a key
        with mods, 'F' = the F-key."""
        for n in range(12):
            w = want[n]
            with self.subTest(key=n + 1, alt=alt):
                reps, cons = self.tap_number(kb, n)
                for r in reps:
                    self.assertFalse(r[0] & MOD_RSFT, f"Right Shift leaked: {r}")
                if w == "F":
                    self.assertEqual(cons, [])
                    self.assertEqual([r for r in reps if any(r[2:])], [report(0, 0x3A + n)])
                elif w is None:
                    self.assertEqual((cons, [r for r in reps if any(r[2:])]), ([], []))
                elif isinstance(w, tuple):
                    self.assertEqual(cons, [])
                    self.assertIn(report(w[1], w[2]), reps)
                    self.assertEqual(reps[-1], report(), "all released")
                else:
                    self.assertEqual(cons, [w, 0])
                    self.assertEqual([r for r in reps if any(r[2:])], [])


class TestWin(RowCase):

    def test_fn_gives_f_keys(self):
        kb = self.session(mac=False)
        kb.down(kb.fn)
        self.check_set(kb, ["F"] * 12, alt=False)

    def test_fn_right_shift_gives_media_either_order(self):
        for order in ("fn", "rshift"):
            with self.subTest(order=order):
                kb = self.session(mac=False)
                reps = self.press_fn_rshift(kb, order)
                if order == "rshift":
                    self.assertEqual(reps[0], report(MOD_RSFT), "Right Shift alone is Shift")
                    self.assertEqual(reps[-1], report(), "withdrawn once Fn is down")
                else:
                    self.assertEqual(reps, [], "Right Shift under Fn never reaches the host")
                self.check_set(kb, MEDIA_WIN, alt=True)


class TestMac(RowCase):

    def test_fn_gives_media(self):
        kb = self.session(mac=True)
        kb.down(kb.fn)
        self.check_set(kb, MEDIA_MAC, alt=False)

    def test_fn_right_shift_gives_f_keys_either_order(self):
        for order in ("fn", "rshift"):
            with self.subTest(order=order):
                kb = self.session(mac=True)
                self.press_fn_rshift(kb, order)
                self.check_set(kb, ["F"] * 12, alt=True)


def keys_of(r):
    return [k for k in r[2:] if k]


def lone_shift_pairs(reps, bit=MOD_RSFT):
    """Places where `bit` goes on and off again with no key down in between:
    what a host sees as a Shift tapped on its own."""
    out, on_at = [], None
    for i, r in enumerate(reps):
        on = bool(r[0] & bit)
        if on and on_at is None:
            on_at = i
        elif not on and on_at is not None:
            if not any(keys_of(x) for x in reps[on_at:i]):
                out.append((on_at, i))
            on_at = None
    return out


class ShiftCase(RowCase):

    def run_ops(self, kb, ops):
        """ops: (True / False = down / up, key); 'FN' is the layout's Fn.
        Returns the whole report stream, and the index where Fn first went down."""
        reps, fn_at = [], None
        for down, key in ops:
            k = kb.fn if key == "FN" else key
            if down and key == "FN" and fn_at is None:
                fn_at = len(reps)
            reps += kb.down(k) if down else kb.up(k)
        kb.matrix.clear()
        reps += kb.step()
        return reps, fn_at


class TestShifts(ShiftCase):

    def test_right_shift_alone_is_shift(self):
        kb = self.session(mac=False)
        self.assertEqual(kb.tap(RSFT, A), report(MOD_RSFT, 0x04))

    def test_no_lone_shift_in_any_order(self):
        """Fn first: Right Shift never reaches the host. Right Shift first: it
        is Shift until Fn goes down (as pressed), and never comes back on its
        own afterwards, whichever key goes up first."""
        for first in ("FN", RSFT):
            second = RSFT if first == "FN" else "FN"
            for up_first in ("FN", RSFT):
                up_second = RSFT if up_first == "FN" else "FN"
                with self.subTest(first=first, up_first=up_first):
                    kb = self.session(mac=False)
                    reps, fn_at = self.run_ops(kb, [(True, first), (True, second), (False, up_first),
                                                    (False, up_second)])
                    after = reps[fn_at:]
                    self.assertEqual([r for r in after if r[0] & MOD_RSFT], [], reps)
                    if first == "FN":
                        self.assertEqual([r for r in reps if r[0] & MOD_RSFT], [], reps)

    def test_shift_comes_back_for_the_next_key(self):
        kb = self.session(mac=False)
        reps, _ = self.run_ops(kb, [(True, "FN"), (True, RSFT), (False, "FN"), (True, A), (False, A),
                                    (False, RSFT)])
        self.assertIn(report(MOD_RSFT, 0x04), reps)
        self.assertEqual(lone_shift_pairs(reps), [], reps)
        self.assertEqual(reps[-1], report())

    def test_fn_again_takes_shift_back_out(self):
        for mac, key, want in ((False, NUM[2], report(MOD_LALT, 0x2B)), (True, NUM[0], report(0, 0x3A))):
            for typed in (False, True):
                with self.subTest(mac=mac, typed=typed):
                    kb = self.session(mac=mac)
                    ops = [(True, "FN"), (True, RSFT), (False, "FN")]
                    if typed:                          # Shift came back for a key
                        ops += [(True, A), (False, A)]
                    ops += [(True, "FN"), (True, key), (False, key), (False, "FN"), (False, RSFT)]
                    reps, _ = self.run_ops(kb, ops)
                    self.assertIn(want, reps, "no Shift on the key")
                    for r in reps[reps.index(want):]:
                        self.assertFalse(r[0] & MOD_RSFT, reps)

    def test_number_key_held_across_the_fn_release(self):
        for first in ("FN", RSFT):
            for key in (NUM[2], NUM[10]):             # Alt+Tab, Volume -
                with self.subTest(first=first, key=key):
                    kb = self.session(mac=False)
                    second = RSFT if first == "FN" else "FN"
                    c0 = len(ep2(kb))
                    reps, fn_at = self.run_ops(kb, [(True, first), (True, second), (True, key), (False, "FN"),
                                                    (False, key), (False, RSFT)])
                    for r in reps[fn_at:]:
                        self.assertFalse(r[0] & MOD_RSFT, reps)
                        self.assertFalse((r[0] & MOD_LALT) and (r[0] & (MOD_RSFT | MOD_LSFT)), reps)
                    self.assertEqual(reps[-1:], [report()] if reps else [])
                    if key == NUM[10]:
                        self.assertEqual(ep2(kb)[c0:], [0x0EA, 0])

    def test_right_shift_released_while_fn_held(self):
        kb = self.session(mac=False)
        reps, _ = self.run_ops(kb, [(True, "FN"), (True, RSFT), (False, RSFT), (True, NUM[0]), (False, NUM[0]),
                                    (False, "FN")])
        self.assertIn(report(0, 0x3A), reps, "F1: the second Fn is up again")
        self.assertEqual([r for r in reps if r[0] & MOD_RSFT], [])

    def test_right_shift_again_after_a_release_under_fn(self):
        """Released under Fn it is up: no Shift for the next key, and Shift
        again when pressed again."""
        kb = self.session(mac=False)
        reps, _ = self.run_ops(kb, [(True, RSFT), (True, "FN"), (False, RSFT), (False, "FN"), (True, A), (False, A),
                                    (True, RSFT), (True, A), (False, A), (False, RSFT)])
        self.assertIn(report(0, 0x04), reps)
        self.assertLess(reps.index(report(0, 0x04)), reps.index(report(MOD_RSFT, 0x04)))
        kb = self.session(mac=False)
        reps, _ = self.run_ops(kb, [(True, "FN"), (True, RSFT), (False, RSFT), (False, "FN"), (True, A), (False, A)])
        self.assertIn(report(0, 0x04), reps, "the second Fn released under Fn is not pending as Shift")

    def test_held_modifiers_survive_the_keys_made_here(self):
        """Fn + Right Shift + Esc (~) with Left Shift held, and the Alt+Tab
        key with Left Alt held: the user's modifier stays when they go up."""
        kb = self.session(mac=False)
        reps, _ = self.run_ops(kb, [(True, LSFT), (True, "FN"), (True, RSFT), (True, ESC), (False, ESC),
                                    (False, RSFT), (False, "FN"), (True, A), (False, A), (False, LSFT)])
        self.assertIn(report(MOD_LSFT, 0x35), reps)
        self.assertIn(report(MOD_LSFT, 0x04), reps, "Left Shift still held after the ~")
        lalt = (2, 4)
        kb = self.session(mac=False)
        reps, _ = self.run_ops(kb, [(True, lalt), (True, "FN"), (True, RSFT), (True, NUM[2]), (False, NUM[2]),
                                    (False, RSFT), (False, "FN"), (True, A), (False, A), (False, lalt)])
        self.assertIn(report(MOD_LALT, 0x2B), reps)
        self.assertIn(report(MOD_LALT, 0x04), reps, "Left Alt still held after the Alt+Tab key")

    def test_left_shift_under_fn_stays_shift(self):
        kb = self.session(mac=False)
        self.assertEqual(kb.tap(LSFT, kb.fn, NUM[0]), report(MOD_LSFT, 0x3A))
        self.assertEqual(kb.tap(LSFT, kb.fn, ESC), report(MOD_LSFT, 0x35))

    def test_fn_right_shift_esc_is_tilde(self):
        for mac in (False, True):
            kb = self.session(mac=mac)
            self.assertEqual(kb.tap(kb.fn, RSFT, ESC), report(MOD_LSFT, 0x35))
            self.assertEqual(kb.tap(RSFT, kb.fn, ESC), report(MOD_LSFT, 0x35))

    def test_other_fn_keys_do_not_move(self):
        kb = self.session(mac=False)
        self.assertEqual(kb.tap(kb.fn, RSFT, (7, 2)), report(0, 0x0D), "Fn+J is still J")
        # build-6: Fn+Left is the lighting speed key (test_f65_backlight.py), Left without Fn
        self.assertEqual(kb.tap(RSFT, (13, 4)), report(MOD_RSFT, 0x50), "Shift+Left without Fn")


class TestShiftsUsjis(ShiftCase):
    """US-JIS on: it sees the Shift the report carries (withdrawn under Fn,
    back for the next key, and across an OS change under Fn)."""

    FW = F65_FW
    # ; under Fn (transparent; [ ] \ are lighting keys there since build-6): without
    # Shift the JIS ;-key, with Shift the JIS :-key (0x34)
    SCLN, KEY2 = (10, 2), (2, 0)
    HID_SCLN, HID_LBRC = 0x33, 0x2F

    def session(self, mac):
        kb = super().session(mac)
        kb.set_setting("usjis", 1)
        return kb

    def test_withdrawn_shift_is_not_used_for_substitution(self):
        kb = self.session(mac=False)
        reps, fn_at = self.run_ops(kb, [(True, RSFT), (True, "FN"), (True, self.SCLN), (False, self.SCLN),
                                        (False, "FN"), (False, RSFT)])
        self.assertIn(report(0, self.HID_SCLN), reps, "; without Shift: the JIS ;-key, not : (Shift withdrawn)")
        for r in reps[fn_at:]:
            self.assertFalse(r[0] & (MOD_RSFT | MOD_LSFT), reps)

    def test_shift_back_for_the_next_key_is_substituted(self):
        kb = self.session(mac=False)
        reps, _ = self.run_ops(kb, [(True, "FN"), (True, RSFT), (False, "FN"), (True, self.KEY2),
                                    (False, self.KEY2), (False, RSFT)])
        self.assertIn(report(0, self.HID_LBRC), reps, "Shift+2 = @: the JIS @-key without Shift")
        self.assertNotIn(report(MOD_RSFT, 0x1F), reps)

    def test_os_change_under_fn_with_right_shift_held(self):
        kb = self.session(mac=True)
        reps, fn_at = self.run_ops(kb, [(True, RSFT), (True, "FN"), (True, A), (False, A), (True, NUM[0]),
                                        (False, NUM[0]), (False, "FN"), (False, RSFT)])
        for r in reps[fn_at:]:
            self.assertFalse(r[0] & MOD_RSFT, reps)


class TestUsjisLayout(TestWin):
    FW = F65_FW


class TestAgainstStockAndDescriptor(unittest.TestCase):

    def test_win_media_is_the_stock_row(self):
        img = os.environ.get("SMK_F65_STOCK_IMAGE")
        if not img or not Path(img).exists():
            skip_or_fail("set SMK_F65_STOCK_IMAGE to the official AULA_F65_V1_FN_Ctrl_firmware.bin")
        d = Path(img).read_bytes()
        got = [d[0xB800 + slot * 24:0xB800 + slot * 24 + 4].hex() for slot in range(1, 13)]
        self.assertEqual(got, STOCK_WIN_ROW0)
        for entry, want in zip(STOCK_WIN_ROW0, MEDIA_WIN):
            e = int(entry, 16)
            if e >> 24 == 0x02:
                self.assertEqual(want, e & 0xFFFF)
            else:
                self.assertEqual(want, ("key", (e >> 16) & 0xFF, e & 0xFF))

    def test_usages_fit_the_consumer_descriptor(self):
        """Usage and logical maximum of the Consumer collection cover every
        usage the row sends (Launchpad 0x2A0 is the highest)."""
        for fw in (F65_ANSI_FW, F65_FW):
            _need(fw)
            data = read_ihex(fw)
            rom = bytes(data.get(a, 0) for a in range(max(data) + 1)) if isinstance(data, dict) else bytes(data)
            i = rom.find(bytes([0x05, 0x0C, 0x09, 0x01, 0xA1, 0x01, 0x85, 0x02]))
            self.assertGreater(i, 0, "Consumer collection in the report descriptor")
            seg = rom[i:i + 24]
            umax = seg[seg.index(0x2A) + 1] | (seg[seg.index(0x2A) + 2] << 8)
            lmax = seg[seg.index(0x26) + 1] | (seg[seg.index(0x26) + 2] << 8)
            top = max(u for u in MEDIA_MAC + MEDIA_WIN if isinstance(u, int))
            self.assertGreaterEqual(umax, top)
            self.assertGreaterEqual(lmax, top)


if __name__ == "__main__":
    unittest.main()
