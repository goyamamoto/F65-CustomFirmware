#!/usr/bin/env python3
"""Mouse keys on the aula-f65-v1 VIA images (MOUSEKEY_ENABLE, src/smk/mousekey.c),
in the patched uCsim.

- USB: interface 1 has a mouse report (ID 4: eight buttons, then x, y, wheel
  and pan, one signed byte each, relative), sent on EP1.
- A movement key moves one step (8) at once and keeps moving, the step growing
  to 80; two directions move diagonally; the movement stops with the key.
- The buttons go down and up with their keys; the wheel steps every 80 ms;
  KC_MS_ACCEL2 held gives the full step at once.
- Over the radio (2.4 GHz here) the short report frame carries the buttons
  and the movement (x, y as 16-bit little-endian, wheel, pan).

The keys are set from VIA, so the images are the VIA ones: SMK_F65_VIA_FIRMWARE
and SMK_F65_ANSI_VIA_FIRMWARE, as for test_f65_via.
"""

import re
import unittest

from test_f65_via import ViaCase, VIA_ANSI_FW, A
from f65_radio import SWITCH_24G
from f65_devices import reports

REPORT_ID_MOUSE = 4
KC_MS_UP, KC_MS_DOWN, KC_MS_LEFT, KC_MS_RIGHT = 0xCD, 0xCE, 0xCF, 0xD0
KC_MS_BTN1, KC_MS_BTN2 = 0xD1, 0xD2
KC_MS_WH_UP = 0xD9
KC_MS_ACCEL2 = 0xDF
S_KEY, D_KEY = (2, 2), (3, 2)


def s8(b):
    return b - 256 if b > 127 else b


def s16(lo, hi):
    v = lo | hi << 8
    return v - 65536 if v > 32767 else v


class MouseCase(ViaCase):
    def mouse(self, kb, since=0):
        """The mouse reports on EP1 from packet `since` on: (buttons, x, y, wheel, pan)."""
        return [(p[1], s8(p[2]), s8(p[3]), s8(p[4]), s8(p[5]))
                for p in kb.ep1_packets()[since:] if len(p) == 6 and p[0] == REPORT_ID_MOUSE]

    def set_keys(self, kb, **keys):
        """VIA sets keycodes on the Win base layer: a=..., s=..., d=..."""
        for name, kc in keys.items():
            col, row = {"a": A, "s": S_KEY, "d": D_KEY}[name]
            kb.set_keycode(0, row, col, kc)


