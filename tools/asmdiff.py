"""Differential test of the hand-written assembly (fw/asm/) against its C original.

    cd fw && make asm cref && python3 ../tools/asmdiff.py            # every function below
    python3 ../tools/asmdiff.py lcd_text_bg -n 5000                   # one, more cases

Boots build/td-h3-asm.bin (ASM=1, `make asm`) and build/td-h3-c.bin (all C, `make cref`) to the main loop once,
then for each case restores that state in both, sets the same inputs by symbol name, calls the
function with interrupts off and compares what it did: the bytes sent to the LCD, the REMOTE and
Bluetooth UARTs, the BK4819 and I2C traffic, the return registers, the port pins, every named
variable in internal RAM and XDATA (globals and statics, from the .rst listings) except the
function's own parameters and locals, and the stack pointer after the return. It also reports
each side's deepest stack. Addresses differ between the two builds, so everything goes by name.
"""
import argparse
import os
import random
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
FW = os.path.join(HERE, '..', 'fw')
sys.path.insert(0, FW)
import test_fw  # noqa: E402

RET = 0xF7F0            # past the image: zeros, never code
XBUF = 0x0C00           # test strings in XDATA: past the 1,280 B the firmware uses
CBUF = 0xF000           # and in code space, past the image (the emulator's code array is writable)
PTRS = {'_sl', '_ee_read_PARM_2'}   # XDATA pointers: compared and translated by what they point into
TIME = {'_ticks'}         # the tick count: an EEPROM read runs with interrupts on, so it moves with speed
K = {m.group(1): int(m.group(2), 0) for m in re.finditer(r'^(\w+) = (\S+)$', open(os.path.join(FW, 'build', 'asm.inc')).read(), re.M)}
CFG = lambda field, i=0: 'cfg+%d' % (K['CFG_' + field] + i)   # a cfg_t field, by asm.inc's offsets
SFR_SKIP = {0x81, 0x82, 0x83, 0xD0, 0xE0, 0xF0, 0x88, 0x8A, 0x8B, 0x8C, 0x8D}   # SP, DPTR, PSW, A, B; timer 0/1 counts and overflow flags (time, not behaviour)


