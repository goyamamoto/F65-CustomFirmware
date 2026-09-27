/*
 * uCsim CPU variant: SinoWealth SH68F90  (-t sh68f90)
 *
 * An 8052 core (cl_uc52) plus a single cl_hw peripheral model (cl_sh68f90_sie)
 * that emulates the chip-specific blocks the NuPhy Air60 stock firmware drives:
 *   - USB SIE      : EP0 control/enumeration, EP1/EP2 IN report endpoints
 *   - Key matrix   : pin-level (P7/P5 rows, P5/P3/P2/P1 cols), NKRO
 *   - BK3632 SPI   : bit-bang ACK (P4.2) + MISO status + connection state
 *   - Flash ISP    : IB_CON/XPAGE/IB_OFFSET/IB_DATA erase+program into code space
 *   - Sleep/wake   : PCON power-down + INT4 (EXF1) wake
 *   - Watchdog     : RSTSTAT kick + timeout reset
 * plus a cl_sh68f90_interrupt that registers the SH68F90's (remapped) interrupt
 * vectors instead of the standard 8051 INT0/INT1.
 *
 * This replaces the old approach of patching the shared s51 core files
 * (interrupt.cc / uc51.cc): everything chip-specific now lives here, and the CPU
 * is registered as a normal uCsim variant (cpus_51[] + sim51.cc factory).
 */
#include <stdio.h>

#include "globals.h"
#include "regs51.h"
#include "dregcl.h"
#include "portcl.h"
#include "timer2cl.h"
#include "interruptcl.h"
#include "itsrccl.h"

#include "uc52cl.h"
#include "sh68f90cl.h"

#include <deque>

/* An interrupt source that knows which enable register (IEN0 / IEN1) it hangs
 * off, so the SH68F90 priority registers can be applied to it: IPL0/IPH0 for
 * the IEN0 sources, IPL1/IPH1 for the IEN1 sources (same bit positions). */
#define MODEL_VERSION 8

// The pin model bits (xram 0x1f13) as last written by a test, for the PWM model
// too (xram itself is not cleared at start, so its raw value is not used).
static t_mem sh_pin_model = 0;

class cl_sh_it_src : public cl_it_src
{
   public:
    t_addr ien_addr;
    cl_sh_it_src(cl_uc *Iuc, int Inuof, class cl_memory_cell *Iie_cell, t_addr Iien_addr, t_mem Iie_mask,
                 class cl_memory_cell *Isrc_cell, t_mem Isrc_mask, t_addr Iaddr, bool Iclr_bit, const char *Iname,
                 int apoll_priority)
        : cl_it_src(Iuc, Inuof, Iie_cell, Iie_mask, Isrc_cell, Isrc_mask, Iaddr, Iclr_bit, false, Iname, apoll_priority),
          ien_addr(Iien_addr)
    {
    }
    // Any flag bit under the mask requests (uCsim's default wants the masked
    // value to equal the whole mask, which only matters for INT4: its eight
    // sub-flags IF40-IF47 share one vector).
    virtual bool pending(void)
    {
        return src_cell && (src_cell->get() & src_mask) != 0;
    }
};

/* ===================================================================== *
 *  SH68F90 peripheral model (USB SIE + matrix + BK3632 + flash + power)  *
 * ===================================================================== */
class cl_sh68f90_sie : public cl_hw
{
    class cl_address_space *xram, *sfr, *iram, *rom;
    class cl_memory_cell   *cell_ep0con, *cell_usbif2, *cell_iep0cnt;
    class cl_memory_cell   *cell_ep1con, *cell_iep1cnt;
    class cl_memory_cell   *cell_ep2con, *cell_iep2cnt;
    class cl_memory_cell   *cell_pllcon;
    class cl_memory_cell   *cell_sbuf, *cell_scon;
    class cl_memory_cell   *cell_p1, *cell_p2, *cell_p3, *cell_p5, *cell_p7;
    class cl_memory_cell   *cell_p0, *cell_p4;
    class cl_memory_cell   *cell_ibcon5, *cell_rststat, *cell_pcon;
    unsigned                pwm_acc;
    // Opt-in Timer2 at its real rate (xram 0x1f52 bit 0, set by a test): the scan
    // tick then comes when Timer2, counting at Fsys/12 from its reload value
    // (RCAP2), overflows, instead of every 30000 cycles; TH2/TL2 read as that
    // count (uCsim's own Timer2, which counts every cycle, is switched off). Off
    // by default: the other tests keep the calibrated tick.
    class cl_memory_cell *cell_t2mode, *cell_rcap2l, *cell_tl2, *cell_th2;
    bool                  t2_real;
    unsigned              t2_count, t2_sub;
    // Model 8, real-rate Timer2 only: TF2 (T2CON bit 7) is the real flag - an
    // overflow sets it, only the firmware clears it, and the interrupt request
    // follows it (so an overflow while ET2 is off waits, and one that comes
    // after the handler cleared TF2 is another interrupt). xram 0x1f58/0x1f59
    // (16 bits): after every arm (an RCAP2L write) Timer2 starts counting only
    // that many cycles later. The tests sweep the phase between the code and
    // the Timer2 grid with it, since the real chip's instruction timing (and so
    // where the matrix scan ends on that grid) is not the simulator's.
    class cl_memory_cell *cell_t2adj, *cell_t2adj_hi;
    unsigned              t2_arm_delay, t2_hold;
    int                     in_packets;
    bool                    rf_ack_toggle; // BK3632 SPI: P4.2 ACK flips each pin-read so the
                                           // firmware's "wait for ACK to change" poll matches.
    int      miso_bitpos;                  // BK3632 SPI: bit cursor into the 4-byte status reply.
    unsigned wdt_acc;                      // watchdog: cycles since last RSTSTAT(0xb1) kick.
    unsigned wdt_gap_max;                  // the longest stretch between two kicks (xram 0x1f28..0x1f2b)
    unsigned last_kick_pc;                 // code address of the last kick (a new longest gap >= 1.5 ms is logged)
    class cl_memory_cell *cell_kickgap, *cell_kickgap_hi; // xram 0x1f28/29: the maximum; write 0x1f28 to clear
    class cl_memory_cell *cell_usbcon;     // USBCON: D+ pull-up (SW1CON) changes are logged
    // Test holds (model 7): xram 0x1f53 != 0 = the host takes no EP1 IN report
    // (IEP1RDY stays set until the test writes 0 there, which delivers the last
    // one); xram 0x1f54 != 0 = the PLL never reports lock (PLLSTA stays 0).
    // (kept in the model, not read back from xram: xram is not cleared at start)
    class cl_memory_cell *cell_ep1hold, *cell_pllhold;
    bool                  ep1_hold, pll_hold;
    bool     wdt_armed;                    // watchdog only enforced after the firmware kicks once.

    // External level present on each port's pins (what the board drives). An input
    // pin reads this, not its output latch; it idles high (== the internal pull-up
    // a real input enables via PxPCR). The board model (tests/) pulls bits low via
    // the staging cells registered in init(); nothing board-specific lives here.
    t_mem                 pin_ext[8];
    class cl_memory_cell *cell_pinext_p5, *cell_pinext_p7;
    // Bit cells for the bit-addressable input pins of P5/P7. Bit reads bypass the
    // byte read() operator, so we hook these too (as cl_port does) -- otherwise a
    // `MOV C,P5.5` (CONN_MODE switch) reads the latch instead of the pin level.
    class cl_memory_cell *p5_bit[8], *p7_bit[8];

    // P0 / P4 external pins (the F65's connection switch P0.4/P0.5, radio TX line
    // P0.2, radio ready line P4.7, USB power P4.4). Off by default so the boards
    // whose tests expect P0/P4 to read their latches are unchanged: a test turns
    // them on by writing a mask to the staging cell 0x1f13 (bit0 = P0, bit4 = P4;
    // bit1 = key contacts, see contact_mask(); bit7 = PWM0 / Timer2 timed from
    // their registers, see tick()).
    class cl_memory_cell *cell_p0cr, *cell_pinext_p0, *cell_pinext_p4, *cell_pinmodel;
    class cl_memory_cell *p0_bit[8], *p4_bit[8];
    t_mem                 pin_model;