class TestMouseUsb(MouseCase):
    def test_interface_1_has_the_mouse_report(self):
        kb = self.kb()
        mark = len(kb.stderr_text())
        kb.setup_packet([0x81, 0x06, 0x00, 0x22, 1, 0x00, 0xFF, 0x00])
        kb.ms(10)
        d = bytes(int(x, 16) for m in re.findall(r"\[SIE\] EP0 IN\[\d+\] \d+ bytes:((?: [0-9a-f]{2})*)",
                                                  kb.stderr_text()[mark:]) for x in m.split())
        i = d.index(bytes([0x05, 0x01, 0x09, 0x02, 0xA1, 0x01, 0x85, REPORT_ID_MOUSE]))
        mouse = d[i:]
        self.assertIn(bytes([0x05, 0x09, 0x19, 0x01, 0x29, 0x08]), mouse, "buttons 1-8")
        self.assertIn(bytes([0x09, 0x30, 0x09, 0x31]), mouse, "x, y")
        self.assertIn(bytes([0x09, 0x38]), mouse, "wheel")
        self.assertIn(bytes([0x0A, 0x38, 0x02]), mouse, "AC Pan")
        self.assertEqual(mouse.count(bytes([0x81, 0x06])), 3, "x / y, wheel, pan: relative")

    def test_a_movement_key_moves_and_speeds_up(self):
        kb = self.kb()
        self.set_keys(kb, a=KC_MS_RIGHT)
        n = len(kb.ep1_packets())
        kb.press(A, ms=800)
        got = self.mouse(kb, n)
        self.assertEqual(got[0], (0, 8, 0, 0, 0), "one step at once")
        self.assertTrue(all(b == 0 and x > 0 and y == 0 and v == 0 and h == 0 for b, x, y, v, h in got), got)
        xs = [x for _, x, _, _, _ in got]
        self.assertEqual(xs[-1], 80, "the full step after the time to max")
        self.assertEqual(xs[1:], sorted(xs[1:]), "the step grows")
        self.assertGreaterEqual(len(got), 31)
        kb.release(A, ms=40)
        m = len(kb.ep1_packets())
        kb.ms(200)
        self.assertEqual(self.mouse(kb, m), [], "no movement after the key is up")

    def test_two_directions_move_diagonally(self):
        kb = self.kb()
        self.set_keys(kb, a=KC_MS_RIGHT, s=KC_MS_UP)
        kb.press(A, S_KEY, ms=300)
        both = [r for r in self.mouse(kb) if r[1] and r[2]]
        self.assertTrue(both, "steps on both axes")
        self.assertTrue(all(x > 0 and y == -x for _, x, y, _, _ in both), both)
        kb.release(A, S_KEY, ms=40)

    def test_buttons_go_down_and_up_with_their_keys(self):
        kb = self.kb()
        self.set_keys(kb, a=KC_MS_BTN1, s=KC_MS_BTN2)
        n = len(kb.ep1_packets())
        kb.press(A, ms=40)
        kb.press(S_KEY, ms=40)
        kb.release(A, ms=40)
        kb.release(S_KEY, ms=40)
        self.assertEqual(self.mouse(kb, n), [(1, 0, 0, 0, 0), (3, 0, 0, 0, 0), (2, 0, 0, 0, 0), (0, 0, 0, 0, 0)])

    def test_the_wheel_steps_every_80_ms(self):
        kb = self.kb()
        self.set_keys(kb, a=KC_MS_WH_UP)
        n = len(kb.ep1_packets())
        kb.press(A, ms=500)
        got = self.mouse(kb, n)
        self.assertEqual(got[0], (0, 0, 0, 1, 0))
        self.assertTrue(all(v > 0 and x == 0 and y == 0 for _, x, y, v, _ in got), got)
        self.assertIn(len(got), range(5, 9), "one at once, after 10 ms, then every 80 ms")
        kb.release(A, ms=40)

    def test_accel2_held_gives_the_full_step(self):
        kb = self.kb()
        self.set_keys(kb, a=KC_MS_RIGHT, s=KC_MS_ACCEL2)
        kb.press(S_KEY, ms=40)
        n = len(kb.ep1_packets())
        kb.press(A, ms=100)
        got = self.mouse(kb, n)
        self.assertGreaterEqual(len(got), 5, got)
        self.assertTrue(all(x == 80 for _, x, _, _, _ in got), got)
        kb.release(A, S_KEY, ms=40)


