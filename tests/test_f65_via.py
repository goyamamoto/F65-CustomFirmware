#!/usr/bin/env python3
"""aula-f65-v1 with VIA (layouts ansi-via / usjis-via), in the patched uCsim.

What the VIA images change against ansi / usjis, checked here:
- USB: interface 0 is VIA's raw HID (usage page 0xFF60, 32-byte reports, no
  report IDs) on EP2 IN / OUT; interface 1 has the keyboard (report ID 3, the
  bare boot report under the boot protocol), system, consumer and the ISP
  report 5 on EP1. sinowisp's report 5 on interface 1 still jumps to the
  bootloader.
- VIA's commands (src/smk/via.c): protocol version, layer count, keycodes by
  position and as VIA's byte buffer, the switch matrix, the keymap reset;
  unknown commands come back as 0xFF.
- The keymap (src/smk/dynamic_keymap.c): what VIA sets is what the keys send,
  it is written to flash 0xE800-0xEBFF only, once and after the changes stop,
  kept across a power cycle, and a record that is not whole (power cut
  before its magic) or that another layout wrote is not taken.
- The settings-reset chord (Fn + Backspace held, Fn + V) resets the keymap too.
- A layer-tap key from VIA: a tap, its layer after the tapping term, when a
  later key goes down and up while it is held or at the ninth waiting event,
  the waiting keys in order, two layer-tap keys, Fn held, and the stack with
  the USB interrupt nested while the waiting keys go out.

Run after building, with SMK_F65_VIA_FIRMWARE (usjis-via) and
SMK_F65_ANSI_VIA_FIRMWARE (ansi-via) pointing at the images, or from the
default build directory. Needs model 10 of tools/ucsim/sh68f90.cc (EP2 OUT).
"""

import os
import re
import unittest
from pathlib import Path

from sim import REPO_ROOT, Sim, load_symbols, skip_or_fail, get_descriptor, DESC_CONFIGURATION
from test_air75 import read_ihex
from f65_devices import OursF65
from f65_radio import SWITCH_USB, SWITCH_24G

VIA_FW = os.environ.get("SMK_F65_VIA_FIRMWARE") or str(REPO_ROOT / "build" / "aula-f65-v1_usjis-via_smk.hex")
VIA_ANSI_FW = os.environ.get("SMK_F65_ANSI_VIA_FIRMWARE") or str(REPO_ROOT / "build" / "aula-f65-v1_ansi-via_smk.hex")

KEYMAP_ADDR, KEYMAP_END = 0xE800, 0xEC00     # dynamic_keymap.c record (meson.build: two sectors below the settings)
ISP_ENTRY = 0xFF00
LAYERS, ROWS, COLS = 4, 5, 16

KC_A, KC_B, KC_ESC, KC_CAPS, KC_Q = 0x04, 0x05, 0x29, 0x39, 0x14
REPORT_ID_KEYBOARD, REPORT_ID_CONSUMER = 3, 2
A, ESC, BSPC, V = (1, 2), (0, 0), (13, 0), (4, 3)
FN = {"ansi": (9, 4), "usjis": (10, 4)}
CAPS_ANSI = (0, 2)       # ansi: Caps Lock where printed
QK_MOMENTARY = 0x5220

USBIF2, OEP2CNT, EP2CON, EP0_OUT_BUF = 0x93, 0xA4, 0x9A, 0x1100
OEP2IF, OEP0IF, OEP2RDY = 0x40, 0x10, 0x01
KC_MUTE, CONSUMER_MUTE = 0x00A8, 0x00E2


def _need(fw):
    if not Path(fw).exists():
        skip_or_fail(f"no aula-f65-v1 VIA firmware at {fw}")


def _layout(fw):
    return "ansi" if "ansi" in Path(fw).name else "usjis"


def builtin_keymap(fw):
    """keymaps[] of the image, as [layer][row][col] keycodes (from the ROM)."""
    data = read_ihex(fw)
    base = load_symbols(Path(fw).with_suffix(".map"))["keymaps"]
    km = []
    for layer in range(LAYERS):
        rows = []
        for row in range(ROWS):
            cols = []
            for col in range(COLS):
                a = base + ((layer * ROWS + row) * COLS + col) * 2
                cols.append(data[a] | data[a + 1] << 8)
            rows.append(cols)
        km.append(rows)
    return km


class ViaF65(OursF65):
    """The VIA image on the F65 board: VIA's reports go in on EP2 OUT, the
    answers come out on EP2 IN; the keyboard reports carry report ID 3 on EP1."""

    def __init__(self, firmware=None):
        super().__init__(firmware or VIA_FW)

    def start(self, position=SWITCH_USB, stub_delays=True):
        """As OursF65.start, then (wired) the host's SET_ADDRESS and
        SET_CONFIGURATION, which arm EP2 OUT for VIA."""
        super().start(position, stub_delays)
        if self.transport() == 0:
            self.configure()

    def configure(self):
        self.setup_packet([0x00, 0x05, 0x2A, 0x00, 0x00, 0x00, 0x00, 0x00])   # SET_ADDRESS
        self.ms(2)
        self.setup_packet([0x00, 0x09, 0x01, 0x00, 0x00, 0x00, 0x00, 0x00])   # SET_CONFIGURATION 1
        self.ms(2)

    def ep2_out_armed(self):
        return bool(self.get_sfr(EP2CON) & OEP2RDY)

    def deliver(self, *data):
        """The host's OUT transfer of one VIA report: needs EP2 OUT armed; the
        SIE takes the packet and drops the ready bit (the firmware re-arms)."""
        assert self.ep2_out_armed(), "EP2 OUT is not armed: the host's report would be NAKed"
        pkt = (list(data) + [0] * 32)[:32]
        self.cmd("set mem xram 0x1140 " + " ".join("0x%02x" % b for b in pkt))
        self.set_sfr(OEP2CNT, 32)
        self.set_sfr(EP2CON, self.get_sfr(EP2CON) & ~OEP2RDY)
        self.set_sfr(USBIF2, self.get_sfr(USBIF2) | OEP2IF)

    def ep2_in(self):
        return [[int(x, 16) for x in m.split()]
                for m in re.findall(r"\[SIE\] EP2 IN \d+ bytes:((?: [0-9a-f]{2})*)", self.stderr_text())]

    def ep1_packets(self):
        return [[int(x, 16) for x in m.split()]
                for m in re.findall(r"\[SIE\] EP1 IN \d+ bytes:((?: [0-9a-f]{2})*)", self.stderr_text())]

    def ep1_reports(self):
        """The 6KRO keyboard reports (report ID 3 taken off)."""
        return [p[1:] for p in self.ep1_packets() if len(p) == 9 and p[0] == REPORT_ID_KEYBOARD]

    def flash_ops(self):
        return [(op, int(a, 16)) for op, a in re.findall(r"\[SIE\] FLASH (\w+) ([0-9a-f]{4})", self.stderr_text())]

    def via(self, *data):
        """Send one VIA report (padded to 32 bytes); return the 32-byte answer."""
        before = len(self.ep2_in())
        self.deliver(*data)
        self.ms(50, until=lambda s: len(self.ep2_in()) > before and self.ep2_out_armed())
        answers = self.ep2_in()
        assert len(answers) > before, "no answer on EP2 IN"
        assert len(answers) == before + 1, "more than one answer"
        assert self.ep2_out_armed(), "EP2 OUT not armed again after the answer"
        return answers[-1]

    # --- VIA commands -----------------------------------------------------
    def get_keycode(self, layer, row, col):
        a = self.via(0x04, layer, row, col)
        return a[4] << 8 | a[5]

    def set_keycode(self, layer, row, col, kc):
        return self.via(0x05, layer, row, col, kc >> 8, kc & 0xFF)

    def get_buffer(self, offset, size):
        a = self.via(0x12, offset >> 8, offset & 0xFF, size)
        return a[4:4 + size]

    def set_buffer(self, offset, data):
        return self.via(0x13, offset >> 8, offset & 0xFF, len(data), *data)

    def wait_saved(self, ms=3000):
        """Run until the keymap's flash write is done (its magic is the last
        program, at KEYMAP_ADDR) or an erase-only save has happened: both
        sectors erased and no flash operation for 50 ms after that (the
        programs of a whole save follow the erases at once)."""
        n = len(self.flash_ops())
        quiet = {"len": 0, "ms": 0}

        def done(s):
            ops = self.flash_ops()[n:]
            if any(op == "program" and a == KEYMAP_ADDR + 1 for op, a in ops):
                return True
            if len(ops) != quiet["len"]:
                quiet["len"], quiet["ms"] = len(ops), 0
                return False
            quiet["ms"] += 1
            return (quiet["ms"] >= 50 and all(KEYMAP_ADDR <= a < KEYMAP_END for _, a in ops) and
                    [op for op, _ in ops] == ["erase", "erase"])
        self.ms(ms, until=done)
        return self.flash_ops()[n:]

    def led_report(self, setup, data):
        """A SET_REPORT(Output) control transfer on the running firmware."""
        self.setup_packet(setup)
        self.cmd("set mem xram 0x%x " % EP0_OUT_BUF + " ".join("0x%02x" % b for b in (list(data) + [0] * 8)[:8]))
        self.set_sfr(USBIF2, self.get_sfr(USBIF2) | OEP0IF)
        self.ms(5)
        return self.get_xram(self._a("keyboard_state"))[0]


