#!/usr/bin/env python3
"""aula-f65-v1 build-5: the LEDs together with the radio.

The link and pairing indicators, the battery display (Fn + B), the
low-battery warning, Caps Lock from the module, the LEDs across both sleeps,
the watchdog with the LED rendering running, the LED limits while EUART0 (at
interrupt priority 3, the only nested interrupt) takes bytes, where LED code
may run, and the review items queued for this build (the column-dwell
backstop, the endpoint-drain and flash-operation watchdog kicks, the bounded
PLL wait).

The board is f65_devices.OursF65 (switch, USB power, the module and keys as
contacts in the simulator) with the PWM trace of test_f65_led. Where the
stock image (SMK_F65_STOCK_IMAGE) can say how an indicator behaves, it runs
in the same simulator and board (StockF65) and its frame buffer (0x05A2,
the same cell layout as led.c's table) is the oracle: the pairing and
reconnecting blink rhythms, the 3 s solid after connecting, the low-battery
blink, and - through its painter 0x3108 run directly (test_f65_led.StockSim)
- the battery display.

    python3 -m unittest discover -s tests -p test_f65_indicators.py
"""

import json
import os
import re
import sys
import tempfile
import unittest
from pathlib import Path

from sim import REPO_ROOT, skip_or_fail
from f65_radio import status_frame, container_frame, module_send, SWITCH_USB, SWITCH_24G, SWITCH_BT
from f65_devices import OursF65, StockF65, stock_image, stock_hex, reports
from test_f65 import F65_FW, F65_ANSI_FW, _need, ISP_ENTRY
from test_f65_led import (LedSim, Trace, check_invariants, StockSim, wait_for_sentinel, COLS, STOCK_PHASE, LOADER,
                          DUTY_ON, PERIOD_CYCLES, TRACE, MARK, T2_REAL, P1, P2, P3, P4, P6, IEN1, EPWM0, expect_cell,
                          LED_MAP)

sys.path.insert(0, str(REPO_ROOT / "utils"))
import check_interrupts  # noqa: E402

CYCLES_PER_MS = 24000
B = (5, 3)                              # the B key (col, row)
FN_CELL = {"ansi": (9, 5), "usjis": (10, 5)}
CAPS_CELL = {"ansi": (0, 3), "usjis": (0, 5)}
LINK_CELL = {"Y": (6, 2), "Q": (1, 2), "W": (2, 2), "E": (3, 2), "R": (4, 2)}
PHASE = [STOCK_PHASE[n] for n in LOADER]        # slot channel j = row * 3 + c -> phase
BATT_STEPS = [737, 758, 781, 802, 824, 846, 868, 890, 912, 934]
STOCK_FB16 = 0x05A2


def colour(vals, row, strict=True):
    """A cell's three channels as a colour string ('' = off, 'RGB' = white):
    the channels at 0x0400. Strict (ours): every other channel is its phase.
    The stock's effect engine paints the cells no indicator holds, so there
    only 0x0400 counts."""
    out = ""
    for c, v in enumerate(vals):
        ph = PHASE[row * 3 + c]
        if v == DUTY_ON:
            out += "RGB"[c]
        elif strict and v != ph:
            raise AssertionError("cell channel %d = %#x, neither the phase %#x nor 0x0400" % (c, v, ph))
    return out


class IndRig(OursF65):
    """OursF65 plus the PWM trace and the LED table (from test_f65_led.LedSim)."""

    LIGHTING = False                    # the indicators alone (build-6: backlight off)

    trace_on = LedSim.trace_on
    trace_off = LedSim.trace_off
    mark_trace = LedSim.mark
    table = LedSim.table
    pwm_off_state = LedSim.pwm_off_state
    assertDark = LedSim.assertDark
    as_after_a_lit_subframe = LedSim.as_after_a_lit_subframe

    def __init__(self, firmware=None):
        super().__init__(firmware)
        self._mark = 0
        self.layout = "ansi" if "ansi" in Path(self.firmware).name else "usjis"

    def rhythm(self):
        """Timer2 at its real rate (the board's scan rhythm)."""
        self.cmd("set mem xram 0x%x 0x01" % T2_REAL)

    def cell(self, col, row):
        base = self._xdata_static("led", "led_fb")
        v = self.get_xram(base + col * 36 + row * 6, 6)
        return colour([v[0] << 8 | v[1], v[2] << 8 | v[3], v[4] << 8 | v[5]], row)

    def lit_ms(self, n):
        self.trace_on()
        self.ms(n)
        return self.trace_off()

    def label(self, module, name):
        """Address of a module's variable from its relocated listing (a release
        map has no module statics)."""
        rst = Path(self.firmware).with_suffix(".ihx.p") / (module + ".rst")
        for line in rst.read_text().splitlines():
            m = re.match(r"^\s+([0-9A-F]{6})\s+\d+ _%s::?$" % re.escape(name), line)
            if m:
                return int(m.group(1), 16)
        raise KeyError(name)

    def holds(self):
        return self.get_iram(self.label("led", "led_holds"), 1)[0]

    def sample(self, col, row, n):
        """The cell's colour at every 1 ms tick for n ms."""
        out = []

        def take(s):
            out.append(self.cell(col, row))
            return False
        self.ms(n, until=take)
        return out


class StockRig(StockF65):
    def cell(self, col, row):
        v = self.get_xram(STOCK_FB16 + col * 36 + row * 6, 6)
        return colour([v[0] << 8 | v[1], v[2] << 8 | v[3], v[4] << 8 | v[5]], row, strict=False)

    def sample(self, col, row, n):
        out = []

        def take(s):
            out.append(self.cell(col, row))
            return False
        self.ms(n, until=take)
        return out


def runs(samples):
    """[(colour, length in ms)] of a sampled cell."""
    out = []
    for s in samples:
        if out and out[-1][0] == s:
            out[-1][1] += 1
        else:
            out.append([s, 1])
    return [tuple(r) for r in out]


