#!/usr/bin/env python3
"""aula-f65-v1 LED engine, indicators and diagnostic, in the patched uCsim.

The simulator's PWM model (tools/ucsim/sh68f90.cc, cl_sh68f90_pwm) traces every
latched PWM period ([PWMP]: DUTY1/DUTY2 of the six channels of a bank) and
every change of the PWM run/route state or of a P1-P7 latch or direction
([PWMC]). From that, Trace below rebuilds when each LED conducts: a channel's
pulse (DUTY1 to DUTY2 of its period, while its bank runs and routes it to the
pin) overlapping a low column. The board wiring (which pin is which column)
lives here, as in devices.py.

Where the expectations come from ("oracle" in the docstrings):
  - stock image: AULA_F65_V1_FN_Ctrl_firmware.bin (SMK_F65_STOCK_IMAGE), read
    statically or run in the same simulator. Skipped without it.
  - painter: the stock indicator painter 0x3108 run in uCsim by the analysis
    (the author's painter_oracle.json, not published); the values used are
    copied below and checked against the file when SMK_F65_PAINTER_ORACLE
    points at it.
  - LED map: the static analysis's key -> PWM channel map (led_map.json);
    copied below, checked against SMK_F65_LED_MAP when set.
  - mirror: the port's own design (slot counts, the Tab / A / S indicators,
    timings); these tests only hold the code to what docs/keyboards/aula-f65-v1.md
    says.
"""

import bisect
import json
import os
import re
import tempfile
import time
import unittest
from pathlib import Path

from sim import REPO_ROOT, load_symbols, skip_or_fail
from devices import UcsimSession, P5, P7
from test_f65 import F65Sim, F65KeyMatrix, F65_FW, F65_ANSI_FW, FN_ANSI, _need, ISP_ENTRY, ESC, S, report

F65_DIAG_FW = os.environ.get("SMK_F65_LEDDIAG_FIRMWARE") or str(REPO_ROOT / "build" / "aula-f65-v1_leddiag_smk.hex")

P1, P2, P3, P4, P6 = 0x90, 0x98, 0xA0, 0xB0, 0xC0
IEN1, IPH1, IPL1 = 0xA9, 0xB5, 0xB9
EPWM0 = 0x02
TRACE, MARK, PIN_P4, T2_REAL = 0x1F50, 0x1F51, 0x1F14, 0x1F52   # tools/ucsim model 7

# --- the board: columns (kbdef.h) and the LED-only columns 16/17 --------------
# port index in [PWMC] lat/cr: P1..P7 = 0..6
COLS = {0: (5, 0), 1: (5, 1), 2: (5, 2), 3: (5, 3), 4: (5, 4), 5: (5, 5), 6: (5, 6), 7: (5, 7),
        8: (4, 0), 9: (4, 1), 10: (4, 2), 11: (4, 7), 12: (3, 0), 13: (3, 2), 14: (3, 3), 15: (3, 5),
        16: (3, 6), 17: (6, 4)}      # 16 = P4.6, 17 = P7.4 (stock LED-only slots)

# --- stock facts (mirrored here; checked against the image in TestStockFacts) ---
# DUTY1 per channel = phase, stock init 0x6313
STOCK_PHASE = {"PWM00": 0xC3, "PWM01": 0xC4, "PWM02": 0xC5, "PWM03": 0xC0, "PWM04": 0xC1, "PWM05": 0xC2,
               "PWM10": 0xBA, "PWM11": 0xBB, "PWM12": 0xBC, "PWM13": 0xBD, "PWM14": 0xBE, "PWM15": 0xBF,
               "PWM20": 0xB4, "PWM21": 0xB5, "PWM22": 0xB6, "PWM23": 0xB7, "PWM24": 0xB8, "PWM25": 0xB9}
# slot channel j = row*3 + c -> PWM channel (loader 0x6C45)
LOADER = ["PWM21", "PWM20", "PWM22", "PWM24", "PWM23", "PWM25", "PWM11", "PWM10", "PWM12",
          "PWM14", "PWM13", "PWM15", "PWM04", "PWM03", "PWM05", "PWM01", "PWM00", "PWM02"]
PERIOD, CLK_SHIFT = 0x04B0, 1
PERIOD_CYCLES = PERIOD << CLK_SHIFT
DUTY_ON = 0x0400
SLOTS = 20

# LED map (led_map.json, static analysis): key -> (col, LED row, R, G, B channels)
LED_MAP = {
    "Esc": (0, 1, "PWM24", "PWM23", "PWM25"), "Tab": (0, 2, "PWM11", "PWM10", "PWM12"),
    "CapsLock": (0, 3, "PWM14", "PWM13", "PWM15"), "LShift": (0, 4, "PWM04", "PWM03", "PWM05"),
    "LCtrl": (0, 5, "PWM01", "PWM00", "PWM02"), "A": (1, 3, "PWM14", "PWM13", "PWM15"),
    "S": (2, 3, "PWM14", "PWM13", "PWM15"), "Y": (6, 2, "PWM11", "PWM10", "PWM12"),
    "Fn": (9, 5, "PWM01", "PWM00", "PWM02"), "RCtrl": (10, 5, "PWM01", "PWM00", "PWM02"),
    "Enter": (13, 3, "PWM14", "PWM13", "PWM15"), "Up": (14, 4, "PWM04", "PWM03", "PWM05"),
    "Right": (15, 5, "PWM01", "PWM00", "PWM02"),
    "side_0_B": (0, 0, "PWM20", "PWM22", "PWM21"), "side_0_A": (9, 0, "PWM20", "PWM22", "PWM21"),
}

# painter_oracle.json (the stock painter 0x3108 in uCsim): fb16 cell (col, row) -> c0, c1, c2
PAINTER = {
    "charging, USB power": {(9, 5): [1024, 195, 197]},
    "caps lock (host LED bit1), USB": {(0, 3): [1024, 1024, 1024]},
    "Fn held, USB": {(6, 2): [1024, 1024, 1024], (1, 2): [187, 186, 188], (3, 2): [187, 186, 188]},
}