class ViaCase(unittest.TestCase):
    FW = VIA_FW

    def kb(self, fw=None):
        fw = fw or self.FW
        _need(fw)
        kb = ViaF65(fw)
        self.addCleanup(kb.close)
        kb.start(SWITCH_USB)
        kb.ms(20)
        return kb

    def last_report(self, kb):
        """The keyboard's last report on EP1 (none is a failure, not an
        IndexError)."""
        reports = kb.ep1_reports()
        self.assertTrue(reports, "no keyboard report")
        return reports[-1]


class TestViaImage(unittest.TestCase):
    def test_code_stays_below_the_keymap_sectors(self):
        for fw in (VIA_FW, VIA_ANSI_FW):
            with self.subTest(fw=Path(fw).name):
                _need(fw)
                data = read_ihex(fw)
                self.assertLess(max(data), KEYMAP_ADDR,
                                f"code reaches 0x{max(data):04x}; the keymap record starts at 0x{KEYMAP_ADDR:04x}")
                self.assertEqual(data.get(0), 0x02, "sinowisp relocates the LJMP at 0x0000 to 0xEFFB")

    def test_via_and_the_boot_escape_are_linked(self):
        for fw in (VIA_FW, VIA_ANSI_FW):
            with self.subTest(fw=Path(fw).name):
                _need(fw)
                sym = load_symbols(Path(fw).with_suffix(".map"))
                for name in ("via_process", "dynamic_keymap_task", "user_boot_escape", "isp_jump"):
                    self.assertIn(name, sym)


class TestViaDefinition(unittest.TestCase):
    """src/keyboards/aula-f65-v1/via.json against the firmware."""

    DEF = REPO_ROOT / "src" / "keyboards" / "aula-f65-v1" / "via.json"
    KBDEF = REPO_ROOT / "src" / "keyboards" / "aula-f65-v1" / "kbdef.h"

    def setUp(self):
        import json
        self.d = json.loads(self.DEF.read_text())

    def test_ids_and_matrix(self):
        self.assertEqual((int(self.d["vendorId"], 16), int(self.d["productId"], 16)), (0x258A, 0x010C))
        self.assertEqual(self.d["matrix"], {"rows": ROWS, "cols": COLS})

    def test_custom_keycodes_follow_the_enum(self):
        """VIA gives customKeycodes[i] the keycode QK_KB_0 + i: one entry per
        custom keycode of kbdef.h (the leddiag ones are not in a VIA image)."""
        text = self.KBDEF.read_text()
        body = text[text.index("enum custom_keycodes {"):text.index("KB_SAFE_RANGE")]
        body = re.sub(r"#ifdef LED_DIAG.*?#endif", "", body, flags=re.S)
        body = re.sub(r"#ifdef VIA_ENABLE.*?#else(.*?)#endif", r"\1", body, flags=re.S)
        names = re.findall(r"^\s*([A-Z][A-Z0-9_]+)\s*(?:=[^,]*)?,", body, flags=re.M)
        self.assertEqual(names[0], "USJIS_TOG")
        self.assertEqual(len(self.d["customKeycodes"]), len(names), names)
        self.assertLessEqual(len(names), 32, "QK_KB_0 .. QK_KB_31")
        expect = {"USJIS_TOG": "US-JIS", "OS_WIN": "Win mode", "OS_MAC": "Mac mode", "LNK_24G": "2.4G",
                  "LNK_BT1": "BT 1", "LNK_BT2": "BT 2", "LNK_BT3": "BT 3", "FN_ALT": "Fn Alt",
                  "BAT_SHOW": "Battery", "BL_TOG": "BL on/off", "BL_EFF": "BL effect", "BL_COL": "BL colour",
                  "BL_BRI_UP": "BL bright +", "BL_BRI_DN": "BL bright -", "BL_SPD_UP": "Speed +",
                  "BL_SPD_DN": "Speed -", "SL_EFF": "Side effect", "SL_COL": "Side colour", "SL_BRI": "Side bright"}
        expect.update({"FR_%d" % i: "Fn row %d" % i for i in range(1, 13)})
        self.assertEqual([c["name"] for c in self.d["customKeycodes"]], [expect[n] for n in names],
                         "customKeycodes[i] is the enum's i-th keycode")

    def test_layout_keys_are_the_keys_of_the_keymap(self):
        """Every position the layout shows has a key in both base layers of
        both images, and every key of the base layers is shown."""
        shown = set()
        for row in self.d["layouts"]["keymap"]:
            for item in row:
                if isinstance(item, str):
                    r, c = map(int, item.split(","))
                    shown.add((r, c))
        for fw in (VIA_FW, VIA_ANSI_FW):
            _need(fw)
            km = builtin_keymap(fw)
            for layer in (0, 1):
                with self.subTest(fw=Path(fw).name, layer=layer):
                    keys = {(r, c) for r in range(ROWS) for c in range(COLS) if km[layer][r][c]}
                    self.assertEqual(keys, shown)


