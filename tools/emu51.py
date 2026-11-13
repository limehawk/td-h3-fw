"""Minimal 8051 core plus TD-H3 peripheral models, for running the firmware on a PC.

Models: BK4819 3-wire bus (P5.2-P5.4), LCD over SPI (SPDR, D/C on P0.6), AT24C64 on
bit-banged I2C (SCL P1.6, SDA P5.0), keypad matrix, ADC, UART1, Timer0/1 interrupts.
Any write to the flash controller SFRs (0xFB-0xFF) raises: firmware must never touch them.
Timing is not cycle-accurate: Timer0 fires every TICK instructions.
"""

TICK = 4000
# PyPy's JIT with loop unrolling off. With it, most instructions of a run with an until() went back
# through the loop's set-up code (measured 4x slower); without it, every branch of the trace returns
# to the one loop.
try:
    import pypyjit
    pypyjit.set_param('enable_opts=intbounds:rewrite:virtualize:string:pure:earlyforce:heap')   # the default less unroll
except Exception:      # CPython, or a PyPy without that setting: the same, only slower
    pass
PORTS = {0x80: 0x9A, 0x90: 0xA1, 0xA0: 0xA2, 0xB0: 0xA3, 0xC0: 0xA4, 0xD8: 0xA5}  # port -> TRIS
FLASH_SFRS = {0xFB, 0xFC, 0xFD, 0xFE, 0xFF}
KEYS = {  # key -> (row index 0..5 or None for side keys, column 0=L1 1=L2 2=L3)
    'MENU': (0, 2), 'UP': (0, 1), 'EXIT': (0, 0), 'DOWN': (1, 1), '1': (2, 2), '2': (2, 1), '3': (2, 0),
    '4': (3, 2), '5': (3, 1), '6': (3, 0), '7': (4, 2), '8': (4, 1), '9': (4, 0), '*': (5, 2), '0': (5, 1),
    '#': (5, 0), 'PTT': (None, 0), 'SIDE1': (None, 1), 'SIDE2': (None, 2),
}
ROWBITS = [(0x80, 3), (0x80, 2), (0x80, 1), (0x80, 0), (0xA0, 7), (0xA0, 6)]  # R1..R6


class Halt(Exception):
    pass


class BK4819:
    def __init__(self):
        self.writes, self.reads = [], []
        self.regs = {0x67: 0x0040}  # RSSI raw: 0x40/2-160 = -128 dBm
        self.bits, self.n, self.out = 0, 0, None
        self.dbm = None             # optional fn(freq_10hz) -> dBm, drives REG_67
        self.busy = None            # optional fn(freq_10hz) -> bool, drives REG_0C bit 1 (squelch open)
        self.ctcss = None           # optional fn(freq) -> 0.1 Hz tone being received, drives REG_68
        self.dcs = None             # optional fn(freq) -> 23-bit DCS word as received, drives REG_69/6A
        self.counter = None         # optional fn() -> carrier (10 Hz) for the frequency counter, REG_0D/0E
        self.alert = None           # optional fn(freq) -> bool, 1050 Hz present (REG_0C bits 11:10 with REG_51=944A)
        self.tonematch = None       # optional fn(freq) -> bool, the programmed CTCSS (REG_0C bit 10) or DCS (bit 14) heard
        self.dtmf = []              # DTMF digit codes still to be decoded (0-15), heard only with our setup (REG_24 0xC17E)
        self.tails = 0              # 55 Hz squelch tails still to be heard, one interrupt each, with the detector armed
        self.settle = 0             # instructions after RX turns on (REG_30) that RSSI reads the floor: the real chip's
        self.clock = None           # few ms; the glitch count (REG_63) reads high meanwhile; fn() -> CPU icount
        self.t_tuned = -10**9
        self.voice = 0              # the mic's level as the VOX detector sees it; found over REG_46, lost under REG_79 (REG_31 bit 2 on)
        self.vox_found = False

    def freq(self):
        return self.regs.get(0x39, 0) << 16 | self.regs.get(0x38, 0)

    def irq(self):                  # pending interrupts as REG_02 reports them: bit 11 DTMF, bit 10 tail
        r = self.regs
        f = 0x0800 if self.dtmf and r.get(0x24) == 0xC17E and r.get(0x3F, 0) & 0x0800 else 0
        if self.tails and r.get(0x3F, 0) & 0x0400 and r.get(0x07) == 0x2000 | 462 and r.get(0x51, 0) & 0x8000:
            f |= 0x0400
        if r.get(0x31, 0) & 4 and r.get(0x3F, 0) & 0x30:
            if not self.vox_found and self.voice > r.get(0x46, 0) & 0x7FF: f |= 0x20
            if self.vox_found and self.voice < r.get(0x79, 0) & 0x7FF: f |= 0x10
        return f

    def edge(self, scn, sck_rise, sda):
        """Called on SCK rising edge while SCN low; returns bit to drive (reads) or None."""
        if self.out is not None:
            b = (self.out >> 15) & 1
            self.out = (self.out << 1) & 0xFFFF
            self.n += 1
            return b
        self.bits = (self.bits << 1) | sda
        self.n += 1
        if self.n == 8 and self.bits & 0x80:  # read: chip drives the next 16 bits
            reg = self.bits & 0x7F
            self.reads.append(reg)
            if reg == 0x67 and self.dbm:
                self.regs[0x67] = (self.dbm(self.freq()) + 160) * 2
            if self.settle and reg in (0x63, 0x67):
                busy = self.clock() - self.t_tuned < self.settle
                if reg == 0x63:
                    self.regs[0x63] = 0xFF if busy else 0   # glitch count: high until it settles
                elif busy:
                    self.regs[0x67] = 0x40  # -128 dBm: not settled yet
            f = self.freq()
            if reg == 0x0C:
                busy = self.busy and self.busy(f)
                tone = self.alert and self.regs.get(0x51) == 0x944A and self.alert(f)
                match = self.tonematch and self.regs.get(0x51, 0) & 0x8000 and self.tonematch(f)
                match = (0x4000 if self.regs.get(0x51) == 0x8033 else 0x400) if match else 0
                self.regs[0x0C] = (2 if busy else 0) | (0x400 if tone else 0) | match | (1 if self.irq() else 0)
            if reg == 0x0B and self.dtmf:  # one code register: tones found since the last read leave the newest
                self.regs[0x0B] = self.dtmf[-1] << 8
                self.dtmf.clear()
            if reg == 0x68:
                t = self.ctcss and self.ctcss(f)
                self.regs[0x68] = round(t * 2.064888) & 0x1FFF if t else 0x8000   # datasheet: Hz x 20.64888
            if reg in (0x69, 0x6A):
                w = self.dcs and self.dcs(f)
                self.regs[0x69] = w >> 12 & 0xFFF if w else 0x8000
                self.regs[0x6A] = w & 0xFFF if w else 0
            if reg in (0x0D, 0x0E):
                c = self.counter and self.regs.get(0x32) == 0x0245 and self.counter()
                self.regs[0x0D] = (c >> 16 & 0x7FF) if c else 0x8000
                self.regs[0x0E] = c & 0xFFFF if c else 0
            self.out = self.regs.get(reg, 0)
        elif self.n == 24:
            reg, val = (self.bits >> 16) & 0x7F, self.bits & 0xFFFF
            self.writes.append((reg, val))
            self.regs[reg] = val
            if reg == 0x30 and val & 0x0200 and self.clock:   # RX on (bk_rx_on): the RSSI path starts settling
                self.t_tuned = self.clock()
            if reg == 0x02:         # the acknowledge: REG_02 now says which interrupts fired
                self.regs[0x02] = self.irq()
                if self.regs[0x02] & 0x0400:
                    self.tails -= 1
                if self.regs[0x02] & 0x30:
                    self.vox_found = bool(self.regs[0x02] & 0x20)
        return None

    def deselect(self):
        self.bits, self.n, self.out = 0, 0, None