def chan_name(i):
    return "PWM%d%d" % (i // 6, i % 6)


# --- trace ------------------------------------------------------------------
RE_P = re.compile(r"\[PWMP\] b=(\d) t=(\d+) n=(\d+) per=(\d+) sh=(\d+) d1=([0-9a-f,]+) d2=([0-9a-f,]+)")
RE_C = re.compile(r"\[PWMC\] t=(\d+) run=([0-9a-f]+) ss=([0-9a-f]+) lat=([0-9a-f]{14}) cr=([0-9a-f]{14})")
RE_W = re.compile(r"\[PWMW\] t=(\d+) a=(s?[0-9a-f]+) v=([0-9a-f]{2})")
RE_M = re.compile(r"\[PWMM\] t=(\d+) v=([0-9a-f]{2})")


class Trace:
    """The PWM / pin history of one window, and what it lit."""

    def __init__(self, text):
        self.periods, self.states, self.writes, self.marks = [], [], [], []
        for line in text.splitlines():
            m = RE_P.search(line)
            if m:
                b, t, n, per, sh = (int(m.group(k)) for k in range(1, 6))
                self.periods.append(dict(b=b, t=t, n=n, per=per, sh=sh,
                                         d1=[int(x, 16) for x in m.group(6).split(",")],
                                         d2=[int(x, 16) for x in m.group(7).split(",")]))
                continue
            m = RE_C.search(line)
            if m:
                lat = bytes.fromhex(m.group(4))
                cr = bytes.fromhex(m.group(5))
                self.states.append(dict(t=int(m.group(1)), run=int(m.group(2), 16), ss=int(m.group(3), 16),
                                        lat=lat, cr=cr))
                continue
            m = RE_W.search(line)
            if m:
                a = m.group(2)
                self.writes.append((int(m.group(1)), a if a.startswith("s") else int(a, 16), int(m.group(3), 16)))
                continue
            m = RE_M.search(line)
            if m:
                self.marks.append((int(m.group(1)), int(m.group(2), 16)))
        self.end = max([p["t"] for p in self.periods] + [s["t"] for s in self.states] + [0])
        self._starts = [s["t"] for s in self.states]

    @staticmethod
    def low_cols(state):
        return {c for c, (port, bit) in COLS.items()
                if state["cr"][port] >> bit & 1 and not state["lat"][port] >> bit & 1}

    def state_spans(self, t0, t1):
        """(start, end, state) pieces of [t0, t1)."""
        out = []
        k = max(bisect.bisect_right(self._starts, t0) - 1, 0)
        while k < len(self.states) and self.states[k]["t"] < t1:
            s = self.states[k]
            s1 = self.states[k + 1]["t"] if k + 1 < len(self.states) else float("inf")
            a, b = max(s["t"], t0), min(s1, t1)
            if a < b:
                out.append((a, b, s))
            k += 1
        return out

    def lit(self):
        """Every stretch where an LED conducts: (column, channel, start, end, DUTY2)."""
        segs = []
        by_bank = {}
        for p in self.periods:
            by_bank.setdefault(p["b"], []).append(p)
        for b, ps in by_bank.items():
            for k, p in enumerate(ps):
                cut = ps[k + 1]["t"] if k + 1 < len(ps) else float("inf")
                for ch in range(6):
                    d1, d2 = p["d1"][ch], p["d2"][ch]
                    if d2 <= d1:
                        continue
                    w0 = p["t"] + (d1 << p["sh"])
                    w1 = min(p["t"] + (min(d2, p["per"]) << p["sh"]), cut)
                    i = b * 6 + ch
                    for a, e, s in self.state_spans(w0, w1):
                        if s["run"] >> b & 1 and s["ss"] >> i & 1:
                            for col in self.low_cols(s):
                                segs.append((col, chan_name(i), a, e, d2))
        return sorted(segs, key=lambda x: x[2])

    def lit_set(self):
        return {(col, ch, d2) for col, ch, _, _, d2 in self.lit()}

    def running(self):
        return any(s["run"] & 7 for s in self.states)


def wait_for_sentinel(sess, start):
    """Put a marker into the trace (still on) and wait until the stderr reader
    has it: everything the trace wrote before it is then in sess.serr."""
    sess.cmd("set mem xram 0x%x 0xee" % MARK)
    end = time.time() + 20
    while time.time() < end:
        with sess._serr_lock:
            if any("[PWMM]" in l and "v=ee" in l for l in sess.serr[start:]):
                return
        time.sleep(0.01)
    raise AssertionError("trace sentinel never arrived")


def check_invariants(tc, tr, what=""):
    """The engine's hard limits, on one trace (see docs/keyboards/aula-f65-v1.md).
    A "selection" is one LED subframe: a column lit for consecutive PWM periods.
    (With nothing lit the PWM does not run at all: no period in the trace.)"""
    # DUTY1 <= DUTY2 <= 0x0400, DUTY1 = the stock phase, period and clock stock
    for p in tr.periods:
        tc.assertEqual((p["per"], p["sh"]), (PERIOD, CLK_SHIFT), f"{what}: bank {p['b']} period / clock")
        for ch in range(6):
            name = chan_name(p["b"] * 6 + ch)
            tc.assertEqual(p["d1"][ch], STOCK_PHASE[name], f"{what}: {name} DUTY1")
            tc.assertTrue(p["d1"][ch] <= p["d2"][ch] <= DUTY_ON, f"{what}: {name} DUTY2 {p['d2'][ch]:#x} at t={p['t']}")
    prev = None
    for s in tr.states:
        low = Trace.low_cols(s)
        # at most one column low while a LED bank runs
        if s["run"] & 7:
            tc.assertLessEqual(len(low), 1, f"{what}: columns {sorted(low)} low with the PWM on, t={s['t']}")
            # a column is selected before the banks start, never switched while they run
            if prev is not None and prev["run"] & 7:
                tc.assertFalse(low - Trace.low_cols(prev), f"{what}: column switched with the PWM on, t={s['t']}")
        # the stock LED-only columns P4.6 / P7.4 are never driven low
        tc.assertFalse(low & {16, 17}, f"{what}: P4.6/P7.4 low at t={s['t']}")
        # P1-P3 latches stay 0
        tc.assertEqual([s["lat"][k] & 0x3F for k in range(3)], [0, 0, 0], f"{what}: P1-P3 latches at t={s['t']}")
        prev = s
    segs = tr.lit()
    # pulses (segments of one channel on one column, joined)
    pulses = {}
    for col, ch, a, e, d2 in segs:
        lst = pulses.setdefault((col, ch), [])
        if lst and a - lst[-1][1] <= 4:
            lst[-1][1] = e
        else:
            lst.append([a, e])
    for (col, ch), lst in pulses.items():
        for a, e in lst:
            tc.assertLessEqual(e - a, ((DUTY_ON - STOCK_PHASE[ch]) << CLK_SHIFT) + 4, f"{what}: {ch} on column {col} too long")
        # selections: pulses less than 3 periods apart
        sel = [[lst[0][0], lst[0][0], 1]]
        for a, e in lst[1:]:
            if a - sel[-1][1] < 3 * PERIOD_CYCLES:
                sel[-1][1] = a
                sel[-1][2] += 1
            else:
                sel.append([a, a, 1])
        for a, last, n in sel:
            tc.assertLessEqual(n, SUBFRAME_PULSES, f"{what}: {ch} on column {col}: {n} pulses in one selection")
        # the same LED again only after enough periods to stay under the stock's
        # average: <= SUBFRAME_PULSES pulses per 20 x SUBFRAME_PULSES periods
        for s0, s1 in zip(sel, sel[1:]):
            tc.assertGreaterEqual(s1[0] - s0[0], SLOTS * SUBFRAME_PULSES * PERIOD_CYCLES,
                                  f"{what}: column {col} lit again after {(s1[0] - s0[0]) / PERIOD_CYCLES:.0f} periods")
    return segs


SUBFRAME_PULSES = 5   # an LED subframe is ~400 us: 4 PWM periods, 5 if it starts early in one


# --- the simulated board ------------------------------------------------------
class LedMatrix(F65KeyMatrix):
    """F65 key matrix plus the charge-status pin P7.7 (low = charging)."""

    def __init__(self):
        super().__init__()
        self.p7_low = 0

    def inject(self, sess):
        port_cache = {}
        low_rows = set()
        from test_f65 import ROW_PIN
        for (c, r) in self.pressed:
            if self._col_driven_low(sess, c, port_cache):
                low_rows.add(r)
        level = {P5: 0xFF, P7: 0xFF & ~self.p7_low}
        for r in low_rows:
            port, bit = ROW_PIN[r]
            level[port] &= ~(1 << bit)
        sess.set_pin(P7, level[P7])
        sess.set_pin(P5, level[P5])


class LedSim(F65Sim):
    LIGHTING = False                               # the indicators alone (build-6: backlight off)

    def __init__(self, firmware=None):
        super().__init__(firmware)
        self.matrix = LedMatrix()
        self._mark = 0
        if "leddiag" in Path(self.firmware).name:
            self.fn = FN_ANSI                      # leddiag is ansi plus the diagnostic keys
        self.window = 3                            # scans per lit_window

    def hardware_rhythm(self):
        """Scan at the board's rhythm: Timer2 at its real rate (systick.c reloads,
        Fsys/12) and the real delay_us in the sweep, so the PWM runs about 4
        periods between two scans, as it will on the board. (By default the model
        raises the scan tick every 30000 cycles, which leaves ~24 periods.)"""
        from test_air75 import read_ihex
        a = self._a("delay_us")
        self.cmd("set mem rom 0x%x 0x%02x" % (a, read_ihex(self.firmware)[a]))
        self.cmd("set mem xram 0x%x 0x01" % T2_REAL)
        self.window = 24

    # --- trace window --------------------------------------------------------
    def trace_on(self, bits=1):
        self.cmd("set mem xram 0x%x 0x%02x" % (TRACE, bits))
        with self._serr_lock:
            self._mark = len(self.serr)

    def trace_off(self):
        wait_for_sentinel(self, self._mark)
        self.cmd("set mem xram 0x%x 0x00" % TRACE)
        with self._serr_lock:
            window = self.serr[self._mark:]
            # drop the trace lines (later ep1_reports() stay cheap), keep the rest
            self.serr[self._mark:] = [l for l in window if not l.startswith("[PWM")]
        return Trace("".join(window))

    def mark(self, v):
        self.cmd("set mem xram 0x%x 0x%02x" % (MARK, v))

    # --- running ----------------------------------------------------------------
    def scans(self, n, keys=True):
        """Run n matrix scans, serving the key matrix (and P7.7) at every row read."""
        rr, ms = self._a("user_matrix_read_rows"), self._a("matrix_scan_full")
        if keys:
            self.brk(rr)
        self.brk(ms)
        done = 0
        while done < n:
            at = self.stopped_at(self.run())
            if at == ms:
                done += 1
            elif at == rr:
                self.matrix.inject(self)
        self.cmd("delete")

    def charging(self, on):
        """P7.7 low (charging) now and at every later row read."""
        self.matrix.p7_low = 0x80 if on else 0
        self.set_pin(P7, 0xFF & ~self.matrix.p7_low)

    def usb_power(self, on):
        self.cmd("set mem xram 0x%x 0x%02x" % (PIN_P4, 0xFF if on else 0xEF))

    def host_leds(self, value):
        self.cmd("set mem xram 0x%x 0x%02x" % (self._a("keyboard_state"), value))

    def pwm_regs(self):
        return self.get_xram(0xFF80, 0x80)

    def pwm_off_state(self):
        """(banks running, IEN1.EPWM0, columns low, P1-P3 latches) right now."""
        con = self.get_xram(0xFF80, 18)
        run = [bool(con[b * 6] & 0x80) for b in range(3)]
        cols = set()
        for c, (port, bit) in COLS.items():
            sfr = [P1, P2, P3, P4, P5, P6, P7][port]
            cr = [0xE2, 0xE3, 0xE4, 0xE5, 0xE6, 0xE7, 0xD1][port]
            if self.get_sfr(cr) >> bit & 1 and not self.get_sfr(sfr) >> bit & 1:
                cols.add(c)
        return dict(run=run, epwm0=bool(self.get_sfr(IEN1) & EPWM0), cols=cols,
                    p123=[self.get_sfr(p) & 0x3F for p in (P1, P2, P3)], ss=[c & 0x08 for c in con])

    def as_after_a_lit_subframe(self, col=0):
        """Put the board, right now, in the state a lit LED subframe leaves: the
        banks started as the subframe starts them (CON 0x08, CON0 0x89) and one
        column low. The main loop can be anywhere in that state (a subframe runs
        in the Timer2 ISR), so a hook that must go dark is checked from it; the
        natural rhythm reaches it only when the event falls just after a lit slot."""
        for i in range(18):
            self.cmd("set mem xram 0x%x 0x%02x" % (0xFF80 + i, 0x89 if i % 6 == 0 else 0x08))
        port, bit = COLS[col]
        sfr = [P1, P2, P3, P4, P5, P6, P7][port]
        self.set_sfr(sfr, self.get_sfr(sfr) & ~(1 << bit) & 0xFF)
        return self.pwm_off_state()

    def assertDark(self, tc, what):
        st = self.pwm_off_state()
        tc.assertEqual(st["run"], [False] * 3, f"{what}: PWM banks running")
        tc.assertFalse(st["epwm0"], f"{what}: PWM0 interrupt enabled")
        tc.assertEqual(st["ss"], [0] * 18, f"{what}: a channel still routed to its pin")
        tc.assertEqual(st["cols"], set(), f"{what}: columns low")
        tc.assertEqual(st["p123"], [0, 0, 0], f"{what}: P1-P3 latches")

    def lit_window(self, scans=None, keys=True):
        self.trace_on()
        self.scans(scans or self.window, keys)
        return self.trace_off()

    def table(self):
        """The duty table as {(col, row): [c0, c1, c2]}."""
        base = self._xdata_static("led", "led_fb")
        raw = self.get_xram(base, 16 * 36)
        out = {}
        for col in range(16):
            for row in range(6):
                o = col * 36 + row * 6
                out[(col, row)] = [raw[o + 2 * k] << 8 | raw[o + 2 * k + 1] for k in range(3)]
        return out


def expect_cell(key, rgb, duty=DUTY_ON, where=None):
    """Lit set of one key cell in colour rgb (subset of 'RGB') at DUTY2 = duty."""
    col, row, r, g, b = LED_MAP[key]
    if where:
        col = where
    return {(col, ch, duty) for ch, on in zip((r, g, b), ("R" in rgb, "G" in rgb, "B" in rgb)) if on}


class LedCase(unittest.TestCase):
    FW = F65_FW

    @classmethod
    def setUpClass(cls):
        _need(cls.FW)

    def session(self, fw=None, mac=False, power=False, rhythm=True):
        kb = LedSim(fw or self.FW)
        self.addCleanup(kb.close)
        kb.usb_power(power)
        kb.boot(mac=mac)
        if rhythm:
            kb.hardware_rhythm()
        return kb


# --- the PWM setup against the stock image -----------------------------------------
def stock_image():
    img = os.environ.get("SMK_F65_STOCK_IMAGE")
    if not img or not Path(img).exists():
        # (skip_or_fail: under SMK_TESTS_STRICT a missing oracle fails, also
        # where a class's setUpClass asks for it)
        skip_or_fail("set SMK_F65_STOCK_IMAGE to the official AULA_F65_V1_FN_Ctrl_firmware.bin")
    data = Path(img).read_bytes()
    assert len(data) == 0xF000
    return data


def stock_straight_writes(data, start, end):
    """(address, value) of every MOVX @DPTR,A in straight-line stock code
    (MOV DPTR,#imm / MOV A,#imm / CLR A / INC DPTR / MOVX @DPTR,A only)."""
    a, dptr, acc, out = start, None, None, []
    while a < end:
        op = data[a]
        if op == 0x90:
            dptr, a = data[a + 1] << 8 | data[a + 2], a + 3
        elif op == 0x74:
            acc, a = data[a + 1], a + 2
        elif op == 0xE4:
            acc, a = 0, a + 1
        elif op == 0xA3:
            dptr, a = dptr + 1, a + 1
        elif op == 0xF0:
            out.append((dptr, acc))
            a += 1
        else:
            raise AssertionError("unexpected opcode %02x at %04x" % (op, a))
    return out


def stock_hex(data, tmp):
    path = Path(tmp) / "stock_v1.hex"
    with open(path, "w") as f:
        for addr in range(0, len(data), 16):
            chunk = data[addr:addr + 16]
            rec = bytes([len(chunk), addr >> 8, addr & 0xFF, 0]) + chunk
            f.write(":%s%02X\n" % (rec.hex().upper(), (-sum(rec)) & 0xFF))
        f.write(":00000001FF\n")
    return str(path)


class StockSim(UcsimSession):
    """The stock V1 image in the same simulator: boot, paint a state with the
    stock indicator painter (0x3108) on a clean frame buffer, start its PWM (0x6313,
    0x90D7) and let its own slot interrupt (0x6A83) run."""

    STUB = 0xEFD0
    FB16 = 0x05A2

    def __init__(self, hexfile):
        super().__init__(firmware=hexfile)
        self._mark = 0

    def iram(self, a):
        out = self.cmd("dump iram 0x%x 0x%x" % (a, a))
        return int(re.search(r"0x[0-9a-f]+\s+([0-9a-f]{2})", out).group(1), 16)

    def setbit(self, spec, v):
        byte, bit = spec
        cur = self.iram(byte)
        self.cmd("set mem iram 0x%x 0x%02x" % (byte, (cur | 1 << bit) if v else (cur & ~(1 << bit))))

    def boot(self):
        self.cmd("reset")
        self.cmd("break 0x6313")
        self.cmd("run")
        self.cmd("delete")
        for _ in range(30):
            self.cmd("step 200000")

    def paint_and_run(self, bits=(), xram=(), periods=45):
        self.cmd("set mem sfr 0xa8 0x00")                     # EA off while we set up
        # every fb16 cell back to its phase (the boot animation leaves colours)
        phase = [STOCK_PHASE[n] for n in LOADER]
        for col in range(21):
            vals = []
            for j in range(18):
                vals += [0, phase[j]]
            self.cmd("set mem xram 0x%x " % (self.FB16 + col * 36) + " ".join("0x%02x" % v for v in vals))
        self.setbit((0x26, 3), 0)                             # power-on animation done
        self.setbit((0x27, 2), 0)                             # no column blanking
        for spec, v in bits:
            self.setbit(spec, v)
        for a, v in xram:
            self.cmd("set mem xram 0x%x " % a + " ".join("0x%02x" % x for x in (v if isinstance(v, list) else [v])))
        # LCALL 0x3108 (painter); LCALL 0x6313 (PWM init); LCALL 0x90D7 (PWM0 int on); SJMP $
        self.cmd("set mem rom 0x%x 0x12 0x31 0x08 0x12 0x63 0x13 0x12 0x90 0xd7 0x80 0xfe" % self.STUB)
        self.cmd("pc 0x%x" % self.STUB)
        self.cmd("break 0x%x" % (self.STUB + 9))
        self.cmd("run")
        self.cmd("delete")
        self.cmd("set mem sfr 0xa9 0x02")                     # IEN1: PWM0 only
        self.cmd("set mem sfr 0xa8 0x80")                     # EA, no Timer2 frame task
        self.cmd("set mem xram 0x%x 0x01" % TRACE)
        with self._serr_lock:
            self._mark = len(self.serr)
        self.cmd("step %d" % (periods * 1300))                # ~periods PWM periods of 2400 cycles
        wait_for_sentinel(self, self._mark)
        self.cmd("set mem xram 0x%x 0x00" % TRACE)
        with self._serr_lock:
            return Trace("".join(self.serr[self._mark:]))


# --- tests -------------------------------------------------------------------------
class TestStockFacts(unittest.TestCase):
    """Oracle: stock image. The constants this file and led.c mirror."""

    @classmethod
    def setUpClass(cls):
        cls.stock = stock_image()

    def test_phases_and_loader_order(self):
        init = stock_straight_writes(self.stock, 0x6313, 0x64FB)
        duty1l = {a: v for a, v in init if 0xFFA0 <= a <= 0xFFB1}
        for i in range(18):
            self.assertEqual(duty1l[0xFFA0 + i], STOCK_PHASE[chan_name(i)], chan_name(i))
        # loader 0x6C45: DUTY2H address of each MOV DPTR,#0xffe8+ in order
        order, a = [], 0x6C45
        while len(order) < 18:
            a = self.stock.index(b"\x90\xff", a)
            x = self.stock[a + 2]
            if 0xE8 <= x <= 0xF9:
                order.append(chan_name(x - 0xE8))
            a += 3
        self.assertEqual(order, LOADER)
        self.assertEqual(list(self.stock[0x2605:0x2605 + 18]), [STOCK_PHASE[n] for n in LOADER], "phase table 0x2605")
        self.assertEqual(self.stock[0x6B79:0x6B7B], bytes([0x94, SLOTS]), "20 slots (SUBB A,#0x14)")


class TestPwmSequences(LedCase):
    """Oracle: stock image. The PWM start and stop, register write for register write."""

    def test_start_and_stop_are_the_stock_sequences(self):
        """The PWM set-up at start is the stock 0x6313 sequence, write for write
        (the stock's period interrupt, 0x90D7, is not used); every scan stops the
        banks with the stock 0xAD49 sequence; a lit subframe loads DUTY2 in the
        stock order (0x6C45) and starts the banks with the stock CON values, in
        the Air75's order: each bank started, then its channels routed (review
        B4C-4: no channel routed while its bank is stopped)."""
        stock = stock_image()
        setup = stock_straight_writes(stock, 0x6313, 0x64FB)
        self.assertEqual(len(setup), 96)
        stop = [(0xFF80 + i, 0x01) for i in range(18)]              # 0xAD49
        self.assertEqual(stock_straight_writes(stock, 0xAD49, 0xAD71), stop)
        start = []
        for b in range(3):
            start += [(0xFF80 + b * 6, 0x89)] + [(0xFF80 + b * 6 + c, 0x08) for c in range(1, 6)]
        for fw in (F65_FW, F65_ANSI_FW):
            with self.subTest(fw=Path(fw).name):
                kb = LedSim(fw)
                self.addCleanup(kb.close)
                kb.trace_on(3)
                kb.boot()
                w = [(a, v) for t, a, v in kb.trace_off().writes if isinstance(a, int) and a < 0xFFFA]
                i = w.index(setup[0])
                self.assertEqual(w[i:i + 96], setup, "set-up = stock 0x6313")
                kb.hardware_rhythm()
                kb.host_leds(0x02)
                kb.scans(3, keys=False)
                kb.trace_on(3)
                kb.scans(24, keys=False)
                w = [(a, v) for t, a, v in kb.trace_off().writes if isinstance(a, int) and a < 0xFFFA]
                i = w.index(stop[0])
                self.assertEqual(w[i:i + 18], stop, "stop = stock 0xAD49")
                starts = [k for k in range(len(w) - 17) if w[k:k + 18] == start]
                self.assertTrue(starts, "a lit subframe, each bank started before its channels are routed")
                k = starts[0]
                duty2 = [a for a, v in w[k - 36:k]]
                order = []
                for n in LOADER:
                    idx = int(n[3]) * 6 + int(n[4])
                    order += [0xFFE8 + idx, 0xFFD0 + idx]
                self.assertEqual(duty2, order, "DUTY2 in the stock loader order, high byte first")


class TestPeriodAndPhases(LedCase):
    """Oracle: stock image (via TestStockFacts' constants). Every latched period:
    1200 counts at Fsys/2, DUTY1 = the stock phase."""

    def test_every_period(self):
        kb = self.session()
        kb.scans(2, keys=False)                      # past the first scan (the set-up leaves the banks on, dark)
        tr = kb.lit_window(keys=False)
        self.assertEqual((tr.periods, tr.lit()), ([], []), "nothing lit: the PWM does not run")
        check_invariants(self, tr, "idle")
        kb.host_leds(0x02)
        tr = kb.lit_window(48, keys=False)
        self.assertGreater(len(tr.periods), 20)
        check_invariants(self, tr, "Caps Lock")


class TestInvariants(LedCase):
    """Mirror (limits from the stock: 0x0400 in 1 of 20 slots). Every indicator
    at once, through scans, a USB control transfer and a settings save."""

    def _busy(self, kb, what):
        kb.host_leds(0x02)                           # Caps Lock
        kb.charging(True)
        kb.usb_power(True)
        kb.scans(13 * 23, keys=False)                # 20+ power samples
        kb.matrix.press(*kb.fn)                      # Y, A (or S), Tab
        kb.step()
        kb.trace_on()
        kb.scans(24)
        kb.nest_usb_descriptor()                     # a USB control transfer in the middle
        kb.scans(24)
        tr = kb.trace_off()
        segs = check_invariants(self, tr, what)
        self.assertTrue(segs, f"{what}: something must be lit")
        return segs

    def test_all_indicators_usjis(self):
        kb = self.session(F65_FW)
        segs = self._busy(kb, "usjis")
        cols = {s[0] for s in segs}
        self.assertEqual(cols, {6, 0, 1, 10}, "Y, Tab + Caps (col 0), A, Fn (col 10)")

    def test_all_indicators_ansi(self):
        kb = self.session(F65_ANSI_FW)
        segs = self._busy(kb, "ansi")
        self.assertEqual({s[0] for s in segs}, {6, 0, 1, 9}, "Y, Caps (col 0), A, Fn (col 9)")

    def test_key_rows_are_read_with_the_pwm_off(self):
        """Every user_matrix_read_rows: banks stopped, PWM0 interrupt off, at most
        the scanned column low, even with indicators lit."""
        kb = self.session()
        kb.host_leds(0x02)
        kb.matrix.press(*kb.fn)
        kb.step()
        rr = kb._a("user_matrix_read_rows")
        kb.brk(rr)
        for _ in range(40):
            self.assertEqual(kb.stopped_at(kb.run()), rr)
            st = kb.pwm_off_state()
            self.assertEqual(st["run"], [False] * 3)
            self.assertFalse(st["epwm0"])
            self.assertLessEqual(len(st["cols"]), 1)
            kb.matrix.inject(kb)
        kb.cmd("delete")

    def test_stack_with_leds_and_usb(self):
        """The LED subframe runs in the Timer2 interrupt while USB requests are
        answered: the stack must still fit (SSEG ends at 0xFF)."""
        kb = self.session()
        kb.host_leds(0x02)
        kb.matrix.press(*kb.fn)
        kb.step()
        kb.paint_stack()
        for _ in range(6):
            kb.nest_usb_descriptor()
            kb.scans(1)
        used = kb.stack_highwater()
        room = kb.STACK_TOP - kb.stack_base
        print(f"\n[stack] deepest with LEDs lit and USB requests: {used}/{room} bytes")
        self.assertLess(used, room - 16, "less than 16 bytes of stack left")

    def test_every_column_shows_at_the_hardware_rhythm(self):
        """At the board's rhythm (one LED subframe of ~4 PWM periods between two
        scans) every column takes its turn: one channel lit in all 16 columns,
        each column gets full pulses at least twice in ~48 ms (a frame is 20
        subframes, ~15 ms), and waits less than 20 ms."""
        kb = self.session(F65_ANSI_FW)
        base, flags = kb._xdata_static("led", "led_fb"), kb._xdata_static("led", "led_lit_col")
        for col in range(16):
            kb.cmd("set mem xram 0x%x 0x04 0x00" % (base + col * 36 + 3 * 2))   # LED row 1, red: 0x0400
            kb.cmd("set mem xram 0x%x 0x01" % (flags + col))
        kb.scans(3, keys=False)
        tr = kb.lit_window(64, keys=False)
        check_invariants(self, tr, "hardware rhythm")
        starts = {c: [] for c in range(16)}
        for col, ch, a, e, d2 in tr.lit():
            if e - a == (DUTY_ON - STOCK_PHASE[ch]) << CLK_SHIFT:
                if not starts[col] or a - starts[col][-1] > 6 * PERIOD_CYCLES:
                    starts[col].append(a)
        ms = (tr.end - tr.periods[0]["t"]) / 24000
        gap = max(b - a for c in range(16) for a, b in zip(starts[c], starts[c][1:])) / 24000
        print(f"\n[hardware rhythm] {ms:.1f} ms, subframes shown per column {[len(starts[c]) for c in range(16)]}, "
              f"longest wait {gap:.1f} ms")
        for col in range(16):
            self.assertGreaterEqual(len(starts[col]), 2, f"column {col} shown {len(starts[col])} times in {ms:.0f} ms")
        self.assertLess(gap, 20, "a column waited more than 20 ms for its turn")

    def test_anode_latches_through_boot(self):
        """From reset to the main loop: SDCC's init writes its page register,
        __XPAGE = SFR 0xA0, which is the P3 latch (anodes PWM00-05) on this chip.
        The anode ports must never drive high: whenever a P1-P3 pin is an output
        its latch is 0 (user_gpio_init writes the latches before the directions),
        and in the main loop the latches are 0."""
        kb = LedSim(self.FW)
        self.addCleanup(kb.close)
        kb.trace_on()
        kb.reset_fast()
        self.assertEqual(kb.run_until(kb._a("kb_update_switches")), kb._a("kb_update_switches"))
        kb.scans(2, keys=False)
        tr = kb.trace_off()
        self.assertTrue(any(st["lat"][2] & 0x3F for st in tr.states),
                        "SDCC's init did write the P3 latch (__XPAGE = 0xA0)")
        for st in tr.states:
            self.assertEqual([st["cr"][k] & st["lat"][k] & 0x3F for k in range(3)], [0, 0, 0],
                             f"an anode pin driven high at t={st['t']}")
        self.assertEqual([kb.get_sfr(p) & 0x3F for p in (P1, P2, P3)], [0, 0, 0], "P1-P3 latches in the main loop")

    def test_warm_start_does_not_drive_the_anodes(self):
        """A jump to 0 without a reset, with the PWM running (Caps lit), a column
        low and interrupts off, as after a crash: STARTUP_LED_OFF stops the PWM
        and makes P1-P3 inputs before anything else (SDCC's init then writes
        __XPAGE = SFR 0xA0 = P3), so nothing lights on the way to user_gpio_init."""
        kb = self.session()
        kb.host_leds(0x02)
        for _ in range(40):                          # stop in a lit subframe (at the next scan's entry)
            kb.scans(1, keys=False)
            if kb.pwm_off_state()["run"][0]:
                break
        self.assertTrue(kb.pwm_off_state()["run"][0], "PWM running before the jump")
        kb.set_sfr(0xA8, 0x00)                        # interrupts off
        kb.set_sfr(P6, 0xFE)                          # column 0 low
        kb.trace_on()
        kb.mark(1)
        kb.cmd("pc 0x0000")
        self.assertEqual(kb.run_until(kb._a("user_gpio_init")), kb._a("user_gpio_init"))
        tr = kb.trace_off()
        t0 = tr.marks[0][0]
        stopped = [s["t"] for s in tr.states if s["t"] >= t0 and not s["run"] & 7]
        self.assertTrue(stopped, "PWM stopped")
        self.assertLess(stopped[0] - t0, 200, "stopped within the first instructions")
        self.assertEqual([seg for seg in tr.lit() if seg[2] > stopped[0]], [], "nothing lit after that")
        for st in tr.states:
            driven = [st["cr"][k] & st["lat"][k] & 0x3F for k in range(3)]
            if st["t"] >= t0 and Trace.low_cols(st):
                self.assertEqual(driven, [0, 0, 0], f"anodes driven high with a column low at t={st['t']}")

    def test_warm_start_with_interrupts_on(self):
        """Review B4C-5: the same jump to 0 with interrupts on (EA = 1) and the
        Timer2 tick and USB due: STARTUP_LED_OFF clears EA first, so no handler
        of the old image runs (and relights) before user_gpio_init, and the PWM
        stops within the first instructions."""
        kb = self.session()
        kb.host_leds(0x02)
        for _ in range(40):
            kb.scans(1, keys=False)
            if kb.pwm_off_state()["run"][0]:
                break
        self.assertTrue(kb.pwm_off_state()["run"][0], "PWM running before the jump")
        self.assertTrue(kb.get_sfr(0xA8) & 0x80, "interrupts on")
        kb.set_sfr(P6, 0xFE)                          # column 0 low
        kb.cmd("set mem xram 0x1f09 0x01")            # a Timer2 tick pending
        kb.set_sfr(0x92, 0x10)                        # and a USB SETUP
        handlers = [kb._a("systick_interrupt_handler"), kb._a("usb_interrupt_handler"),
                    kb._a("pwm4_ms_tick_interrupt_handler"), kb._a("rf_euart0_interrupt_handler")]
        kb.trace_on()
        kb.mark(1)
        kb.cmd("pc 0x0000")
        at = kb.run_until(kb._a("user_gpio_init"), *handlers)
        tr = kb.trace_off()
        self.assertEqual(at, kb._a("user_gpio_init"), "no interrupt handler ran before user_gpio_init")
        self.assertEqual(kb.get_sfr(0xA8) & 0x80, 0, "EA off")
        t0 = tr.marks[0][0]
        stopped = [s["t"] for s in tr.states if s["t"] >= t0 and not s["run"] & 7]
        self.assertTrue(stopped, "PWM stopped")
        self.assertLess(stopped[0] - t0, 200, "stopped within the first instructions")
        self.assertEqual([seg for seg in tr.lit() if seg[2] > stopped[0]], [], "nothing lit after that")

    def test_usb_traffic(self):
        """40 USB control transfers while three columns are lit: the limits hold."""
        kb = self.session(F65_ANSI_FW)
        kb.host_leds(0x02)
        kb.matrix.press(*kb.fn)
        kb.step()
        kb.trace_on()
        for _ in range(40):
            kb.nest_usb_descriptor()
        tr = kb.trace_off()
        check_invariants(self, tr, "USB traffic")
        self.assertTrue(tr.lit(), "lit during the transfers")

    def test_no_led_interrupt(self):
        """As on the Air75 (and build-2): no LED interrupt. The LED work runs in the
        Timer2 tick, at the same priority as USB, so nothing LED nests over the USB
        interrupt; the interrupt priorities stay at their reset values but for
        EUART0 at 3 (the radio build, as the stock 0xB108: IPH1 = IPL1 = ES0)."""
        kb = self.session()
        kb.host_leds(0x02)
        for _ in range(2 * 20 + 8):                  # past every slot (Caps lit in one of 20), twice
            kb.scans(1)
            self.assertEqual(kb.get_sfr(IEN1) & EPWM0, 0, "PWM0 interrupt off")
            self.assertEqual([kb.get_xram(0xFF80)[0] & 0x40, kb.get_sfr(IPH1), kb.get_sfr(IPL1)], [0, 0x40, 0x40])
            self.assertEqual([kb.get_sfr(0xB4), kb.get_sfr(0xB8)], [0, 0], "IPH0 / IPL0")


class TestDarkStates(LedCase):
    """Mirror, with the stock doing the same before sleep (0xAD49) and on USB
    suspend (0x9A67). Nothing is left lit or driven where the engine stops."""

    def test_settings_save(self):
        self._settings_save()

    def test_settings_save_right_after_a_lit_subframe(self):
        """The save starts in the state a lit subframe leaves (the natural run
        reaches it only when the save falls just after a lit slot), with every
        column flagged lit so any subframe during the save would start the
        banks: from settings_save_pre on, nothing runs until settings_save_post."""
        kb = self.session()
        kb.host_leds(0x02)
        kb.scans(2)
        kb.matrix.press(*kb.fn)
        kb.step()
        kb.matrix.press(*S)                          # Fn+S: Mac, saved
        rr, post = kb._a("user_matrix_read_rows"), kb._a("settings_save_post")
        kb.brk(kb._a("settings_save_pre"))           # (the debug build dumps the settings first)
        kb.brk(rr)
        while kb.stopped_at(kb.run()) == rr:
            kb.matrix.inject(kb)
        kb.cmd("delete")
        flags = kb._xdata_static("led", "led_lit_col")
        kb.cmd("set mem xram 0x%x %s" % (flags, " ".join(["0x01"] * 16)))
        st = kb.as_after_a_lit_subframe()
        self.assertEqual((st["run"], st["cols"]), ([True] * 3, {0}), "the lit state was set up")
        kb.trace_on()
        kb.brk(post)
        kb.brk(rr)
        while kb.stopped_at(kb.run()) == rr:
            kb.matrix.inject(kb)
        kb.cmd("delete")
        kb.assertDark(self, "at settings_save_post")
        tr = kb.trace_off()
        stopped = [s["t"] for s in tr.states if not s["run"] & 7]
        self.assertTrue(stopped, "the banks were stopped for the save")
        again = [s["t"] for s in tr.states if s["run"] & 7 and s["t"] > stopped[0]]
        self.assertEqual(again, [], "banks started again during the save")
        self.assertFalse(any(Trace.low_cols(s) for s in tr.states if s["t"] > stopped[0] and s["run"] & 7))

    def _settings_save(self):
        kb = self.session()
        kb.host_leds(0x02)
        kb.scans(2)
        kb.matrix.press(*kb.fn)
        kb.step()
        kb.matrix.press(*S)                          # Fn+S: Mac, saved
        watch = {kb._a("flash_erase"), kb._a("flash_program_from")}
        for addr in watch:
            kb.brk(addr)
        rr = kb._a("user_matrix_read_rows")
        kb.brk(rr)
        seen = set()
        for _ in range(400):
            at = kb.stopped_at(kb.run())
            if at in watch:
                seen.add(at)
                kb.assertDark(self, "at %s" % ("flash_erase" if at == kb._a("flash_erase") else "flash_program_from"))
            else:
                kb.matrix.inject(kb)
            if len(seen) == 2 and at == rr:
                break
        kb.cmd("delete")
        self.assertEqual(seen, watch, "the save erased and programmed")
        kb.matrix.clear()
        kb.step()                                    # Fn and S released (S = Mac shows while Fn is held)
        tr = kb.lit_window()
        self.assertEqual(tr.lit_set(), expect_cell("LCtrl", "RGB"), "Caps (usjis: stock Left Ctrl) lit again after the save")

    def _lit_isp(self, fw, worst=False):
        kb = self.session(fw)
        kb.host_leds(0x02)
        kb.scans(2)
        # SET_REPORT(Feature 5) on interface 1, then the 05 75 data stage
        kb.cmd("set mem xram 0x1100 0x21 0x09 0x05 0x03 0x01 0x00 0x06 0x00")
        kb.set_sfr(0x92, 0x10)
        kb.brk(kb._a("usb_task"))
        kb.run()
        kb.cmd("delete")
        kb.cmd("set mem xram 0x1100 0x05 0x75 0x00 0x00 0x00 0x00 0x00 0x00")
        kb.set_sfr(0x93, 0x10)
        if worst:
            kb.brk(kb._a("usb_isp_prepare"))
            self.assertEqual(kb.stopped_at(kb.run()), kb._a("usb_isp_prepare"))
            kb.cmd("delete")
            kb.as_after_a_lit_subframe()
        kb.brk(ISP_ENTRY)
        at = kb.stopped_at(kb.run())
        kb.cmd("delete")
        self.assertEqual(at, ISP_ENTRY)
        return kb

    def test_report5_isp_jump(self):
        for fw in (F65_FW, F65_ANSI_FW):
            with self.subTest(fw=Path(fw).name):
                kb = self._lit_isp(fw)
                kb.assertDark(self, "at the ISP entry (report 5)")
                self.assertEqual(kb.get_sfr(0xA8) & 0x80, 0, "EA off")

    def test_report5_isp_jump_right_after_a_lit_subframe(self):
        for fw in (F65_FW, F65_ANSI_FW):
            with self.subTest(fw=Path(fw).name):
                kb = self._lit_isp(fw, worst=True)
                kb.assertDark(self, "at the ISP entry (report 5), from a lit subframe")

    def test_boot_escape_before_any_led_setup(self):
        kb = LedSim(self.FW)
        self.addCleanup(kb.close)
        kb.trace_on(2)
        kb.reset_fast()
        kb.matrix.press(*ESC)
        order = kb.run_until(kb._a("user_boot_escape"), kb._a("led_init"))
        self.assertEqual(order, kb._a("user_boot_escape"), "the escape runs before led_init")
        # the only PWM writes before it: the startup's STARTUP_LED_OFF, CON = 0 (their reset value)
        self.assertEqual([(a, v) for t, a, v in kb.trace_off().writes], [(0xFF80 + i, 0) for i in range(18)],
                         "before the escape, PWM registers only put back to their reset value")
        kb.trace_on(2)
        rr = kb._a("user_matrix_read_rows")
        kb.brk(rr)
        kb.brk(ISP_ENTRY)
        kb.brk(kb._a("usb_init"))
        while True:
            at = kb.stopped_at(kb.run())
            if at != rr:
                break
            kb.matrix.inject(kb)
        kb.cmd("delete")
        self.assertEqual(at, ISP_ENTRY)
        self.assertEqual(kb.trace_off().writes, [], "nor on the way to the bootloader")
        self.assertEqual(kb.pwm_regs(), [0] * 0x80, "PWM registers at their reset values")
        self.assertEqual((kb.get_sfr(IPH1), kb.get_sfr(IEN1) & EPWM0), (0, 0))
        st = kb.pwm_off_state()
        self.assertEqual(st["cols"], set(), "every column released")

    def test_usb_suspend(self):
        kb = self.session()
        kb.host_leds(0x02)
        kb.scans(2)
        kb.set_bit("usb", "usb_suspended", 1)
        kb.run_until(kb._a("kb_update"))             # a whole main-loop pass: kb_update sees it,
        kb.run_until(kb._a("usb_task"))              # and nothing restarts the PWM before usb_task
        kb.assertDark(self, "suspended")
        tr = kb.lit_window()
        self.assertEqual(tr.lit(), [], "dark through scans while suspended")
        self.assertFalse(any(s["run"] & 7 for s in tr.states), "the PWM stays stopped")
        kb.set_bit("usb", "usb_suspended", 0)
        tr = kb.lit_window()
        self.assertEqual(tr.lit_set(), expect_cell("LCtrl", "RGB"), "Caps (usjis: stock Left Ctrl) back after the resume")


class BouncyMatrix(LedMatrix):
    """Contacts that bounce: for `bounce` scans after a key goes down or up, the
    key reads pressed and released on alternate scans (both reads of one scan
    agree, so smk's within-scan check cannot catch it), then settles."""

    def __init__(self, bounce):
        super().__init__()
        self.bounce = bounce
        self.edges = {}        # key -> (scan it changed at, now pressed)
        self.scan = 0          # scans seen (column 0 is read twice per scan)
        self.col0_reads = 0

    def press(self, col, row):
        self.edges[(col, row)] = (self.scan + 1, True)

    def release(self, col, row):
        self.edges[(col, row)] = (self.scan + 1, False)

    def _level(self, key):
        at, down = self.edges[key]
        k = self.scan - at
        if k < 0:
            return not down
        if k < self.bounce:
            return down if k % 2 == 0 else not down   # new, old, new, old, ... then new
        return down

    def inject(self, sess):
        from test_f65 import ROW_PIN, COL_PIN
        cache = {}
        if self._col_driven_low(sess, 0, cache):
            self.col0_reads += 1
            self.scan = self.col0_reads // 2
        self.pressed = {k for k in self.edges if self._level(k)}
        super().inject(sess)


class TestChatter(LedCase):
    """Bouncing contacts give exactly one press and one release per physical
    press (MATRIX_DEBOUNCE_SCANS, stock-like per-key debounce), on every image,
    at the board's rhythm; Caps Lock, typed keys and the LED report together."""

    def _press_release(self, kb, key):
        n0 = len(kb.ep1_reports())
        kb.matrix.press(*key)
        kb.step(400)
        kb.matrix.release(*key)
        kb.step(400)
        return kb.ep1_reports()[n0:]

    def test_bouncing_keys_report_once(self):
        caps = TestCapsReport("test_caps_toggles_while_typing")
        for fw in (F65_DIAG_FW, F65_ANSI_FW, F65_FW):
            with self.subTest(fw=Path(fw).name):
                kb = self.session(fw, rhythm=True)
                kb.matrix = BouncyMatrix(bounce=7)
                caps_key = (0, 2) if fw != F65_FW else (0, 4)
                for key, code in (((1, 2), 0x04), (caps_key, 0x39), ((2, 2), 0x16), (caps_key, 0x39)):
                    reps = self._press_release(kb, key)
                    self.assertEqual(reps, [report(0, code), report()], f"key {key}: {reps}")
                    if code == 0x39:
                        caps._led_report(kb, 0x02)


class TestReportRate(LedCase):
    """At most one EP1 report per real state change, no EP2 traffic, whatever the
    LEDs and the host's Caps Lock reports do (the board's rhythm)."""

    def test_reports_follow_state_changes(self):
        caps = TestCapsReport("test_caps_toggles_while_typing")
        for fw in (F65_DIAG_FW, F65_FW):
            with self.subTest(fw=Path(fw).name):
                kb = self.session(fw, rhythm=True)

                def window(nscans):
                    n0 = len(kb.stderr_text())
                    kb.scans(nscans)
                    txt = kb.stderr_text()[n0:]
                    return (re.findall(r"\[SIE\] EP1 IN \d+ bytes:((?: [0-9a-f]{2})*)", txt),
                            re.findall(r"\[SIE\] EP2 IN", txt))

                ep1, ep2 = window(40)
                self.assertEqual((ep1, ep2), ([], []), "idle")
                kb.matrix.press(1, 2)
                ep1, ep2 = window(40)
                self.assertEqual((len(ep1), ep2), (1, []), "one key held")
                caps._led_report(kb, 0x02)
                ep1, ep2 = window(40)
                self.assertEqual((ep1, ep2), ([], []), "Caps LED on, key still held")
                n0 = len(kb.stderr_text())
                kb.matrix.clear()
                for v in (0, 2, 0):
                    caps._led_report(kb, v)
                window(40)
                txt = kb.stderr_text()[n0:]
                self.assertEqual((len(re.findall(r"\[SIE\] EP1 IN", txt)), re.findall(r"\[SIE\] EP2 IN", txt)),
                                 (1, []), "key up, Caps toggles")


class TestCapsReport(LedCase):
    """The host's Caps Lock LED report (macOS: SET_REPORT Output, ID 0, interface
    0, wLength 1) while typing, at the board's rhythm, on every image: the status
    stage is answered and keys keep going out on EP1 right after. (A hardware
    report of the keyboard stalling after Caps Lock on build-4 leddiag did not
    reproduce here; this keeps the path covered.)"""

    def _led_report(self, kb, value):
        n0 = len(kb.stderr_text())
        kb.cmd("set mem xram 0x1100 0x21 0x09 0x00 0x02 0x00 0x00 0x01 0x00")
        kb.set_sfr(0x92, 0x10)
        kb.scans(1)                                  # the matrix is served meanwhile
        kb.cmd("set mem xram 0x1100 0x%02x 0 0 0 0 0 0 0" % value)
        kb.set_sfr(0x93, 0x10)
        kb.scans(1)
        return kb.stderr_text()[n0:]

    def test_caps_toggles_while_typing(self):
        for fw in (F65_DIAG_FW, F65_ANSI_FW, F65_FW):
            with self.subTest(fw=Path(fw).name):
                kb = self.session(fw, rhythm=True)
                for i in range(6):
                    v = 0x02 if i % 2 == 0 else 0x00
                    kb.matrix.press(1, 2)                       # A down
                    self.assertEqual(kb.step()[-1:], [report(0, 0x04)])
                    log = self._led_report(kb, v)
                    self.assertRegex(log, r"\[SIE\] EP0 IN\[\d+\] 0 bytes:", "status stage answered")
                    self.assertEqual(kb.get_xram(kb._a("keyboard_state"))[0], v)
                    kb.matrix.release(1, 2)
                    self.assertEqual(kb.step()[-1:], [report()], "A up right after the LED report")
                    self.assertEqual(kb.tap((2, 2)), report(0, 0x16), "S types")
                self.assertEqual(led_holds(kb) & ~0x02, 0, "no LED hold left behind (only a scan in progress)")


def led_holds(kb):
    """led.c's hold mask (a __data static: from the linker listing)."""
    import re as _re
    rst = Path(kb.firmware).with_suffix(".ihx.p") / "led.rst"
    for line in rst.read_text().splitlines():
        m = _re.match(r"^\s+([0-9A-F]{6})\s+\d+ _led_holds::?$", line)
        if m:
            return kb.get_iram(int(m.group(1), 16), 1)[0]
    raise KeyError("led_holds")


class TestIndicators(LedCase):
    """Oracle: LED map and painter where noted; the rest mirror."""

    def _only(self, kb, expected, what, scans=None, keys=True):
        kb.scans(1 if kb.window < 10 else 4, keys)   # the main loop repaints the last change first
        tr = kb.lit_window(scans, keys)
        check_invariants(self, tr, what)
        self.assertEqual(tr.lit_set(), expected, what)
        return tr

    def test_nothing_lit_by_default(self):
        for fw in (F65_FW, F65_ANSI_FW):
            with self.subTest(fw=Path(fw).name):
                kb = self.session(fw)
                self._only(kb, set(), "no indicator")

    def test_caps_lock(self):
        """Oracle: LED map (channels) and painter (0x0400 x 3). ansi: the stock
        Caps key; usjis: the stock Left Ctrl position, where Caps Lock is."""
        for fw, key in ((F65_ANSI_FW, "CapsLock"), (F65_FW, "LCtrl")):
            for rhythm in (True,):
                with self.subTest(fw=Path(fw).name):
                    kb = self.session(fw, rhythm=rhythm)
                    kb.host_leds(0x02)
                    self._only(kb, expect_cell(key, "RGB"), "Caps Lock")
                    kb.host_leds(0x00)
                    self._only(kb, set(), "Caps Lock off")

    def test_fn_held(self):
        """Y white (link: USB) and A (Win) or S (Mac) white while Fn is held;
        usjis also Tab red (US-JIS off). Oracle for Y: painter "Fn held, USB"."""
        for fw in (F65_ANSI_FW, F65_FW):
            for mac in (False, True):
                with self.subTest(fw=Path(fw).name, mac=mac):
                    kb = self.session(fw, mac=mac)
                    kb.matrix.press(*kb.fn)
                    kb.step()
                    exp = expect_cell("Y", "RGB") | expect_cell("S" if mac else "A", "RGB")
                    if fw == F65_FW:
                        exp |= expect_cell("Tab", "R")
                    self._only(kb, exp, "Fn held")
                    kb.matrix.clear()
                    kb.step()
                    self._only(kb, set(), "Fn released")

    def test_tab_shows_us_jis(self):
        """usjis: Fn+Tab turns US-JIS on; Tab is green while Fn is held and for
        ~1 s (100 x 13 scans) after, then dark; off again: red."""
        kb = self.session(F65_FW)
        kb.matrix.press(*kb.fn)
        kb.step()
        kb.matrix.press(0, 1)
        kb.step()
        kb.matrix.clear()
        kb.step()
        self.assertEqual(kb.setting("usjis"), 1)
        self._only(kb, expect_cell("Tab", "G"), "Tab after Fn+Tab (on)")
        ticks = kb._xdata_static("indicators", "ind_tab_ticks")
        left = kb.get_xram(ticks)[0]
        self.assertTrue(90 <= left <= 100, left)
        kb.cmd("set mem xram 0x%x 0x01" % ticks)        # skip to the last tick
        kb.scans(30)
        self._only(kb, set(), "Tab after the second")
        kb.tap(kb.fn, (0, 1))
        self.assertEqual(kb.setting("usjis"), 0)
        self._only(kb, expect_cell("Tab", "R"), "Tab after Fn+Tab (off)")

    def test_charging(self):
        """Stock 0x9E26: charging needs USB power (P4.4 high, 3 samples) and P7.7
        low for 20 samples, one every 10 ms (the radio build samples on its
        1 ms tick, f65_rf.c; a scan pair takes ~0.8 ms at the board's rhythm).
        Oracle: painter "charging, USB power" (Fn red, 0x0400 on R only)."""
        for fw, where in ((F65_ANSI_FW, 9), (F65_FW, 10)):
            with self.subTest(fw=Path(fw).name):
                kb = self.session(fw, power=True)
                kb.charging(True)
                kb.scans(13 * 15, keys=False)               # ~160 ms
                self._only(kb, set(), "fewer than 20 samples")
                kb.scans(13 * 8, keys=False)                # another ~85 ms
                self._only(kb, expect_cell("Fn", "R", where=where), "charging")
                kb.usb_power(False)
                kb.scans(13 * 4, keys=False)
                self._only(kb, set(), "no USB power")

    def test_table_matches_the_stock_painter(self):
        """Oracle: painter_oracle.json. The duty table cells for the same states
        hold the stock painter's values (cells moved to the key's position in usjis)."""
        path = os.environ.get("SMK_F65_PAINTER_ORACLE")
        if path and Path(path).exists():
            got = json.loads(Path(path).read_text())
            for name, cells in PAINTER.items():
                for cell, vals in cells.items():
                    self.assertEqual(got[name][str(cell)], vals, f"painter_oracle.json {name} {cell}")
        for fw in (F65_ANSI_FW, F65_FW):
            usjis = fw == F65_FW
            with self.subTest(fw=Path(fw).name):
                kb = self.session(fw, power=True)
                kb.host_leds(0x02)
                kb.charging(True)
                kb.scans(13 * 23, keys=False)
                t = kb.table()
                self.assertEqual(t[(0, 5) if usjis else (0, 3)], PAINTER["caps lock (host LED bit1), USB"][(0, 3)])
                self.assertEqual(t[(10, 5) if usjis else (9, 5)], PAINTER["charging, USB power"][(9, 5)])
                kb.matrix.press(*kb.fn)
                kb.step()
                kb.scans(4)                          # build-6: the table follows the indicators a few passes later (led_flush)
                t = kb.table()
                for cell, vals in PAINTER["Fn held, USB"].items():
                    self.assertEqual(t[cell], vals, f"Fn held {cell}")

    def test_led_map_file(self):
        """The LED_MAP copy against led_map.json when it is available."""
        path = os.environ.get("SMK_F65_LED_MAP")
        if not path or not Path(path).exists():
            skip_or_fail("set SMK_F65_LED_MAP to the LED map JSON (led_map.json)")
        m = json.loads(Path(path).read_text())
        cells = {c["name"]: c for c in m["cells"]}
        for key, (col, row, r, g, b) in LED_MAP.items():
            c = cells[key]
            self.assertEqual((c["col"], c["stock_rowbit"], c["pwm_R"], c["pwm_G"], c["pwm_B"]), (col, row, r, g, b), key)
        self.assertEqual(m["loader_order"], LOADER)


class TestAgainstStockRun(LedCase):
    """Oracle: the stock image, run in the same model. For the same state, the
    stock's own slot interrupt lights the same (column, channel, DUTY2) as ours."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.tmp = tempfile.TemporaryDirectory()
        cls.hex = stock_hex(stock_image(), cls.tmp.name)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def stock(self, bits=(), xram=()):
        st = StockSim(self.hex)
        self.addCleanup(st.close)
        st.boot()
        return st.paint_and_run(bits, xram)

    def test_caps_lock(self):
        tr = self.stock(xram=[(0x0F42, 0x02), (0x031A, 0x00)])
        self.assertEqual(tr.lit_set(), expect_cell("CapsLock", "RGB"), "stock")
        kb = self.session(F65_ANSI_FW)
        kb.host_leds(0x02)
        self.assertEqual(kb.lit_window().lit_set(), tr.lit_set(), "ours vs stock")

    def test_charging(self):
        tr = self.stock(bits=[((0x26, 0), 1), ((0x2D, 3), 1)])
        self.assertEqual(tr.lit_set(), expect_cell("Fn", "R"), "stock")
        kb = self.session(F65_ANSI_FW, power=True)
        kb.charging(True)
        kb.scans(13 * 23, keys=False)
        self.assertEqual(kb.lit_window().lit_set(), tr.lit_set(), "ours vs stock")

    def test_fn_held_link(self):
        tr = self.stock(bits=[((0x28, 4), 0)], xram=[(0x031A, 0x00)])
        link = {s for s in tr.lit_set() if s[0] in (1, 3, 4, 5, 6)}      # Q E R T Y
        self.assertEqual(link, expect_cell("Y", "RGB"), "stock: Y white on USB")
        kb = self.session(F65_ANSI_FW)
        kb.matrix.press(*kb.fn)
        kb.step()
        ours = {s for s in kb.lit_window().lit_set() if s[0] in (3, 4, 5, 6) or (s[0] == 1 and s[1] in ("PWM11", "PWM10", "PWM12"))}
        self.assertEqual(ours, link, "ours vs stock (link cells)")

    def test_pwm4_timebase_is_1ms(self):
        """The model's PWM4 (0xAEAD: CON 0xC3 = clock Fsys/8, period 0x0BB8) against
        the stock's own millisecond counter (ISR 0x7FBB, 16 bits at 0x0303): one
        count per 24000 cycles, i.e. 1 ms at 24 MHz."""
        st = StockSim(self.hex)
        self.addCleanup(st.close)
        st.boot()
        st.paint_and_run(periods=2)
        st.cmd("set mem sfr 0xa9 0x22")                      # PWM0 + PWM4
        st.cmd("set mem xram 0x%x 0x01" % TRACE)

        def sample(v):
            st.cmd("set mem xram 0x%x 0x%02x" % (MARK, v))
            hi, lo = st.get_xram(0x0303, 2)
            return hi << 8 | lo

        c0 = sample(1)
        st.cmd("step 150000")
        c1 = sample(2)
        wait_for_sentinel(st, 0)
        st.cmd("set mem xram 0x%x 0x00" % TRACE)
        with st._serr_lock:
            marks = [(int(m.group(1)), int(m.group(2), 16)) for m in map(RE_M.search, st.serr) if m]
        t = {v: tt for tt, v in marks}
        ms = (t[2] - t[1]) / 24000
        self.assertGreater(ms, 5)
        self.assertLessEqual(abs((c1 - c0) - ms), 1.0, f"{c1 - c0} counts in {ms:.2f} ms")

    def test_stock_trace_keeps_our_invariants_bar_the_column_choice(self):
        """The same checks on the stock's own trace: its DUTY limits, P1-P3 and slot
        spacing hold. (It selects every column in turn, lit or not, and P4.6/P7.4.)"""
        tr = self.stock(xram=[(0x0F42, 0x02), (0x031A, 0x00)])
        for p in tr.periods:
            for ch in range(6):
                self.assertTrue(p["d1"][ch] <= p["d2"][ch] <= DUTY_ON)
        cols = [sorted(Trace.low_cols(s)) for s in tr.states if s["run"] & 7]
        self.assertTrue(all(len(c) <= 1 for c in cols))
        self.assertIn([16], cols, "the stock drives P4.6 (slot 16)")


class TestGuards(LedCase):
    """Mirror. The clamp and the audit, driven directly."""

    STUB = 0xE000

    def _set_duty(self, kb, col, row, c, duty):
        """Cold-call led_set_duty(col, row, c, duty) from a ROM stub. SDCC
        --stack-auto: the first argument in DPL, the others pushed last to first,
        low byte first (see a call site in indicators.asm)."""
        f = kb._a("led_set_duty")
        code = [0x74, duty & 0xFF, 0xC0, 0xE0, 0x74, duty >> 8, 0xC0, 0xE0, 0x74, c, 0xC0, 0xE0,
                0x74, row, 0xC0, 0xE0, 0x75, 0x82, col, 0x12, f >> 8, f & 0xFF,
                0xD0, 0xE0, 0xD0, 0xE0, 0xD0, 0xE0, 0xD0, 0xE0, 0x80, 0xFE]
        loop = kb._a("kb_update_switches")
        kb.brk(loop)
        kb.run()
        kb.cmd("delete")
        kb.cmd("set mem rom 0x%x " % self.STUB + " ".join("0x%02x" % b for b in code))
        kb.cmd("pc 0x%x" % self.STUB)
        kb.brk(self.STUB + len(code) - 2)
        kb.run()
        kb.cmd("delete")
        kb.cmd("pc 0x%x" % loop)                   # SP is back where the main loop left it

    def test_clamp(self):
        kb = self.session()
        for duty, want in ((0x0500, DUTY_ON), (0xFFFF, DUTY_ON), (0x0010, STOCK_PHASE["PWM24"]), (0, STOCK_PHASE["PWM24"])):
            with self.subTest(duty=hex(duty)):
                self._set_duty(kb, 0, 1, 0, duty)          # Esc red = PWM24
                self.assertEqual(kb.table()[(0, 1)][0], want)
        self._set_duty(kb, 0, 1, 0, 0x0500)
        tr = kb.lit_window(keys=False)
        check_invariants(self, tr, "after an over-range request")
        self.assertEqual(tr.lit_set(), {(0, "PWM24", DUTY_ON)})

    def test_audit_repairs_a_corrupt_table(self):
        kb = self.session()
        base = kb._xdata_static("led", "led_fb")
        kb.cmd("set mem xram 0x%x 0x07 0xff" % (base + 3 * 36 + 2 * 2))      # col 3, j 2 = 0x07ff
        kb.cmd("set mem xram 0x%x 0x00 0x10" % (base + 5 * 36 + 9 * 2))      # col 5, j 9 = 0x0010
        kb.scans(13 * 18, keys=False)                                    # one row per ~10 ms: all 17 rows
        t = kb.table()
        self.assertEqual(t[(3, 0)][2], STOCK_PHASE["PWM22"], "0x07ff -> off")
        self.assertEqual(t[(5, 3)][0], STOCK_PHASE["PWM14"], "0x0010 -> off")
        tr = kb.lit_window(keys=False)
        check_invariants(self, tr, "after the audit")
        self.assertEqual(tr.lit(), [])


class TestDiag(LedCase):
    """The leddiag image. Oracle: LED map (the channel each colour lights)."""

    FW = F65_DIAG_FW
    CELLS = [("Esc", "esc"), ("Tab", "tab"), ("CapsLock", "caps"), ("LShift", "lshift"), ("LCtrl", "lctrl"),
             ("Y", "y"), ("Enter", "enter"), ("Up", "up"), ("Fn", "fn"), ("Right", "right"),
             ("side_0_B", "side b0"), ("side_0_A", "side a0")]
    RIGHT, LEFT, UP, DOWN, PGDN = (15, 4), (13, 4), (14, 3), (14, 4), (15, 2)

    def _fn(self, kb, key):
        kb.matrix.press(*kb.fn)
        kb.step()
        kb.matrix.press(*key)
        reps = kb.step()
        kb.matrix.clear()
        reps += kb.step()
        return reps

    @staticmethod
    def _typed(reps):
        keys = {0x2C: " ", 0x37: ".", 0x27: "0"}
        keys.update({0x04 + i: chr(ord("a") + i) for i in range(26)})
        keys.update({0x1E + i: str(i + 1) for i in range(9)})
        out = []
        for r in reps:
            if r[2]:
                out.append(keys.get(r[2], "?"))
        return "".join(out)

    def test_every_cell_and_colour(self):
        """At the board's scan rhythm, as the hardware check will see it."""
        kb = self.session(rhythm=True)
        self.assertEqual(kb.lit_window().lit_set(), set(), "nothing lit before the first key")
        for n, (key, name) in enumerate(self.CELLS):
            for k, colour in enumerate("RGB"):
                with self.subTest(cell=key, colour=colour):
                    self._fn(kb, self.RIGHT if k == 0 else self.UP)
                    col, row, *chans = LED_MAP[key]
                    ch = chans[k]
                    exp = {(col, ch, STOCK_PHASE[ch] + 128)}
                    tr = kb.lit_window()                     # a full rotation at this rhythm
                    check_invariants(self, tr, f"diag {key} {colour}")
                    self.assertEqual(tr.lit_set(), exp)
                    pin = "p%d.%s" % (3 - int(ch[3]), ch[4])
                    reps = self._fn(kb, self.DOWN)
                    for _ in range(60):                  # (the debug image types slower: its console runs too)
                        if self._typed(reps).rstrip().endswith(("dim", "full")):
                            break
                        reps += kb.step(16)
                    colour_name = {"R": "red", "G": "green", "B": "blue"}[colour]
                    self.assertEqual(self._typed(reps).strip(), f"{name} {colour_name} {ch.lower()} {pin} dim")
            self._fn(kb, self.UP)                  # back to red for the next cell

    def test_level_key(self):
        """Fn+PgDn: dim (phase + 128) <-> the indicator level (0x0400), same channel."""
        kb = self.session()
        self._fn(kb, self.RIGHT)                                   # Esc red = PWM24
        self.assertEqual(kb.lit_window().lit_set(), {(0, "PWM24", STOCK_PHASE["PWM24"] + 128)})
        self._fn(kb, self.PGDN)
        tr = kb.lit_window()
        check_invariants(self, tr, "diag full")
        self.assertEqual(tr.lit_set(), {(0, "PWM24", DUTY_ON)})
        reps = self._fn(kb, self.DOWN)
        for _ in range(30):
            if self._typed(reps).endswith("full "):
                break
            reps += kb.step(16)
        self.assertEqual(self._typed(reps).strip(), "esc red pwm24 p1.4 full")
        self._fn(kb, self.PGDN)
        self.assertEqual(kb.lit_window().lit_set(), {(0, "PWM24", STOCK_PHASE["PWM24"] + 128)})

    def test_keys_and_isp_still_work(self):
        kb = self.session()
        self.assertEqual(kb.tap(ESC), report(0, 0x29))
        self.assertEqual(kb.tap(kb.fn, (1, 0)), report(0, 0x3A))
        kb.host_leds(0x02)
        self.assertEqual(kb.lit_window().lit_set(), set(), "no Caps indicator in the diagnostic")
        kb.close()
        kb = TestDarkStates._lit_isp(self, self.FW)
        kb.assertDark(self, "diag: at the ISP entry (report 5)")


if __name__ == "__main__":
    unittest.main()