class TestViaUsb(unittest.TestCase):
    """Descriptors and the ISP report, through the real init path (batch sim)."""

    @classmethod
    def setUpClass(cls):
        _need(VIA_FW)
        cls.sim = Sim(VIA_FW)

    def _report_desc(self, iface):
        """GET_DESCRIPTOR(Report) on the running firmware: longer than the
        batch sim's NOP sled lets a control transfer run."""
        _need(VIA_FW)
        kb = ViaF65(VIA_FW)
        self.addCleanup(kb.close)
        kb.start(SWITCH_USB)
        mark = len(kb.stderr_text())
        kb.setup_packet([0x81, 0x06, 0x00, 0x22, iface, 0x00, 0xFF, 0x00])
        kb.ms(10)
        return [int(x, 16) for m in re.findall(r"\[SIE\] EP0 IN\[\d+\] \d+ bytes:((?: [0-9a-f]{2})*)",
                                               kb.stderr_text()[mark:]) for x in m.split()]

    def test_configuration_descriptor(self):
        desc = self.sim.reassemble(self.sim.control_in(get_descriptor(DESC_CONFIGURATION, length=0xFF)))
        self.assertEqual(desc[2] | desc[3] << 8, len(desc), "wTotalLength")
        self.assertEqual(desc[4], 2)
        items, i = [], 9
        while i < len(desc):
            items.append(desc[i:i + desc[i]])
            i += desc[i]
        ifaces = [d for d in items if d[1] == 4]
        eps = [d for d in items if d[1] == 5]
        self.assertEqual([(d[2], d[4], d[5], d[6], d[7]) for d in ifaces], [(0, 2, 3, 0, 0), (1, 1, 3, 1, 1)],
                         "IF0: HID, two endpoints (raw HID); IF1: HID boot keyboard, one endpoint")
        self.assertEqual([(d[2], d[3], d[4] | d[5] << 8, d[6]) for d in eps],
                         [(0x82, 3, 32, 1), (0x02, 3, 32, 1), (0x81, 3, 16, 1)])

    def test_interface_0_is_vias_raw_hid(self):
        self.assertEqual(self._report_desc(0), [
            0x06, 0x60, 0xFF, 0x09, 0x61, 0xA1, 0x01,
            0x09, 0x62, 0x15, 0x00, 0x26, 0xFF, 0x00, 0x95, 0x20, 0x75, 0x08, 0x81, 0x02,
            0x09, 0x63, 0x15, 0x00, 0x26, 0xFF, 0x00, 0x95, 0x20, 0x75, 0x08, 0x91, 0x02,
            0xC0])

    def test_interface_1_has_the_keyboard_and_report_5(self):
        d = bytes(self._report_desc(1))
        self.assertTrue(d.startswith(bytes([0x05, 0x01, 0x09, 0x06, 0xA1, 0x01, 0x85, REPORT_ID_KEYBOARD])), d.hex())
        self.assertIn(bytes([0x85, 0x01]), d)
        self.assertIn(bytes([0x85, 0x02]), d)
        i = d.index(bytes([0x06, 0x00, 0xFF, 0x09, 0x01, 0xA1, 0x01, 0x85, 0x05]))
        self.assertIn(bytes([0xB1, 0x02]), d[i:], "report 5 is a feature report")
        self.assertNotIn(bytes([0x85, 0x06]), d, "no NKRO report (EP1 takes 16 bytes)")

    def test_isp_report_on_interface_1_jumps_to_bootloader(self):
        """sinowisp's send_feature_report([05 75 00 00 00 00]) on interface 1."""
        sim = self.sim
        setup = [0x21, 0x09, 0x05, 0x03, 0x01, 0x00, 0x06, 0x00]
        sled = 0x900E
        cmds = sim._boot_to_post_init() + [
            sim._set_xram(sim.EP0_OUT_BUF, setup), "set mem sfr 0x92 0x10",
            "break 0x%x" % sled, "run",
            sim._set_xram(sim.EP0_OUT_BUF, [0x05, 0x75, 0, 0, 0, 0, 0, 0]), "set mem sfr 0x93 0x10",
            "pc 0x9000", "break 0x%x" % sled, "run",
            "pc 0x%x" % sim.USB_TASK, "break 0x%x" % ISP_ENTRY, "run", "info registers",
        ]
        out = sim.run(cmds)
        self.assertEqual(sim.stopped_at(out), ISP_ENTRY)
        self.assertEqual((sim.acc(out), sim.reg_b(out)), (0x5A, 0xA5))


class TestViaCommands(ViaCase):
    def test_protocol_version_and_layers(self):
        kb = self.kb()
        self.assertEqual(kb.via(0x01)[:3], [0x01, 0x00, 0x0C])
        self.assertEqual(kb.via(0x11)[:2], [0x11, LAYERS])

    def test_the_keymap_reads_as_the_built_in_one(self):
        kb = self.kb()
        km = builtin_keymap(self.FW)
        self.assertEqual(kb.get_keycode(0, 0, 0), km[0][0][0])
        self.assertEqual(km[0][0][0], KC_ESC)
        size = LAYERS * ROWS * COLS * 2
        buf = []
        for off in range(0, size, 28):
            buf += kb.get_buffer(off, min(28, size - off))
        want = [b for layer in km for row in layer for kc in row for b in (kc >> 8, kc & 0xFF)]
        self.assertEqual(buf, want, "VIA's buffer: layer, row, column, high byte first")

    def test_buffer_ends_with_zeros(self):
        kb = self.kb()
        size = LAYERS * ROWS * COLS * 2
        km = builtin_keymap(self.FW)
        last = km[3][4][15]
        self.assertEqual(kb.get_buffer(size - 2, 6), [last >> 8, last & 0xFF, 0, 0, 0, 0])

    def test_buffer_near_the_end_of_the_offsets(self):
        """A bulk read or write from 0xFFF0 does not wrap back to offset 0:
        the read is zeros, the write changes nothing."""
        kb = self.kb()
        self.assertEqual(kb.get_buffer(0xFFF0, 28), [0] * 28)
        want = kb.get_buffer(0, 28)
        kb.set_buffer(0xFFF0, [0x55] * 28)
        self.assertEqual(kb.get_buffer(0, 28), want)

    def test_uptime_in_milliseconds(self):
        """id_uptime: milliseconds since power-up (the 1 ms tick), 32 bits
        wide past the 16-bit counter's wrap."""
        kb = self.kb()

        def uptime():
            a = kb.via(0x02, 0x01)
            return a[2] << 24 | a[3] << 16 | a[4] << 8 | a[5]

        u0 = uptime()
        kb.ms(1000)
        u1 = uptime()
        self.assertTrue(950 <= u1 - u0 <= 1100, u1 - u0)
        at = kb._xdata_static("f65_rf", "ms_now")
        kb.cmd("set mem xram 0x%x 0xf0 0xff" % at)      # the 16-bit counter just before its wrap
        kb.ms(100)
        u2 = uptime()
        self.assertTrue(0x10000 <= u2 < 0x10000 + 200, hex(u2))

    def test_unknown_commands_come_back_as_ff(self):
        kb = self.kb()
        for cmd in (0x07, 0x08, 0x09, 0x0A, 0x0B, 0x14, 0x99):
            with self.subTest(cmd=cmd):
                a = kb.via(cmd, 1, 2, 3)
                self.assertEqual(a[:4], [0xFF, 1, 2, 3])

    def test_no_macros(self):
        kb = self.kb()
        self.assertEqual(kb.via(0x0C)[:2], [0x0C, 0])
        self.assertEqual(kb.via(0x0D)[:3], [0x0D, 0, 0])

    def test_switch_matrix_state(self):
        kb = self.kb()
        kb.press(A, ms=40)
        a = kb.via(0x02, 0x03, 0)
        self.assertEqual(a[:3], [0x02, 0x03, 0x00])
        rows = [a[3 + 2 * r] << 8 | a[4 + 2 * r] for r in range(ROWS)]
        self.assertEqual(rows, [0, 0, 1 << A[0], 0, 0])
        kb.release(A, ms=40)
        a = kb.via(0x02, 0x03, 0)
        self.assertEqual(a[3:3 + 2 * ROWS], [0] * (2 * ROWS))