    // EUART0 with byte timing. One byte (start + 8 data + stop) takes
    // uart_byte_cycles machine cycles (default 920 = 10 bits at Fsys/92, taking a
    // machine cycle as one Fsys clock of the 1T core). TX: an SBUF write starts a
    // byte, TI rises when it has gone out; a write while a byte is going out sets
    // TXCOL and is dropped. RX: bytes the test queues (staging cells 0x1f20 normal,
    // 0x1f21 with a framing error) arrive back to back while SCON.REN is set; a
    // byte that completes while RI is still set is lost and sets RXOV. With
    // PCON.SSTAT set, SCON[7:5] read as FE/RXOV/TXCOL and the mode bits are kept
    // aside (the SH68F90 behaviour smk's and the stock firmware rely on).
    class cl_memory_cell   *cell_rxpush, *cell_rxpush_fe, *cell_bytecyc_lo, *cell_bytecyc_hi, *cell_rxcount;
    // xram 0x1f2f reads the model's version, so a test can tell an older
    // simulator from one with the features it needs: 1 = EUART0, P0/P4 pins,
    // PWM4; 2 = register-timed PWM0/Timer2; 3 = key contacts, INT4 wake from
    // power-down (0x1f25); 4 = the USB interrupt on RESMIF / SUSPIF too;
    // 5 = and on PUPIF / USBRSTIF; 6 = the longest watchdog-kick gap
    // (0x1f28) and the D+ pull-up logged; 7 = the PWM banks (cl_sh68f90_pwm)
    // with the LED trace (0x1f50 / 0x1f51, PWM0 period on 0x1f0b), the
    // opt-in real-rate Timer2 (0x1f52) and the dual DPTR; the EP1 IN hold
    // (0x1f53), the PLL lock hold (0x1f54), "[SIE] FLASH <op> <addr> kick
    // <cycles since the last watchdog kick>" on every flash operation, and
    // "[SIE] KICKGAP ..." for each new longest kick gap of 1.5 ms or more;
    // 8 = the PWM trace's [PWMC]-only mode (0x1f50 bit 2), the real-rate
    // Timer2's TF2 as a real flag and its arm delay (0x1f58/0x1f59).
    class cl_memory_cell   *cell_modelver;
    std::deque<unsigned>    rx_queue; // byte | 0x100 = framing error
    unsigned                uart_byte_cycles;
    unsigned                tx_left, rx_left;
    bool                    tx_busy, rx_busy;
    unsigned                rx_cur;
    t_mem                   scon_mode_saved;
    bool                    sstat;
    unsigned long long      now; // machine cycles since start (log timestamps)
    unsigned                pwm4_acc; // PWM4 period timer (the AULA F65 1 ms tick)
    unsigned                t2_left;  // register timing: cycles until the Timer2 one-shot
    bool                    t2_was_on;
    int                     p02_level;

   public:
    cl_sh68f90_sie(class cl_uc *auc) : cl_hw(auc, HW_DUMMY, 0, "sh68f90_sie")
    {
        xram = sfr = iram = rom = 0;
        cell_ibcon5 = cell_rststat = cell_pcon = 0;
        wdt_acc                                = 0;
        wdt_armed                              = false;
        cell_ep0con = cell_usbif2 = cell_iep0cnt = 0;
        cell_ep1con = cell_iep1cnt = 0;
        cell_ep2con = cell_iep2cnt = 0;
        cell_pllcon                = 0;
        cell_sbuf = cell_scon = 0;
        cell_p1 = cell_p2 = cell_p3 = cell_p5 = cell_p7 = 0;
        cell_p0 = cell_p4 = 0;
        pwm_acc           = 0;
        cell_t2mode = cell_rcap2l = cell_tl2 = cell_th2 = 0;
        t2_real                   = false;
        t2_count = t2_sub = 0;
        cell_t2adj = cell_t2adj_hi = 0;
        t2_arm_delay = t2_hold = 0;
        in_packets        = 0;
        rf_ack_toggle     = false;
        miso_bitpos       = 0;
        for (int i = 0; i < 8; i++)
            pin_ext[i] = 0xff; // pins idle high (pull-ups)
        cell_pinext_p5 = cell_pinext_p7 = 0;
        // P4.4 (the F65's USB-power sense) idles LOW: a bit read of P4.4 always
        // reads the pin (see read()), and with nothing staged there is no USB power.
        pin_ext[4] &= ~0x10;
        for (int i = 0; i < 8; i++)
            p5_bit[i] = p7_bit[i] = 0;
        cell_p0cr = cell_pinext_p0 = cell_pinext_p4 = cell_pinmodel = 0;
        for (int i = 0; i < 8; i++)
            p0_bit[i] = p4_bit[i] = 0;
        pin_model  = 0;
        cell_rxpush = cell_rxpush_fe = cell_bytecyc_lo = cell_bytecyc_hi = cell_rxcount = 0;
        cell_modelver = 0;
        wdt_gap_max   = 0;
        last_kick_pc  = 0;
        cell_kickgap  = cell_kickgap_hi = 0;
        cell_usbcon   = 0;
        cell_ep1hold = cell_pllhold = 0;
        ep1_hold = pll_hold = false;
        uart_byte_cycles = 920;
        tx_left = rx_left = 0;
        tx_busy = rx_busy = false;
        rx_cur          = 0;
        scon_mode_saved = 0;
        sstat           = false;
        now             = 0;
        p02_level       = -1;
        pwm4_acc        = 0;
        t2_left         = 0;
        t2_was_on       = false;
    }
    virtual int init(void)
    {
        cl_hw::init();
        xram = uc->address_space("xram");
        sfr  = uc->address_space(MEM_SFR_ID);
        iram = uc->address_space("iram");
        rom  = uc->address_space("rom"); // code/flash space (for ISP erase/program)
        if (sfr) {
            cell_ep0con  = register_cell(sfr, 0x97); // EP0CON
            cell_usbif2  = sfr->get_cell(0x93);      // USBIF2
            cell_iep0cnt = sfr->get_cell(0x9b);      // IEP0CNT (IN byte count)
            cell_ep1con  = register_cell(sfr, 0x99); // EP1CON (keyboard report endpoint)
            cell_iep1cnt = sfr->get_cell(0x9c);      // IEP1CNT
            cell_ep2con  = register_cell(sfr, 0x9a); // EP2CON (IF1 multiplexed IN: NKRO etc.)
            cell_iep2cnt = sfr->get_cell(0x9d);      // IEP2CNT
            cell_pllcon  = register_cell(sfr, 0xbc); // PLLCON (clock PLL)
            cell_sbuf    = register_cell(sfr, 0xaa); // SBUF (real UART TX data)
            cell_scon    = sfr->get_cell(0xd8);      // SCON (TI = bit1)
            // GPIO port cells (plain MCU ports). What's wired to them -- the key
            // matrix columns/rows, the BK3632 -- is board-level and lives test-side.
            cell_p1      = sfr->get_cell(0x90);
            cell_p2      = sfr->get_cell(0x98);
            cell_p3      = sfr->get_cell(0xa0);
            cell_p5      = register_cell(sfr, 0x88); // P5: cols C0-2 + rows R3-R4
            cell_p7      = register_cell(sfr, 0xf8); // P7: rows R0-R2 (bits 1-3)
            cell_p0      = register_cell(sfr, 0x80); // P0: BK3632 MISO=P0.6, MOSI=P0.7, MOT=P0.5
            cell_p4      = register_cell(sfr, 0xb0); // P4: BK3632 SCK=P4.7, ACK=P4.2
            cell_rcap2l  = register_cell(sfr, 0xca); // RCAP2L: the firmware (re)arms Timer2 here
            cell_tl2     = register_cell(sfr, 0xcc); // TL2 / TH2: the real-rate Timer2 count
            cell_th2     = register_cell(sfr, 0xcd);
            cell_ibcon5  = register_cell(sfr, 0xf6); // IB_CON5: flash ISP commit (write 0x06)
            cell_rststat = register_cell(sfr, 0xb1); // RSTSTAT: watchdog kick (write 0)
            cell_usbcon  = register_cell(sfr, 0x91); // USBCON: log the D+ pull-up
            cell_pcon    = register_cell(sfr, 0x87); // PCON: bit1 -> sleep/power-down
            cell_p0cr    = register_cell(sfr, 0xe1); // P0CR: P0.2 direction (radio TX line)
            cell_scon    = register_cell(sfr, 0xd8); // SCON: SSTAT flag view
        }
        if (xram) {
            // Staging for the external pin levels of P5 / P7 (the ports the firmware
            // reads as inputs). The board model writes these; read() applies them to
            // the input bits. Outside the firmware's xram window, so they never alias
            // real data.
            cell_pinext_p5 = register_cell(xram, 0x1f15);
            cell_pinext_p7 = register_cell(xram, 0x1f17);
            cell_pinext_p0 = register_cell(xram, 0x1f10);
            cell_pinext_p4 = register_cell(xram, 0x1f14);
            cell_pinmodel  = register_cell(xram, 0x1f13);
            cell_rxpush     = register_cell(xram, 0x1f20);
            cell_rxpush_fe  = register_cell(xram, 0x1f21);
            cell_bytecyc_lo = register_cell(xram, 0x1f22);
            cell_bytecyc_hi = register_cell(xram, 0x1f23);
            cell_rxcount    = register_cell(xram, 0x1f24);
            cell_modelver   = register_cell(xram, 0x1f2f);
            cell_kickgap    = register_cell(xram, 0x1f28);
            cell_kickgap_hi = register_cell(xram, 0x1f29);
            cell_ep1hold    = register_cell(xram, 0x1f53);
            cell_pllhold    = register_cell(xram, 0x1f54);
            cell_t2mode     = register_cell(xram, 0x1f52);
            cell_t2adj      = register_cell(xram, 0x1f58);
            cell_t2adj_hi   = register_cell(xram, 0x1f59);
        }
        class cl_address_space *bas = uc->address_space("bits");
        if (bas) {
            // The firmware reads some pins bit-wise (P5: rows b3/b4, CONN_MODE b5,
            // OS switch b6; P7: key rows b0-b3 -- b0 is the nuphy-air75 F-row --
            // plus the nuphy-air75 power inputs b5/b7). Hook those bit cells so bit
            // reads see the pin level too. (Bit addr of Px.i = Px + i; these are all
            // inputs on every board, so no bit-write linkage to maintain.)
            int p5in[] = {3, 4, 5, 6}, p7in[] = {0, 1, 2, 3, 5, 7};
            for (int k = 0; k < 4; k++)
                p5_bit[p5in[k]] = register_cell(bas, 0x88 + p5in[k]);
            for (int k = 0; k < 6; k++)
                p7_bit[p7in[k]] = register_cell(bas, 0xf8 + p7in[k]);
            for (int k = 0; k < 8; k++) {
                p0_bit[k] = register_cell(bas, 0x80 + k);
                p4_bit[k] = register_cell(bas, 0xb0 + k);
            }
        }
        return 0;
    }

