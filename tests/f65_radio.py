"""Test-side model of the F65's radio module and of its wiring to the SH68F90.

The simulator (tools/ucsim/sh68f90.cc) models the MCU's EUART0 with byte timing
and logs, on stderr:

    [RF] TX <cycle> <byte>          a byte the firmware starts sending
    [RF] TXCOL <cycle> <byte>       an SBUF write while a byte was going out
    [RF] RX <cycle> <byte> [lost|fe] a byte delivered to the firmware
    [RF] P0.2 <cycle> <level>       the level on the radio's send-request line

Everything outside the MCU is modelled here: the module that answers on the
UART, the ready line P4.7 it drives, the three-position connection switch on
P0.4 / P0.5 and the USB-power sense on P4.4. Staging cells (xram, outside the
firmware's window) carry the levels into the simulator:

    0x1f10  P0 external level     0x1f14  P4 external level
    0x1f13  pin model enable (bit0 = P0, bit1 = key contacts, bit4 = P4,
            bit7 = PWM0 / Timer2 timed from their registers)
    0x1f20  queue an RX byte      0x1f21  queue an RX byte with a framing error
    0x1f22/0x1f23  byte time in machine cycles (lo/hi)    0x1f24  RX bytes pending
    0x1f25  a key held while asleep: raises INT4 (IF40) in power-down
    0x1f28  the longest gap between two watchdog kicks, in 1000 cycles (read); write clears it
    0x1f2f  the model's version (6: everything above, the USB interrupt on the flags a test
            raises, the D+ pull-up logged as "[SIE] D+ pull-up on|off <cycle>")
    0x1f30-0x1f4f  key contacts: 16 pin-id pairs (port * 8 + bit, 0xff = none)
"""

import re

P0_STAGE, P4_STAGE, PIN_MODEL, CONTACTS = 0x1F10, 0x1F14, 0x1F13, 0x1F30
MODEL_P0, MODEL_CONTACTS, MODEL_P4, MODEL_REG_TIMING = 0x01, 0x02, 0x10, 0x80
WAKE_KEY, MODEL_VER = 0x1F25, 0x1F2F
RX_PUSH, RX_PUSH_FE, BYTE_LO, BYTE_HI, RX_COUNT = 0x1F20, 0x1F21, 0x1F22, 0x1F23, 0x1F24

# Connection switch (P0.5 low = 2.4 GHz, P0.4 low = Bluetooth, both high = USB).
SWITCH_USB, SWITCH_24G, SWITCH_BT = "usb", "24g", "bt"

_LINE = re.compile(r"^\[RF\] (TX|TXCOL|RX|P0\.2) (\d+) ([0-9a-f]+)(?: (\w+))?$")


def checksum(data):
    """The module's and the firmware's frame sum: 0x55 minus the byte sum."""
    return (0x55 - sum(data)) & 0xFF


def frame(cmd, *payload, length):
    """A frame the keyboard sends: 01 <cmd> <payload, zero padded> <sum>."""
    body = [0x01, cmd] + list(payload)
    body += [0] * (length - 1 - len(body))
    return body + [checksum(body)]