class TestViaKeymap(ViaCase):
    def test_a_set_keycode_is_what_the_key_sends(self):
        kb = self.kb()
        kb.press(A, ms=40)
        self.assertEqual(self.last_report(kb)[2], KC_A)
        kb.release(A, ms=40)
        self.assertEqual(kb.set_keycode(0, A[1], A[0], KC_B)[:6], [0x05, 0, A[1], A[0], 0, KC_B])
        self.assertEqual(kb.get_keycode(0, A[1], A[0]), KC_B)
        kb.press(A, ms=40)
        self.assertEqual(self.last_report(kb)[2], KC_B)
        kb.release(A, ms=40)

    def test_set_buffer_round_trip(self):
        kb = self.kb()
        off = ((0 * ROWS + A[1]) * COLS + A[0]) * 2
        kb.set_buffer(off, [0x00, KC_Q])
        self.assertEqual(kb.get_keycode(0, A[1], A[0]), KC_Q)
        self.assertEqual(kb.get_buffer(off, 2), [0x00, KC_Q])

    def test_out_of_range_positions(self):
        kb = self.kb()
        self.assertEqual(kb.get_keycode(LAYERS, 0, 0), 0)
        self.assertEqual(kb.get_keycode(0, ROWS, 0), 0)
        kb.set_keycode(0, 0, COLS, KC_B)          # ignored
        kb.set_keycode(LAYERS, 0, 0, KC_B)
        self.assertEqual(kb.get_keycode(0, 0, 0), KC_ESC)

    def test_saved_once_in_the_keymap_sectors_and_kept(self):
        kb = self.kb()
        kb.set_keycode(0, A[1], A[0], KC_B)
        kb.set_keycode(1, A[1], A[0], KC_B)
        ops = kb.wait_saved()
        self.assertTrue(ops, "no flash write")
        self.assertTrue(all(KEYMAP_ADDR <= a < KEYMAP_END for _, a in ops), [hex(a) for _, a in ops if not KEYMAP_ADDR <= a < KEYMAP_END])
        self.assertEqual([a for op, a in ops if op == "erase"], [0xE800, 0xEA00])
        self.assertEqual([a for op, a in ops if op == "program"][-2:], [KEYMAP_ADDR, KEYMAP_ADDR + 1], "the magic last")
        n = len(kb.flash_ops())
        kb.ms(1500)
        self.assertEqual(kb.flash_ops()[n:], [], "saved once")
        kb.reboot()
        kb.ms(20)
        self.assertEqual(kb.get_keycode(0, A[1], A[0]), KC_B)
        kb.press(A, ms=40)
        self.assertEqual(self.last_report(kb)[2], KC_B)
        kb.release(A, ms=40)

    def test_reset_erases_the_record(self):
        kb = self.kb()
        kb.set_keycode(0, A[1], A[0], KC_B)
        kb.wait_saved()
        kb.via(0x06)
        self.assertEqual(kb.get_keycode(0, A[1], A[0]), KC_A)
        ops = kb.wait_saved()
        self.assertEqual(ops, [("erase", 0xE800), ("erase", 0xEA00)])
        self.assertEqual(kb.get_rom(KEYMAP_ADDR, 2), [0xFF, 0xFF])

    def test_a_record_without_its_magic_is_not_taken(self):
        """As after a power cut before the last two bytes went in."""
        kb = self.kb()
        kb.set_keycode(0, A[1], A[0], KC_B)
        kb.wait_saved()
        kb.cmd("set mem rom 0x%x 0xff 0xff" % KEYMAP_ADDR)
        kb.reboot()
        kb.ms(20)
        self.assertEqual(kb.get_keycode(0, A[1], A[0]), KC_A)

    def test_a_record_of_other_defaults_is_not_taken(self):
        """The header carries a sum of the built-in keymap: an image of the
        other layout does not take this one's record."""
        kb = self.kb()
        kb.set_keycode(0, A[1], A[0], KC_B)
        kb.wait_saved()
        hdr = kb.get_rom(KEYMAP_ADDR, 8)
        self.assertEqual(hdr[:6], [0x56, 0x4B, LAYERS, ROWS, COLS, 0])
        kb.cmd("set mem rom 0x%x 0x%02x" % (KEYMAP_ADDR + 6, hdr[6] ^ 0x01))
        kb.reboot()
        kb.ms(20)
        self.assertEqual(kb.get_keycode(0, A[1], A[0]), KC_A)

    def test_a_record_with_a_bad_sum_is_written_again(self):
        """A record whose sum went bad is not taken, and setting the same
        keymap again writes it whole (the flash matching is not enough)."""
        kb = self.kb()
        kb.set_keycode(0, A[1], A[0], KC_B)
        kb.wait_saved()
        sum_at = KEYMAP_ADDR + 8 + LAYERS * ROWS * COLS * 2
        lo = kb.get_rom(sum_at, 1)[0]
        kb.cmd("set mem rom 0x%x 0x%02x" % (sum_at, lo ^ 0xFF))
        kb.reboot()
        kb.ms(20)
        self.assertEqual(kb.get_keycode(0, A[1], A[0]), KC_A)
        kb.set_keycode(0, A[1], A[0], KC_B)
        self.assertIn(("program", KEYMAP_ADDR + 1), kb.wait_saved())
        kb.reboot()
        kb.ms(20)
        self.assertEqual(kb.get_keycode(0, A[1], A[0]), KC_B)

    def test_the_other_layouts_record_is_not_taken(self):
        """The record an image of the other layout saved (copied into this
        one's flash, as after writing the other image over a kept record)."""
        other = VIA_ANSI_FW if _layout(self.FW) == "usjis" else VIA_FW
        donor = self.kb(other)
        donor.set_keycode(0, A[1], A[0], KC_B)
        donor.wait_saved()
        rec = donor.get_rom(KEYMAP_ADDR, 8 + LAYERS * ROWS * COLS * 2 + 2)
        self.assertEqual(rec[:2], [0x56, 0x4B])
        kb = self.kb()
        for i in range(0, len(rec), 16):
            kb.cmd("set mem rom 0x%x " % (KEYMAP_ADDR + i) + " ".join("0x%02x" % b for b in rec[i:i + 16]))
        kb.reboot()
        kb.ms(20)
        self.assertEqual(kb.get_keycode(0, A[1], A[0]), KC_A)
        self.assertEqual(kb.get_rom(KEYMAP_ADDR, 2), [0x56, 0x4B], "left in flash until a change is saved")

    def test_unchanged_keymap_is_not_written(self):
        kb = self.kb()
        kb.set_keycode(0, A[1], A[0], KC_A)      # the same keycode: nothing to save
        n = len(kb.flash_ops())
        kb.ms(1500)
        self.assertEqual(kb.flash_ops()[n:], [])

    def test_modified_keys(self):
        """LCTL(KC_C), RSFT(KC_A): the modifiers go out with the key only."""
        kb = self.kb()
        for kc, mods, key in ((0x0100 | 0x06, 0x01, 0x06), (0x1200 | KC_A, 0x20, KC_A), (0x0F00 | KC_Q, 0x0F, KC_Q)):
            with self.subTest(keycode=hex(kc)):
                kb.set_keycode(0, A[1], A[0], kc)
                kb.press(A, ms=40)
                self.assertEqual(self.last_report(kb)[:3], [mods, 0, key])
                kb.release(A, ms=40)
                self.assertEqual(self.last_report(kb), [0] * 8)

    def test_mod_tap(self):
        """LCTL_T(KC_A): a tap types a, a hold is Left Ctrl (both images)."""
        kb = self.kb()
        kb.set_keycode(0, A[1], A[0], 0x2000 | 0x0100 | KC_A)
        n = len(kb.ep1_reports())
        kb.press(A, ms=40)
        kb.release(A, ms=60)
        self.assertEqual([r[2] for r in kb.ep1_reports()[n:]], [KC_A, 0], kb.ep1_reports()[n:])
        n = len(kb.ep1_reports())
        kb.press(A, ms=2000)
        self.assertEqual(kb.ep1_reports()[n:][:1], [[0x01, 0, 0, 0, 0, 0, 0, 0]])
        kb.release(A, ms=60)
        self.assertEqual(self.last_report(kb), [0] * 8)

    def test_a_layer_key_past_the_keymap_sends_nothing(self):
        """MO(5) from VIA: the layer does not exist; keys under it are KC_NO."""
        kb = self.kb()
        kb.set_keycode(0, ESC[1], ESC[0], QK_MOMENTARY | 5)
        kb.press(ESC, ms=40)
        n = len(kb.ep1_reports())
        kb.press(A, ms=40)
        self.assertTrue(all(r[2] == 0 for r in kb.ep1_reports()[n:]), kb.ep1_reports()[n:])
        kb.release(A, ESC, ms=40)
        kb.press(A, ms=40)
        self.assertEqual(self.last_report(kb)[2], KC_A)
        kb.release(A, ms=40)

    def test_layer_key_moved_while_held(self):
        """VIA moves the layer key while it is held: its release still ends
        the layer, and A types a afterwards."""
        kb = self.kb()
        fn = FN[_layout(self.FW)]
        kb.press(fn, ms=40)
        kb.set_keycode(0, fn[1], fn[0], KC_B)          # Fn's position is now a plain key
        kb.set_keycode(1, fn[1], fn[0], KC_B)
        kb.release(fn, ms=40)
        kb.press(A, ms=40)
        self.assertEqual(self.last_report(kb)[2], KC_A, "back on the base layer")
        kb.release(A, ms=40)
        kb.press(fn, ms=40)
        self.assertEqual(self.last_report(kb)[2], KC_B, "the moved position types its new key")
        kb.release(fn, ms=40)

    def test_fn_s_with_fn_elsewhere_on_the_mac_layer(self):
        """Fn + S switches to the Mac base layer, where VIA put another key
        at Fn's position: releasing Fn still ends the Fn layer."""
        kb = self.kb()
        fn = FN[_layout(self.FW)]
        S_KEY = (2, 2)
        kb.set_keycode(1, fn[1], fn[0], KC_B)
        kb.press(fn, ms=40)
        kb.tap(S_KEY, hold=40, after=40)               # Fn + S: Mac mode
        kb.release(fn, ms=40)
        kb.press(A, ms=40)
        self.assertEqual(self.last_report(kb)[2], KC_A, "the Fn layer ended")
        kb.release(A, ms=40)
        kb.press(fn, ms=40)
        self.assertEqual(self.last_report(kb)[2], KC_B, "Mac base layer: B at Fn's position")
        kb.release(fn, ms=40)

    def test_overlapping_modified_keys(self):
        """Two modified keys with the same modifier: releasing one keeps the
        modifier for the other (each key's modifiers are its own)."""
        kb = self.kb()
        kb.set_keycode(0, A[1], A[0], 0x0100 | 0x06)      # LCTL(KC_C)
        kb.set_keycode(0, V[1], V[0], 0x0100 | 0x19)      # LCTL(KC_V)
        kb.press(A, ms=40)
        kb.press(V, ms=40)
        kb.release(A, ms=40)
        r = self.last_report(kb)
        self.assertEqual(r[0], 0x01, "Ctrl stays for the V still held")
        self.assertEqual([k for k in r[2:] if k], [0x19])
        kb.release(V, ms=40)
        self.assertEqual(self.last_report(kb), [0] * 8)

    def test_modified_key_into_a_full_report(self):
        """Six keys held, a modified key pressed: its key does not fit the
        report, and its modifier is not sent either."""
        kb = self.kb()
        held = [(c, 1) for c in range(1, 7)]               # Q W E R T Y
        kb.set_keycode(0, A[1], A[0], 0x0200 | KC_A)       # LSFT(KC_A)
        for k in held:
            kb.press(k, ms=30)
        before = self.last_report(kb)
        self.assertEqual(sum(1 for k in before[2:] if k), 6)
        kb.press(A, ms=40)
        self.assertEqual(self.last_report(kb), before, "no Shift for the six keys held")
        kb.release(A, ms=40)
        for k in held:
            kb.release(k, ms=30)
        self.assertEqual(self.last_report(kb), [0] * 8)

    def test_same_key_with_other_modifiers(self):
        """LCTL(KC_C) and LSFT(KC_C) on two keys: both held send Ctrl+Shift+C;
        releasing one leaves C with the other's modifier."""
        kb = self.kb()
        kb.set_keycode(0, A[1], A[0], 0x0100 | 0x06)      # LCTL(KC_C)
        kb.set_keycode(0, V[1], V[0], 0x0200 | 0x06)      # LSFT(KC_C)
        kb.press(A, ms=40)
        kb.press(V, ms=40)
        self.assertEqual(self.last_report(kb)[:3], [0x03, 0, 0x06])
        kb.release(V, ms=40)
        self.assertEqual(self.last_report(kb)[:3], [0x01, 0, 0x06], "C with Ctrl, A still held")
        kb.release(A, ms=40)
        self.assertEqual(self.last_report(kb), [0] * 8)

    def test_fn_released_under_a_modified_key(self):
        """A modified key on the Fn layer, Fn released first: the key and its
        modifier go up together; nothing stays held until the key is up."""
        kb = self.kb()
        fn = FN[_layout(self.FW)]
        kb.set_keycode(2, A[1], A[0], 0x0100 | 0x06)      # Win Fn layer: LCTL(KC_C)
        kb.press(fn, ms=40)
        kb.press(A, ms=40)
        self.assertEqual(self.last_report(kb)[:3], [0x01, 0, 0x06])
        kb.release(fn, ms=40)
        self.assertEqual(self.last_report(kb), [0] * 8, "Ctrl went up with the key")
        kb.release(A, ms=40)
        self.assertEqual(self.last_report(kb), [0] * 8)

    def test_caps_indicator_follows_the_base_layer(self):
        """VIA moves Caps Lock on the Mac base layer only: after Fn + S the
        Caps Lock indicator is on the Mac layer's key, after Fn + A back."""
        kb = self.kb()
        km = builtin_keymap(self.FW)
        caps = next((c, r) for r in range(ROWS) for c in range(COLS) if km[0][r][c] == KC_CAPS)
        ind = 8 if _layout(self.FW) == "usjis" else 7    # IND_CAPS (indicators.c; IND_TAB on usjis)
        at = kb._xdata_static("indicators", "ind_col")
        row_at = kb._xdata_static("indicators", "ind_row")

        def cell():
            return kb.get_xram(at + ind)[0], kb.get_xram(row_at + ind)[0]

        kb.set_keycode(1, caps[1], caps[0], KC_A)
        kb.set_keycode(1, A[1], A[0], KC_CAPS)
        self.assertEqual(cell(), (caps[0], caps[1] + 1), "Win base layer")
        fn = FN[_layout(self.FW)]
        kb.press(fn, ms=40)
        kb.tap((2, 2), hold=40, after=40)                # Fn + S: Mac
        kb.release(fn, ms=40)
        self.assertEqual(cell(), (A[0], A[1] + 1), "Mac base layer: Caps Lock where VIA put it")
        kb.press(fn, ms=40)
        kb.tap(A, hold=40, after=40)                     # Fn + A: Win
        kb.release(fn, ms=40)
        self.assertEqual(cell(), (caps[0], caps[1] + 1), "back on the Win base layer")

    def test_settings_reset_chord_resets_the_keymap(self):
        kb = self.kb()
        kb.set_keycode(0, A[1], A[0], KC_B)
        kb.wait_saved()
        fn = FN[_layout(self.FW)]
        kb.press(fn, ms=40)
        kb.press(BSPC, ms=40)
        kb.press(V, ms=40)
        kb.release(V, BSPC, fn, ms=40)
        self.assertEqual(kb.get_keycode(0, A[1], A[0]), KC_A)
        kb.ms(1500)
        self.assertEqual(kb.get_rom(KEYMAP_ADDR, 2), [0xFF, 0xFF], "the record erased")