class IndCase(unittest.TestCase):
    FW = F65_ANSI_FW

    def setUp(self):
        import f65_radio
        f65_radio._edge[0] = 0            # the module's P4.7 edge phase: the same in every test

    def dev(self, position=SWITCH_USB, fw=None, stub_delays=True, rhythm=False):
        fw = fw or self.FW
        _need(fw)
        d = IndRig(fw)
        self.addCleanup(self._close, d)
        d.start(position, stub_delays=stub_delays)
        if rhythm:
            d.rhythm()
        return d

    def stock(self, position=SWITCH_USB):
        d = StockRig(self._stock_hex())
        self.addCleanup(self._close, d)
        d.start(position)
        return d

    _hex = None

    @classmethod
    def _stock_hex(cls):
        if IndCase._hex is None:
            IndCase._hex = stock_hex(stock_image())
        return IndCase._hex

    def _close(self, d):
        try:
            self.assertNotIn("WATCHDOG", d.stderr_text())
        finally:
            d.close()

    def into(self, d, pos, slot=None, state=3, raw=0x03DD, leds=0):
        d.switch(pos, 200)
        if slot is not None:
            d.status(slot, state, raw=raw, leds=leds)
            d.ms(20)

    def poke_idle(self, d, seconds):
        a = d._xdata_static("f65_rf", "idle_s")
        d.cmd("set mem xram 0x%x 0x%02x 0x%02x" % (a, seconds & 0xFF, seconds >> 8))
        d.cmd("set mem xram 0x%x 0x00 0x00" % d._xdata_static("f65_rf", "idle_ms"))

    def poke_quiet(self, d, ms):
        a = d._xdata_static("f65_rf", "usb_quiet_ms")
        d.cmd("set mem xram 0x%x 0x%02x 0x%02x" % (a, ms & 0xFF, ms >> 8))


# ------------------------------------------------------------ link indicators
class TestLinkKeys(IndCase):
    """Mirror (the user's key mapping: Y = USB, Q / W / E = Bluetooth 1-3,
    R = 2.4 GHz) with the stock painter's rules (0x36D5): while Fn is held only
    the connected link's key lights - Y white, R green, the active Bluetooth
    slot's key blue (build-5b; the stock lights them white)."""

    def fn_cells(self, d):
        return {k: d.cell(*c) for k, c in LINK_CELL.items()}

    def held(self, d):
        d.press(d.fn, ms=30)
        got = self.fn_cells(d)
        d.release(d.fn, ms=30)
        return got

    def test_usb_y(self):
        d = self.dev()
        self.assertEqual(self.fn_cells(d), dict.fromkeys(LINK_CELL, ""), "dark without Fn")
        self.assertEqual(self.held(d), {"Y": "RGB", "Q": "", "W": "", "E": "", "R": ""})
        self.assertEqual(self.fn_cells(d), dict.fromkeys(LINK_CELL, ""), "dark after Fn")

    def test_24g_r_and_bt_slots(self):
        d = self.dev()
        self.into(d, SWITCH_24G, 0)
        d.ms(3100)                                          # past the 3 s solid after connecting
        self.assertEqual(self.held(d), {"Y": "", "Q": "", "W": "", "E": "", "R": "G"}, "2.4 GHz: R green")
        d.switch(SWITCH_BT, 200)
        for slot, key in ((1, "Q"), (2, "W"), (3, "E")):
            with self.subTest(slot=slot):
                if slot != 1:
                    d.press(d.fn, ms=20)
                    d.tap([d.BT1, d.BT2, d.BT3][slot - 1], hold=40, after=40)   # select on release
                    d.release(d.fn, ms=20)
                d.status(slot, 3)
                d.ms(3100)
                want = dict.fromkeys(LINK_CELL, "")
                want[key] = "B"                             # the active slot blue, the others dark
                self.assertEqual(self.held(d), want)

    def test_nothing_while_not_linked(self):
        d = self.dev()
        self.into(d, SWITCH_BT, 1, state=0)                 # the module reports no link
        self.assertEqual(self.held(d), dict.fromkeys(LINK_CELL, ""))

    def test_fn_held_link_through_the_pwm(self):
        """What the PWM trace lights while Fn is held in Bluetooth slot 2:
        W blue (and A white, Win) - the table, the limits and the pins agree."""
        d = self.dev(rhythm=True)
        self.into(d, SWITCH_BT)
        d.press(d.fn, ms=20)
        d.tap(d.BT2, hold=40, after=40)
        d.status(2, 3)
        d.ms(3100)
        tr = d.lit_ms(40)
        check_invariants(self, tr, "Fn held, BT 2")
        self.assertEqual(tr.lit_set(), expect_cell("A", "RGB") | {(2, "PWM12", DUTY_ON)})
        d.release(d.fn, ms=30)


class TestPairingIndicators(IndCase):
    """Oracle: the stock image. Pairing (state 1) blinks the slot's key at an
    80 ms toggle, reconnecting (state 2) at 380 ms, and the key is solid for
    3 s once connected; blue for Bluetooth, cyan for 2.4 GHz; without Fn."""

    def blink(self, rig, key_col, pos, slot, state, n=1300):
        self.into(rig, pos, slot, state=state)
        if isinstance(rig, StockRig):
            for _ in range(20):
                if rig.boot_show_done():
                    break
                rig.ms(100)
        rig.ms(200)
        return runs(rig.sample(key_col, 2, n))

    @staticmethod
    def periods(rr):
        return [n for c, n in rr[1:-1]]              # whole runs only

    def test_bt_pairing_80ms(self):
        o = self.blink(self.dev(), 1, SWITCH_BT, 1, 1)
        self.assertEqual({c for c, n in o}, {"", "B"}, o)
        self.assertEqual(set(self.periods(o)), {80}, o)
        s = self.blink(self.stock(), 3, SWITCH_BT, 1, 1)      # the stock's slot 1 key: E
        self.assertEqual({c for c, n in s}, {"", "B"}, s)
        self.assertEqual(set(self.periods(s)), set(self.periods(o)), "the stock's rhythm")

    def test_bt_reconnecting_380ms(self):
        d = self.dev()
        self.into(d, SWITCH_BT)
        d.press(d.fn, ms=20)
        d.tap(d.BT2, hold=40, after=40)
        d.release(d.fn, ms=20)
        o = self.blink(d, 2, SWITCH_BT, 2, 2, n=2400)
        self.assertEqual({c for c, n in o}, {"", "B"}, o)
        self.assertEqual(set(self.periods(o)), {380}, o)
        s = self.blink(self.stock(), 3, SWITCH_BT, 1, 2, n=2400)
        self.assertEqual(set(self.periods(s)), {380}, s)

    def test_24g_pairing_cyan_on_r(self):
        o = self.blink(self.dev(), 4, SWITCH_24G, 0, 1)
        self.assertEqual({c for c, n in o}, {"", "GB"}, o)
        self.assertEqual(set(self.periods(o)), {80}, o)
        s = self.blink(self.stock(), 1, SWITCH_24G, 0, 1)     # the stock's 2.4 GHz key: Q
        self.assertEqual({c for c, n in s}, {"", "GB"}, s)
        self.assertEqual(set(self.periods(s)), {80}, s)

    def solid(self, rig, key_col):
        self.into(rig, SWITCH_BT, 1, state=2)
        if isinstance(rig, StockRig):
            for _ in range(20):
                if rig.boot_show_done():
                    break
                rig.ms(100)
        rig.ms(100)
        rig.status(1, 3)
        return runs(rig.sample(key_col, 2, 3300))

    def test_connected_solid_3s(self):
        o = self.solid(self.dev(), 1)
        s = self.solid(self.stock(), 3)
        on_o = [n for c, n in o if c == "B"]
        on_s = [n for c, n in s if c == "B"]
        self.assertEqual(o[-1][0], "", o)
        self.assertEqual(len(on_o), 1, o)
        self.assertTrue(2990 <= on_o[0] <= 3030, o)
        self.assertEqual(s[-1][0], "", s)
        self.assertLessEqual(abs(on_s[-1] - on_o[0]), 12, (o, s))

    def test_blink_shows_with_fn_held_too(self):
        d = self.dev()
        self.into(d, SWITCH_BT, 1, state=1)
        d.press(d.fn, ms=20)
        rr = runs(d.sample(1, 2, 400))
        self.assertIn(("B", 80), rr, rr)
        d.release(d.fn, ms=20)

    def test_blink_only_while_synced(self):
        """The module reporting another slot is not our link: no blink there
        (the stock needs 0x2d.1 as well)."""
        d = self.dev()
        self.into(d, SWITCH_BT, 3, state=1)                   # we selected slot 1
        self.assertEqual({c for c, n in runs(d.sample(3, 2, 300))}, {""})
        self.assertEqual({c for c, n in runs(d.sample(1, 2, 50))}, {""})


