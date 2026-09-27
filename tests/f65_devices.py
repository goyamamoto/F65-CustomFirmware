"""The F65 board around the smk image and around the stock V1 image, driven
alike, so a scenario can run on both and their radio frames can be compared
(the stock image is the oracle).

Both run in the patched uCsim with the radio board of f65_radio.py; the keys
are closed contacts inside the simulator (ContactMatrix), and time advances in
1 ms steps of each firmware's own 1 ms tick (PWM4 on both)."""

import os
import tempfile
from pathlib import Path

from devices import UcsimSession
from sim import skip_or_fail
from test_f65 import F65Sim, COL_PIN, ROW_PIN
from f65_radio import (RadioBoard, ContactMatrix, Timeline, module_send, status_frame, announce_frame,
                       container_frame, MODEL_P0, MODEL_P4, MODEL_REG_TIMING, SWITCH_USB, SWITCH_24G, SWITCH_BT)

STOCK_MAIN_LOOP = 0x8B66   # stock main loop head
STOCK_MS_ISR = 0x7FBB      # stock PWM4 1 ms ISR
STOCK_ROW_READ = 0x6B81    # stock row read in the PWM0 scan ISR
STOCK_IDLE_S = 0x09AF      # stock idle seconds (XDATA, big-endian)
STOCK_TRANSPORT = 0x031A   # stock transport (0 wired, 1 2.4G, 2 BT)

IEN1, IPH1, IPL1, SCON, P0CR, USBCON = 0xA9, 0xB5, 0xB9, 0xD8, 0xE1, 0x91
ES0 = 0x40


def stock_image():
    """The official V1 image (SMK_F65_STOCK_IMAGE), or skip / fail (strict)."""
    img = os.environ.get("SMK_F65_STOCK_IMAGE")
    if not img or not Path(img).exists():
        skip_or_fail("set SMK_F65_STOCK_IMAGE to the official AULA_F65_V1_FN_Ctrl_firmware.bin "
                     "(the stock oracle for the radio frames)")
    return img


def stock_hex(image_bin):
    """The official image as Intel HEX in a temp file (uCsim loads HEX)."""
    d = Path(image_bin).read_bytes()
    tmp = Path(tempfile.mkdtemp()) / "stock_v1.hex"
    with open(tmp, "w") as f:
        for a in range(0, len(d), 16):
            rec = bytes([16, a >> 8, a & 0xFF, 0]) + d[a:a + 16]
            f.write(":%s%02X\n" % (rec.hex().upper(), (-sum(rec)) & 0xFF))
        f.write(":00000001FF\n")
    return str(tmp)


class RigMixin:
    """What a scenario needs from either image: time, keys, the module."""

    def ms(self, n, until=None):
        return self.tl.run_ms(n, until)

    def press(self, *keys, ms=30):
        for k in keys:
            self.keys.press(*k)
        self.ms(ms)

    def release(self, *keys, ms=30):
        for k in keys:
            self.keys.release(*k)
        self.ms(ms)

    def tap(self, *keys, hold=30, after=30):
        self.press(*keys, ms=hold)
        self.release(*keys, ms=after)

    def module(self, data, fe_at=(), gap_ms=2):
        module_send(self.radio, self.tl, data, fe_at, gap_ms)

    def status(self, slot, state, raw=0x03DD, leds=0x00, gap_ms=2):
        self.module(status_frame(slot, state, raw, leds), gap_ms=gap_ms)

    def switch(self, pos, ms=150):
        """Move the connection switch and let the 10-sample debounce (~100 ms)
        and the entry run."""
        self.radio.switch(pos)
        self.ms(ms)

    def frames(self, since=0):
        """Frames sent to the module (byte lists), from index `since` on."""
        return [f["bytes"] for f in self.radio.log().tx_frames()[since:]]

    def mark(self):
        return len(self.radio.log().tx_frames())