    // A port read must honour pin direction: PxCR selects output(1)/input(0); an
    // output bit reads its latch, an input bit reads the external pin level (which
    // idles high via the pull-up, modelled as pin_ext defaulting to 0xff and pulled
    // low by the board test-side). This models only the MCU's own I/O behaviour --
    // what is wired to the pins (key matrix, CONN_MODE switch, BK3632) is test-side.
    t_mem port_read(class cl_memory_cell *cell, int n, t_addr cr_addr)
    {
        t_mem latch = cell->get();
        t_mem cr    = sfr ? sfr->get(cr_addr) : 0; // PxCR: 1=output, 0=input
        t_mem ext   = pin_ext[n];
        if (pin_model & 0x02) ext &= contact_mask(n);
        return (latch & cr) | (ext & (t_mem)(~cr & 0xff));
    }

    // Key contacts (pin_model bit 1): up to 16 closed switches, each joining
    // two pins, staged as pairs of pin ids (port * 8 + bit, 0xff = none) at
    // xram 0x1f30-0x1f4f. A pin the firmware drives low (output, latch 0) pulls
    // the pin at the other end of a closed contact low -- a key matrix without
    // a test-side stop at every row read, so a key can be held for seconds.
    bool pin_driven_low(unsigned id)
    {
        static const t_addr port_sfr[8] = {0x80, 0x90, 0x98, 0xa0, 0xb0, 0x88, 0xc0, 0xf8};
        static const t_addr cr_sfr[8]   = {0xe1, 0xe2, 0xe3, 0xe4, 0xe5, 0xe6, 0xe7, 0xd1};
        unsigned port = (id >> 3) & 7, bit = id & 7;
        return ((sfr->get(cr_sfr[port]) >> bit) & 1) && !((sfr->get(port_sfr[port]) >> bit) & 1);
    }
    t_mem contact_mask(int n)
    {
        t_mem mask = 0xff;
        if (!xram || !sfr) return mask;
        for (int i = 0; i < 16; i++) {
            unsigned a = xram->get(0x1f30 + 2 * i), b = xram->get(0x1f31 + 2 * i);
            if (a == 0xff || b == 0xff) continue;
            if ((int)(b >> 3) == n && pin_driven_low(a)) mask &= (t_mem)~(1u << (b & 7));
            if ((int)(a >> 3) == n && pin_driven_low(b)) mask &= (t_mem)~(1u << (a & 7));
        }
        return mask;
    }
    virtual t_mem read(class cl_memory_cell *cell)
    {
        if (cell == cell_p5) return port_read(cell, 5, 0xe6); // P5CR @ 0xe6
        if (cell == cell_p7) return port_read(cell, 7, 0xd1); // P7CR @ 0xd1
        if (cell == cell_p0 && (pin_model & 0x01)) return port_read(cell, 0, 0xe1); // P0CR @ 0xe1
        if (cell == cell_p4 && (pin_model & 0x10)) return port_read(cell, 4, 0xe5); // P4CR @ 0xe5
        if (cell == cell_rxcount) return (t_mem)rx_queue.size() + (rx_busy ? 1 : 0);
        if (cell == cell_modelver) return MODEL_VERSION;
        if (cell == cell_kickgap || cell == cell_kickgap_hi) {
            // the longest gap between two watchdog kicks, in units of 1000
            // cycles (~42 us), 16 bits little-endian, saturating
            unsigned k = wdt_gap_max / 1000u;
            if (k > 0xffffu) k = 0xffffu;
            return (t_mem)((cell == cell_kickgap) ? (k & 0xff) : (k >> 8));
        }
        for (int i = 0; i < 8; i++) {
            if (p5_bit[i] && cell == p5_bit[i]) return (port_read(cell_p5, 5, 0xe6) >> i) & 1;
            if (p7_bit[i] && cell == p7_bit[i]) return (port_read(cell_p7, 7, 0xd1) >> i) & 1;
            if (p0_bit[i] && cell == p0_bit[i]) {
                if (pin_model & 0x01) return (port_read(cell_p0, 0, 0xe1) >> i) & 1;
                return (cell_p0->get() >> i) & 1;
            }
            if (p4_bit[i] && cell == p4_bit[i]) {
                // P4.4 (USB power) always reads the pin, idle low (the LED tests
                // stage it without the P4 model); the other bits with the P4 model.
                if ((pin_model & 0x10) || i == 4) return (port_read(cell_p4, 4, 0xe5) >> i) & 1;
                return (cell_p4->get() >> i) & 1;
            }
        }
        return cl_hw::read(cell);
    }

    // What is wired to the pins (key matrix, BK3632, USB host, CONN_MODE switch)
    // is NOT modelled here -- the board model lives test-side (tests/devices.py:
    // KeyMatrix), driving pin_ext via the staging cells above. The chip only models
    // its own pins (direction/latch/pull-up via read()), staying board-agnostic.

    // PWM0 timebase: periodically request the PWM interrupt (vector 0x43) that
    // drives matrix_scan_step(), once the firmware has enabled it (IEN1._EPWM0).
    void log_p02(t_mem latch, t_mem cr)
    {
        int level = (cr & 0x04) ? (int)((latch >> 2) & 1) : (int)((pin_ext[0] >> 2) & 1);
        if (level != p02_level) {
            p02_level = level;
            fprintf(stderr, "[RF] P0.2 %llu %d\n", now, level);
        }
    }

    void uart_tick(void)
    {
        if (!cell_scon) return;
        if (tx_busy && --tx_left == 0) {
            tx_busy = false;
            cell_scon->set(cell_scon->get() | 0x02); // TI
        }
        if (rx_busy) {
            if (--rx_left == 0) {
                rx_busy  = false;
                t_mem sc = cell_scon->get();
                if (sc & 0x01) { // RI still set: the byte is lost
                    if (sstat) sc |= 0x40; // RXOV
                    fprintf(stderr, "[RF] RX %llu %02x lost\n", now, rx_cur & 0xff);
                } else {
                    if (cell_sbuf) cell_sbuf->set(rx_cur & 0xff);
                    sc |= 0x01; // RI
                    if ((rx_cur & 0x100) && sstat) sc |= 0x80; // FE
                    fprintf(stderr, "[RF] RX %llu %02x%s\n", now, rx_cur & 0xff, (rx_cur & 0x100) ? " fe" : "");
                }
                cell_scon->set(sc);
            }
        } else if (!rx_queue.empty() && (cell_scon->get() & 0x10)) { // REN
            rx_cur = rx_queue.front();
            rx_queue.pop_front();
            rx_busy = true;
            rx_left = uart_byte_cycles;
        }
    }