class EEPROM:
    def __init__(self, data=None):
        self.mem = bytearray(data or b'\xff' * 8192)
        self.writes = []
        self.reset()
        self.scl, self.sda = 1, 1

    def reset(self):
        self.state, self.byte, self.nbits, self.addr_bytes, self.out = 'idle', 0, 0, [], None
        self.reading, self.drive = False, 1

    def lines(self, scl, sda):
        if scl and self.scl and sda != self.sda:  # START / STOP
            if not sda:
                self.state, self.byte, self.nbits, self.addr_bytes = 'dev', 0, 0, []
                self.reading, self.drive = False, 1
            else:
                self.reset()
        elif scl and not self.scl:  # rising edge: sample
            if self.state in ('dev', 'addr', 'data') and not self.reading and self.nbits < 8:
                self.byte = (self.byte << 1) | sda
                self.nbits += 1
            elif self.state == 'mack':  # master ACK (0) or NACK (1) after our byte
                self.mack = sda
        elif not scl and self.scl:  # falling edge: advance
            self._fall()
        self.scl, self.sda = scl, sda

    def _fall(self):
        if self.state == 'ack':
            self.drive = 1
            if self.reading:
                self.state, self.out, self.nbits = 'rdata', self.mem[self.ptr & 0x1FFF], 0
                self.ptr += 1
                self.drive = (self.out >> 7) & 1
            else:
                self.state = 'addr' if len(self.addr_bytes) < 2 else 'data'
                self.byte, self.nbits = 0, 0
            return
        if self.state == 'rdata':
            self.nbits += 1
            if self.nbits == 8:
                self.state, self.drive = 'mack', 1
            else:
                self.drive = (self.out >> (7 - self.nbits)) & 1
            return
        if self.state == 'mack':
            if self.mack:
                self.state, self.drive = 'idle', 1
            else:
                self.state, self.out, self.nbits = 'rdata', self.mem[self.ptr & 0x1FFF], 0
                self.ptr += 1
                self.drive = (self.out >> 7) & 1
            return
        if self.nbits == 8 and self.state in ('dev', 'addr', 'data'):
            b = self.byte
            if self.state == 'dev':
                if b & 0xFE != 0xA0:
                    self.state = 'idle'
                    return
                self.reading = bool(b & 1)
                if self.reading:
                    self.ptr = self.addr
            elif self.state == 'addr':
                self.addr_bytes.append(b)
                if len(self.addr_bytes) == 2:
                    self.addr = ((self.addr_bytes[0] << 8) | self.addr_bytes[1]) & 0x1FFF
                    self.ptr = self.addr
            else:
                self.writes.append(self.ptr)
                self.mem[self.ptr & 0x1FFF] = b
                self.ptr = (self.ptr & ~31) | ((self.ptr + 1) & 31)  # page wrap
            self.state, self.drive = 'ack', 0


class I2CSniffer:
    """Records write transactions on a bit-banged bus (START, bytes, STOP) and ACKs nothing.
    (The RDA5807 is never read: the MCU cannot read P3.6 back on this board.)"""
    def __init__(self):
        self.txns, self.cur, self.byte, self.n = [], None, 0, 0
        self.scl, self.sda = 1, 1

    def lines(self, scl, sda):
        if scl and self.scl and sda != self.sda:
            if not sda:
                self.cur, self.byte, self.n = [], 0, 0
            elif self.cur is not None:
                self.txns.append(self.cur)
                self.cur = None
        elif scl and not self.scl and self.cur is not None:
            if self.n < 8:
                self.byte = self.byte << 1 | sda
                self.n += 1
            else:
                self.cur.append(self.byte)
                self.byte, self.n = 0, 0
        self.scl, self.sda = scl, sda