# ------------------------------------------------------------- battery display
class TestBatteryDisplay(IndCase):
    """Oracle: the stock painter 0x3108 (run in the simulator by
    test_f65_led.StockSim, and painter_oracle.json of the analysis). Fn + B
    held in a wireless position on battery lights 1 .. 0 by the raw value."""

    RAWS = (700, 736, 737, 780, 781, 801, 802, 850, 933, 934, 1000)

    @staticmethod
    def expected(raw):
        n = sum(1 for t in BATT_STEPS if raw >= t)
        col = "G" if raw >= 802 else "R"
        return [col if i < n else "" for i in range(10)]

    def ours(self, raws):
        d = self.dev()
        self.into(d, SWITCH_BT, 1)
        d.radio.usb_power(False)
        d.ms(60)
        got = {}
        for raw in raws:
            d.status(1, 3, raw=raw)
            m = d.mark()
            usb = len(d.ep1_reports())
            d.press(d.fn, ms=20)
            d.press(B, ms=30)
            got[raw] = [d.cell(c, 1) for c in range(1, 11)]
            d.release(B, ms=20)
            d.release(d.fn, ms=30)
            self.assertEqual([c for c in range(1, 11) if d.cell(c, 1)], [], "dark once released")
            self.assertEqual([f for f in reports(d.frames(m)) if 0x05 in f[3:9]], [], "B never reaches the host")
            self.assertEqual(len(d.ep1_reports()), usb)
        return got

    def test_levels_against_the_stock_painter(self):
        path = os.environ.get("SMK_F65_PAINTER_ORACLE")
        if path and Path(path).exists():
            orc = json.loads(Path(path).read_text())
            for name, raw in (("battery display 0x0340", 0x340), ("battery display 0x02f0", 0x2F0)):
                cells = [orc[name]["(%d, 1)" % c] for c in range(1, 11)]
                self.assertEqual([colour(v, 1) for v in cells], self.expected(raw), name)
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        from test_f65_led import stock_hex as led_stock_hex, stock_image as led_stock_image
        st = StockSim(led_stock_hex(led_stock_image(), tmp.name))
        self.addCleanup(st.close)
        st.boot()
        stock = {}
        for raw in self.RAWS:
            # the painter's output, the frame buffer (fb16 0x05A2, the same
            # cells as led.c's table): big-endian raw at 0x02E9
            st.paint_and_run(bits=[((0x23, 5), 1)], xram=[(0x02E9, [raw >> 8, raw & 0xFF])], periods=2)
            lit = []
            for c in range(1, 11):
                v = st.get_xram(STOCK_FB16 + c * 36 + 6, 6)
                lit.append(colour([v[0] << 8 | v[1], v[2] << 8 | v[3], v[4] << 8 | v[5]], 1))
            stock[raw] = lit
        for raw in self.RAWS:
            self.assertEqual(stock[raw], self.expected(raw), "stock painter, raw %d" % raw)
        got = self.ours(self.RAWS)
        for raw in self.RAWS:
            self.assertEqual(got[raw], stock[raw], "raw %d" % raw)

    def test_not_on_usb_power_nor_in_the_middle(self):
        d = self.dev()
        for where in ("middle", "bt on USB power"):
            if where != "middle":
                self.into(d, SWITCH_BT, 1, raw=900)
            usb = len(d.ep1_reports())
            d.press(d.fn, ms=20)
            d.press(B, ms=30)
            self.assertEqual([d.cell(c, 1) for c in range(1, 11)], [""] * 10, where)
            d.release(B, ms=20)
            d.release(d.fn, ms=30)
            self.assertEqual(len(d.ep1_reports()), usb, where + ": Fn + B sends nothing")

    def test_everything_else_gives_way(self):
        """While the battery shows, every other indicator is dark, as on the
        stock (it clears every cell at the press, 0x41B4, and paints nothing
        else, 0x34D6): the Fn-held keys (link, A / S) and Caps Lock."""
        d = self.dev()
        caps = CAPS_CELL[d.layout]
        self.into(d, SWITCH_BT, 1, raw=900, leds=0x02)
        d.radio.usb_power(False)
        d.ms(3100)
        d.press(d.fn, ms=20)
        self.assertEqual((d.cell(*LINK_CELL["Q"]), d.cell(1, 3), d.cell(*caps)), ("B", "RGB", "RGB"),
                         "Fn: Q blue, A white, Caps")
        d.press(B, ms=30)
        self.assertEqual((d.cell(*LINK_CELL["Q"]), d.cell(1, 3), d.cell(*caps)), ("", "", ""), "Fn + B: they give way")
        self.assertEqual([d.cell(c, 1) for c in range(1, 11)], ["G"] * 8 + [""] * 2, "raw 900: 8 keys green")
        d.release(B, ms=20)
        self.assertEqual((d.cell(*LINK_CELL["Q"]), d.cell(1, 3), d.cell(*caps)), ("B", "RGB", "RGB"))
        d.release(d.fn, ms=20)