class OursF65(RigMixin, F65Sim):
    """The smk image on the F65 board with the radio module."""

    # Under Fn: Bluetooth slots on Q / W / E, 2.4 GHz on R, T free.
    BT1, BT2, BT3, P24, FREE = (1, 1), (2, 1), (3, 1), (4, 1), (5, 1)

    def __init__(self, firmware=None):
        super().__init__(firmware)
        self.keys = ContactMatrix(self, self.radio, COL_PIN, ROW_PIN)
        self.tl = Timeline(self, self._a("pwm4_ms_tick_interrupt_handler"), self._a("user_matrix_read_rows"),
                           self.keys)

    def start(self, position=SWITCH_USB, stub_delays=True):
        """Power up with the switch at `position` (the saved transport decides
        until the debounce settles). The busy-wait delays are stubbed unless
        `stub_delays` is false (the priority test needs the real scan length)."""
        self.radio.switch(position)
        self.radio.mark_start()
        if stub_delays:
            self.reset_fast()
        else:
            self.cmd("reset")
        self.brk(self._a("kb_update_switches"))
        self.run()
        self.cmd("delete")
        self.apply_lighting()
        if self.transport() == 0:
            self.mark_usb_configured()

    def reboot(self, position=None, stub_delays=True):
        """A power-on reset (flash kept, RAM cleared by the startup code)."""
        self.start(self.radio_position if position is None else position, stub_delays)

    @property
    def radio_position(self):
        p0 = self.radio.p0
        if not p0 & 0x20:
            return SWITCH_24G
        if not p0 & 0x10:
            return SWITCH_BT
        return SWITCH_USB

    def code(self, module, name):
        """Address of a module-static function: from the map where the build
        keeps debug symbols (C: <addr> F<module>$<name>$0$0), else from the
        module's relocated listing (`<addr> <line> _<name>:`), as a release
        build has only that."""
        import re
        pat = re.compile(r"^C:\s+([0-9A-Fa-f]+)\s+F%s\$%s\$0\$0\b" % (re.escape(module), re.escape(name)))
        with open(Path(self.firmware).with_suffix(".map")) as f:
            for line in f:
                m = pat.match(line)
                if m:
                    return int(m.group(1), 16)
        rst = Path(self.firmware).with_suffix(".ihx.p") / (module + ".rst")
        if rst.exists():
            lab = re.compile(r"^\s+([0-9A-Fa-f]{6})\s+\d+\s+_%s:\s*$" % re.escape(name))
            for line in rst.read_text().splitlines():
                m = lab.match(line)
                if m:
                    return int(m.group(1), 16)
        raise KeyError(name)

    def transport(self):
        return self.get_xram(self._xdata_static("f65_power", "transport"))[0]

    def bt_slot(self):
        return self.get_xram(self._xdata_static("f65_power", "bt_slot"))[0]

    def usb_enabled(self):
        return bool(self.get_sfr(USBCON) & 0x80)

    def es0(self):
        return bool(self.get_sfr(IEN1) & ES0)

    def led_state(self):
        return self.get_xram(self._a("keyboard_state"))[0]


class StockF65(RigMixin, UcsimSession):
    """The stock V1 image in the same simulator and board."""

    fn = (9, 4)   # the stock's Fn, right of Space
    # Under Fn: Bluetooth slots on E / R / T, 2.4 GHz on Q.
    BT1, BT2, BT3, P24, FREE = (3, 1), (4, 1), (5, 1), (1, 1), (2, 1)

    def __init__(self, hex_path):
        super().__init__(hex_path)
        self.radio = RadioBoard(self, MODEL_P0 | MODEL_P4 | MODEL_REG_TIMING)
        # PWM0 (the stock's 100 µs scan slot) and Timer2 (its end-of-sweep
        # one-shot) timed from their registers, as on the chip.
        self.keys = ContactMatrix(self, self.radio, COL_PIN, ROW_PIN)
        self.tl = Timeline(self, STOCK_MS_ISR, STOCK_ROW_READ, self.keys)

    def start(self, position=SWITCH_USB):
        self.radio.switch(position)
        self.cmd("reset")
        self.brk(STOCK_MAIN_LOOP)
        self.run()
        self.cmd("delete")

    def transport(self):
        return self.get_xram(STOCK_TRANSPORT)[0]

    def get_iram(self, addr, n):
        import re
        out = self.cmd("dump iram 0x%x 0x%x" % (addr, addr + n - 1))
        vals = []
        for line in out.splitlines():
            m = re.match(r"\s*0x[0-9a-fA-F]+\s+((?:[0-9a-fA-F]{2} )+)", line)
            if m:
                vals += [int(x, 16) for x in m.group(1).split()]
        return vals[:n]

    def boot_show_done(self):
        """0x26.3, set at boot (0x8B29) until the power-on lighting ends
        (0x1D3E); the battery display's 95-99 → 100 step waits for it."""
        return not (self.get_iram(0x26, 1)[0] & 0x08)

    def es0(self):
        return bool(self.get_sfr(IEN1) & ES0)


def control(frames):
    """The control frames (everything but reports 02/03 and containers 08).
    Envelopes without a command byte (the P0.2 wake pulse) are left out."""
    return [f for f in frames if len(f) > 1 and f[1] not in (0x02, 0x03, 0x08)]


def reports(frames):
    return [f for f in frames if len(f) > 1 and f[1] in (0x02, 0x03)]