    virtual int tick(int cycles)
    {
        if (uc->state == stPD) return 0; // power-down: the clock is stopped
        now += cycles;
        for (int c = 0; c < cycles; c++)
            uart_tick();
        // PWM4 period interrupt, timed from its registers: PWM40CON (0xda) bits
        // 7:6 = run + interrupt, bits 2:0 = clock Fsys/2^n; period PWM4PERDH:L
        // (0xde:0xdd). One machine cycle is taken as one Fsys clock. Raises
        // PWM40CON bit 5 and the virtual request at xram 0x1f0a (vector 0x63).
        if (sfr && xram) {
            t_mem    con    = sfr->get(0xda);
            unsigned period = (((unsigned)sfr->get(0xde) << 8) | sfr->get(0xdd)) << (con & 0x07);
            if ((con & 0xc0) == 0xc0 && period) {
                pwm4_acc += cycles;
                if (pwm4_acc >= period) {
                    pwm4_acc -= period;
                    if (sfr->get(0xa9) & 0x20) { // only with its interrupt enabled
                        sfr->set(0xda, con | 0x20);
                        xram->set(0x1f0a, xram->get(0x1f0a) | 0x01);
                    }
                }
            } else {
                pwm4_acc = 0;
            }
        }
        if (xram && sfr) {
            // Watchdog: count cycles since the last RSTSTAT kick (paused during sleep,
            // when the clock stops). Only armed once the firmware has kicked it at least
            // once -- a firmware that never touches RSTSTAT (e.g. SMK) is left alone.
            if (wdt_armed && !(sfr->get(0x87) & 0x02)) {
                wdt_acc += cycles;
                if (wdt_acc > 80000000u) {
                    fprintf(stderr, "[SIE] WATCHDOG timeout -> reset\n");
                    wdt_acc = 0;
                    uc->reset();
                }
            }
            // Timer ticks. These are the chip's own timers, just calibrated for uCsim:
            // raise PWM0 (vector 0x43, SMK scan) and the Timer2 1 ms overflow (vector
            // 0x0003, stock scan) at a rate the ISRs can keep up with. (External-hardware
            // effects of those scans -- the key matrix, the BK3632 -- are modelled
            // test-side; INT4 wake is likewise triggered test-side by raising EXF1.)
            // Timer2 at its real rate (opt-in): Fsys/12, overflow reloads RCAP2 and
            // raises the scan tick (the virtual flag 0x1f09) if ET2 and TR2 are set.
            if (t2_real && (sfr->get(0xc8) & 0x04)) {
                unsigned c = (unsigned)cycles;
                if (t2_hold) {
                    const unsigned h = (t2_hold < c) ? t2_hold : c;
                    t2_hold -= h;
                    c -= h;
                }
                t2_sub += c;
                while (t2_sub >= 12) {
                    t2_sub -= 12;
                    if (++t2_count > 0xffff) {
                        t2_count = ((unsigned)(sfr->get(0xcb) & 0xff) << 8) | (unsigned)(sfr->get(0xca) & 0xff);
                        sfr->set(0xc8, sfr->get(0xc8) | 0x80); // TF2
                    }
                    sfr->set(0xcc, t2_count & 0xff);
                    sfr->set(0xcd, (t2_count >> 8) & 0xff);
                }
            }
            // The request follows TF2 (the handler's `clr TF2` ends it; the
            // interrupt's own acceptance clears the virtual flag, which is set
            // again here only while TF2 is still set - within the handler, at
            // the same level, that cannot interrupt).
            if (t2_real) xram->set(0x1f09, (sfr->get(0xc8) & 0x80) ? 1 : 0);
            if (pin_model & 0x80) {
                // Register timing (for firmware whose scan is short enough, e.g.
                // the AULA F65 stock image): PWM0 interrupts at PWM0PERD (xram
                // 0xff9c:0xff98) x Fsys/2^n (PWM00CON 0xff80 bits 2:0) once
                // PWM00CON has run + interrupt set; Timer2 fires once,
                // (0x10000 - RCAP2) x 12 cycles after TR2 goes on.
                t_mem    con    = xram->get(0xff80);
                unsigned period = (((unsigned)xram->get(0xff9c) << 8) | xram->get(0xff98)) << (con & 0x07);
                if ((con & 0xc0) == 0xc0 && period) {
                    pwm_acc += cycles;
                    if (pwm_acc >= period) {
                        pwm_acc -= period;
                        if (sfr->get(0xa9) & 0x02) xram->set(0x1f08, xram->get(0x1f08) | 0x01);
                    }
                } else {
                    pwm_acc = 0;
                }
                // (Timer2 here only when the opt-in real-rate Timer2 above is off)
                bool tr2 = !t2_real && (sfr->get(0xc8) & 0x04) != 0;
                if (tr2 && !t2_was_on) t2_left = (0x10000u - (((unsigned)sfr->get(0xcb) << 8) | sfr->get(0xca))) * 12u;
                t2_was_on = tr2;
                if (tr2 && t2_left) {
                    t2_left = (t2_left > (unsigned)cycles) ? t2_left - cycles : 0;
                    if (!t2_left && (sfr->get(0xa8) & 0x01)) xram->set(0x1f09, xram->get(0x1f09) | 0x01);
                }
                return 0;
            }
            pwm_acc += cycles;
            // period must exceed the matrix-scan ISR duration or the main code starves
            if (pwm_acc >= 30000) {
                pwm_acc = 0;
                // (not while PWM bank 0 runs: its period interrupt then comes from
                // cl_sh68f90_pwm, at the real period)
                if ((sfr->get(0xa9) & 0x02) && !(xram->get(0xff80) & 0x80)) // IEN1._EPWM0
                    xram->set(0x1f08, xram->get(0x1f08) | 0x01);
                // Matrix-scan tick: the SH68F90 Timer2 ISR @0x27bd (vector 0x0003) is
                // the 1ms scan handler. uCsim's own Timer2 overflows ~12x too fast for
                // its modelled clock, so the scan+LED ISR storms and starves the main
                // loop. Drive it instead from this calibrated virtual flag (0x1f09) at a
                // rate the ISR can keep up with, gated on the scan enable IEN0(0xa8).bit0
                // and T2CON.TR2(0x04). clr_bit=true on the it_src auto-clears the flag.
                if (!t2_real && (sfr->get(0xa8) & 0x01) && (sfr->get(0xc8) & 0x04)) xram->set(0x1f09, xram->get(0x1f09) | 0x01);
            }
        }
        return 0;
    }
    virtual void write(class cl_memory_cell *cell, t_mem *val)
    {
        // Board model sets the external pin levels for P5 / P7 via these staging
        // cells (read() applies them to the input bits).
        if (cell == cell_pinext_p5) pin_ext[5] = *val & 0xff;
        if (cell == cell_pinext_p7) pin_ext[7] = *val & 0xff;
        if (cell == cell_pinext_p4) pin_ext[4] = *val & 0xff;
        if (cell == cell_pinext_p0) {
            pin_ext[0] = *val & 0xff;
            if (cell_p0 && cell_p0cr) log_p02(cell_p0->get(), cell_p0cr->get());
        }
        if (cell == cell_pinmodel) sh_pin_model = pin_model = *val & 0xff;
        if (cell == cell_rxpush) rx_queue.push_back(*val & 0xff);
        if (cell == cell_rxpush_fe) rx_queue.push_back((*val & 0xff) | 0x100);
        if (cell == cell_bytecyc_lo) uart_byte_cycles = (uart_byte_cycles & 0xff00) | (*val & 0xff);
        if (cell == cell_bytecyc_hi) uart_byte_cycles = (uart_byte_cycles & 0x00ff) | ((*val & 0xff) << 8);
        // Radio TX line P0.2: log every change of the level on the pin.
        if (cell_p0 && cell_p0cr) {
            if (cell == cell_p0) log_p02(*val, cell_p0cr->get());
            if (cell == cell_p0cr) log_p02(cell_p0->get(), *val);
            if (p0_bit[2] && cell == p0_bit[2])
                log_p02((cell_p0->get() & ~0x04) | ((*val & 1) << 2), cell_p0cr->get());
        }
        // PCON.SSTAT: SCON[7:5] turn into the FE/RXOV/TXCOL flags; the mode bits
        // are kept aside and come back when SSTAT is cleared.
        if (cell == cell_pcon && cell_scon) {
            bool ns = (*val & 0x40) != 0;
            if (ns && !sstat) {
                scon_mode_saved = cell_scon->get() & 0xe0;
                cell_scon->set(cell_scon->get() & 0x1f);
            } else if (!ns && sstat) {
                cell_scon->set((cell_scon->get() & 0x1f) | scon_mode_saved);
            }
            sstat = ns;
        }
        if (cell == cell_t2adj) t2_arm_delay = (t2_arm_delay & 0xff00u) | (unsigned)(*val & 0xff);
        if (cell == cell_t2adj_hi) t2_arm_delay = (t2_arm_delay & 0x00ffu) | ((unsigned)(*val & 0xff) << 8);
        if (cell == cell_t2mode) {
            t2_real            = (*val & 0x01) != 0;
            class cl_hw *timer = uc->get_hw(HW_TIMER, 2, NULL);
            if (timer) timer->on = !t2_real;
            if (sfr) t2_count = ((unsigned)(sfr->get(0xcd) & 0xff) << 8) | (unsigned)(sfr->get(0xcc) & 0xff);
        }
        if (cell == cell_rcap2l && sfr) { // systick_arm: RCAP2H, then RCAP2L, then TH2/TL2 = the same
            t2_count = ((unsigned)(sfr->get(0xcb) & 0xff) << 8) | (unsigned)(*val & 0xff);
            t2_sub   = 0;
            t2_hold  = t2_arm_delay;
        }
        if (cell == cell_th2) t2_count = ((unsigned)(*val & 0xff) << 8) | (t2_count & 0xff);
        if (cell == cell_tl2) t2_count = (t2_count & 0xff00) | (unsigned)(*val & 0xff);
        // Flash ISP: the firmware writes the IB register file then commits with
        // IB_CON5(0xf6)=0x06. Opcode in IB_CON1(0xf2): 0xe6=erase, 0x6e=program.
        // Address = XPAGE(0xf7)<<8 | IB_OFFSET(0xfb); program data = IB_DATA(0xfc).
        // Erase granularity = 512 B (XPAGE = sector*2 -> base = XPAGE*0x100).
        if (cell == cell_ibcon5 && (*val) == 0x06 && rom && sfr) {
            t_mem    op   = sfr->get(0xf2);
            unsigned base = (unsigned)sfr->get(0xf7) << 8;
            // Every flash operation, with the cycles since the last watchdog kick
            // (the stock kicks right before each, 0x94A9).
            fprintf(stderr, "[SIE] FLASH %s %04x kick %u\n", op == 0xe6 ? "erase" : (op == 0x6e ? "program" : "other"),
                    (base | (sfr->get(0xfb) & 0xff)) & 0xffff, wdt_armed ? wdt_acc : 0xffffffffu);
            if (op == 0xe6) // ERASE: fill the 512 B sector with 0xff
                for (unsigned i = 0; i < 0x200; i++)
                    rom->set((base + i) & 0xffff, 0xff);
            else if (op == 0x6e) // PROGRAM: AND one byte (real flash can't set 1s)
            {
                unsigned a = (base | (sfr->get(0xfb) & 0xff)) & 0xffff;
                rom->set(a, rom->get(a) & sfr->get(0xfc));
            }
        }
        // Watchdog kick: any RSTSTAT(0xb1) write (firmware writes 0) reloads the WDT.
        if (cell == cell_kickgap) wdt_gap_max = 0;
        if (cell == cell_usbcon && cell_usbcon) {
            t_mem was = cell_usbcon->get();
            if ((was ^ *val) & 0x40)
                fprintf(stderr, "[SIE] D+ pull-up %s %llu\n", (*val & 0x40) ? "on" : "off", now);
        }
        if (cell == cell_rststat) {
            if (wdt_armed && wdt_acc > wdt_gap_max) {
                wdt_gap_max = wdt_acc;
                // where a long one ended and began (the kicks' code addresses)
                if (wdt_acc >= 36000u)
                    fprintf(stderr, "[SIE] KICKGAP %u cycles, kick at %04x after the kick at %04x\n", wdt_acc,
                            (unsigned)uc->PC & 0xffff, last_kick_pc);
            }
            wdt_acc      = 0;
            wdt_armed    = true;
            last_kick_pc = (unsigned)uc->PC & 0xffff;
        }
        // Sleep: PCON(0x87) bit1 set = power-down/STOP (after SUSLO=0x55). The core
        // halts here until INT4 (matrix-wake) fires; the wake is injected from tick().
        if (cell == cell_pcon && ((*val) & 0x02)) fprintf(stderr, "[SIE] sleep: PCON power-down (wake on INT4 / key)\n");
        if (cell == cell_ep0con && ((*val) & 0x04)) // IEP0RDY: firmware queued IN data
        {
            t_mem n = cell_iep0cnt ? cell_iep0cnt->get() : 0;
            fprintf(stderr, "[SIE] EP0 IN[%d] %u bytes:", in_packets, (unsigned)n);
            for (t_mem i = 0; i < n && i < 8; i++)
                fprintf(stderr, " %02x", (unsigned)(xram->get(0x1108 + i) & 0xff));
            fprintf(stderr, "\n");
            in_packets++;
            *val &= ~0x04;                                                                   // host consumed the packet -> clear ready
            if (in_packets < 16 && cell_usbif2) cell_usbif2->set(cell_usbif2->get() | 0x01); // IEP0IF -> next chunk
        }
        // EP1 = the keyboard's interrupt-IN report endpoint (single-packet)
        if (cell == cell_pllhold) pll_hold = (*val & 0xff) != 0;
        if (cell == cell_ep1hold) ep1_hold = (*val & 0xff) != 0;
        if (cell == cell_ep1hold && !ep1_hold && cell_ep1con && (cell_ep1con->get() & 0x04)) {
            // the hold ends: the host takes the report that waited
            t_mem n = cell_iep1cnt ? cell_iep1cnt->get() : 0;
            fprintf(stderr, "[SIE] EP1 IN %u bytes:", (unsigned)n);
            for (t_mem i = 0; i < n && i < 16; i++)
                fprintf(stderr, " %02x", (unsigned)(xram->get(0x1120 + i) & 0xff));
            fprintf(stderr, "\n");
            cell_ep1con->set(cell_ep1con->get() & ~0x04);
        }
        if (cell == cell_ep1con && ((*val) & 0x04) && ep1_hold) {
            fprintf(stderr, "[SIE] EP1 IN held %llu\n", now); // IEP1RDY stays set
        } else if (cell == cell_ep1con && ((*val) & 0x04)) // IEP1RDY
        {
            t_mem n = cell_iep1cnt ? cell_iep1cnt->get() : 0;
            fprintf(stderr, "[SIE] EP1 IN %u bytes:", (unsigned)n);
            for (t_mem i = 0; i < n && i < 16; i++)
                fprintf(stderr, " %02x", (unsigned)(xram->get(0x1120 + i) & 0xff));
            fprintf(stderr, "\n");
            *val &= ~0x04; // host consumed the report -> clear ready
        }
        // EP2 = the IF1 multiplexed interrupt-IN endpoint (NKRO keyboard + others,
        // each prefixed with a Report ID). FIFO at 0x1180, length in IEP2CNT.
        if (cell == cell_ep2con && ((*val) & 0x04)) // IEP2RDY
        {
            t_mem n = cell_iep2cnt ? cell_iep2cnt->get() : 0;
            fprintf(stderr, "[SIE] EP2 IN %u bytes:", (unsigned)n);
            for (t_mem i = 0; i < n && i < 24; i++)
                fprintf(stderr, " %02x", (unsigned)(xram->get(0x1180 + i) & 0xff));
            fprintf(stderr, "\n");
            *val &= ~0x04; // host consumed the report -> clear ready
        }
        // Clock PLL: report "locked" (PLLSTA) the moment firmware enables it (PLLON),
        // so clock_init()'s `while (!(PLLCON & _PLLSTA))` spin returns and the real
        // boot path (init -> usb_init -> main) runs instead of deadlocking.
        if (cell == cell_pllcon && ((*val) & 0x02) && !pll_hold) // _PLLON -> set _PLLSTA
            *val |= 0x04;
        // SH68F90 EUART0 TX: an SBUF write starts a byte; TI rises when it has gone
        // out (uart_tick). A write while a byte is still going out is dropped and
        // sets TXCOL (with SSTAT). Every byte is logged with its start time.
        if (cell == cell_sbuf) {
            if (tx_busy) {
                if (sstat && cell_scon) cell_scon->set(cell_scon->get() | 0x20);
                fprintf(stderr, "[RF] TXCOL %llu %02x\n", now, (unsigned)(*val & 0xff));
            } else {
                tx_busy = true;
                tx_left = uart_byte_cycles;
                fprintf(stderr, "[RF] TX %llu %02x\n", now, (unsigned)(*val & 0xff));
            }
        }
    }
};

