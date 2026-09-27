#!/usr/bin/env python3
"""aula-f65-v1 radio tests: Bluetooth (3 slots) and 2.4 GHz through the module
on EUART0, driven through the patched uCsim with the board model of
f65_radio.py (switch, USB power, the module's ready line P4.7 and its UART)
and keys as contacts inside the simulator (f65_devices.py).

The numbered cases follow the simulator plan of the radio comparison
(the author's radio comparison notes, not published):
 1 boot wired, names          2 switch entries, bounce     3 status supervisor
 4 receive robustness         5 EUART0 priority            6 module busy (P4.7 low)
 7 acks F0/F1, container 06   8 battery                    9 report layouts
10 release repeat, pacing    11 pairing keys              12 into wired
13 sleep and wake            14 stuck send                 15 module absent
plus the review items (1 USB off after a wireless wake, 3 pending pairing
kept, 4 hold-off after a link-select, 5 battery display starts at the
target, 7 host LEDs from the status frame, 11 wired position without a host
sleeps) and the recovery paths in the wireless positions.

TestAgainstStock runs the same scenarios on the official V1 image
(SMK_F65_STOCK_IMAGE) in the same simulator and compares the frames: the
stock image is the oracle there. Elsewhere the expected bytes come from the
stock analysis (logs/2026-09-26-stock-radio-spec.md).

    python3 -m unittest discover -s tests -p test_f65_radio.py
"""

import os
import re
import unittest

import f65_radio

from f65_radio import (frame, status_frame, announce_frame, container_frame, hexs,
                       SWITCH_USB, SWITCH_24G, SWITCH_BT)
from f65_devices import (OursF65, StockF65, stock_image, stock_hex, control, reports,
                         IEN1, IPH1, IPL1, SCON, P0CR, USBCON, ES0, STOCK_IDLE_S)
from test_f65 import F65_FW, F65_ANSI_FW, _need, bit_addr, ISP_ENTRY
from devices import SOCKET_TIMEOUT

# Keys (col, row) of the stock matrix; Fn as in the ansi layout.
ESC, A, B, C, D, E_, F, S = (0, 0), (1, 2), (5, 3), (3, 3), (3, 2), (3, 1), (4, 2), (2, 2)
# The link keys are per rig (OursF65 / StockF65: BT1-3, P24 = 2.4 GHz, FREE).
FN, RSFT, LSFT = (9, 4), (13, 3), (0, 3)
NUM = {n: (n, 0) for n in range(1, 13)}          # 1 .. 0 - =

CYCLES_PER_MS = 24000                            # Fsys 24 MHz, 1T core


def sel(flag, slot):
    return frame(0x01, flag, slot, 0, length=6)


STATUS_REQ = frame(0x06, length=6)
ACK_F0, ACK_F1 = frame(0xF0, length=6), frame(0xF1, length=6)
TO_WIRED = [frame(0x0E, length=6), frame(0x0B, 0, length=6)]
SLEEP_UNLINKED = [frame(0x0B, 1, length=6)]
SLEEP_LINKED = [frame(0x0C, 8, 7, length=6), frame(0x0B, 0, length=6)]


def ctl(frames):
    """Control frames other than the periodic status request and the battery
    percent (both timing-driven)."""
    return [f for f in control(frames) if f != STATUS_REQ and f[1] != 0x0D]


def batt(pct):
    return frame(0x0D, pct, length=6)


def name(profile, text):
    return frame(0x09, profile, 0x0F, *text.encode(), length=32)


NAMES = [name(0, "AULA F65 BT5.0 "), name(1, "AULA F65 BT3.0 ")]


def long_frame(mods=0, *keys, bitmap=()):
    body = [0x01, 0x02, mods] + (list(keys) + [0] * 5)[:5] + [0] + [0] * 16 + [0] * 4
    for u in bitmap:
        body[9 + (u >> 3)] |= 1 << (u & 7)
    return frame(*body[1:], length=30)


def short_frame(cons=0, sys=0):
    return frame(0x03, cons & 0xFF, cons >> 8, sys, length=13)


LONG_EMPTY, LONG_ERO = long_frame(), long_frame(0, 0x01)
SHORT_EMPTY = short_frame()


def ours(fw=None):
    fw = fw or F65_ANSI_FW
    _need(fw)
    return OursF65(fw)


# The send request line: P0.2 low at least this long before the first start
# bit (the stock's shortest in the simulator is 3.4 us for an ack, 5 us for a
# control frame), and high at least this long between two frames.
MIN_LEAD = 5 * CYCLES_PER_MS // 1000
MIN_GAP = 1000 * CYCLES_PER_MS // 1000


class RadioCase(unittest.TestCase):
    """Every scenario: no 0x04 frame (test mode) ever, no frame longer than 32
    bytes, every frame's sum right, no watchdog reset; for smk also P0.2's
    lead and gap (MIN_LEAD, MIN_GAP) on every frame."""

    FW = None   # the image dev() starts: F65_ANSI_FW unless a class says otherwise

    def setUp(self):
        # The module's P4.7 edges cycle through EDGE_STEPS; start every test at
        # the same place, so a scenario (and the stock oracle's occasional lost
        # status frame) does not depend on which tests ran before it.
        f65_radio._edge[0] = 0

    def dev(self, fw=None, position=SWITCH_USB, **kw):
        d = ours(fw or self.FW)
        self.addCleanup(self._check_and_close, d)
        d.start(position, **kw)
        return d

    def stock(self, position=SWITCH_USB):
        d = StockF65(self._stock_hex())
        self.addCleanup(self._check_and_close, d)
        d.start(position)
        return d

    _hex = None

    @classmethod
    def _stock_hex(cls):
        if RadioCase._hex is None:
            RadioCase._hex = stock_hex(stock_image())
        return RadioCase._hex

    def _check_and_close(self, d):
        try:
            for fr in d.radio.log().tx_frames():
                f = fr["bytes"]
                if len(f) < 2 or fr.get("open"):
                    continue                # a send cut short (the stuck-send test), or still going out at the end
                self.assertNotEqual(f[1], 0x04, "0x04 (test mode) must never be sent")
                self.assertLessEqual(len(f), 32, hexs(f))
                if len(f) >= 6:
                    self.assertEqual(f[-1], (0x55 - sum(f[:-1])) & 0xFF, "frame sum: " + hexs(f))
            self.assertNotIn("WATCHDOG", d.stderr_text())
            if isinstance(d, OursF65) and not os.environ.get("SMK_F65_SKIP_LINE_TIMING"):
                # (SMK_F65_SKIP_LINE_TIMING: to see what else a test checks
                # on an image that predates the P0.2 timing)
                faults = d.radio.log().timing_faults(MIN_LEAD, MIN_GAP)
                self.assertEqual(faults, [], "P0.2 lead / gap")
        finally:
            d.close()

    # --- helpers --------------------------------------------------------
    def into(self, d, pos, status_slot=None, state=3):
        """Switch to `pos`; with status_slot the module then reports that
        slot in `state`."""
        d.switch(pos, 200)
        if status_slot is not None:
            d.status(status_slot, state)
            d.ms(20)

    def saved(self, d, pos):
        """Switch to `pos`, let the settings save, and power-cycle there."""
        d.switch(pos, 200)
        d.ms(50)
        d.reboot(pos)

    def poke_quiet(self, d, ms):
        """The wired sleep's counter: ms since the last USB interrupt or key."""
        a = d._xdata_static("f65_rf", "usb_quiet_ms")
        d.cmd("set mem xram 0x%x 0x%02x 0x%02x" % (a, ms & 0xFF, ms >> 8))

    def poke_idle(self, d, seconds):
        a = d._xdata_static("f65_rf", "idle_s")
        d.cmd("set mem xram 0x%x 0x%02x 0x%02x" % (a, seconds & 0xFF, seconds >> 8))
        d.cmd("set mem xram 0x%x 0x00 0x00" % d._xdata_static("f65_rf", "idle_ms"))

    def cycles_ms(self, t0, t1):
        return (t1 - t0) / CYCLES_PER_MS

    def envelopes(self, d):
        return d.radio.log().tx_frames()