# ----------------------------------------------------------------- low battery
class TestLowBattery(IndCase):
    """Oracle: the stock image for the blink (raw < 781 for 24 samples, the
    Fn key red, toggled every 250 ms). Sticky until raw > 912 for 1 s or USB
    power; charging stays solid red."""

    def fn_runs(self, rig, cell, n):
        return runs(rig.sample(*cell, n))

    def test_blink_against_the_stock(self):
        d = self.dev()
        self.into(d, SWITCH_BT, 1)
        d.radio.usb_power(False)
        d.ms(60)
        d.status(1, 3, raw=770)
        o = self.fn_runs(d, FN_CELL[d.layout], 1400)
        self.assertEqual({c for c, n in o}, {"", "R"}, o)
        self.assertEqual({n for c, n in o[1:-1]}, {250}, o)
        s = self.stock()
        self.into(s, SWITCH_BT, 1)
        for _ in range(20):
            if s.boot_show_done():
                break
            s.ms(100)
        s.radio.usb_power(False)
        s.ms(60)
        s.status(1, 3, raw=770)
        sr = self.fn_runs(s, (9, 5), 1400)
        self.assertEqual({n for c, n in sr[1:-1]}, {250}, sr)

    def test_sticky_then_cleared(self):
        d = self.dev()
        self.into(d, SWITCH_BT, 1)
        d.radio.usb_power(False)
        d.ms(60)
        d.status(1, 3, raw=780)
        d.ms(200)
        self.assertFalse(d.get_bit("f65_rf", "batt_low"), "not before 24 samples")
        d.ms(100)
        self.assertTrue(d.get_bit("f65_rf", "batt_low"))
        d.status(1, 3, raw=900)                               # up again, but not above 912: stays
        d.ms(1200)
        self.assertIn("R", {c for c, n in self.fn_runs(d, FN_CELL[d.layout], 600)})
        d.status(1, 3, raw=920)
        d.ms(1100)
        self.assertFalse(d.get_bit("f65_rf", "batt_low"), "raw > 912 for 1 s clears it")
        self.assertEqual({c for c, n in self.fn_runs(d, FN_CELL[d.layout], 300)}, {""})
        d.status(1, 3, raw=770)
        d.ms(600)
        self.assertTrue(d.get_bit("f65_rf", "batt_low"))
        d.radio.usb_power(True)
        d.ms(60)
        self.assertFalse(d.get_bit("f65_rf", "batt_low"), "USB power clears it")
        self.assertEqual({c for c, n in self.fn_runs(d, FN_CELL[d.layout], 300)}, {""})

    def test_no_warning_in_the_middle_position(self):
        d = self.dev()
        d.radio.usb_power(False)                             # (the board would have no power)
        d.ms(600)
        self.assertEqual({c for c, n in self.fn_runs(d, FN_CELL[d.layout], 300)}, {""})

    def test_charging_stays_solid_red(self):
        d = self.dev()
        self.into(d, SWITCH_BT, 1, raw=770)
        d.radio.charging(True)
        d.ms(300)
        self.assertEqual(runs(d.sample(*FN_CELL[d.layout], 300)), [("R", 300)])

    def test_cutoff_is_dark(self):
        d = self.dev(rhythm=True)
        self.into(d, SWITCH_BT, 1, leds=0x02)
        d.radio.usb_power(False)
        d.ms(60)
        self.assertEqual(d.cell(*CAPS_CELL[d.layout]), "RGB")
        d.set_bit("f65_rf", "batt_cut", 1)
        d.ms(5)
        self.assertTrue(d.holds() & 0x80, "LED_HOLD_CUTOFF")
        tr = d.lit_ms(30)
        self.assertEqual(tr.lit(), [], "nothing lit in the cutoff")
        d.set_bit("f65_rf", "batt_cut", 0)
        d.ms(5)
        self.assertFalse(d.holds() & 0x80)
        self.assertTrue(d.lit_ms(30).lit(), "back after the cutoff")


# --------------------------------------------------------- Caps over the radio
class TestCapsOverTheRadio(IndCase):
    """The host's Caps Lock from the status frame (byte 3), shown as over
    USB while the link is connected (stock 0x34EA)."""

    def test_caps(self):
        for fw in (F65_ANSI_FW, F65_FW):
            with self.subTest(fw=Path(fw).name):
                d = self.dev(fw=fw)
                caps = CAPS_CELL[d.layout]
                self.into(d, SWITCH_24G, 0, leds=0x02)
                self.assertEqual(d.cell(*caps), "RGB", "2.4 GHz, connected")
                d.status(0, 3, leds=0x00)
                d.ms(20)
                self.assertEqual(d.cell(*caps), "")
                d.switch(SWITCH_BT, 200)
                d.status(1, 3, leds=0x02)
                d.ms(20)
                self.assertEqual(d.cell(*caps), "RGB", "Bluetooth, connected")
                d.status(1, 2, leds=0x02)
                d.ms(20)
                self.assertEqual(d.cell(*caps), "", "not while reconnecting")


    def test_a_usb_suspend_left_behind_does_not_darken_the_radio(self):
        """The USB-suspend hold is for the wired position: with the bus
        suspended when the switch goes to Bluetooth, the LEDs still show."""
        d = self.dev(rhythm=True)
        d.set_bit("usb", "usb_suspended", 1)
        d.ms(10)
        self.assertTrue(d.holds() & 0x08, "dark while the bus is suspended (wired)")
        self.into(d, SWITCH_BT, 1, leds=0x02)
        self.assertEqual(d.get_bit("usb", "usb_suspended"), 1, "the flag is still set")
        self.assertEqual(d.holds() & 0x08, 0)
        self.assertTrue(d.lit_ms(30).lit(), "lit in Bluetooth")