/* ===================================================================== *
 *  PWM banks 0-4: the LED PWM (banks 0-2) and the PWM4 timebase, with an *
 *  event trace for the tests                                             *
 * ===================================================================== */
// Registers (src/sino51lib/sh68f90/sh68f90.h). Banks 0-3 in XDATA, i = bank*6 + ch
// (bank 3 = i 18-21): CON 0xFF80+i, PERDL 0xFF98+bank, PERDH 0xFF9C+bank,
// DUTY1L 0xFFA0+i, DUTY1H 0xFFB8+i, DUTY2L 0xFFD0+i, DUTY2H 0xFFE8+i. Bank 4 in SFRs:
// CON 0xDA-0xDC, PERDL 0xDD, PERDH 0xDE.
// Channel 0's CON drives the bank: bit7 runs it, bit6 enables the period interrupt,
// bit5 is the period flag (the firmware clears it), bits 2:0 pick the clock Fsys >> n.
// On the other channels bit3 (SS) routes the PWM to the pin.
//
// Model assumptions, none from a datasheet: one uCsim machine cycle = one Fsys clock
// (a 1T-class core: the stock F65 LED interrupt only fits its 100 us slot on one); a
// period is PERD counts; DUTY1/DUTY2 are latched when a bank starts and at each
// period start, so a write lands in the next period (the stock loads slot n+1 during
// slot n); the pin is high from count DUTY1 to count DUTY2; a stopped bank or a
// channel without SS leaves the pin to its port latch.
//
// Interrupts: bank 0 -> vector 0x43 (IEN1.EPWM0), requested while flag and enable
// are both set (level), through the virtual cell 0x1f0b -- unless the SIE's
// register timing (pin model bit 7, xram 0x1f13) is on, which raises PWM0 itself
// (0x1f08, as the radio tests of the stock image have it). Bank 4 (the PWM4 1 ms
// timebase) is only traced here: its period interrupt (0x1f0a, vector 0x63) comes
// from the SIE's tick(), which stops in power-down. Every bank stops counting in
// power-down (the clock is stopped).
//
// Trace, off unless the test sets xram 0x1f50 (outside the chip's memory):
//   bit0: [PWMP] b=<bank> t=<cycle> n=<period no> per=<counts> sh=<clock shift>
//                d1=<6 x DUTY1> d2=<6 x DUTY2>        at each latched period, banks 0-2
//         [PWMC] t=<cycle> run=<banks> ss=<SS bits, i> lat=<P1..P7 latches> cr=<P1..P7 CR>
//                                                     whenever any of those changes
//   bit1: [PWMW] t=<cycle> a=<ffxx | sXX> v=<value>   every PWM register write
//   bit2: the [PWMC] records alone (no [PWMP]), for long runs: with DUTY1/DUTY2
//         and the period known, the run / route / column changes say what lit
//   and, whenever the test writes xram 0x1f51 (trace on): [PWMM] t=<cycle> v=<value>,
//   a marker that ties a breakpoint to the trace's time.
// What is wired to the pins (columns, LED anodes) stays test-side.
class cl_sh68f90_pwm : public cl_hw
{
    class cl_address_space *xram, *sfr;
    struct bank_t
    {
        bool               run;
        unsigned           shift, sub, count, period, nch, n;
        unsigned           d1[6], d2[6];
    } bank[5];
    unsigned long long cyc;
    t_mem              trace;
    unsigned           run_mask, ss_mask;
    unsigned           last_sig[16];
    bool               sig_valid;
    class cl_memory_cell *cell_con4, *cell_trace, *cell_mark;