class RadioLog:
    """The parsed [RF] events of one simulator session. `starts`: event
    counts at which the MCU was reset (a power cycle: no gap to check across
    it)."""

    def __init__(self, text, starts=()):
        self.starts = set(starts)
        self.events = []
        for line in text.splitlines():
            m = _LINE.match(line.strip())
            if m:
                kind, t, val, extra = m.groups()
                self.events.append((kind, int(t), int(val, 16), extra))

    def tx_frames(self):
        """Bytes sent, grouped by the P0.2 envelopes the firmware draws around
        each frame (P0.2 low before the first byte, released after the last).
        Bytes sent outside any envelope come back as frames of their own,
        flagged with envelope=False. Each envelope also carries its timing in
        machine cycles: "lead" (P0.2 low to the first byte, None without
        bytes) and "gap" (P0.2 high since the previous envelope, None for the
        first)."""
        frames, cur, inside, start, lead, last_end = [], None, False, None, None, None
        for i, (kind, t, val, _) in enumerate(self.events):
            if i in self.starts:
                last_end = None
            if kind == "P0.2":
                if val == 0 and not inside:
                    inside, cur, start, lead = True, [], t, None
                elif val == 1 and inside:
                    inside = False
                    frames.append({"bytes": cur, "t": start, "envelope": True, "lead": lead,
                                   "gap": None if last_end is None else start - last_end})
                    last_end, cur = t, None
            elif kind == "TX":
                if inside:
                    if lead is None:
                        lead = t - start
                    cur.append(val)
                else:
                    frames.append({"bytes": [val], "t": t, "envelope": False, "lead": None, "gap": None})
        if inside and cur:
            frames.append({"bytes": cur, "t": start, "envelope": True, "open": True, "lead": lead,
                           "gap": None if last_end is None else start - last_end})
        return frames

    def timing_faults(self, min_lead, min_gap):
        """Envelopes whose lead or gap (cycles) is below the minimum, and
        bytes sent without P0.2 low."""
        out = []
        for f in self.tx_frames():
            if not f["envelope"]:
                out.append(("no envelope", f["bytes"]))
            elif f["lead"] is not None and f["lead"] < min_lead:
                out.append(("lead %d" % f["lead"], f["bytes"][:3]))
            elif f["lead"] is not None and f["gap"] is not None and f["gap"] < min_gap:
                # (the wake pulse, an envelope without bytes, is not a frame;
                # the frame after it is checked against it)
                out.append(("gap %d" % f["gap"], f["bytes"][:3]))
        return out

    def rx(self):
        return [(t, v, extra) for kind, t, v, extra in self.events if kind == "RX"]

    def p02(self):
        return [(t, v) for kind, t, v, _ in self.events if kind == "P0.2"]


class RadioBoard:
    """The board around the MCU for one simulator session (a UcsimSession or a
    subclass): the switch, USB power, the module's ready line and its UART."""

    MODEL_VERSION = 6   # tools/ucsim/sh68f90.cc: key contacts, INT4 wake, USB flags, kick gap, pull-up log

    def __init__(self, sess, model=MODEL_P0 | MODEL_P4):
        self.sess = sess
        have = sess.get_xram(MODEL_VER)[0]
        if have < self.MODEL_VERSION:
            from sim import skip_or_fail
            skip_or_fail("the simulator's SH68F90 model is version %d, these tests need %d: "
                         "build it from this tree's tools/ucsim (nix build .#ucsim-sh68f90)"
                         % (have, self.MODEL_VERSION))
        self.p0 = 0xFF
        self.p4 = 0xFF          # P4.4 high = USB power present, P4.7 high = module idle
        self.p7 = 0xFF          # P7.7 low = charging
        self.starts = []
        self.model = model
        self.set_model(model)
        self.wake_key(False)    # xram is not cleared at start: no key held while asleep
        self._push()

    def set_model(self, bits):
        self.model = bits
        self.sess.cmd("set mem xram 0x%x 0x%02x" % (PIN_MODEL, bits))

    def _push(self):
        self.sess.cmd("set mem xram 0x%x 0x%02x" % (P0_STAGE, self.p0))
        self.sess.cmd("set mem xram 0x%x 0x%02x" % (P4_STAGE, self.p4))

    def kick_gap_ms(self):
        """The longest stretch between two watchdog kicks since the last
        clear_kick_gap (power-down not counted), in ms."""
        lo, hi = self.sess.get_xram(0x1F28, 2)
        return (lo | hi << 8) * 1000 / 24000.0

    def clear_kick_gap(self):
        self.sess.cmd("set mem xram 0x1f28 0x00")

    def pullup_log(self):
        """The D+ pull-up changes the simulator logged: [(cycle, on)]."""
        import re
        return [(int(t), v == "on") for v, t in re.findall(r"\[SIE\] D\+ pull-up (on|off) (\d+)", self.sess.stderr_text())]

    def charging(self, on):
        """P7.7: low while the charger charges the cell."""
        from devices import P7
        self.p7 = (self.p7 & ~0x80) if on else (self.p7 | 0x80)
        self.sess.set_pin(P7, self.p7)

    def wake_key(self, held):
        """A key held while the MCU is in power-down raises INT4 (the model
        checks this on every power-down cycle)."""
        self.sess.cmd("set mem xram 0x%x 0x%02x" % (WAKE_KEY, 1 if held else 0))

    def switch(self, pos):
        self.p0 |= 0x30
        if pos == SWITCH_24G:
            self.p0 &= ~0x20
        elif pos == SWITCH_BT:
            self.p0 &= ~0x10
        self._push()

    def usb_power(self, on):
        self.p4 = (self.p4 | 0x10) if on else (self.p4 & ~0x10)
        self._push()

    def ready(self, high):
        """P4.7: high = the module is idle; low while it sends to the MCU."""
        self.p4 = (self.p4 | 0x80) if high else (self.p4 & ~0x80)
        self._push()

    def queue_rx(self, data, fe_at=()):
        for i, b in enumerate(data):
            self.sess.cmd("set mem xram 0x%x 0x%02x" % (RX_PUSH_FE if i in fe_at else RX_PUSH, b))

    def rx_pending(self):
        return self.sess.get_xram(RX_COUNT)[0]

    def byte_cycles(self, n):
        self.sess.cmd("set mem xram 0x%x 0x%02x" % (BYTE_LO, n & 0xFF))
        self.sess.cmd("set mem xram 0x%x 0x%02x" % (BYTE_HI, (n >> 8) & 0xFF))

    def mark_start(self):
        """The MCU is about to be reset (a power cycle)."""
        self.starts.append(len(self.log().events))

    def log(self):
        """The [RF] events so far. The simulator's stderr is read by a thread;
        wait until it has caught up with what the stopped simulator wrote."""
        import time
        text = self.sess.stderr_text()
        for _ in range(100):
            time.sleep(0.005)
            again = self.sess.stderr_text()
            if len(again) == len(text):
                break
            text = again
        return RadioLog(text, self.starts)