# -------------------------------------------------------------- LEDs and sleep
class TestSleepDark(IndCase):
    """Every LED off (PWM stopped, anode latches 0, columns released) before
    either sleep's parking and power-down; back after the wake. The hardware
    gate: in Bluetooth with the host's Caps Lock on, the Caps LED goes dark at
    the idle timeout and a key brings it back with the link."""

    def check_parking_dark(self, d, what, entry, col):
        """Run into the sleep; at its entry put the board in the state a lit
        subframe leaves (the main loop can be there in that state), then:
        nothing lit from the entry on, dark (columns released) at the parking,
        the banks stopped and the anodes low at the power-down."""
        park, pd, ent = d.code("f65_power", "park_pins"), d.code("f65_power", "power_down"), d._a(entry)
        d.brk(ent)
        d.brk(d._a("pwm4_ms_tick_interrupt_handler"))
        for _ in range(8000):
            if d.stopped_at(d.run()) == ent:
                break
        else:
            self.fail(what + ": no sleep")
        d.cmd("delete")
        st = d.as_after_a_lit_subframe(col)
        self.assertEqual((st["run"], st["cols"]), ([True] * 3, {col}))
        d.mark_trace(3)
        self.assertEqual(d.run_until(park), park)
        d.assertDark(self, what + ": at the parking")
        d.mark_trace(2)
        self.assertEqual(d.run_until(pd), pd)
        st = d.pwm_off_state()
        self.assertEqual((st["run"], st["ss"], st["p123"], st["epwm0"]), ([False] * 3, [0] * 18, [0, 0, 0], False),
                         what + ": at the power-down (columns parked low, the anodes dark)")
        self.assertEqual([d.get_sfr(cr) & 0x3F for cr in (0xE2, 0xE3, 0xE4)], [0x3F] * 3,
                         what + ": P1-P3 outputs driven low (the anodes)")

    def test_bluetooth_gate(self):
        d = self.dev(rhythm=True)
        caps = CAPS_CELL[d.layout]
        self.into(d, SWITCH_BT, 1, leds=0x02)
        d.ms(3100)                                            # past the link key's 3 s solid
        tr = d.lit_ms(30)
        self.assertEqual({s[0] for s in tr.lit()}, {caps[0]}, "Caps lit before")
        d.trace_on()
        self.poke_idle(d, 60)
        self.check_parking_dark(d, "Bluetooth idle sleep", "f65_sleep_wireless", caps[0])
        # as if the power-down lost the PWM set-up: the period and a phase
        # (the stock runs its set-up 0x6313 again after a wake; led_wake too)
        d.cmd("set mem xram 0xff98 0x00")
        d.cmd("set mem xram 0xff9c 0x00")
        d.cmd("set mem xram 0xffa0 0x00")
        tr = d.trace_off()
        t_entry = [t for t, v in tr.marks if v == 3][0]
        self.assertEqual([s for s in tr.lit() if s[3] > t_entry + 600], [],
                         "nothing lit from the sleep's first instructions on")
        d.radio.wake_key(True)
        d.ms(40)
        d.radio.wake_key(False)
        self.assertIn("[SIE] wake from power-down", d.stderr_text())
        d.status(1, 3, leds=0x02)                             # the module reports the link
        d.ms(20)
        self.assertEqual(d.holds() & 0x10, 0, "LED_HOLD_SLEEP released")
        self.assertEqual(d.get_xram(0xFF98, 1) + d.get_xram(0xFF9C, 1) + d.get_xram(0xFFA0, 1), [0xB0, 0x04, 0xC3],
                         "the PWM set up again after the wake (period 0x04B0, PWM00 phase)")
        tr = d.lit_ms(30)
        check_invariants(self, tr, "after the wake")
        self.assertEqual({s[0] for s in tr.lit()}, {caps[0]}, "Caps back after the wake")

    def test_24g_sleep(self):
        d = self.dev(rhythm=True)
        self.into(d, SWITCH_24G, 0, leds=0x02)
        self.poke_idle(d, 60)
        d.trace_on()
        self.check_parking_dark(d, "2.4 GHz idle sleep", "f65_sleep_wireless", CAPS_CELL[d.layout][0])
        tr = d.trace_off()
        t_entry = [t for t, v in tr.marks if v == 3][0]
        self.assertEqual([s for s in tr.lit() if s[3] > t_entry + 600], [], "nothing lit from the sleep's start on")
        d.radio.wake_key(True)
        d.ms(40)
        d.radio.wake_key(False)
        d.status(0, 3, leds=0x02)
        d.ms(20)
        self.assertTrue(d.lit_ms(30).lit(), "back after the wake")

    def test_wired_sleep_on_a_charger(self):
        d = self.dev(rhythm=True)
        d.radio.charging(True)
        d.ms(300)
        self.assertEqual(d.cell(*FN_CELL[d.layout]), "R", "charging")
        d.cmd("set mem xram 0x%x 0x00" % d._a("usb_device_state"))     # no host configured it
        self.poke_quiet(d, 9990)
        d.trace_on()
        self.check_parking_dark(d, "wired sleep", "f65_sleep_wired", FN_CELL[d.layout][0])
        tr = d.trace_off()
        t_entry = [t for t, v in tr.marks if v == 3][0]
        self.assertEqual([s for s in tr.lit() if s[3] > t_entry + 600], [], "nothing lit from the sleep's start on")
        d.radio.wake_key(True)
        d.ms(40)
        d.radio.wake_key(False)
        d.ms(50)
        self.assertEqual(d.holds() & 0x10, 0)
        tr = d.lit_ms(30)
        check_invariants(self, tr, "after the wired wake")
        self.assertEqual(tr.lit_set(), expect_cell("Fn", "R", where=FN_CELL[d.layout][0]), "the Fn key red again")