    static t_addr con_addr(int b, int ch) { return b < 3 ? 0xff80 + b * 6 + ch : 0xff92 + ch; }
    static int    chan_index(int b, int ch) { return b < 3 ? b * 6 + ch : 18 + ch; }

   public:
    cl_sh68f90_pwm(class cl_uc *auc) : cl_hw(auc, HW_DUMMY, 1, "sh68f90_pwm")
    {
        xram = sfr = 0;
        cyc        = 0;
        trace      = 0;
        run_mask = ss_mask = 0;
        sig_valid          = false;
        cell_con4 = cell_trace = cell_mark = 0;
        for (int b = 0; b < 5; b++) {
            bank[b].run = false;
            bank[b].shift = bank[b].sub = bank[b].count = bank[b].n = 0;
            bank[b].period = 1;
            bank[b].nch    = b < 3 ? 6 : (b == 3 ? 4 : 3);
            for (int c = 0; c < 6; c++)
                bank[b].d1[c] = bank[b].d2[c] = 0;
        }
    }
    virtual int init(void)
    {
        cl_hw::init();
        xram = uc->address_space("xram");
        sfr  = uc->address_space(MEM_SFR_ID);
        if (xram) {
            for (t_addr a = 0xff80; a <= 0xffff; a++)
                register_cell(xram, a);
            cell_trace = register_cell(xram, 0x1f50);
            cell_mark  = register_cell(xram, 0x1f51);
        }
        if (sfr) {
            cell_con4 = register_cell(sfr, 0xda);
            for (t_addr a = 0xdb; a <= 0xde; a++)
                register_cell(sfr, a);
        }
        return 0;
    }

    unsigned reg16(t_addr lo, t_addr hi) { return (unsigned)(xram->get(lo) & 0xff) | ((unsigned)(xram->get(hi) & 0xff) << 8); }
    unsigned period_of(int b)
    {
        unsigned p = b < 4 ? reg16(0xff98 + b, 0xff9c + b) : (unsigned)(sfr->get(0xdd) & 0xff) | ((unsigned)(sfr->get(0xde) & 0xff) << 8);
        return p ? p : 0x10000;
    }
    t_mem con0(int b) { return b < 4 ? xram->get(con_addr(b, 0)) : sfr->get(0xda); }
    void  set_con0(int b, t_mem v)
    {
        if (b < 4)
            xram->set(con_addr(b, 0), v);
        else
            sfr->set(0xda, v);
    }
    void update_irq(int b, t_mem con)
    {
        if (b == 0) xram->set(0x1f0b, ((con & 0x60) == 0x60 && !(sh_pin_model & 0x80)) ? 1 : 0);
    }
    void latch(int b)
    {
        bank_t &k = bank[b];
        if (b > 3) return;
        for (unsigned c = 0; c < k.nch; c++) {
            int i  = chan_index(b, c);
            k.d1[c] = reg16(0xffa0 + i, 0xffb8 + i);
            k.d2[c] = reg16(0xffd0 + i, 0xffe8 + i);
        }
    }
    void emit_period(int b)
    {
        bank_t &k = bank[b];
        if (!(trace & 1) || b > 2) return;
        fprintf(stderr, "[PWMP] b=%d t=%llu n=%u per=%u sh=%u d1=", b, cyc, k.n, k.period, k.shift);
        for (unsigned c = 0; c < k.nch; c++)
            fprintf(stderr, c ? ",%x" : "%x", k.d1[c]);
        fprintf(stderr, " d2=");
        for (unsigned c = 0; c < k.nch; c++)
            fprintf(stderr, c ? ",%x" : "%x", k.d2[c]);
        fprintf(stderr, "\n");
    }
    void period_start(int b)
    {
        bank_t &k = bank[b];
        k.count   = 0;
        k.n++;
        k.period = period_of(b);
        t_mem c  = con0(b) | 0x20;
        set_con0(b, c);
        update_irq(b, c);
        latch(b);
        emit_period(b);
    }
    void recompute_masks(void)
    {
        unsigned r = 0, s = 0;
        for (int b = 0; b < 4; b++) {
            if (con0(b) & 0x80) r |= 1u << b;
            for (unsigned c = 0; c < bank[b].nch; c++)
                if (xram->get(con_addr(b, c)) & 0x08) s |= 1u << chan_index(b, c);
        }
        if (sfr->get(0xda) & 0x80) r |= 0x10;
        run_mask = r; // sample_pins() emits a record when these change
        ss_mask  = s;
    }
    // A write to channel 0's CON: start / stop the bank, follow the clock and the
    // interrupt enable. Called before the new value is stored.
    void con0_write(int b, t_mem v)
    {
        bank_t &k   = bank[b];
        bool    run = (v & 0x80) != 0;
        k.shift     = v & 0x07;
        if (run && !k.run) {
            k.run    = true;
            k.sub    = 0;
            k.count  = 0;
            k.n++;
            k.period = period_of(b);
            latch(b);
            emit_period(b);
        } else if (!run && k.run) {
            k.run = false;
        }
        update_irq(b, v);
    }

    virtual void write(class cl_memory_cell *cell, t_mem *val)
    {
        if (cell == cell_trace) {
            trace     = *val & 0xff;
            sig_valid = false;
            return;
        }
        if (cell == cell_mark) {
            if (trace) fprintf(stderr, "[PWMM] t=%llu v=%02x\n", cyc, (unsigned)(*val & 0xff));
            return;
        }
        if (cell == cell_con4) {
            if (trace & 2) fprintf(stderr, "[PWMW] t=%llu a=s%02x v=%02x\n", cyc, 0xda, (unsigned)(*val & 0xff));
            con0_write(4, *val);
            cell->set(*val);
            recompute_masks();
            return;
        }
        // XDATA 0xff80-0xffff and the other bank-4 SFRs
        for (int b = 0; b < 4; b++) {
            if (xram && cell == xram->get_cell(con_addr(b, 0))) {
                if (trace & 2) fprintf(stderr, "[PWMW] t=%llu a=%04x v=%02x\n", cyc, (unsigned)con_addr(b, 0), (unsigned)(*val & 0xff));
                con0_write(b, *val);
                cell->set(*val); // store now so the masks below see it
                recompute_masks();
                return;
            }
        }
        if (xram) {
            for (t_addr ad = 0xff80; ad <= 0xffff; ad++) {
                if (cell == xram->get_cell(ad)) {
                    if (trace & 2) fprintf(stderr, "[PWMW] t=%llu a=%04x v=%02x\n", cyc, (unsigned)ad, (unsigned)(*val & 0xff));
                    if (ad <= 0xff95) {
                        cell->set(*val);
                        recompute_masks();
                    }
                    return;
                }
            }
        }
        if (sfr && (trace & 2)) {
            for (t_addr ad = 0xdb; ad <= 0xde; ad++)
                if (cell == sfr->get_cell(ad)) fprintf(stderr, "[PWMW] t=%llu a=s%02x v=%02x\n", cyc, (unsigned)ad, (unsigned)(*val & 0xff));
        }
    }

