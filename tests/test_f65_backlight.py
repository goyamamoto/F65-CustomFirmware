#!/usr/bin/env python3
"""aula-f65-v1 build-6: the key backlight and the side lights (backlight.c).

The oracle is the stock V1 image (SMK_F65_STOCK_IMAGE) in the same simulator:
its LED task 0x1108 and side-light task 0x5872 run from a ROM stub with the
interrupts off, the 1 ms counters advanced by the harness as its PWM4
interrupt does, one call per simulated ms (the harness of the effect
specification in the author's analysis notes, not published). Ours runs the
same way: backlight_task from a ROM stub, one call per ms until its work is
done. After every ms the two 8-bit frames (the stock's fb8 at 0x0155, our
led_fb8, same layout) must be equal on every key cell, and the side-light
cells equal to the stock's fb16 row 0 (value * 4 + phase).

uCsim does not model the SH68F90 16/8 divide the stock's brightness gain
uses (0xEC5C); the stock run has that routine patched to the C51 16/16
divide in the simulator's ROM (the same quotient), as the specification's
harness does.

The rest runs the whole image on the F65 board (f65_devices.OursF65): the
lighting keys, saving, the battery idle-off, the LED invariants with every
effect at full brightness, and the watchdog, P4.7 and report timing with
the effects running.

    python3 -m unittest discover -s tests -p test_f65_backlight.py
"""

import re
import unittest
from pathlib import Path

from devices import UcsimSession
from f65_radio import SWITCH_USB, SWITCH_24G, SWITCH_BT, status_frame
from f65_devices import OursF65, stock_image, stock_hex
from test_f65 import F65_FW, F65_ANSI_FW, _need, field_offset

STUB = 0xE800                 # free ROM in our image (code ends below 0xB000; settings at 0xEC00)
STOCK_STUB = 0xEFD0           # free ROM at the end of the stock image
STOCK_FB8, STOCK_FB16 = 0x0155, 0x05A2
STOCK_PHASE = [0xB5, 0xB4, 0xB6, 0xB8, 0xB7, 0xB9, 0xBB, 0xBA, 0xBC, 0xBE, 0xBD, 0xBF,
               0xC1, 0xC0, 0xC2, 0xC4, 0xC3, 0xC5]
RAND0 = 0xCF54CD5E            # the stock's rand state after boot and the power-on sweep (the spec's vectors)
SIDE_SPEED_OF_KEY = [0, 0, 1, 1, 2]
KEY_OF_SIDE_SPEED = {0: 0, 1: 2, 2: 4}


def _vals(out):
    vals = []
    for line in out.splitlines():
        m = re.match(r"\s*0x[0-9a-fA-F]+\s+((?:[0-9a-fA-F]{2} )+)", line)
        if m:
            vals += [int(x, 16) for x in m.group(1).split()]
    return vals


def _setx(sess, a, vals):
    if isinstance(vals, int):
        vals = [vals]
    for i in range(0, len(vals), 32):                    # uCsim takes a short command line only
        sess.cmd("set mem xram 0x%x " % (a + i) + " ".join("0x%02x" % v for v in vals[i:i + 32]))