# ------------------------------------------------- the watchdog with the LEDs
class TestWatchdogWithLeds(IndCase):
    """build-3d's rule on the merged image: no ISR kicks, the bounded main-loop
    waits do; with the LED subframes, the rendering, the board's scan rhythm
    and the real delays, the longest gap between two kicks stays within 3 ms in
    every position."""

    LIMIT_MS = 3.0

    def caps_report(self, d, value):
        d.cmd("set mem xram 0x1100 0x21 0x09 0x00 0x02 0x00 0x00 0x01 0x00")
        d.set_sfr(0x92, 0x10)
        d.ms(2)
        d.cmd("set mem xram 0x1100 0x%02x 0 0 0 0 0 0 0" % value)
        d.set_sfr(0x93, 0x10)
        d.ms(2)

    def gap(self, d):
        """The longest gap (ms); the simulator logs where each new longest
        one of 1.5 ms or more ended and began."""
        d.radio.log()                                         # (lets the stderr reader catch up)
        for line in re.findall(r"\[SIE\] KICKGAP .*", d.stderr_text()):
            print("\n" + line + " " + self.where(d, line))
        return d.radio.kick_gap_ms()

    @staticmethod
    def where(d, line):
        """The functions holding the two kick addresses (from the map)."""
        m = re.search(r"kick at ([0-9a-f]{4}) after the kick at ([0-9a-f]{4})", line)
        if not m:
            return ""
        syms = sorted((a, n) for n, a in d.sym.items() if isinstance(a, int) and a < 0x10000)
        out = []
        for h in m.groups():
            a = int(h, 16)
            name = max((x for x in syms if x[0] <= a), default=(0, "?"))[1]
            out.append("%s=%s" % (h, name))
        return "(" + ", ".join(out) + ")"

    def test_usb(self):
        d = self.dev(stub_delays=False, rhythm=True)
        d.radio.charging(True)
        d.radio.clear_kick_gap()
        for i in range(30):                                   # 30 Caps Lock toggles while typing
            d.press((1, 2), ms=8)
            self.caps_report(d, 0x02 if i % 2 == 0 else 0x00)
            d.release((1, 2), ms=8)
        d.press(d.fn, ms=40)                                  # Y, A, Fn-held keys
        d.release(d.fn, ms=40)
        g = self.gap(d)
        print("\n[kick gap] USB with LEDs: %.2f ms" % g)
        self.assertLessEqual(g, self.LIMIT_MS)

    def wireless(self, pos, slot):
        d = self.dev(stub_delays=False, rhythm=True)
        self.into(d, pos, slot, leds=0x02)
        d.radio.usb_power(False)
        d.radio.clear_kick_gap()
        d.status(slot, 1)                                     # pairing blink
        d.ms(300)
        d.status(slot, 3, leds=0x02)                          # the 3 s solid, Caps
        d.ms(100)
        d.press(d.fn, ms=40)
        d.press(B, ms=60)                                     # battery display: 10 cells at once
        d.release(B, ms=40)
        d.release(d.fn, ms=40)
        for _ in range(6):
            d.tap((1, 2), hold=20, after=20)
        d.status(slot, 3, raw=770, leds=0x02)                 # low-battery blink
        d.ms(600)
        for _ in range(10):                                   # frames back to back from the module
            d.module(container_frame(0x01), gap_ms=1)
        d.ms(10)                                              # parsed (a container clears the idle time) before
        self.poke_idle(d, 60)                                 # sleep with the LEDs lit, and the wake
        d.radio.wake_key(True)
        d.ms(1500)
        d.radio.wake_key(False)
        g = self.gap(d)
        d.radio.log()                                         # (lets the stderr reader catch up)
        self.assertIn("wake from power-down", d.stderr_text())
        return g

    def test_24g(self):
        g = self.wireless(SWITCH_24G, 0)
        print("\n[kick gap] 2.4 GHz with LEDs: %.2f ms" % g)
        self.assertLessEqual(g, self.LIMIT_MS)

    def test_bluetooth(self):
        g = self.wireless(SWITCH_BT, 1)
        print("\n[kick gap] Bluetooth with LEDs: %.2f ms" % g)
        self.assertLessEqual(g, self.LIMIT_MS)

    def test_boot_and_a_long_catch_up(self):
        """Review B5R-1: from reset through the first renders the kick gap stays
        within 3 ms; and a render that finds its 10 ms clock 4 s behind (the
        first render after a 4 s USB enumeration wait) catches up at most 10
        ticks, so it does not make a long stretch without a kick either."""
        _need(self.FW)
        d = IndRig(self.FW)
        self.addCleanup(self._close, d)
        d.rhythm()                                            # Timer2 at its real rate from the reset on
        d.start(SWITCH_USB, stub_delays=False)
        d.radio.charging(True)
        d.ms(50)
        g = self.gap(d)
        print("\n[kick gap] boot to the first renders: %.2f ms" % g)
        self.assertLessEqual(g, self.LIMIT_MS)
        a = d.label("indicators", "ind_last_ms")
        lo, hi = d.get_xram(a, 2)
        v = ((hi << 8 | lo) - 4000) & 0xFFFF
        d.cmd("set mem xram 0x%x 0x%02x 0x%02x" % (a, v & 0xFF, v >> 8))
        d.radio.clear_kick_gap()
        d.ms(20)
        g = self.gap(d)
        print("\n[kick gap] render 4 s behind: %.2f ms" % g)
        self.assertLessEqual(g, self.LIMIT_MS)
        d.ms(300)
        self.assertTrue(d.lit_ms(30).lit(), "still rendering (charging: Fn red)")

    def test_endpoint_drain_kicks(self):
        """Review 3c F5: the host takes no EP1 report for a while (the model
        holds IEP1RDY), so each report waits in ep1_in_drain (up to ~10 ms):
        that wait kicks as it goes."""
        d = self.dev(stub_delays=False, rhythm=True)
        d.radio.clear_kick_gap()
        d.cmd("set mem xram 0x1f53 0x01")
        t0 = d.stderr_text().count("[SIE] EP1 IN held")
        for _ in range(3):
            d.tap((1, 2), hold=30, after=30)
        held = d.stderr_text().count("[SIE] EP1 IN held") - t0
        d.cmd("set mem xram 0x1f53 0x00")
        d.ms(20)
        self.assertGreaterEqual(held, 2, "reports waited in the drain")
        g = self.gap(d)
        print("\n[kick gap] EP1 drain held: %.2f ms" % g)
        self.assertLessEqual(g, self.LIMIT_MS)

    def test_flash_operations_follow_a_kick(self):
        """Review 3c F7: every flash erase and program comes right after a
        watchdog kick (the stock: 0x94A9), whatever came before it."""
        d = self.dev(stub_delays=False, rhythm=True)
        n0 = len(re.findall(r"\[SIE\] FLASH", d.stderr_text()))
        d.press(d.fn, ms=20)
        d.tap((2, 2), hold=40, after=200)                      # Fn + S: Mac, saved
        d.release(d.fn, ms=100)
        ops = re.findall(r"\[SIE\] FLASH (\w+) ([0-9a-f]{4}) kick (\d+)", d.stderr_text())[n0:]
        self.assertTrue(any(o == "erase" for o, a, k in ops) and any(o == "program" for o, a, k in ops), ops)
        worst = max(int(k) for o, a, k in ops)
        print("\n[flash] %d operations, at most %d cycles after a kick" % (len(ops), worst))
        self.assertLess(worst, 200, ops)