class TestViaLayerTap(ViaCase):
    """LT(2, KC_SPC) at A, from VIA: a tap types Space; a hold is layer 2 (the
    Win Fn layer, where VIA puts B at S), after the tapping term or when S
    goes down and up while A is held. A released first is a tap, and the keys
    that went down meanwhile come after it, in their order."""

    S_KEY, LSFT_KEY = (2, 2), (0, 3)
    KC_S, KC_SPC = 0x16, 0x2C

    def rig(self, layer=2):
        kb = self.kb()
        kb.set_keycode(0, A[1], A[0], 0x4000 | layer << 8 | self.KC_SPC)
        kb.set_keycode(2, self.S_KEY[1], self.S_KEY[0], KC_B)
        return kb

    def keys(self, kb, n):
        return [r[2] for r in kb.ep1_reports()[n:]]

    def fn_key(self):
        return FN[_layout(self.FW)]

    def test_tap_types_the_key(self):
        kb = self.rig()
        n = len(kb.ep1_reports())
        kb.press(A, ms=40)
        self.assertEqual(self.keys(kb, n), [], "nothing while it is undecided")
        kb.release(A, ms=40)
        self.assertEqual(self.keys(kb, n), [self.KC_SPC, 0])

    def test_held_past_the_tapping_term_is_the_layer(self):
        kb = self.rig()
        n = len(kb.ep1_reports())
        kb.press(A, ms=2000)
        kb.tap(self.S_KEY, hold=40, after=40)
        kb.release(A, ms=40)
        kb.tap(self.S_KEY, hold=40, after=40)
        self.assertEqual(self.keys(kb, n), [KC_B, 0, self.KC_S, 0], "B under the layer, S after it")

    def test_the_tapping_term_alone_makes_it_the_layer(self):
        """A held past the term, then S goes down and stays down: B goes out
        on S's press, before anything comes up (no other event decides)."""
        kb = self.rig()
        n = len(kb.ep1_reports())
        kb.press(A, ms=2000)                          # the term: 285 scans, under 1 s in the simulator
        kb.press(self.S_KEY, ms=60)
        self.assertEqual(self.keys(kb, n), [KC_B], "B on the press")
        kb.release(self.S_KEY, ms=40)
        kb.release(A, ms=40)
        self.assertEqual(self.keys(kb, n), [KC_B, 0])

    def test_held_past_the_term_and_released_alone_sends_nothing(self):
        kb = self.rig()
        n = len(kb.ep1_reports())
        kb.press(A, ms=2000)
        kb.release(A, ms=40)
        self.assertEqual(self.keys(kb, n), [], "the layer, no Space")
        kb.tap(self.S_KEY, hold=40, after=40)
        self.assertEqual(self.keys(kb, n), [self.KC_S, 0], "the layer has ended: S")

    def test_a_key_let_go_while_it_is_undecided_goes_up_at_once(self):
        """D down before A, released while A is undecided: D goes up on the
        host at once, not when A is decided (a fast key repeat would repeat
        it meanwhile). A is decided later by the term."""
        kb = self.rig()
        n = len(kb.ep1_reports())
        kb.press(self.D_KEY, ms=40)
        kb.press(A, ms=40)
        kb.release(self.D_KEY, ms=40)
        self.assertEqual(self.keys(kb, n), [self.KC_D, 0], "D up while A is undecided")
        kb.ms(2000)                                   # A still held, past the term
        kb.tap(self.S_KEY, hold=40, after=40)
        kb.release(A, ms=40)
        self.assertEqual(self.keys(kb, n), [self.KC_D, 0, KC_B, 0], "then the layer")

    def test_a_tap_after_an_early_release(self):
        """D down, A down, D up (at once), A up: the release did not decide A,
        which is a tap of Space."""
        kb = self.rig()
        n = len(kb.ep1_reports())
        kb.press(self.D_KEY, ms=40)
        kb.press(A, ms=40)
        kb.release(self.D_KEY, ms=40)
        kb.release(A, ms=40)
        self.assertEqual(self.keys(kb, n), [self.KC_D, 0, self.KC_SPC, 0])

    def test_a_modified_key_let_go_while_it_is_undecided_waits(self):
        """Shift+1 (a modified key at D) down, A down, S down, Shift+1 up, A
        up: Shift+1's release waits, so Space and S go out with its Shift, as
        in a roll without a layer-tap key."""
        kb = self.rig()
        kb.set_keycode(0, self.D_KEY[1], self.D_KEY[0], 0x0200 | 0x1E)   # LSFT(KC_1)
        kb.press(self.D_KEY, ms=40)
        n = len(kb.ep1_reports())
        kb.press(A, ms=40)
        kb.press(self.S_KEY, ms=40)
        kb.release(self.D_KEY, ms=40)
        self.assertEqual(kb.ep1_reports()[n:], [], "everything waits for A")
        kb.release(A, ms=40)
        downs = {}
        for r in kb.ep1_reports()[n:]:
            for k in r[2:8]:
                if k in (self.KC_SPC, self.KC_S) and k not in downs:
                    downs[k] = r[0]                  # the modifiers when the key first shows
        self.assertEqual(sorted(downs), sorted([self.KC_SPC, self.KC_S]), kb.ep1_reports()[n:])
        self.assertTrue(all(m & 0x02 for m in downs.values()), kb.ep1_reports()[n:])
        kb.release(self.S_KEY, ms=40)

    def test_an_early_release_in_a_replay(self):
        """A (LT) down, F down, D (LT) down, F up: F's release decides A as
        its layer; F's press and D's go on, D is undecided, and F's waiting
        release goes on at once as well, before D is decided."""
        kb = self.rig()
        kb.set_keycode(0, self.D_KEY[1], self.D_KEY[0], 0x4000 | 2 << 8 | self.KC_ENT)
        n = len(kb.ep1_reports())
        kb.press(A, ms=40)
        kb.press(self.F_KEY, ms=40)
        kb.press(self.D_KEY, ms=40)
        kb.release(self.F_KEY, ms=60)
        self.assertEqual(self.keys(kb, n), [self.KC_F, 0], "F down and up, D still undecided")
        kb.release(self.D_KEY, ms=40)
        kb.release(A, ms=40)

    def test_the_settings_reset_chord_with_a_layer_tap_key_in_between(self):
        """Fn held, Backspace down (the chord armed), A (LT) down, V down,
        Backspace up, A up: Backspace's release waits like V's press, so V
        still finds the chord armed and the settings and keymap are reset (A
        is A again)."""
        kb = self.rig()
        fn = self.fn_key()
        kb.press(fn, ms=40)
        kb.press(BSPC, ms=40)
        kb.press(A, ms=40)
        kb.press(V, ms=40)
        kb.release(BSPC, ms=40)
        kb.release(A, ms=40)
        kb.release(V, fn, ms=40)
        self.assertEqual(kb.get_keycode(0, A[1], A[0]), KC_A, "the reset put A back")

    def test_tilde_keeps_its_shift_for_the_keys_that_waited(self):
        """Fn + Right Shift + Esc gives ~ with a Shift of its own; A (LT) down,
        C down, Esc up, A up: Esc's release waits, so C goes out with that
        Shift, as it would without a layer-tap key in the roll."""
        kb = self.rig()
        fn = self.fn_key()
        kb.press(fn, ms=40)
        kb.press(self.RSFT_KEY, ms=40)
        kb.press(ESC, ms=40)
        n = len(kb.ep1_reports())
        kb.press(A, ms=40)
        kb.press(self.C_KEY, ms=40)
        kb.release(ESC, ms=40)
        kb.release(A, ms=40)
        mods = [r[0] for r in kb.ep1_reports()[n:] if self.KC_C in r[2:8]]
        self.assertTrue(mods and mods[0] & 0x02, kb.ep1_reports()[n:])
        kb.release(self.C_KEY, self.RSFT_KEY, fn, ms=40)

    def test_a_key_pressed_and_released_while_held_is_under_the_layer(self):
        kb = self.rig()
        n = len(kb.ep1_reports())
        kb.press(A, ms=40)
        kb.press(self.S_KEY, ms=40)
        self.assertEqual(self.keys(kb, n), [], "S waits while A is undecided")
        kb.release(self.S_KEY, ms=40)
        kb.release(A, ms=40)
        self.assertEqual(self.keys(kb, n), [KC_B, 0])

    def test_released_first_is_a_tap_before_the_keys_that_waited(self):
        kb = self.rig()
        n = len(kb.ep1_reports())
        kb.press(A, ms=40)
        kb.press(self.S_KEY, ms=40)
        kb.release(A, ms=40)
        kb.release(self.S_KEY, ms=40)
        self.assertEqual(self.keys(kb, n), [self.KC_SPC, 0, self.KC_S, 0])

    def test_the_waiting_events_keep_their_order(self):
        """Shift down before A, released while A is undecided after S went
        down: Space and S both go out shifted, then Shift comes up."""
        kb = self.rig()
        kb.press(self.LSFT_KEY, ms=40)
        n = len(kb.ep1_reports())
        kb.press(A, ms=40)
        kb.press(self.S_KEY, ms=40)
        kb.release(self.LSFT_KEY, ms=40)
        kb.release(A, ms=40)
        self.assertEqual([r[:3] for r in kb.ep1_reports()[n:]],
                         [[0x02, 0, self.KC_SPC], [0x02, 0, 0], [0x02, 0, self.KC_S], [0, 0, self.KC_S]])
        kb.release(self.S_KEY, ms=40)
        self.assertEqual(self.last_report(kb), [0] * 8)

    D_KEY, F_KEY, RSFT_KEY, N3_KEY, QUOT_KEY, TAB_KEY = (3, 2), (4, 2), (13, 3), (3, 0), (11, 2), (0, 1)
    KC_ENT, KC_TRNS, KC_D, KC_F, KC_C = 0x28, 0x01, 0x07, 0x09, 0x06
    C_KEY = (3, 3)
    ROW2 = [(c, 2) for c in range(2, 11)]          # S D F G H J K L ;

    def test_two_layer_tap_keys_rolled_both_tap(self):
        """LT(2, KC_ENT) at D too: A down, D down, A up, D up types Space then Enter."""
        kb = self.rig()
        kb.set_keycode(0, self.D_KEY[1], self.D_KEY[0], 0x4000 | 2 << 8 | self.KC_ENT)
        n = len(kb.ep1_reports())
        kb.press(A, ms=40)
        kb.press(self.D_KEY, ms=40)
        kb.release(A, ms=40)
        kb.release(self.D_KEY, ms=40)
        self.assertEqual(self.keys(kb, n), [self.KC_SPC, 0, self.KC_ENT, 0])

    def test_a_layer_tap_key_tapped_while_another_waits(self):
        """D (LT) tapped while A (LT) waits: A becomes its layer, D is a tap of
        Enter, and A's layer goes on until A comes up."""
        kb = self.rig()
        kb.set_keycode(0, self.D_KEY[1], self.D_KEY[0], 0x4000 | 2 << 8 | self.KC_ENT)
        n = len(kb.ep1_reports())
        kb.press(A, ms=40)
        kb.tap(self.D_KEY, hold=40, after=40)
        kb.tap(self.S_KEY, hold=40, after=40)
        kb.release(A, ms=40)
        kb.tap(self.S_KEY, hold=40, after=40)
        self.assertEqual(self.keys(kb, n), [self.KC_ENT, 0, KC_B, 0, self.KC_S, 0])

    def test_a_ninth_waiting_event_makes_it_the_layer(self):
        kb = self.rig()
        for col, row in self.ROW2:
            kb.set_keycode(2, row, col, self.KC_TRNS)
        n = len(kb.ep1_reports())
        kb.press(A, ms=40)
        for key in self.ROW2[:8]:
            kb.press(key, ms=20)
        self.assertEqual(self.keys(kb, n), [], "eight events wait")
        kb.press(self.ROW2[8], ms=40)
        self.assertTrue(kb.ep1_reports()[n:], "the ninth decides it")
        self.assertNotIn(self.KC_SPC, self.keys(kb, n), "the layer, not a tap")
        kb.release(*self.ROW2, ms=40)
        kb.release(A, ms=40)
        self.assertEqual(self.last_report(kb), [0] * 8)

    def test_a_layer_tap_key_under_fn(self):
        """Fn (MO) held: the layer-tap key's tap still types Space, and Fn's
        layer stays."""
        kb = self.rig()
        fn = self.fn_key()
        n = len(kb.ep1_reports())
        kb.press(fn, ms=40)
        kb.tap(A, hold=40, after=40)
        kb.tap(self.S_KEY, hold=40, after=40)
        kb.release(fn, ms=40)
        self.assertEqual(self.keys(kb, n), [self.KC_SPC, 0, KC_B, 0])

    def test_stack_through_a_replay_with_usb_nested(self):
        """The deepest key path: A waits with the key left of Space, S .. H and
        Right Shift behind it; 3 (under the Fn layer with Right Shift: Alt+Tab,
        a key of its own through process_keycode) goes down and up and decides
        it. A GET_DESCRIPTOR is fired at every EP1 send while the waiting keys
        go out, so the USB interrupt nests at the deepest point. The stack
        keeps 16 bytes free."""
        kb = self.rig()
        for col, row in self.ROW2[:5]:
            kb.set_keycode(2, row, col, self.KC_TRNS)
        kb.press(A, ms=20)
        for key in [(2, 4)] + self.ROW2[:5] + [self.RSFT_KEY]:
            kb.press(key, ms=15)
        kb.press(self.N3_KEY, ms=20)
        kb.paint_stack()
        kb.keys.release(*self.N3_KEY)
        send = kb._a("usb_hw_ep1_in_send")
        tick = kb._a("pwm4_ms_tick_interrupt_handler")
        kb.cmd("break 0x%x" % send)
        kb.cmd("break 0x%x 300" % tick)
        sends = 0
        try:
            for _ in range(400):
                if kb.stopped_at(kb.run()) != send:
                    break
                sends += 1
                kb.cmd("set mem xram 0x1100 0x80 0x06 0x00 0x01 0x00 0x00 0x40 0x00")
                kb.set_sfr(0x92, 0x10)                 # USBIF1.SETUPIF: GET_DESCRIPTOR(device)
        finally:
            kb.cmd("delete")
        self.assertGreater(sends, 3, "the waiting keys went out")
        used = kb.stack_highwater()
        room = kb.STACK_TOP - kb.stack_base
        print(f"\n[via] layer-tap replay: stack {used}/{room} bytes")
        self.assertLess(used, room - 16, "less than 16 bytes of stack left")
        kb.release(A, (2, 4), *self.ROW2[:5], self.RSFT_KEY, ms=40)

    def test_stack_through_a_replay_over_the_radio(self):
        """The same replay in the 2.4 GHz position, with US-JIS on in the usjis
        image: the reports go into the radio queue (a deeper chain than the
        USB send), through the US-JIS substitution and the Right Shift hook.
        The stack keeps 16 bytes free."""
        kb = self.rig()
        for col, row in self.ROW2[:4]:
            kb.set_keycode(2, row, col, self.KC_TRNS)
        kb.switch(SWITCH_24G, 200)
        kb.status(0, 3)
        kb.ms(700)
        if _layout(self.FW) == "usjis":
            kb.press(self.fn_key(), ms=20)
            kb.tap(self.TAB_KEY, hold=40, after=40)    # Fn + Tab: US-JIS on
            kb.release(self.fn_key(), ms=40)
            usjis = kb.get_xram(kb._a("user_settings") + 9)[0]
            self.assertEqual(usjis, 1, "US-JIS on")
        m = kb.mark()
        kb.press(A, ms=20)
        # seven presses wait (the queue holds eight), so 3's release, after
        # the stack is painted, decides A and replays them
        for key in [(2, 4)] + self.ROW2[:4] + [self.QUOT_KEY, self.RSFT_KEY]:
            kb.press(key, ms=15)
        kb.press(self.N3_KEY, ms=20)
        kb.paint_stack()
        kb.release(self.N3_KEY, ms=300)
        sent = [f for f in kb.frames(m) if len(f) > 1 and f[1] == 0x02]
        self.assertGreater(len(sent), 3, "the waiting keys went out over the radio")
        used = kb.stack_highwater()
        room = kb.STACK_TOP - kb.stack_base
        print(f"\n[via] layer-tap replay over 2.4 GHz: stack {used}/{room} bytes")
        self.assertLess(used, room - 16, "less than 16 bytes of stack left")
        kb.release(A, (2, 4), *self.ROW2[:4], self.QUOT_KEY, self.RSFT_KEY, ms=40)

    def test_a_layer_past_the_keymap_sends_nothing_when_held(self):
        kb = self.rig(layer=5)
        n = len(kb.ep1_reports())
        kb.press(A, ms=2000)
        kb.tap(self.S_KEY, hold=40, after=40)
        kb.release(A, ms=40)
        self.assertTrue(all(r[2] == 0 for r in kb.ep1_reports()[n:]), kb.ep1_reports()[n:])
        kb.tap(A, hold=40, after=40)
        self.assertEqual(self.keys(kb, n)[-2:], [self.KC_SPC, 0], "a tap still types Space")