class StockEngine:
    """The stock LED tasks, called directly."""

    _hex = None

    def __init__(self):
        if StockEngine._hex is None:
            StockEngine._hex = stock_hex(stock_image())
        s = self.s = UcsimSession(StockEngine._hex)
        s.cmd("reset")
        s.brk(0x6313)
        s.run()
        s.cmd("delete")
        for _ in range(30):
            s.cmd("step 200000")
        s.cmd("set mem sfr 0xa8 0x00")                              # EA off
        s.cmd("set mem rom 0xec5c 0x7c 0x00 0x02 0x48 0xaf")        # MOV R4,#0; LJMP ?C?UIDIV
        # One ms as the PWM4 interrupt 0x7FBB counts it (0x0303:0x0304 + 1, 0x097E + 1,
        # 0x0992 + 1), then 0x1108 and 0x5872, as the main loop calls them.
        code = [0x75, 0xB1, 0x00,                                   # the watchdog kick (RSTSTAT = 0)
                0x90, 0x03, 0x04, 0xE0, 0x24, 0x01, 0xF0, 0x50, 0x06, 0x90, 0x03, 0x03, 0xE0, 0x04, 0xF0,
                0x90, 0x09, 0x7E, 0xE0, 0x04, 0xF0, 0x90, 0x09, 0x92, 0xE0, 0x04, 0xF0,
                0x12, 0x11, 0x08, 0x12, 0x58, 0x72, 0x80, 0xFE]
        self.ms_stub = STOCK_STUB - 0x40
        self.ms_halt = self.ms_stub + len(code) - 2
        s.cmd("set mem rom 0x%x " % self.ms_stub + " ".join("0x%02x" % b for b in code))
        s.cmd("break 0x%x" % (STOCK_STUB + 3))
        s.cmd("break 0x%x" % self.ms_halt)

    def close(self):
        self.s.close()

    def x(self, a, n=1):
        return self.s.get_xram(a, n)

    def setx(self, a, vals):
        _setx(self.s, a, vals)

    def bit(self, spec, val):
        byte, b = spec.split(".")
        byte, b = int(byte, 16), int(b)
        cur = _vals(self.s.cmd("dump iram 0x%x 0x%x" % (byte, byte)))[0]
        cur = (cur | (1 << b)) if val else (cur & ~(1 << b))
        self.s.cmd("set mem iram 0x%x 0x%02x" % (byte, cur))

    def call(self, addr):
        s = self.s
        s.cmd("set mem rom 0x%x 0x12 0x%02x 0x%02x 0x80 0xfe" % (STOCK_STUB, addr >> 8, addr & 0xFF))
        s.cmd("set mem sfr 0xd0 0x00")
        s.cmd("pc 0x%x" % STOCK_STUB)
        s.cmd("run")

    def setup(self, eff, colour, bri, speed, side=(1, 0, 2, 1), rand=RAND0):
        for b in ("0x26.3", "0x23.5", "0x27.1", "0x2a.3", "0x2b.1", "0x23.1", "0x23.3", "0x2d.4", "0x2b.3",
                  "0x2b.4", "0x2a.5"):
            self.bit(b, 0)
        self.setx(0x0D1D, 0)
        self.setx(0x0002, 0)
        self.setx(0x0427, 0)
        self.setx(0x0896, 0)
        self.setx(0x0311, 0)
        self.setx(0x0315, 0)
        self.setx(0x00A0, eff)
        self.setx(0x0344 + 2 * eff, [bri, (speed << 4) | colour])
        self.setx(0x0897, 0xFF)                                     # the change path on the first call (as ours)
        for col in range(16):                                       # side cells dark (ours start from 0)
            self.setx(STOCK_FB16 + col * 36, [0, STOCK_PHASE[0], 0, STOCK_PHASE[1], 0, STOCK_PHASE[2]])
        self.setx(0x031E, list(side))                               # side effect, colour, brightness, speed
        self.setx(0x0325, 0)
        self.setx(0x0F6E, list(rand.to_bytes(4, "big")))
        self.setx(0x0EC9, 0)
        self.setx(0x0303, [0, 0])
        self.setx(0x097E, 0)
        self.setx(0x0992, 0)
        self.setx(0x0ECA, 0)
        self.bit("0x28.7", 1)
        self.setx(0x039E, [0xFF] * 10)
        self.setx(0x0965, 0)
        self.setx(0x08E1, 0)
        self.setx(0x0D02, [0] * 21)

    def settings(self, eff, colour, bri, speed):
        self.setx(0x00A0, eff)
        self.setx(0x0344 + 2 * eff, [bri, (speed << 4) | colour])

    def key(self, k, eff, press=True):
        """0x96A7 for effects 4, 7, 12: the held mask (12) and the ring."""
        col, row = k // 6, k % 6
        if eff == 12:
            h = self.x(0x0D02 + col)[0]
            self.setx(0x0D02 + col, (h | (1 << row)) if press else (h & ~(1 << row) & 0xFF))
        if press:
            wr = self.x(0x08E1)[0]
            if self.x(0x039E + wr)[0] == 0xFF:
                self.setx(0x039E + wr, k)
                self.setx(0x08E1, (wr + 1) % 10)

    def ms(self, side=True):
        self.s.cmd("pc 0x%x" % self.ms_stub)
        self.s.cmd("run")

    def fb8(self):
        return self.x(STOCK_FB8, 16 * 18)

    def side(self):
        """The 14 side cells as (col, [slot values]), from fb16 row 0."""
        raw = self.x(STOCK_FB16, 16 * 36)
        out = {}
        for i in range(7):
            for col in (9 + i, i):
                vals = []
                for c in range(3):
                    w = raw[col * 36 + c * 2] << 8 | raw[col * 36 + c * 2 + 1]
                    vals.append((w - STOCK_PHASE[c]) // 4 if (w - STOCK_PHASE[c]) % 4 == 0 else None)
                out[col] = vals
        return out


class OurEngine:
    """backlight.c in our image, called directly (after a normal boot)."""

    def __init__(self, fw):
        _need(fw)
        d = self.d = OursF65(fw)
        d.start(SWITCH_USB)
        d.ms(20)
        d.cmd("set mem sfr 0xa8 0x00")                                 # EA off: rf_ms stands still
        d.cmd("set mem xram 0x%x 0x00" % d._a("matrix_updated"))     # no scan pending (backlight_task waits for none)
        self.fw = fw
        self.us = d._a("user_settings")
        self.bl = self.us + field_offset(fw, "backlight", "backlight_defaults")   # bl_magic
        self.fb8 = d._a("led_fb8")
        L = self.label
        self.a = {n: L(n) for n in ("pending_ms", "op_i", "n_ops", "c0303", "c097e", "s_ms", "s_phase", "s_reset",
                                     "rnd_state", "cur_eff", "ring", "ring_wr", "ring_rd", "ec9", "held", "running",
                                     "key_k")}
        task = d._a("backlight_task")
        pend, op_i, n_ops = self.a["pending_ms"], self.a["op_i"], self.a["n_ops"]
        # pending_ms = 1; do { RSTSTAT = 0 (the watchdog kick); backlight_task(); } while (op_i < n_ops); sjmp $
        code = [0x90, pend >> 8, pend & 0xFF, 0x74, 0x01, 0xF0, 0xA3, 0xE4, 0xF0,
                0x75, 0xB1, 0x00,
                0x12, task >> 8, task & 0xFF,
                0x90, n_ops >> 8, n_ops & 0xFF, 0xE0, 0xFF,
                0x90, op_i >> 8, op_i & 0xFF, 0xE0, 0xC3, 0x9F,
                0x40, 0x00,
                0x80, 0xFE]
        loop = 9
        code[27] = (loop - 28) & 0xFF
        self.halt = STUB + 28
        d.cmd("set mem rom 0x%x " % STUB + " ".join("0x%02x" % b for b in code))
        d.cmd("break 0x%x" % self.halt)

    def label(self, name):
        rst = Path(self.fw).with_suffix(".ihx.p") / "backlight.rst"
        for line in rst.read_text().splitlines():
            m = re.match(r"^\s+([0-9A-F]{6})\s+\d+ _%s::?$" % re.escape(name), line)
            if m:
                return int(m.group(1), 16)
        raise KeyError(name)

    def close(self):
        self.d.close()

    def x(self, a, n=1):
        return self.d.get_xram(a, n)

    def setx(self, a, vals):
        _setx(self.d, a, vals)

    def settings(self, eff, colour, bri, speed, side=None):
        self.setx(self.bl + 1, [1, eff])                               # bl_on, bl_effect
        self.setx(self.bl + 3 + 2 * eff, [bri, (speed << 4) | colour])
        if side is not None:
            self.setx(self.bl + 39, list(side))                        # side effect, colour, brightness

    def setup(self, eff, colour, bri, speed, side=(1, 0, 2), rand=RAND0):
        self.settings(eff, colour, bri, speed, side)
        a = self.a
        self.setx(a["rnd_state"], list(rand.to_bytes(4, "little")))
        for n in ("ec9", "c097e", "s_ms", "s_phase", "op_i", "n_ops", "ring_wr", "ring_rd"):
            self.setx(a[n], 0)
        self.setx(a["c0303"], [0, 0])
        self.setx(a["pending_ms"], [0, 0])
        self.setx(a["s_reset"], 1)
        self.setx(a["cur_eff"], 0xFF)
        self.setx(a["key_k"], 0xFF)
        self.setx(a["ring"], [0xFF] * 10)
        self.setx(a["held"], [0] * 16)
        self.setx(self.fb8, [0] * (16 * 18))

    def key(self, k, eff, press=True):
        col, row = k // 6, k % 6
        a = self.a
        if eff == 12:
            h = self.x(a["held"] + col)[0]
            self.setx(a["held"] + col, (h | (1 << row)) if press else (h & ~(1 << row) & 0xFF))
        if press:
            wr = self.x(a["ring_wr"])[0]
            if self.x(a["ring"] + wr)[0] == 0xFF:
                self.setx(a["ring"] + wr, k)
                self.setx(a["ring_wr"], (wr + 1) % 10)

    def ms(self):
        d = self.d
        d.cmd("pc 0x%x" % STUB)
        d.cmd("run")

    def fb(self):
        return self.x(self.fb8, 16 * 18)


def diff_cells(ours, stock, rows=range(1, 6)):
    out = []
    for col in range(16):
        for row in rows:
            o = ours[col * 18 + row * 3: col * 18 + row * 3 + 3]
            s = stock[col * 18 + row * 3: col * 18 + row * 3 + 3]
            if o != s:
                out.append(((col, row), "".join("%02x" % v for v in o), "".join("%02x" % v for v in s)))
    return out


class LockStep(unittest.TestCase):
    """Runs a scenario on both engines and compares after every ms."""

    FW = F65_FW
    stock = ours = None

    @classmethod
    def setUpClass(cls):
        cls.stock = StockEngine()
        cls.ours = OurEngine(cls.FW)

    @classmethod
    def tearDownClass(cls):
        for e in (cls.stock, cls.ours):
            if e:
                e.close()

    def run_case(self, eff, colour, bri, speed, n_ms, keys=None, changes=None, side=None, check_side=False):
        """keys: {ms: [(k, press)]} before that ms; changes: {ms: (eff, colour, bri, speed)}."""
        st, us = self.stock, self.ours
        if side is not None:
            assert SIDE_SPEED_OF_KEY[speed] == side[3], "the side speed follows the key speed"
        st.setup(eff, colour, bri, speed, side=side or (1, 0, 2, SIDE_SPEED_OF_KEY[speed]))
        us.setup(eff, colour, bri, speed, side=(side or (1, 0, 2, 0))[:3])
        cur = eff
        lit = 0
        for t in range(1, n_ms + 1):
            for k, press in (keys or {}).get(t, []):
                st.key(k, cur, press)
                us.key(k, cur, press)
            if changes and t in changes:
                cur, c, b, sp = changes[t]
                st.settings(cur, c, b, sp)
                us.settings(cur, c, b, sp)
            st.ms(side=check_side)
            us.ms()
            a, b_ = us.fb(), st.fb8()
            d = diff_cells(a, b_)
            self.assertEqual(d, [], "effect %d colour %d bri %d speed %d: ms %d, cells (ours, stock)"
                             % (eff, colour, bri, speed, t))
            lit += sum(1 for v in a if v)
            if check_side:
                s = st.side()
                for col, vals in s.items():
                    o = us.x(us.fb8 + col * 18, 3)
                    self.assertEqual(o, vals, "side cell (%d, 0) at ms %d" % (col, t))
        return lit


class TestEffectsAgainstStock(LockStep):
    """Every effect of the Fn + \\ cycle, frame for frame against the stock."""

    def check(self, eff, colour, bri, speed, n_ms=400, **kw):
        lit = self.run_case(eff, colour, bri, speed, n_ms, **kw)
        if bri:
            self.assertGreater(lit, 0, "something was lit")
        print("\n[oracle] effect %2d colour %d bri %d speed %d: %d ms equal" % (eff, colour, bri, speed, n_ms))

    # One check per effect: a few hundred ms (at least a few steps of it) with
    # fixed settings, rand state and keys; mostly colour 7 (the random colours
    # follow the stock's rand sequence), full or mixed brightness, fast speeds.
    def test_01_static_rings(self):
        self.check(1, 7, 4, 3, 400)

    def test_02_breathing(self):
        self.check(2, 7, 4, 4, 300)

    def test_03_rainbow_cycle(self):
        self.check(3, 7, 4, 4, 200)

    def test_04_reactive_line(self):
        self.check(4, 7, 3, 4, 300, keys={1: [(0x1B, True)], 30: [(0x1B, False)], 60: [(0x0A, True), (0x20, True)],
                                          90: [(0x0A, False), (0x20, False)]})

    def test_05_rain(self):
        self.check(5, 7, 2, 4, 300)

    def test_07_reactive_ripple(self):
        self.check(7, 1, 3, 4, 250, keys={1: [(0x1B, True)], 20: [(0x1B, False)]})

    def test_08_twinkle(self):
        self.check(8, 7, 4, 4, 500)

    def test_10_serpentine(self):
        self.check(10, 7, 3, 4, 200)

    def test_11_wave(self):
        self.check(11, 4, 3, 4, 200)

    def test_12_reactive_fade(self):
        self.check(12, 7, 4, 4, 250, keys={2: [(0x14, True)], 3: [(0x4C, True)], 24: [(0x14, False)],
                                           60: [(0x4C, False)], 100: [(0x0A, True)], 130: [(0x0A, False)]})

    def test_13_auto_ripple(self):
        self.check(13, 2, 4, 4, 300)

    def test_15_gradient(self):
        self.check(15, 7, 2, 4, 150)

    def test_16_rows(self):
        self.check(16, 7, 4, 3, 200)

    def test_17_moving_rings(self):
        self.check(17, 0, 4, 4, 200)

    def test_every_speed_and_colour(self):
        """Every effect of the cycle at every speed (colour 7), and at every
        colour 0-6 where the effect takes one (speed 4): shorter runs than the
        one-setting checks above, each a few steps of the effect at that
        speed. Effect 1 has no speed; 3, 15, 16 and 17 no colour choice."""
        keys = {1: [(0x1B, True)], 20: [(0x1B, False)], 50: [(0x0A, True)], 70: [(0x0A, False)]}
        runs = 0
        for eff in CYCLE[1:]:
            for speed in range(1 if eff == 1 else 5):
                with self.subTest(effect=eff, speed=speed):
                    self.run_case(eff, 7, 4, speed, 160 if speed < 2 else 100, keys=keys)
                    runs += 1
            if eff not in (3, 15, 16, 17):
                for col in range(7):
                    with self.subTest(effect=eff, colour=col):
                        self.run_case(eff, col, 3, 4, 80, keys=keys)
                        runs += 1
        print("\n[oracle] %d effect / speed / colour runs equal" % runs)

    def test_changes_mid_run(self):
        """Effect, colour, brightness and speed changes on the way (the change
        path and the re-inits of effects 1 and 8)."""
        self.check(11, 7, 4, 3, 400, changes={60: (11, 7, 2, 4), 100: (4, 7, 2, 4), 140: (8, 7, 2, 4),
                                               220: (8, 7, 3, 2), 280: (1, 3, 3, 2), 320: (1, 3, 4, 2),
                                               360: (0, 0, 0, 0)},
                   keys={110: [(0x10, True)], 120: [(0x10, False)]})


class TestSideLightsAgainstStock(LockStep):
    """The side lights (0x5872): every side effect against the stock."""

    def test_side_effects(self):
        for side in ((1, 0, 4, 1), (2, 0, 3, 2), (3, 5, 2, 1), (4, 6, 4, 2), (0, 0, 2, 1)):
            with self.subTest(side=side):
                self.run_case(1, 7, 4, KEY_OF_SIDE_SPEED[side[3]], 200, side=side, check_side=True)
                print("\n[oracle] side %s: 200 ms equal" % (side,))


# ------------------------------------------------------------------ the whole image
from f65_devices import reports                                            # noqa: E402
import test_f65_indicators as ind                                           # noqa: E402
from test_f65_indicators import IndRig, IndCase, PHASE, CAPS_CELL, FN_CELL, LINK_CELL  # noqa: E402
from test_f65_led import check_invariants                                   # noqa: E402

NVM_BASE = 0xEC00
BL_OFF = 42                        # the lighting fields after bl_magic ... sl_brightness
BSLS, RBRC, LBRC = (13, 1), (12, 1), (11, 1)
UP, DOWN, LEFT, RIGHT = (14, 3), (14, 4), (13, 4), (15, 4)
SLSH, DOT, COMM = (10, 3), (9, 3), (8, 3)
A_KEY = (1, 2)
CYCLE = [0, 1, 2, 3, 4, 5, 7, 8, 10, 11, 12, 13, 15, 16, 17]


class LitRig(IndRig):
    """IndRig with the lighting on (as after a fresh flash)."""

    LIGHTING = True

    def bl(self):
        return self.lighting_setting()

    def fields(self):
        v = self.get_xram(self.bl(), BL_OFF)
        return dict(magic=v[0], on=v[1], effect=v[2], cfg=[(v[3 + 2 * e], v[4 + 2 * e] >> 4, v[4 + 2 * e] & 15)
                                                            for e in range(18)],
                    side=tuple(v[39:42]))

    def cfg(self, eff=None):
        f = self.fields()
        return f["cfg"][f["effect"] if eff is None else eff]    # (brightness, speed, colour)

    def set_lighting(self, eff, colour=7, bri=4, speed=4, side=(1, 0, 4)):
        a = self.bl()
        self.cmd("set mem xram 0x%x 0x01 0x%02x" % (a + 1, eff))
        self.cmd("set mem xram 0x%x 0x%02x 0x%02x" % (a + 3 + 2 * eff, bri, (speed << 4) | colour))
        self.cmd("set mem xram 0x%x 0x%02x 0x%02x 0x%02x" % ((a + 39,) + tuple(side)))

    def fn_tap(self, key, n=1):
        self.press(self.fn, ms=20)
        for _ in range(n):
            self.tap(key, hold=20, after=20)
        self.release(self.fn, ms=20)

    def duty_table(self):
        base = self._xdata_static("led", "led_fb")
        raw = self.get_xram(base, 16 * 36)
        return [[raw[c * 36 + j * 2] << 8 | raw[c * 36 + j * 2 + 1] for j in range(18)] for c in range(16)]

    def backlit(self, skip=()):
        """Cells (col, row) of the duty table above their phase, bar `skip`."""
        t = self.duty_table()
        return {(c, j // 3) for c in range(16) for j in range(18) if t[c][j] != PHASE[j] and (c, j // 3) not in skip}

    def flash_ops(self):
        return re.findall(r"\[SIE\] FLASH (\w+) ([0-9a-f]{4})", self.stderr_text())

    def label_bl(self, name):
        rst = Path(self.firmware).with_suffix(".ihx.p") / "backlight.rst"
        for line in rst.read_text().splitlines():
            m = re.match(r"^\s+([0-9A-F]{6})\s+\d+ _%s::?$" % re.escape(name), line)
            if m:
                return int(m.group(1), 16)
        raise KeyError(name)


class LitCase(IndCase):
    FW = F65_FW

    def lit(self, position=SWITCH_USB, fw=None, stub_delays=True, rhythm=False):
        fw = fw or self.FW
        _need(fw)
        d = LitRig(fw)
        self.addCleanup(self._close, d)
        d.start(position, stub_delays=stub_delays)
        if rhythm:
            d.rhythm()
        return d


class TestLightingKeys(LitCase):
    """The lighting keys under Fn (both layouts), as the stock's handlers; none
    of them reaches the host."""

    def assertNothingSent(self, d, n0, what):
        for r in d.ep1_reports()[n0:]:
            self.assertEqual(r[2:], [0] * (len(r) - 2), "%s: a key reached the host: %s" % (what, r))

    def test_defaults(self):
        for fw in (F65_FW, F65_ANSI_FW):
            with self.subTest(fw=Path(fw).name):
                d = self.lit(fw=fw)
                f = d.fields()
                self.assertEqual((f["magic"], f["on"], f["effect"], f["side"]), (0xB6, 1, 11, (1, 0, 2)))
                self.assertEqual(set(f["cfg"]), {(2, 3, 7)}, "every effect: brightness 2 of 4, speed 3, colour 7")
                d.ms(100)
                self.assertGreater(len(d.backlit()), 50, "effect 11 lights the keys at power-up")

    def test_effect_cycle(self):
        for fw in (F65_FW, F65_ANSI_FW):
            with self.subTest(fw=Path(fw).name):
                d = self.lit(fw=fw)
                n0 = len(d.ep1_reports())
                seen = []
                d.press(d.fn, ms=20)
                for _ in range(15):
                    d.tap(BSLS, hold=20, after=20)
                    seen.append(d.fields()["effect"])
                d.release(d.fn, ms=20)
                self.assertEqual(seen, [12, 13, 15, 16, 17, 0, 1, 2, 3, 4, 5, 7, 8, 10, 11])
                self.assertNothingSent(d, n0, "Fn + \\")

    def test_colour_brightness_speed(self):
        d = self.lit()
        n0 = len(d.ep1_reports())
        d.press(d.fn, ms=20)
        cols = []
        for _ in range(9):
            d.tap(RBRC, hold=20, after=20)
            cols.append(d.cfg()[2])
        self.assertEqual(cols, [0, 1, 2, 3, 4, 5, 6, 7, 0])
        bris = []
        for k in [UP] * 3 + [DOWN] * 6:
            d.tap(k, hold=20, after=20)
            bris.append(d.cfg()[0])
        self.assertEqual(bris, [3, 4, 4, 3, 2, 1, 0, 0, 0])
        spds = []
        for k in [RIGHT] * 2 + [LEFT] * 5:
            d.tap(k, hold=20, after=20)
            spds.append(d.cfg()[1])
        self.assertEqual(spds, [4, 4, 3, 2, 1, 0, 0])
        d.release(d.fn, ms=20)
        self.assertNothingSent(d, n0, "Fn + ] / arrows")
        # effect 3 (and 15-17) have no colour choice; effect 1 no speed
        d.set_lighting(3, colour=7, bri=2, speed=3)
        d.fn_tap(RBRC)
        self.assertEqual(d.cfg(3), (2, 3, 7), "Fn + ] refused for effect 3")
        d.set_lighting(1, colour=7, bri=2, speed=3)
        d.fn_tap(RIGHT)
        self.assertEqual(d.cfg(1), (2, 3, 7), "Fn + Right refused for effect 1")
        d.fn_tap(RBRC)
        self.assertEqual(d.cfg(1), (2, 3, 0), "... but its colour moves")
        # the settings are per effect, as on the stock
        self.assertEqual(d.cfg(11), (0, 0, 0))

    def test_side_keys(self):
        d = self.lit()
        n0 = len(d.ep1_reports())
        effs = []
        d.press(d.fn, ms=20)
        for _ in range(6):
            d.tap(SLSH, hold=20, after=20)
            effs.append(d.fields()["side"][0])
        self.assertEqual(effs, [2, 3, 4, 0, 1, 2])
        d.tap(DOT, hold=20, after=20)
        self.assertEqual(d.fields()["side"][1], 0, "Fn + . refused in the rainbow effects")
        d.tap(SLSH, hold=20, after=20)                  # 3: static
        cols = []
        for _ in range(7):
            d.tap(DOT, hold=20, after=20)
            cols.append(d.fields()["side"][1])
        self.assertEqual(cols, [1, 2, 3, 4, 5, 6, 0])
        bris = []
        for _ in range(4):
            d.tap(COMM, hold=20, after=20)
            bris.append(d.fields()["side"][2])
        self.assertEqual(bris, [3, 4, 0, 1])
        d.release(d.fn, ms=20)
        self.assertNothingSent(d, n0, "Fn + / . ,")

    def test_on_off(self):
        """Fn + [: everything the lighting paints goes dark (the indicators stay);
        while off the other lighting keys do nothing; Fn + [ again: back."""
        d = self.lit()
        d.ms(100)
        self.assertGreater(len(d.backlit()), 50)
        d.fn_tap(LBRC)
        self.assertEqual(d.fields()["on"], 0)
        d.ms(30)
        self.assertEqual(d.backlit(), set(), "dark with the lighting off")
        d.fn_tap(BSLS)
        self.assertEqual(d.fields()["effect"], 11, "Fn + \\ does nothing while off")
        d.fn_tap(LBRC)
        self.assertEqual(d.fields()["on"], 1)
        d.ms(100)
        self.assertGreater(len(d.backlit()), 50, "lit again")

    def test_arrows_and_home(self):
        """Without Fn the arrows are arrows; Fn + End is still Home."""
        d = self.lit()
        for key, code in ((UP, 0x52), (DOWN, 0x51), (LEFT, 0x50), (RIGHT, 0x4F), (SLSH, 0x38), (LBRC, 0x2F)):
            n0 = len(d.ep1_reports())
            d.tap(key, hold=20, after=20)
            self.assertIn(code, [r[2] for r in d.ep1_reports()[n0:]], "key %s" % (key,))
        n0 = len(d.ep1_reports())
        d.fn_tap((15, 3))
        self.assertIn(0x4A, [r[2] for r in d.ep1_reports()[n0:]], "Fn + End = Home")


class TestSaving(LitCase):
    """A lighting change is saved once, ~3 s after the last change, and comes
    back after a power cycle; a record from before build-6 still loads."""

    def erases(self, d):
        return [a for op, a in d.flash_ops() if op == "erase"]

    def test_saved_once_after_three_seconds(self):
        d = self.lit()
        d.ms(50)
        e0 = len(self.erases(d))
        d.fn_tap(UP)                                   # brightness 2 -> 3
        d.ms(2500)
        self.assertEqual(len(self.erases(d)), e0, "nothing written within 3 s of the change")
        d.fn_tap(BSLS)                                 # effect 12, 2.6 s later: the 3 s start again
        d.ms(2700)
        self.assertEqual(len(self.erases(d)), e0, "the 3 s run from the last change")
        d.ms(600)
        self.assertEqual(self.erases(d)[e0:], ["ec00"], "one write, the settings sector")
        d.ms(4000)
        self.assertEqual(len(self.erases(d)), e0 + 1, "and only one")
        before = d.fields()
        d.reboot(SWITCH_USB)
        d.ms(50)
        after = d.fields()
        self.assertEqual(after, before)
        self.assertEqual((after["effect"], after["cfg"][11][0]), (12, 3))

    def test_everything_persists(self):
        d = self.lit()
        d.press(d.fn, ms=20)
        for k in (BSLS, BSLS, RBRC, DOWN, LEFT, SLSH, SLSH, DOT, COMM, LBRC):
            d.tap(k, hold=20, after=20)
        d.release(d.fn, ms=20)
        d.ms(3300)
        before = d.fields()
        self.assertEqual((before["on"], before["effect"], before["cfg"][13], before["side"]),
                         (0, 13, (1, 2, 0), (3, 1, 3)))
        d.reboot(SWITCH_USB)
        d.ms(50)
        self.assertEqual(d.fields(), before)
        self.assertEqual(d.backlit(), set(), "off after the power cycle too")

    def test_sector_as_sinowisp_leaves_it(self):
        """sinowisp erases everything but the bootloader and fills to 0xF000
        with zeros before writing, so after a flash the settings sector holds
        no record at all (the state every flashed board starts in): every
        setting is a default, US-JIS off and Windows included. An erased
        sector (0xFF) is treated the same."""
        d = self.lit()
        us = d._a("user_settings")
        for fill, what in ((0x00, "zero-filled (sinowisp)"), (0xFF, "erased")):
            with self.subTest(sector=what):
                for a in range(NVM_BASE, NVM_BASE + 64, 16):                   # uCsim takes a short command line
                    d.cmd("set mem rom 0x%x " % a + " ".join("0x%02x" % fill for _ in range(16)))
                d.reboot(SWITCH_USB)
                d.ms(100)
                self.assertEqual(d.get_xram(us, 11)[8:], [0x01, 0, 0], what + ": rf_link USB, US-JIS off, Windows")
                f = d.fields()
                self.assertEqual((f["magic"], f["on"], f["effect"], f["side"]), (0xB6, 1, 11, (1, 0, 2)), what)
                self.assertEqual(set(f["cfg"]), {(2, 3, 7)}, what)
                self.assertGreater(len(d.backlit()), 50, what)

    def test_record_from_before_the_lighting(self):
        """A build-5 record (no lighting fields) in front of the build-6 code:
        its settings stay, the lighting gets its defaults. This state does not
        arise from a sinowisp write (the sector is cleared first, see above);
        the loader's fallback (SETTINGS_LEGACY_LEN) is kept as a guard for an
        in-place update that leaves the sector alone, and this test keeps it
        honest."""
        d = self.lit()
        us = d._a("user_settings")
        n = d.bl() - us                                 # the old record length
        self.assertEqual(n, 11, "usjis: 9 + US-JIS + os_mac")
        payload = [0] * 8 + [0x01, 1, 1]                 # rf_link USB, US-JIS on, Mac
        rec = [0x5A, 0xA5, n] + payload + [sum(payload) & 0xFF]
        d.cmd("set mem rom 0x%x " % NVM_BASE + " ".join("0x%02x" % b for b in rec))
        d.reboot(SWITCH_USB)
        d.ms(100)
        v = d.get_xram(us, n)
        self.assertEqual(v[8:], [0x01, 1, 1], "rf_link, US-JIS and Mac from the old record")
        f = d.fields()
        self.assertEqual((f["magic"], f["on"], f["effect"], f["side"]), (0xB6, 1, 11, (1, 0, 2)))
        self.assertGreater(len(d.backlit()), 50)

    def test_settings_reset_restores_the_lighting(self):
        d = self.lit()
        d.fn_tap(LBRC)                                  # off
        d.press(d.fn, ms=20)
        d.press((13, 0), ms=40)                         # Backspace held
        d.tap((4, 3), hold=20, after=20)                # V
        d.release((13, 0), ms=20)
        d.release(d.fn, ms=20)
        f = d.fields()
        self.assertEqual((f["on"], f["effect"]), (1, 11), "reset: the lighting defaults")


class TestBatteryIdleOff(LitCase):
    """On battery: dark 30 s after the last key; the next key lights it again
    and still reaches the host; the indicators keep working."""

    def idle(self, d, ms):
        d.cmd("set mem xram 0x%x 0x%02x 0x%02x" % (d.label_bl("idle_ms"), ms & 0xFF, ms >> 8))

    def test_idle_off_and_wake(self):
        for pos, slot in ((SWITCH_BT, 1), (SWITCH_24G, 0)):
            with self.subTest(pos=pos):
                d = self.lit()
                self.into(d, pos, slot, leds=0x02)          # Caps on over the radio
                d.radio.usb_power(False)
                d.ms(200)
                caps = {CAPS_CELL[d.layout]} | set(LINK_CELL.values())   # Caps, and the link key's 3 s solid
                self.assertGreater(len(d.backlit(skip=caps)), 50)
                self.idle(d, 30000 - 300)
                d.ms(200)
                self.assertGreater(len(d.backlit(skip=caps)), 50, "still lit at 29.9 s")
                d.ms(200)
                self.assertEqual(d.backlit(skip=caps), set(), "dark after 30 s without a key")
                self.assertEqual(d.cell(*CAPS_CELL[d.layout]), "RGB", "Caps still shown")
                m = d.mark()
                d.tap(A_KEY, hold=30, after=30)
                self.assertTrue([f for f in reports(d.frames(m)) if 0x04 in f[3:9]], "the waking key reached the host")
                d.ms(50)
                self.assertGreater(len(d.backlit(skip=caps)), 50, "lit again")

    def test_not_on_usb_power(self):
        d = self.lit()
        self.into(d, SWITCH_BT, 1)
        d.radio.usb_power(True)
        d.ms(100)
        self.idle(d, 30000 - 100)
        d.ms(400)
        self.assertGreater(len(d.backlit()), 50, "no idle-off with USB power")
        d2 = self.lit()
        self.idle(d2, 30000 - 100)
        d2.ms(400)
        self.assertGreater(len(d2.backlit()), 50, "none in the wired position")

    def test_low_battery_turns_it_off(self):
        d = self.lit()
        self.into(d, SWITCH_BT, 1)
        d.radio.usb_power(False)
        d.ms(100)
        self.assertGreater(len(d.backlit()), 50)
        d.status(1, 3, raw=770)
        d.ms(1500)
        ind = {FN_CELL[d.layout]} | set(LINK_CELL.values())      # the Fn blink, the link key
        self.assertEqual(d.backlit(skip=ind), set(), "low battery: the lighting off, as the stock")


class TestInvariantsWithEffects(LitCase):
    """Every effect at full brightness with the side lights at full: the LED
    engine's limits hold (DUTY2 <= 0x0400, one column low while the PWM runs,
    the P1-P3 latches 0, the pulse and repeat limits), the table never leaves
    [phase, 0x0400], and Caps shows over the effect."""

    def test_every_effect_at_full_brightness(self):
        d = self.lit(stub_delays=False, rhythm=True)
        for eff in CYCLE:
            with self.subTest(effect=eff):
                d.set_lighting(eff, colour=7, bri=4, speed=4, side=(1 if eff % 2 else 4, 6, 4))
                d.tap((4, 2), hold=10, after=10)          # a key for the reactive effects
                d.ms(60)
                tr = d.lit_ms(25)
                check_invariants(self, tr, "effect %d" % eff)
                t = d.duty_table()
                for c in range(16):
                    for j in range(18):
                        self.assertTrue(PHASE[j] <= t[c][j] <= 0x0400, "effect %d cell %d/%d: %#x" % (eff, c, j, t[c][j]))
                        self.assertLessEqual(t[c][j], 0x03FC)
                if eff:
                    self.assertTrue(tr.lit(), "effect %d lit something" % eff)
        print("\n[invariants] %d effects at full brightness" % len(CYCLE))

    def test_every_effect_white_at_full_brightness(self):
        """Colour 6 (white: all three channels of a cell at once, the most LEDs
        lit per column) with the side lights single-colour white at full
        (gate 12b's load, for every effect)."""
        d = self.lit(stub_delays=False, rhythm=True)
        for eff in CYCLE[1:]:
            with self.subTest(effect=eff):
                d.set_lighting(eff, colour=6, bri=4, speed=4, side=(3, 6, 4))
                d.tap((4, 2), hold=10, after=10)
                d.ms(60)
                tr = d.lit_ms(25)
                check_invariants(self, tr, "effect %d white" % eff)
                t = d.duty_table()
                for c in range(16):
                    for j in range(18):
                        self.assertTrue(PHASE[j] <= t[c][j] <= 0x03FB, "effect %d white cell %d/%d: %#x" % (eff, c, j, t[c][j]))
                self.assertTrue(tr.lit(), "effect %d white lit something" % eff)
        print("\n[invariants] %d effects in white at full brightness" % (len(CYCLE) - 1))

    def test_caps_over_effects(self):
        d = self.lit(stub_delays=False, rhythm=True)
        self.into(d, SWITCH_BT, 1, leds=0x02)
        for eff in (11, 15, 4):
            d.set_lighting(eff, colour=6, bri=4, speed=4)
            d.ms(80)
            self.assertEqual(d.cell(*CAPS_CELL[d.layout]), "RGB", "effect %d: Caps white at 0x0400" % eff)


class TestOverlaysOverEffects(LitCase):
    """The indicators over a running effect (15 at full brightness, every key
    cell repainted every step): each shows as without the backlight, the
    engine's limits hold under the two together, and what the stock darkens
    (the battery display, the low-battery warning) darkens here."""

    DISPLAY = {(c, 1) for c in range(1, 11)}

    def running(self, leds=0):
        d = self.lit(stub_delays=False, rhythm=True)
        self.into(d, SWITCH_BT, 1, raw=900, leds=leds)
        d.radio.usb_power(False)
        d.set_lighting(15, colour=7, bri=4, speed=4, side=(1, 0, 4))
        d.ms(3100)                                           # past the 3 s solid after connecting
        self.assertGreater(len(d.backlit()), 50, "the effect is running")
        return d

    def test_battery_display_darkens_the_effect(self):
        d = self.running()
        d.press(d.fn, ms=20)
        d.press(ind.B, ms=30)
        tr = d.lit_ms(25)
        check_invariants(self, tr, "Fn + B over effect 15")
        self.assertEqual([d.cell(c, 1) for c in range(1, 11)], ["G"] * 8 + [""] * 2, "raw 900: 8 keys green")
        self.assertEqual(d.backlit(skip=self.DISPLAY), set(), "every other key dark while the battery shows")
        d.release(ind.B, ms=20)
        d.release(d.fn, ms=20)
        d.ms(60)
        self.assertGreater(len(d.backlit()), 50, "the effect is back")

    def test_pairing_blink_over_the_effect(self):
        d = self.running()
        d.press(d.fn, ms=20)
        d.press(d.BT1, ms=3100)                                # the 3 s hold: pairing
        d.release(d.BT1, ms=20)
        d.release(d.fn, ms=20)
        d.status(1, 1)                                         # the module: pairing on slot 1
        d.ms(20)
        rr = ind.runs(d.sample(*LINK_CELL["Q"], 400))
        self.assertEqual({c for c, n in rr}, {"", "B"}, rr)
        # the 1 ms samples fall at a varying point of the pass with the effect's
        # work in it, so a run may read 79 or 81
        self.assertTrue(all(79 <= n <= 81 for c, n in rr[1:-1]), "80 ms blink over the effect: %s" % (rr,))
        self.assertGreaterEqual(len(rr), 4, rr)
        tr = d.lit_ms(25)
        check_invariants(self, tr, "pairing blink over effect 15")
        self.assertTrue(tr.lit())

    def test_fn_held_link_over_the_effect(self):
        d = self.running(leds=0x02)
        d.press(d.fn, ms=30)
        tr = d.lit_ms(25)
        check_invariants(self, tr, "Fn held over effect 15")
        self.assertEqual({k: d.cell(*c) for k, c in LINK_CELL.items()}, {"Y": "", "Q": "B", "W": "", "E": "", "R": ""})
        self.assertEqual((d.cell(1, 3), d.cell(*CAPS_CELL[d.layout])), ("RGB", "RGB"), "A white, Caps white")
        d.release(d.fn, ms=30)

    def test_low_battery_darkens_the_effect(self):
        d = self.running()
        d.status(1, 3, raw=770)
        d.ms(320)                                              # 24 samples
        self.assertTrue(d.get_bit("f65_rf", "batt_low"))
        fn = FN_CELL[d.layout]
        rr = ind.runs(d.sample(*fn, 1000))
        self.assertEqual({c for c, n in rr}, {"", "R"}, rr)
        self.assertEqual({n for c, n in rr[1:-1]}, {250}, rr)
        self.assertEqual(d.backlit(skip={fn}), set(), "the backlight is off while the warning blinks")
        tr = d.lit_ms(25)
        check_invariants(self, tr, "low battery over effect 15")


def _lit_dev(self, position=SWITCH_USB, fw=None, stub_delays=True, rhythm=False):
    """IndCase.dev with the lighting on and a heavy effect: effect 15 at speed 4
    repaints every key cell every ms (the most work per ms), full brightness."""
    d = LitCase.lit(self, position, fw, stub_delays, rhythm)
    d.set_lighting(self.EFFECT, colour=7, bri=4, speed=4, side=(1, 0, 4))
    return d


class TestWatchdogWithEffects(ind.TestWatchdogWithLeds):
    """build-5's watchdog scenarios (USB typing with Caps toggles, the wireless
    blinks, battery display, sleep and wake, frames back to back) with an
    effect running: the longest gap between two kicks stays within 3 ms."""

    EFFECT = 15
    lit = LitCase.lit
    dev = _lit_dev


class TestReportLatency(LitCase):
    """From the scan in which a key's debounced state changes to its report
    (kb_send_report), wired, 16 presses at spread phases: the main loop's part
    of the key-to-host time (the debounce itself is the scans'). With effect
    15 (every key cell repainted every step) at full brightness against the
    lighting off in the same image (whose main loop is build-5b's plus an idle
    backlight_task). The backlight work runs in units of at most two cells and
    none while a scan waits for matrix_task; what remains is the unit in
    progress when the scan ends and the LED subframes' time with every column
    lit: the mean grows by some tens of microseconds. The longest is the same
    path in both modes, a scan that ends just before the indicator render's
    once-per-tick work (the table audit and the painting, ~200 us) - whether a
    tap hits it is the luck of its phase, so the longest is bounded on its own
    (~390 us seen) rather than against the other mode's."""

    TAPS = 32
    MAX_US = 450

    def latencies(self, d):
        import random
        rnd = random.Random(1)
        ks, se, mx = d._a("kb_send_report"), d._a("indicators_pwm_enable"), d._a("matrix")
        col, row = A_KEY
        out = []
        for _ in range(self.TAPS):
            d.ms(rnd.randint(3, 9))
            d.cmd("step %d" % rnd.randint(0, 3000))
            d.keys.press(*A_KEY)
            d.brk(se)                                    # the end of each scan (matrix_scan_full)
            while True:
                d.run()
                if d.get_xram(mx + col, 1)[0] & (1 << row):
                    break
            d.cmd("delete")
            t0 = self.clks(d)
            d.brk(ks)
            d.run()
            d.cmd("delete")
            out.append((self.clks(d) - t0) / 288.0)      # us (the simulator counts 12 clocks per cycle)
            d.ms(15)
            d.keys.release(*A_KEY)
            d.ms(15)
        return out

    @staticmethod
    def clks(d):
        return int(re.search(r"since last reset=.*?\((\d+) clks\)", d.cmd("state")).group(1))

    def test_effects_do_not_slow_the_reports(self):
        res = {}
        for mode, bri in (("off", None), ("effect 15, full brightness", 4)):
            d = self.lit(stub_delays=False, rhythm=True)
            if bri is None:
                d.fn_tap(LBRC)
            else:
                d.set_lighting(15, colour=7, bri=bri, speed=4, side=(1, 0, bri))
            d.ms(50)
            lat = sorted(self.latencies(d))
            res[mode] = (lat[-1], sum(lat) / len(lat))
            print("\n[latency] scan to report, %s: max %.0f us, mean %.0f us" % ((mode,) + res[mode]))
        off, eff = res["off"], res["effect 15, full brightness"]
        self.assertLessEqual(eff[1], off[1] + 100, "mean")
        self.assertLessEqual(eff[0], self.MAX_US, "longest")
        self.assertLessEqual(off[0], self.MAX_US, "longest, lighting off")


class TestRadioWithEffects(ind.TestLedsWithTheRadio):
    """build-5's LED-and-radio checks with an effect running: the LED limits
    under radio traffic, and two module frames 100 us apart are both framed
    wherever the gap falls (P4.7 is looked at between the effect's units)."""

    EFFECT = 15
    lit = LitCase.lit
    dev = _lit_dev

    GAPS_US = (100, 300, 500, 800)

    def test_two_frames_at_the_real_rhythm(self):
        """At the board's scan rhythm with effect 15 at full brightness: two
        module frames with P4.7 high between them for 100 .. 800 us, the gap
        placed at 40 phases of the 1 ms tick, are both parsed (the second's
        ack goes out and the first's Caps state shows). The frames are told
        apart by P4.7 alone; the longest stretch without a look at it is
        ~56 us, and a frame that ends before the main loop's next pass has
        taken the one before it (about 0.5 ms) waits in its own bank. build-6
        (two banks, one waiting frame) lost 1-2 of 40 phases at every gap."""
        from f65_radio import announce_frame, frame
        ack_f0 = frame(0xF0, length=6)
        d = self.lit(stub_delays=False, rhythm=True)
        d.set_lighting(15, colour=7, bri=4, speed=4, side=(1, 0, 4))
        self.into(d, SWITCH_24G, 0)
        d.ms(700)
        lost = {}
        for gap in self.GAPS_US:
            missed = []
            for off in range(0, 24000, 600):
                d.status(0, 3, leds=0x00)
                d.ms(5)
                m = d.mark()
                d.cmd("step %d" % off)
                d.radio.ready(False)
                d.radio.queue_rx(status_frame(0, 3, leds=0x02))
                d.ms(50, until=lambda s: d.radio.rx_pending() == 0)
                d.cmd("step 300")
                d.radio.ready(True)
                d.cmd("step %d" % (gap * 12))
                d.radio.ready(False)
                d.radio.queue_rx(announce_frame())
                d.ms(50, until=lambda s: d.radio.rx_pending() == 0)
                d.cmd("step 300")
                d.radio.ready(True)
                d.ms(10)
                if not (d.led_state() == 0x02 and ack_f0 in d.frames(m)):
                    missed.append(off)
            if missed:
                lost[gap] = missed
        self.assertEqual(lost, {}, "gap us -> phases (instructions into the tick) where a frame was lost")
        print("\n[radio] two frames %s us apart at 40 phases each: none lost" % (self.GAPS_US,))


if __name__ == "__main__":
    unittest.main()