# ---------------------------------------------------------------- 1, 2, 12
class TestTransport(RadioCase):

    def test_boot_wired_sends_the_names_and_leaves_euart0_off(self):
        d = self.dev()
        d.ms(20)
        self.assertEqual(d.frames(), NAMES)
        self.assertFalse(d.es0(), "ES0 off in the wired position")
        self.assertTrue(d.usb_enabled())
        self.assertEqual(d.transport(), 0)

    def test_switch_to_24g_then_bt(self):
        d = self.dev()
        m = d.mark()
        d.switch(SWITCH_24G, 200)
        self.assertEqual(ctl(d.frames(m)), [sel(0, 0)])
        self.assertTrue(d.es0())
        self.assertFalse(d.usb_enabled(), "USB off in a wireless position")
        m = d.mark()
        d.switch(SWITCH_BT, 200)
        self.assertEqual(ctl(d.frames(m)), [sel(0, 1)])
        self.assertEqual((d.transport(), d.bt_slot()), (2, 1))

    def test_entries_release_everything_first(self):
        d = self.dev()
        m = d.mark()
        d.switch(SWITCH_BT, 200)
        got = d.frames(m)
        self.assertEqual(reports(got), [LONG_EMPTY, SHORT_EMPTY])
        self.assertLess(got.index(LONG_EMPTY), got.index(sel(0, 1)))

    def test_a_bounce_of_nine_samples_does_not_switch(self):
        d = self.dev()
        m = d.mark()
        for pos in (SWITCH_BT, SWITCH_24G):
            d.radio.switch(pos)
            d.ms(85)                      # 8 slow ticks
            d.radio.switch(SWITCH_USB)
            d.ms(200)
        self.assertEqual(d.frames(m), [])
        self.assertEqual(d.transport(), 0)
        self.assertTrue(d.usb_enabled())
        # and back out of a wireless position: a bounce through the middle
        self.into(d, SWITCH_BT, 1)
        m = d.mark()
        d.radio.switch(SWITCH_USB)
        d.ms(85)
        d.radio.switch(SWITCH_BT)
        d.ms(200)
        self.assertEqual(ctl(d.frames(m)), [])
        self.assertEqual(d.transport(), 2)
        self.assertFalse(d.usb_enabled())

    def test_middle_position_brings_usb_back(self):
        d = self.dev()
        self.into(d, SWITCH_BT, 1)
        m = d.mark()
        d.switch(SWITCH_USB, 200)
        self.assertEqual(ctl(d.frames(m)), TO_WIRED)
        self.assertEqual(d.transport(), 0)
        self.assertTrue(d.usb_enabled())
        self.assertFalse(d.es0())
        self.assertEqual(d.get_xram(d._a("usb_device_state"))[0], 0, "USB starts over (usb_init)")

    def test_saved_transport_and_slot(self):
        d = self.dev()
        self.into(d, SWITCH_BT, 1)
        d.press(d.fn, ms=20)
        d.tap(d.BT2)
        d.release(d.fn, ms=50)
        rec = d.record()
        link = rec[d.off["rf_link"]]
        self.assertEqual(link, 0x22, "BT (2) in bits 5:4, slot 2")
        d.reboot(SWITCH_BT)
        self.assertEqual((d.transport(), d.bt_slot()), (2, 2))
        self.assertFalse(d.usb_enabled(), "USB never comes up at a wireless boot")

    def test_saved_wireless_with_the_switch_at_usb_tells_the_module_once(self):
        d = self.dev()
        self.saved(d, SWITCH_BT)
        m = d.mark()
        d.reboot(SWITCH_USB)
        self.assertFalse(d.usb_enabled(), "before the debounce the saved transport holds")
        d.ms(600)
        got = d.frames(m)
        self.assertEqual(got[:2], NAMES)
        self.assertEqual(ctl(got[2:]), TO_WIRED)
        self.assertTrue(d.usb_enabled())
        m = d.mark()
        d.reboot(SWITCH_USB)
        d.ms(300)
        self.assertEqual(d.frames(m), NAMES, "saved wired now: nothing more")


# ---------------------------------------------------------------- 3, review 3/4/7
class TestSupervisor(RadioCase):

    def test_matching_status_is_left_alone(self):
        d = self.dev()
        self.into(d, SWITCH_BT)
        d.ms(700)
        m = d.mark()
        d.status(1, 3)
        d.ms(50)
        self.assertEqual(ctl(d.frames(m)), [])
        self.assertEqual(d.get_bit("f65_rf", "conn_latch"), 1)

    def test_wrong_slot_or_idle_module_is_selected_again(self):
        for slot, state in ((2, 3), (1, 0)):
            with self.subTest(slot=slot, state=state):
                d = self.dev()
                self.into(d, SWITCH_BT)
                d.ms(700)
                m = d.mark()
                d.status(slot, state)
                d.ms(20)
                self.assertEqual(ctl(d.frames(m)), [sel(0, 1)])

    def test_24g_wants_slot_0(self):
        d = self.dev()
        self.into(d, SWITCH_24G)
        d.ms(700)
        m = d.mark()
        d.status(1, 3)
        d.ms(20)
        self.assertEqual(ctl(d.frames(m)), [sel(0, 0)])

    def test_holdoff_after_a_select(self):
        """Review 4: a stale status right after a link-select does not undo it."""
        d = self.dev()
        self.into(d, SWITCH_BT)
        m = d.mark()
        d.status(2, 3)                     # old state, < 600 ms after the select
        d.ms(20)
        self.assertEqual(ctl(d.frames(m)), [])
        d.ms(600)
        m = d.mark()
        d.status(2, 3)
        d.ms(20)
        self.assertEqual(ctl(d.frames(m)), [sel(0, 1)])

    def test_pending_pairing_is_not_replaced(self):
        """Review 3: a pairing request waiting for the module is not
        overwritten by the supervisor's plain select."""
        d = self.dev()
        self.into(d, SWITCH_BT, 1)
        d.ms(700)
        d.radio.ready(False)               # the module is busy sending
        d.press(d.fn, ms=20)
        d.press(d.BT1, ms=3100)            # the pairing hold completes: select(1) pending
        d.release(d.BT1, d.fn, ms=20)
        self.assertEqual(d.get_xram(d._xdata_static("f65_rf", "link_flag"))[0], 1)
        m = d.mark()
        d.radio.queue_rx(status_frame(2, 3))   # a mismatching status arrives
        d.ms(50, until=lambda s: d.radio.rx_pending() == 0)
        d.ms(1)
        d.radio.ready(True)
        d.ms(30)
        self.assertEqual(ctl(d.frames(m))[:1], [sel(1, 1)])
        self.assertNotIn(sel(0, 1), d.frames(m))

    def test_host_leds_from_the_status_frame(self):
        """Review 7: status [3] is the host LED byte (Caps Lock etc.)."""
        d = self.dev()
        self.into(d, SWITCH_BT, 1)
        d.status(1, 3, leds=0x02)
        self.assertEqual(d.led_state(), 0x02)
        d.status(1, 3, leds=0x00)
        self.assertEqual(d.led_state(), 0x00)

    def test_status_is_ignored_in_the_wired_position(self):
        d = self.dev()
        d.status(1, 3, leds=0x02)
        d.ms(20)
        self.assertEqual(d.frames()[2:], [])
        self.assertEqual(d.led_state(), 0x00)


# ---------------------------------------------------------------- 4
class TestReceive(RadioCase):

    def linked(self):
        d = self.dev()
        self.into(d, SWITCH_24G, 0)
        d.ms(700)
        return d

    def test_two_frames_in_one_envelope_only_the_first_counts(self):
        """As the stock parser (0x0608): one frame per P4.7 envelope, the one
        its first byte announces."""
        d = self.linked()
        m = d.mark()
        d.module(announce_frame() + status_frame(0, 3, leds=0x02), gap_ms=10)
        self.assertEqual(d.frames(m).count(ACK_F0), 1)
        self.assertEqual(d.led_state(), 0x00, "the status after it is not parsed")
        m = d.mark()
        d.module(status_frame(0, 3, leds=0x02) + announce_frame(), gap_ms=10)
        self.assertEqual(d.led_state(), 0x02)
        self.assertNotIn(ACK_F0, d.frames(m), "no ack for bytes after the first frame")

    def test_frame_split_across_passes(self):
        d = self.linked()
        d.radio.byte_cycles(920 * 12)      # ~0.5 ms a byte: the frame spans ~5 ms
        d.status(0, 3, leds=0x02, gap_ms=5)
        d.radio.byte_cycles(920)
        self.assertEqual(d.led_state(), 0x02)

    def test_an_envelope_that_starts_unknown_is_dropped(self):
        d = self.linked()
        m = d.mark()
        d.module([0x55] + status_frame(0, 3, leds=0x02))
        d.module([0x55] + announce_frame(), gap_ms=10)
        d.module(status_frame(0, 3)[:4] + [0x03, 0x00, 0x00, 0x00, 0x00, 0x52], gap_ms=10)
        self.assertEqual(d.led_state(), 0x00)
        self.assertNotIn(ACK_F0, d.frames(m), "no spurious F0 from a 03 inside an envelope")

    def test_bad_sum_is_ignored(self):
        d = self.linked()
        bad = status_frame(0, 3, leds=0x02)
        bad[-1] ^= 0xFF
        d.module(bad)
        self.assertEqual(d.led_state(), 0x00)

    def test_truncated_frame_then_a_good_one(self):
        d = self.linked()
        d.module(status_frame(0, 3, leds=0x02)[:5], gap_ms=20)
        self.assertEqual(d.led_state(), 0x00)
        d.status(0, 3, leds=0x02)
        self.assertEqual(d.led_state(), 0x02)

    def test_framing_error_flags_are_cleared(self):
        d = self.linked()
        d.module(status_frame(0, 3, leds=0x02), fe_at=(3,))
        self.assertEqual(d.get_sfr(SCON) & 0xE0, 0, "SCON[7:5] (FE/RXOV/TXCOL) cleared")
        d.status(0, 3, leds=0x04)
        self.assertEqual(d.led_state(), 0x04, "the link goes on")


# ---------------------------------------------------------------- 5
class TestPriority(RadioCase):
    """EUART0 at priority 3 (IPH1/IPL1, stock 0xB108): a byte arrives every
    38 µs while the Timer2 matrix scan runs ~320 µs with its real delays."""

    def burst(self, with_fix):
        """Three 22-byte containers, each starting as a matrix scan starts."""
        d = self.dev(stub_delays=False)
        self.into(d, SWITCH_24G)
        self.assertTrue(d.get_sfr(IPH1) & d.get_sfr(IPL1) & ES0, "EUART0 at level 3")
        if not with_fix:
            d.set_sfr(IPH1, d.get_sfr(IPH1) & ~ES0)
            d.set_sfr(IPL1, d.get_sfr(IPL1) & ~ES0)
        start = len(d.radio.log().rx())
        scan = d._a("matrix_scan_full")
        d.sock.settimeout(60)                         # real delays, a loaded machine
        for _ in range(3):
            d.brk(scan)
            d.run()
            d.cmd("delete")
            d.radio.ready(False)
            d.radio.queue_rx(container_frame(0x08, [0x5A] * 15))
            d.ms(3)
            d.radio.ready(True)
            d.ms(3)
        rx = d.radio.log().rx()[start:]
        return d, sum(1 for _, _, extra in rx if extra == "lost"), len(rx)

    def test_no_byte_is_lost_during_the_scan(self):
        d, lost, n = self.burst(with_fix=True)
        self.assertEqual(n, 3 * 22)
        self.assertEqual(lost, 0)

    def test_without_the_priority_bytes_are_lost(self):
        """The regression the priority fixes: reproduced with IPH1/IPL1 cleared."""
        d, lost, n = self.burst(with_fix=False)
        self.assertGreater(lost, 0)