class TestViaLayerTapAnsi(TestViaLayerTap):
    FW = VIA_ANSI_FW


class TestViaLimits(ViaCase):
    def test_stack_and_watchdog_through_a_keymap_load_and_save(self):
        """VIA's commands run in the main loop (usb_task -> via_process ->
        the keymap -> the indicators' re-find), the save there too; the USB
        and LED interrupts come on top. The stack keeps 16 bytes free and the
        longest stretch without a watchdog kick stays under 3 ms."""
        kb = self.kb()
        kb.paint_stack()
        kb.radio.clear_kick_gap()
        size = LAYERS * ROWS * COLS * 2
        buf = []
        for off in range(0, size, 28):
            buf += kb.get_buffer(off, min(28, size - off))
        for off in range(0, size, 28):
            chunk = buf[off:off + 28]
            chunk[1::2] = [b ^ 0x01 for b in chunk[1::2]]      # every keycode changed
            kb.set_buffer(off, chunk)
            kb.nest_usb_descriptor()
        kb.via(0x02, 0x03, 0)
        ops = kb.wait_saved()
        self.assertIn(("program", KEYMAP_ADDR + 1), ops)
        used = kb.stack_highwater()
        room = kb.STACK_TOP - kb.stack_base
        gap = kb.radio.kick_gap_ms()
        print(f"\n[via] stack {used}/{room} bytes, longest kick gap {gap:.2f} ms")
        self.assertLess(used, room - 16, "less than 16 bytes of stack left")
        self.assertLess(gap, 3.0)