# ------------------------------------------------- the LEDs and the radio
class TestLedsWithTheRadio(IndCase):
    """EUART0 at priority 3 is the only interrupt that nests (over the Timer2
    scan and subframe). With the module sending back to back, the LED limits
    hold and a column is never selected with the PWM running for more than
    one subframe."""

    DWELL_MAX = 5 * PERIOD_CYCLES                  # 500 us: one subframe (~400 us) and the scan's entry

    def dwell(self, tr):
        """The longest stretch with the LED banks running and a column low."""
        worst, start = 0, None
        for s in tr.states:
            on = bool(s["run"] & 7) and bool(Trace.low_cols(s))
            if on and start is None:
                start = s["t"]
            elif not on and start is not None:
                worst = max(worst, s["t"] - start)
                start = None
        return worst

    def test_limits_under_radio_traffic(self):
        for pos, slot in ((SWITCH_BT, 1), (SWITCH_24G, 0)):
            with self.subTest(pos=pos):
                d = self.dev(stub_delays=False, rhythm=True)
                self.into(d, pos, slot, leds=0x02)
                d.status(slot, 1, leds=0x02)                  # pairing blink too
                d.press(d.fn, ms=20)                          # A and the Fn-held keys
                n_rx = len(d.radio.log().rx())
                d.trace_on()
                # 22-byte frames 1 ms apart for ~80 ms: a lit column's subframe
                # comes once per rotation (20 subframes, ~20 ms), so several
                # rotations, or whether a byte lands in one is luck
                for _ in range(40):
                    d.module(container_frame(0x01), gap_ms=1)
                tr = d.trace_off()
                d.release(d.fn, ms=20)
                rx = d.radio.log().rx()[n_rx:]
                self.assertGreater(len(rx), 800)
                check_invariants(self, tr, "radio traffic")
                self.assertTrue(tr.lit())
                w = self.dwell(tr)
                print("\n[dwell] %s: longest column dwell with the PWM on %.0f us, %d bytes received"
                      % (pos, w / 24.0, len(rx)))
                self.assertLessEqual(w, self.DWELL_MAX)
                # bytes did arrive while a column was lit (EUART0 nested there)
                spans = []
                start = None
                for s in tr.states:
                    on = bool(s["run"] & 7) and bool(Trace.low_cols(s))
                    if on and start is None:
                        start = s["t"]
                    elif not on and start is not None:
                        spans.append((start, s["t"]))
                        start = None
                inside = [t for t, v, e in rx if any(a <= t < b for a, b in spans)]
                self.assertTrue(inside, "some bytes arrived during a lit subframe")


    def test_two_envelopes_100us_apart_at_every_phase(self):
        """The module ends a frame, P4.7 stays high ~100 us, the next frame: both
        are framed and parsed wherever the gap falls in the 1 ms tick - the
        indicator render (once per 1 ms, the main loop's longest pass) and
        its table audit poll P4.7 as they go (rf_rx_poll), so neither spans
        the gap."""
        from f65_radio import announce_frame, frame
        ack_f0 = frame(0xF0, length=6)
        d = self.dev()
        self.into(d, SWITCH_24G, 0)
        d.ms(700)
        missed = []
        for off in range(0, 24000, 600):                     # 40 phases
            d.status(0, 3, leds=0x00)
            d.ms(5)
            m = d.mark()
            d.cmd("step %d" % off)
            d.radio.ready(False)
            d.radio.queue_rx(status_frame(0, 3, leds=0x02))
            d.ms(50, until=lambda s: d.radio.rx_pending() == 0)
            d.cmd("step 300")
            d.radio.ready(True)
            d.cmd("step 1200")                        # ~100 us high
            d.radio.ready(False)
            d.radio.queue_rx(announce_frame())
            d.ms(50, until=lambda s: d.radio.rx_pending() == 0)
            d.cmd("step 300")
            d.radio.ready(True)
            d.ms(10)
            if not (d.led_state() == 0x02 and ack_f0 in d.frames(m)):
                missed.append(off)
        self.assertEqual(missed, [], "phases (instructions into the tick) where an envelope was lost")