PARITY = bytes(bin(i).count('1') & 1 for i in range(256))
# SFRs whose write does more than store the byte: run() sends those through wr(). TCON, SCON0, IE,
# 0xAA (the ADC interrupt's enable) and SCON1 are here as they decide when an interrupt or a received
# byte is next due (_events).
HOOKED = bytearray(256)
for _a in (0x81, 0x87, 0x88, 0x90, 0x96, 0x97, 0x98, 0x99, 0xA1, 0xA3, 0xA5, 0xA8, 0xAA, 0xB0, 0xD8, 0xDF, 0xEA,
           0xEB, 0xEE, *FLASH_SFRS):
    HOOKED[_a] = 1


class CPU:
    def __init__(self, code, eeprom=None):
        self.code = bytearray(code) + bytearray(65536 - len(code))
        self.iram = bytearray(256)
        self.sfr = bytearray(256)
        self.xram = bytearray(65536)
        self.icount = 0
        self.restart()
        self.sp_max = 7        # deepest the stack went: internal RAM ends at 0xFF
        self.resets = []       # BOOTCON (XSFR 0xF691) at each software reset: 0x55 the loader, 0xAA the app
        self.bk, self.ee, self.fm = BK4819(), EEPROM(eeprom), I2CSniffer()
        self.uart0_out, self.uart0_in, self.uart0_t = bytearray(), bytearray(), 0
        self.lcd = []          # (is_data, byte)
        self.uart_out, self.uart_in = bytearray(), bytearray()
        self.key = None
        self.adc = 3500
        self.wdt_kicks = 0
        self.bk_sda = 1
        self._sck = 0
        self.t1_count = 0
        self.strict_ri = False   # raise on MOVX @Ri: P2 paging on this MCU is unverified
        self.adc_pending = False
        self.uart_t = 0
        self.uart_lost = 0
        self.uart_baud = 1_000_000   # the computer's end of USB-C (None: any); a mismatch drops bytes both ways
        self.uart_garbled = 0     # bytes dropped for that
        self.uart_tx_due = 0      # icount when SBUF1's byte is out and TI sets
        self.idles = 0

    def restart(self):     # power-on or software reset; the loader isn't here, so it starts the app
        self.pc, self.in_isr, self.idle, self.ta_step = 0, [], False, 0   # idle: PCON IDLE till an interrupt
        self._ev = 0           # run(): the instruction count at which _events() must look again
        self.sfr[:] = bytes(256)
        self.sfr[0x81] = 7
        for p in PORTS:
            self.sfr[p] = 0xFF

    # ---- memory ----
    def ext_inputs(self, port):
        v = 0xFF
        if port == 0x80 or port == 0xA0:
            if self.key:
                # key: one name, or a tuple held together (PTT and a digit). A side key grounds its column.
                # A row drives its key's column low only as an output at 0; a row that is an input reads
                # its pull-up (P0UP 0xF00A, P2UP 0xF02A), or 0 floating, or 0 through a key whose column is grounded.
                held = [KEYS[k] for k in ((self.key,) if isinstance(self.key, str) else self.key)]
                gnd = {c for r, c in held if r is None}
                for r, c in held:
                    if r is None:
                        if port == 0xA0: v &= ~(1 << (3 + c))
                        continue
                    p, b = ROWBITS[r]
                    if self.sfr[PORTS[p]] >> b & 1:
                        if not self.sfr[p] >> b & 1 and port == 0xA0: v &= ~(1 << (3 + c))
                    elif c in gnd and port == p: v &= ~(1 << b)
            # rows that are inputs (TRIS bit 0) with no pull-up float and read 0: R1-R4 are P0.3-P0.0, R5-R6 P2.7-P2.6
            if port == 0x80:
                v &= ~(~self.sfr[0x9A] & ~self.xram[0xF00A] & 0x0F)
            else:
                v &= ~(~self.sfr[0xA2] & ~self.xram[0xF02A] & 0xC0)
        elif port == 0xD8:
            if not self.bk_sda:
                v &= ~0x04
            if not self.ee.drive:
                v &= ~0x01
        return v

    def rd(self, a, rmw=False):
        if a < 0x80:
            return self.iram[a]
        if a == 0xD0:
            return self.psw()
        if a in PORTS and not rmw:
            out = self.sfr[PORTS[a]]
            return (self.sfr[a] & out) | (self.ext_inputs(a) & ~out & 0xFF)
        return self.sfr[a]

    def psw(self):
        return self.sfr[0xD0] & 0xFE | PARITY[self.sfr[0xE0]]

    def wr(self, a, v):
        v &= 0xFF
        if a < 0x80:
            self.iram[a] = v
            return
        self._ev = 0           # anything here (or a callback from pins()) can bring an event forward
        if a in FLASH_SFRS:
            raise Halt('flash controller SFR 0x%02X written at PC 0x%04X' % (a, self.pc))
        old = self.sfr[a]
        self.sfr[a] = v
        if a == 0x87 and v & 3:  # PCON IDLE / STOP
            if v & 2:
                raise Halt('PCON STOP at PC 0x%04X: nothing here wakes it' % self.pc)
            if not self.sfr[0xA8] & 0x80:
                raise Halt('PCON IDLE with EA off at PC 0x%04X: never wakes' % self.pc)
            self.sfr[0x87] &= ~3
            self.idle = True
            self.idles += 1
        if a == 0x81 and v > self.sp_max:
            self.sp_max = v
        if a == 0x96:  # TA
            self.ta_step = 1 if v == 0xAA else (2 if v == 0x55 and self.ta_step == 1 else 0)
        elif a == 0x97 and v & 0x80 and self.ta_step == 2:   # WDCON SWRST after the TA unlock
            self.resets.append(self.xram[0xF691])
            self.restart()
        elif a == 0x97 and v & 1 and not old & 1:
            self.wdt_kicks += 1
            self.sfr[0x97] &= ~1
        elif a == 0xEE:  # SPDR
            self.lcd.append((self.sfr[0x80] >> 6 & 1, v))
            self.sfr[0xED] |= 0x80
        elif a == 0x99:  # SBUF0, Bluetooth UART
            self.uart0_out.append(v)
            self.sfr[0x98] |= 0x02
            self.sfr[0x99] = old
        elif a == 0xEB:  # SBUF1 (a write transmits; the receive side is uart_in)
            if self.uart_match():
                self.uart_out.append(v)
            else:
                self.uart_garbled += 1
            self.uart_tx_due = self.icount + self.uart_reload() * 8 // 3   # 10 bits: 800 instructions a ms
            self.sfr[0xEB] = old
        elif a == 0xDF and v & 0x02:  # ADC GO
            self.sfr[0xDF] &= ~0x02
            self.sfr[0xDD], self.sfr[0xDC] = self.adc >> 4, (self.adc & 15) << 4
            self.sfr[0xB2] |= 0x10      # EIF2 ADC flag
            self.adc_pending = True
        if a in (0xD8, 0xA5, 0x90, 0xA1):
            self.pins()
        if a in (0xB0, 0xA3):
            p3, t3 = self.sfr[0xB0], self.sfr[0xA3]
            self.fm.lines(p3 >> 7 & 1, (p3 >> 6 & 1) if t3 & 0x40 else 1)

    def pins(self):
        p5, t5, p1 = self.sfr[0xD8], self.sfr[0xA5], self.sfr[0x90]
        scn, sck, sda = p5 >> 3 & 1, p5 >> 4 & 1, p5 >> 2 & 1
        if scn:
            self.bk.deselect()
            self.bk_sda = 1
        elif sck and not self._sck:
            b = self.bk.edge(scn, True, sda if t5 & 0x04 else 1)
            if b is not None:
                self.bk_sda = b
        self._sck = sck
        ee_sda = (p5 & 1) if t5 & 0x01 else 1
        self.ee.lines(p1 >> 6 & 1, ee_sda & self.ee.drive)

    def rx(self, a):
        return self.xram[a]

    def wx(self, a, v):
        self.xram[a] = v & 0xFF

    def rbit(self, b, rmw=False):
        if b < 0x80:
            return self.iram[0x20 + (b >> 3)] >> (b & 7) & 1
        return self.rd(b & 0xF8, rmw) >> (b & 7) & 1

    def wbit(self, b, v):
        m = 1 << (b & 7)
        if b < 0x80:
            a = 0x20 + (b >> 3)
            self.iram[a] = self.iram[a] | m if v else self.iram[a] & ~m
            return
        a = b & 0xF8
        cur = self.rd(a, True)
        self.wr(a, cur | m if v else cur & ~m)

    # _step() keeps PC in a local: these two write through wr() with self.pc current, and hand back
    # the PC after (a software reset moves it).
    def _wd(self, a, v, pc):
        if a < 0x80:
            self.iram[a] = v & 0xFF
        elif HOOKED[a]:
            self.pc = pc
            self.wr(a, v)
            return self.pc
        else:
            self.sfr[a] = v & 0xFF
        return pc

    def _wb(self, b, v, pc):
        if b < 0x80:
            m, a = 1 << (b & 7), 0x20 + (b >> 3)
            self.iram[a] = self.iram[a] | m if v else self.iram[a] & ~m
            return pc
        a = b & 0xF8
        if HOOKED[a]:
            self.pc = pc
            self.wbit(b, v)
            return self.pc
        m, cur = 1 << (b & 7), self.rd(a, True)
        self.sfr[a] = cur | m if v else cur & ~m
        return pc

    # ---- registers ----
    @property
    def bank(self):
        return self.sfr[0xD0] & 0x18

    def R(self, i):
        return self.iram[self.bank + i]

    def setR(self, i, v):
        self.iram[self.bank + i] = v & 0xFF

    @property
    def A(self):
        return self.sfr[0xE0]

    @A.setter
    def A(self, v):
        self.sfr[0xE0] = v & 0xFF

    @property
    def C(self):
        return self.sfr[0xD0] >> 7

    @C.setter
    def C(self, v):
        self.sfr[0xD0] = (self.sfr[0xD0] & 0x7F) | (0x80 if v else 0)

    @property
    def dptr(self):
        return self.sfr[0x83] << 8 | self.sfr[0x82]

    @dptr.setter
    def dptr(self, v):
        self.sfr[0x83], self.sfr[0x82] = v >> 8 & 0xFF, v & 0xFF

    def push(self, v):
        sp = self.sfr[0x81] = (self.sfr[0x81] + 1) & 0xFF
        if sp > self.sp_max:
            self.sp_max = sp
        self.iram[sp] = v & 0xFF

    def pop(self):
        v = self.iram[self.sfr[0x81]]
        self.sfr[0x81] = (self.sfr[0x81] - 1) & 0xFF
        return v

    # ---- execution ----
    def vector(self, v):
        self.push(self.pc & 0xFF)
        self.push(self.pc >> 8)
        self.in_isr.append(v)
        self.pc = v

    def uart_reload(self):                 # BRTDH:BRTDL counts up to 65536 at Fsys/16
        return 65536 - (self.xram[0xF5C2] << 8 | self.xram[0xF5C1]) or 1

    def uart_match(self):                  # the radio's speed within 3% of the computer's
        return self.uart_baud is None or abs(3_000_000 / self.uart_reload() - self.uart_baud) < self.uart_baud * 0.03

    def step(self):
        self.run(1)

    def run(self, n, until=None):
        """Runs n instructions, or until until() is true after one (then returns True). until()
        only looks: a UART byte or interrupt bit it set would be seen at the next event, not at once.

        The suite spends nearly all its time here, so this is written for PyPy's JIT. An instruction
        is _step(), a call the JIT inlines: its temporaries never become loop state. The state
        that changes every instruction (pc, icount) stays in self, which the JIT keeps in place;
        a local carried round the loop would be boxed at every turn the trace takes. Timer0's
        tick is a countdown to the next multiple of TICK instead of a division every instruction.
        With no until(), PCON IDLE skips straight to the next thing that can wake it: a quarter
        of the suite's instructions are spent idle."""
        code, iram, sfr, xram = self.code, self.iram, self.sfr, self.xram
        self._due = (self.icount // TICK + 1) * TICK
        self._ev = 0                           # the caller may have changed anything since the last run
        end = self.icount + n
        skip = 0 if until else end
        while self.icount < end:
            self._step(skip, code, iram, sfr, xram)
            if until and until():
                return True
        return False

    def _events(self, icount, pc, skip, sfr):
        """Timer0's tick, the UART lines, interrupts and PCON IDLE: what can happen between
        instructions. Run only at self._ev, the next instruction count where one of them can do
        anything; wr() (every SFR that bears on them is HOOKED), RETI, restart() and run() set it
        to 0 to look again at once. Hands back the PC to run from, or -1 with the CPU idle.
        skip: with it non-zero, idle counts ahead to the next event, but not past skip."""
        if icount == self._due:
            self._due = icount + TICK
            if sfr[0x88] & 0x10:
                sfr[0x88] |= 0x20          # TF0 latches: a tick while EA is off or in an ISR waits, as on the chip
        if self.uart_tx_due and icount >= self.uart_tx_due:
            self.uart_tx_due = 0
            sfr[0xEA] |= 0x02
        if self.uart_in and icount - self.uart_t > 2000:   # the computer's bytes, 2.5 ms apart
            b = self.uart_in.pop(0)
            self.uart_t = icount
            if not self.uart_match():
                self.uart_garbled += 1
            elif sfr[0xEA] & 1:
                self.uart_lost += 1                # landed before the last one was read: lost, as on the radio
            else:
                sfr[0xEB] = b
                sfr[0xEA] |= 1
        if self.uart0_in and not sfr[0x98] & 1 and icount - self.uart0_t > 8000:
            sfr[0x99] = self.uart0_in.pop(0)  # one byte per ~1 ms at 9600
            sfr[0x98] |= 1
            self.uart0_t = icount
        # ---- interrupts ----
        ie = sfr[0xA8]
        if ie & 0x80 and (ie & 0x02 and sfr[0x88] & 0x20 or ie & 0x40 and sfr[0xEA] & 3 or ie & 0x08 and sfr[0x88] & 0x40
                          or self.adc_pending and sfr[0xAA] & 0x10) and not self.in_isr:
            v = 0
            if sfr[0x88] & 0x20 and ie & 0x02:
                sfr[0x88] &= ~0x20
                v = 0x0B
            elif ie & 0x40 and sfr[0xEA] & 3:
                v = 0x33                   # UART1, RI1 or TI1 (the handler clears them)
            elif self.adc_pending and sfr[0xAA] & 0x10:
                self.adc_pending = False
                v = 0x9B
            elif ie & 0x08 and sfr[0x88] & 0x40:
                self.t1_count += 1
                if self.t1_count % 500 == 0:
                    v = 0x1B
            if v:
                self.pc = pc
                self.vector(v)
                pc = v
        ie = sfr[0xA8]
        t1 = ie & 0x88 == 0x88 and sfr[0x88] & 0x40 and not self.in_isr   # Timer1 counts every instruction
        if self.idle:
            if not self.in_isr:
                if skip and not t1:
                    # Nothing runs and nothing pending changes till the next event: those
                    # instructions would only count, so count them at once.
                    nxt = min(self._due, skip + 1)
                    if self.uart_tx_due:
                        nxt = min(nxt, self.uart_tx_due)
                    if self.uart_in:
                        nxt = min(nxt, self.uart_t + 2001)
                    if self.uart0_in and not sfr[0x98] & 1:
                        nxt = min(nxt, self.uart0_t + 8001)
                    if nxt - 1 > icount:
                        self.icount = nxt - 1
                self._ev = 0
                self.pc = pc
                return -1
            self.idle = False
        if t1:
            self._ev = icount + 1
        else:
            ev = self._due
            if self.uart_tx_due:
                ev = min(ev, self.uart_tx_due)
            if self.uart_in:
                ev = min(ev, self.uart_t + 2001)       # the next byte from the computer
            if self.uart0_in and not sfr[0x98] & 1:   # (with RI0 set, only a write to SCON0 changes that)
                ev = min(ev, self.uart0_t + 8001)
            self._ev = ev
        return pc

    def _step(self, skip, code, iram, sfr, xram):
        """One instruction, or with the CPU idle, nothing. PC is a local here: self.pc is written
        before anything that can look at it (wr()'s hooks, through _wd/_wb) and at the end."""
        icount = self.icount + 1
        self.icount = icount
        pc = self.pc
        if icount >= self._ev:
            pc = self._events(icount, pc, skip, sfr)
            if pc < 0:
                return
        # ---- one instruction ----
        o = code[pc]
        pc = (pc + 1) & 0xFFFF
        lo = o & 15
        hi = o >> 4
        if lo >= 4 and 0x27C >> hi & 1:    # ADD ADDC ORL ANL XRL SUBB: A, #imm / direct / @Ri / Rn
            if lo == 4:
                v = code[pc]
                pc = (pc + 1) & 0xFFFF
            elif lo == 5:
                a = code[pc]
                pc = (pc + 1) & 0xFFFF
                v = iram[a] if a < 0x80 else self.rd(a)
            elif lo >= 8:
                v = iram[(sfr[0xD0] & 0x18) + lo - 8]
            else:
                v = iram[iram[(sfr[0xD0] & 0x18) + lo - 6]]
            a = sfr[0xE0]
            if hi == 4:
                sfr[0xE0] = a | v
            elif hi == 5:
                sfr[0xE0] = a & v
            elif hi == 6:
                sfr[0xE0] = a ^ v
            else:
                psw = sfr[0xD0]
                c = psw >> 7 if hi != 2 else 0
                psw &= 0x3B
                if hi == 9:
                    r = a - v - c
                    if r < 0: psw |= 0x80
                    if (a & 15) - (v & 15) - c < 0: psw |= 0x40
                    if (a ^ v) & (a ^ r) & 0x80: psw |= 0x04
                else:
                    r = a + v + c
                    if r > 255: psw |= 0x80
                    if (a & 15) + (v & 15) + c > 15: psw |= 0x40
                    if (a ^ r) & (v ^ r) & 0x80: psw |= 0x04
                sfr[0xD0] = psw
                sfr[0xE0] = r & 0xFF
        elif lo >= 6:                      # Rn (8-F) and @Ri (6, 7): ra is the internal RAM byte
            if lo >= 8:
                ra = (sfr[0xD0] & 0x18) + lo - 8
            else:
                ra = iram[(sfr[0xD0] & 0x18) + lo - 6]
            if hi == 0xE:
                sfr[0xE0] = iram[ra]
            elif hi == 0xF:
                iram[ra] = sfr[0xE0]
            elif hi == 7:
                iram[ra] = code[pc]
                pc = (pc + 1) & 0xFFFF
            elif hi == 0xD:
                if lo >= 8:                # DJNZ Rn
                    off = code[pc]
                    pc = (pc + 1) & 0xFFFF
                    v = (iram[ra] - 1) & 0xFF
                    iram[ra] = v
                    if v:
                        pc = (pc + (off - 256 if off > 127 else off)) & 0xFFFF
                else:                      # XCHD A, @Ri
                    v, a = iram[ra], sfr[0xE0]
                    iram[ra] = (v & 0xF0) | (a & 15)
                    sfr[0xE0] = (a & 0xF0) | (v & 15)
            elif hi == 0:
                iram[ra] = (iram[ra] + 1) & 0xFF
            elif hi == 1:
                iram[ra] = (iram[ra] - 1) & 0xFF
            elif hi == 8:                  # MOV direct, Rn
                a = code[pc]
                pc = (pc + 1) & 0xFFFF
                pc = self._wd(a, iram[ra], pc)
            elif hi == 0xA:                # MOV Rn, direct
                a = code[pc]
                pc = (pc + 1) & 0xFFFF
                iram[ra] = iram[a] if a < 0x80 else self.rd(a)
            elif hi == 0xB:                # CJNE Rn, #imm, rel
                imm, off = code[pc], code[(pc + 1) & 0xFFFF]
                pc = (pc + 2) & 0xFFFF
                v = iram[ra]
                sfr[0xD0] = (sfr[0xD0] & 0x7F) | (0x80 if v < imm else 0)
                if v != imm:
                    pc = (pc + (off - 256 if off > 127 else off)) & 0xFFFF
            else:                          # XCH A, Rn
                v = iram[ra]
                iram[ra] = sfr[0xE0]
                sfr[0xE0] = v
        elif lo == 1:                      # AJMP / ACALL
            a = ((pc + 1) & 0xF800) | (o >> 5) << 8 | code[pc]
            pc = (pc + 1) & 0xFFFF
            if o & 0x10:
                self.push(pc & 0xFF)
                self.push(pc >> 8)
            pc = a
        elif lo == 0:
            if hi >= 4 and hi <= 8:        # JC JNC JZ JNZ SJMP
                off = code[pc]
                pc = (pc + 1) & 0xFFFF
                if hi == 4: j = sfr[0xD0] & 0x80
                elif hi == 5: j = not sfr[0xD0] & 0x80
                elif hi == 6: j = sfr[0xE0] == 0
                elif hi == 7: j = sfr[0xE0] != 0
                else: j = True
                if j:
                    pc = (pc + (off - 256 if off > 127 else off)) & 0xFFFF
            elif hi == 0xE:                # MOVX A, @DPTR
                sfr[0xE0] = xram[sfr[0x83] << 8 | sfr[0x82]]
            elif hi == 0xF:                # MOVX @DPTR, A
                xram[sfr[0x83] << 8 | sfr[0x82]] = sfr[0xE0]
            elif hi == 9:                  # MOV DPTR, #imm
                sfr[0x83], sfr[0x82] = code[pc], code[(pc + 1) & 0xFFFF]
                pc = (pc + 2) & 0xFFFF
            elif hi >= 1 and hi <= 3:      # JBC JB JNB bit, rel
                b, off = code[pc], code[(pc + 1) & 0xFFFF]
                pc = (pc + 2) & 0xFFFF
                v = self.rbit(b, hi == 1)
                if v if hi != 3 else not v:
                    if hi == 1:
                        pc = self._wb(b, 0, pc)
                    pc = (pc + (off - 256 if off > 127 else off)) & 0xFFFF
            elif hi == 0xC:                # PUSH direct
                a = code[pc]
                pc = (pc + 1) & 0xFFFF
                self.push(iram[a] if a < 0x80 else self.rd(a))
            elif hi == 0xD:                # POP direct
                a = code[pc]
                pc = (pc + 1) & 0xFFFF
                v = self.pop()
                if a != 0xD0:
                    pc = self._wd(a, v, pc)
                else:
                    sfr[0xD0] = v
            elif hi == 0xA:                # ORL C, /bit
                b = code[pc]
                pc = (pc + 1) & 0xFFFF
                if not self.rbit(b): sfr[0xD0] |= 0x80
            elif hi == 0xB:                # ANL C, /bit
                b = code[pc]
                pc = (pc + 1) & 0xFFFF
                if self.rbit(b): sfr[0xD0] &= 0x7F
            # 0x00 NOP
        elif lo == 2:
            if hi == 1 or hi == 0:         # LCALL / LJMP
                a = code[pc] << 8 | code[(pc + 1) & 0xFFFF]
                pc = (pc + 2) & 0xFFFF
                if hi:
                    self.push(pc & 0xFF)
                    self.push(pc >> 8)
                pc = a
            elif hi == 2 or hi == 3:       # RET / RETI
                if hi == 3:
                    if self.in_isr:
                        self.in_isr.pop()
                    self._ev = 0           # an interrupt held back by this one may go now
                sp = sfr[0x81]
                pc = iram[sp] << 8 | iram[(sp - 1) & 0xFF]
                sfr[0x81] = (sp - 2) & 0xFF
            elif hi >= 4 and hi <= 6:      # ORL ANL XRL direct, A
                a = code[pc]
                pc = (pc + 1) & 0xFFFF
                v = iram[a] if a < 0x80 else self.rd(a, True)
                pc = self._wd(a, v | sfr[0xE0] if hi == 4 else v & sfr[0xE0] if hi == 5 else v ^ sfr[0xE0], pc)
            elif hi == 0xC or hi == 0xD:   # CLR / SETB bit
                b = code[pc]
                pc = (pc + 1) & 0xFFFF
                pc = self._wb(b, hi - 0xC, pc)
            elif hi == 0xE or hi == 0xF:   # MOVX A, @R0 / MOVX @R0, A
                if self.strict_ri:
                    self.pc = pc
                    raise Halt('MOVX @Ri at 0x%04X: P2 paging is unverified on this MCU' % ((pc - 1) & 0xFFFF))
                if hi == 0xE: sfr[0xE0] = xram[iram[sfr[0xD0] & 0x18]]
                else: xram[iram[sfr[0xD0] & 0x18]] = sfr[0xE0]
            elif hi == 7:                  # ORL C, bit
                b = code[pc]
                pc = (pc + 1) & 0xFFFF
                if self.rbit(b): sfr[0xD0] |= 0x80
            elif hi == 8:                  # ANL C, bit
                b = code[pc]
                pc = (pc + 1) & 0xFFFF
                if not self.rbit(b): sfr[0xD0] &= 0x7F
            elif hi == 9:                  # MOV bit, C
                b = code[pc]
                pc = (pc + 1) & 0xFFFF
                pc = self._wb(b, sfr[0xD0] >> 7, pc)
            elif hi == 0xA:                # MOV C, bit
                b = code[pc]
                pc = (pc + 1) & 0xFFFF
                sfr[0xD0] = (sfr[0xD0] & 0x7F) | (0x80 if self.rbit(b) else 0)
            else:                          # CPL bit
                b = code[pc]
                pc = (pc + 1) & 0xFFFF
                pc = self._wb(b, 1 - self.rbit(b, True), pc)
        elif lo == 3:
            if hi == 0xA:                  # INC DPTR
                d = (sfr[0x83] << 8 | sfr[0x82]) + 1
                sfr[0x83], sfr[0x82] = d >> 8 & 0xFF, d & 0xFF
            elif hi == 9:                  # MOVC A, @A+DPTR
                sfr[0xE0] = code[((sfr[0x83] << 8 | sfr[0x82]) + sfr[0xE0]) & 0xFFFF]
            elif hi == 0xC: sfr[0xD0] &= 0x7F          # CLR C
            elif hi == 0xD: sfr[0xD0] |= 0x80          # SETB C
            elif hi == 0xB: sfr[0xD0] ^= 0x80          # CPL C
            elif hi >= 4 and hi <= 6:      # ORL ANL XRL direct, #imm
                a, imm = code[pc], code[(pc + 1) & 0xFFFF]
                pc = (pc + 2) & 0xFFFF
                v = iram[a] if a < 0x80 else self.rd(a, True)
                pc = self._wd(a, v | imm if hi == 4 else v & imm if hi == 5 else v ^ imm, pc)
            elif hi <= 3:                  # RR RRC RL RLC
                a = sfr[0xE0]
                if hi == 0:
                    sfr[0xE0] = (a >> 1) | (a << 7 & 0x80)
                elif hi == 1:
                    sfr[0xE0] = (a >> 1) | (sfr[0xD0] & 0x80)
                    sfr[0xD0] = (sfr[0xD0] & 0x7F) | (a & 1) << 7
                elif hi == 2:
                    sfr[0xE0] = (a << 1 | a >> 7) & 0xFF
                else:
                    sfr[0xE0] = (a << 1 | sfr[0xD0] >> 7) & 0xFF
                    sfr[0xD0] = (sfr[0xD0] & 0x7F) | (a & 0x80)
            elif hi == 7:                  # JMP @A+DPTR
                pc = ((sfr[0x83] << 8 | sfr[0x82]) + sfr[0xE0]) & 0xFFFF
            elif hi == 8:                  # MOVC A, @A+PC
                sfr[0xE0] = code[(pc + sfr[0xE0]) & 0xFFFF]
            else:                          # MOVX A, @R1 / MOVX @R1, A
                if self.strict_ri:
                    self.pc = pc
                    raise Halt('MOVX @Ri at 0x%04X: P2 paging is unverified on this MCU' % ((pc - 1) & 0xFFFF))
                if hi == 0xE: sfr[0xE0] = xram[iram[(sfr[0xD0] & 0x18) + 1]]
                else: xram[iram[(sfr[0xD0] & 0x18) + 1]] = sfr[0xE0]
        elif lo == 4:
            if hi == 7:                    # MOV A, #imm
                sfr[0xE0] = code[pc]
                pc = (pc + 1) & 0xFFFF
            elif hi == 0xE: sfr[0xE0] = 0                       # CLR A
            elif hi == 0: sfr[0xE0] = (sfr[0xE0] + 1) & 0xFF   # INC A
            elif hi == 1: sfr[0xE0] = (sfr[0xE0] - 1) & 0xFF   # DEC A
            elif hi == 0xF: sfr[0xE0] ^= 0xFF                  # CPL A
            elif hi == 0xC: sfr[0xE0] = (sfr[0xE0] << 4 | sfr[0xE0] >> 4) & 0xFF   # SWAP A
            elif hi == 0xB:                # CJNE A, #imm, rel
                imm, off = code[pc], code[(pc + 1) & 0xFFFF]
                pc = (pc + 2) & 0xFFFF
                a = sfr[0xE0]
                sfr[0xD0] = (sfr[0xD0] & 0x7F) | (0x80 if a < imm else 0)
                if a != imm:
                    pc = (pc + (off - 256 if off > 127 else off)) & 0xFFFF
            elif hi == 8:                  # DIV AB: C and OV clear, OV set for /0 (A and B kept)
                a, b = sfr[0xE0], sfr[0xF0]
                if b == 0:
                    sfr[0xD0] = (sfr[0xD0] & 0x7B) | 0x04
                else:
                    sfr[0xE0], sfr[0xF0] = a // b, a % b
                    sfr[0xD0] &= 0x7B
            elif hi == 0xA:                # MUL AB: C clear, OV for a product over 255
                r = sfr[0xE0] * sfr[0xF0]
                sfr[0xE0], sfr[0xF0] = r & 0xFF, r >> 8
                sfr[0xD0] = (sfr[0xD0] & 0x7B) | (0x04 if r > 255 else 0)
            else:                          # DA A
                a, c = sfr[0xE0], sfr[0xD0] >> 7
                if (a & 15) > 9 or (sfr[0xD0] >> 6) & 1: a += 6
                if a > 0xFF: c = 1
                if ((a >> 4) & 0x1F) > 9 or c: a += 0x60
                if a > 0xFF: c = 1
                sfr[0xE0] = a & 0xFF
                sfr[0xD0] = (sfr[0xD0] & 0x7F) | (0x80 if c else 0)
        else:                              # lo 5: direct operand
            a = code[pc]
            pc = (pc + 1) & 0xFFFF
            if hi == 0xE:                  # MOV A, direct
                sfr[0xE0] = iram[a] if a < 0x80 else self.rd(a)
            elif hi == 0xF:                # MOV direct, A
                pc = self._wd(a, sfr[0xE0], pc)
            elif hi == 7:                  # MOV direct, #imm
                v = code[pc]
                pc = (pc + 1) & 0xFFFF
                pc = self._wd(a, v, pc)
            elif hi == 8:                  # MOV direct, direct (source first)
                d = code[pc]
                pc = (pc + 1) & 0xFFFF
                pc = self._wd(d, iram[a] if a < 0x80 else self.rd(a), pc)
            elif hi == 0 or hi == 1:       # INC / DEC direct
                v = iram[a] if a < 0x80 else self.rd(a, True)
                pc = self._wd(a, v + 1 if hi == 0 else v - 1, pc)
            elif hi == 0xD:                # DJNZ direct, rel
                off = code[pc]
                pc = (pc + 1) & 0xFFFF
                v = ((iram[a] if a < 0x80 else self.rd(a, True)) - 1) & 0xFF
                pc = self._wd(a, v, pc)
                if v:
                    pc = (pc + (off - 256 if off > 127 else off)) & 0xFFFF
            elif hi == 0xB:                # CJNE A, direct, rel
                off = code[pc]
                pc = (pc + 1) & 0xFFFF
                v = iram[a] if a < 0x80 else self.rd(a)
                r = sfr[0xE0]
                sfr[0xD0] = (sfr[0xD0] & 0x7F) | (0x80 if r < v else 0)
                if r != v:
                    pc = (pc + (off - 256 if off > 127 else off)) & 0xFFFF
            elif hi == 0xC:                # XCH A, direct
                v = iram[a] if a < 0x80 else self.rd(a)
                pc = self._wd(a, sfr[0xE0], pc)
                sfr[0xE0] = v
            else:
                self.pc = (pc - 1) & 0xFFFF
                raise Halt('unimplemented opcode 0x%02X at 0x%04X' % (o, (pc - 2) & 0xFFFF))
        self.pc = pc