class Image:
    def __init__(self, path):
        self.path = path
        d = os.path.join(os.path.dirname(path), os.path.basename(path)[:-4])
        self.code, self.vars = {}, {}            # name -> address; (module, name) -> (space, address, size)
        mp = open(os.path.join(d, 'fw.map')).read()
        for m in re.findall(r'^\S*/(\w+)\.rel\s', mp[mp.index('Files Linked'):], re.M):   # only what this image linked:
            self._rst(os.path.join(d, m + '.rst'), m)                                      # a build keeps stale listings
        self.cpu = test_fw.boot(path)
        self.cpu.uart_baud = None                # any speed: REMOTE's bytes are kept whatever the port's rate
        self.save()

    def save(self):
        c = self.cpu
        assert not c.in_isr, 'snapshot inside an interrupt'
        self.snap = (bytes(c.iram), bytes(c.sfr), bytes(c.xram), c.pc)

    def sync(self, other):                      # start from other's state: its SFRs and every variable both have
        for key, (s, a, size) in self.vars.items():
            if key in other.vars and size:
                n = min(size, other.vars[key][2])
                v = other.read(key)[:n]
                if key[1] in PTRS:
                    v = self.ptr(*other.meaning(v))
                self.write(s, a, v)
        self.cpu.sfr[:] = other.cpu.sfr
        self.save()

    def read(self, key):
        s, a, size = self.vars[key]
        if s == 'b':
            return bytes([self.cpu.iram[0x20 + a // 8] >> a % 8 & 1])
        return bytes((self.cpu.xram if s == 'x' else self.cpu.iram)[a:a + size])

    def write(self, s, a, data):
        if s == 'b':
            bit, at = 1 << a % 8, 0x20 + a // 8
            self.cpu.iram[at] = self.cpu.iram[at] | bit if data[0] else self.cpu.iram[at] & ~bit
        else:
            (self.cpu.xram if s == 'x' else self.cpu.iram)[a:a + len(data)] = data

    def meaning(self, v):                       # an XDATA pointer as (variable, offset)
        p = v[0] | v[1] << 8
        for (mod, n), (s, a, size) in self.vars.items():
            if s == 'x' and a <= p < a + max(size, 1):
                return n[1:], p - a
        return None, p

    def ptr(self, name, off):                   # the same in this image, low byte first
        p = off + (self.addr(name)[1] if name else 0)
        return bytes([p & 0xFF, p >> 8])

    def _rst(self, f, mod):
        area, last = None, None
        for line in open(f, errors='replace'):
            m = re.search(r'\.area\s+(\w+)', line)
            if m:
                area, last = m.group(1), None
                continue
            m = re.match(r'\s+([0-9A-F]{6})\s+\d+\s+(_\w+)::?\s*(?:\.ds\s+(\d+))?', line)
            if m:
                a, n = int(m.group(1), 16), m.group(2)
                if area in ('CSEG', 'HOME', 'GSINIT'):
                    self.code.setdefault(n, a)
                elif area == 'BSEG':                 # __bit: a bit address, 0x20.0 on
                    self.vars[(mod, n)] = ('b', a, 1)
                elif area in ('XSEG', 'DSEG', 'OSEG', 'ISEG'):
                    last = (mod, n)
                    self.vars[last] = ('x' if area == 'XSEG' else 'i', a, int(m.group(3) or 0))
                    if m.group(3):
                        last = None
                continue
            m = re.match(r'\s+[0-9A-F]{6}\s+\d+\s+\.ds\s+(\d+)', line)
            if m and last:
                s, a, _ = self.vars[last]
                self.vars[last] = (s, a, int(m.group(1)))
                last = None

    def addr(self, name):                       # a variable's address by its C name, any module
        for (mod, n), (s, a, size) in self.vars.items():
            if n == '_' + name:
                return s, a
        raise KeyError(name)

    def reset(self):
        c = self.cpu
        i, s, x, c.pc = self.snap
        c.iram[:], c.sfr[:], c.xram[:] = i, s, x
        c.idle, c.in_isr = False, []
        c.lcd, c.uart_out[:], c.uart0_out[:] = [], b'', b''
        c.bk.writes, c.bk.reads, c.fm.txns = [], [], []
        c.sp_max = 0

    def poke(self, name, data):                 # name or name+offset; data bytes, or ('&', var, offset): its address
        name, _, off = name.partition('+')
        s, a = self.addr(name)
        if isinstance(data, tuple):
            data = self.ptr(data[1], data[2])
        self.write(s, a + int(off or 0), bytes(data))

    def call(self, fn, dpl=0, stack=b'', code=None):
        c = self.cpu
        for a, v in (code or {}).items():
            c.code[a] = v
        c.sfr[0xA8] &= 0x7F                     # EA off: nothing else runs
        sp = c.sfr[0x81]
        for b in stack:                         # reentrant arguments, in push order
            sp += 1
            c.iram[sp] = b
        base = c.sfr[0x81]
        c.iram[sp + 1], c.iram[sp + 2] = RET & 0xFF, RET >> 8
        c.sfr[0x81], c.sfr[0x82], c.pc = sp + 2, dpl, self.code['_' + fn]
        c.sp_max = sp + 2
        assert c.run(20_000_000, until=lambda: c.pc == RET), '%s never returned' % fn
        return {
            'ret': (c.sfr[0x82], c.sfr[0x83], c.sfr[0xF0], c.sfr[0xE0])[:self.ret],
            'sp': c.sfr[0x81] - len(stack) - base,
            'lcd': c.lcd, 'uart': bytes(c.uart_out), 'uart0': bytes(c.uart0_out),
            'bk': list(c.bk.writes), 'fm': list(c.fm.txns),
            'sfr': {a: c.sfr[a] for a in range(0x80, 0x100) if a not in SFR_SKIP},
        }, c.sp_max - (sp + 2)

    def state(self, fn):                        # every named variable but fn's own
        out = {}
        for (mod, n), (s, a, size) in self.vars.items():
            if n.startswith('_%s_' % fn) or not size or n in TIME:
                continue
            v = self.read((mod, n))
            out[(mod, n)] = self.meaning(v) if n in PTRS else v
        return out


# ---- cases: each returns (pokes {var: bytes}, dpl, stack bytes, code {addr: byte}, xram {addr: bytes}) ----

def gen_lcd_text_bg(r):
    n = r.choice([0, 1, 1, 3, 5, 8, 12, 20, 25])
    s = bytes(r.choice([r.randrange(0x20, 0x7F)] * 6 + [r.randrange(1, 256)]) for _ in range(n)) + b'\0'
    in_code = r.random() < 0.4
    p = (CBUF if in_code else XBUF)
    scale = r.choice([1, 1, 1, 2, 2, 3, 4])
    pk = {'lcd_text_bg_PARM_2': [r.randrange(256)], 'lcd_text_bg_PARM_3': [p & 0xFF, p >> 8, 0x80 if in_code else 0],
          'lcd_text_bg_PARM_4': [scale], 'lcd_text_bg_PARM_5': r.randbytes(2), 'lcd_text_bg_PARM_6': r.randbytes(2),
          'text_pad': [r.choice([0, 0, 1, 2, 3])], 'mirror': [r.random() < 0.3]}
    code = {CBUF + i: b for i, b in enumerate(s)} if in_code else {}
    xram = {} if in_code else {XBUF: s}
    return pk, r.choice([0, r.randrange(130), r.randrange(256)]), b'', code, xram


def gen_lcd_icon(r):
    w, h = r.choice([0] + [r.randrange(1, 9)] * 20), r.choice([0] + [r.randrange(1, 11)] * 20)
    ic = bytes([w, h]) + r.randbytes(h)
    pk = {'mirror': [r.random() < 0.4]}
    stack = bytes([r.randrange(8), CBUF & 0xFF, CBUF >> 8, r.randrange(256)])   # fi, ic, y
    return pk, r.randrange(256), stack, {CBUF + i: b for i, b in enumerate(ic)}, {}


def gen_lcd_fill(r):
    w, h = r.choice([0, 1, 2, 8, 128, r.randrange(256)]), r.choice([0, 1, 9, r.randrange(1, 129), r.randrange(256)])
    stack = r.randbytes(2) + bytes([h, w, r.randrange(256)])      # c, h, w, y
    return {'mirror': [r.random() < 0.4]}, r.randrange(256), stack, {}, {}


def gen_draw_status(r):
    bit = lambda p=0.5: [r.random() < p]
    note = r.random() < 0.25
    act = r.randrange(2)
    pk = {'note': [CBUF & 0xFF, CBUF >> 8] if note else [0, 0],
          'saving': [r.choice([0, 0, 0, r.randrange(1, 200)])], 'entry_n': [r.choice([0, 0, r.randrange(1, 7)])],
          'entry': bytes(r.randrange(0x30, 0x3A) for _ in range(8)), 'overwrite': bit(0.3),
          'sl': ('&', 'cfg', K['CFG_S'] + K['SLOT_SIZE'] * act), CFG('ACT'): [act],
          CFG('S', K['SLOT_MEM']): [r.randrange(2)], CFG('S', K['SLOT_SIZE'] + K['SLOT_MEM']): [r.randrange(2)],
          'scanning': bit(0.4), CFG('GROUP'): [r.choice([0, 1, 2, 5, 0x80, 0xFF, r.randrange(256)])],
          CFG('SQL'): [r.randrange(19)], CFG('DW'): [r.randrange(256)], CFG('PRIO'): [r.choice([0, 0, r.randrange(1, 200)])],
          CFG('WX'): [r.choice([0, 0, r.randrange(1, 8)])], CFG('BCAL'): [r.randrange(-12, 13) & 0xFF],
          'mon': bit(0.3), 'rev': bit(0.3), 'locked': bit(0.3), 'rang': bit(0.3), 'peek': [r.randrange(4)],
          'torch': [r.randrange(4)], 'tlevel': [r.randrange(4)], 'mirror': bit(0.3),
          'tx': bit(0.3), 'dtmf_n': [r.choice([0, 0, r.randrange(1, 17)])], 'dt_mem': [r.choice([0, 0, 1])],
          'dtmf': bytes(r.choice(b'0123456789ABCD*#') for _ in range(r.randrange(17))) + b'\0',
          CFG('VOX'): [r.randrange(256)], CFG('KLOCK'): [r.randrange(256)],
          '@adc': r.choice([r.randrange(2500, 4096), r.randrange(4096)])}
    code = {CBUF + i: b for i, b in enumerate(b'LOW BATTERY\0')} if note else {}
    return pk, 0, b'', code, {}


def gen_draw_meter(r):
    bit = lambda p=0.5: [r.random() < p]
    dbm = r.choice([r.randrange(-260, 60), r.randrange(-150, -40), r.choice([-130, -129, -93, -92, -50, -51, -147, -148, -199, -200, 0, 1, 6, 7]),
                    r.randrange(-32768, 32768)])
    same = r.random() < 0.15
    pk = {'tx': bit(0.3), 'open': bit(), 'dbm': (dbm & 0xFFFF).to_bytes(2, 'little'),
          'm_dbm': (dbm & 0xFFFF).to_bytes(2, 'little') if same else r.randbytes(2), 'm_open': [r.choice([0, 1, 0xFF])],
          CFG('TOT'): [r.randrange(5)], CFG('SQL'): [r.choice([r.randrange(19), r.randrange(256)])], CFG('THEME'): [r.randrange(256)],
          'pal': r.randbytes(16), 'ticks': r.randbytes(2), 't_tx': r.randbytes(2), 'mirror': bit(0.3),
          '@bk64': r.choice([0, 1, r.randrange(0x10000), r.randrange(0x100)])}
    return pk, 0, b'', {}, {}


def chan(r):                                   # a random chan_t, as asm.inc lays it out
    c = bytearray(K['CHAN_SIZE'])
    rx = r.choice([14652000, 44610000, 16255000, 46256250, 46256875, 14652001, r.randrange(1800000, 99999999), r.randrange(1 << 31)])   # 12.5, 6.25 kHz and 10 Hz digits
    tx = r.choice([rx, 0, rx + 60000, rx - 500000, rx + 25, r.randrange(1800000, 99999999), r.randrange(1 << 31)])
    c[K['CHAN_RX']:K['CHAN_RX'] + 4] = rx.to_bytes(4, 'little')
    c[K['CHAN_TX']:K['CHAN_TX'] + 4] = (tx % (1 << 31)).to_bytes(4, 'little')
    tone = lambda: r.choice([0, 0, 885, 1000, 1413, 0x8000 | 0o23, r.randrange(0x10000)])
    rt = tone()
    c[K['CHAN_RXT']:K['CHAN_RXT'] + 2] = rt.to_bytes(2, 'little')
    c[K['CHAN_TXT']:K['CHAN_TXT'] + 2] = (rt if r.random() < 0.3 else tone()).to_bytes(2, 'little')
    c[K['CHAN_MODE']], c[K['CHAN_NARROW']], c[K['CHAN_POWER']] = r.randrange(3), r.randrange(2), r.randrange(4)   # power: pow_d's 4
    name = bytes(r.randrange(0x20, 0x7F) for _ in range(r.randrange(9)))
    c[K['CHAN_NAME']:K['CHAN_NAME'] + len(name) + 1] = name + b'\0'
    return bytes(c)


def gen_draw_slot(r):
    bit = lambda p=0.5: [r.random() < p]
    S = lambda n, f: CFG('S', n * K['SLOT_SIZE'] + K[f])
    pk = {CFG('ACT'): [r.randrange(2)], 'peek': [r.randrange(4)], 'tx': bit(0.3), 'open': bit(), 'fast': bit(), 'wxm': bit(0.2),
          'ch': chan(r), 'main_ch': chan(r), 'other': chan(r), 'pal': r.randbytes(16), CFG('THEME'): [r.randrange(256)],
          S(0, 'SLOT_MEM'): [r.randrange(2)], S(1, 'SLOT_MEM'): [r.randrange(2)], S(0, 'SLOT_CHN'): [r.randrange(200)], S(1, 'SLOT_CHN'): [r.randrange(200)],
          'tinted': [r.choice([0, 7, 8]), r.choice([0, 7, 8])], 'small': [r.randrange(4)], 'shown': r.randbytes(16), 'shown_c': r.randbytes(4),
          'dtmf_n': [r.randrange(6)], 'dtmf': bytes(r.choice(b'0123456789ABCD*#') for _ in range(r.randrange(17))) + b'\0',
          'heard': r.choice([0, 0, 1000, r.randrange(0x10000)]).to_bytes(2, 'little'), 'mirror': bit(0.3)}
    step = K['CFG_STEP']                       # step 0-11 and sstep 0-3: indexes into their tables
    pk['cfg+%d' % step] = [r.randrange(256) & ~K['CFG_STEP_MASK'] | r.randrange(12) << K['CFG_STEP_BIT']]
    pk['cfg+%d' % K['CFG_SSTEP']] = [r.randrange(256) & ~K['CFG_SSTEP_MASK'] | r.randrange(4) << K['CFG_SSTEP_BIT']]
    if r.random() < 0.5:                       # fast redraws of what's shown, so the unchanged digits are skipped
        pk['shown'] = b'146.520\0' * 2
    return pk, r.randrange(2), b'', {}, {}


CASES = {'lcd_text_bg': (gen_lcd_text_bg, 1), 'lcd_icon': (gen_lcd_icon, 0), 'lcd_fill': (gen_lcd_fill, 0),
         'draw_status': (gen_draw_status, 0), 'draw_meter': (gen_draw_meter, 0),
         'draw_slot': (gen_draw_slot, 0)}   # name: (cases, bytes returned in DPL, DPH, B, A)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('fn', nargs='*', default=sorted(CASES))
    ap.add_argument('-n', type=int, default=500)
    ap.add_argument('--seed', type=int, default=1)
    a = ap.parse_args()
    asm, c = Image(os.path.join(FW, 'build', 'td-h3-asm.bin')), Image(os.path.join(FW, 'build', 'td-h3-c.bin'))
    stale = subprocess.check_output([sys.executable, os.path.join(HERE, 'asmcheck.py'), 'stale'], text=True).split()
    if stale:
        print('asm stale (built from C, so nothing to compare): %s' % ' '.join(stale))
    asm.sync(c)
    bad = 0
    for fn in [f for f in a.fn if f not in stale]:
        r, before = random.Random(a.seed), bad
        gen, asm.ret = CASES[fn]
        c.ret = asm.ret
        deep = [0, 0]
        for k in range(a.n):
            pk, dpl, stack, code, xram = gen(r)
            got = []
            for side, img in enumerate((asm, c)):
                img.reset()
                for name, data in pk.items():
                    if name == '@adc':
                        img.cpu.adc = data
                    elif name.startswith('@bk'):         # a BK4819 register's reading, '@bk64'
                        img.cpu.bk.regs[int(name[3:], 16)] = data
                    else:
                        img.poke(name, data)
                for at, data in xram.items():
                    img.cpu.xram[at:at + len(data)] = data
                res, d = img.call(fn, dpl, stack, code)
                deep[side] = max(deep[side], d)
                got.append((res, img.state(fn)))
            (ra, sa), (rc, sc) = got
            diff = [key for key in ra if ra[key] != rc[key]]
            diff += ['%s.%s' % m for m in sa.keys() & sc.keys() if sa[m] != sc[m]]
            if diff:
                bad += 1
                print('%s case %d differs in %s: pokes %r dpl %d' % (fn, k, ', '.join(diff), pk, dpl))
                if 'sfr' in diff:
                    print('  sfr asm/C:', {'%02X' % x: (ra['sfr'][x], rc['sfr'][x]) for x in ra['sfr'] if ra['sfr'][x] != rc['sfr'][x]})
                if bad > 5:
                    sys.exit(1)
        print('%-16s %d cases %s; stack below the call: asm %d B, C %d B' % (fn, a.n, 'differ' if bad > before else 'identical', *deep))
    sys.exit(1 if bad else 0)


if __name__ == '__main__':
    main()