class TestHeldIndicatorsNeverBlink(IndCase):
    """Hardware report on build-5: with Fn held, one lit key at a time blinked
    off about every 5 s, moving through the lit keys. Cause: the Timer2
    interrupt that starts a subframe clears TF2 and only then re-arms Timer2,
    which until then still runs on the scan's ~100 us reload; an overflow of
    that reload in between leaves TF2 set, the interrupt comes again at once
    and is taken for the next scan, and that subframe's column is lit ~13 us
    instead of ~385 us. Where the scan ends on that grid is fixed by the code;
    the chip's instruction timing differs from the simulator's, so the test
    sweeps the phase (model 8: Timer2 starts counting a set number of cycles
    after every arm, xram 0x1f58) over more than one grid period, holding Fn
    30 s in each position: every lit column gets its full subframe in every
    rotation and every lit cell stays at 0x0400. On build-5 (71dabb0) some
    phases cut every subframe and the edges of that range cut one now and
    then."""

    FW = F65_FW
    PHASES = range(0, 2430, 30)           # 81 phases, over one ~2412-cycle grid period
    SCANS = 450                           # per phase: ~0.38 s, so 81 x 450 scans hold Fn ~31 s
    MIN_DWELL_US = 300                    # a full subframe is ~385 us

    def selections(self, tr):
        sel, cur = {}, None
        for st in tr.states:
            low = Trace.low_cols(st)
            on = bool(st["run"] & 7) and len(low) == 1
            c = next(iter(low)) if on else None
            if cur and (not on or c != cur[0]):
                sel.setdefault(cur[0], []).append((cur[1], st["t"]))
                cur = None
            if on and cur is None:
                cur = (c, st["t"])
        return sel

    def lit_cells(self, d):
        base = d._xdata_static("led", "led_fb")
        raw = d.get_xram(base, 16 * 36)
        out = {}
        for col in range(16):
            for j in range(18):
                v = raw[col * 36 + j * 2] << 8 | raw[col * 36 + j * 2 + 1]
                if v != PHASE[j]:
                    out[(col, j)] = v
        return out

    def hold(self, pos, slot):
        d = self.dev(stub_delays=False, rhythm=True)
        if d.get_xram(0x1F2F)[0] < 8:
            skip_or_fail("the simulator's SH68F90 model is older than 8 (no Timer2 arm delay): build tools/ucsim")
        if pos != SWITCH_USB:
            self.into(d, pos, slot, leds=0x02)
            d.ms(3100)                                    # past the 3 s solid
        else:
            d.cmd("set mem xram 0x%x 0x02" % d._a("keyboard_state"))
        d.press(d.fn, ms=100)
        cells = self.lit_cells(d)
        self.assertTrue(cells and all(v == DUTY_ON for v in cells.values()), cells)
        cols = {c for c, j in cells}
        ms = d._a("matrix_scan_full")
        bad = []
        for k, x in enumerate(self.PHASES):
            if pos != SWITCH_USB and k % 4 == 0:
                d.cmd("set mem xram 0x1f58 0x00 0x00")
                d.status(slot, 3, leds=0x02)              # the module's status, every ~1.5 s
            d.cmd("set mem xram 0x1f58 0x%02x 0x%02x" % (x & 0xFF, x >> 8))
            d.trace_on(4)
            for _ in range(self.SCANS // 150):
                d.cmd("break 0x%x 150" % ms)
                d.run()
                d.cmd("delete")
            tr = d.trace_off()
            sel = self.selections(tr)
            for c in cols:
                v = sel.get(c, [])[1:]                    # (the first may start before the trace)
                short = [round((e0 - s0) / 24) for s0, e0 in v if (e0 - s0) / 24 < self.MIN_DWELL_US]
                gaps = [round((b[0] - a[0]) / 24000, 1) for a, b in zip(v, v[1:]) if b[0] - a[0] > 1.5 * 20 * 900 * 24]
                if short or gaps or len(v) < self.SCANS // 20 - 3:
                    bad.append((x, c, len(v), short[:4], gaps[:4]))
            now = self.lit_cells(d)
            if now != cells:
                bad.append((x, "cells", {k2: now.get(k2) for k2 in cells if now.get(k2) != cells[k2]}))
        d.cmd("set mem xram 0x1f58 0x00 0x00")
        d.release(d.fn, ms=50)
        self.assertEqual(bad, [], "%s: (phase, column, subframes, short ones in us, gaps in ms)" % pos)
        return cols

    def test_usb(self):
        self.assertEqual(self.hold(SWITCH_USB, None), {0, 1, 6}, "Tab / Caps, A, Y")

    def test_24g(self):
        self.assertEqual(self.hold(SWITCH_24G, 0), {0, 1, 4}, "Tab / Caps, A, R")

    def test_bluetooth(self):
        self.assertEqual(self.hold(SWITCH_BT, 1), {0, 1}, "Tab / Caps, A and Q")


class TestWhereLedCodeRuns(unittest.TestCase):
    """Static (the linked image's call graph, utils/check_interrupts.py): no
    LED code is reachable from any interrupt handler except the Timer2 tick,
    and from that only the scan's stop and the subframe."""

    LED_MODULES = ("led", "indicators", "led_diag")
    TIMER2_ALLOWED = {"_indicators_pwm_disable", "_indicators_pwm_enable", "_led_hold", "_led_release", "_led_pwm_off",
                      "_indicators_pre_update", "_indicators_update_step", "_indicators_post_update", "_led_load",
                      "_led_col_select"}

    def led_funcs(self, asm_dir):
        """Every label of the LED modules (functions and data alike)."""
        funcs = set()
        for m in self.LED_MODULES:
            p = Path(asm_dir) / (m + ".asm")
            if p.exists():
                funcs |= set(re.findall(r"^(_\w+?):", p.read_text(), re.M))
        return funcs

    def test_call_graph(self):
        for fw in (F65_FW, F65_ANSI_FW, os.environ.get("SMK_F65_LEDDIAG_FIRMWARE") or ""):
            if not fw:
                continue
            with self.subTest(fw=Path(fw).name):
                if not Path(fw).exists():
                    skip_or_fail("no image at %s" % fw)
                asm_dir = str(Path(fw).with_suffix(".ihx.p"))
                calls = check_interrupts.call_graph(asm_dir)
                vec = check_interrupts.vector_table(asm_dir)
                led = self.led_funcs(asm_dir)
                self.assertTrue({"_led_set_duty", "_indicators_update_step"} <= led, sorted(led)[:20])
                timer2 = [h for i, h in vec.items() if i == 0]            # vector 0x0003
                self.assertEqual(len(timer2), 1)
                for idx, handler in vec.items():
                    hit = check_interrupts.reachable(calls, [handler]) & led
                    if idx == 0:
                        self.assertLessEqual(hit, self.TIMER2_ALLOWED, "Timer2: %s" % sorted(hit - self.TIMER2_ALLOWED))
                        self.assertIn("_indicators_update_step", hit)
                    else:
                        self.assertEqual(hit, set(), "vector %d (%s) reaches LED code" % (idx, handler))


# ---------------------------------------------------------- review items
class TestReviewItems(IndCase):

    def test_stall_backstop(self):
        """B4C-1: the scans stop (Timer2 masked) while a subframe's column is
        lit and the main loop runs: within ~5 ms the LEDs are held dark; when the
        scans come again, they light again."""
        d = self.dev(rhythm=True)
        d.radio.charging(True)
        d.ms(300)
        self.assertTrue(d.lit_ms(30).lit(), "Fn red")
        self.assertEqual(d.run_until(d._a("kb_update_switches")), d._a("kb_update_switches"))
        d.set_sfr(0xA8, d.get_sfr(0xA8) & ~0x01)             # ET2 off: no more scans
        st = d.as_after_a_lit_subframe(col=0)                # the state a lit subframe leaves
        self.assertEqual((st["run"], st["cols"]), ([True] * 3, {0}))
        d.trace_on()
        d.mark_trace(1)
        d.ms(8)
        tr = d.trace_off()
        t0 = tr.marks[0][0]
        dark = [s["t"] for s in tr.states if s["t"] > t0 and not s["run"] & 7 and not Trace.low_cols(s)]
        self.assertTrue(dark, "held dark")
        print("\n[stall] dark %.2f ms after the scans stopped" % ((dark[0] - t0) / CYCLES_PER_MS))
        self.assertLess((dark[0] - t0) / CYCLES_PER_MS, 6.5, "within ~5 ms")
        self.assertTrue(d.holds() & 0x40, "LED_HOLD_STALL")
        d.assertDark(self, "scans stopped")
        d.set_sfr(0xA8, d.get_sfr(0xA8) | 0x01)
        d.ms(5)
        self.assertFalse(d.holds() & 0x40)
        self.assertTrue(d.lit_ms(30).lit(), "lit again")

    def test_pll_wait_is_bounded(self):
        """3c F8: a PLL that never reports lock: the boot still reaches the main
        loop (the stock never polls PLLSTA; it switches after a fixed delay)."""
        _need(self.FW)
        d = IndRig(self.FW)
        self.addCleanup(d.close)
        d.cmd("set mem xram 0x1f54 0x01")
        d.radio.switch(SWITCH_USB)
        d.reset_fast()
        self.assertEqual(d.run_until(d._a("kb_update_switches")), d._a("kb_update_switches"))
        d.cmd("set mem xram 0x1f54 0x00")


if __name__ == "__main__":
    unittest.main()