# ---------------------------------------------------------------- 6, 15
class TestModuleBusyOrAbsent(RadioCase):

    def test_reports_wait_while_the_module_sends(self):
        d = self.dev()
        self.into(d, SWITCH_24G, 0)
        d.ms(700)
        d.radio.ready(False)
        m = d.mark()
        d.tap(A, hold=40, after=40)
        self.assertEqual(d.frames(m), [], "nothing goes out while P4.7 is low")
        d.radio.ready(True)
        d.ms(60)
        got = reports(d.frames(m))
        self.assertEqual(got[:2], [long_frame(0, 0x04), LONG_EMPTY])

    def test_usb_comes_back_with_the_module_stuck_busy(self):
        d = self.dev()
        self.into(d, SWITCH_BT, 1)
        d.radio.ready(False)
        d.switch(SWITCH_USB, 300)
        self.assertEqual(d.transport(), 0)
        self.assertTrue(d.usb_enabled())
        d.mark_usb_configured()
        reps = d.down(A)
        self.assertEqual(reps[-1][2], 0x04, "keys reach USB")
        d.up(A)

    def test_module_absent_wired_board_works(self):
        """No module: nothing ever answers. P4.7 has no pull-up (the stock
        init leaves P4PCR bit 7 clear), so without a module its level is not
        defined; this models it high (the idle level)."""
        d = self.dev()
        d.ms(300)
        reps = d.down(A)
        self.assertEqual(reps[-1][2], 0x04)
        d.up(A)
        self.assertNotIn("WATCHDOG", d.stderr_text())

    def test_module_absent_wireless_no_hang(self):
        d = self.dev()
        d.switch(SWITCH_BT, 200)
        d.ms(1000)                          # probes every 200 ms, no replies
        m = d.mark()
        d.tap(A)
        self.assertEqual(reports(d.frames(m))[0], long_frame(0, 0x04))
        d.switch(SWITCH_USB, 200)
        self.assertTrue(d.usb_enabled())


# ---------------------------------------------------------------- 7
class TestAcks(RadioCase):

    def linked(self):
        d = self.dev()
        self.into(d, SWITCH_BT, 1)
        d.ms(700)
        return d

    def test_announce_gets_f0(self):
        d = self.linked()
        m = d.mark()
        d.module(announce_frame(), gap_ms=10)
        self.assertEqual(d.frames(m)[0], ACK_F0)

    def test_container_gets_f1_but_not_sub_8(self):
        d = self.linked()
        m = d.mark()
        d.module(container_frame(0x01), gap_ms=10)
        self.assertEqual(d.frames(m), [ACK_F1])
        m = d.mark()
        d.module(container_frame(0x08), gap_ms=10)
        self.assertEqual(d.frames(m), [])

    def test_container_with_a_bad_sum_is_not_acked(self):
        d = self.linked()
        bad = container_frame(0x01)
        bad[-1] ^= 0xFF
        m = d.mark()
        d.module(bad, gap_ms=10)
        self.assertEqual(d.frames(m), [])

    def test_container_06_factory_reset_does_nothing(self):
        d = self.linked()
        d.press(d.fn, ms=20)
        d.tap(S)                            # Fn+S: Mac mode (a saved setting)
        d.release(d.fn, ms=50)
        before = d.get_rom(0xEC00, 0x200)
        m = d.mark()
        d.module(container_frame(0x06), gap_ms=50)
        self.assertEqual(d.frames(m), [ACK_F1])
        self.assertEqual(d.get_rom(0xEC00, 0x200), before, "settings untouched")
        self.assertEqual(d.setting("os_mac"), 1)


# ---------------------------------------------------------------- 8, review 5
class TestBattery(RadioCase):

    def test_percent_from_raw(self):
        d = self.dev()
        self.into(d, SWITCH_24G, 0)
        d.ms(700)
        for raw, pct in ((700, 0), (715, 0), (737, 10), (781, 30), (912, 89), (935, 100), (989, 100)):
            with self.subTest(raw=raw):
                d.module(announce_frame(), gap_ms=5)   # re-arms the display
                m = d.mark()
                d.status(0, 3, raw=raw, gap_ms=10)
                self.assertEqual([f for f in d.frames(m) if len(f) > 1 and f[1] == 0x0D], [batt(pct)])

    def test_display_starts_at_the_target(self):
        """Review 5: the first value shown is the module's, not a slow ramp."""
        d = self.dev()
        self.into(d, SWITCH_BT)
        m = d.mark()
        d.status(1, 3, raw=0x03DD)
        d.ms(10)
        self.assertIn(batt(100), d.frames(m))
        self.assertEqual(d.frames(m).count(batt(100)), 1)

    def test_on_battery_a_big_drop_jumps(self):
        d = self.dev()
        self.into(d, SWITCH_BT, 1)
        d.radio.usb_power(False)
        d.ms(50)
        m = d.mark()
        for _ in range(6):
            d.status(1, 3, raw=781)
        self.assertEqual([f for f in d.frames(m) if len(f) > 1 and f[1] == 0x0D], [batt(30)])

    def pct_frames(self, d, m):
        return [f[2] for f in d.frames(m) if len(f) > 1 and f[1] == 0x0D]

    def statuses(self, d, raw, n=6):
        for _ in range(n):
            d.status(1, 3, raw=raw)

    def test_on_battery_a_big_rise_jumps(self):
        """0x7E5D: on battery the display may also jump up, by 30 or more."""
        d = self.dev()
        self.into(d, SWITCH_BT)
        d.radio.usb_power(False)
        d.ms(50)
        self.statuses(d, 800)                         # re-armed by the unplug: 38 at once
        m = d.mark()
        self.statuses(d, 870)                         # target 70: 32 above
        self.assertEqual(self.pct_frames(d, m), [70])

    def test_charging_shows_99_then_100_when_charged(self):
        d = self.dev()
        self.into(d, SWITCH_BT)
        d.radio.usb_power(False)
        d.ms(50)
        self.statuses(d, 800)
        d.radio.usb_power(True)
        d.radio.charging(True)
        d.ms(300)
        m = d.mark()
        self.statuses(d, 935, 12)                     # target 100 while charging: 99
        self.assertEqual(self.pct_frames(d, m), [99])
        d.radio.charging(False)
        d.ms(2100)                                    # P7.7 high for 200 samples: charged
        m = d.mark()
        self.statuses(d, 935)
        self.assertEqual(self.pct_frames(d, m), [100])

    def test_unplugging_re_arms_the_display(self):
        """0x9E5D: when USB power goes, the next battery pass shows the target
        as it is (and after six status frames, I[0x18] = 0)."""
        d = self.dev()
        self.into(d, SWITCH_BT)
        self.statuses(d, 989)                         # 100 on USB power
        d.radio.usb_power(False)
        d.ms(50)
        m = d.mark()
        self.statuses(d, 900, 5)
        self.assertEqual(self.pct_frames(d, m), [])
        self.statuses(d, 900, 1)
        self.assertEqual(self.pct_frames(d, m), [84])

    def test_usb_keys_are_never_blocked_by_the_cutoff(self):
        """The cutoff only holds back keys in the wireless positions (here the
        middle position is reached with P4.4 still reading low)."""
        d = self.dev()
        self.into(d, SWITCH_BT, 1)
        d.radio.usb_power(False)
        d.status(1, 3, raw=730)
        d.radio.wake_key(True)
        d.ms(2300)
        self.assertEqual(d.get_bit("f65_rf", "batt_cut"), 1)
        d.radio.wake_key(False)
        d.switch(SWITCH_USB, 300)
        d.mark_usb_configured()
        reps = d.down(A)
        self.assertTrue(reps and reps[-1][2] == 0x04, reps)
        d.up(A)

    def test_low_battery_cutoff_blocks_keys_and_sleeps(self):
        d = self.dev()
        self.into(d, SWITCH_BT, 1)
        d.radio.usb_power(False)
        d.status(1, 3, raw=730)
        d.radio.wake_key(True)     # a key held: the sleep wakes at once
        d.ms(2300)
        self.assertIn("[SIE] sleep: PCON power-down", d.stderr_text())
        d.radio.wake_key(False)
        m = d.mark()
        d.tap(A, hold=40, after=60)
        self.assertNotIn(long_frame(0, 0x04), d.frames(m), "no key reports during the cutoff")
        d.radio.usb_power(True)                 # external power ends it
        d.ms(50)
        m = d.mark()
        d.tap(A, hold=40, after=60)
        self.assertIn(long_frame(0, 0x04), d.frames(m))


