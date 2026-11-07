"""Minimal 8051 core plus TD-H3 peripheral models, for running the firmware on a PC.

Models: BK4819 3-wire bus (P5.2-P5.4), LCD over SPI (SPDR, D/C on P0.6), AT24C64 on
bit-banged I2C (SCL P1.6, SDA P5.0), keypad matrix, ADC, UART1, Timer0/1 interrupts.
Any write to the flash controller SFRs (0xFB-0xFF) raises: firmware must never touch them.
Timing is not cycle-accurate: Timer0 fires every TICK instructions.
"""

TICK = 4000
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
        self.t1_count = 0
        self.strict_ri = False   # raise on MOVX @Ri: P2 paging on this MCU is unverified
        self.adc_pending = False
        self.uart_t = 0
        self.uart_lost = 0
        self.uart_baud = 115200   # the computer's end of USB-C (None: any); a mismatch drops bytes both ways
        self.uart_garbled = 0     # bytes dropped for that
        self.uart_tx_due = 0      # icount when SBUF1's byte is out and TI sets
        self.idles = 0

    def restart(self):     # power-on or software reset; the loader isn't here, so it starts the app
        self.pc, self.in_isr, self.idle, self.ta_step = 0, [], False, 0   # idle: PCON IDLE till an interrupt
        self.sfr[:] = bytes(256)
        self.sfr[0x81] = 7
        for p in PORTS:
            self.sfr[p] = 0xFF

    # ---- memory ----
    def ext_inputs(self, port):
        v = 0xFF
        if port in (0x80, 0xA0) and self.key:
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
        if port in (0x80, 0xA0):
            for p, b in ROWBITS:
                if p == port and not self.sfr[PORTS[p]] >> b & 1 and not self.xram[0xF00A if p == 0x80 else 0xF02A] >> b & 1:
                    v &= ~(1 << b)          # floating input: no pull-up, reads 0
        if port == 0xD8:
            if not self.bk_sda:
                v &= ~0x04
            if not self.ee.drive:
                v &= ~0x01
        return v

    def rd(self, a, rmw=False):
        if a < 0x80:
            return self.iram[a]
        if a in PORTS and not rmw:
            out = self.sfr[PORTS[a]]
            return (self.sfr[a] & out) | (self.ext_inputs(a) & ~out & 0xFF)
        if a == 0xD0:
            return self.psw()
        return self.sfr[a]

    def psw(self):
        p = self.sfr[0xD0] & 0xFE
        return p | (bin(self.sfr[0xE0]).count('1') & 1)

    def wr(self, a, v):
        v &= 0xFF
        if a < 0x80:
            self.iram[a] = v
            return
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
        elif sck and not getattr(self, '_sck', 0):
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

    def bit_addr(self, b):
        return (0x20 + (b >> 3), b & 7) if b < 0x80 else (b & 0xF8, b & 7)

    def rbit(self, b, rmw=False):
        a, n = self.bit_addr(b)
        return self.rd(a, rmw) >> n & 1

    def wbit(self, b, v):
        a, n = self.bit_addr(b)
        cur = self.rd(a, True)
        self.wr(a, (cur | 1 << n) if v else (cur & ~(1 << n)))

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

    def setflags(self, c, ac, ov):
        self.sfr[0xD0] = (self.sfr[0xD0] & 0x3B) | (c << 7) | (ac << 6) | (ov << 2)

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

    def ind(self, i):  # @R0/@R1
        return self.R(i)

    def opnd_get(self, lo, rmw=False):  # lo nibble 4..F operand: #imm handled by caller
        if lo == 5:
            return self.rd(self.fetch(), rmw)
        if lo in (6, 7):
            return self.iram[self.R(lo - 6)]
        return self.R(lo - 8)

    # ---- execution ----
    def fetch(self):
        b = self.code[self.pc]
        self.pc = (self.pc + 1) & 0xFFFF
        return b

    def rel(self, off):
        return (self.pc + (off - 256 if off > 127 else off)) & 0xFFFF

    def add(self, v, carry=0):
        a = self.A
        r = a + v + carry
        ac = ((a & 15) + (v & 15) + carry) > 15
        ov = ((a ^ r) & (v ^ r) & 0x80) != 0
        self.setflags(r > 255, int(ac), int(ov))
        self.A = r

    def subb(self, v):
        a, c = self.A, self.C
        r = a - v - c
        ac = ((a & 15) - (v & 15) - c) < 0
        ov = ((a ^ v) & (a ^ r) & 0x80) != 0
        self.setflags(r < 0, int(ac), int(ov))
        self.A = r

    def interrupts(self):
        ie = self.sfr[0xA8]
        if self.icount % TICK == 0 and self.sfr[0x88] & 0x10:
            self.sfr[0x88] |= 0x20         # TF0 latches: a tick while EA is off or in an ISR waits, as on the chip
        if not ie & 0x80 or self.in_isr:
            return
        if self.sfr[0x88] & 0x20 and ie & 0x02:
            self.sfr[0x88] &= ~0x20
            self.vector(0x0B)
        elif self.adc_pending and self.sfr[0xAA] & 0x10:
            self.adc_pending = False
            self.vector(0x9B)
        elif ie & 0x08 and self.sfr[0x88] & 0x40:
            self.t1_count += 1
            if self.t1_count % 500 == 0:
                self.vector(0x1B)

    def vector(self, v):
        self.push(self.pc & 0xFF)
        self.push(self.pc >> 8)
        self.in_isr.append(v)
        self.pc = v

    def run(self, n, until=None):
        for _ in range(n):
            self.step()
            if until and until():
                return True
        return False

    def uart_reload(self):                 # BRTDH:BRTDL counts up to 65536 at Fsys/16
        return 65536 - (self.xram[0xF5C2] << 8 | self.xram[0xF5C1]) or 1

    def uart_match(self):                  # the radio's speed within 3% of the computer's
        return self.uart_baud is None or abs(3_000_000 / self.uart_reload() - self.uart_baud) < self.uart_baud * 0.03

    def step(self):
        self.icount += 1
        if self.uart_tx_due and self.icount >= self.uart_tx_due:
            self.uart_tx_due = 0
            self.sfr[0xEA] |= 0x02
        if self.uart_in and self.icount - self.uart_t > 2000:   # the computer's bytes, 2.5 ms apart
            b = self.uart_in.pop(0)
            self.uart_t = self.icount
            if not self.uart_match():
                self.uart_garbled += 1
            elif self.sfr[0xEA] & 1:
                self.uart_lost += 1                # landed before the last one was read: lost, as on the radio
            else:
                self.sfr[0xEB] = b
                self.sfr[0xEA] |= 1
        if self.uart0_in and not self.sfr[0x98] & 1 and self.icount - self.uart0_t > 8000:
            self.sfr[0x99] = self.uart0_in.pop(0)  # one byte per ~1 ms at 9600
            self.sfr[0x98] |= 1
            self.uart0_t = self.icount
        self.interrupts()
        if self.idle:
            if not self.in_isr:
                return
            self.idle = False
        pc0 = self.pc
        o = self.fetch()
        lo, hi = o & 15, o >> 4
        if lo == 1:  # AJMP / ACALL
            a = ((self.pc + 1) & 0xF800) | (o >> 5) << 8 | self.fetch()
            if o & 0x10:
                self.push(self.pc & 0xFF); self.push(self.pc >> 8)
            self.pc = a
            return
        if lo >= 4 and hi in (2, 3, 4, 5, 6, 9):
            if lo == 4:
                v = self.fetch()
            else:
                v = self.opnd_get(lo)
            if hi == 2: self.add(v)
            elif hi == 3: self.add(v, self.C)
            elif hi == 4: self.A = self.A | v
            elif hi == 5: self.A = self.A & v
            elif hi == 6: self.A = self.A ^ v
            else: self.subb(v)
            return
        if lo >= 6:
            ri = lo - 6 if lo < 8 else None
            def get():
                return self.iram[self.R(ri)] if ri is not None else self.R(lo - 8)
            def put(v):
                if ri is not None: self.iram[self.R(ri)] = v & 0xFF
                else: self.setR(lo - 8, v)
            if hi == 0: put(get() + 1); return
            if hi == 1: put(get() - 1); return
            if hi == 7: put(self.fetch()); return
            if hi == 8: self.wr(self.fetch(), get()); return
            if hi == 0xA: put(self.rd(self.fetch())); return
            if hi == 0xB:
                imm, off = self.fetch(), self.fetch()
                v = get()
                self.C = v < imm
                if v != imm: self.pc = self.rel(off)
                return
            if hi == 0xC:
                v = get(); put(self.A); self.A = v; return
            if hi == 0xD:
                if ri is not None:
                    v = get(); put((v & 0xF0) | (self.A & 15)); self.A = (self.A & 0xF0) | (v & 15)
                else:
                    off = self.fetch(); v = (get() - 1) & 0xFF; put(v)
                    if v: self.pc = self.rel(off)
                return
            if hi == 0xE: self.A = get(); return
            if hi == 0xF: put(self.A); return
        f = self.fetch
        if o == 0x00: return
        if o == 0x02: self.pc = f() << 8 | f(); return
        if o == 0x12:
            a = f() << 8 | f(); self.push(self.pc & 0xFF); self.push(self.pc >> 8); self.pc = a; return
        if o in (0x22, 0x32):
            if o == 0x32 and self.in_isr: self.in_isr.pop()
            hi8 = self.pop(); self.pc = hi8 << 8 | self.pop(); return
        if o == 0x03: self.A = (self.A >> 1) | (self.A << 7 & 0x80); return
        if o == 0x13: c = self.A & 1; self.A = (self.A >> 1) | (self.C << 7); self.C = c; return
        if o == 0x23: self.A = (self.A << 1) | (self.A >> 7); return
        if o == 0x33: c = self.A >> 7; self.A = (self.A << 1) | self.C; self.C = c; return
        if o == 0x04: self.A += 1; return
        if o == 0x14: self.A -= 1; return
        if o == 0x05: a = f(); self.wr(a, self.rd(a, True) + 1); return
        if o == 0x15: a = f(); self.wr(a, self.rd(a, True) - 1); return
        if o in (0x10, 0x20, 0x30):
            b, off = f(), f()
            v = self.rbit(b, o == 0x10)
            if (o == 0x30 and not v) or (o != 0x30 and v):
                if o == 0x10: self.wbit(b, 0)
                self.pc = self.rel(off)
            return
        if o in (0x40, 0x50, 0x60, 0x70, 0x80):
            off = f()
            cond = {0x40: self.C, 0x50: not self.C, 0x60: self.A == 0, 0x70: self.A != 0, 0x80: True}[o]
            if cond: self.pc = self.rel(off)
            return
        if o in (0x42, 0x52, 0x62):
            a = f(); v = self.rd(a, True)
            self.wr(a, v | self.A if o == 0x42 else v & self.A if o == 0x52 else v ^ self.A); return
        if o in (0x43, 0x53, 0x63):
            a, imm = f(), f(); v = self.rd(a, True)
            self.wr(a, v | imm if o == 0x43 else v & imm if o == 0x53 else v ^ imm); return
        if o == 0x72: self.C = self.C | self.rbit(f()); return
        if o == 0x82: self.C = self.C & self.rbit(f()); return
        if o == 0xA0: self.C = self.C | (1 - self.rbit(f())); return
        if o == 0xB0: self.C = self.C & (1 - self.rbit(f())); return
        if o == 0x73: self.pc = (self.dptr + self.A) & 0xFFFF; return
        if o == 0x83: self.A = self.code[(self.pc + self.A) & 0xFFFF]; return
        if o == 0x93: self.A = self.code[(self.dptr + self.A) & 0xFFFF]; return
        if o == 0x74: self.A = f(); return
        if o == 0x75: a = f(); self.wr(a, f()); return
        if o == 0x85: s = f(); d = f(); self.wr(d, self.rd(s)); return
        if o == 0x90: self.dptr = f() << 8 | f(); return
        if o == 0x84:
            a, b = self.A, self.sfr[0xF0]
            if b == 0: self.setflags(0, (self.sfr[0xD0] >> 6) & 1, 1); return
            self.A, self.sfr[0xF0] = a // b, a % b; self.setflags(0, (self.sfr[0xD0] >> 6) & 1, 0); return
        if o == 0xA4:
            r = self.A * self.sfr[0xF0]; self.A, self.sfr[0xF0] = r & 0xFF, r >> 8
            self.setflags(0, (self.sfr[0xD0] >> 6) & 1, int(r > 255)); return
        if o == 0x92: self.wbit(f(), self.C); return
        if o == 0xA2: self.C = self.rbit(f()); return
        if o == 0xA3: self.dptr = (self.dptr + 1) & 0xFFFF; return
        if o == 0xB2: b = f(); self.wbit(b, 1 - self.rbit(b, True)); return
        if o == 0xC2: self.wbit(f(), 0); return
        if o == 0xD2: self.wbit(f(), 1); return
        if o == 0xB3: self.C = 1 - self.C; return
        if o == 0xC3: self.C = 0; return
        if o == 0xD3: self.C = 1; return
        if o == 0xB4:
            imm, off = f(), f(); self.C = self.A < imm
            if self.A != imm: self.pc = self.rel(off)
            return
        if o == 0xB5:
            a, off = f(), f(); v = self.rd(a); self.C = self.A < v
            if self.A != v: self.pc = self.rel(off)
            return
        if o == 0xC0: self.push(self.rd(f())); return
        if o == 0xD0: a = f(); self.wr(a, self.pop()) if a != 0xD0 else self.sfr.__setitem__(0xD0, self.pop()); return
        if o == 0xC4: self.A = (self.A << 4 | self.A >> 4) & 0xFF; return
        if o == 0xC5: a = f(); v = self.rd(a); self.wr(a, self.A); self.A = v; return
        if o == 0xD4:
            a, c = self.A, self.C
            if (a & 15) > 9 or (self.sfr[0xD0] >> 6) & 1: a += 6
            if a > 0xFF: c = 1
            if ((a >> 4) & 0x1F) > 9 or c: a += 0x60
            if a > 0xFF: c = 1
            self.A, self.C = a, c; return
        if o == 0xD5:
            a, off = f(), f(); v = (self.rd(a, True) - 1) & 0xFF; self.wr(a, v)
            if v: self.pc = self.rel(off)
            return
        if o == 0xE0: self.A = self.rx(self.dptr); return
        if o in (0xE2, 0xE3, 0xF2, 0xF3) and self.strict_ri:
            raise Halt('MOVX @Ri at 0x%04X: P2 paging is unverified on this MCU' % pc0)
        if o in (0xE2, 0xE3): self.A = self.rx(self.R(o - 0xE2)); return
        if o == 0xE4: self.A = 0; return
        if o == 0xE5: self.A = self.rd(f()); return
        if o == 0xF0: self.wx(self.dptr, self.A); return
        if o in (0xF2, 0xF3): self.wx(self.R(o - 0xF2), self.A); return
        if o == 0xF4: self.A = ~self.A; return
        if o == 0xF5: self.wr(f(), self.A); return
        raise Halt('unimplemented opcode 0x%02X at 0x%04X' % (o, pc0))