def status_frame(slot, state, battery_raw=0x03DD, leds=0x00, b8=0x01):
    """The module's status reply to 0x06: 02 06 00 <host LEDs> <slot> <state>
    <battery lo> <battery hi> <b8> <0x55 - sum of bytes 0..8> (stock parser
    0x0637-0x067B). state: 1 pairing, 2 reconnecting, 3 connected, 0 idle."""
    body = [0x02, 0x06, 0x00, leds, slot, state, battery_raw & 0xFF, battery_raw >> 8, b8]
    return body + [checksum(body)]


def announce_frame():
    """Type 03 from the module (no checksum is checked by the stock)."""
    return [0x03, 0x00, 0x00, 0x00, 0x00, 0x52]


def container_frame(sub, data=()):
    """A 22-byte type-08 frame: 08 <len> <sub> <cnt> <idx> <lb> d[6..20] <sum>."""
    body = [0x08, 0x13, sub, 0x01, 0x00, 0x00] + list(data)
    body = (body + [0] * 21)[:21]
    return body + [checksum(body)]


# Port SFR -> port number (pin id = port * 8 + bit).
PORT_NO = {0x80: 0, 0x90: 1, 0x98: 2, 0xA0: 3, 0xB0: 4, 0x88: 5, 0xC0: 6, 0xF8: 7}