    // Pins the LED path depends on: P1..P7 latches and directions.
    void sample_pins(void)
    {
        static const t_addr lat[7] = {0x90, 0x98, 0xa0, 0xb0, 0x88, 0xc0, 0xf8};
        static const t_addr cr[7]  = {0xe2, 0xe3, 0xe4, 0xe5, 0xe6, 0xe7, 0xd1};
        unsigned            sig[16];
        for (int i = 0; i < 7; i++) {
            sig[i]     = sfr->get(lat[i]) & 0xff;
            sig[7 + i] = sfr->get(cr[i]) & 0xff;
        }
        sig[14] = run_mask;
        sig[15] = ss_mask;
        bool same = sig_valid;
        for (int i = 0; same && i < 16; i++)
            same = sig[i] == last_sig[i];
        if (same) return;
        for (int i = 0; i < 16; i++)
            last_sig[i] = sig[i];
        sig_valid = true;
        fprintf(stderr, "[PWMC] t=%llu run=%x ss=%06x lat=%02x%02x%02x%02x%02x%02x%02x cr=%02x%02x%02x%02x%02x%02x%02x\n", cyc, run_mask, ss_mask,
                sig[0], sig[1], sig[2], sig[3], sig[4], sig[5], sig[6], sig[7], sig[8], sig[9], sig[10], sig[11], sig[12], sig[13]);
    }

    virtual int tick(int cycles)
    {
        if (uc->state == stPD) return 0; // power-down: the clock is stopped
        cyc += cycles;
        if (!xram || !sfr) return 0;
        for (int b = 0; b < 5; b++) {
            bank_t &k = bank[b];
            if (!k.run) continue;
            k.sub += cycles;
            unsigned step = 1u << k.shift;
            while (k.sub >= step) {
                k.sub -= step;
                if (++k.count >= k.period) period_start(b);
            }
        }
        if (trace & 5) sample_pins();
        return 0;
    }
    // A reset stops every bank and clears the PWM registers (uCsim keeps XDATA
    // across a reset; the chip's PWM registers reset to 0).
    virtual void reset(void)
    {
        for (int b = 0; b < 5; b++) {
            bank[b].run = false;
            bank[b].n   = 0;
        }
        run_mask = ss_mask = 0;
        sig_valid          = false;
        if (xram) {
            for (t_addr a = 0xff80; a <= 0xffff; a++)
                xram->set(a, 0);
            xram->set(0x1f0a, 0);
            xram->set(0x1f0b, 0);
        }
        if (sfr) {
            for (t_addr a = 0xda; a <= 0xde; a++)
                sfr->set(a, 0);
        }
    }
};

/* ===================================================================== *
 *  Interrupt controller: SH68F90 vectors (no standard INT0/INT1)         *
 * ===================================================================== */
class cl_sh68f90_interrupt : public cl_interrupt
{
   public:
    cl_sh68f90_interrupt(class cl_uc *auc) : cl_interrupt(auc) {}
    virtual int  init(void);
    virtual void added_to_uc(void);
};

int cl_sh68f90_interrupt::init(void)
{
    cl_hw::init();
    sfr = uc->address_space(MEM_SFR_ID);
    // SH68F90 has no standard INT0/INT1, and 0x88/0x8a are P5/MAPPING (owned by the
    // SIE). Register only IE; bind cell_tcon/it0/it1 via get_cell (NOT register_cell,
    // so we don't become an operator on P5). The base class derefs these, so they
    // must be non-null even though we add no INT0/INT1 sources.
    if (sfr) {
        register_cell(sfr, IE);
        cell_tcon = sfr->get_cell(TCON);
        bit_INT0  = 0;
        bit_INT1  = 0;
        cell_it0  = sfr->get_cell(TCON);
        cell_it1  = sfr->get_cell(TCON);
    }
    return 0;
}

void cl_sh68f90_interrupt::added_to_uc(void)
{
    class cl_address_space *sfr = uc->address_space(MEM_SFR_ID);
    class cl_it_src        *is;
    // SH68F90 USB interrupt (_INT_USB = vector 7 @ 0x003B).
    // enable: IEN1(0xa9).EUSB(bit0); request: USBIF1(0x92) PUPIF(bit7),
    // SETUPIF(bit4), RESMIF(bit2), SUSPIF(bit1) or USBRSTIF(bit0) -- the model
    // raises only SETUPIF itself; a test raises the others to stand for the
    // host's suspend, resume, bus reset and pull-up events.
    // level-triggered (clr_bit=false): the firmware ISR clears the flag.
    uc->it_sources->add(is = new cl_sh_it_src(uc, 0x100, sfr->get_cell(0xa9), 0xa9, 0x01, sfr->get_cell(0x92), 0x97, 0x003b, false, "USB (SH68F90)", 7));
    is->init();
    // EP0 IN/OUT completion sources at the same USB vector (USBIF2), so the SIE
    // model can advance control transfers and the ISP set-report status stage.
    // uCsim's pending() is (flag & mask) == mask, so each source needs a
    // single-bit mask -- a combined mask would require all those bits set at once.
    uc->it_sources->add(is = new cl_sh_it_src(uc, 0x101, sfr->get_cell(0xa9), 0xa9, 0x01, sfr->get_cell(0x93), 0x01, // IEP0IF
                                           0x003b, false, "USB EP0-IN (SH68F90)", 7));
    is->init();
    uc->it_sources->add(is = new cl_sh_it_src(uc, 0x102, sfr->get_cell(0xa9), 0xa9, 0x01, sfr->get_cell(0x93), 0x10, // OEP0IF
                                           0x003b, false, "USB EP0-OUT (SH68F90)", 7));
    is->init();
    // SH68F90 UART TX-complete interrupt (_INT_EUART0 = vector 13 @ 0x6B).
    // enable IEN1(0xa9)._ES0(0x40); request SCON(0xd8).TI(0x02). The SIE sets TI on
    // each SBUF write, so this fires and uart_interrupt_handler clears uart_tx_busy.
    uc->it_sources->add(is = new cl_sh_it_src(uc, 0x103, sfr->get_cell(0xa9), 0xa9, 0x40, sfr->get_cell(0xd8), 0x02, 0x006b, false, "UART TI (SH68F90)", 13));
    is->init();
    // EUART0 receive (RI, SCON bit0) at the same vector.
    uc->it_sources->add(is = new cl_sh_it_src(uc, 0x109, sfr->get_cell(0xa9), 0xa9, 0x40, sfr->get_cell(0xd8), 0x01, 0x006b, false, "UART RI (SH68F90)", 13));
    is->init();
    // SH68F90 PWM0 interrupt (_INT_PWM0 = vector 8 @ 0x43), which drives the matrix
    // scan. enable IEN1(0xa9)._EPWM0(0x02); request a virtual flag at xram 0x1f08
    // that the SIE's tick() raises periodically; clr_bit=true (HW auto-clears, the
    // firmware ISR doesn't).
    uc->it_sources->add(is = new cl_sh_it_src(uc, 0x104, sfr->get_cell(0xa9), 0xa9, 0x02, uc->address_space("xram")->get_cell(0x1f08), 0x01, 0x0043, true, "PWM0 (SH68F90)", 8));
    is->init();
    // SH68F90 PWM4 period interrupt (vector 12 @ 0x63): enable IEN1(0xa9)._EPWM4
    // (0x20), request the virtual flag xram 0x1f0a raised from the PWM4 registers
    // in the SIE's tick().
    uc->it_sources->add(is = new cl_sh_it_src(uc, 0x10a, sfr->get_cell(0xa9), 0xa9, 0x20, uc->address_space("xram")->get_cell(0x1f0a), 0x01, 0x0063, true, "PWM4 (SH68F90)", 12));
    is->init();
    // SH68F90 Timer2 overflow is remapped to the INT0 vector slot (0x0003): the
    // 1 ms Timer2 ISR @0x27bd is the LED-PWM-mux + MATRIX-SCAN handler. uCsim's
    // own Timer2 runs (auto-reload) and sets T2CON.TF2(0x80) on overflow but would
    // fire the standard 0x2B vector, which this firmware doesn't use. Route TF2 ->
    // 0x0003 instead, enabled by IEN0(0xa8).bit0; clr_bit=false because the
    // firmware ISR clears TF2 itself (CLR 0xcf). This drives the key-matrix scan
    // that populates the row bitmap at EXTMEM 0x06b0.
    uc->it_sources->add(is = new cl_sh_it_src(uc, 0x105, sfr->get_cell(0xa8), 0xa8, 0x01, uc->address_space("xram")->get_cell(0x1f09), 0x01, 0x0003, true, "Timer2 scan (SH68F90)", 1));
    is->init();
    // INT4 = matrix/RF wake (vector 0x000b). Enable IEN0(0xa8).EX4(bit1), armed only
    // by the sleep path; request via EXF1(0xe8) sub-flags IF40..IF47 (raised from the
    // SIE tick() when a key is staged while asleep). ISR clears EXF1 -> clr_bit=false.
    uc->it_sources->add(is = new cl_sh_it_src(uc, 0x106, sfr->get_cell(0xa8), 0xa8, 0x02, sfr->get_cell(0xe8), 0xff, 0x000b, false, "INT4 wake (SH68F90)", 2));
    is->init();
    // INT3 (0x0013) / INT2 (0x001b): real ISR bodies exist but the firmware never
    // arms them in this build; wired so they dispatch if ever enabled. Enable
    // IEN0(0xa8).EX3(bit2)/EX2(bit3); request EXF0(0xb6).bit1/bit0; ISR clears flag.
    uc->it_sources->add(is = new cl_sh_it_src(uc, 0x107, sfr->get_cell(0xa8), 0xa8, 0x04, sfr->get_cell(0xb6), 0x02, 0x0013, false, "INT3 (SH68F90)", 3));
    is->init();
    uc->it_sources->add(is = new cl_sh_it_src(uc, 0x108, sfr->get_cell(0xa8), 0xa8, 0x08, sfr->get_cell(0xb6), 0x01, 0x001b, false, "INT2 (SH68F90)", 3));
    is->init();
    // PWM bank 0 period interrupt (vector 0x43, IEN1.EPWM0) from cl_sh68f90_pwm: a
    // level request through the virtual cell 0x1f0b (flag AND interrupt enable in
    // PWM00CON); the firmware clears the flag.
    uc->it_sources->add(is = new cl_sh_it_src(uc, 0x10b, sfr->get_cell(0xa9), 0xa9, 0x02, uc->address_space("xram")->get_cell(0x1f0b), 0x01, 0x0043, false, "PWM0 period (SH68F90)", 8));
    is->init();
}