class TestViaReports(ViaCase):
    def test_keyboard_report_carries_id_3(self):
        kb = self.kb()
        kb.press(A, ms=40)
        p = kb.ep1_packets()[-1]
        self.assertEqual(p, [REPORT_ID_KEYBOARD, 0, 0, KC_A, 0, 0, 0, 0, 0])
        kb.release(A, ms=40)

    def test_boot_protocol_gives_the_bare_report(self):
        kb = self.kb()
        kb.setup_packet([0x21, 0x0B, 0x00, 0x00, 0x01, 0x00, 0x00, 0x00])   # SET_PROTOCOL(boot), interface 1
        kb.press(A, ms=40)
        self.assertEqual(kb.ep1_packets()[-1], [0, 0, KC_A, 0, 0, 0, 0, 0])
        kb.release(A, ms=40)

    def test_boot_protocol_sends_no_media_report(self):
        """A boot host reads every EP1 report as the keyboard's: the consumer
        report goes out under the report protocol only."""
        kb = self.kb()
        kb.set_keycode(0, A[1], A[0], KC_MUTE)
        kb.tap(A, hold=40, after=40)
        self.assertIn([REPORT_ID_CONSUMER, CONSUMER_MUTE, 0x00], kb.ep1_packets())
        kb.setup_packet([0x21, 0x0B, 0x00, 0x00, 0x01, 0x00, 0x00, 0x00])   # SET_PROTOCOL(boot), interface 1
        n = len(kb.ep1_packets())
        kb.tap(A, hold=40, after=40)
        self.assertEqual([p for p in kb.ep1_packets()[n:] if len(p) != 8], [], kb.ep1_packets()[n:])

    def test_set_configuration_during_an_answer(self):
        """SET_CONFIGURATION while usb_task() answers: EP2 OUT stays unarmed
        until the answer is out (a report then would land in via_buf)."""
        kb = self.kb()
        before = len(kb.ep2_in())
        kb.deliver(0x11)
        kb.brk(kb._a("via_process"))
        kb.run()
        kb.cmd("delete")
        kb.cmd("set mem xram 0x1100 0x00 0x09 0x01 0x00 0x00 0x00 0x00 0x00")
        kb.set_sfr(0x92, 0x10)                               # SETUPIF: taken during via_process
        kb.brk(kb._a("usb_hw_ep2_in_send"))
        kb.run()
        kb.cmd("delete")
        self.assertFalse(kb.ep2_out_armed(), "armed while the answer is being made")
        kb.ms(5, until=lambda s: len(kb.ep2_in()) > before and kb.ep2_out_armed())
        self.assertEqual(kb.ep2_in()[-1][:2], [0x11, LAYERS])
        self.assertTrue(kb.ep2_out_armed())

    def test_led_report_with_and_without_id(self):
        kb = self.kb()
        st = kb.led_report([0x21, 0x09, REPORT_ID_KEYBOARD, 0x02, 0x01, 0x00, 0x02, 0x00], [REPORT_ID_KEYBOARD, 0x02])
        self.assertEqual(st & 0x1F, 0x02, "Caps Lock from the report with its ID")
        st = kb.led_report([0x21, 0x09, 0x00, 0x02, 0x01, 0x00, 0x01, 0x00], [0x00])
        self.assertEqual(st & 0x1F, 0x00, "the bare byte (boot protocol)")


class TestViaAnsi(TestViaKeymap):
    FW = VIA_ANSI_FW


if __name__ == "__main__":
    unittest.main()