class TestMouseRadio(MouseCase):
    """In the 2.4 GHz position: the short frames carry the mouse fields."""

    def short_frames(self, kb, since):
        return [f for f in reports(kb.frames(since)) if f[1] == 0x03]

    def into_24g(self, kb):
        kb.switch(SWITCH_24G, 200)
        kb.status(0, 3)
        kb.ms(20)

    def test_movement_and_buttons_in_the_short_frame(self):
        kb = self.kb()
        self.set_keys(kb, a=KC_MS_RIGHT, s=KC_MS_BTN1)
        self.into_24g(kb)
        m = kb.mark()
        kb.press(A, ms=300)
        kb.release(A, ms=60)
        moves = self.short_frames(kb, m)
        xs = [s16(f[6], f[7]) for f in moves]
        self.assertTrue(moves, "short frames while the key is held")
        self.assertTrue(all(len(f) == 13 for f in moves))
        self.assertEqual(xs[0], 8, "the first step")
        self.assertTrue(all(x > 0 for x in xs), xs)
        self.assertTrue(all(s16(f[8], f[9]) == 0 and f[10] == 0 and f[11] == 0 for f in moves))
        m = kb.mark()
        kb.press(S_KEY, ms=60)
        kb.release(S_KEY, ms=60)
        self.assertEqual([(f[5], s16(f[6], f[7])) for f in self.short_frames(kb, m)], [(1, 0), (0, 0)])

    def test_left_up_and_the_wheel_in_the_short_frame(self):
        """Negative movement keeps its sign in the 16-bit x and y (high byte
        0xFF); a wheel step goes in its own byte."""
        kb = self.kb()
        self.set_keys(kb, a=KC_MS_LEFT, s=KC_MS_UP, d=KC_MS_WH_UP)
        self.into_24g(kb)
        m = kb.mark()
        kb.press(A, S_KEY, ms=200)
        kb.release(A, S_KEY, ms=60)
        moves = [f for f in self.short_frames(kb, m) if s16(f[6], f[7]) or s16(f[8], f[9])]
        self.assertTrue(moves, "short frames while the keys are held")
        xs = [(f[6], f[7]) for f in moves if f[6] or f[7]]
        ys = [(f[8], f[9]) for f in moves if f[8] or f[9]]
        self.assertTrue(xs and ys, [f[5:12] for f in moves])
        self.assertTrue(all(hi == 0xFF and s16(lo, hi) < 0 for lo, hi in xs + ys), [f[5:12] for f in moves])
        both = [f for f in moves if s16(f[6], f[7]) and s16(f[8], f[9])]
        self.assertTrue(both and all(s16(f[6], f[7]) == s16(f[8], f[9]) for f in both), "diagonal")
        m = kb.mark()
        kb.press(D_KEY, ms=40)
        kb.release(D_KEY, ms=60)
        wheel = [s8(f[10]) for f in self.short_frames(kb, m) if f[10]]
        self.assertTrue(wheel and all(w == 1 for w in wheel), wheel)
        self.assertTrue(all(s16(f[6], f[7]) == 0 and s16(f[8], f[9]) == 0 and f[11] == 0
                            for f in self.short_frames(kb, m)))

    def test_a_held_mouse_button_keeps_the_board_awake(self):
        """A drag (button 1 held, no movement) is activity: the idle seconds,
        put at 55 (the linked sleep comes at 61), go back to 0 while the
        button is held."""
        kb = self.kb()
        self.set_keys(kb, s=KC_MS_BTN1)
        self.into_24g(kb)
        kb.press(S_KEY, ms=40)
        idle_s = kb._xdata_static("f65_rf", "idle_s")
        kb.cmd("set mem xram 0x%x 0x37 0x00" % idle_s)
        kb.cmd("set mem xram 0x%x 0x00 0x00" % kb._xdata_static("f65_rf", "idle_ms"))
        kb.ms(1500)
        self.assertNotIn("sleep: PCON", kb.stderr_text())
        lo, hi = kb.get_xram(idle_s, 2)
        self.assertLess(lo | hi << 8, 2, "the idle seconds start again while the button is held")
        kb.release(S_KEY, ms=40)

    def test_the_movement_does_not_run_on_after_a_busy_module(self):
        """The module busy (P4.7 low) while the key is held: once it is free,
        the six frames the queue holds go out, then at most two frames' worth
        (254), then nothing more."""
        kb = self.kb()
        self.set_keys(kb, a=KC_MS_RIGHT)
        self.into_24g(kb)
        kb.ms(700)
        kb.radio.ready(False)
        kb.press(A, ms=400)
        kb.release(A, ms=20)
        m = kb.mark()
        kb.radio.ready(True)
        kb.ms(200)
        xs = [s16(f[6], f[7]) for f in self.short_frames(kb, m)]
        self.assertEqual(len(xs), 6 + 2, xs)
        self.assertEqual(xs[6:], [127, 127], "the two frames' worth that waited, no more")


class TestMouseUsbAnsi(TestMouseUsb):
    FW = VIA_ANSI_FW


if __name__ == "__main__":
    unittest.main()