class ContactMatrix:
    """Held keys as closed contacts inside the simulator (pin model bit 1): the
    firmware's column drive reaches the row pin with no test-side stop at the
    row reads, so keys can be held for seconds of simulated time. Takes the
    board's column and row pin maps ({index: (port SFR, bit)})."""

    in_sim = True

    def __init__(self, sess, board, col_pin, row_pin):
        self.sess, self.board = sess, board
        self.col_pin, self.row_pin = col_pin, row_pin
        self.pressed = set()
        board.set_model(board.model | MODEL_CONTACTS)
        self._push()

    def _id(self, pin):
        sfr, bit = pin
        return PORT_NO[sfr] * 8 + bit

    def _push(self):
        pairs = [(self._id(self.col_pin[c]), self._id(self.row_pin[r])) for (c, r) in sorted(self.pressed)]
        if len(pairs) > 16:
            raise ValueError("at most 16 keys at a time")
        pairs += [(0xFF, 0xFF)] * (16 - len(pairs))
        flat = [b for p in pairs for b in p]
        self.sess.cmd("set mem xram 0x%x %s" % (CONTACTS, " ".join("0x%02x" % b for b in flat)))

    def press(self, col, row):
        self.pressed.add((col, row))
        self._push()

    def release(self, col, row):
        self.pressed.discard((col, row))
        self._push()

    def clear(self):
        self.pressed.clear()
        self._push()

    def inject(self, sess):
        pass


class Timeline:
    """Runs a session in 1 ms steps (breakpoints on the firmware's 1 ms tick
    ISR), serving the key matrix at every row read. Works for smk (the PWM4 tick
    handler and user_matrix_read_rows) and for the stock image (0x7FBB and the
    row read at 0x6B81), so the same script can drive both."""

    CHUNK = 200

    def __init__(self, sess, ms_isr, row_read, matrix):
        self.sess, self.ms_isr, self.row_read, self.matrix = sess, ms_isr, row_read, matrix

    def run_ms(self, n, until=None):
        """Advance n ms of simulated time; `until(sess)` may stop it early
        (checked once per ms). Returns the ms actually run. While no key is
        held the row lines stay high, so the row reads are not stopped at
        (one stop per column read is what makes a held key slow to run)."""
        s = self.sess
        if not self.matrix.pressed:
            self.matrix.inject(s)       # all rows high until a key goes down
        rows = bool(self.matrix.pressed) and not getattr(self.matrix, "in_sim", False)
        if not rows and until is None and n > 1:
            # Nothing to serve on the way: one stop after every CHUNK ticks
            # (a stop per chunk keeps each command well inside the socket
            # timeout on a loaded machine).
            left = n
            while left:
                k = min(left, self.CHUNK)
                if k > 1:
                    s.cmd("break 0x%x %d" % (self.ms_isr, k))
                else:
                    s.brk(self.ms_isr)
                try:
                    at = s.stopped_at(s.run())
                finally:
                    s.cmd("delete")
                if at != self.ms_isr:
                    raise RuntimeError("stopped at %r" % at)
                left -= k
            return n
        s.brk(self.ms_isr)
        if rows:
            s.brk(self.row_read)
        done = 0
        try:
            while done < n:
                at = s.stopped_at(s.run())
                if at == self.row_read:
                    self.matrix.inject(s)
                elif at == self.ms_isr:
                    done += 1
                    if until and until(s):
                        break
                else:
                    raise RuntimeError("stopped at %r" % at)
        finally:
            s.cmd("delete")
        return done


# P4.7 goes high this many instructions after the last byte is in: the edge
# falls anywhere between two 1 ms ticks, not on one (cycled, so a run is
# repeatable).
EDGE_STEPS = (40, 900, 2300, 4700, 7100, 9900, 300, 6000)
_edge = [0]


def module_send(board, timeline, data, fe_at=(), gap_ms=2, edge_steps=None):
    """The module sends `data`: P4.7 low, the bytes, P4.7 high somewhere
    between two ticks, then `gap_ms` for the firmware to frame and parse it."""
    board.ready(False)
    board.queue_rx(data, fe_at)
    timeline.run_ms(50, until=lambda s: board.rx_pending() == 0)
    if edge_steps is None:
        edge_steps = EDGE_STEPS[_edge[0] % len(EDGE_STEPS)]
        _edge[0] += 1
    if edge_steps:
        board.sess.cmd("step %d" % edge_steps)
    board.ready(True)
    timeline.run_ms(gap_ms)


def hexs(frame):
    return " ".join("%02x" % b for b in frame)