# ---------------------------------------------------------------- 9, 10
class TestReports(RadioCase):

    def linked(self, pos=SWITCH_24G):
        d = self.dev()
        self.into(d, pos, 0 if pos == SWITCH_24G else 1)
        d.ms(700)
        return d

    def test_a(self):
        d = self.linked()
        m = d.mark()
        d.tap(A)
        self.assertEqual(reports(d.frames(m))[:2], [long_frame(0, 0x04), LONG_EMPTY])

    def test_shift_a(self):
        d = self.linked()
        m = d.mark()
        d.press(LSFT, A, ms=40)
        self.assertIn(long_frame(0x02, 0x04), d.frames(m))

    def test_six_keys_sixth_in_the_bitmap(self):
        d = self.linked()
        m = d.mark()
        for k in (A, B, C, D, E_, F):
            d.press(k, ms=15)
        d.ms(30)
        want = frame(0x02, 0x00, 0x04, 0x05, 0x06, 0x07, 0x08, 0x00, 0x00, 0x02, length=30)
        self.assertEqual(want[-1], 0x32)
        self.assertIn(want, d.frames(m))

    def test_consumer_short_frame(self):
        d = self.linked()
        d.press(d.fn, ms=20)
        d.tap(S)                                # Fn+S: Mac (media without Right Shift)
        m = d.mark()
        d.tap(NUM[12], hold=40, after=40)       # Fn+= : Volume Up
        d.tap(NUM[3], hold=40, after=40)        # Fn+3 : Mission Control 0x29F
        d.release(d.fn)
        got = [f for f in d.frames(m) if len(f) > 1 and f[1] == 0x03]
        self.assertEqual(got, [short_frame(0xE9), SHORT_EMPTY, short_frame(0x29F), SHORT_EMPTY])
        self.assertEqual(short_frame(0xE9)[-1], 0x68)

    def test_release_repeat(self):
        d = self.linked(SWITCH_BT)
        m = d.mark()
        d.tap(A, hold=40, after=150)
        self.assertEqual(reports(d.frames(m)),
                         [long_frame(0, 0x04), LONG_EMPTY] + [LONG_ERO, LONG_EMPTY] * 3)

    def test_pacing_bt_8ms_24g_2ms(self):
        """Report starts: Bluetooth every 8 ms (4 pumps), 2.4 GHz on the 2 ms
        pump - a long frame (1.2 ms) plus the 1 ms of P0.2 high makes that
        every 4 ms for long frames."""
        # (a key event pumps once more, as on the stock, so one interval
        # can be a pump shorter)
        for pos, lo, hi in ((SWITCH_BT, 5.9, 12), (SWITCH_24G, 1.9, 4.5)):
            with self.subTest(pos=pos):
                d = self.linked(pos)
                m = len(self.envelopes(d))
                d.tap(A, hold=40, after=150)
                env = [e for e in self.envelopes(d)[m:] if e["bytes"][1] == 0x02]
                gaps = [self.cycles_ms(a["t"], b["t"]) for a, b in zip(env[1:], env[2:])]
                self.assertTrue(gaps and all(g >= lo for g in gaps), gaps)
                self.assertLess(min(gaps), hi, gaps)
                if pos == SWITCH_BT:
                    self.assertGreaterEqual(sorted(gaps)[len(gaps) // 2], 7.9, gaps)


# ---------------------------------------------------------------- 11
class TestPairingKeys(RadioCase):

    def hold(self, d, key, ms):
        d.press(d.fn, ms=20)
        d.press(key, ms=ms)
        d.release(key, ms=20)
        d.release(d.fn, ms=50)

    def test_bt_hold_pairs_the_slot(self):
        d = self.dev()
        self.into(d, SWITCH_BT, 1)
        d.ms(700)
        m = d.mark()
        self.hold(d, d.BT1, 3200)
        self.assertEqual(ctl(d.frames(m)), [sel(1, 1)])

    def test_bt_hold_is_real_time(self):
        d = self.dev()
        self.into(d, SWITCH_BT, 1)
        d.ms(700)
        m = d.mark()
        self.hold(d, d.BT1, 2800)
        self.assertEqual(ctl(d.frames(m)), [sel(0, 1)],
                         "released before 3 s: a plain select on release")

    def test_bt_short_press_selects_on_release(self):
        d = self.dev()
        self.into(d, SWITCH_BT, 1)
        d.ms(700)
        d.press(d.fn, ms=20)
        m = d.mark()
        d.press(d.BT2, ms=50)
        self.assertEqual(ctl(d.frames(m)), [], "nothing on the press")
        d.release(d.BT2, ms=20)
        d.release(d.fn, ms=20)
        self.assertEqual(ctl(d.frames(m)), [sel(0, 2)])
        self.assertEqual(d.bt_slot(), 2)

    def test_24g_hold_pairs_the_dongle(self):
        d = self.dev()
        self.into(d, SWITCH_24G, 0)
        d.ms(700)
        m = d.mark()
        self.hold(d, d.P24, 3200)
        self.assertEqual(ctl(d.frames(m)), [sel(1, 0)])

    def test_keys_of_the_other_mode_and_wired_send_nothing(self):
        """Fn+Q/W/E only in Bluetooth, Fn+R only in 2.4 GHz, nothing over USB;
        Fn+T sends nothing anywhere."""
        k = OursF65
        for pos, keys in ((SWITCH_24G, (k.BT1, k.BT2, k.BT3, k.FREE)), (SWITCH_BT, (k.P24, k.FREE)),
                          (SWITCH_USB, (k.P24, k.BT1, k.BT2, k.BT3, k.FREE))):
            with self.subTest(pos=pos):
                d = self.dev()
                if pos != SWITCH_USB:
                    self.into(d, pos, 0 if pos == SWITCH_24G else 1)
                    d.ms(700)
                m = d.mark()
                usb = len(d.ep1_reports())
                for k in keys:
                    self.hold(d, k, 3200 if k == keys[0] else 100)
                self.assertEqual(ctl(d.frames(m)), [])
                if pos == SWITCH_USB:
                    self.assertEqual([r for r in d.ep1_reports()[usb:] if any(r[2:])], [])


# ---------------------------------------------------------------- 13, review 1/11
class TestSleep(RadioCase):

    def sleep_now(self, d, wake=True):
        """Run into the next power-down; stop there. Returns nothing; the
        caller inspects the pins, then wake() lets a key wake it."""
        pd = d.code("f65_power", "power_down")
        d.brk(pd)
        d.brk(d._a("pwm4_ms_tick_interrupt_handler"))
        try:
            for _ in range(5000):
                if d.stopped_at(d.run()) == pd:
                    return
            self.fail("no sleep")
        finally:
            d.cmd("delete")

    def wake(self, d):
        d.radio.wake_key(True)
        d.ms(30)
        d.radio.wake_key(False)
        self.assertIn("[SIE] wake from power-down", d.stderr_text())

    def test_unlinked_sleep_frames_and_parking(self):
        d = self.dev()
        self.into(d, SWITCH_BT, 1, state=2)     # reconnecting in BT: (10 x 2 + 1) s
        self.poke_idle(d, 20)
        m = d.mark()
        self.sleep_now(d)
        self.assertEqual(ctl(d.frames(m)), SLEEP_UNLINKED)
        self.check_parked(d)

    def check_parked(self, d):
        p0, p0cr = d.get_sfr(0x80), d.get_sfr(P0CR)
        self.assertEqual((p0cr & 0x30, p0 & 0x30), (0x30, 0x00), "both switch pins driven low (stock 0x006E)")
        self.assertEqual(d.get_sfr(0xB0) & 0x02, 0x02, "P4.1 high")
        self.assertEqual(d.get_sfr(0xF8) & 0x40, 0x00, "P7.6 low")
        self.assertEqual(d.get_sfr(0xD1) & 0x0F, 0x00, "rows are inputs")
        self.assertEqual(d.get_sfr(0xE6) & 0x18, 0x00, "rows are inputs")
        self.assertFalse(d.get_sfr(USBCON) & 0x80, "USB off")
        self.assertFalse(d.es0())

    def test_linked_sleep_after_61_s(self):
        d = self.dev()
        self.into(d, SWITCH_BT, 1)
        self.poke_idle(d, 60)
        d.ms(900)
        self.assertNotIn("sleep: PCON", d.stderr_text(), "not before (30 x 2 + 1) s")
        m = d.mark()
        self.sleep_now(d)
        self.assertEqual(ctl(d.frames(m)), SLEEP_LINKED)

    def test_wake_pulse_usb_stays_off(self):
        """Review 1: after a wireless wake USB stays off; P0.2 pulses low to
        wake the module."""
        d = self.dev()
        self.into(d, SWITCH_BT, 1, state=2)
        self.poke_idle(d, 20)
        self.sleep_now(d)
        n = len(d.radio.log().p02())
        self.wake(d)
        p02 = d.radio.log().p02()[n:]
        self.assertGreaterEqual(len(p02), 2)
        (t0, v0), (t1, v1) = p02[0], p02[1]
        self.assertEqual((v0, v1), (0, 1))
        self.assertTrue(1.0 <= self.cycles_ms(t0, t1) <= 2.5, self.cycles_ms(t0, t1))
        self.assertFalse(d.usb_enabled(), "USB stays off after a wireless wake")
        self.assertTrue(d.es0())
        m = d.mark()
        d.tap(A, hold=40, after=40)
        self.assertIn(long_frame(0, 0x04), d.frames(m))

    def test_no_sleep_before_any_status(self):
        d = self.dev()
        d.switch(SWITCH_BT, 200)
        self.poke_idle(d, 3000)
        d.ms(300)
        self.assertNotIn("sleep: PCON", d.stderr_text())

    def test_wired_without_a_host_sleeps(self):
        """Review 11: the wired position powered but never configured, 10 s
        without a USB interrupt (the stock's check at 0x8BC1), keys or not."""
        d = self.dev()
        d.cmd("set mem xram 0x%x 0x00" % d._a("usb_device_state"))
        self.poke_quiet(d, 9990)
        self.sleep_now(d)
        self.wake(d)
        self.assertTrue(d.usb_enabled())

    def test_wired_with_a_host_does_not_idle_sleep(self):
        d = self.dev()
        self.poke_idle(d, 30)
        self.poke_quiet(d, 30000)
        d.ms(300)
        self.assertNotIn("sleep: PCON", d.stderr_text())

    ENUMERATION = ([0x80, 0x06, 0x00, 0x01, 0x00, 0x00, 0x12, 0x00], [0x00, 0x05, 0x07, 0x00, 0x00, 0x00, 0x00, 0x00],
                   [0x80, 0x06, 0x00, 0x02, 0x00, 0x00, 0x09, 0x00], [0x00, 0x09, 0x01, 0x00, 0x00, 0x00, 0x00, 0x00])

    def test_bus_reset_after_a_long_idle_does_not_sleep(self):
        """A host that resets the bus (a reboot) after the board has been
        configured and untouched for 30 s: enumeration runs, no power-down
        in the middle of it (the USB interrupts count, not the keys)."""
        d = self.dev()
        self.poke_idle(d, 30)
        try:
            self.poke_quiet(d, 30000)
        except KeyError:
            pass                                    # an image without the USB counter
        d.radio.wake_key(True)                      # a power-down would come back at once, logged
        d.set_sfr(0x92, d.get_sfr(0x92) | 0x01)     # USBRSTIF: the host resets the bus
        d.ms(20)
        for setup in self.ENUMERATION:
            d.setup_packet(setup)
            d.ms(50)
        d.ms(200)
        self.assertNotIn("sleep: PCON", d.stderr_text())
        self.assertTrue(d.usb_enabled())

    def test_usb_interrupts_and_keys_keep_it_awake(self):
        d = self.dev()
        d.cmd("set mem xram 0x%x 0x00" % d._a("usb_device_state"))
        self.poke_quiet(d, 9900)
        d.radio.wake_key(True)
        for _ in range(6):                          # 2.4 s, a SETUP every 400 ms
            d.setup_packet(self.ENUMERATION[0])
            d.ms(400)
        self.poke_quiet(d, 9990)
        d.tap(A, hold=20, after=20)                 # a key clears the counter too
        d.ms(300)
        self.assertNotIn("sleep: PCON", d.stderr_text())


# ---------------------------------------------------------------- 14
class TestStuckSend(RadioCase):

    def _check_and_close(self, d):
        # the send cut short by the watchdog is incomplete on purpose
        try:
            for f in d.frames():
                self.assertFalse(len(f) > 1 and f[1] == 0x04)
        finally:
            d.close()

    def test_line_freed_after_three_probes(self):
        d = self.dev()
        self.into(d, SWITCH_BT, 1)
        d.ms(700)
        n = len(d.radio.log().p02())
        # A frame starts (the next status request); once its first byte is
        # going out, TI is never serviced again.
        d.brk(d.code("f65_rf", "tx_start"))
        d.run()
        d.cmd("delete")
        d.cmd("step 3000")
        d.set_sfr(IEN1, d.get_sfr(IEN1) & ~ES0)
        d.ms(1000)
        p02 = d.radio.log().p02()[n:]
        # The first send hangs with P0.2 low; the stuck-send watchdog frees the
        # line within three probe periods (~600 ms), and again after each retry.
        self.assertEqual(p02[0][1], 0)
        freed = [t for t, v in p02 if v == 1]
        self.assertTrue(freed, "P0.2 released by the watchdog")
        # three status requests (200 ms apart) without any UART interrupt;
        # the first may still see the interrupt of the byte that went out
        self.assertLessEqual(self.cycles_ms(p02[0][0], freed[0]), 850)
        d.set_sfr(IEN1, d.get_sfr(IEN1) | ES0)
        d.ms(300)
        m = d.mark()
        d.tap(A)
        self.assertIn(long_frame(0, 0x04), d.frames(m))


# ---------------------------------------------------------------- build-3 review: USB in the wireless positions
class TestUsbStaysOff(RadioCase):
    """In 2.4 GHz and Bluetooth USB stays off whatever the bus does: the USB
    interrupt is off, usb_init refuses (as the stock's 0xAF16 does in a
    wireless transport), and the D+ pull-up is off so the host sees a detach."""

    PUPIF, USBRSTIF = 0x80, 0x01

    def bus_event(self, d, flag):
        d.set_sfr(0x92, d.get_sfr(0x92) | flag)
        d.ms(20)

    def check_off(self, d, what):
        self.assertEqual(d.get_sfr(USBCON) & 0xC0, 0, what + ": ENUSB and the D+ pull-up off")
        self.assertEqual(d.transport(), 2, what)

    def test_bus_events_in_bluetooth(self):
        d = self.dev()
        self.into(d, SWITCH_BT, 1)
        self.check_off(d, "after the entry")
        self.assertEqual(d.get_sfr(IEN1) & 0x01, 0, "USB interrupt off")
        for flag in (self.PUPIF, self.USBRSTIF):
            self.bus_event(d, flag)
            self.check_off(d, "flag %02x" % flag)
        d.set_sfr(IEN1, d.get_sfr(IEN1) | 0x01)       # even with the interrupt forced on
        for flag in (self.PUPIF, self.USBRSTIF):
            self.bus_event(d, flag)
            self.check_off(d, "interrupt forced on, flag %02x" % flag)

    def test_bus_events_after_a_wireless_wake(self):
        d = self.dev()
        self.into(d, SWITCH_BT, 1, state=2)
        self.poke_idle(d, 20)
        TestSleep.sleep_now(self, d)
        TestSleep.wake(self, d)
        d.ms(50)
        self.check_off(d, "after the wake")
        self.assertEqual(d.get_sfr(IEN1) & 0x01, 0, "USB interrupt off after the wake")
        for flag in (self.PUPIF, self.USBRSTIF):
            self.bus_event(d, flag)
            self.check_off(d, "after the wake, flag %02x" % flag)

    def test_the_middle_attaches_again(self):
        d = self.dev()
        self.into(d, SWITCH_BT, 1)
        d.switch(SWITCH_USB, 200)
        self.assertEqual(d.get_sfr(USBCON) & 0xC0, 0xC0)
        self.assertEqual(d.get_sfr(IEN1) & 0x01, 0x01)

    def test_wkup_only_after_a_key_wake(self):
        """0x1063: USBCON.WKUP after a wireless wake only if the key
        interrupt woke it."""
        for key in (True, False):
            with self.subTest(key=key):
                d = self.dev()
                self.into(d, SWITCH_BT, 1, state=2)
                self.poke_idle(d, 20)
                TestSleep.sleep_now(self, d)          # at power_down()
                if key:
                    TestSleep.wake(self, d)
                else:                                 # power_down returns without an interrupt
                    sp = d.get_sfr(0x81)
                    lo, hi = d.get_iram(sp - 1, 2)
                    d.cmd("pc 0x%x" % ((hi << 8) | lo))
                    d.set_sfr(0x81, sp - 2)
                    d.ms(30)
                self.assertEqual(d.get_sfr(USBCON) & 0x02, 0x02 if key else 0x00)


# ---------------------------------------------------------------- build-3 review: sends and receives
class TestLineTiming(RadioCase):
    """P0.2 before the first start bit and between frames (checked on every
    scenario by RadioCase); here the frames of a flush, the lead per frame
    type against the stock's, and frames ending between two ticks."""

    def test_release_frames_are_paced(self):
        d = self.dev()
        self.into(d, SWITCH_24G, 0)
        d.ms(300)
        m = len(self.envelopes(d))
        d.switch(SWITCH_BT, 300)                      # flush: long and short release, then the select
        env = [e for e in self.envelopes(d)[m:] if e["lead"] is not None]
        rel = [e for e in env if e["bytes"][1] in (0x02, 0x03)]
        self.assertEqual(len(rel), 2)
        self.assertGreaterEqual(self.cycles_ms(rel[0]["t"], rel[1]["t"]), 7.9, "Bluetooth pacing in the drain")
        for e in env[1:]:
            self.assertGreaterEqual(e["gap"], MIN_GAP, hexs(e["bytes"][:3]))

    # The stock's lead in the simulator, per first two bytes (the shortest
    # seen): smk must not be shorter.
    STOCK_LEAD_US = {0x02: 34.9, 0x03: 17.2, 0x01: 5.0, 0x09: 23.5, 0x06: 5.0, 0x0D: 5.0, 0x0E: 5.0,
                     0x0B: 5.0, 0x0C: 5.0, 0xF0: 3.4, 0xF1: 3.4}

    def test_lead_per_frame_type(self):
        d = self.dev()
        d.ms(50)
        self.into(d, SWITCH_24G, 0)
        d.ms(300)
        d.module(announce_frame(), gap_ms=10)
        d.module(container_frame(0x01), gap_ms=10)
        d.tap(A, hold=30, after=100)
        d.switch(SWITCH_BT, 300)
        d.status(1, 3)
        d.ms(300)
        d.switch(SWITCH_USB, 300)
        seen = {}
        for e in self.envelopes(d):
            if e["lead"] is None or len(e["bytes"]) < 2:
                continue
            seen.setdefault(e["bytes"][1], []).append(e["lead"] / 24.0)
        self.assertTrue({0x02, 0x03, 0x01, 0x09, 0x06, 0x0E, 0x0B, 0xF0, 0xF1} <= set(seen), sorted(seen))
        for cmd, leads in seen.items():
            self.assertGreaterEqual(min(leads), self.STOCK_LEAD_US[cmd], "0x%02x: %s" % (cmd, leads))

    def linked(self):
        d = self.dev()
        self.into(d, SWITCH_24G, 0)
        d.ms(700)
        self.assertEqual(d.led_state(), 0x00)
        return d

    def queue_status_low(self, d):
        """A status frame (host LEDs 02) is in; P4.7 is still low."""
        d.radio.ready(False)
        d.radio.queue_rx(status_frame(0, 3, leds=0x02))
        d.ms(50, until=lambda s: d.radio.rx_pending() == 0)

    def due_now(self, d):
        d.cmd("set mem xram 0x%x 0x00 0x00" % d._xdata_static("f65_rf", "probe_at"))
        d.cmd("set mem xram 0x%x 0x00 0x00" % d._xdata_static("f65_rf", "pump_at"))

    def test_frame_ending_in_the_main_loop_before_a_send(self):
        """P4.7 goes high between two ticks while a send is due in that pass:
        the frame is parsed, not thrown away by the send's receive restart."""
        d = self.linked()
        self.queue_status_low(d)
        d.brk(d._a("rf_task"))
        d.run()
        d.cmd("delete")
        d.radio.ready(True)
        self.due_now(d)
        d.ms(10)
        self.assertEqual(d.led_state(), 0x02)

    def test_frame_ending_just_before_the_send_starts(self):
        d = self.linked()
        self.queue_status_low(d)
        self.due_now(d)
        d.brk(d.code("f65_rf", "probe"))
        d.run()
        d.cmd("delete")
        d.radio.ready(True)                           # after rf_task looked, before tx_start
        d.ms(10)
        self.assertEqual(d.led_state(), 0x02)

    def test_two_envelopes_within_a_millisecond(self):
        """A frame, P4.7 high for ~100 µs, the next frame: two envelopes, both
        parsed (the main loop looks at P4.7, not only the 1 ms tick)."""
        d = self.linked()
        m = d.mark()
        d.radio.ready(False)
        d.radio.queue_rx(status_frame(0, 3, leds=0x02))
        d.ms(50, until=lambda s: d.radio.rx_pending() == 0)
        d.cmd("step 300")
        d.radio.ready(True)
        d.cmd("step 1200")                            # ~100 µs high
        d.radio.ready(False)
        d.radio.queue_rx(announce_frame())
        d.ms(50, until=lambda s: d.radio.rx_pending() == 0)
        d.cmd("step 300")
        d.radio.ready(True)
        d.ms(10)
        self.assertEqual(d.led_state(), 0x02)
        self.assertIn(ACK_F0, d.frames(m))

    def test_ack_waits_for_a_frame_in_flight(self):
        d = self.linked()
        d.radio.byte_cycles(920 * 20)                 # 0.8 ms a byte: a long frame takes ~23 ms
        m = d.mark()
        n = len(d.radio.log().events)
        d.keys.press(*A)
        for _ in range(60):
            d.ms(1)
            if any(k == "TX" for k, *_ in d.radio.log().events[n:]):
                break
        d.module(announce_frame(), gap_ms=60)         # the module sends while the long frame goes out
        d.keys.release(*A)
        d.radio.byte_cycles(920)
        d.ms(200)
        ev = d.radio.log().events[n:]
        self.assertEqual([e for e in ev if e[0] == "TXCOL"], [])
        got = d.frames(m)
        self.assertIn(long_frame(0, 0x04), got)
        self.assertIn(ACK_F0, got)
        self.assertLess(got.index(long_frame(0, 0x04)), got.index(ACK_F0))


# ---------------------------------------------------------------- build-3 review: requests across transport changes
class TestLinkRequests(RadioCase):

    def test_pairing_hold_carried_into_the_middle(self):
        """Fn+Q (slot 1) held in Bluetooth, the switch moved to the middle
        before 3 s, held on past 3 s: nothing goes to the module, P0.2 stays high, and
        back in Bluetooth the first frames are the entry's."""
        d = self.dev()
        self.into(d, SWITCH_BT, 1)
        d.ms(700)
        d.press(d.fn, ms=20)
        d.press(d.BT1, ms=500)
        d.switch(SWITCH_USB, 300)
        n = len(d.radio.log().events)
        d.ms(2600)
        ev = d.radio.log().events[n:]
        self.assertEqual([e for e in ev if e[0] in ("TX", "P0.2")], [])
        self.assertEqual(d.get_bit("f65_rf", "tx_busy"), 0)
        d.release(d.BT1, d.fn, ms=50)
        m = d.mark()
        d.switch(SWITCH_BT, 300)
        self.assertEqual(ctl(d.frames(m))[:1], [sel(0, 1)])
        self.assertNotIn(sel(1, 1), d.frames(m))

    def test_pairing_hold_across_a_mode_switch_does_not_pair(self):
        d = self.dev()
        self.into(d, SWITCH_BT, 1)
        d.ms(700)
        m = d.mark()
        d.press(d.fn, ms=20)
        d.press(d.BT1, ms=1000)
        d.switch(SWITCH_24G, 300)
        d.ms(2500)                                    # held well past 3 s
        d.release(d.BT1, d.fn, ms=50)
        self.assertEqual([f for f in ctl(d.frames(m)) if f[1] == 0x01], [sel(0, 0)])

    def test_waiting_request_dropped_on_a_transport_change(self):
        """A pairing request still waiting for the module (P4.7 low) belongs
        to the transport it was made in."""
        d = self.dev()
        self.into(d, SWITCH_BT, 1)
        d.ms(700)
        d.radio.ready(False)
        d.press(d.fn, ms=20)
        d.press(d.BT1, ms=3100)
        d.release(d.BT1, d.fn, ms=20)
        self.assertEqual(d.get_xram(d._xdata_static("f65_rf", "link_flag"))[0], 1)
        m = d.mark()
        d.switch(SWITCH_24G, 300)
        d.radio.ready(True)
        d.ms(50)
        self.assertEqual([f for f in ctl(d.frames(m)) if f[1] == 0x01], [sel(0, 0)])


# ---------------------------------------------------------------- build-3b review: kicks, sleep, USB end to end
class TestWatchdogKicks(RadioCase):
    """Only the main loop kicks the watchdog, so every bounded wait in it
    kicks as it goes: the longest stretch without a kick stays within 3 ms
    (build-5; the stock's longest in the simulator is 8.2 ms; the real period
    is not known). Real delays: stubbed ones would hide a long delay_ms. The
    board's scan rhythm (Timer2 at its real rate: the scan takes ~40 % of the
    core, which stretches every main-loop stretch) and LEDs lit (Caps Lock,
    charging), so the LED subframes and the rendering run too."""

    LIMIT_MS = 3.0

    def dev(self, *a, **kw):
        d = super().dev(*a, **kw)
        d.cmd("set mem xram 0x1f52 0x01")                    # Timer2 at its real rate
        d.cmd("set mem xram 0x%x 0x02" % d._a("keyboard_state"))   # Caps Lock on
        d.radio.charging(True)
        return d

    def check(self, d, what):
        # A debug image (not for the board) prints its settings at boot
        # (settings_dump) without a kick: ~3.1 ms there; release: ~1.5 ms.
        limit = self.LIMIT_MS + (1.0 if "settings_dump" in d.sym else 0.0)
        self.assertLessEqual(d.radio.kick_gap_ms(), limit, what)

    def test_entries_with_the_module_busy(self):
        d = self.dev(stub_delays=False)
        self.into(d, SWITCH_BT, 1)
        d.ms(300)
        d.radio.ready(False)                          # every gate shut: the waits run out
        d.tap(A)
        d.switch(SWITCH_USB, 400)
        self.check(d, "Bluetooth to the middle, module busy")
        d.switch(SWITCH_BT, 300)
        d.ms(100)
        d.reboot(SWITCH_USB, stub_delays=False)       # saved Bluetooth, powered in the middle, still busy
        d.ms(600)
        self.check(d, "saved Bluetooth, powered in the middle")

    def test_pairing_hold_with_the_module_busy(self):
        d = self.dev(stub_delays=False)
        self.into(d, SWITCH_BT, 1)
        d.ms(300)
        d.radio.ready(False)
        d.press(d.fn, ms=20)
        d.press(d.BT1, ms=3300)
        d.release(d.BT1, d.fn, ms=300)
        self.check(d, "pairing flush with the module busy")

    def test_sleeps_and_switch_cycle(self):
        d = self.dev(stub_delays=False)
        for pos in (SWITCH_24G, SWITCH_BT, SWITCH_USB, SWITCH_BT):
            d.switch(pos, 300)
        d.status(1, 2)
        self.poke_idle(d, 20)
        d.radio.wake_key(True)
        d.ms(1500)                                    # sleeps, wakes at once
        d.radio.wake_key(False)
        # the wired entry (two control frames with their waits, 200 ms, the
        # link save to flash) ends with a clear of the quiet counter about
        # 400 ms after the switch: poke the counter only once it is over
        d.switch(SWITCH_USB, 600)
        d.cmd("set mem xram 0x%x 0x00" % d._a("usb_device_state"))
        self.poke_quiet(d, 9990)
        d.radio.wake_key(True)
        d.ms(300)                                     # the wired sleep and its wake
        d.radio.wake_key(False)
        self.assertEqual(d.stderr_text().count("wake from power-down"), 2)
        self.check(d, "switch cycle, wireless and wired sleep")


class TestUsbEndToEnd(RadioCase):
    """USB comes back usable: it enumerates and sinowisp's feature report 5
    (05 75 00 00 00 00) reaches the bootloader entry."""

    def enumerate_and_isp(self, d):
        from sim import set_address, set_configuration
        d.setup_packet(set_address(0x2A))
        d.setup_packet(set_configuration(1))
        self.assertEqual(d.get_xram(d._a("usb_device_state"))[0], 0x02, "configured")
        d.setup_packet([0x21, 0x09, 0x05, 0x03, 0x01, 0x00, 0x06, 0x00])
        d.cmd("set mem xram 0x1100 0x05 0x75 0x00 0x00 0x00 0x00 0x00 0x00")
        d.set_sfr(0x93, 0x10)                         # OEP0IF: the data stage
        d.brk(ISP_ENTRY)
        d.cmd("break 0x%x 100" % d._a("pwm4_ms_tick_interrupt_handler"))
        at = d.stopped_at(d.run())
        d.cmd("delete")
        self.assertEqual(at, ISP_ENTRY)

    def test_after_a_wired_sleep_wake(self):
        d = self.dev()
        d.cmd("set mem xram 0x%x 0x01" % d._a("usb_device_state"))   # addressed, not configured
        self.poke_quiet(d, 9990)
        TestSleep.sleep_now(self, d)
        slept = len(d.radio.pullup_log())
        TestSleep.wake(self, d)
        d.ms(50)
        self.assertEqual([on for _, on in d.radio.pullup_log()[slept:]], [False, True],
                         "detached, then attached again: the host enumerates afresh")
        self.enumerate_and_isp(d)

    def test_after_wireless_to_the_middle(self):
        d = self.dev()
        self.into(d, SWITCH_BT, 1)
        d.switch(SWITCH_USB, 300)
        self.assertEqual(d.radio.pullup_log()[-1][1], True)
        self.enumerate_and_isp(d)

    def test_after_power_up_in_the_middle_with_bluetooth_saved(self):
        d = self.dev()
        self.saved(d, SWITCH_BT)
        d.reboot(SWITCH_USB)
        d.ms(300)
        self.assertTrue(d.usb_enabled())
        self.enumerate_and_isp(d)

    def in_power_down(self, d):
        return "PowerDown" in d.cmd("state")

    def test_asleep_stays_asleep_and_the_switch_waits_for_a_key(self):
        """No key, no event: the board stays in power-down; the switch moved
        to the middle meanwhile changes nothing until a key wakes it, and
        then USB comes back usable."""
        d = self.dev()
        self.into(d, SWITCH_BT, 1, state=2)
        self.poke_idle(d, 20)
        TestSleep.sleep_now(self, d)
        d.cmd("step 100")
        self.assertTrue(self.in_power_down(d))
        d.cmd("step 300000")
        self.assertTrue(self.in_power_down(d), "no key, no event: still asleep")
        d.radio.switch(SWITCH_USB)
        d.cmd("step 300000")
        self.assertTrue(self.in_power_down(d), "the switch alone does not wake it")
        self.assertFalse(d.usb_enabled())
        TestSleep.wake(self, d)
        d.ms(300)
        self.assertEqual(d.transport(), 0)
        self.enumerate_and_isp(d)

    def test_stale_key_wake_flag(self):
        """A key wake the wired sleep does not look at leaves the key flag
        set; a later wireless wake that no key caused must not set WKUP."""
        d = self.dev()
        d.cmd("set mem xram 0x%x 0x00" % d._a("usb_device_state"))
        self.poke_quiet(d, 9990)
        TestSleep.sleep_now(self, d)
        TestSleep.wake(self, d)                       # the key interrupt fired
        d.ms(50)
        self.into(d, SWITCH_BT, 1, state=2)
        self.poke_idle(d, 20)
        TestSleep.sleep_now(self, d)
        sp = d.get_sfr(0x81)
        lo, hi = d.get_iram(sp - 1, 2)
        d.cmd("pc 0x%x" % ((hi << 8) | lo))           # power_down returns without an interrupt
        d.set_sfr(0x81, sp - 2)
        d.ms(30)
        self.assertEqual(d.get_sfr(USBCON) & 0x02, 0x00)


# ---------------------------------------------------------------- the usjis image
class TestTransportUsjis(TestTransport):
    FW = F65_FW


class TestReportsUsjis(TestReports):
    FW = F65_FW


class TestPairingKeysUsjis(TestPairingKeys):
    FW = F65_FW


class TestImeKeysOverBluetooth(RadioCase):
    """usjis: the IME mod-taps on both sides of Space over Bluetooth (Win:
    left Alt / Muhenkan INT5, right Right Ctrl / Henkan INT4)."""

    FW = F65_FW
    LEFT, RIGHT = (2, 4), (9, 4)

    def test_taps_and_holds(self):
        d = self.dev()
        self.into(d, SWITCH_BT, 1)
        d.ms(700)
        for key, usage, mod in ((self.RIGHT, 0x8A, 0x10), (self.LEFT, 0x8B, 0x04)):
            with self.subTest(key=key):
                m = d.mark()
                d.tap(key, hold=30, after=200)
                got = reports(d.frames(m))
                self.assertIn(long_frame(0, usage), got)
                self.assertEqual(got[got.index(long_frame(0, usage)) + 1], LONG_EMPTY)
                m = d.mark()
                d.press(key, ms=300)                  # held past the tapping term
                d.tap(A, hold=30, after=30)
                d.release(key, ms=200)
                got = reports(d.frames(m))
                self.assertIn(long_frame(mod, 0x04), got)
                self.assertNotIn(long_frame(0, usage), got)


# ---------------------------------------------------------------- recovery
class TestRecovery(RadioCase):

    def test_boot_escape_before_any_radio_init_in_every_position(self):
        for pos in (SWITCH_USB, SWITCH_24G, SWITCH_BT):
            with self.subTest(pos=pos):
                d = self.dev()
                self.saved(d, pos)
                n = len(d.radio.log().events)
                d.keys.press(*ESC)
                d.radio.switch(pos)
                d.reset_fast()
                d.brk(ISP_ENTRY)
                d.brk(d._a("rf_uart_init"))
                at = d.stopped_at(d.run())
                d.cmd("delete")
                self.assertEqual(at, ISP_ENTRY, "Esc held at power-up reaches ISP before the radio")
                self.assertEqual([e for e in d.radio.log().events[n:] if e[0] == "TX"], [])

    def test_main_loop_hang_is_reset_by_the_watchdog(self):
        """A hang of the main loop with the interrupts still running (the
        matrix scan and its delays, the 1 ms tick): only the main loop kicks
        the watchdog (DELAY_NO_WATCHDOG_KICK), so it resets the board, and Esc
        held through that reaches the bootloader. Real delays: a stubbed
        delay_us would hide a kick in the scan."""
        d = self.dev(stub_delays=False)
        self.into(d, SWITCH_BT, 1)
        d.keys.press(*ESC)
        d.ms(40)
        d.brk(d._a("kb_update"))                      # stop in the main loop, not in an interrupt
        d.run()
        d.cmd("delete")
        d.cmd("set mem rom 0x9000 0x80 0xfe")        # SJMP $: the main loop hangs there
        d.cmd("pc 0x9000")
        self.assertEqual(d.get_sfr(0xA8) & 0x80, 0x80, "EA stays on")
        at = None
        for _ in range(20):                           # up to 10 s, 500 ms a run
            d.cmd("break 0x%x 500" % d._a("pwm4_ms_tick_interrupt_handler"))
            d.brk(ISP_ENTRY)
            at = d.stopped_at(d.run())
            d.cmd("delete")
            if at == ISP_ENTRY:
                break
        self.assertIn("WATCHDOG timeout", d.stderr_text())
        self.assertEqual(at, ISP_ENTRY)

    def _check_and_close(self, d):
        # the watchdog test resets on purpose
        try:
            for f in d.frames():
                self.assertFalse(len(f) > 1 and f[1] == 0x04)
        finally:
            d.close()


# ---------------------------------------------------------------- stack
class TestStack(RadioCase):
    """EUART0 at priority 3 nests on top of the Timer2 scan and USB
    interrupts. With the real delays (the scan at its full length), a
    wireless session with container bursts, the second-Fn media keys, typing,
    a sleep and a wake, then back to USB: the stack keeps half its room."""

    def test_nested_interrupts_leave_headroom(self):
        for fw in (F65_FW, F65_ANSI_FW):
            with self.subTest(fw=os.path.basename(fw)):
                d = self.dev(fw, stub_delays=False)
                d.brk(d._a("kb_update"))
                d.run()
                d.cmd("delete")
                d.paint_stack()
                self.into(d, SWITCH_BT, 1)
                d.ms(700)
                for _ in range(6):
                    d.module(container_frame(0x01, [0x5A] * 15), gap_ms=1)
                fn = d.fn
                d.press(fn, ms=20)
                d.press(RSFT, ms=20)
                d.tap(NUM[10], hold=40, after=40)
                d.release(RSFT, fn, ms=40)
                d.tap(A, hold=40, after=150)
                self.poke_idle(d, 60)
                d.radio.wake_key(True)
                d.ms(2000)
                d.radio.wake_key(False)
                self.assertIn("[SIE] wake from power-down", d.stderr_text())
                d.switch(SWITCH_USB, 400)
                d.mark_usb_configured()
                d.down(A)
                d.up(A)
                # Wired: the Win Alt+Tab key under Fn + Right Shift runs a key
                # of its own through process_keycode again (the re-entry), and a
                # SETUP is handled in the USB interrupt meanwhile.
                d.press(fn, ms=20)
                d.press(RSFT, ms=20)
                d.press(NUM[3], ms=30)
                d.setup_packet([0x80, 0x06, 0x00, 0x02, 0x00, 0x00, 0x22, 0x00])
                d.release(NUM[3], ms=30)
                d.release(RSFT, fn, ms=40)
                room = 0xFF - d.stack_base
                used = d.stack_highwater()
                self.assertLess(used, room // 2, "stack: %d of %d bytes" % (used, room))


# ---------------------------------------------------------------- stock oracle
class TestAgainstStock(RadioCase):
    """The same scenario on the stock V1 image and on smk: the frames must
    agree (reports, controls). The stock's own 08 containers (driver
    protocol) are left out; smk does not send them."""

    def both(self, scenario, pos=SWITCH_USB):
        out = []
        for make in (lambda: self.stock(pos), lambda: self.dev(position=pos)):
            d = make()
            m = d.mark()
            scenario(d)
            out.append([f for f in d.frames(m) if len(f) > 1 and f[1] != 0x08])
        return out

    def test_boot_names(self):
        s = self.stock()
        s.ms(100)
        o = self.dev()
        o.ms(100)
        self.assertEqual(s.frames(), NAMES)
        self.assertEqual(o.frames(), NAMES)
        self.assertFalse(s.es0(), "stock: ES0 off in the wired position")

    def test_entries(self):
        def sc(d):
            d.ms(50)
            d.switch(SWITCH_24G, 200)
            d.switch(SWITCH_BT, 200)
            d.status(1, 3)
            d.ms(50)
            d.switch(SWITCH_USB, 300)
        s, o = self.both(sc)
        self.assertEqual(ctl(o), ctl(s))
        self.assertEqual(sorted(map(tuple, reports(o))), sorted(map(tuple, reports(s))))

    def test_typing_and_release_repeat(self):
        def sc(d):
            d.ms(50)
            d.switch(SWITCH_BT, 200)
            d.status(1, 3)
            d.ms(700)
            m = d.mark()
            d.tap(A, hold=40, after=150)
            for k in (A, B, C, D, E_, F):
                d.press(k, ms=20)
            # Last pressed first: the stock sends its ErrorRollOver frames as
            # soon as the five slots are empty, even with a key left in the
            # bitmap; smk waits until every key is up (see the docs).
            for k in (F, E_, D, C, B, A):
                d.release(k, ms=20)
            d.ms(150)
            d.press(LSFT, ms=20)
            d.press(A, ms=40)
            d.release(A, ms=20)
            d.release(LSFT, ms=150)
            d.result = reports(d.frames(m))
        res = []
        for make in (lambda: self.stock(), lambda: self.dev()):
            d = make()
            sc(d)
            res.append(d.result)
        self.assertEqual(res[1], res[0])

    def test_pairing_holds(self):
        """The same keys by function: the stock pairs on its Fn+E / Fn+Q, smk
        on Fn+Q / Fn+R."""
        def sc(d, pos, key):
            key = getattr(d, key)
            d.ms(50)
            d.switch(pos, 200)
            d.status(0 if pos == SWITCH_24G else 1, 3)
            d.ms(700)
            m = d.mark()
            d.press(d.fn, ms=20)
            d.press(key, ms=3200)
            d.release(key, ms=20)
            d.release(d.fn, ms=50)
            d.press(d.fn, ms=20)
            d.tap(d.BT2 if pos == SWITCH_BT else d.BT1)
            d.release(d.fn, ms=50)
            return ctl(d.frames(m))
        for pos, key in ((SWITCH_BT, "BT1"), (SWITCH_24G, "P24")):
            with self.subTest(pos=pos):
                s = sc(self.stock(), pos, key)
                o = sc(self.dev(), pos, key)
                self.assertEqual(o, s)

    def test_supervisor_and_acks(self):
        def sc(d):
            d.ms(50)
            d.switch(SWITCH_BT, 200)
            d.ms(700)
            m = d.mark()
            d.status(2, 3)
            d.ms(20)
            d.module(announce_frame(), gap_ms=10)
            d.module(container_frame(0x01), gap_ms=10)
            d.module(container_frame(0x08), gap_ms=10)
            return ctl(d.frames(m))
        s = sc(self.stock())
        o = sc(self.dev())
        self.assertEqual(o, s)

    def test_battery(self):
        def sc(d):
            d.ms(50)
            d.switch(SWITCH_24G, 200)
            d.ms(700)
            out = []
            for raw in (700, 737, 781, 912, 989):
                d.module(announce_frame(), gap_ms=5)
                m = d.mark()
                d.status(0, 3, raw=raw, gap_ms=10)
                out += [f for f in d.frames(m) if len(f) > 1 and f[1] == 0x0D]
            return out
        s = sc(self.stock())
        o = sc(self.dev())
        self.assertEqual(o, s)

    PARK_REGS = {"P0": 0x80, "P0CR": 0xE1, "P0PCR": 0xE9, "P4": 0xB0, "P4CR": 0xE5, "P4PCR": 0xED,
                 "P5": 0x88, "P5CR": 0xE6, "P5PCR": 0xEE, "P6": 0xC0, "P6CR": 0xE7, "P6PCR": 0xEF,
                 "P7": 0xF8, "P7CR": 0xD1, "P7PCR": 0xD9, "IEN0": 0xA8, "IEN1": 0xA9, "IENC": 0xBA,
                 "EXF0": 0xB6, "USBCON": 0x91, "SCON": 0xD8}

    def test_parking_and_wake(self):
        """Every pin, the wake sources and USB at the power-down instruction
        are the stock's; after a key wakes it, P0.2 pulses low (the stock:
        0-2 ms) and USB stays off."""
        got = {}
        for name, d in (("stock", self.stock()), ("smk", self.dev())):
            d.ms(50)
            d.switch(SWITCH_BT, 200)
            d.status(1, 2)
            d.ms(1000)
            if name == "stock":
                d.cmd("set mem xram 0x%x 0x00 0x14" % STOCK_IDLE_S)
                d.cmd("break sfr w 0x87")
                for _ in range(400):
                    d.run()
                    if d.get_sfr(0x87) & 0x02:
                        break
                d.cmd("delete")
            else:
                self.poke_idle(d, 20)
                d.brk(d.code("f65_power", "power_down"))
                d.run()
                d.cmd("delete")
            regs = {k: d.get_sfr(v) for k, v in self.PARK_REGS.items()}
            n = len(d.radio.log().p02())
            d.radio.wake_key(True)
            d.ms(40)
            d.radio.wake_key(False)
            p02 = d.radio.log().p02()[n:]
            self.assertEqual([v for _, v in p02[:2]], [0, 1], name)
            # (smk ends the pulse on its second 1 ms tick after the wake; the
            # PWM4 tick can wait behind a level-0 Timer2 scan, whose LED stop
            # adds some tens of microseconds since build-5: 2.2 ms, not 2.05)
            self.assertLessEqual(self.cycles_ms(p02[0][0], p02[1][0]), 2.2 if name == "smk" else 2.05, name)
            self.assertEqual(d.get_sfr(USBCON) & 0x80, 0, name + ": USB stays off")
            got[name] = regs
        # SCON: the stock leaves REN set, as smk does; compare everything.
        self.assertEqual(got["smk"], got["stock"])

    def test_battery_display_ramp(self):
        """The shown percent through the stock's rules: re-armed at the start
        and when USB power goes, a jump up on battery, a fall by 30, 99 while
        charging, 100 once charged."""
        def sc(d):
            d.ms(50)
            d.switch(SWITCH_BT, 200)
            for _ in range(20):
                if not isinstance(d, StockF65) or d.boot_show_done():
                    break
                d.ms(100)
            m = d.mark()
            # (the stock loses a status frame now and then: one that ends as
            # it starts a send - so a few more frames than passes need)
            steps = [("raw", 800, 6), ("unplug",), ("raw", 800, 8), ("raw", 870, 9), ("raw", 790, 14),
                     ("plug",), ("raw", 935, 14), ("charged",), ("raw", 935, 8), ("unplug",), ("raw", 900, 8)]
            for st in steps:
                if st[0] == "raw":
                    for _ in range(st[2]):
                        d.status(1, 3, raw=st[1])
                elif st[0] == "unplug":
                    d.radio.usb_power(False)
                    d.ms(60)
                elif st[0] == "plug":
                    d.radio.usb_power(True)
                    d.radio.charging(True)
                    d.ms(300)
                elif st[0] == "charged":
                    d.radio.charging(False)
                    d.ms(2100)
            return [f[2] for f in d.frames(m) if len(f) > 1 and f[1] == 0x0D]
        s = sc(self.stock())
        o = sc(self.dev())
        self.assertEqual(s, [38, 38, 70, 34, 99, 100, 84])
        self.assertEqual(o, s)

    def test_envelopes(self):
        """One frame per P4.7 envelope, the first; an unknown first byte drops
        it all: the acks match the stock's."""
        def sc(d):
            d.ms(50)
            d.switch(SWITCH_24G, 200)
            d.status(0, 3)
            d.ms(700)
            m = d.mark()
            d.module(announce_frame() + status_frame(0, 3), gap_ms=10)
            d.module(status_frame(0, 3) + announce_frame(), gap_ms=10)
            d.module([0x55] + announce_frame(), gap_ms=10)
            d.module(container_frame(0x01) + announce_frame(), gap_ms=10)
            return [f for f in d.frames(m) if len(f) > 1 and f[1] in (0xF0, 0xF1)]
        s = sc(self.stock())
        o = sc(self.dev())
        self.assertEqual(s, [ACK_F0, ACK_F1])
        self.assertEqual(o, s)

    def test_announce_restarts_the_status_request(self):
        def sc(d):
            d.ms(50)
            d.switch(SWITCH_BT, 200)
            d.status(1, 3)
            d.ms(300)
            n = d.mark()
            for _ in range(300):
                d.ms(1)
                if STATUS_REQ in d.frames(n):
                    break
            d.ms(120)
            m = len(d.radio.log().tx_frames())
            d.module(announce_frame(), gap_ms=2)
            d.ms(400)
            env = d.radio.log().tx_frames()[m:]
            t_ack = next(e["t"] for e in env if e["bytes"] == ACK_F0)
            t_req = next(e["t"] for e in env if e["bytes"] == STATUS_REQ and e["t"] > t_ack)
            return (t_req - t_ack) / CYCLES_PER_MS
        s = sc(self.stock())
        o = sc(self.dev())
        self.assertTrue(190 <= s <= 215, s)
        self.assertTrue(190 <= o <= 215, o)

    def test_sleep_frames(self):
        def sc(d, state, idle):
            d.ms(50)
            d.switch(SWITCH_BT, 200)
            d.status(1, state)
            d.ms(1000)                      # the stock clears idle on the state change
            if isinstance(d, StockF65):
                d.cmd("set mem xram 0x%x 0x%02x 0x%02x" % (STOCK_IDLE_S, idle >> 8, idle & 0xFF))
            else:
                self.poke_idle(d, idle)
            d.radio.wake_key(True)
            m = d.mark()
            d.ms(2500)
            return ctl(d.frames(m))[:2]
        # one second before the limit: (10 x 2 + 1) s reconnecting in BT,
        # (30 x 2 + 1) s connected
        for state, idle, want in ((2, 20, SLEEP_UNLINKED), (3, 60, SLEEP_LINKED)):
            with self.subTest(state=state):
                s = sc(self.stock(), state, idle)
                o = sc(self.dev(), state, idle)
                self.assertEqual(s, want)
                self.assertEqual(o, want)


if __name__ == "__main__":
    unittest.main()