/* ===================================================================== *
 *  cl_sh68f90 : 8052 core + the peripherals above                        *
 * ===================================================================== */
cl_sh68f90::cl_sh68f90(struct cpu_entry *Itype, class cl_sim *asim) : cl_uc52(Itype, asim) {}

int cl_sh68f90::init(void)
{
    int ret = cl_uc52::init();
    // Dual data pointer: INSCON (0x86) bit 0 selects DPTR1 (DPL1 0x84 / DPH1 0x85),
    // as the stock F65 LED loader uses it (0x6C45); same scheme as uCsim's C521.
    cpu->cfg_set(uc51cpu_aof_mdps, 0x86);
    cpu->cfg_set(uc51cpu_mask_mdps, 1);
    cpu->cfg_set(uc51cpu_aof_mdps1l, 0x84);
    cpu->cfg_set(uc51cpu_aof_mdps1h, 0x85);
    decode_dptr();
    return ret;
}

// SH68F90 interrupt priority: four levels from IPH/IPL, IPL0/IPH0 (0xb8/0xb4)
// for the IEN0 sources and IPL1/IPH1 (0xb9/0xb5) for the IEN1 sources, same
// bit positions as the enables. (The 8051 core in uCsim knows only IP at 0xb8
// with two levels.)
int cl_sh68f90::priority_of_src(class cl_it_src *is)
{
    t_addr        ien = 0xa8;
    cl_sh_it_src *s   = dynamic_cast<cl_sh_it_src *>(is);
    if (s) ien = s->ien_addr;
    t_addr ipl = (ien == 0xa9) ? 0xb9 : 0xb8;
    t_addr iph = (ien == 0xa9) ? 0xb5 : 0xb4;
    int    pr  = 0;
    if (sfr->get(ipl) & is->ie_mask) pr |= 1;
    if (sfr->get(iph) & is->ie_mask) pr |= 2;
    return pr;
}

// Power-down: the 8051 core's do_inst returns early (as a self-jump) while
// no instruction runs, so interrupts are never looked at and nothing wakes
// it. Here the pending interrupts are checked in power-down too
// (do_interrupt ends it).
int cl_sh68f90::do_inst(void)
{
    if (state == stPD) {
        tick(1);
        return do_interrupt();
    }
    return cl_uc52::do_inst();
}

// As cl_51core::do_interrupt, but the pending source with the highest priority
// wins (ties: registration order), and it must be above the level in service.
int cl_sh68f90::do_interrupt(void)
{
    if (state == stPD) {
        // Power-down (the hardware ticks stop, this still runs): a key held
        // while asleep -- the test sets the staging cell xram 0x1f25 --
        // raises INT4's flag IF40, as the chip's key-wake input would.
        class cl_address_space *x = address_space("xram");
        if (x && x->get(0x1f25)) sfr->set(0xe8, sfr->get(0xe8) | 0x01);
    }
    if (interrupt->was_reti) {
        interrupt->was_reti = false;
        return resGO;
    }
    if (!(sfr->get(IE) & bmEA)) return resGO;
    class it_level  *il   = (class it_level *)(it_levels->top());
    class cl_it_src *best = 0;
    int              bpr  = -1;
    for (int i = 0; i < it_sources->count; i++) {
        class cl_it_src *is = (class cl_it_src *)(it_sources->at(i));
        if (!(is->is_active() && is->enabled() && is->pending())) continue;
        int pr = priority_of_src(is);
        if (il->level >= 0 && pr <= il->level) continue;
        if (pr > bpr) {
            best = is;
            bpr  = pr;
        }
    }
    if (!best) return resGO;
    if (state == stPD) {
        // An enabled interrupt ends power-down (the SH68F90 wakes on INT4 and
        // the USB events); the oscillator restart is not modelled.
        state = stGO;
        sfr->set(PCON, sfr->get(PCON) & ~bmPD);
        fprintf(stderr, "[SIE] wake from power-down (vector 0x%04x)\n", (unsigned)best->addr);
    }
    if (state == stIDLE) {
        state = stGO;
        sfr->set(PCON, sfr->get(PCON) & ~bmIDL);
        interrupt->was_reti = true;
        return resGO;
    }
    best->clear();
    class it_level *IL = new it_level(bpr, best->addr, PC, best);
    return accept_it(IL);
}

void cl_sh68f90::mk_hw_elements(void)
{
    // This is cl_51core::mk_hw_elements() with timer0/timer1/serial OMITTED (the
    // SH68F90 remaps SFRs 0x88-0x99 to GPIO/clock/USB, so the standard timer/UART
    // models would corrupt those), plus Timer2 (from cl_uc52), the SIE peripheral,
    // and the SH68F90 interrupt controller.
    cl_uc::mk_hw_elements();

    class cl_hw *h;
    acc = sfr->get_cell(ACC);
    psw = sfr->get_cell(PSW);

    // Timer2 (8052) -- kept; the firmware's 1 ms matrix-scan ISR rides its overflow.
    h = new cl_timer2(this, 2, "timer2", t2_default | t2_down);
    h->init();
    add_hw(h);

    add_hw(h = new cl_dreg(this, 0, "dreg"));
    h->init();

    class cl_port_ui *d;
    add_hw(d = new cl_port_ui(this, 0, "dport"));
    d->init();

    class cl_port *p0, *p1, *p2, *p3;
    add_hw(p0 = new cl_port(this, 0));
    p0->init();
    add_hw(p1 = new cl_port(this, 1));
    p1->init();
    add_hw(p2 = new cl_port(this, 2));
    p2->init();
    add_hw(p3 = new cl_port(this, 3));
    p3->init();

    class cl_port_data pd;
    pd.init();
    pd.cell_dir = NULL;
    pd.set_name("P0");
    pd.cell_p  = p0->cell_p;
    pd.cell_in = p0->cell_in;
    pd.keyset  = keysets[0];
    pd.basx    = 1;
    pd.basy    = 5;
    d->add_port(&pd, 0);
    pd.set_name("P1");
    pd.cell_p  = p1->cell_p;
    pd.cell_in = p1->cell_in;
    pd.keyset  = keysets[1];
    pd.basx    = 20;
    pd.basy    = 5;
    d->add_port(&pd, 1);
    pd.set_name("P2");
    pd.cell_p  = p2->cell_p;
    pd.cell_in = p2->cell_in;
    pd.keyset  = keysets[2];
    pd.basx    = 40;
    pd.basy    = 5;
    d->add_port(&pd, 2);
    pd.set_name("P3");
    pd.cell_p  = p3->cell_p;
    pd.cell_in = p3->cell_in;
    pd.keyset  = keysets[3];
    pd.basx    = 60;
    pd.basy    = 5;
    d->add_port(&pd, 3);

    // The chip-specific peripheral model (registers its SFR cells in init()).
    cl_sh68f90_sie *sie = new cl_sh68f90_sie(this);
    add_hw(sie);
    sie->init();

    // PWM banks + timebase (LED PWM, period interrupts, test trace).
    cl_sh68f90_pwm *pwm = new cl_sh68f90_pwm(this);
    add_hw(pwm);
    pwm->init();

    // Interrupt controller (its added_to_uc adds the SH68F90 it_sources).
    add_hw(interrupt = new cl_sh68f90_interrupt(this));
    interrupt->init();
}

/* End of s51.src/sh68f90.cc */
