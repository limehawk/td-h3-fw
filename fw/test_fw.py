"""Runs the built firmware in tools/emu51.py and checks what it does to the hardware.

    cd fw && make && make rxonly && python3 test_fw.py
    python3 test_fw.py scan fm             # only the feature tests whose names contain these words

Boot traffic (LCD init, BK4819 init) is compared live against stock 250317 when
stock/td-h3/250317/H3_250317.BIN is present.
"""
import glob
import os
import re
import sys
from functools import partial

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', 'tools'))
from emu51 import CPU  # noqa: E402

FW = os.path.join(HERE, 'build', 'td-h3.bin')
FW_RX = os.path.abspath(os.environ.get('FW_RX', os.path.join(HERE, 'build', 'td-h3-rx.bin')))   # screenshots: build/td-h3-shots.bin
STOCK = os.path.join(HERE, '..', 'stock', 'td-h3', '250317', 'H3_250317.BIN')

CAL_LOW, CAL_HIGH = 40, 120


def bcd(v, n):
    out = bytearray()
    for _ in range(n):
        out.append((v % 10) | (v // 10 % 10) << 4)
        v //= 100
    return bytes(out)


def eeprom(cal=True):
    ee = bytearray(b'\xff' * 8192)
    if cal:
        ee[0x1FF0] = 0x85
        ee[0x1F50:0x1F80] = bytes([CAL_LOW] * 16 + [CAL_HIGH] * 16 + [0x55] * 16)
    chans = {1: (14652000, 14652000, None, None, 'CALL2M', 0x01),   # scan group 1
             2: (46256250, 46256250, None, 1413, 'GMRS1', 0x02),    # group 2
             3: (16255000, 16255000, None, None, 'WX7', 0x01),      # group 1
             4: (44610000, 44610000, b'\x23\x80', b'\x54\xc7', 'DCS', 0x00)}   # D023N / D754I
    def tone(t):
        return t if isinstance(t, bytes) else bcd(t, 2) if t else b'\xff\xff'
    for n, (rx, tx, rt, tt, name, groups) in chans.items():
        rec = bcd(rx, 4) + bcd(tx, 4) + tone(rt) + tone(tt)
        rec += b'\x00\x00\x00' + bytes([groups])
        ee[0x10 * n:0x10 * n + 16] = rec
        ee[0x0D40 + 8 * (n - 1):0x0D40 + 8 * n] = name.encode().ljust(8, b'\xff')
        ee[0x1900 + (n - 1) // 8] &= ~0  # cleared below
    ee[0x1900:0x1919] = bytes(25)
    for n in chans:
        ee[0x1900 + (n - 1) // 8] |= 1 << ((n - 1) % 8)
    # settings (cfg in main.c, magic 0xAD): both slots in VFO on 146.520 / 446.000. SQUELCH 3; STEP 6,
    # MIC 5; POWER LOW; LIGHT 10 s; BEEP on; OFFSET 0.6; PAUSE BUSY, RESUME 5 s; TOT 3; TX BANDS 3; the rest 0
    import struct
    cfg = bytes([0xAD, 3, 0x56, 0x41, 0x10, 0x10, 0x14, 3]) + bytes(9) + bytes([3]) + bytes(5)
    cfg += struct.pack('<HHH', 0, 0, 1019) + struct.pack('<IBB', 14652000, 0, 1) + struct.pack('<IBB', 44600000, 0, 1)
    cfg += bytes(7)
    ee[0x1FC0:0x1FC0 + len(cfg)] = cfg
    return ee


def symbol(path, name):
    mapfile = os.path.join(os.path.dirname(path), os.path.basename(path)[:-4], 'fw.map')
    for line in open(mapfile):
        f = line.split()
        if len(f) >= 3 and f[0] in ('C:', 'D:') and f[2] == name:
            return int(f[1], 16)
    raise KeyError(name)


def boot(path=FW, cal=True, ee=None, key=None):
    cpu = CPU(open(path, 'rb').read(), ee or eeprom(cal))
    cpu.key = key                          # held from power-on
    cpu.strict_ri = True
    cpu.key_held_at = symbol(path, '_key_held')   # XDATA: the key the firmware has registered
    scan = symbol(path, '_key_scan')
    ok = cpu.run(12_000_000, until=lambda: cpu.pc == scan)
    assert ok, 'boot never reached the main loop within 12M instructions (pc %04X)' % cpu.pc
    assert (0x30, 0xBFF1) in cpu.bk.writes, 'BK4819 not in RX after boot'
    cpu.run(1_000_000)                     # first full screen draw (~535K instructions)
    return cpu


def freq(cpu):
    regs = cpu.bk.regs
    return regs.get(0x39, 0) << 16 | regs.get(0x38, 0)


def press(cpu, key, hold=250_000, after=700_000):   # short press: fires on release
    for _ in range(40):                    # the last key's release seen first, or the check below sees that key
        if not cpu.xram[cpu.key_held_at]: break
        cpu.run(50_000)
    cpu.key = key
    cpu.run(hold)
    # A full redraw takes ~600K instructions here (~40 ms on the radio): keep holding until the
    # firmware has seen the key, as a person's 100 ms press always outlasts it on the radio.
    for _ in range(40):
        if cpu.xram[cpu.key_held_at]: break
        cpu.run(50_000)
    cpu.key = None
    cpu.run(after)


def longpress(cpu, key):
    press(cpu, key, hold=1_400_000)


def keys(cpu, seq):
    for k in seq:
        press(cpu, k)


def peek16(cpu, a):                        # a 16-bit XDATA variable, little-endian as SDCC lays it out
    return cpu.xram[a] | cpu.xram[a + 1] << 8


def poke16(cpu, a, v):
    cpu.xram[a], cpu.xram[a + 1] = v & 0xFF, v >> 8 & 0xFF


def pins(cpu):
    p4, p5 = cpu.sfr[0xC0], cpu.sfr[0xD8]
    return {'VRX': not p4 >> 4 & 1, 'URX': not p5 >> 5 & 1, 'TXV': not p4 >> 5 & 1, 'TXU': not p4 >> 7 & 1,
            'APC': cpu.xram[0xF142]}


def test_boot_matches_stock(cpu):
    assert cpu.ee.writes == [], 'boot wrote EEPROM'
    assert cpu.wdt_kicks > 100
    if not os.path.exists(STOCK):
        print('  (stock image absent, skipped live comparison)')
        return
    st = CPU(open(STOCK, 'rb').read(), eeprom())
    st.code[0xEAB6] = 0x22                  # skip stock's busy-wait ms delay, timing only
    st.run(15_000_000, until=lambda: len(st.bk.writes) >= 45 and len(st.lcd) > 120)
    def init(lcd):
        out = []
        for d, b in lcd:
            if not d and b == 0x2A:
                break
            out.append((d, b))
        return out
    assert init(cpu.lcd) == init(st.lcd), 'LCD init differs from stock'
    assert cpu.bk.writes[:45] == st.bk.writes[:45], "BK4819 init (reset + table + AGC) differs from stock"


def test_vhf_rx_path(cpu):
    assert freq(cpu) == 14652000, freq(cpu)
    p = pins(cpu)
    assert p['VRX'] and not p['URX'] and not p['TXV'] and not p['TXU'] and p['APC'] == 0, p


def leds(cpu):                             # (green A, green B, red A, red B): left spot slot A, right B
    p1, p3 = cpu.sfr[0x90], cpu.sfr[0xB0]
    return p1 & 1, p3 & 1, p1 >> 2 & 1, p3 >> 1 & 1


def amber_blinks(cpu):                     # over 5.2 s: all four chips lit briefly, then dark, four times. A 100 ms
    seen = [leds(cpu) for _ in range(520) if cpu.run(8_000) or True]   # flash in a 2.6 s window hung on where a
    on = seen.count((1, 1, 1, 1))          # long draw or weather look fell (it read 7 lit at ASM=1)
    return 10 <= on <= 120 and (0, 0, 0, 0) in seen, on


def test_stock_squelch(cpu):
    # SQ3 on 146.520 wide: stock's VHF wide table, level 3 (0x6ACD/0x6AE1).
    assert cpu.bk.regs[0x78] == 0x5754 and cpu.bk.regs[0x4F] == 0x3D33, (hex(cpu.bk.regs[0x78]), hex(cpu.bk.regs[0x4F]))
    assert cpu.sfr[0xC0] >> 3 & 1 == 0, 'speaker on with squelch closed'
    cpu.bk.busy = lambda f: True
    cpu.run(1_000_000)
    assert cpu.sfr[0xC0] >> 3 & 1, 'speaker not on when the chip reports squelch open'
    assert leds(cpu) == (1, 0, 0, 0), 'green not lit on slot A\'s spot'
    cpu.bk.busy = None
    cpu.run(1_000_000)
    assert cpu.sfr[0xC0] >> 3 & 1 == 0, 'speaker stays on after squelch closes'
    assert leds(cpu) == (0, 0, 0, 0), 'green stays lit after squelch closes'


def test_keypad_entry_uhf(cpu):
    keys(cpu, '446000')
    assert freq(cpu) == 44600000, freq(cpu)
    p = pins(cpu)
    assert p['URX'] and not p['VRX'], p


def test_tx_allowed_band(cpu):
    cpu.key = 'PTT'
    cpu.run(300_000)
    p = pins(cpu)
    assert p['TXU'] and not p['TXV'] and not p['URX'] and not p['VRX'], p
    assert p['APC'] == CAL_LOW, p          # 446 MHz, low power: cal[5 + 4]
    assert freq(cpu) == 44600000 and cpu.bk.regs[0x30] == 0xC1FE
    assert leds(cpu) == (0, 0, 1, 0), 'red not lit on slot A\'s spot'
    cpu.key = None
    cpu.run(2_000_000)                     # 250 ms tail tone, then a full redraw
    assert leds(cpu) == (0, 0, 0, 0), 'red stays lit after transmit'
    p = pins(cpu)
    assert not p['TXU'] and p['APC'] == 0 and p['URX'] and cpu.bk.regs[0x30] == 0xBFF1, p


def test_keys_with_ptt(cpu):                # the keypad reads while PTT or PTT-B grounds its column (DTMF):
    at = symbol(FW, '_ptt_key')             # every key, through the rows' pull-ups for the grounded one
    code = {'0': 1, 'MENU': 11, 'UP': 12, 'DOWN': 13, 'EXIT': 14, '*': 15, '#': 16, **{str(d): d + 1 for d in range(1, 10)}}
    assert cpu.xram[0xF00A] & 0x0F == 0x0F and cpu.xram[0xF02A] & 0xC0 == 0xC0, 'row pull-ups off'
    for side in ('PTT', 'SIDE1'):
        cpu.key = side
        cpu.run(1_000_000)
        assert cpu.xram[at] == 0, (side, cpu.xram[at])
        for k, v in code.items():
            cpu.key = (side, k)
            driven_high = 0
            for _ in range(30):             # no row is ever driven high: a key would short it to the grounded column
                cpu.run(10_000)
                for p, b in ((0x80, 3), (0x80, 2), (0x80, 1), (0x80, 0), (0xA0, 7), (0xA0, 6)):
                    tris = 0x9A if p == 0x80 else 0xA2
                    driven_high |= cpu.sfr[tris] >> b & cpu.sfr[p] >> b & 1
            assert cpu.xram[at] == v, (side, k, cpu.xram[at])
            assert not driven_high, (side, k)
        cpu.key = None
        cpu.run(2_000_000)
    press(cpu, '4')                         # and alone, as ever
    assert cpu.xram[at] == 0


def dtmf_words(d):                         # REG_71, REG_72 for DTMF digit d (0-9, * 14, # 15): the standard's pair
    rc = {0: (3, 1), 14: (3, 0), 15: (3, 2), **{n: ((n - 1) // 3, (n - 1) % 3) for n in range(1, 10)}}[d]
    w = lambda hz: hz * 1032444 // 100000
    return w([697, 770, 852, 941][rc[0]]), w([1209, 1336, 1477][rc[1]])


def dtmf_sent(cpu, since):                 # the digits put on the air: a REG_70 tone-on after REG_71/72
    out, w = [], cpu.bk.writes[since:]
    for i, (r, v) in enumerate(w):
        if r == 0x70 and v == 0xD0D8:
            pair = [x for x in w[:i] if x[0] in (0x71, 0x72)][-2:]
            out.append(next(d for d in (*range(10), 14, 15) if dtmf_words(d) == (pair[0][1], pair[1][1])))
    return out


def test_dtmf_send(cpu):                   # keys held with PTT or PTT-B send their tone, mic muted; MENU 1-8 a stored code
    ee = eeprom()
    ee[0x1830:0x1837] = bytes([14, 7, 3, 0xFF, 0xFF, 0xFF, 0xFF])   # code 1: *73
    ee[0x1860:0x1867] = bytes(7)            # code 4: seven 0s, a factory radio's blank
    cpu = boot(ee=ee)
    menu(cpu, 'REMOTE', ['UP', 'EXIT'])
    cpu.key = 'PTT'
    cpu.run(800_000)
    n = len(cpu.bk.writes)
    for k in ('3', '#', '5'):
        cpu.key = ('PTT', k)
        cpu.run(300_000)
        assert cpu.bk.regs[0x70] == 0xD0D8 and cpu.bk.regs[0x50] == 0xBB20, (k, hex(cpu.bk.regs[0x70]))
        cpu.key = 'PTT'
        cpu.run(300_000)
        assert cpu.bk.regs[0x70] == 0 and cpu.bk.regs[0x50] == 0x3B20, (k, 'tone stuck on')
    assert dtmf_sent(cpu, n) == [3, 15, 5], dtmf_sent(cpu, n)
    cpu.key = 'PTT'
    assert 'DTMF 3#5' in remote_texts(cpu), remote_texts(cpu)
    n = len(cpu.bk.writes)
    for k in ('MENU', '1', 'MENU', '4'):    # code 1, then the blank code 4: nothing
        cpu.key = ('PTT', k)
        cpu.run(400_000)
        cpu.key = 'PTT'
        cpu.run(1_500_000)
    assert dtmf_sent(cpu, n) == [14, 7, 3], dtmf_sent(cpu, n)
    assert 'DTMF 3#5*73' in remote_texts(cpu), remote_texts(cpu)
    cpu.key = None
    cpu.run(2_000_000)
    assert not pins(cpu)['TXV'] and cpu.bk.regs[0x70] == 0
    cpu.key = 'SIDE1'                       # PTT-B too
    cpu.run(1_500_000)
    n = len(cpu.bk.writes)
    cpu.key = ('SIDE1', '9')
    cpu.run(300_000)
    cpu.key = None
    cpu.run(2_000_000)
    assert dtmf_sent(cpu, n) == [9], dtmf_sent(cpu, n)


def test_dtmf_codes(cpu):                  # DTMF CODES: type a code, V/M deletes a digit, MENU saves it as stock stores it
    ee = eeprom()
    ee[0x1860:0x1867] = bytes(7)            # code 4: a factory blank shows as empty
    cpu = boot(ee=ee)
    menu(cpu, 'REMOTE', ['UP', 'EXIT'])
    menu(cpu, 'DTMF', ['UP'])
    cpu.run(1_000_000)
    rows = remote_texts(cpu)
    assert '1 -' in [r.strip() for r in rows] and '4 -' in [r.strip() for r in rows], rows
    keys(cpu, ['DOWN', 'MENU', '1', '2', '9', 'EXIT', '*', '#', 'MENU'])   # code 2: 12, then * #
    cpu.run(1_000_000)
    assert bytes(cpu.ee.mem[0x1840:0x1847]) == bytes([1, 2, 14, 15, 0xFF, 0xFF, 0xFF]), bytes(cpu.ee.mem[0x1840:0x1847]).hex()
    assert '2 12*#' in [r.strip() for r in remote_texts(cpu)], remote_texts(cpu)
    keys(cpu, ['MENU'] + ['1'] * 9 + ['MENU'])   # 7 digits at most
    cpu.run(1_000_000)
    assert bytes(cpu.ee.mem[0x1840:0x1847]) == bytes([1, 2, 14, 15, 1, 1, 1]), bytes(cpu.ee.mem[0x1840:0x1847]).hex()
    keys(cpu, ['MENU'] + ['EXIT'] * 8)      # every digit off, then V/M again: left as it was
    cpu.run(1_000_000)
    assert bytes(cpu.ee.mem[0x1840:0x1847]) == bytes([1, 2, 14, 15, 1, 1, 1])
    assert all(not (0x1F50 <= a < 0x1F80 or a >= 0x1FF0) for a in cpu.ee.writes)


MORSE = {'.-': 'A', '-...': 'B', '-.-.': 'C', '-..': 'D', '.': 'E', '..-.': 'F', '--.': 'G', '....': 'H', '..': 'I',
         '.---': 'J', '-.-': 'K', '.-..': 'L', '--': 'M', '-.': 'N', '---': 'O', '.--.': 'P', '--.-': 'Q', '.-.': 'R',
         '...': 'S', '-': 'T', '..-': 'U', '...-': 'V', '.--': 'W', '-..-': 'X', '-.--': 'Y', '--..': 'Z', '-----': '0',
         '.----': '1', '..---': '2', '...--': '3', '....-': '4', '.....': '5', '-....': '6', '--...': '7', '---..': '8',
         '----.': '9', '-..-.': '/', '-....-': '-', '.-.-.-': '.'}


class Stamped(list):                       # BK4819 writes with the tick each was made on
    def __init__(self, cpu):
        super().__init__(cpu.bk.writes)
        self.cpu, self.at, self.t = cpu, symbol(FW, '_ticks'), [0] * len(cpu.bk.writes)
    def append(self, x):
        self.t.append(peek16(self.cpu, self.at))
        super().append(x)


def morse_sent(cpu, since, until=None):                # CALL ID's Morse from REG_70's tone 1 alone on and off, 20 WPM: a dot 12 ticks
    near = lambda d, n: n <= d <= n + 6    # the emulator's bit-banged writes run ~25x slow: a few ticks late
    w, t = cpu.bk.writes, cpu.bk.writes.t
    edges = [(t[i], v == 0xD000) for i in range(since, until or len(w)) for r, v in [w[i]]
             if r == 0x70 and v in (0xD000, 0)]
    out, sym, last_off, on = '', '', None, None
    for tick, up in edges:
        if up and on is None:
            if last_off is not None:
                gap = (tick - last_off) & 0xFFFF
                assert near(gap, 12) or near(gap, 36) or near(gap, 84), 'gap of %d ticks' % gap
                if gap > 24: out += MORSE[sym] + (' ' if gap > 60 else ''); sym = ''
            assert cpu.bk.regs.get(0x71) == 800 * 1032444 // 100000 and (0x50, 0xBB20) in w[since:], 'not 800 Hz, mic on'
            on = tick
        elif not up and on is not None:
            d = (tick - on) & 0xFFFF
            assert near(d, 12) or near(d, 36), 'element of %d ticks' % d
            sym += '.' if d < 24 else '-'
            on, last_off = None, tick
    return out + MORSE.get(sym, '?') if sym else out   # ? : cut off mid-letter


def ptt_tap(cpu, hold=800_000, after=9_000_000):   # talk, let go, wait out the ID (N0CALL/4: 7.6 s) and the tail
    cpu.key = 'PTT'
    cpu.run(hold)
    assert pins(cpu)['TXV'] or pins(cpu)['TXU'], 'not transmitting'
    cpu.key = None
    cpu.run(after)


def test_call_id_off(cpu):                 # CALL ID: OFF on a new radio (EEPROM FF), no Morse sent
    menu(cpu, 'REMOTE', ['UP', 'EXIT'])
    keys(cpu, ['MENU', '2', '0'])
    cpu.run(1_000_000)
    rows = remote_texts(cpu)
    assert any(r.startswith('TRANSMIT') and r.endswith('20/52') for r in rows), rows
    assert any(r.startswith('CALL ID') and r.endswith('OFF') for r in rows), rows
    keys(cpu, ['EXIT'])
    cpu.bk.writes = Stamped(cpu)
    n = len(cpu.bk.writes)
    ptt_tap(cpu)
    assert not pins(cpu)['TXV'] and morse_sent(cpu, n) == '', 'Morse sent with CALL ID OFF'


def test_call_id(cpu):                     # CALL ID: typed in CH NAME's editor; Morse on PTT release, once in 10 minutes
    menu(cpu, 'REMOTE', ['UP', 'EXIT'])
    menu(cpu, 'CALL', ['UP'])
    cpu.run(1_000_000)
    tap = lambda k: press(cpu, k, hold=100_000, after=100_000)
    for k in ['66', '00', '222', '2', '555', '555', '11', '4444']:   # N 0 C A L L / 4, all 8: a key's letters, then its digit
        for c in k: tap(c)
        cpu.run(1_500_000)                 # over a second: the next press starts a new character
    keys(cpu, ['MENU'])
    cpu.run(1_000_000)
    assert bytes(cpu.ee.mem[0x1E00:0x1E08]) == b'N0CALL/4', bytes(cpu.ee.mem[0x1E00:0x1E08])
    assert all(not (0x1F50 <= a < 0x1F80 or a >= 0x1FF0) for a in cpu.ee.writes)
    rows = remote_texts(cpu)
    assert any(r.startswith('CALL ID') and r.endswith('N0CALL/4') for r in rows), rows
    keys(cpu, ['EXIT'])                    # saved: back in the menu; V/M home
    cpu.bk.writes = Stamped(cpu)
    n = len(cpu.bk.writes)
    cpu.uart_out.clear()
    ptt_tap(cpu)
    assert morse_sent(cpu, n) == 'N0CALL/4', morse_sent(cpu, n)
    assert b'ID N0CALL/4' in bytes(cpu.uart_out), 'ID not on the screen'
    assert not pins(cpu)['TXV'] and cpu.bk.regs[0x70] == 0, 'still keyed, or the tone on'
    w = cpu.bk.writes[n:]
    assert w.index((0x51, 0x903E)) > max(i for i, x in enumerate(w) if x == (0x70, 0xD000)), 'tail tone before the ID'
    n = len(cpu.bk.writes)
    ptt_tap(cpu)                           # again within 10 minutes: nothing
    assert morse_sent(cpu, n) == '', 'ID again within 10 minutes'
    up10 = rst_addr('_up10')
    v = peek16(cpu, up10) + 60             # 10 minutes later
    poke16(cpu, up10, v)
    n = len(cpu.bk.writes)
    ptt_tap(cpu)
    assert morse_sent(cpu, n) == 'N0CALL/4', 'no ID after 10 minutes'
    keys(cpu, list('462562'))              # GMRS 1: never an ID
    cpu.run(1_000_000)
    v += 60
    poke16(cpu, up10, v)
    n = len(cpu.bk.writes)
    ptt_tap(cpu)
    assert freq(cpu) == 46256250 and morse_sent(cpu, n) == '', 'ID sent on GMRS'


def test_call_id_wrap(cpu):                # CALL ID: up10 wrapping (7.5 days on) doesn't stop the ID
    ee = eeprom()
    ee[0x1E00:0x1E08] = b'E\0\0\0\0\0\0\0'
    cpu = boot(ee=ee)
    cpu.bk.writes = Stamped(cpu)
    ptt_tap(cpu, after=2_000_000)          # the first ID since power-on
    up10, at = rst_addr('_up10'), rst_addr('_id_at')
    poke16(cpu, at, 0xFFF0)
    poke16(cpu, up10, 0xFFF0 + 59)        # 9 m 50 s later, past the wrap: not yet
    n = len(cpu.bk.writes)
    ptt_tap(cpu, after=2_000_000)
    assert morse_sent(cpu, n) == '', 'ID before 10 minutes'
    poke16(cpu, up10, 0xFFF0 + 60)        # 10 minutes, up10 now 0x002C
    n = len(cpu.bk.writes)
    ptt_tap(cpu, after=2_000_000)
    assert morse_sent(cpu, n) == 'E', 'no ID after up10 wrapped'


def test_call_id_cut(cpu):                 # CALL ID: PTT during it cuts it off and you're talking; TX TIMEOUT still ends it
    ee = eeprom()
    ee[0x1E00:0x1E08] = b'K0ABC\0\0\0'
    cpu = boot(ee=ee)
    cpu.bk.writes = Stamped(cpu)
    n = len(cpu.bk.writes)
    ptt_tap(cpu, after=600_000)            # into the ID: K sent (144 ticks with its gap), 0 going
    assert cpu.bk.regs[0x50] == 0xBB20 and pins(cpu)['TXV']
    cut = len(cpu.bk.writes)
    cpu.key = 'PTT'
    cpu.run(400_000)
    assert pins(cpu)['TXV'] and cpu.bk.regs[0x50] == 0x3B20 and cpu.bk.regs[0x70] == 0, 'not talking again'
    m = len(cpu.bk.writes)
    cpu.run(800_000)
    assert (0x70, 0xD000) not in cpu.bk.writes[m:], 'the ID went on'
    sent = morse_sent(cpu, n, cut)
    assert sent[:1] == "K" and len(sent) <= 2, sent   # K, maybe part of 0
    cpu.key = None                         # let go: the ID is still due
    cpu.run(300_000)
    t = symbol(FW, '_t_tx')                # TX TIMEOUT 120 s reached during it: unkeyed
    poke16(cpu, t, peek16(cpu, t) - 121 * 200)
    cpu.run(1_500_000)
    assert not pins(cpu)['TXV'] and cpu.bk.regs[0x70] == 0, 'TX TIMEOUT ignored'
    assert cpu.bk.writes[m:].count((0x70, 0xD000)) < 18, 'the whole ID sent past TX TIMEOUT'   # K0ABC: 18 elements


def tx_refused(cpu):
    n = len(cpu.bk.writes)
    cpu.key = 'PTT'
    cpu.run(600_000)
    p = pins(cpu)
    cpu.key = None
    cpu.run(800_000)
    assert not p['TXU'] and not p['TXV'] and p['APC'] == 0, p
    assert (0x30, 0xC1FE) not in cpu.bk.writes[n:]


def test_tx_refused_out_of_band(cpu):
    keys(cpu, '162550')                    # NOAA weather
    assert freq(cpu) == 16255000
    tx_refused(cpu)


def test_note_times_out(cpu):              # TX NOT ALLOWED flashes 3 s and goes with no key pressed
    menu(cpu, 'REMOTE', ['UP', 'EXIT'])    # remote_texts reads the screen through REMOTE
    keys(cpu, '162550')                    # NOAA weather: outside TX BANDS
    tx_refused(cpu)
    assert 'TX NOT ALLOWED' in remote_texts(cpu), remote_texts(cpu)   # drawn at once; the read runs ~3.7 s
    cpu.run(800_000)                       # 1 s more, no key
    assert 'TX NOT ALLOWED' not in remote_texts(cpu), 'the note never cleared'


def test_tx_bands(cpu):                     # TX BANDS: HAM GMRS by default; MURS on its own when picked
    menu(cpu, 'REMOTE', ['UP', 'EXIT'])    # remote_texts reads the screen through REMOTE
    menu(cpu, 'TXB')
    assert any(t.startswith('TX BANDS') and 'HAM GMRS' in t for t in remote_texts(cpu)), remote_texts(cpu)
    press(cpu, 'EXIT')                     # leave the menu (a second EXIT would be V/M)
    cpu.run(1_000_000)                     # its full redraw
    keys(cpu, '151820')                    # MURS channel 1
    tx_refused(cpu)
    menu(cpu, 'TXB', ['UP'])               # 3 -> 4: MURS only
    assert any(t.startswith('TX BANDS') and t.endswith('MURS') for t in remote_texts(cpu)), remote_texts(cpu)
    press(cpu, 'EXIT')                     # leave the menu (a second EXIT would be V/M)
    cpu.run(1_000_000)                     # its full redraw
    cpu.key = 'PTT'
    cpu.run(300_000)
    assert pins(cpu)['TXV'], 'MURS on, no TX on 151.820'
    cpu.key = None
    cpu.run(2_000_000)
    keys(cpu, '146520')                    # HAM is off now
    tx_refused(cpu)


def test_tail_tone(cpu):                    # TAIL TONE ON: 55 Hz on unkey; OFF: straight to receive; REVERSE: CTCSS phase-reversed
    def unkey_writes():
        cpu.key = 'PTT'
        cpu.run(300_000)
        n = len(cpu.bk.writes)
        cpu.key = None
        cpu.run(2_000_000)
        return cpu.bk.writes[n:]
    assert (0x07, 0x046F) in unkey_writes(), 'no 55 Hz tail with TAIL TONE on'
    assert cpu.bk.regs[0x19] == 0x1041, 'mic AGC off'
    menu(cpu, 'TAIL', ['UP', 'EXIT'])      # ON -> OFF
    w = unkey_writes()
    assert (0x07, 0x046F) not in w and (0x52, 0xC310) not in w, 'tail sent with TAIL TONE off'
    menu(cpu, 'TAIL', ['UP', 'EXIT'])      # REVERSE: no TX tone here, so the 55 Hz tail as before
    w = unkey_writes()
    assert (0x07, 0x046F) in w and (0x52, 0xC310) not in w, 'no-tone channel lost its 55 Hz tail'
    menu(cpu, 'TXT', ['UP', 'EXIT'])       # TX TONE 67.0
    w = unkey_writes()
    assert (0x52, 0xC310) in w and (0x07, 0x046F) not in w, 'no reverse burst on a CTCSS channel'
    assert cpu.bk.regs[0x51] == 0x904A, 'the tone itself was not kept on for the burst'


def test_compander(cpu):                   # COMPANDER: expander on receive, compressor on transmit, REG_31 bit 3 with each
    r = cpu.bk.regs
    def keyed():                           # REG_28, 29, 31 while transmitting
        cpu.key = 'PTT'
        cpu.run(300_000)
        got = r[0x28], r[0x29], r[0x31]
        cpu.key = None
        cpu.run(2_000_000)
        return got
    assert (r[0x28], r[0x31]) == (0x5638, 0) and keyed() == (0x6B00, 0xAB24, 0), 'compander on by default'
    menu(cpu, 'COMP', ['UP', 'EXIT'])      # RX
    assert (r[0x28], r[0x31]) == (0x6B38, 8), (hex(r[0x28]), hex(r[0x31]))
    assert keyed() == (0x6B00, 0xAB24, 0), 'RX only, yet the transmit side is on'
    menu(cpu, 'COMP', ['UP', 'EXIT'])      # TX
    assert (r[0x28], r[0x31]) == (0x5638, 0) and keyed() == (0x6B00, 0xAB40, 8)
    menu(cpu, 'COMP', ['UP', 'EXIT'])      # BOTH
    assert (r[0x28], r[0x31]) == (0x6B38, 8) and keyed() == (0x6B00, 0xAB40, 8)
    cpu.run(3_000_000)                     # saved 2 s after the last change
    assert cpu.ee.mem[0x1FC9] & 3 == 3, cpu.ee.mem[0x1FC0:0x1FF0].hex()


def test_narrow_khz(cpu):                  # NARROW kHz 6.25: narrow FM's filters (REG_43) narrower still; wide as it was
    r = cpu.bk.regs
    menu(cpu, 'NARKHZ', ['UP', 'EXIT'])    # 6.25
    assert r[0x43] == 0x3628, hex(r[0x43])
    menu(cpu, 'BW', ['UP', 'EXIT'])        # NARROW
    assert r[0x43] == 0x1248, hex(r[0x43])
    menu(cpu, 'NARKHZ', ['UP', 'EXIT'])    # 12.5
    assert r[0x43] == 0x4848, hex(r[0x43])


def test_ab_slots(cpu):
    press(cpu, '#')                        # A/B: slot B defaults to 446.000
    assert freq(cpu) == 44600000 and pins(cpu)['URX'], freq(cpu)
    press(cpu, '#')                        # back to A, 146.520
    assert freq(cpu) == 14652000 and pins(cpu)['VRX'], freq(cpu)


def test_memory_channels(cpu):
    press(cpu, 'EXIT')                     # V/M
    assert freq(cpu) == 14652000
    press(cpu, 'UP')
    assert freq(cpu) == 46256250, freq(cpu)
    press(cpu, 'UP')
    assert freq(cpu) == 16255000
    press(cpu, 'UP')
    assert freq(cpu) == 44610000
    press(cpu, 'UP')                       # wraps to channel 1
    assert freq(cpu) == 14652000
    press(cpu, 'EXIT')                     # back to VFO


def polymod(a, g=0xC75):                   # remainder of polynomial a over GF(2) divided by g
    while a.bit_length() >= g.bit_length():
        a ^= g << (a.bit_length() - g.bit_length())
    return a


def golay(code):                           # by definition: the parity that makes the codeword divisible by g
    data = code | 0x800
    parity = next(p for p in range(2048) if polymod(data << 11 | p) == 0)
    return data | parity << 12             # stored data in bits 11:0, parity in 22:12


def dcs_regs(code, inverted=False):
    cw = golay(code) ^ (0x7FFFFF if inverted else 0)
    return [(0x51, 0x8033), (0x07, 2775), (0x08, cw & 0xFFF), (0x08, 0x8000 | cw >> 12 & 0xFFF)]


def test_dcs(cpu):
    keys(cpu, ['EXIT', 'UP', 'UP', 'UP'])  # memory mode, channel 4
    assert freq(cpu) == 44610000
    n = len(cpu.bk.writes)
    keys(cpu, ['DOWN', 'UP'])              # retune channel 4 and watch the writes
    w = cpu.bk.writes[n:]
    rx = dcs_regs(0o23)
    assert any(w[i:i + 4] == rx for i in range(len(w))), 'D023N not programmed for RX'
    n = len(cpu.bk.writes)
    cpu.key = 'PTT'
    cpu.run(600_000)
    w = cpu.bk.writes[n:]
    cpu.key = None
    cpu.run(1_000_000)
    tx = dcs_regs(0o754, inverted=True)
    assert any(w[i:i + 4] == tx for i in range(len(w))), 'D754I not programmed for TX'
    press(cpu, 'EXIT')


MENU = ['SQUELCH', 'ATT', 'COMP', 'NARKHZ', 'BW', 'MODE', 'STEP', 'RXT', 'POWER', 'SHIFT', 'OFFS', 'TXT', 'TXB', 'TOT', 'TAIL', 'MIC', 'VOX', 'VOXD', 'DTMF', 'CALL',   # fw/main.c's M_ order
        'GROUP', 'PAUSE', 'RESUME', 'DW', 'PRIO', 'GLINK', 'GRP', 'SCOPE', 'FF', 'FM', 'WEATHER', 'LH', 'NAME', 'TOVFO', 'DEL',
        'LIGHT', 'BRIGHT', 'THEME', 'DIGITS', 'BEEP', 'KLOCK', 'BELL', 'WX', 'APO', 'SAVE', 'PC', 'REMOTE', 'BT', 'FCAL', 'MCAL', 'BCAL', 'VER']


def num(item):                             # the two digits that jump to an item
    return list('%02d' % (MENU.index(item) + 1))


ACTIONS = {'DTMF', 'CALL', 'SCOPE', 'FF', 'FM', 'WEATHER', 'PC', 'LH', 'GRP', 'GLINK', 'DEL', 'TOVFO', 'NAME'}


def menu(cpu, item, action=()):            # go to an item by its number; action: UP/DOWN changes it, EXIT leaves
    i = MENU.index(item) + 1               # an action item starts on MENU, so its first UP stands for that
    keys(cpu, ['MENU'])
    cpu.run(1_000_000)                     # opening clears and draws the whole screen
    keys(cpu, ['%d' % (i // 10), '%d' % (i % 10)])
    cpu.run(1_000_000)                     # the jump redraws the rows: slower when REMOTE mirrors them
    action = list(action)
    leave = action[-1:] == ['EXIT']
    if leave: action.pop()
    if item in ACTIONS: keys(cpu, ['MENU'] + action[1:] if action else [])
    elif action:                           # select, change, done
        keys(cpu, ['MENU'] + action)
        cpu.run(1_000_000)                 # REMOTE just switched on mirrors the redraw: slower
        keys(cpu, ['MENU'])
    if leave:
        keys(cpu, ['EXIT'])
        cpu.run(1_000_000)                 # leaving the menu redraws the whole screen (~25 ms on the radio)


def tuned(cpu, since=0):                   # frequencies written to the BK4819 (REG_39 then REG_38), never half-written
    out, hi = [], None
    for r, v in cpu.bk.writes[since:]:
        if r == 0x39:
            hi = v
        elif r == 0x38 and hi is not None:
            out.append(hi << 16 | v)
    return out


class Writes:                              # the FM chip's write transactions, live (status reads skipped)
    def __init__(self, cpu, since=0): self.cpu, self.since = cpu, since
    def _w(self): return [x for x in self.cpu.fm.txns if x[0] == 0x20][self.since:]
    def __getitem__(self, i): return self._w()[i]


def test_fm_radio(cpu):
    n = len(Writes(cpu)._w())              # boot's power-down came first
    menu(cpu, 'FM', ['UP'])
    cpu.run(4_000_000)                     # FM start-up: chip reset, 72-register table, presets, noise floor
    t = Writes(cpu, n)
    assert t[0] == [0x20, 0x00, 0x02], t[:1]
    table = open(STOCK, 'rb').read()[0xDA05:0xDA05 + 72] if os.path.exists(STOCK) else None
    assert len(t[1]) == 73 and t[1][0] == 0x20 and (table is None or bytes(t[1][1:]) == table), 'init table'
    assert t[2] == [0x20, 0xD2, 0xE1, 0x40, 0xD8], t[2]          # 101.9 MHz: (1019-760)<<6 | TUNE | band
    assert cpu.sfr[0xC0] >> 3 & 1, 'speaker amp off in FM'
    press(cpu, '*')                        # fine tune +0.1
    assert t[-1] == [0x20, 0xD2, 0xE1, 0x41, 0x18], t[-1]        # 102.0
    keys(cpu, ['MENU', '8', '8', '5', 'MENU'])   # MENU, then the frequency
    assert t[-1][3:] == [(125 << 6 | 0x18) >> 8, (125 << 6 | 0x18) & 0xFF], t[-1]   # 88.5
    press(cpu, 'EXIT')
    assert t[-1] == [0x20, 0x00, 0x00], 'FM chip not powered down'
    press(cpu, 'EXIT')
    assert cpu.bk.regs[0x30] == 0xBFF1, 'radio not back in RX after FM'


def fm_tuned(cpu):                         # the last channel written to the RDA5807, in 100 kHz
    w = [x for x in cpu.fm.txns if x[0] == 0x20 and len(x) >= 5 and x[4] & 0x10]
    return 760 + ((w[-1][3] << 8 | w[-1][4]) >> 6)


def test_fm_preset_junk(cpu):               # junk in a preset's upper bytes must read as empty, not 76.0
    ee = eeprom()
    ee[0x0CD0:0x0CD4] = bytes([0x96, 0x62, 0x06, 0x00])   # BCD 66296 = 65536 + 760: cut to 16 bits it was 76.0
    cpu = boot(ee=ee)
    menu(cpu, 'FM', ['UP'])
    cpu.run(4_000_000)
    press(cpu, '1')                        # an empty preset: stays on 101.9
    assert fm_tuned(cpu) == 1019, fm_tuned(cpu)


def test_fm_presets_after_scope(cpu):      # the scope and FM share one buffer: FM reads its presets afresh
    ee = eeprom()
    ee[0x0CD0:0x0CD4] = bytes([0x19, 0x10, 0x00, 0x00])   # preset 1 = 101.9, where FM opens
    cpu = boot(ee=ee)
    menu(cpu, 'SCOPE', ['UP'])
    cpu.run(3_000_000)                     # the sweep fills the shared buffer
    press(cpu, 'EXIT')
    menu(cpu, 'FM', ['UP'])
    cpu.run(4_000_000)
    longpress(cpu, '1')                    # held on preset 1's own station: clears it (needs the cache right)
    assert bytes(cpu.ee.mem[0x0CD0:0x0CD4]) == b'\xff' * 4, cpu.ee.mem[0x0CD0:0x0CD4].hex()


def test_fm_seek_presets(cpu):             # seek by BK4819 signal strength while the RDA5807 plays
    stations = {887, 1019, 1043}           # US raster: odd tenths; each spills 6 dB weaker into the next
    cpu.bk.dbm = lambda f: -70 if f // 10000 in stations else -76 if (f // 10000 - 2) in stations or (f // 10000 + 2) in stations else -120
    menu(cpu, 'FM', ['UP'])                # 101.9
    cpu.run(4_000_000)                     # start-up includes measuring the band's noise floor
    press(cpu, 'UP', hold=300_000); cpu.run(3_000_000)
    assert fm_tuned(cpu) == 1043, fm_tuned(cpu)
    press(cpu, 'UP', hold=300_000); cpu.run(20_000_000)   # ~160 channels measured (~0.4 s on the radio)
    assert fm_tuned(cpu) == 887, 'seek did not wrap past 108 MHz'
    press(cpu, 'DOWN', hold=300_000); cpu.run(20_000_000)
    assert fm_tuned(cpu) == 1043, 'seek down did not wrap past 76 MHz'
    longpress(cpu, '3')
    assert bytes(cpu.ee.mem[0x0CD8:0x0CDC]) == b'\x43\x10\x00\x00', cpu.ee.mem[0x0CD8:0x0CDC].hex()
    longpress(cpu, '3')                    # held again on its own station: cleared
    assert bytes(cpu.ee.mem[0x0CD8:0x0CDC]) == b'\xff' * 4, 'preset 3 not cleared'
    longpress(cpu, '3')                    # and saved again
    assert bytes(cpu.ee.mem[0x0CD8:0x0CDC]) == b'\x43\x10\x00\x00', 'preset 3 not saved again'
    press(cpu, '*')
    assert fm_tuned(cpu) == 1044, 'fine tune'
    press(cpu, '3')
    assert fm_tuned(cpu) == 1043, 'preset 3 did not recall'
    press(cpu, '5')                        # empty preset: stays put
    assert fm_tuned(cpu) == 1043
    cpu.run(1_000_000)                     # its refusal tone holds the keys while it plays
    n = len(cpu.bk.writes)
    press(cpu, 'EXIT')                     # leaving FM: the BK4819 goes back to sleep before the radio resumes
    assert (0x37, 0x1D00) in cpu.bk.writes[n:], 'BK4819 not put back to sleep when leaving FM'


def test_fm_seek_low_floor(cpu):           # a band floor under -128 dBm: int8 readings wrapped to +127 and noise
    cpu.bk.dbm = lambda f: -100 if f // 10000 == 1043 else -130   # outshone every station
    menu(cpu, 'FM', ['UP']); cpu.run(4_000_000)   # 101.9
    press(cpu, 'UP', hold=300_000)         # held: seek up
    cpu.run(20_000_000)
    assert fm_tuned(cpu) == 1043, fm_tuned(cpu)


def test_fm_side_key_leaves(cpu):          # side key 1 (PTT-B) leaves FM as PTT does, and transmits on B: it did nothing
    menu(cpu, 'FM', ['UP']); cpu.run(4_000_000)
    cpu.key = 'SIDE1'; cpu.run(3_000_000)
    p = pins(cpu)
    cpu.key = None; cpu.run(1_000_000)
    assert Writes(cpu, 0)[-1] == [0x20, 0x00, 0x00] and (p['TXU'] or p['TXV']) and freq(cpu) == 44600000, (p, freq(cpu))


def test_fm_auto_prompt_alert(cpu):        # WX ALERT on and the AUTO PRESETS? prompt left on screen: the alert still
    menu(cpu, 'WX', ['UP', 'EXIT'])        # leaves FM (the prompt waited for a key with no weather look)
    menu(cpu, 'FM', ['UP']); cpu.run(4_000_000)
    longpress(cpu, '0'); cpu.run(500_000)
    cpu.bk.alert = lambda f: f == 16240000
    cpu.run(12_000_000)
    assert Writes(cpu, 0)[-1] == [0x20, 0x00, 0x00] and freq(cpu) == 16240000, freq(cpu)


def test_fm_light_times_out(cpu):          # LIGHT 10 s holds in FM too (it stayed full on), and the first key on the
    menu(cpu, 'LIGHT', ['DOWN', 'EXIT'])   # dark screen only lights it. 10 s
    longpress(cpu, '8'); cpu.run(4_000_000)
    cpu.run(10 * 800_000)                  # past the light and any REMOTE mirror window
    assert cpu.xram[0xF140] == 0, 'backlight still %d in FM' % cpu.xram[0xF140]
    n = len(cpu.fm.txns)
    press(cpu, '#')                        # would nudge up 0.1 MHz
    assert cpu.xram[0xF140] and fm_tuned(cpu) == 1019, (cpu.xram[0xF140], fm_tuned(cpu))


def test_fm_flashlight(cpu):               # side key 2 works in FM as everywhere (it did nothing there)
    longpress(cpu, '8'); cpu.run(4_000_000)
    press(cpu, 'SIDE2'); cpu.run(2_000_000)
    assert torch(cpu), 'no flashlight in FM'


def test_fm_alert_in_weather_mode(cpu):    # weather mode, then FM: an alert heard there sounds the alarm on leaving
    menu(cpu, 'WX', ['UP', 'EXIT'])        # (FM closed, but weather mode skipped the look that raises it)
    cpu.bk.dbm = lambda f: -80 if f == 16240000 else -120   # WX1 strongest: weather mode on 162.400
    longpress(cpu, '#'); cpu.run(3_000_000)
    longpress(cpu, '8'); cpu.run(4_000_000)
    cpu.bk.alert = lambda f: f == 16240000
    cpu.run(9_000_000)
    ok, on = amber_blinks(cpu)
    assert Writes(cpu, 0)[-1] == [0x20, 0x00, 0x00] and ok, 'no alarm (%d lit)' % on


def fm_presets(cpu):                       # presets 1-25 from EEPROM, 0 = empty
    m = cpu.ee.mem
    return [0 if m[a] == 0xFF else (m[a] & 15) + (m[a] >> 4) * 10 + (m[a + 1] & 15) * 100 + (m[a + 1] >> 4) * 1000
            for a in range(0x0CD0, 0x0D34, 4)]


def fm_used(cpu):                          # CHIRP's fmusedflags at 0x1940, little-endian bits
    return int.from_bytes(bytes(cpu.ee.mem[0x1940:0x1944]), 'little')


def fm_stations(cpu, st, spill=6):         # made-up stations {100 kHz: dBm}, each spilling weaker 0.2 MHz either side
    def dbm(f):
        c = f // 10000
        n = [st[x] - spill for x in (c - 2, c + 2) if x in st]
        return st[c] if c in st else max(n) if n else -120
    cpu.bk.dbm = dbm


def test_fm_auto_presets(cpu):             # hold 0, then 0: the band's stations to presets in frequency order
    m = cpu.ee.mem
    m[0x0CD0:0x0CD4] = bcd(991, 4)         # old presets 1 and 12, and a stray CHIRP bit past preset 25
    m[0x0CD0 + 44:0x0CD0 + 48] = bcd(1055, 4)
    m[0x1940:0x1944] = (1 | 1 << 11 | 1 << 30).to_bytes(4, 'little')
    fm_stations(cpu, {883: -70, 951: -60, 1003: -75, 1047: -66})
    menu(cpu, 'REMOTE', ['UP', 'EXIT'])
    menu(cpu, 'FM', ['UP'])                # 101.9
    cpu.run(4_000_000)
    remote_texts(cpu)
    longpress(cpu, '0')
    assert status_line(cpu) == 'AUTO PRESETS? 0 = YES', status_line(cpu)
    press(cpu, '5')                        # anything but 0: no sweep, nothing written, 5 not acted on
    assert fm_tuned(cpu) == 1019 and fm_presets(cpu)[0] == 991 and fm_presets(cpu)[11] == 1055
    cpu.uart_in.append(0)                  # keep the mirror window open
    longpress(cpu, '0'); press(cpu, '0')
    assert status_line(cpu).startswith('PASS 1/3 FOUND'), status_line(cpu)
    press(cpu, 'UP')                       # any key stops it: presets as they were
    cpu.run(2_000_000)
    assert fm_presets(cpu)[0] == 991 and fm_presets(cpu)[11] == 1055 and fm_tuned(cpu) == 1019
    cpu.uart_in.append(0)
    longpress(cpu, '0'); press(cpu, '0')
    for _ in range(60):                    # three passes, ~105 s here with the mirror's wire time
        cpu.uart_in.append(0)
        cpu.run(2_000_000)
        if fm_presets(cpu)[0] == 883: break
    cpu.run(2_000_000)
    assert fm_presets(cpu) == [883, 951, 1003, 1047] + [0] * 21, (fm_presets(cpu), status_line(cpu), fm_tuned(cpu))
    assert fm_used(cpu) == 0b1111 | 1 << 30, hex(fm_used(cpu))   # stations' bits set, preset 12's cleared, bit 30 left
    assert fm_tuned(cpu) == 883, 'not playing preset 1'
    assert status_line(cpu) == 'SAVED 4', status_line(cpu)
    fm_stations(cpu, {})                   # an empty band: nothing changes
    longpress(cpu, '0'); press(cpu, '0')
    for _ in range(60):
        cpu.uart_in.append(0)
        cpu.run(2_000_000)
        if status_line(cpu) == 'NO STATIONS FOUND': break
    assert status_line(cpu) == 'NO STATIONS FOUND', status_line(cpu)
    assert fm_presets(cpu)[:4] == [883, 951, 1003, 1047] and fm_tuned(cpu) == 883


def test_fm_auto_presets_strongest(cpu):   # 30 stations: the 25 strongest, still in frequency order
    st = {881 + 6 * k: -50 - (k * 7) % 30 for k in range(30)}
    fm_stations(cpu, st, spill=40)         # packed 0.6 MHz apart: little spill, or each hides the next
    menu(cpu, 'FM', ['UP'])
    cpu.run(4_000_000)
    longpress(cpu, '0'); press(cpu, '0')
    for _ in range(60):
        cpu.run(2_000_000)
        if fm_presets(cpu)[0]: break
    cpu.run(2_000_000)
    want = sorted(sorted(st, key=lambda f: (-st[f], f))[:25])
    assert fm_presets(cpu) == want, (fm_presets(cpu), want)
    assert fm_used(cpu) & (1 << 25) - 1 == (1 << 25) - 1


def test_fm_auto_presets_score(cpu):      # three passes: a flicker, a one-off spike and splashes are not stations
    st = {883: -70, 1003: -75, 1047: -66, 1065: -40, 1077: -100}   # steady; 1065's splash 6 dB down is still strong
    reads = {}
    def dbm(f):
        c = f // 10000
        reads[c] = reads.get(c, 0) + 1
        p = (reads[c] + 1) // 2                               # two RSSI reads a sample: the first is thrown away
        if c == 951: return -55 if p % 2 else -120            # flickers: gone every other pass
        if c == 1011: return -50 if p == 1 else -120          # one spike, first pass only
        n = [st[x] - 6 for x in (c - 2, c + 2) if x in st]
        return st[c] if c in st else max(n) if n else -120
    cpu.bk.dbm = dbm
    menu(cpu, 'FM', ['UP'])
    cpu.run(4_000_000)
    longpress(cpu, '0'); press(cpu, '0')
    for _ in range(60):
        cpu.run(2_000_000)
        if fm_presets(cpu)[0]: break
    cpu.run(2_000_000)
    assert fm_presets(cpu) == [883, 1003, 1047, 1065, 1077] + [0] * 20, fm_presets(cpu)
    assert reads[1011] == 6 and reads[951] >= 6, (reads[1011], reads[951])   # three passes


def test_fm_preset_pages(cpu):             # tap 0: presets 10-18, then 19-25, then 1-9 again
    m = cpu.ee.mem
    m[0x0CD0 + 36:0x0CD0 + 40] = bcd(977, 4)   # preset 10
    m[0x0CD0 + 96:0x0CD0 + 100] = bcd(1065, 4)  # preset 25
    m[0x1940:0x1944] = bytes(4)
    menu(cpu, 'FM', ['UP'])                # 101.9
    cpu.run(4_000_000)
    press(cpu, '0')
    press(cpu, '1')
    assert fm_tuned(cpu) == 977, 'page 2 key 1 is not preset 10'
    press(cpu, '*')                        # 97.8
    longpress(cpu, '2')                    # saved as preset 11
    assert fm_presets(cpu)[10] == 978 and fm_used(cpu) == 1 << 10, (fm_presets(cpu)[10], hex(fm_used(cpu)))
    longpress(cpu, '2')                    # held on its own station: cleared, its bit too
    assert fm_presets(cpu)[10] == 0 and fm_used(cpu) == 0
    press(cpu, '0')
    press(cpu, '7')
    assert fm_tuned(cpu) == 1065, 'page 3 key 7 is not preset 25'
    cpu.run(1_000_000)
    press(cpu, '8')                        # no preset 26: stays put
    assert fm_tuned(cpu) == 1065
    cpu.run(1_000_000)
    longpress(cpu, '9')                    # nothing written past preset 25
    assert bytes(m[0x0D34:0x0D40]) == b'\xff' * 12 and fm_presets(cpu)[24] == 1065
    press(cpu, '0')                        # back to 1-9
    press(cpu, '1')
    assert fm_tuned(cpu) == 1065, 'empty preset 1 retuned'


def test_fm_hold_other_keys(cpu):          # holding MENU, the arrows, * or # saves no preset (was M10-M18)
    menu(cpu, 'FM', ['UP'])
    cpu.run(4_000_000)
    before = bytes(cpu.ee.mem[0x0CD0:0x0D40]) + bytes(cpu.ee.mem[0x1940:0x1944])
    for k in ['MENU', 'UP', 'DOWN', '*', '#']:
        longpress(cpu, k)
        cpu.run(1_000_000)
    assert bytes(cpu.ee.mem[0x0CD0:0x0D40]) + bytes(cpu.ee.mem[0x1940:0x1944]) == before, 'a held key saved a preset'


def test_fm_grid_numbers(cpu):             # each page: its slots top right, a key per preset, the slot playing
    m = cpu.ee.mem
    m[0x0CD0 + 36:0x0CD0 + 40] = bcd(977, 4)   # preset 10
    menu(cpu, 'REMOTE', ['UP', 'EXIT'])    # the grid's text, read through REMOTE
    menu(cpu, 'FM', ['UP'])
    cpu.run(4_000_000)
    for page in range(3):
        if page: press(cpu, '0')
        if page == 1: press(cpu, '1')       # M10
        cpu.uart_out.clear()
        cpu.uart_in.extend(b'S')
        cpu.run(3_000_000)
        t = {r[1:3]: r[3] for r in remote_records(cpu.uart_out) if r[0] == 't' and (r[2] in (0, 12, 110, 120) or 76 <= r[2] < 110)}
        keys_ = [t.get((x + 1, y)) for y in (76, 87, 98) for x in (0, 44, 88)]
        assert keys_ == [str(k) if page * 9 + k <= 25 else None for k in range(1, 10)], (page, keys_)
        assert t[(92, 0)].strip() == ['M1-9', 'M10-18', 'M19-25'][page], t[(92, 0)]
        assert t[(0, 110)] == '0 page  *# fine' and t[(0, 120)] == 'UP/DN seek  V/M back', t   # the keys
        if page: assert t[(100, 12)].strip() == 'M10', t[(100, 12)]   # the slot playing, on every page
        if page == 1: assert t[(10, 76)] == " 97.7", t   # M10: 97.7


def test_weather_mode_ignores_dual_watch(cpu):   # B busy (or the USB cable's noise): weather mode stays on WX
    menu(cpu, 'DW', ['UP', 'EXIT'])
    cpu.bk.busy = lambda f: f == 44600000
    longpress(cpu, '#')
    cpu.run(1_000_000)
    if not 16240000 <= freq(cpu) <= 16255000:   # it was in weather mode already: that long # left it
        longpress(cpu, '#')
        cpu.run(1_000_000)
    assert 16240000 <= freq(cpu) <= 16255000, freq(cpu)
    seen = []
    for _ in range(100):
        cpu.run(30_000)
        seen.append(freq(cpu))
    assert 44600000 not in seen, 'weather mode looked at B'
    cpu.bk.busy = None
    longpress(cpu, '#')                    # leave weather mode
    cpu.run(1_000_000)
    menu(cpu, 'DW', ['UP', 'EXIT'])


def test_dual_watch(cpu):
    menu(cpu, 'DW', ['UP', 'EXIT'])        # on A (146.520), watch B (446.000)
    cpu.bk.busy = lambda f: f == 44600000
    cpu.run(3_000_000)
    assert freq(cpu) == 44600000 and cpu.sfr[0xC0] >> 3 & 1, 'did not switch to the busy B slot'
    assert leds(cpu) == (0, 1, 0, 0), 'green not on slot B\'s spot for a dual-watch call on B'
    cpu.bk.busy = lambda f: False
    cpu.run(2_500_000)                     # 2 s hold after the signal drops
    seen = []
    for _ in range(300):                   # then mostly on main, with short peeks at the watch channel
        cpu.run(10_000)                    # (fine samples: 30 coarse ones hit or missed peeks by phase)
        seen.append(freq(cpu))
    assert seen.count(14652000) > 200 and 44600000 in seen, 'not back on A with peeks: %d of 300 on A' % seen.count(14652000)
    cpu.bk.busy = None
    menu(cpu, 'DW', ['UP', 'EXIT'])        # back to OFF


def rgb(r, g, b):                          # fw.h's RGB(): BGR565
    return (b & 0xF8) << 8 | (g & 0xFC) << 3 | r >> 3


def tints(cpu):                            # the backgrounds of slot A's and B's blocks (x 127: no text reaches it),
    import lcdshot                         # each down or up to the meter (rows 61-69): no dark rows between
    fb = lcdshot.frame(cpu.lcd)
    a, b = {fb[y][127] for y in range(13, 61)}, {fb[y][127] for y in range(70, 117)}
    assert len(a) == len(b) == 1, ('a block not one colour to the meter', a, b)
    assert fb[0][127] == 0, 'the status bar changed colour'
    return a.pop(), b.pop()


def test_tool_rows(cpu):                   # tools/radio.py and tdh3 read the slots and the bottom line where they're drawn
    sys.path.insert(0, os.path.join(HERE, '..', 'tools'))
    import radio
    src = open(os.path.join(HERE, 'main.c')).read()
    b, a = map(int, re.search(r'uint8_t y = i \? (\d+) : (\d+)', src).group(1, 2))
    go = open(os.path.join(HERE, '..', 'tools', 'tdh3', 'remote.go')).read()
    assert radio.SLOT_Y == {'A': a, 'B': b}, (radio.SLOT_Y, a, b)
    assert 'map[string]int{"A": %d, "B": %d}' % (a, b) in go, 'tdh3 slotY'
    assert 'sc.at(%d, 32)' % (a + 9) in go and 'sc.at(%d, 32)' % (b + 9) in go, 'tdh3 isHome'
    assert 'sc.at(120, 0)' in go and '(120, 0)' in open(radio.__file__).read(), 'the bottom line (BOT_Y)'


def test_slot_tints(cpu):                  # a slot heard on a green tint, the one transmitting on red
    import lcdshot
    green, red = rgb(6, 64, 28), rgb(74, 12, 0)
    assert tints(cpu) == (0, 0), 'idle: not black'
    cpu.bk.busy = lambda f: f == 14652000
    cpu.run(1_000_000)
    assert tints(cpu) == (green, 0), 'RX on A: no green tint on A'
    cpu.bk.busy = None
    cpu.run(1_000_000)
    assert tints(cpu) == (0, 0), 'the tint stays after squelch closes'
    cpu.key = 'PTT'
    cpu.run(600_000)                       # the slot, then the rows under it
    assert tints(cpu) == (red, 0), 'TX on A: no red tint on A'
    cpu.run(700_000)                       # the mic bar's first draw
    assert lcdshot.frame(cpu.lcd)[69][127] == red, 'TX: the meter track not in the TX tint'
    cpu.key = None
    cpu.run(2_000_000)
    assert tints(cpu) == (0, 0), 'the tint stays after transmit'
    menu(cpu, 'DW', ['UP', 'EXIT'])        # watch B (446.000): a call there tints B, not A
    cpu.bk.busy = lambda f: f == 44600000
    cpu.run(3_000_000)
    assert tints(cpu) == (0, green), 'dual-watch RX on B: no green tint on B'
    cpu.bk.busy = None
    cpu.run(3_000_000)
    menu(cpu, 'DW', ['UP', 'EXIT'])
    menu(cpu, 'THEME', ['UP', 'UP', 'UP', 'EXIT'])   # NIGHT RED: red only, dim on RX, brighter on TX
    cpu.bk.busy = lambda f: f == 14652000
    cpu.run(1_000_000)
    assert tints(cpu) == (rgb(40, 0, 0), 0), 'NIGHT RED: RX tint'
    cpu.bk.busy = None
    cpu.run(1_000_000)
    cpu.key = 'PTT'
    cpu.run(600_000)                       # the slot, then the rows under it
    assert tints(cpu) == (rgb(84, 0, 0), 0), 'NIGHT RED: TX tint'
    cpu.key = None
    cpu.run(2_000_000)
    assert tints(cpu) == (0, 0), 'NIGHT RED: the tint stays after transmit'


def test_dual_watch_waits_after_a_fade(cpu):   # a call on A fades for 0.2 s: no look at B then, so A comes straight back
    menu(cpu, 'DW', ['UP', 'EXIT'])        # on A (146.520), watch B (446.000)
    on = [True]
    cpu.bk.busy = lambda f: f == 14652000 and on[0]
    cpu.run(2_000_000)                     # A's station heard
    n = len(cpu.bk.writes)
    on[0] = False                          # the fade
    cpu.run(200_000)
    on[0] = True
    cpu.run(1_500_000)
    assert 44600000 not in tuned(cpu, n), 'looked at B during a fade on A'
    on[0] = False                          # the call ends: after 3 s the looks come back
    cpu.run(6_000_000)
    assert 44600000 in tuned(cpu, n), 'dual watch never looked again after the call'
    cpu.bk.busy = None


def test_dual_watch_wrong_tone(cpu):       # a carrier without the slot's RX tone doesn't hold the receiver on it
    menu(cpu, 'RXT', ['UP', 'EXIT'])       # RX tone 67.0 (both slots in frequency mode)
    menu(cpu, 'DW', ['UP', 'EXIT'])        # on A (146.520), watch B (446.000)
    cpu.bk.busy = lambda f: f == 44600000  # B has a carrier, never the tone
    cpu.run(1_000_000)
    run = longest = 0
    for _ in range(100):                   # 4M instructions: several looks at B
        cpu.run(40_000)                    # 10 ticks (50 ms)
        run = run + 1 if freq(cpu) == 44600000 else 0
        longest = max(longest, run)
    assert longest < 20, 'held on the wrong-tone carrier for %d ms' % (50 * longest)   # a look (slow here), not the 2 s hold
    cpu.bk.tonematch = lambda f: f == 44600000   # now with its tone: taken
    cpu.run(3_000_000)
    assert freq(cpu) == 44600000 and cpu.sfr[0xC0] >> 3 & 1, 'the right tone on B was not taken'


def test_bluetooth(cpu):
    menu(cpu, 'BT', ['UP'])
    assert cpu.sfr[0xB0] >> 3 & 1, 'BT module not enabled (P3.3)'
    keys(cpu, num('PC') + ['MENU'])        # PC LINK
    cpu.uart0_out.clear()
    cpu.uart0_in.extend(b'PVOJH\x5c\x14')
    cpu.run(600_000)
    assert bytes(cpu.uart0_out) == b'\x06', bytes(cpu.uart0_out)
    cpu.uart0_out.clear()
    cpu.uart0_in.extend(b'\x02')
    cpu.run(600_000)
    assert bytes(cpu.uart0_out) == b'P31183\xff\xff'
    assert not cpu.uart_out, 'answered on the USB port'
    keys(cpu, ['EXIT'])                    # leave PC LINK
    menu(cpu, 'BT', ['UP', 'EXIT'])        # off
    assert not cpu.sfr[0xB0] >> 3 & 1


def test_settings_saved_only_in_own_block(cpu):
    cpu.run(3_000_000)                     # let the 2 s save timer fire
    assert cpu.ee.writes, 'settings were never saved'
    assert all(0x1FC0 <= a < 0x1FF0 for a in cpu.ee.writes), [hex(a) for a in cpu.ee.writes]


def test_no_auto_pc_link(cpu):
    # PC LINK is menu-only: a host's hello on the main screen must not take the radio off the air.
    cpu.uart_baud = 38400                  # CHIRP's speed, the radio's own with REMOTE off
    cpu.uart_out.clear()
    for _ in range(4):
        cpu.uart_in.extend(b'PVOJH\x5c\x14')
        cpu.run(300_000)
    assert not cpu.uart_out, 'answered the clone hello outside PC LINK'
    assert cpu.bk.regs[0x30] == 0xBFF1, 'receiver left RX'


def test_pc_link_clone(cpu):
    menu(cpu, 'REMOTE', ['UP', 'EXIT'])    # REMOTE at 1 Mbaud ...
    cpu.uart_baud = 38400                  # ... and PC LINK back at CHIRP's 38400, then 1 Mbaud again after
    menu(cpu, 'PC', ['UP'])
    cal_before = bytes(cpu.ee.mem[0x1F50:0x1F80])
    out = cpu.uart_out
    def send(b, wait=300_000):
        out.clear()
        cpu.uart_in.extend(b)
        cpu.run(wait)
        return bytes(out)
    assert send(b'PVOJH\x5c\x14') == b'\x06'
    assert send(b'\x02') == b'P31183\xff\xff'
    assert send(b'\x06') == b'\x06'
    assert send(b'R\x00\x10\x20') == b'', 'answered a misaligned block'
    r = send(b'R\x00\x20\x20', 600_000)
    assert r[:4] == b'W\x00\x20\x20' and r[4:36] == bytes(cpu.ee.mem[0x20:0x40]) and r[36] == sum(r[4:36]) & 0xFF, r.hex()
    blk = bytes(range(32))
    assert send(b'W\x1f\x40\x20' + blk + bytes([sum(blk) & 0xFF]), 600_000) == b'\x06'
    assert bytes(cpu.ee.mem[0x1F40:0x1F50]) == blk[:16], 'unprotected half not written'
    assert bytes(cpu.ee.mem[0x1F50:0x1F80]) == cal_before, 'calibration was overwritten'
    assert send(b'W\x1f\xe0\x20' + blk + bytes([sum(blk) & 0xFF]), 600_000) == b'\x06'
    assert cpu.ee.mem[0x1FF0] == 0x85, 'stock magic was overwritten'
    assert send(b'E') == b'\x06'
    assert not cpu.uart_garbled, cpu.uart_garbled
    press(cpu, 'EXIT')                     # leave PC LINK
    press(cpu, 'EXIT')                     # leave menu
    assert cpu.bk.regs[0x30] == 0xBFF1, 'radio not back in RX after PC LINK'
    cpu.uart_baud = 1_000_000
    assert remote_texts(cpu), 'REMOTE not back at 1 Mbaud after PC LINK'


def test_pc_link_light_and_idle(cpu):       # PC LINK: dark after LIGHT, a key lights it, back to the radio after 10 quiet minutes
    import emu51
    menu(cpu, 'LIGHT', ['DOWN', 'EXIT'])   # 10 s
    cpu.uart_baud = 38400                  # PC LINK's speed
    menu(cpu, 'PC', ['UP'])
    assert cpu.xram[0xF140] == 200, 'PC LINK opened dark'
    cpu.run(12 * 800_000)
    assert cpu.xram[0xF140] == 0, 'PC LINK screen still lit after LIGHT'
    press(cpu, '5')
    assert cpu.xram[0xF140] == 200, 'a key left the PC LINK screen dark'
    cpu.uart_out.clear()
    cpu.uart_in.extend(b'PVOJH\x5c\x14')
    cpu.run(300_000)
    assert bytes(cpu.uart_out) == b'\x06', 'left PC LINK too soon'
    tick = emu51.TICK
    emu51.TICK = 200                       # 10 minutes of 100 ms waits, quickly
    try:
        for _ in range(80):
            cpu.run(1_000_000)
            if cpu.bk.regs[0x30] == 0xBFF1: break
    finally:
        emu51.TICK = tick
    assert cpu.bk.regs[0x30] == 0xBFF1, 'still in PC LINK after 10 quiet minutes'
    cpu.run(2_000_000)
    cpu.uart_out.clear()
    cpu.uart_in.extend(b'PVOJH\x5c\x14')
    cpu.run(600_000)
    assert not cpu.uart_out, 'answered the clone hello after leaving PC LINK'


def test_channel_power(cpu):                # hold 2 on a memory channel steps its own saved power, round; nothing
    press(cpu, 'EXIT')                     # else in the record changes (name, tones, groups, scan list)
    rec = lambda: bytes(cpu.ee.mem[0x10:0x20])
    before, name, bits = rec(), bytes(cpu.ee.mem[0x0D40:0x0D48]), bytes(cpu.ee.mem[0x1900:0x1940])
    assert before[14] & 0x30 == 0x00, 'channel 1 not saved LOW: %s' % before.hex()
    for want in (0x20, 0x10, 0x30, 0x00):  # MID, HIGH, ULTRA LOW, LOW
        longpress(cpu, '2')
        r = rec()
        assert r[14] & 0x30 == want, 'power bits %02x, want %02x' % (r[14] & 0x30, want)
        assert r[:14] + bytes([r[14] & 0xCF]) + r[15:] == before[:14] + bytes([before[14] & 0xCF]) + before[15:], r.hex()
    assert bytes(cpu.ee.mem[0x0D40:0x0D48]) == name and bytes(cpu.ee.mem[0x1900:0x1940]) == bits
    menu(cpu, 'REMOTE', ['UP', 'EXIT'])
    longpress(cpu, '2')                    # on MID: the slot shows it at once
    assert any('MID' in t for t in remote_texts(cpu)), remote_texts(cpu)

def test_pc_link_settings_reset(cpu):     # tdh3 reset: the settings' magic cleared, then E restarts with every default
    cpu.uart_baud = 38400
    menu(cpu, 'PC', ['UP'])
    out = cpu.uart_out
    def send(b, wait=300_000):
        out.clear()
        cpu.uart_in.extend(b)
        cpu.run(wait)
        return bytes(out)
    assert send(b'PVOJH\x5c\x14') == b'\x06' and send(b'\x02') and send(b'\x06') == b'\x06'
    r = send(b'R\x1f\xc0\x20', 600_000)
    blk = b'\x00' + r[5:36]
    assert send(b'W\x1f\xc0\x20' + blk + bytes([sum(blk) & 0xFF]), 600_000) == b'\x06'
    cal = bytes(cpu.ee.mem[0x1F50:0x1F80]) + bytes(cpu.ee.mem[0x1FF0:0x2000])
    assert not cpu.resets, 'restarted before the end of the session'
    assert send(b'E', 400_000) == b'\x06'
    assert cpu.resets == [0x55], 'no restart: %r' % cpu.resets
    scan = symbol(FW, '_key_scan')
    assert cpu.run(12_000_000, until=lambda: cpu.pc == scan), 'no boot after the restart'
    cpu.run(2_000_000)
    assert cpu.xram[symbol(FW, '_cfg') + 4] & 4, 'DUAL WATCH not back at its default ON'
    assert bytes(cpu.ee.mem[0x1F50:0x1F80]) + bytes(cpu.ee.mem[0x1FF0:0x2000]) == cal, 'calibration touched'
    assert cpu.bk.regs[0x30] == 0xBFF1, 'not receiving after the restart'


# ---- features added in 0.2 ----

def test_save_vfo_to_memory(cpu):
    longpress(cpu, 'MENU')                 # suggests the first free channel: 5
    press(cpu, 'MENU')
    m = cpu.ee.mem
    assert bytes(m[0x50:0x58]) == bcd(14652000, 4) * 2, bytes(m[0x50:0x60]).hex()
    assert m[0x1900] >> 4 & 1 and m[0x1920] >> 4 & 1, 'channel 5 not marked used / in scan list'
    assert bytes(m[0x0D40 + 32:0x0D40 + 40]) == b'146.520\xff', bytes(m[0x0D40 + 32:0x0D40 + 40])   # named by frequency, not band
    assert all(not (0x1F50 <= a < 0x1F80 or a >= 0x1FF0) for a in cpu.ee.writes)


def test_save_with_reverse_on(cpu):        # a repeater saved with reverse on keeps its input and output the right way round
    ee = eeprom(); ee[0x1FC3] = 0x49       # SHIFT -, POWER LOW, OFFSET 0.6: listen 146.520, transmit 145.920
    cpu = boot(ee=ee)
    longpress(cpu, '5')                    # reverse: listening on 145.920
    assert freq(cpu) == 14592000, freq(cpu)
    longpress(cpu, 'MENU'); press(cpu, 'MENU')   # to the suggested channel 5
    m = cpu.ee.mem
    assert bytes(m[0x50:0x58]) == bcd(14652000, 4) + bcd(14592000, 4) and bytes(m[0x0D60:0x0D67]) == b'146.520', \
        (bytes(m[0x50:0x58]).hex(), bytes(m[0x0D60:0x0D68]))
    assert freq(cpu) == 14592000, 'reverse went off'


def test_save_to_typed_channel_over_199(cpu):   # 300 must fail, not wrap to channel 44
    before = bytes(cpu.ee.mem[0x2C0:0x2D0])
    longpress(cpu, 'MENU')
    keys(cpu, ['3', '0', '0', 'MENU'])
    assert bytes(cpu.ee.mem[0x2C0:0x2D0]) == before, 'channel 44 overwritten'


def test_save_over_priority_channel(cpu):  # PRIORITY CH 3, then a frequency saved over channel 3: the look goes there
    menu(cpu, 'PRIO', ['UP', 'UP', 'UP', 'EXIT'])   # (it kept looking at the old 162.550 till a restart)
    keys(cpu, '446500')
    longpress(cpu, 'MENU')
    keys(cpu, ['0', '0', '3', 'MENU', 'MENU'])   # REPLACE 003? yes
    keys(cpu, '146520')
    cpu.bk.busy = lambda f: f == 44650000
    n = len(cpu.bk.writes)
    cpu.run(5_000_000)
    t = tuned(cpu, n)
    assert 44650000 in t and 16255000 not in t, t


def test_entry_after_slot_change(cpu):     # 446 typed on A, then PTT-B (B a channel): the next digit ends the entry.
    menu(cpu, 'REMOTE', ['UP', 'EXIT'])    # It ended only on exactly 3 digits and ran on past entry[8] into RAM
    keys(cpu, ['#', 'EXIT', '#'])          # B on a memory channel, A again
    keys(cpu, '446')
    cpu.key = 'SIDE1'; cpu.run(1_200_000)
    cpu.key = None; cpu.run(2_000_000)
    press(cpu, '5')
    t = remote_texts(cpu)
    assert 'NO SUCH CHANNEL' in t, t


def test_save_over_used_channel_asks(cpu):   # a typed channel that holds one: REPLACE 003?, MENU again writes
    menu(cpu, 'REMOTE', ['UP', 'EXIT'])
    remote_texts(cpu)                      # sync the mirror
    before = bytes(cpu.ee.mem[0x30:0x40])
    longpress(cpu, 'MENU')
    cpu.uart_in.append(0)                  # still watching: keeps the 10 s mirror window open
    keys(cpu, ['0', '0', '3', 'MENU'])
    cpu.run(600_000)
    assert status_line(cpu) == 'REPLACE 003? MENU=YES', status_line(cpu)
    assert bytes(cpu.ee.mem[0x30:0x40]) == before, 'channel 3 written before the second MENU'
    press(cpu, 'EXIT')                     # V/M: no save
    cpu.run(600_000)
    assert bytes(cpu.ee.mem[0x30:0x40]) == before, 'V/M still wrote channel 3'
    cpu.uart_in.append(0)                  # still watching: keeps the 10 s mirror window open
    longpress(cpu, 'MENU')
    keys(cpu, ['0', '0', '3', 'MENU'])
    cpu.run(600_000)
    assert status_line(cpu) == 'REPLACE 003? MENU=YES', status_line(cpu)
    press(cpu, 'MENU')                     # yes, write over it
    assert bytes(cpu.ee.mem[0x30:0x38]) == bcd(14652000, 4) * 2, bytes(cpu.ee.mem[0x30:0x40]).hex()
    cpu.run(600_000)
    assert status_line(cpu) == 'SAVED CH 003', status_line(cpu)


def test_up_down_stay_in_group(cpu):       # SCAN GROUP 1 (channels 1 and 3): UP/DOWN step through it like a bank
    menu(cpu, 'GROUP', ['UP', 'EXIT'])
    press(cpu, 'EXIT')                     # V/M: channel 1
    assert freq(cpu) == 14652000
    press(cpu, 'UP')
    assert freq(cpu) == 16255000, freq(cpu)   # channel 3, past 2
    press(cpu, 'UP')
    assert freq(cpu) == 14652000, freq(cpu)   # wraps inside the group
    press(cpu, 'DOWN')
    assert freq(cpu) == 16255000, freq(cpu)
    menu(cpu, 'GROUP', ['DOWN', 'EXIT'])   # back to the scan list: all channels again
    press(cpu, 'UP')
    assert freq(cpu) == 44610000, freq(cpu)


def test_up_down_all_groups(cpu):          # a channel in all eight groups (byte 15 FF, marked ours) is in group 1 too
    def patch(ee):
        ee[0x2D], ee[0x2F] = ee[0x2D] & ~3 | 1, 0xFF   # channel 2
    cpu = mem_ch1(patch)
    menu(cpu, 'GROUP', ['UP', 'EXIT'])
    press(cpu, 'UP')
    assert freq(cpu) == 46256250, freq(cpu)   # channel 2, not skipped to 3


def test_hold_repeats(cpu):
    press(cpu, 'UP', hold=2_500_000)       # 0.5 s, then a step every 0.1 s
    assert freq(cpu) >= 14652000 + 3 * 2500, freq(cpu)


def test_keypad_lock(cpu):
    longpress(cpu, '*')
    press(cpu, 'UP')
    assert freq(cpu) == 14652000, 'locked keypad still tuned'
    longpress(cpu, '*')
    press(cpu, 'UP')
    assert freq(cpu) == 14652500, freq(cpu)


def test_key_lock_ptt(cpu):                # KEY LOCK KEYS (default): PTT works locked. KEYS+PTT: PTT and PTT-B refused,
    longpress(cpu, '*')                    # slot B not taken, the flashlight still on; unlocked, PTT talks again
    cpu.key = 'PTT'
    cpu.run(600_000)
    assert pins(cpu)['TXU'] or pins(cpu)['TXV'], 'KEYS: PTT dead while locked'
    cpu.key = None
    cpu.run(2_000_000)
    longpress(cpu, '*')
    menu(cpu, 'REMOTE', ['UP', 'EXIT'])
    menu(cpu, 'KLOCK', ['UP', 'EXIT'])
    longpress(cpu, '*')
    tx_refused(cpu)
    n = len(cpu.bk.writes)
    cpu.key = 'SIDE1'
    cpu.run(600_000)
    p = pins(cpu)
    cpu.key = None
    cpu.run(800_000)
    assert not p['TXU'] and not p['TXV'] and (0x30, 0xC1FE) not in cpu.bk.writes[n:], p
    assert 'LOCKED: NO TX' in remote_texts(cpu), remote_texts(cpu)
    assert freq(cpu) == 14652000, 'PTT-B moved to slot B while locked'
    press(cpu, 'SIDE2')
    assert torch(cpu), 'flashlight dead with PTT locked'
    press(cpu, 'SIDE2')
    longpress(cpu, '*')
    cpu.key = 'PTT'
    cpu.run(600_000)
    assert pins(cpu)['TXV'], 'unlocked, PTT still refused'
    cpu.key = None
    cpu.run(2_000_000)


def test_step_raster(cpu):                 # 146.520 is off the 25 kHz raster
    press(cpu, 'DOWN')
    assert freq(cpu) == 14650000, freq(cpu)
    keys(cpu, ['UP', 'UP'])
    assert freq(cpu) == 14655000, freq(cpu)


def test_air_band_steps(cpu):              # 8.33 kHz lands on the real air-band channels (25 kHz / 3)
    menu(cpu, 'STEP', ['UP'] * 3 + ['EXIT'])   # 25 (default) -> 50 -> 100 -> 8.33
    keys(cpu, list('118000'))
    assert freq(cpu) == 11800000, freq(cpu)
    seen = []
    for k in ['UP'] * 3 + ['DOWN'] * 4:
        press(cpu, k)
        seen.append(freq(cpu))
    assert seen == [11800833, 11801667, 11802500, 11801667, 11800833, 11800000, 11799167], seen
    menu(cpu, 'STEP', ['UP', 'EXIT'])      # 15 kHz
    keys(cpu, list('146520') + ['UP'])
    assert freq(cpu) == 14653500, freq(cpu)


def test_battery_curve(cpu):               # percent follows a 2S lithium-ion curve, not a straight line
    menu(cpu, 'REMOTE', ['UP', 'EXIT'])
    got = []
    for adc in (4082, 3800, 3500, 3304):   # 8.40, 7.82, 7.20, 6.80 V
        cpu.adc = adc
        remote_texts(cpu)
        got.append([r[3] for r in remote_records(cpu.uart_out) if r[0] == 't' and r[2] == 0 and r[3].endswith('%')][-1])
    assert got == ['100%', '68%', '7%', '0%'], got


def test_scan_group(cpu):
    menu(cpu, 'GROUP', ['UP', 'EXIT'])     # group 1: channels 1 and 3
    keys(cpu, ['EXIT', '*'])               # memory mode, scan
    n = len(cpu.bk.writes)
    cpu.run(6_000_000)
    seen = set(tuned(cpu, n))
    assert seen <= {14652000, 16255000} and 16255000 in seen, seen


def test_group_link(cpu):                  # GROUP LINK 1+2: a scan covers channels 1, 2 and 3, never 4
    menu(cpu, 'REMOTE', ['UP', 'EXIT'])
    menu(cpu, 'GLINK', ['UP'])
    cpu.run(1_000_000)
    keys(cpu, ['MENU', 'DOWN', 'MENU', 'EXIT'])   # groups 1 and 2, back to the menu
    cpu.run(500_000)
    assert any('G12' in t for t in remote_texts(cpu)), [t for t in remote_texts(cpu) if 'GROUP' in t]
    keys(cpu, ['EXIT'])                    # leave the menu
    cpu.run(1_000_000)
    n = len(cpu.bk.writes)
    keys(cpu, ['EXIT', '*'])               # memory mode, scan
    cpu.run(6_000_000)
    seen = set(tuned(cpu, n))
    assert {14652000, 46256250, 16255000} <= seen and 44610000 not in seen, sorted(seen)
    assert ' SCAN G12' in status_bar(cpu), status_bar(cpu)
    keys(cpu, ['EXIT'])


def test_scan_group_switch(cpu):           # tap 2 while scanning group 1: now only channel 2, and it says so
    menu(cpu, 'REMOTE', ['UP', 'EXIT'])
    menu(cpu, 'GROUP', ['UP', 'EXIT'])
    keys(cpu, ['EXIT', '*'])
    assert 'SCAN G1' in ' '.join(remote_texts(cpu)), 'status does not show SCAN G1'
    cpu.uart_in.append(0)                  # still watching: keeps the 10 s mirror window open
    press(cpu, '2', hold=800_000)          # a scan step is ~330K instructions here (~16 ms on the radio)
    cpu.run(3_000_000)
    # what the radio drew by itself since the last sync (asking for a screen would force a full redraw)
    status = [r[3] for r in remote_records(cpu.uart_out) if r[0] == 't' and r[2] == 0 and r[1] == 0]
    assert status and 'SCAN G2' in status[-1], status[-3:]
    cpu.run(600_000)
    n = len(cpu.bk.writes)
    cpu.run(3_000_000)
    seen = set(tuned(cpu, n))
    assert seen <= {46256250} and freq(cpu) == 46256250, (seen, freq(cpu))   # group 2 is one channel: it sits there


def status_bar(cpu):                       # the last status-bar text (top line) the radio drew by itself
    s = [r[3] for r in remote_records(cpu.uart_out) if r[0] == 't' and r[2] == 0 and r[1] == 0]
    return s[-1].strip() if s else None


def status_line(cpu):                      # the last bottom-line text (messages, typing) the radio drew by itself
    s = [r[3] for r in remote_records(cpu.uart_out) if r[0] == 't' and r[2] == 120 and r[1] == 0]
    return s[-1].strip() if s else None


def test_empty_group_mid_scan(cpu):        # tap 5 (no channels) while scanning group 1
    menu(cpu, 'REMOTE', ['UP', 'EXIT'])
    menu(cpu, 'GROUP', ['UP', 'EXIT'])
    keys(cpu, ['EXIT', '*'])
    remote_texts(cpu)                      # sync the mirror
    press(cpu, '5', hold=800_000)
    cpu.run(3_000_000)
    assert status_line(cpu) == 'GROUP IS EMPTY', status_line(cpu)
    n = len(cpu.bk.writes)
    cpu.run(3_000_000)
    assert set(tuned(cpu, n)) <= {14652000, 16255000}, 'left group 1'
    cpu.uart_in.append(0)                  # still watching: keeps the 10 s mirror window open
    press(cpu, '2', hold=800_000)
    cpu.run(3_000_000)
    assert ' SCAN G2' in status_bar(cpu), status_bar(cpu)


def test_scan_resume_stop(cpu):            # PAUSE BUSY (the default) and RESUME HOLD: stops on the first station
    menu(cpu, 'RESUME', ['UP', 'EXIT'])    # 5 s -> HOLD
    cpu.bk.busy = lambda f: f == 46256250       # channel 2 busy
    keys(cpu, ['EXIT', '*'])
    cpu.run(4_000_000)
    assert freq(cpu) == 46256250
    cpu.run(2_000_000)
    assert freq(cpu) == 46256250, 'scan did not stop'
    assert leds(cpu) == (1, 0, 0, 0), 'green not lit on a scan stop'


def test_scan_waits_for_tone(cpu):         # a carrier on a tone channel: the tone decodes ~250 ms in, the scan waits for it
    def tone2(ee):
        ee[0x20 + 8:0x20 + 10] = bcd(1413, 2)   # channel 2 (GMRS1) listens for 141.3
    cpu = mem_ch1(tone2)
    menu(cpu, 'RESUME', ['UP', 'EXIT'])    # HOLD: stops on the first station it opens for
    cpu.bk.busy = lambda f: f == 46256250
    since = {}
    def match(f):                          # true once the radio has listened there ~250 ms (50 ticks)
        if f not in since: since.clear(); since[f] = cpu.icount
        return f == 46256250 and cpu.icount - since[f] > 50 * 4000
    cpu.bk.tonematch = match
    press(cpu, '*')
    cpu.run(6_000_000)
    assert freq(cpu) == 46256250 and cpu.sfr[0xC0] >> 3 & 1, 'scan moved on before the tone decoded'


def test_scan_skip_kept(cpu):              # long V/M while scanning takes a channel off the scan list for good (Kenwood's
    cpu.bk.busy = lambda f: f == 46256250   # lockout): passed in this scan, the next, a group scan, after power-off
    keys(cpu, ['EXIT', '*'])               # channel 2: a nuisance that never stops
    assert cpu.run(3_000_000, until=lambda: freq(cpu) == 46256250), 'never reached the busy channel'
    menu_off = cpu.ee.mem[0x1920]
    press(cpu, 'EXIT', hold=3_000_000)     # long V/M: skip it (well past 0.8 s while the scan keeps the CPU busy)
    assert menu_off & 2 and not cpu.ee.mem[0x1920] & 2, 'channel 2 still on the scan list'
    def passes(cpu):
        keys(cpu, ['*'])
        seen = [freq(cpu) for _ in range(40) if cpu.run(100_000) or True]
        keys(cpu, ['EXIT'])
        cpu.run(500_000)
        return 46256250 not in seen and len(set(seen)) > 1, seen
    keys(cpu, ['EXIT'])                    # stop the scan
    cpu.run(500_000)
    ok, seen = passes(cpu)
    assert ok, 'the scan stopped on the skipped channel: %s' % seen
    cpu = boot(ee=bytearray(cpu.ee.mem))   # switched off and on
    cpu.bk.busy = lambda f: f == 46256250
    keys(cpu, ['EXIT'])
    ok, seen = passes(cpu)
    assert ok, 'skipped channel back after power-off: %s' % seen
    menu(cpu, 'GROUP', ['UP', 'UP', 'EXIT'])   # GROUP 2: channel 2 only, now off the list
    menu(cpu, 'REMOTE', ['UP', 'EXIT'])
    remote_texts(cpu)                      # sync the mirror
    keys(cpu, ['*'])
    t = remote_texts(cpu)
    assert 'GROUP IS EMPTY' in t and 46256250 != freq(cpu), (t, freq(cpu))
    menu(cpu, 'GROUP', ['DOWN', 'DOWN', 'EXIT'])   # SCAN LIST again
    keys(cpu, ['0', '0', '2'])             # typed (or UP/DOWN), it is still there: channel 2
    assert freq(cpu) == 46256250, freq(cpu)
    longpress(cpu, 'EXIT')                 # back on the scan list
    assert cpu.ee.mem[0x1920] & 2, 'not back on the scan list'


def test_scan_resume_hold(cpu):            # RESUME HOLD: the pause time on a station even after it drops, then on
    menu(cpu, 'PAUSE', ['UP', 'EXIT'])     # BUSY -> 2 s
    menu(cpu, 'RESUME', ['UP', 'EXIT'])    # HOLD
    on = [True]
    cpu.bk.busy = lambda f: f == 46256250 and on[0]   # channel 2: a short call
    keys(cpu, ['EXIT', '*'])
    assert cpu.run(3_000_000, until=lambda: freq(cpu) == 46256250), 'never reached the busy channel'
    cpu.run(300_000)
    on[0] = False                          # the call ends
    cpu.run(600_000)                       # ~0.7 s later: still holding
    assert freq(cpu) == 46256250, 'left as soon as the signal dropped'
    n = len(cpu.bk.writes)
    cpu.run(3_000_000)
    assert set(tuned(cpu, n)) - {46256250}, 'never resumed after the pause time'


def test_scan_pause(cpu):                  # a station that never stops (weather) is left after the pause timer
    menu(cpu, 'PAUSE', ['UP', 'EXIT'])     # BUSY -> 2 s
    cpu.bk.busy = lambda f: f == 46256250       # channel 2 busy forever
    keys(cpu, ['EXIT', '*'])
    stopped = cpu.run(3_000_000, until=lambda: freq(cpu) == 46256250)
    assert stopped, 'never reached the busy channel'
    cpu.run(400_000)                       # ~0.5 s: still there
    assert freq(cpu) == 46256250, 'did not stop on the busy channel'
    n = len(cpu.bk.writes)
    cpu.run(3_000_000)                     # ~4 s here: past the 2 s pause
    assert set(tuned(cpu, n)) - {46256250}, 'never left the busy channel'


def scan_to_busy(cpu, on):               # scan the memories until it stops on channel 2 while on[0]
    cpu.bk.busy = lambda f: f == 46256250 and on[0]
    keys(cpu, ['EXIT', '*'])
    assert cpu.run(3_000_000, until=lambda: freq(cpu) == 46256250), 'never reached the busy channel'
    cpu.run(300_000)


def test_scan_stops_for_save_and_tools(cpu):   # hold MENU on a scan stop saves that channel, though the call ends
    on = [True]
    scan_to_busy(cpu, on)
    longpress(cpu, 'MENU')
    on[0] = False
    cpu.run(6_000_000)
    press(cpu, 'MENU')
    assert bytes(cpu.ee.mem[0x50:0x54]) == bcd(46256250, 4), bytes(cpu.ee.mem[0x50:0x54]).hex()


def test_scan_stops_for_freq_find(cpu):    # a freq-find pick on a scan stop stays tuned (the scan went on under it)
    keys(cpu, ['*'])                       # a VFO scan on 2 m
    cpu.run(1_000_000)
    cpu.bk.counter = lambda: 44700000
    longpress(cpu, '7')
    cpu.run(1_500_000)
    press(cpu, 'MENU')
    cpu.bk.counter = None
    cpu.run(3_000_000)
    assert freq(cpu) == 44700000, freq(cpu)


# Stopping a scan: where it goes. A channel scan on the memories from channel 1 (146.520), and a VFO scan
# on 2 m from 146.520; each stops on its hits in turn (a station on each, moved on from with UP).
SCAN_FROM = 14652000
SCAN_HITS = {True: (46256250, 16255000, 44610000), False: (14660000, 14700000, 14750000)}   # 3rd: paused on it


def scan_hits(cpu, mem, n, pause=False):   # scan, stopping on the first n hits; pause: then stay on the third
    hits = SCAN_HITS[mem]
    left = set(hits[:n] + (hits[2:] if pause else ()))
    cpu.bk.busy = lambda f: f in left
    cpu.bk.tonematch = lambda f: True      # channels 2 and 4 have tones: theirs
    keys(cpu, ['EXIT', '*'] if mem else ['*'])
    heard = static_at(FW, 'lh_n')          # LAST HEARD's rows: one more as the squelch opens on each hit
    for i, f in enumerate(hits[:n] + (hits[2:] if pause else ())):
        assert cpu.run(20_000_000, until=lambda: freq(cpu) == f and cpu.xram[heard] > i), 'never stopped on %d' % f
        cpu.run(200_000)
        if i == n: return hits             # paused on the third
        left.discard(f)
        press(cpu, 'UP')                   # on at once
    f = lambda: freq(cpu) % 250 == 0 and freq(cpu) not in hits[:n] + (SCAN_FROM,)   # (not a register half-written)
    assert cpu.run(5_000_000, until=f), 'the scan stopped'
    return hits


def stopped_on(cpu, f, what):              # tuned to f, and the scan is over (still there 2 s on)
    cpu.run(2_000_000, until=lambda: freq(cpu) == f)   # a key in a running scan is seen late here (emulator timing)
    assert freq(cpu) == f, '%s: on %d, not %d' % (what, freq(cpu), f)
    for _ in range(8):
        cpu.run(200_000)
        assert freq(cpu) == f, '%s: the scan went on' % what


def stop_scan(cpu, key='*'):               # a short press that stops a running scan, and time for it to be seen
    press(cpu, key, after=2_500_000)


def test_scan_stop_goes_back_or_to_hit(cpu, mem):   # * with no hit: back to the start; one hit: to it; the hold keys the same
    k = 'memory' if mem else 'VFO'
    c = cpu
    scan_hits(c, mem, 0)
    press(c, '*')
    stopped_on(c, SCAN_FROM, k + ' scan, no hit')
    c = boot()
    hits = scan_hits(c, mem, 1)
    press(c, '*')
    stopped_on(c, hits[0], k + ' scan, one hit')
    c = boot()
    hits = scan_hits(c, mem, 1)
    press(c, 'MENU', hold=3_000_000)       # held (longer while the scan keeps the CPU busy): save what it stopped on
    press(c, 'EXIT')                       # no, thanks
    stopped_on(c, hits[0], k + ' scan, one hit, hold MENU')
    c = boot()
    scan_hits(c, mem, 0)
    press(c, '7', hold=3_000_000)          # held: freq find, from the start
    c.run(500_000)
    press(c, 'EXIT')
    stopped_on(c, SCAN_FROM, k + ' scan, no hit, hold 7')


def test_scan_stop_picks_from_hits(cpu, mem):   # * with two hits: LAST HEARD, the hits on top, newest lit; MENU, DOWN, V/M
    import lcdshot
    k = 'memory' if mem else 'VFO'
    c = cpu
    menu(c, 'REMOTE', ['UP', 'EXIT'])
    hits = scan_hits(c, mem, 2)
    stop_scan(c)
    c.uart_in.extend(b'S')                 # ask for a fresh screen, as tdh3 does
    c.run(1_000_000)
    rows = sorted((r[2], r[3]) for r in remote_records(c.uart_out) if r[0] == 't' and 14 <= r[2] < 110)
    want = ('M3 ', 'M2 ') if mem else ('147.000', '146.600')
    assert len(rows) >= 2 and all(w in r[1] for w, r in zip(want, rows)), (k, rows)       # the hits on top, newest first
    fb = lcdshot.frame(c.lcd)
    assert fb[13][64] == rgb(255, 176, 0) and fb[25][64] == 0, k + ': the newest hit not lit'
    press(c, 'MENU')
    stopped_on(c, hits[1], k + ': MENU on the newest')
    c = boot()
    hits = scan_hits(c, mem, 2)
    stop_scan(c)
    keys(c, ['DOWN', 'MENU'])
    stopped_on(c, hits[0], k + ': DOWN, MENU')
    c = boot()
    scan_hits(c, mem, 2)
    stop_scan(c)
    press(c, 'EXIT')
    stopped_on(c, SCAN_FROM, k + ': V/M in the list')


def test_scan_stop_paused_and_vm(cpu, mem):   # * while paused on a station: stays; V/M: always back to the start
    k = 'memory' if mem else 'VFO'
    c = cpu
    menu(c, 'REMOTE', ['UP', 'EXIT'])
    hits = scan_hits(c, mem, 2, pause=True)
    remote_texts(c)                        # sync the mirror
    press(c, '*')
    stopped_on(c, hits[2], k + ': * paused on a station')
    assert not any('LAST HEARD' in t for t in remote_texts(c)), k + ': the list opened'
    c = boot()
    scan_hits(c, mem, 1, pause=True)
    press(c, 'EXIT')
    stopped_on(c, SCAN_FROM, k + ': V/M paused on a station')
    c = boot()
    scan_hits(c, mem, 2)
    press(c, 'EXIT')
    stopped_on(c, SCAN_FROM, k + ': V/M with two hits')


def static_at(path, name, src='main'):    # a static's XDATA address, from the module's listing (fw.map has globals only)
    for line in open(os.path.join(os.path.dirname(path), os.path.basename(path)[:-4], src + '.rst')):
        f = line.split()
        if len(f) == 3 and f[2] in ('_%s:' % name, '_%s::' % name):   # a static, or a global (fw/asm reads it)
            return int(f[0], 16)
    raise KeyError(name)


def test_scan_quiet_past_tick_wrap(cpu):   # 327 s with nothing heard: the 16-bit tick count wrapped under the last
    keys(cpu, ['EXIT', '*'])               # signal's stamp, and the scan sat out a 5 s resume wait on an empty channel
    at, t = symbol(FW, '_ticks'), static_at(FW, 't_open')
    o = (cpu.xram[at] | cpu.xram[at + 1] << 8) - 64700   # heard last 323.5 s ago
    cpu.xram[t], cpu.xram[t + 1] = o & 0xFF, o >> 8 & 0xFF
    last, still, worst = freq(cpu), 0, 0
    for _ in range(100):                   # 12 s: across the wrap
        cpu.run(100_000)
        still = still + 1 if freq(cpu) == last else 0
        last, worst = freq(cpu), max(worst, still)
    assert worst < 12, 'the scan stood %.1f s on one channel' % (worst / 8)


def test_scan_saves_settings_once(cpu):    # SCAN PAUSE 2 s on stations that never stop: no settings write at every
    menu(cpu, 'PAUSE', ['UP', 'EXIT'])     # stop (a scan step saved the slot, 2 s later), one when the scan ends
    cpu.run(3_000_000)
    cpu.bk.busy = lambda f: f in (46256250, 44610000)
    keys(cpu, ['EXIT', '*'])
    cpu.ee.writes.clear()
    cpu.run(24_000_000)                    # ~30 s
    assert not [a for a in cpu.ee.writes if 0x1FC0 <= a < 0x1FF0], 'settings written while scanning'
    press(cpu, '*')
    cpu.run(3_000_000)
    assert len([a for a in cpu.ee.writes if 0x1FC0 <= a < 0x1FF0]) == 48, 'no save once the scan ended'


def test_scan_long_call_kept(cpu):         # the defaults: a call that goes on and on is never left
    on = [True]
    scan_to_busy(cpu, on)
    n = len(cpu.bk.writes)
    cpu.run(12_000_000)                    # ~15 s, past the old 10 s pause
    assert set(tuned(cpu, n)) <= {46256250}, 'left a station still talking'


def test_scan_hang_time(cpu):              # a reply within RESUME (5 s) keeps it there; then 5 s of quiet moves on
    on = [True]
    scan_to_busy(cpu, on)
    n = len(cpu.bk.writes)
    on[0] = False
    cpu.run(800_000)                       # ~1 s gap, then the other side answers
    on[0] = True
    cpu.run(800_000)
    on[0] = False
    cpu.run(2_400_000)                     # ~3 s of quiet: still waiting
    assert set(tuned(cpu, n)) <= {46256250}, 'left within the hang time'
    cpu.run(3_000_000)                     # past 5 s
    assert set(tuned(cpu, n)) - {46256250}, 'never moved on after the hang time'


def test_scan_up_skips_stop(cpu):          # UP on a stop: next channel at once, and the scan goes on
    on = [True]
    scan_to_busy(cpu, on)
    press(cpu, 'UP')
    assert freq(cpu) != 46256250, 'UP did not leave the stop'
    n = len(cpu.bk.writes)
    cpu.run(2_000_000)
    assert len(set(tuned(cpu, n))) > 1, 'the scan stopped after UP'


def test_scan_direction(cpu):              # DOWN while scanning: it scans downward from then on (Icom, Yaesu); UP back up
    def steps(n):                          # channel changes in order, repeats squeezed out
        out = []
        for f in tuned(cpu, n):
            if not out or out[-1] != f: out.append(f)
        return set(zip(out, out[1:]))
    keys(cpu, ['EXIT', '*'])
    n = len(cpu.bk.writes)
    for _ in range(16):                    # a whole lap upward, so every step DOWN can take is known
        cpu.run(500_000); up = steps(n)
        if up and len(up) == len({f for s in up for f in s}): break
    assert len(up) == len({f for s in up for f in s}), 'no full lap up: %s' % up
    press(cpu, 'DOWN')
    n = len(cpu.bk.writes); cpu.run(2_000_000); down = steps(n)
    assert len(down) >= 2 and down <= {(b, a) for a, b in up}, (up, down)
    press(cpu, 'UP')
    n = len(cpu.bk.writes); cpu.run(2_000_000)
    assert len(steps(n)) >= 2 and steps(n) <= up, 'UP did not turn it back'


def test_scan_shows_each_channel(cpu):     # each channel's own power, shift and tones as the scan lands on it
    def high_plus5(ee):                    # channel 2: HIGH, +5 MHz, T141.3 (a GMRS repeater)
        ee[0x20 + 4:0x20 + 8] = bcd(46756250, 4)
        ee[0x20 + 14] = 0x10               # High as stock and CHIRP write it
    cpu = mem_ch1(high_plus5)
    menu(cpu, 'REMOTE', ['UP', 'EXIT'])
    remote_texts(cpu)
    keys(cpu, ['*'])
    for _ in range(4):
        cpu.uart_in.append(0)              # still watching: keeps the mirror on
        cpu.run(800_000)
    t = [r for r in remote_records(cpu.uart_out) if r[0] == 't' and r[2] in (23, 41, 50)]
    got = {(a[3], tuple(b[3].split()[1:3]), c[3].split()[0]) for a, b, c in zip(t, t[1:], t[2:]) if (a[2], b[2], c[2]) == (23, 41, 50)}
    assert ('462.562', ('HIGH', '+5'), 'T141.3') in got and ('146.520', ('LOW', 'WIDE'), '25K') in got, sorted(got)


def test_priority(cpu):
    menu(cpu, 'PRIO', ['UP', 'UP', 'UP', 'EXIT'])   # channel 3, 162.550
    cpu.bk.busy = lambda f: f == 16255000
    cpu.run(5_000_000)
    assert freq(cpu) == 16255000 and cpu.sfr[0xC0] >> 3 & 1, 'priority channel not taken'


def test_priority_while_receiving(cpu):    # FT-5D: a look at the priority channel every 5 s, even mid-call
    menu(cpu, 'PRIO', ['UP', 'UP', 'UP', 'EXIT'])   # channel 3, 162.550
    cpu.bk.busy = lambda f: f == 14652000  # a call on A
    cpu.run(1_000_000)
    assert cpu.sfr[0xC0] >> 3 & 1, 'call not heard'
    n = len(cpu.bk.writes)
    cpu.run(10_000_000)                    # ~12 s
    looks = tuned(cpu, n).count(16255000)
    assert 2 <= looks <= 3, 'priority looked at %d times in 12 s of a call' % looks
    assert freq(cpu) == 14652000 and cpu.bk.regs[0x47] != 0x6040, 'the call lost its audio after a look'
    cpu.bk.busy = lambda f: f in (14652000, 16255000)   # now the priority channel is busy too
    cpu.run(5_000_000)
    assert freq(cpu) == 16255000 and cpu.bk.regs[0x47] != 0x6040, 'busy priority channel not taken mid-call'


def test_scan_refused_squelch_open(cpu):   # squelch 0 or monitor: the scan would stop on every channel
    menu(cpu, 'REMOTE', ['UP', 'EXIT'])
    menu(cpu, 'SQUELCH', ['DOWN'] * 3 + ['EXIT'])   # 3 -> 0
    press(cpu, '*')
    t = ' '.join(remote_texts(cpu))
    assert 'SQUELCH IS OPEN' in t and 'SCAN' not in t.replace('SCAN LIST', ''), t
    menu(cpu, 'SQUELCH', ['UP'] * 3 + ['EXIT'])
    longpress(cpu, '1')                    # monitor on
    press(cpu, '*')
    t = ' '.join(remote_texts(cpu))
    assert 'SQUELCH IS OPEN' in t and 'MON' in t, t
    longpress(cpu, '1')                    # monitor off: now it scans
    press(cpu, '*')
    t = remote_texts(cpu)
    assert any(' SCAN' in x for x in t) and 'SQUELCH IS OPEN' not in t, t


def test_vfo_scan_outside_bands(cpu):      # Kenwood's MHz scan: 1 MHz either side, or between A and B
    menu(cpu, 'STEP', ['UP', 'UP', 'EXIT'])   # 100 kHz
    keys(cpu, '140000')                    # A: 140.000, between AIR and 2M; B 446.000 is over 300 MHz
    press(cpu, '*')
    n = len(cpu.bk.writes)
    cpu.run(14_000_000)
    seen = set(tuned(cpu, n))
    assert min(seen) == 13900000 and max(seen) == 14100000, (min(seen), max(seen))
    press(cpu, 'EXIT')                     # stop
    keys(cpu, ['#'] + list('140500') + ['#'] + list('140000'))   # B: 140.500, A: 140.000
    press(cpu, '*')
    n = len(cpu.bk.writes)
    cpu.run(6_000_000)
    seen = set(tuned(cpu, n)) - {14050000}  # B shows on screen; A scans from A to B
    assert seen == {14000000, 14010000, 14020000, 14030000, 14040000}, sorted(seen)


def test_wx_alert(cpu):
    menu(cpu, 'WX', ['UP', 'EXIT'])        # 162.400
    cpu.bk.alert = lambda f: f == 16240000
    cpu.run(9_000_000)
    assert freq(cpu) == 16240000 and cpu.sfr[0xC0] >> 3 & 1, 'no weather alert'
    ok, on = amber_blinks(cpu)
    assert ok, 'weather alert: no amber blink (%d of 520 lit)' % on
    cpu.bk.alert = None
    press(cpu, 'EXIT')                     # any key stops it
    assert freq(cpu) == 14652000
    import lcdshot
    cpu.run(2_000_000)
    fb, red = lcdshot.frame(cpu.lcd), rgb(240, 40, 40)
    assert not any(red in row for row in fb[12:]), 'the alert screen left red on the home screen'   # (the battery's red, above)


def test_wx_alert_in_weather_mode(cpu):     # weather mode on the WX ALERT channel: the alarm and amber blink as anywhere,
    menu(cpu, 'WX', ['UP', 'EXIT'])        # once per alert tone (it ran silent there: no looks in weather mode)
    cpu.bk.dbm = lambda f: -80 if f == 16240000 else -120   # WX1 strongest: weather mode on 162.400
    longpress(cpu, '#'); cpu.run(2_000_000)
    assert freq(cpu) == 16240000 and cpu.bk.regs[0x51] == 0x944A, (freq(cpu), hex(cpu.bk.regs.get(0x51, 0)))
    cpu.run(3_000_000)
    ok, on = amber_blinks(cpu)
    assert not ok, 'alarm with no alert'
    cpu.bk.alert = lambda f: f == 16240000
    cpu.run(3_000_000)
    ok, on = amber_blinks(cpu)
    assert ok, 'weather mode: no alarm (%d of 520 lit)' % on
    press(cpu, 'EXIT')                     # any key stops it; the tone goes on, and so does weather mode
    cpu.run(3_000_000)
    ok, on = amber_blinks(cpu)
    assert not ok and freq(cpu) == 16240000, 'the same alert tone rang again (%d lit), or left weather mode' % on
    cpu.bk.alert = None
    cpu.run(2_000_000)
    cpu.bk.alert = lambda f: f == 16240000   # a new alert
    cpu.run(3_000_000)
    ok, on = amber_blinks(cpu)
    assert ok, 'a second alert: no alarm (%d lit)' % on


def test_wx_alert_squelch_open(cpu):        # monitor on, then squelch 0: the weather look still runs
    menu(cpu, 'WX', ['UP', 'EXIT'])        # 162.400
    longpress(cpu, '1')                    # monitor
    cpu.bk.alert = lambda f: f == 16240000
    alerting = lambda: freq(cpu) == 16240000 and cpu.sfr[0xC0] >> 3 & 1   # the amp drops for a moment now and then:
    assert cpu.run(9_000_000, until=alerting), 'no weather alert with monitor on'   # wait for it, don't sample one instant
    cpu.bk.alert = None
    press(cpu, 'EXIT')
    longpress(cpu, '1')                    # monitor off
    menu(cpu, 'SQUELCH', ['DOWN'] * 3 + ['EXIT'])   # 3 -> 0
    cpu.bk.alert = lambda f: f == 16240000
    assert cpu.run(9_000_000, until=alerting), 'no weather alert at squelch 0'
    cpu.bk.alert = None
    press(cpu, 'EXIT')
    assert freq(cpu) == 14652000 and cpu.bk.regs[0x47] == 0x6140, 'squelch 0 audio not back after the alert'


def test_wx_alert_from_fm(cpu):            # FM radio playing: the 5 s look runs there too and leaves FM
    menu(cpu, 'WX', ['UP', 'EXIT'])        # 162.400
    menu(cpu, 'FM', ['UP'])
    cpu.run(4_000_000)
    n = len(cpu.fm.txns)
    cpu.bk.alert = lambda f: f == 16240000
    cpu.run(9_000_000)
    assert Writes(cpu, 0)[-1] == [0x20, 0x00, 0x00] and len(cpu.fm.txns) > n, 'FM not left for the alert'
    assert freq(cpu) == 16240000 and cpu.sfr[0xC0] >> 3 & 1, 'no weather alert from FM'
    cpu.bk.alert = None
    press(cpu, 'EXIT')
    assert freq(cpu) == 14652000
def test_weather_mode(cpu):                # long #: WX1-WX7, the strongest on the way in, the slot back as it was
    menu(cpu, 'REMOTE', ['UP', 'EXIT'])
    level = {16247500: -80}                # WX4 the strongest, the rest band noise
    cpu.bk.dbm = lambda f: level.get(f, -120)
    longpress(cpu, '#')
    cpu.run(2_000_000)                     # it measures all seven
    assert freq(cpu) == 16247500, freq(cpu)
    texts = remote_texts(cpu)
    assert 'WX4' in texts and 'WX' in texts, texts   # the channel, and WX where VFO/M20 goes
    assert not status_bar(cpu).startswith('WX'), 'WX ALERT switched itself on'
    keys(cpu, ['UP'])
    assert freq(cpu) == 16250000, freq(cpu)
    keys(cpu, ['DOWN', 'DOWN'])            # WX3
    assert freq(cpu) == 16245000, freq(cpu)
    longpress(cpu, '#')
    assert freq(cpu) == 14652000, 'slot A not back on 146.520'
    menu(cpu, 'WX', ['UP', 'EXIT'])        # WX ALERT on 162.400
    level = {16245000: -95, 16255000: -80}   # WX3 still comes in, WX7 better: it takes WX7, every time
    longpress(cpu, '#')
    cpu.run(2_000_000)
    assert freq(cpu) == 16255000, freq(cpu)
    menu(cpu, 'WX')
    assert any('WX ALERT' in t and '162.550' in t for t in remote_texts(cpu)), 'WX ALERT not on the channel heard'
    keys(cpu, num('WEATHER'))              # WEATHER from the menu: leaves
    cpu.run(1_000_000)                     # a mirrored menu redraw outlasts a quick release
    keys(cpu, ['MENU'])
    assert cpu.run(3_000_000, until=lambda: freq(cpu) == 14652000), freq(cpu)   # WX ALERT looks at 162.450 every 5 s
    level = {16245000: -80}                # now WX3 the strongest: the menu way in takes it
    menu(cpu, 'WEATHER', ['UP'])
    cpu.run(2_000_000)
    assert freq(cpu) == 16245000, freq(cpu)
    # measuring the seven (~0.2 s on the radio) is ~20x longer here: wait until it reads keys again
    cpu.run(4_000_000, until=lambda: not cpu.xram[cpu.key_held_at])
    keys(cpu, ['1'])                       # typing a frequency leaves weather mode first
    assert freq(cpu) == 14652000 and 'FREQ 1' in remote_texts(cpu), freq(cpu)


def test_battery_saver(cpu):
    menu(cpu, 'SAVE', ['UP', 'EXIT'])
    n = len(cpu.bk.writes)
    cpu.run(7_000_000)
    w = cpu.bk.writes[n:]
    assert (0x37, 0x1D00) in w and w.count((0x30, 0xBFF1)) >= 2, 'receiver never slept and woke'
    asleep = [pins(cpu)['VRX'] for _ in range(400) if cpu.run(5_000) or cpu.bk.regs.get(0x37) == 0x1D00]
    assert asleep and not all(asleep), 'VHF RX path left on while the BK4819 sleeps'


def test_meter_waits_for_retune(cpu):     # the chip reads its floor ~10 ms after a retune: no dips at every wake
    menu(cpu, 'SAVE', ['UP'] * 3 + ['EXIT'])   # LONG: wakes every half second
    cpu.bk.dbm = lambda f: -98             # noise under the squelch, as on a quiet repeater
    cpu.bk.regs[0x65] = 0x7F               # and noisy (REG_65), not a carrier: the saver sleeps
    cpu.bk.settle, cpu.bk.clock = 60_000, lambda: cpu.icount   # past the first read after a wake
    a = symbol(FW, '_dbm')
    cpu.run(2_000_000)
    seen = set()
    for _ in range(600):                   # ~3 s
        cpu.run(4_000)
        v = cpu.xram[a] | cpu.xram[a + 1] << 8
        seen.add(v - 0x10000 if v & 0x8000 else v)
    cpu.bk.dbm, cpu.bk.settle = None, 0
    cpu.bk.regs.pop(0x65, None)
    assert seen == {-98}, 'meter read an unsettled chip: %s' % sorted(seen)


def test_meter_calm(cpu):                  # a fluttering signal: a new reading 3 times a second, a new channel's at once
    import emu51
    tick, ticks, shown = emu51.TICK, symbol(FW, '_ticks'), symbol(FW, '_m_dbm')
    t_meter = symbol(FW, '_t_meter')
    def get(a):
        v = cpu.xram[a] | cpu.xram[a + 1] << 8
        return v - 0x10000 if v & 0x8000 else v
    emu51.TICK = 60000                     # ~12 MIPS: the main loop at the radio's pace, so ticks read true
    try:
        cpu.bk.busy = lambda f: True
        cpu.bk.dbm = lambda f: -80 - cpu.icount // 30000 % 9   # a new reading every half tick
        cpu.run(2_000_000)
        last, at, gaps = get(shown), None, []
        for _ in range(4000):              # ~330 ticks
            cpu.run(5_000)
            if get(shown) != last:
                last, t = get(shown), get(ticks)
                if at is not None: gaps.append((t - at) & 0xFFFF)
                at = t
        assert len(gaps) >= 3 and min(gaps) >= 66, 'meter redrawn sooner than every 330 ms: %s' % gaps
        cpu.bk.busy = None                 # squelch shut on both: no opening to redraw the meter for us
        cpu.bk.dbm = lambda f: -60 if f == 44600000 else -100   # B (446.000) stronger, A weak
        cpu.bk.settle, cpu.bk.clock = 300_000, lambda: cpu.icount   # ~25 ms of floor after a retune or a key's beep
        cpu.run(6_000_000)
        assert get(shown) == -100, get(shown)
        cpu.run(1_000_000, until=lambda: (get(ticks) - get(t_meter)) & 0xFFFF == 1)   # just drawn: the next is 330 ms off
        t, seen = get(ticks), set()
        cpu.key = '#'                      # A/B: B's reading must not wait out the 330 ms
        while (get(ticks) - t) & 0xFFFF < 66 and get(shown) != -60:
            if cpu.xram[cpu.key_held_at]: cpu.key = None
            cpu.run(2_000)
            seen.add(get(shown))
        d = (get(ticks) - t) & 0xFFFF
        assert get(shown) == -60 and d < 56, 'a new channel\'s reading waited %d ticks' % d
        assert seen <= {-100, -60}, 'the meter showed an unsettled reading: %s' % sorted(seen)
    finally:
        emu51.TICK = tick
        cpu.key = cpu.bk.busy = cpu.bk.dbm = None
        cpu.bk.settle = 0
    cpu.run(2_000_000)
    press(cpu, '#')                        # back to A


def test_battery_saver_wakes_on_ptt(cpu):   # PTT refused while asleep: the receiver wakes (it slept 5 s more, deaf)
    menu(cpu, 'SAVE', ['UP', 'EXIT'])
    keys(cpu, '162550')                    # no TX here
    cpu.run(6_000_000)
    assert cpu.run(4_000_000, until=lambda: cpu.bk.regs.get(0x37) == 0x1D00), 'never slept'
    cpu.key = 'PTT'; cpu.run(150_000); cpu.key = None
    cpu.run(800_000)
    assert cpu.bk.regs.get(0x37) != 0x1D00, 'still asleep 1 s after PTT'


def test_battlog_reads_status(cpu):        # tools/battlog.py parses the real status line, mirror bytes and all
    import battlog
    menu(cpu, 'REMOTE', ['UP', 'EXIT'])
    cpu.uart_out.clear()
    cpu.uart_in.extend(b'Q')
    cpu.run(1_500_000)
    f = battlog.parse(bytes(cpu.uart_out))
    assert f and f[0] == '146.520' and f[2] == 'C' and f[3] == '-' and 6000 < int(f[4]) < 9000, f


def test_mcu_idles(cpu):                   # the CPU waits for the tick in IDLE, not spinning
    n = cpu.idles
    cpu.run(400_000)                       # 100 ticks
    assert cpu.idles - n >= 20, 'main loop hardly idled: %d' % (cpu.idles - n)   # here a pass takes ticks; on the radio ~0.2 ms


def torch(cpu):                            # flashlight brightness: PWM5's duty (out of 200)
    return cpu.xram[0xF14A]


def test_torch_modes(cpu):                 # Olight-style: tap, hold from off moonlight, hold on steps, 2/3 taps, SOS
    assert cpu.xram[0xF035] == 5, 'P3.5 not on PWM code 5 (PG5 on the radio; 0x14 left it dark)'
    menu(cpu, 'REMOTE', ['UP', 'EXIT'])    # remote_texts reads the status bar
    def bar():
        return [t for t in remote_texts(cpu) if t.startswith(('DW', 'SQ', 'MOON', 'LOW', 'MED', 'HIGH', 'STROBE', 'SOS'))]
    press(cpu, 'SIDE2')
    cpu.run(300_000)
    assert torch(cpu) == 200, ('tap: on at high', torch(cpu))
    assert any('HIGH' in t for t in bar()), bar()
    press(cpu, 'SIDE2', hold=1_500_000)    # ~1.9 s held while on: HIGH -> MOON (0.5 s) -> LOW (1.5 s)
    assert torch(cpu) == 15, ('hold: low', torch(cpu))
    press(cpu, 'SIDE2')
    cpu.run(300_000)
    assert torch(cpu) == 0, 'tap: off'
    press(cpu, 'SIDE2')
    cpu.run(300_000)
    assert torch(cpu) == 15, 'tap: back on at the last level'
    press(cpu, 'SIDE2')
    cpu.run(300_000)                       # off
    press(cpu, 'SIDE2', hold=700_000)      # hold from off: moonlight
    cpu.run(300_000)
    assert torch(cpu) == 1, ('hold from off: moonlight', torch(cpu))
    assert any('MOON' in t for t in bar()), bar()
    press(cpu, 'SIDE2', after=80_000)      # double tap: high
    press(cpu, 'SIDE2', after=80_000)
    cpu.run(300_000)
    assert torch(cpu) == 200, ('double tap: high', torch(cpu))
    for _ in range(3):                     # triple tap: strobe
        press(cpu, 'SIDE2', after=80_000)
    seen = set()
    for _ in range(30):
        cpu.run(40_000)
        seen.add(torch(cpu))
    assert seen == {0, 200}, ('strobe', seen)
    press(cpu, 'SIDE2')                    # a tap stops it
    cpu.run(300_000)
    assert torch(cpu) == 0, 'tap did not stop strobe'
    press(cpu, 'SIDE2', hold=9_600_000)    # ~12 s held from off: SOS
    seen = set()
    for _ in range(60):
        cpu.run(40_000)
        seen.add(torch(cpu))
    assert seen == {0, 200}, ('SOS', seen)
    press(cpu, 'SIDE2')
    cpu.run(300_000)
    assert torch(cpu) == 0, 'tap did not stop SOS'


def test_ptt_b_keys_slot_b(cpu):
    cpu.key = 'SIDE1'
    cpu.run(1_500_000)
    p = pins(cpu)
    assert p['TXU'] and freq(cpu) == 44600000, (p, freq(cpu))
    cpu.key = None
    cpu.run(1_000_000)
    assert not pins(cpu)['TXU']


def test_ptt_held_at_boot(cpu):            # PTT held for flash mode but the bootloader missed it: no TX till let go
    menu(cpu, 'REMOTE', ['UP', 'EXIT'])    # saved, so the boots below mirror the screen
    cpu.run(3_000_000)
    for key in ('PTT', 'SIDE1'):
        c = boot(ee=bytearray(cpu.ee.mem), key=key)
        assert 'PTT HELD: LET GO' in remote_texts(c), remote_texts(c)
        c.run(4_000_000)                   # 5 s, still held
        assert 'PTT HELD: LET GO' in remote_texts(c), 'the note went while PTT is still held'
        assert not pins(c)['TXU'] and not pins(c)['TXV'] and not any(leds(c)), (key, pins(c), leds(c))
        assert (0x30, 0xC1FE) not in c.bk.writes and not c.xram[c.key_held_at], key
        c.key = None
        c.run(300_000)
        c.key = key                        # let go, press again: transmits as usual
        c.run(1_500_000)
        p = pins(c)
        assert (p['TXU'] and freq(c) == 44600000 if key == 'SIDE1' else p['TXV'] and freq(c) == 14652000), (key, p)
        assert leds(c) == ((0, 0, 0, 1) if key == 'SIDE1' else (0, 0, 1, 0)), leds(c)   # red on the slot that talks
        c.key = None
        c.run(2_000_000)
        assert not pins(c)['TXU'] and not pins(c)['TXV']


def test_clear_tones(cpu):                 # power-on chime and every beep are BK4819 sines; RX comes back
    w = cpu.bk.writes
    assert (0x71, 0x5EC6) in w and (0x72, 0x4087) in w and (0x72, 0x5469) in w, 'no power-on chime'   # ping, whoosh, C7
    n, t1 = len(cpu.bk.writes), cpu.t1_count
    press(cpu, 'UP')
    w = cpu.bk.writes[n:]
    tone = [i for i, (r, v) in enumerate(w) if r == 0x70 and v & 0x8000]
    assert (0x47, 0x6340) in w and tone and cpu.t1_count == t1, 'key click not a sine'
    assert (0x30, 0xBFF1) in w[tone[-1]:], 'receiver not back after the tone'
    assert cpu.bk.regs[0x47] == 0x6040 and cpu.bk.regs[0x70] == 0


def test_fm_chip_off_at_boot(cpu):         # REG02 = 0000 to the RDA5807 before the radio starts
    assert list(Writes(cpu)[0])[1:3] == [0x00, 0x00], Writes(cpu)[0]


def test_remote_voice(cpu):                # REMOTE 'V' n: prompt n clocked to U4 (P4.2 clock, P3.6 data) MSB first
    menu(cpu, 'REMOTE', ['UP', 'EXIT'])
    cpu.uart_out.clear()
    cpu.uart_in.extend(b'V')
    cpu.run(100_000)
    cpu.uart_in.extend(bytes([0xA5]))
    cpu.run(1_500_000)
    assert b'qVOICE 165\n' in bytes(cpu.uart_out), bytes(cpu.uart_out)[-40:]


def test_ultra_low_power(cpu):
    menu(cpu, 'POWER', ['DOWN', 'EXIT'])    # LOW -> ULTRA LOW
    cpu.key = 'PTT'
    cpu.run(800_000)
    p = pins(cpu)
    cpu.key = None
    cpu.run(1_000_000)
    assert p['TXV'] and p['APC'] == 0 and cpu.bk.regs.get(0x30) == 0xBFF1, p


def test_tone_scan_copy(cpu):
    cpu.bk.busy = lambda f: True
    cpu.bk.ctcss = lambda f: 885
    cpu.run(1_500_000)
    longpress(cpu, '6')
    assert cpu.bk.regs[0x51] == 0x904A and cpu.bk.regs[0x07] == 885 * 2065 // 1000, hex(cpu.bk.regs[0x07])


def test_tone_scan_dcs(cpu):               # D754I heard starting at an arbitrary bit, copied with long 6
    w = golay(0o754) ^ 0x7FFFFF
    w = (w << 7 | w >> 16) & 0x7FFFFF       # the receiver locked on 7 bits in
    cpu.bk.busy = lambda f: True
    cpu.bk.dcs = lambda f: w
    cpu.run(1_500_000)
    longpress(cpu, '6')
    # D116N has the same codeword set as D754I and comes first in the standard list: either is right
    ok = {0x8000 | golay(0o116) >> 12 & 0xFFF, 0x8000 | (golay(0o754) ^ 0x7FFFFF) >> 12 & 0xFFF}
    assert cpu.bk.regs[0x51] == 0x8033 and cpu.bk.regs.get(0x08) in ok, (hex(cpu.bk.regs[0x51]), cpu.bk.regs.get(0x08))


DCS_STD = [int(c, 8) for c in '''023 025 026 031 032 036 043 047 051 053 054 065 071 072 073 074 114 115 116 122
    125 131 132 134 143 145 152 155 156 162 165 172 174 205 212 223 225 226 243 244 245 246 251 252 255 261
    263 265 266 271 274 306 311 315 325 331 332 343 346 351 356 364 365 371 411 412 413 423 431 432 445 446
    452 454 455 462 464 465 466 503 506 516 523 526 532 546 565 606 612 624 627 631 632 654 662 664 703 712
    723 731 732 734 743 754'''.split()]


def test_dcs_table(cpu):                   # the 104 TIA-603 codes, read straight out of the image
    import re
    rst = open(os.path.join(HERE, 'build', 'td-h3', 'main.rst')).read()
    a = int(re.search(r'\n\s+([0-9A-F]{6})\s+\d+ _dcs_lo:', rst).group(1), 16)
    img = open(FW, 'rb').read()
    got = [img[a + i] | (0x100 if i >= 64 else 0) for i in range(len(DCS_STD))]   # dcs(): bit 8 is i >= 64
    assert got == DCS_STD, [oct(x) for x in got[:20]]


def rst_addr(name, path=FW):               # a static's or function's address, from main.rst (fw.map lists only globals)
    rst = open(os.path.join(os.path.dirname(path), os.path.basename(path)[:-4], 'main.rst')).read()
    return int(re.search(r'\n\s+([0-9A-F]{6})\s+\d+ %s:' % name, rst).group(1), 16)


def rst_in_data(name, path=FW):            # the static is in internal RAM (DSEG), not XDATA
    rst = open(os.path.join(os.path.dirname(path), os.path.basename(path)[:-4], 'main.rst')).read()
    return re.findall(r'\.area (\w+)', rst[:re.search(r'\n\s+[0-9A-F]{6}\s+\d+ %s:' % name, rst).start()])[-1] == 'DSEG'


def call(cpu, fn, a=0):                    # fn(a) as C calls it (a in DPL), run till it returns
    ret, sp = 0xF7F0, cpu.sfr[0x81]        # past the image: zeros, never code
    cpu.iram[sp + 1], cpu.iram[sp + 2] = ret & 0xFF, ret >> 8
    cpu.sfr[0x81], cpu.sfr[0x82], cpu.pc = sp + 2, a & 0xFF, fn
    assert cpu.run(5_000_000, until=lambda: cpu.pc == ret), 'call to %04X never returned' % fn


MENU_WALK = os.path.join(HERE, 'menu_walk.txt')


def test_menu_walk(cpu):                   # every setting through its whole range: the text shown and the bytes saved, as v0.54
    # Calls menu_change and menu_value straight from the main loop's key_scan, with draw_menu a RET:
    # ~1,400 steps in seconds. MENU_WALK=write python3 test_fw.py walk writes the list (only from main).
    scan = symbol(FW, '_key_scan')
    assert cpu.run(2_000_000, until=lambda: cpu.pc == scan)
    pc, sp = cpu.pc, cpu.sfr[0x81]
    cpu.code[rst_addr('_draw_menu')] = 0x22
    change, value, menu_at, cfg, buf = (rst_addr(n) for n in ('_menu_change', '_menu_value', '_menu', '_cfg', '_buf'))
    ram = cpu.iram if rst_in_data('_menu') else cpu.xram
    cfgb = lambda: bytes(cpu.xram[cfg:cfg + 48])
    def shown(i):
        call(cpu, value, i)
        b = cpu.xram[buf + 26:buf + 48]
        return b[:b.index(0)].decode()
    out = []
    for i, name in enumerate(MENU):
        if name in ACTIONS or name == 'VER': continue   # VERSION: the build's own
        ram[menu_at] = i
        base = cfgb()
        out.append('%s %s' % (name, shown(i)))
        for d in (1, -1):
            start = prev = cfgb()
            for _ in range(8 if name == 'OFFS' else 600):   # OFFSET has 255 values: a few here, the ends in test_offset_dial
                call(cpu, change, d)
                now = cfgb()
                diff = ' '.join('%02X=%02X' % (k, now[k]) for k in range(48) if now[k] != base[k])
                out.append('%s %+d %s | %s' % (name, d, shown(i), diff))
                if now in (start, prev): break     # round to where it began, or stuck at a limit
                prev = now
        cpu.xram[cfg:cfg + 48] = base
    for g in (0x03, 0x81, 0xFF):           # GROUP LINK's sets, shown by both scan items
        cpu.xram[cfg + 18] = g
        out += ['%s group %02X %s' % (n, g, shown(MENU.index(n))) for n in ('GROUP', 'GLINK')]
    cpu.xram[cfg + 18] = 0
    cpu.xram[cfg + 33], cpu.xram[cfg + 34] = 1, 1   # slot A on channel 1: the VFO's items say so
    out += ['mem %s %s' % (n, shown(i)) for i, n in enumerate(MENU) if n != 'VER']
    cpu.pc, cpu.sfr[0x81] = pc, sp
    if os.environ.get('MENU_WALK') == 'write':
        open(MENU_WALK, 'w').write('\n'.join(out) + '\n')
    want = open(MENU_WALK).read().splitlines()
    bad = next((n for n, (a, b) in enumerate(zip(out, want)) if a != b), None)
    assert bad is None and len(out) == len(want), 'line %s: %r, not %r' % (
        bad, out[bad] if bad is not None else len(out), want[bad] if bad is not None else len(want))


def test_freq_find(cpu):
    cpu.bk.counter = lambda: 14700000
    menu(cpu, 'FF', ['UP'])
    cpu.run(1_500_000)
    press(cpu, 'MENU')
    cpu.bk.counter = None
    assert freq(cpu) == 14700000, freq(cpu)


def test_freq_find_rounds(cpu):            # 147.01240 is nearer 147.0125 than 147.0100
    cpu.bk.counter = lambda: 14701240
    menu(cpu, 'FF', ['UP'])
    cpu.run(1_500_000)
    press(cpu, 'MENU')
    cpu.bk.counter = None
    assert freq(cpu) == 14701250, freq(cpu)


def test_scope(cpu):
    n = len(cpu.bk.writes)
    ch43 = cpu.bk.regs[0x43]
    menu(cpu, 'SCOPE', ['UP'])
    cpu.run(4_000_000)
    assert cpu.bk.regs[0x43] == 0x0058, hex(cpu.bk.regs[0x43])   # narrowest filter while sweeping
    swept = {v for r, v in cpu.bk.writes[n:] if r == 0x38}
    assert len(swept) >= 32, len(swept)
    keys(cpu, ['UP', 'MENU'])              # cursor one step up from the centre, then tune
    assert freq(cpu) == 14654500, freq(cpu)
    assert cpu.bk.regs[0x43] == ch43, (hex(cpu.bk.regs[0x43]), hex(ch43))   # the channel's filter is back


def remote_records(out):                   # REMOTE stream after the last sync -> [(kind, ...)]
    i, recs = out.rfind(b'S\xa5\x5a') + 3, []
    while i < len(out):
        k = out[i]
        if k == ord('f'):
            recs.append(('f',) + tuple(out[i + 1:i + 5])); i += 7
        elif k == ord('t'):
            end = out.find(0, i + 8)
            if end < 0:
                break                          # cut off mid-record when the run stopped
            recs.append(('t', out[i + 1], out[i + 2], out[i + 8:end].decode('latin-1'),   # x, y, text, fg, bg, scale
                         out[i + 4] << 8 | out[i + 5], out[i + 6] << 8 | out[i + 7], out[i + 3])); i = end + 1   # the font's arrows are 0x7F, 0x80
        elif k == ord('q'):
            end = out.find(b'\n', i)
            if end < 0:
                break
            recs.append(('q', out[i + 1:end].decode())); i = end + 1
        elif k == ord('E'):
            recs.append(('E',)); i += 1
        else:
            raise AssertionError('bad record 0x%02X at %d' % (k, i))
    return recs


def remote_key(cpu, code, hold=400_000):
    cpu.uart_in.append(0x80 | code)
    cpu.run(hold)
    cpu.uart_in.append(0x80)
    cpu.run(700_000)


def test_remote(cpu):
    cpu.uart_in.extend(b'S')
    cpu.run(600_000)
    assert not cpu.uart_out, 'REMOTE answered while OFF'
    menu(cpu, 'REMOTE', ['UP', 'EXIT'])
    cpu.uart_out.clear()
    cpu.uart_in.extend(b'S')
    cpu.run(200_000)
    cpu.uart_in.extend(b'Q')
    cpu.run(1_500_000)
    recs = remote_records(cpu.uart_out)
    texts = [r[3] for r in recs if r[0] == 't']
    assert any('146.520' in t for t in texts), texts
    assert any(r[0] == 'q' and re.fullmatch(r'146\.520 -\d+ [OC] [S-] \d{4} (\d+\.\d+\.\d+|dev)\S*', r[1]) for r in recs), recs   # every field, battery and version too (dev: a clone without tags)
    kinds = [r[0] for r in recs]            # 'E' once the redraw is out: after slot A's frequency
    e = kinds.index('E') if 'E' in kinds else -1
    assert e > max(i for i, r in enumerate(recs) if r[0] == 't' and '146.520' in r[3]), recs
    remote_key(cpu, 2)                      # K_1
    texts = [r[3] for r in remote_records(cpu.uart_out) if r[0] == 't']
    assert 'FREQ 1' in texts, texts
    remote_key(cpu, 17)                     # K_PTT: refused
    assert not pins(cpu)['TXV'] and not pins(cpu)['TXU'], 'remote keyed the transmitter'


def test_remote_key_lets_go(cpu):           # a lost release byte: the radio lets go after 300 ms, no long press
    menu(cpu, 'REMOTE', ['UP', 'EXIT'])
    cpu.uart_in.append(0x80 | 11)          # MENU held, and the release never comes
    cpu.run(1_200_000)                     # ~1.5 s: a long press would be at 0.8 s
    assert not any(t.startswith('SAVE CH') for t in remote_texts(cpu)), 'a lost release made a long MENU press'


def test_remote_lost_release_in_fm_auto(cpu):   # UP held from the computer stops FM's auto presets, its release is
    fm_stations(cpu, {883: -70})           # lost: the radio let go of it never (that loop reads key_scan alone)
    menu(cpu, 'REMOTE', ['UP', 'EXIT'])
    menu(cpu, 'FM', ['UP']); cpu.run(4_000_000)
    longpress(cpu, '0'); press(cpu, '0')
    cpu.run(1_000_000)
    cpu.uart_in.append(0x80 | 12)
    cpu.run(3 * 800_000)
    assert not cpu.xram[symbol(FW, '_rkey')], 'the lost UP still held 3 s on'


def tdh3_press(cpu, code):                 # as `tdh3 remote keys`: the hold, again at 100 ms, the release at 150 and 190 ms
    for b, ms in ((0x80 | code, 100), (0x80 | code, 50), (0x80, 40), (0x80, 1500)):
        cpu.uart_in.append(b)
        cpu.run(ms * 800)                  # 800 instructions a ms of firmware time here


def test_remote_digit_in_redraw(cpu):      # tdh3 opens with 'S' (its speed probe): the menu redraws and the first
    menu(cpu, 'REMOTE', ['UP', 'EXIT'])    # digit's hold, landing in it, is read late. It was let go unseen: 15 opened 05
    press(cpu, 'MENU')
    cpu.run(1_000_000)
    cpu.uart_in.extend(b'S')
    cpu.run(400 * 800)                     # the keys 400 ms after the probe, as `tdh3 remote keys 1 5`
    d = num('GROUP')
    tdh3_press(cpu, int(d[0]) + 1); tdh3_press(cpu, int(d[1]) + 1)   # REMOTE key codes: digit + 1
    title = next(t for t in remote_texts(cpu) if t.endswith('/%d' % len(MENU)))
    assert title.startswith('SCAN') and title.endswith(' %s/%d' % (''.join(d), len(MENU))), title


def test_remote_same_key_twice(cpu):        # V/M from one tdh3 call, then the next: each switches. A repeat dropped
    menu(cpu, 'REMOTE', ['UP', 'EXIT'])    # after a lost release outlived its press: the second V/M did nothing
    menu(cpu, 'LIGHT', ['DOWN', 'EXIT'])   # 10 s: the 12 s gap below goes dark, and REMOTE keys still count
    got = []
    for gap in (1, 1, 12):
        cpu.run(gap * 800_000)
        cpu.uart_in.extend(b'S')           # each tdh3 call starts with its probe
        cpu.run(400 * 800)
        tdh3_press(cpu, 14)                # V/M
        got.append(next(t.strip() for t in remote_texts(cpu) if t.strip() in ('VFO', '*M1')))   # slot A's label
    assert got == ['*M1', 'VFO', '*M1'], got


def test_remote_paced_vm(cpu):             # as tools/emuport.py feeds tdh3's bytes: one at a time, 150 ms apart, the
    menu(cpu, 'REMOTE', ['UP', 'EXIT'])    # hold landing in the 'S' redraw. Read late, then a slow mirrored pass:
    cpu.run(1_000_000)                     # let go before key_event saw it, so V/M did nothing
    pending, last, n = [ord('S'), 0x8E, 0x8E, 0x80, 0x80], 0, 0
    while pending or n - last < 3_000_000:
        if pending and not cpu.uart_in and not cpu.sfr[0xEA] & 1 and n - last >= 120_000:
            cpu.uart_in.append(pending.pop(0)); last = n
        cpu.run(4000); n += 4000
    assert next(t.strip() for t in remote_texts(cpu) if t.strip() in ('VFO', '*M1')) == '*M1', 'V/M lost'


def test_remote_bytes_kept_in_redraw(cpu):   # uart1_isr keeps what lands while the loop is busy: 'S', then a
    menu(cpu, 'REMOTE', ['UP', 'EXIT'])    # whole V/M press and a 'Q' at once, all inside the mirrored redraw
    cpu.run(1_000_000)
    cpu.uart_out.clear()
    lost = cpu.uart_lost
    cpu.uart_in.extend(b'S\x8e\x8e\x80\x80Q')
    cpu.run(4_000_000)
    assert cpu.uart_lost == lost, 'bytes lost: %d' % (cpu.uart_lost - lost)
    assert b'S\xa5\x5a' in cpu.uart_out and b'\nq' not in cpu.uart_out[:3] and b'q' in cpu.uart_out, 'no status line'
    assert next(t.strip() for t in remote_texts(cpu) if t.strip() in ('VFO', '*M1')) == '*M1', 'V/M lost'


def test_remote_up_one_step(cpu):          # a redraw slower than 200 ms ate both release bytes; the hold byte
    menu(cpu, 'REMOTE', ['UP', 'EXIT'])    # it left read late held UP past the 0.5 s repeat: THEME moved two
    for item, row, vals in (('THEME', 'THEME', ['CLASSIC', 'AMBER', 'GREEN', 'RED']), ('BRIGHT', 'BRIGHTNESS', '12345')):
        menu(cpu, item)
        press(cpu, 'MENU')                 # change its value
        got = []
        for _ in range(4):
            tdh3_press(cpu, 12)            # K_UP
            got.append(vals.index(next(t for t in remote_texts(cpu) if t.startswith(row)).split()[-1]))
        assert all((b - a) % len(vals) == 1 for a, b in zip(got, got[1:])), (item, got)
        keys(cpu, ['EXIT', 'EXIT']); cpu.run(1_000_000)


DIGITS = ['ARCADE', 'LCD', 'CLASSIC']


def test_digits(cpu, style):               # DIGITS: the look's pixels in slot A, the label's place, REMOTE's text, saved
    import lcdshot
    n, cfg, y = DIGITS.index(style), rst_addr('_cfg'), 14
    assert cpu.xram[cfg + 6] >> 6 == 0, 'a new radio (settings FF) not on ARCADE'
    menu(cpu, 'REMOTE', ['UP', 'EXIT'])
    if n:
        menu(cpu, 'DIGITS', ['UP'] * n + ['EXIT'])
    cpu.run(3_000_000)                     # saved 2 s after the last change
    assert cpu.ee.mem[0x1FC6] >> 6 == n, 'DIGITS not saved: %02X' % cpu.ee.mem[0x1FC6]

    def look():                            # slot A's rows as drawn, and its texts as REMOTE sends them after a sync
        cpu.uart_out.clear()
        cpu.uart_in.extend(b'S')
        cpu.run(3_000_000)
        return [row[:] for row in lcdshot.frame(cpu.lcd)[y - 1:y + 44]], {(r[2], r[1]): r for r in remote_records(cpu.uart_out) if r[0] == 't'}
    fb, t = look()
    px = lambda x, yy: fb[yy - y + 1][x]
    bg = px(127, y + 20)
    fg = cpu.xram[rst_addr('_pal') + 2] | cpu.xram[rst_addr('_pal') + 3] << 8
    big = t.get((y + 9, 32))
    if n == 2:                             # CLASSIC as ever: the font at 2x from x 32, the label at x 0 on its row
        assert big and big[3] == '146.520' and big[6] == 2, big
        assert t.get((y + 13, 0), ('',) * 4)[3] == 'VFO  ', t
        return
    assert big and big[3] == '146.520' and big[6] == 2, ('REMOTE not sent the frequency as CLASSIC draws it', big)
    assert not any(r[0] == 'f' and r[2] >= y + 8 and r[2] <= y + 25 and r[1] < 105 for r in remote_records(cpu.uart_out)), 'digit fills mirrored'
    assert any(r[3] == 'VFO' and ry == y and x + 6 * 3 == 128 for (ry, x), r in t.items()), ('the label not at the name row\'s right end', t)
    assert all(px(x, yy) == bg for x in range(5) for yy in range(y + 8, y + 27)), 'something left of the digits'
    if n == 0:                             # ARCADE: '1' at x 5 is shape columns 3-4 (x 11-14), rows y+9 to y+24, its shadow 1 right and down
        dim = (fg >> 1 & 0x7BEF) - (fg >> 3 & 0x18E3) + (fg >> 5 & 0x0020)
        assert px(11, y + 9) == px(14, y + 24) == fg and px(10, y + 9) == bg, 'ARCADE 1 not at x 11-14'
        assert px(15, y + 10) == px(15, y + 25) == dim and px(7, y + 25) == bg and px(8, y + 25) == dim, 'no shadow at 40%'
        assert px(53, y + 22) == fg and px(56, y + 25) == dim and px(53, y + 21) == bg, 'the point not 3x3 on the baseline'
    else:                                  # LCD: '1' is segments b and c, 3 px tapering to x 16-18, a notch between; two colours, no shadow
        assert px(18, y + 9) == px(17, y + 10) == px(16, y + 11) == px(18, y + 17) == fg, 'LCD 1'
        assert px(17, y + 9) == px(18, y + 16) == px(5, y + 12) == px(18, y + 25) == bg, 'LCD 1: its corners, notch, foot'
        assert {px(x, yy) for x in range(128) for yy in range(y + 8, y + 27)} == {fg, bg}, 'more than two colours'
        assert px(53, y + 23) == px(55, y + 25) == fg and px(56, y + 25) == px(53, y + 22) == bg, 'the point not 3x3 on the bottom rows'
    # A fast redraw (the digits that changed) and a 12.5 kHz small digit come and go cleanly: as a full redraw
    keys(cpu, ['EXIT'])                    # V/M: channel 1, 146.520
    for k, f, small in (('UP', '462.562', '5 '), ('DOWN', '146.520', '  ')):
        keys(cpu, [k])
        cpu.run(1_000_000)
        fast, t = look()
        assert t[(y + 9, 32)][3] == f and t.get((y + 17, 116), ('',) * 4)[3].strip() == small.strip(), (k, t[(y + 9, 32)], t.get((y + 17, 116)))
        keys(cpu, ['MENU', 'EXIT'])        # the whole screen again
        cpu.run(1_000_000)
        assert look()[0] == fast, 'a redraw of %s left pixels a full redraw has not' % f


def test_tinted_label_reads(cpu):          # a heard slot's label mirrors as plain "M1": bright on its tint, not lit
    sys.path.insert(0, os.path.join(HERE, '..', 'tools'))
    import radio
    def luma(c):
        return 299 * ((c & 31) << 3) + 587 * ((c >> 5 & 63) << 2) + 114 * ((c >> 11) << 3)
    menu(cpu, 'REMOTE', ['UP', 'EXIT'])
    keys(cpu, ['EXIT'])                    # V/M: slot A onto a memory channel
    cpu.bk.busy = lambda f: True
    cpu.uart_in.extend(b'S')
    cpu.run(3_000_000)
    t = {(r[2], r[1]): r for r in remote_records(cpu.uart_out) if r[0] == 't'}
    y = radio.SLOT_Y['A']
    lab = next(r for (ry, x), r in t.items() if ry == y and x + 6 * len(r[3]) == 128)   # ARCADE, the default: the name row's right end
    letter = t[(y, 1)]
    assert lab[3].lstrip('*').startswith('M') and luma(lab[4]) > luma(lab[5]), lab   # bright text on the dark tint
    assert lab[5] == rgb(6, 64, 28), 'the label not on the RX tint'
    assert luma(letter[5]) > luma(letter[4]), letter   # the selected letter: inverse, so tdh3 calls it lit
    assert radio.Radio.label({(y + 13, 0): '*M20  (lit)'}, y) == '20'   # an old tdh3's suffix is stripped too
    assert radio.Radio.label({(y, 1): 'A  (lit)', (y, 110): '*M1'}, y) == '1'   # ARCADE and LCD: at the right end
    cpu.bk.busy = None


def test_last_heard(cpu):                  # stations heard, newest first; MENU tunes to the picked one
    menu(cpu, 'REMOTE', ['UP', 'EXIT'])
    for f in ('146520', '446000'):
        keys(cpu, f)
        cpu.bk.busy = lambda x, f=int(f) * 100: x == f
        cpu.run(1_500_000)                 # heard
        cpu.bk.busy = None
        cpu.run(1_000_000)
    menu(cpu, 'LH', ['UP'])
    cpu.run(1_000_000)
    cpu.uart_in.extend(b'S')               # ask for a fresh screen, as tdh3 does
    cpu.run(1_000_000)
    rows = sorted((r[2], r[3]) for r in remote_records(cpu.uart_out) if r[0] == 't' and 14 <= r[2] < 110)
    assert len(rows) >= 2 and '446.000' in rows[0][1] and '146.520' in rows[1][1], rows
    assert rows[0][1].endswith(' <1m'), 'no age on the row: %r' % rows[0][1]   # a NUL cut it off
    import lcdshot
    fb, amber = lcdshot.frame(cpu.lcd), rgb(255, 176, 0)
    assert fb[13][64] == fb[14][0] == fb[14][127] == fb[21][0] == amber and fb[22][64] == 0, 'the lit row: a row over the text, a column either side'
    keys(cpu, ['DOWN', 'MENU'])            # the second row, 146.520
    cpu.run(1_000_000)
    assert freq(cpu) == 14652000, freq(cpu)


def test_ch_groups(cpu):                   # CH GROUPS: groups 1 and 3 on for a channel, only its byte 15 (and 13's mark) written
    menu(cpu, 'REMOTE', ['UP', 'EXIT'])
    menu(cpu, 'GRP', ['UP'])               # frequency mode: refused
    cpu.run(1_000_000)
    assert any('MEMORY CHANNELS ONLY' in t for t in remote_texts(cpu)), remote_texts(cpu)
    press(cpu, 'EXIT')                     # V/M: memory mode, channel 1
    cpu.run(1_000_000)
    rec = bytes(cpu.ee.mem[0x10:0x20])
    menu(cpu, 'GRP', ['UP'])
    cpu.run(1_000_000)
    keys(cpu, ['MENU', 'DOWN', 'DOWN', 'MENU', 'EXIT'])   # group 1, then group 3
    cpu.run(1_000_000)
    after = bytes(cpu.ee.mem[0x10:0x20])
    g0 = 0 if rec[15] == 0xFF else rec[15]
    assert after[15] == g0 ^ 0b101 and after[:13] + after[14:15] == rec[:13] + rec[14:15] and after[13] == rec[13] & ~3 | 1, (rec.hex(), after.hex())   # 13: the mark


def ch_groups(cpu):                        # the tuned channel's groups as loaded (chan_t: rx tx rxt txt am narrow power groups)
    return cpu.xram[symbol(FW, '_ch') + 15]


def test_ch_groups_all_eight(cpu):         # all eight groups is byte 15 FF, a blank channel's byte too: ours carries a mark
    press(cpu, 'EXIT')                     # in byte 13 (bits 1:0 01), so it reads back as eight (it read as none)
    cpu.run(1_000_000)
    menu(cpu, 'GRP', ['UP'])
    cpu.run(1_000_000)
    keys(cpu, ['DOWN', 'MENU'] * 7 + ['EXIT', 'EXIT'])   # channel 1: group 1, then 2-8; out of the menu
    keys(cpu, ['UP', 'DOWN'])              # to channel 3 and back: channel 1 read again
    m = cpu.ee.mem
    assert m[0x1F] == 0xFF and m[0x1D] & 3 == 1 and ch_groups(cpu) == 0xFF, (hex(m[0x1D]), hex(m[0x1F]), hex(ch_groups(cpu)))
    longpress(cpu, 'MENU'); press(cpu, 'MENU')   # saved again, as a new channel (5): the mark goes with it
    assert m[0x5F] == 0xFF and m[0x5D] & 3 == 1, (hex(m[0x5D]), hex(m[0x5F]))


def test_ch_groups_blank_byte(cpu):         # byte 15 FF without the mark: stock's records (13 bits 1:0 00 or, erased, 11)
    for b13, b15, want in ((0x00, 0xFF, 0), (0x07, 0xFF, 0), (0x00, 0x00, 0), (0x00, 0x05, 0x05)):   # and CHIRP's zeroed
        def patch(ee, b13=b13, b15=b15):   # ones (13 at 00, 15 at 00 or its groups) read as they always did
            ee[0x10 + 13], ee[0x10 + 15] = b13, b15
        cpu = mem_ch1(patch)
        assert ch_groups(cpu) == want, (hex(b13), hex(b15), hex(ch_groups(cpu)))


def test_delete_ch(cpu):                   # DELETE CH: starts on the slot's channel; MENU twice empties it as CHIRP does
    menu(cpu, 'REMOTE', ['UP', 'EXIT'])
    press(cpu, 'EXIT')                     # V/M: memory mode, channel 1
    cpu.run(1_000_000)
    names = bytes(cpu.ee.mem[0x0D40:0x0D60])
    menu(cpu, 'DEL', ['UP'])
    cpu.run(1_000_000)
    assert any(t.startswith('001 CALL2M') for t in remote_texts(cpu)), remote_texts(cpu)
    keys(cpu, ['DOWN', 'MENU'])            # channel 2, the first MENU only asks
    cpu.run(1_000_000)
    assert cpu.ee.mem[0x20] != 0xFF and cpu.ee.mem[0x1900] == 0x0F, 'deleted on the first MENU'
    assert any(t.startswith('002 GMRS1?') for t in remote_texts(cpu)), remote_texts(cpu)   # after "DELETE"
    keys(cpu, ['EXIT', 'MENU'])            # V/M keeps it; ask again
    assert cpu.ee.mem[0x20] != 0xFF, 'V/M deleted it'
    keys(cpu, ['MENU'])
    cpu.run(1_000_000)
    assert bytes(cpu.ee.mem[0x20:0x30]) == b'\xff' * 16, bytes(cpu.ee.mem[0x20:0x30]).hex()
    assert cpu.ee.mem[0x1900] == 0x0D and bytes(cpu.ee.mem[0x0D40:0x0D60]) == names, hex(cpu.ee.mem[0x1900])
    assert cpu.ee.mem[0x10] != 0xFF and cpu.ee.mem[0x30] != 0xFF, 'a neighbour was touched'
    assert any(t.startswith('003 WX7') for t in remote_texts(cpu)), 'not still listing, on the next channel'
    keys(cpu, ['EXIT', 'EXIT'])            # the list, then the menu
    cpu.run(1_000_000)
    keys(cpu, ['UP'])                      # memory mode now steps 1 -> 3, past the empty 2
    cpu.run(1_000_000)
    assert freq(cpu) == 16255000, freq(cpu)


def test_ch_to_vfo(cpu):                   # CH TO VFO: channel 4 (446.100 +5, D023N/D754I, narrow, high) into slot A's VFO
    cpu.ee.mem[0x44:0x48] = bcd(45110000, 4)
    cpu.ee.mem[0x4E] = 0x18                # narrow, high power (stock's 1)
    menu(cpu, 'REMOTE', ['UP', 'EXIT'])
    menu(cpu, 'TOVFO', ['UP'])             # frequency mode: refused
    cpu.run(1_000_000)
    assert any('MEMORY CHANNELS ONLY' in t for t in remote_texts(cpu)), remote_texts(cpu)
    press(cpu, 'EXIT')                     # V/M: memory mode
    keys(cpu, '004')
    cpu.run(1_000_000)
    menu(cpu, 'TOVFO', ['UP'])
    cpu.run(1_000_000)
    texts = remote_texts(cpu)
    assert freq(cpu) == 44610000 and not any(t.startswith('*M') for t in texts), (freq(cpu), texts)
    for at, rows in (('TXT', [('SHIFT', '+'), ('OFFSET MHz', '5.00'), ('TX TONE', 'D754I')]),   # rows SHIFT to TAIL TONE
                     ('RXT', [('RX TONE', 'D023N'), ('BANDWIDTH', 'NARROW'), ('POWER', 'HIGH')])):   # ATTENUATOR to POWER
        menu(cpu, at)
        cpu.run(1_000_000)
        texts = remote_texts(cpu)
        for item, want in rows:
            row = [t for t in texts if t.startswith(item)]
            assert row and row[0].split()[-1] == want, (item, row)
        keys(cpu, ['EXIT'])


def test_ch_name(cpu):                     # CH NAME: multi-tap on the keypad, saved to the name bytes only, NUL padded
    menu(cpu, 'REMOTE', ['UP', 'EXIT'])
    press(cpu, 'EXIT')                     # V/M: memory mode, channel 1 (CALL2M)
    cpu.run(1_000_000)
    rec = bytes(cpu.ee.mem[0x10:0x20])
    menu(cpu, 'NAME', ['UP'])
    cpu.run(1_000_000)
    tap = lambda k: press(cpu, k, hold=100_000, after=100_000)   # quick taps: within a second of each other
    for k in '44':                         # G, H
        tap(k)
    cpu.run(1_500_000)                     # over a second: the next 4 starts a new letter
    for k in '4440' + '2222':              # I, space, then A B C 2
        tap(k)
    keys(cpu, ['#', '6'])                  # cursor right, M over the 2 of CALL2M
    keys(cpu, ['#', '#', 'UP', 'UP', 'DOWN'])   # past the M: space, A, B, back to A
    keys(cpu, ['*', '*', '#', '#'])        # left twice, right twice: still on it
    cpu.run(1_000_000)
    assert any(t.startswith('HI 2MMA') for t in remote_texts(cpu)), remote_texts(cpu)
    keys(cpu, ['MENU'])
    cpu.run(1_000_000)
    assert bytes(cpu.ee.mem[0x0D40:0x0D48]) == b'HI 2MMA\x00', bytes(cpu.ee.mem[0x0D40:0x0D48])
    assert bytes(cpu.ee.mem[0x10:0x20]) == rec and cpu.ee.mem[0x0D48:0x0D4D] == b'GMRS1', 'more than the name written'
    keys(cpu, ['EXIT'])                    # saved: back in the menu; V/M to the radio
    cpu.run(1_000_000)
    assert any('HI 2MMA' in t for t in remote_texts(cpu)), 'the home screen shows the old name'


def test_auto_shift(cpu):            # SHIFT AUTO: the ARRL band plan's repeater shift in VFO mode, else simplex
    menu(cpu, 'REMOTE', ['UP', 'EXIT'])
    menu(cpu, 'SHIFT', ['UP'] * 3)         # OFF, +, -, AUTO
    assert any('AUTO' in t for t in remote_texts(cpu)), 'no AUTO in the menu'
    keys(cpu, ['EXIT'])
    cpu.run(1_000_000)
    for f, want in [('146940', '-0.6'), ('147000', '+0.6'), ('145350', '-0.6'), ('146400', ''), ('442500', '+5'),
                    ('448000', '-5'), ('446000', ''), ('462725', '+5'), ('462750', 'RXONLY'), ('462575', '+5'),
                    ('462562', ''), ('146520', ''), ('145130', '-0.6'), ('145090', '')]:   # 462.562 types GMRS 1, 462.5625: simplex
        assert shift_of(cpu, f) == (want or 'WIDE'), f
    keys(cpu, '462587')                    # GMRS 2 typed: the half kilohertz is filled in, 462.5875
    assert freq(cpu) == 46258750, freq(cpu)
    keys(cpu, '146940')
    cpu.key = 'PTT'
    cpu.run(600_000)                       # ~11 s after the last 'S', right where REMOTE's 10 s mirror lapses: a
    assert freq(cpu) == 14634000 and pins(cpu)['TXV'], freq(cpu)   # mirrored retune here keys up at ~380 ms (~25 ms on the radio)
    cpu.key = None
    cpu.run(2_000_000)


def test_auto_shift_fresh(cpu):            # SHIFT AUTO is the default
    ee = eeprom()
    ee[0x1FC0:0x1FF0] = b'\xff' * 0x30     # fresh settings: AUTO
    cpu = boot(ee=ee)
    menu(cpu, 'REMOTE', ['UP', 'EXIT'])
    keys(cpu, ['EXIT'] + list('146940'))   # V/M: from channel 1 to frequency mode
    assert any(t.startswith('FM LOW -0.6') for t in remote_texts(cpu)), 'fresh SHIFT is not AUTO'


def shift_of(cpu, f):                      # slot A tuned to f: the shift (or WIDE, for none) its line shows
    keys(cpu, f)
    return [t for t in remote_texts(cpu) if t.startswith('FM ')][0].split()[2]


def test_offset_per_band(cpu):             # OFFSET is one per band: a repeater setting on 70 cm leaves 2 m's alone
    menu(cpu, 'REMOTE', ['UP', 'EXIT'])
    menu(cpu, 'SHIFT', ['UP', 'UP', 'EXIT'])   # OFF, +, -
    assert shift_of(cpu, '446000') == '-5', 'UHF is not 5.0 by default'
    menu(cpu, 'OFFS', ['UP', 'EXIT'])      # 5.00 to 5.05, on 70 cm
    assert shift_of(cpu, '446000') == '-5.05'
    assert shift_of(cpu, '145580') == '-0.6', "70 cm's OFFSET moved 2 m's"
    cpu.key = 'PTT'                        # the report: 145.580, SHIFT -, must not try for 140.580
    cpu.run(600_000)
    assert freq(cpu) == 14498000 and pins(cpu)['TXV'], freq(cpu)
    cpu.key = None
    cpu.run(2_000_000)
    menu(cpu, 'OFFS', ['DOWN', 'EXIT'])    # 0.60 to 0.55, on 2 m
    assert shift_of(cpu, '145580') == '-0.55'
    assert shift_of(cpu, '446000') == '-5.05', "2 m's OFFSET moved 70 cm's"


def offset_row(cpu):                       # OFFSET's value in the menu, then out
    menu(cpu, 'OFFS')
    cpu.run(1_000_000)
    row = [t for t in remote_texts(cpu) if t.startswith('OFFSET')]
    keys(cpu, ['EXIT'])
    cpu.run(1_000_000)
    return row[0].split()[-1]


def test_offset_old_settings(cpu, old):    # settings from before OFFSET was per band: each band starts on its standard
    ee = eeprom(); ee[0x1FC3] = 0x49; ee[0x1FC5] = old << 4   # SHIFT -, the old single OFFSET
    cpu = boot(ee=ee)
    menu(cpu, 'REMOTE', ['UP', 'EXIT'])
    assert shift_of(cpu, '145580') == '-0.6' and offset_row(cpu) == '0.60', old
    assert shift_of(cpu, '446000') == '-5' and offset_row(cpu) == '5.00', old


def test_offset_dial(cpu):                 # 50 kHz steps, 0.05 to 12.75 MHz, round past either end
    ee = eeprom(); ee[0x1FC3] = 0x49; ee[0x1FCA] = 255   # SHIFT -, VHF 12.75
    cpu = boot(ee=ee)
    menu(cpu, 'REMOTE', ['UP', 'EXIT'])
    assert shift_of(cpu, '145580') == '-12.75' and offset_row(cpu) == '12.75'
    menu(cpu, 'OFFS', ['UP', 'EXIT'])
    assert shift_of(cpu, '145580') == '-0.05'
    menu(cpu, 'OFFS', ['DOWN', 'EXIT'])
    assert shift_of(cpu, '145580') == '-12.75'


def test_shift_digits(cpu):                # a shift of 10 MHz or more shows in full, not its last digit
    ee = eeprom(); ee[0x1FC3] = 0x49; ee[0x1FCB] = 200   # SHIFT -, UHF 10.00
    cpu = boot(ee=ee)
    menu(cpu, 'REMOTE', ['UP', 'EXIT'])
    assert shift_of(cpu, '446000') == '-10'
    cpu.ee.mem[0x44:0x48] = bcd(14610000, 4)   # channel 4: 446.100, transmits on 146.100
    press(cpu, 'EXIT')                     # V/M: memory mode
    keys(cpu, '004')
    cpu.run(1_000_000)
    line = [t for t in remote_texts(cpu) if t.startswith('FM ')][0]
    assert line.split()[2] == '-300', line


def test_ch_to_vfo_offset(cpu, tx, shift, off):   # CH TO VFO: any 50 kHz multiple comes over; anything else is simplex
    cpu.ee.mem[0x44:0x48] = bcd(tx, 4)
    menu(cpu, 'REMOTE', ['UP', 'EXIT'])
    press(cpu, 'EXIT')                     # V/M: memory mode
    keys(cpu, '004')
    cpu.run(1_000_000)
    menu(cpu, 'TOVFO', ['UP'])
    cpu.run(1_000_000)
    assert offset_row(cpu) == off, tx
    menu(cpu, 'SHIFT')
    cpu.run(1_000_000)
    row = [t for t in remote_texts(cpu) if t.startswith('SHIFT')]
    assert row and row[0].split()[-1] == shift, (tx, row)
    keys(cpu, ['EXIT'])


def test_entry_rxonly(cpu):                # RX-only: 'RXONLY' made the info line overflow into entry
    cpu = boot(FW_RX)
    menu(cpu, 'REMOTE', ['UP', 'EXIT'])
    keys(cpu, ['1', '6', '2'])
    cpu.uart_out.clear()
    cpu.uart_in.extend(b'S')
    cpu.run(1_500_000)
    texts = [r[3] for r in remote_records(cpu.uart_out) if r[0] == 't']
    assert 'FREQ 162' in texts, texts
    keys(cpu, ['5', '5', '0'])
    assert freq(cpu) == 16255000, freq(cpu)


def test_remote_pc_link_exit(cpu):         # UP pressed remotely starts PC LINK; its release is lost
    menu(cpu, 'REMOTE', ['UP'])
    keys(cpu, ['#', '#'])                  # REMOTE -> PC LINK
    cpu.uart_in.append(0x80 | 12)          # K_UP from the computer: starts PC LINK
    cpu.run(400_000)
    cpu.uart_in.append(0x80)               # release, swallowed by the clone loop
    cpu.run(400_000)
    press(cpu, 'EXIT', hold=600_000)       # V/M leaves PC LINK...
    cpu.run(2_000_000)
    hello = b'PVOJH\x5c\x14'
    cpu.uart_out.clear()
    cpu.uart_in.extend(hello)
    cpu.run(1_000_000)
    assert b'\x06' not in cpu.uart_out, 'still (or again) in PC LINK after V/M'


def mem_ch1(patch):                        # boot in memory mode on channel 1 after patching its record
    ee = eeprom()
    patch(ee)
    cpu = boot(ee=ee)
    keys(cpu, ['EXIT'])                    # V/M: channel 1 (146.520)
    return cpu


def no_tx(cpu):
    cpu.key = 'PTT'
    cpu.run(700_000)                       # long enough to outlast a full redraw (slow in the emulator)
    p = pins(cpu)
    cpu.key = None
    cpu.run(600_000)                       # a refusal tone plays for 200 ms
    return not p['TXV'] and not p['TXU'] and p['APC'] == 0


def test_tx_lock(cpu):                     # TX frequency FF FF FF FF (CHIRP duplex off): never keys
    cpu = mem_ch1(lambda ee: ee.__setitem__(slice(0x14, 0x18), b'\xff' * 4))
    assert no_tx(cpu), 'TX-locked channel transmitted'
    menu(cpu, 'REMOTE', ['UP', 'EXIT'])
    remote_texts(cpu)                      # sync the mirror
    no_tx(cpu)
    cpu.bk.busy = lambda f: True           # a signal opens the squelch: the screen redraws
    cpu.run(1_500_000)                     # the refusal must outlive that redraw
    assert status_line(cpu) == 'TX LOCKED CHANNEL', status_line(cpu)


def usb_ch1(ee):                            # channel 1 in USB: mode bits 13.3 (2) and 14.2 (1), our mark 01 in 13 bits 1:0
    ee[0x10 + 13] = 0x08 | 1


def test_ssb_never_transmits(cpu):         # USB (and AM) are receive only: PTT never keys, on a VFO or a channel
    menu(cpu, 'MODE', ['UP', 'UP', 'EXIT'])   # FM, AM, USB: slot A's VFO on 146.520 in USB
    r = cpu.bk.regs
    assert r[0x3D] == 0 and r[0x73] & 0x10 and r[0x47] & 0x0F00 in (0, 0x0500), (hex(r[0x3D]), hex(r.get(0x73, 0)))
    assert no_tx(cpu), 'USB transmitted'
    assert status_line(cpu) in (None, 'TX NOT ALLOWED'), status_line(cpu)
    menu(cpu, 'MODE', ['UP', 'EXIT'])      # back to FM: transmits again, the chip back on its IF and AFC
    assert r[0x3D] == 0x2AAB and not r[0x73] & 0x10, (hex(r[0x3D]), hex(r[0x73]))
    assert not no_tx(cpu), 'FM refused after USB'
    menu(cpu, 'MODE', ['UP', 'EXIT'])      # AM on 2 m: refused too
    assert no_tx(cpu), 'AM transmitted'
    cpu = mem_ch1(usb_ch1)                 # a channel in USB
    assert no_tx(cpu), 'USB channel transmitted'
    assert cpu.bk.regs[0x3D] == 0, 'channel 1 not tuned in USB'


def test_ssb_mode_bits(cpu):               # the record's mode bits, only with our mark: stock and CHIRP channels read FM
    for b13, usb in ((0x08 | 1, True), (0x08, False), (0x08 | 3, False), (0x0B, False), (1, False)):
        cpu = mem_ch1(lambda ee: (ee.__setitem__(0x10 + 13, b13), ee.__setitem__(0x10 + 14, 0)))
        assert (cpu.bk.regs[0x3D] == 0) == usb, (hex(b13), hex(cpu.bk.regs.get(0x3D, 0x2AAB)))
    cpu = mem_ch1(lambda ee: (ee.__setitem__(0x10 + 13, 0x08 | 1), ee.__setitem__(0x10 + 14, 0x04)))
    assert cpu.bk.regs.get(0x3D) != 0, 'mode 3 (no such mode) tuned USB'
    cpu = boot()                           # a VFO in USB saved to a channel keeps it, and reads back as USB
    menu(cpu, 'MODE', ['UP', 'UP', 'EXIT'])
    longpress(cpu, 'MENU'); press(cpu, 'MENU')   # channel 5
    m = cpu.ee.mem
    assert m[0x50 + 13] & 0x0B == 0x09 and not m[0x50 + 14] & 0x04, bytes(m[0x50:0x60]).hex()
    keys(cpu, ['EXIT'] + list('005'))      # memory mode, channel 5
    assert cpu.bk.regs[0x3D] == 0, 'channel 5 not in USB'


def test_ssb_tuning_and_squelch(cpu):      # USB: its own step on hold 4, the 10 Hz digits shown, squelch by RSSI alone
    menu(cpu, 'REMOTE', ['UP', 'EXIT'])
    menu(cpu, 'MODE', ['UP', 'UP', 'EXIT'])
    press(cpu, 'UP')                       # SSB step 0.05 kHz by default
    assert freq(cpu) == 14652005, freq(cpu)
    t = remote_texts(cpu)
    assert '05' in t and any(x.startswith('USB RXONLY') for x in t) and any(x.strip().endswith('0.05K') for x in t), t
    longpress(cpu, '4')                    # 0.1 kHz; FM's STEP kHz untouched
    press(cpu, 'UP')
    assert freq(cpu) == 14652010, freq(cpu)
    assert cpu.ee.mem[0x1FC2] & 15 == 6, 'FM step changed'
    cpu.bk.busy = lambda f: True           # the chip's noise squelch open, but no signal: stays shut in USB
    cpu.run(1_500_000)
    assert cpu.bk.regs[0x47] != 0x6540, 'USB opened on the noise squelch alone'
    cpu.bk.dbm = lambda f: -90             # a station over SQUELCH 3's RSSI level
    cpu.run(1_500_000)
    assert cpu.bk.regs[0x47] == 0x6540, hex(cpu.bk.regs[0x47])   # AF type 5: the SSB demodulator


def test_busy_lockout(cpu):                # byte 13 bit 2 (CHIRP bcl): no PTT while the squelch is open
    def bcl(ee):
        ee[0x10 + 13] |= 0x04
    cpu = mem_ch1(bcl)
    cpu.bk.busy = lambda f: True
    cpu.run(800_000)
    assert no_tx(cpu), 'keyed over a busy channel'
    cpu.bk.busy = None
    cpu.run(800_000)
    assert not no_tx(cpu), 'BCL blocked a clear channel'


def test_busy_lockout_not_on_vfo(cpu):     # channel 1 has busy lockout; back on a frequency it must not carry over
    cpu = mem_ch1(lambda ee: ee.__setitem__(0x10 + 13, 0x04))
    keys(cpu, ['EXIT'] + list('146550'))   # frequency mode, 146.550
    cpu.bk.busy = lambda f: f == 14655000
    cpu.run(1_500_000)
    assert not no_tx(cpu), 'BUSY: NO TX on a frequency'


def test_busy_lockout_other_slot(cpu):     # PTT onto a busy BCL channel from the other slot, or answering it in dual
    def bcl(ee):                           # watch: refused too (the retune had cleared the squelch's open flag)
        ee[0x10 + 13] |= 0x04
    for dw in (0, 1):
        cpu = mem_ch1(bcl)                 # A: channel 1, 146.520, busy lockout
        keys(cpu, ['#'])                   # B selected
        if dw:
            menu(cpu, 'DW', ['UP', 'EXIT'])
        cpu.bk.busy = lambda f: f == 14652000
        cpu.run(3_000_000)                 # dual watch: hearing A
        assert no_tx(cpu), 'keyed over busy channel 1 (dual watch %d)' % dw


def test_tail_and_mic(cpu):                # mic gain 7 -> REG_7D; 55 Hz tail before the carrier drops
    menu(cpu, 'MIC', ['UP', 'UP', 'EXIT'])
    keys(cpu, ['4', '4', '6', '0', '0', '0'])
    cpu.key = 'PTT'
    cpu.run(300_000)
    assert cpu.bk.regs[0x7D] == 0xE940 | 0x0D, hex(cpu.bk.regs[0x7D])
    n = len(cpu.bk.writes)
    cpu.key = None
    cpu.run(1_500_000)
    w = cpu.bk.writes[n:]
    assert (0x51, 0x903E) in w and (0x07, 0x046F) in w, w[:8]


def test_tot_warning(cpu):                 # TOT 30 s: a beep at 20 s, cut off at 30 s
    menu(cpu, 'TOT', ['DOWN', 'DOWN', 'EXIT'])   # default 120 s -> 60 -> 30
    keys(cpu, ['4', '4', '6', '0', '0', '0'])
    cpu.key = 'PTT'
    cpu.run(15 * 800_000)                  # 4000 instructions = 5 ms, but long interrupt-off stretches
    b0 = cpu.t1_count                      # make firmware time run ~10% slow here: generous windows
    cpu.run(12 * 800_000)
    assert cpu.t1_count > b0, 'no warning beep before the timeout'
    cpu.run(10 * 800_000)
    assert not pins(cpu)['TXU'], 'still transmitting after the timeout'
    cpu.key = None


def keyed(cpu):                            # either TX path on
    p = pins(cpu)
    return p['TXU'] or p['TXV']


def test_vox(cpu):                         # VOX 5: the chip's detector set, a voice keys slot A, VOX DELAY holds it, quiet unkeys
    cpu.bk.voice = 900
    cpu.run(2_000_000)
    assert not keyed(cpu) and not cpu.bk.regs.get(0x31, 0) & 4, 'VOX off by default, yet it keyed or listened'
    cpu.bk.voice = 0
    menu(cpu, 'REMOTE', ['UP', 'EXIT'])
    menu(cpu, 'VOX', ['UP'] * 5 + ['EXIT'])
    r = cpu.bk.regs
    assert (r[0x46], r[0x79], r[0x31] & 4, r[0x3F] & 0x30) == (0xA000 | 534, 0x1800 | 401, 4, 0x30), [hex(r.get(x, 0)) for x in (0x46, 0x79, 0x31, 0x3F)]
    remote_texts(cpu)
    cpu.run(3_000_000)                     # and armed after VOX DELAY (1 s) of quiet
    assert 'VOX' in status_bar(cpu).split(), status_bar(cpu)
    cpu.bk.voice = 600
    cpu.run(800_000)
    assert keyed(cpu) and pins(cpu)['TXV'], 'a voice did not key slot A'
    assert cpu.bk.regs[0x31] & 4, 'VOX off while transmitting: it could never let go'
    cpu.bk.voice = 0
    cpu.run(400_000)                       # 0.5 s of quiet: still on (VOX DELAY 1 s)
    assert keyed(cpu), 'dropped before VOX DELAY'
    cpu.run(1_500_000)
    assert not keyed(cpu), 'still keyed after VOX DELAY'


def test_vox_safety(cpu):                  # VOX never keys while a station is heard, out of TX BANDS (and never chimes
    menu(cpu, 'VOX', ['UP'] * 5 + ['EXIT'])   # trying again), in the menu, locked as KEYS+PTT, or straight after a timeout
    cpu.run(2_000_000)
    cpu.bk.busy = lambda f: True           # someone talking: the squelch open
    cpu.run(1_000_000)
    cpu.bk.voice = 600
    cpu.run(1_500_000)
    assert not keyed(cpu), 'VOX keyed over a station'
    cpu.bk.voice = 0
    cpu.bk.busy = None
    cpu.run(2_000_000)
    keys(cpu, '162550')                    # NOAA weather: refused
    cpu.run(2_000_000)
    n = len(cpu.bk.writes)
    cpu.bk.voice = 600
    cpu.run(3_000_000)
    assert not keyed(cpu) and (0x30, 0xC1FE) not in cpu.bk.writes[n:], 'VOX keyed out of band'
    assert (0x47, 0x6340) not in cpu.bk.writes[n:], 'a VOX refusal chimed: the mic would hear it'
    cpu.bk.voice = 0
    cpu.run(2_000_000)
    keys(cpu, '146520')
    cpu.run(2_000_000)
    keys(cpu, ['MENU'])                    # the menu open: no VOX
    cpu.bk.voice = 600
    cpu.run(1_500_000)
    assert not keyed(cpu), 'VOX keyed in the menu'
    cpu.bk.voice = 0
    keys(cpu, ['EXIT'])
    cpu.run(2_000_000)
    menu(cpu, 'KLOCK', ['UP', 'EXIT'])
    longpress(cpu, '*')
    cpu.run(2_000_000)
    cpu.bk.voice = 600
    cpu.run(1_500_000)
    assert not keyed(cpu), 'VOX keyed with PTT locked'
    cpu.bk.voice = 0
    longpress(cpu, '*')
    menu(cpu, 'TOT', ['DOWN', 'DOWN', 'EXIT'])   # 30 s
    cpu.run(2_000_000)
    cpu.bk.voice = 600                     # talking on and on
    cpu.run(800_000)
    assert keyed(cpu), 'VOX did not key'
    cpu.run(36 * 800_000)
    assert not keyed(cpu), 'the timeout did not cut VOX off'
    cpu.run(4 * 800_000)
    assert not keyed(cpu), 'VOX keyed again straight after the timeout'
    cpu.bk.voice = 0
    cpu.run(2_000_000)                     # quiet for VOX DELAY: armed again
    cpu.bk.voice = 600
    cpu.run(800_000)
    assert keyed(cpu), 'VOX never came back after the quiet'
    cpu.bk.voice = 0
    cpu.run(2_500_000)


def test_ptt_wakes_backlight(cpu):         # RX-only: PTT is refused but still lights the screen
    cpu = boot(FW_RX)
    menu(cpu, 'LIGHT', ['DOWN', 'EXIT'])   # 10 s
    cpu.run(11 * 800_000)
    assert cpu.xram[0xF140] == 0, 'backlight still on after its timeout'
    cpu.key = 'PTT'
    cpu.run(300_000)
    cpu.key = None
    cpu.run(800_000)                       # the refusal tone (200 ms) plays before the redraw
    assert cpu.xram[0xF140] == 200, 'PTT left the screen dark'


def test_dark_key_only_wakes(cpu):          # screen dark: the first key lights it and does nothing else
    cpu = boot(FW_RX)
    menu(cpu, 'LIGHT', ['DOWN', 'EXIT'])   # 10 s
    cpu.run(13 * 800_000)                  # past the light (10 s) and any REMOTE mirror window (10 s)
    assert cpu.xram[0xF140] == 0, 'backlight still on after its timeout'
    keys(cpu, ['9'])                       # only wakes it: typed, it would start an entry
    cpu.run(1_000_000)
    assert cpu.xram[0xF140] != 0, 'the key left the screen dark'
    keys(cpu, ['4', '4', '6', '0', '0', '0'])   # lit now: every key counts (a typed 9 would spoil this)
    cpu.run(1_000_000)
    assert freq(cpu) == 44600000, freq(cpu)


def remote_texts(cpu):                     # what the bottom line etc. show now, via REMOTE
    cpu.uart_out.clear()
    cpu.uart_in.extend(b'S')
    cpu.run(3_000_000)                     # a mirrored full redraw is ~2M instructions here
    return [r[3] for r in remote_records(cpu.uart_out) if r[0] == 't']


def test_basics(cpu):                      # the reviewer's list: each of these used to fail silently
    menu(cpu, 'REMOTE', ['UP', 'EXIT'])
    keys(cpu, ['1', '4', '6', '5'])
    assert 'FREQ 146.5' in remote_texts(cpu), 'typed frequency has no decimal point'
    keys(cpu, ['EXIT'] * 4 + ['EXIT'])     # clear the digits, then V/M: memory mode
    keys(cpu, ['3', '0', '0'])
    assert 'NO SUCH CHANNEL' in remote_texts(cpu), 'channel 300 accepted'
    menu(cpu, 'GROUP', ['UP'] * 5 + ['EXIT'])   # group 5: no channel in it
    keys(cpu, ['*'])
    assert 'GROUP IS EMPTY' in remote_texts(cpu), 'scanned an empty group'
    longpress(cpu, '*')                    # keypad lock
    press(cpu, 'SIDE2')
    assert torch(cpu), 'flashlight dead while locked'


def test_menu_times_out(cpu):              # a forgotten menu goes back to the radio after 20 s
    keys(cpu, ['MENU'])
    cpu.run(26 * 800_000)                  # firmware time runs ~10% slow in the emulator
    press(cpu, 'UP')
    assert freq(cpu) == 14652500, 'still in the menu after 26 s'


def test_settings_survive_one_bad_value(cpu):   # one bad byte resets that setting, not all of them
    menu(cpu, 'REMOTE', ['UP', 'EXIT'])
    menu(cpu, 'THEME', ['UP', 'UP', 'UP', 'EXIT'])   # night red
    cpu.run(3_000_000)                     # saved 2 s after the last change
    ee = bytearray(cpu.ee.mem)
    assert ee[0x1FC4] >> 6 & 1 and ee[0x1FC5] >> 2 & 3 == 3, ee[0x1FC0:0x1FF0].hex()   # REMOTE bit 6, THEME bits 2-3
    ee[0x1FC0 + 1] = 0x77                  # squelch 119: out of range
    cpu2 = boot(ee=ee)
    a = int([l.split()[1] for l in open(os.path.join(HERE, 'build', 'td-h3', 'fw.map')) if ' _remote ' in l][0], 16)
    assert cpu2.xram[a] == 1, 'REMOTE lost to one bad byte'
    cpu2.run(3_000_000)
    assert not cpu2.ee.writes or cpu2.ee.mem[0x1FC5] >> 2 & 3 == 3, 'theme lost'


def test_settings_bad_slot_mode(cpu):      # slot A's memory/frequency byte 2 (corrupt): reset, so V/M still switches
    import struct                          # (it stayed on memory: 2 ^ 1 = 3, never 0)
    ee = eeprom()
    ee[0x1FC0 + 29:0x1FC0 + 35] = struct.pack('<IBB', 14653000, 2, 2)
    cpu = boot(ee=ee)
    at = static_at(FW, 'cfg') + 33         # cfg.s[0].mem
    m = [cpu.xram[at]]
    for _ in range(2):
        press(cpu, 'EXIT'); m.append(cpu.xram[at])
    assert m == [0, 1, 0], m


def test_tail_migration(cpu):              # a 0xAB block's TAIL TONE OFF (0x1FC4 bit 3) becomes 0x1FC9's 2-bit OFF
    ee = eeprom()                          # its 0xAD block, relabelled 0xAB (POWER LOW as 0xAB has it: 0)
    ee[0x1FC0], ee[0x1FC3], ee[0x1FC4], ee[0x1FC9] = 0xAB, ee[0x1FC3] & ~3, ee[0x1FC4] | 0x48, 0x03   # TAIL OFF, REMOTE on, COMPANDER BOTH
    c = boot(ee=ee)
    menu(c, 'TAIL')
    assert any(t.startswith('TAIL TONE') and t.endswith('OFF') for t in remote_texts(c)), remote_texts(c)
    keys(c, ['EXIT'])
    menu(c, 'BEEP', ['UP', 'UP', 'EXIT'])  # a change, so the block is saved
    c.run(3_000_000)
    assert (c.ee.mem[0x1FC0], c.ee.mem[0x1FC3] & 3, c.ee.mem[0x1FC4] & 8, c.ee.mem[0x1FC9]) == (0xAD, 1, 0, 0x07), bytes(c.ee.mem[0x1FC0:0x1FF0]).hex()


def test_power_migration(cpu, old, new, name):   # a 0xAC block's POWER (0 LOW, 1 HIGH, 2 ULTRA LOW) keeps its level as MID comes in
    ee = eeprom()
    ee[0x1FC0], ee[0x1FC3] = 0xAC, ee[0x1FC3] & ~3 | old
    c = boot(ee=ee)
    menu(c, 'REMOTE', ['UP', 'EXIT'])       # a change, so the block is saved; and the screen mirrored
    menu(c, 'POWER')
    rows = [t.split(None, 1)[1].strip() for t in remote_texts(c) if t.startswith('POWER ')]
    assert name in rows, (name, rows)
    keys(c, ['EXIT'])
    c.run(3_000_000)
    assert (c.ee.mem[0x1FC0], c.ee.mem[0x1FC3] & 3) == (0xAD, new), (name, bytes(c.ee.mem[0x1FC0:0x1FC4]).hex())


def test_mid_power(cpu):                   # MID: the whole part of sqrt(LOW x HIGH) of the segment's factory duties
    ee = eeprom()
    ee[0x1F50 + 9], ee[0x1F60 + 9] = 40, 130   # 440-449 MHz: LOW 40, HIGH 130 (both radios' tables)
    ee[0x1F50 + 1], ee[0x1F60 + 1] = 50, 130   # 140-149 MHz: 50, 130
    c = boot(ee=ee)
    menu(c, 'POWER', ['UP', 'EXIT'])        # LOW -> MID
    for f, duty in (('446000', 72), ('146520', 80)):
        keys(c, f)
        c.key = 'PTT'
        c.run(600_000)
        p = pins(c)
        c.key = None
        c.run(1_500_000)
        assert p['APC'] == duty, (f, p)
    ee[0x1F60 + 1] = 0xFF                   # a blank HIGH byte: MID never keys the PA
    c = boot(ee=ee)
    menu(c, 'POWER', ['UP', 'EXIT'])
    keys(c, '146520')
    tx_refused(c)


def test_power_saved_as_stock(cpu):        # MID saves as 2, High as 1 (stock, CHIRP), ULP as 3; each loads back as saved
    menu(cpu, 'REMOTE', ['UP', 'EXIT'])
    m = cpu.ee.mem
    for n, want, name in ((5, 0x20, 'MID'), (6, 0x10, 'HIGH'), (7, 0x30, 'ULOW')):
        menu(cpu, 'POWER', ['UP', 'EXIT'])     # LOW -> MID -> HIGH -> ULTRA LOW
        longpress(cpu, 'MENU')             # the first free channel: 5, then 6
        press(cpu, 'MENU')
        assert m[0x10 * n + 14] & 0x30 == want, (n, hex(m[0x10 * n + 14]))
    press(cpu, 'EXIT')                     # V/M: memory mode
    for n, name in ((5, 'MID'), (6, 'HIGH'), (7, 'ULOW')):
        keys(cpu, '%03d' % n)
        cpu.run(1_000_000)
        assert any(t.startswith('FM ' + name) for t in remote_texts(cpu)), (n, remote_texts(cpu))


def test_attenuator(cpu):                  # ATT: LNA ~10 dB down in REG_13 (FM and AM), ATT on the status bar, saved
    menu(cpu, 'REMOTE', ['UP', 'EXIT'])
    assert cpu.bk.regs[0x13] == 0x03BE, hex(cpu.bk.regs[0x13])
    menu(cpu, 'ATT', ['UP', 'EXIT'])
    assert cpu.bk.regs[0x13] == 0x035E, hex(cpu.bk.regs[0x13])
    remote_texts(cpu)
    cpu.run(3_000_000)                     # saved 2 s after the last change
    assert 'ATT' in status_bar(cpu).split(), status_bar(cpu)
    assert cpu.ee.mem[0x1FC4] >> 1 & 1, cpu.ee.mem[0x1FC0:0x1FF0].hex()   # ATT: bit 1 of 0x1FC4
    keys(cpu, '120000')                    # airband: AM
    assert freq(cpu) == 12000000 and cpu.bk.regs[0x13] == 0x037F, (freq(cpu), hex(cpu.bk.regs[0x13]))
    menu(cpu, 'ATT', ['UP', 'EXIT'])
    assert cpu.bk.regs[0x13] == 0x03FF, hex(cpu.bk.regs[0x13])
    ee = eeprom()                          # a block older than 0xAB (0xA7, ATT 0xFF in its old place): defaults, ATT off
    ee[0x1FC0] = 0xA7
    ee[0x1FEE] = 0xFF
    cpu2 = boot(ee=ee)
    assert cpu2.bk.regs[0x13] in (0x03BE, 0x03FF), hex(cpu2.bk.regs[0x13])


def test_fresh_settings_use_channels(cpu):  # no settings block: A and B start on channels 1 and 2, dual watch on
    ee = eeprom()
    ee[0x1FC0:0x1FF0] = b'\xff' * 0x30
    c = boot(ee=ee)
    assert c.xram[rst_addr('_cfg') + 4] >> 2 & 1, 'DUAL WATCH not ON by default'
    labels = remote_texts_boot(c)
    labels = [l.strip() for l in labels]
    assert '*M1' in labels and '*M2' in labels and labels.index('*M1') < labels.index('*M2'), labels   # A's channel, then B's


def remote_texts_boot(cpu):
    menu(cpu, 'REMOTE', ['UP', 'EXIT'])
    return remote_texts(cpu)


def test_flash_restart_ends_with_remote(cpu, item):   # 'B', then REMOTE OFF or PC LINK: PTT later transmits, no restart.
    leave = ['MENU'] + num(item) + (['MENU', 'UP', 'MENU', 'EXIT'] if item == 'REMOTE' else ['MENU', 'EXIT'])
    menu(cpu, 'REMOTE', ['UP', 'EXIT'])    # remote_tick, which lets the arming lapse, only runs with REMOTE on
    cpu.uart_in.extend(b'B'); cpu.run(2_000_000)
    for k in leave:
        cpu.uart_in.append(0x80)           # tdh3's keep-armed byte
        press(cpu, k)
    cpu.run(12 * 800_000)
    cpu.key = 'PTT'; cpu.run(400_000)
    p = pins(cpu)
    assert not cpu.resets and (p['TXU'] or p['TXV']), (leave[1:], cpu.resets)
    cpu.key = None; cpu.run(1_000_000)


def test_flash_restart_side_key(cpu):      # armed, side key 1 (PTT-B) restarts as PTT does: it transmitted on B
    menu(cpu, 'REMOTE', ['UP', 'EXIT'])
    cpu.uart_in.extend(b'B'); cpu.run(2_000_000)
    n = len(cpu.bk.writes)
    cpu.key = 'SIDE1'; cpu.run(300_000)
    assert cpu.resets == [0x55] and (0x30, 0xC1FE) not in cpu.bk.writes[n:], cpu.resets


def test_flash_restart_saves(cpu):        # a setting changed just before a flash is kept: the restart came inside
    menu(cpu, 'REMOTE', ['UP', 'EXIT'])    # the 2 s save wait and lost it (DUAL WATCH, long 9)
    cpu.run(3 * 800_000)                   # saved
    dw = cpu.ee.mem[0x1FC4] & 4
    press(cpu, '9', hold=1_400_000, after=0)   # long 9; the flasher arms it at once and, answered,
    cpu.uart_out.clear(); cpu.uart_in.extend(b'B')   # says to hold PTT
    for _ in range(40):
        if b'S\xa5\x5a' in cpu.uart_out: break
        cpu.run(20_000)
    assert b'S\xa5\x5a' in cpu.uart_out, "'B' not answered"
    cpu.run(20_000)
    cpu.key = 'PTT'; cpu.run(300_000)
    assert cpu.resets == [0x55], cpu.resets
    assert cpu.ee.mem[0x1FC4] & 4 != dw, 'DUAL WATCH change lost by the restart: %s' % cpu.ee.mem[0x1FC0:0x1FF0].hex()
    cpu.key = None


def test_flash_keeps_dw(cpu):             # DUAL WATCH on before a flash is on after it: the restart, PTT held
    cpu.ee.mem[0x1FC4] |= 4                # through the reboot, the flasher's 'S' and 'Q' after
    cpu = boot(ee=bytes(cpu.ee.mem))
    menu(cpu, 'REMOTE', ['UP', 'EXIT'])
    cpu.run(3 * 800_000)
    assert cpu.ee.mem[0x1FC4] & 4, 'DUAL WATCH lost before the flash: %02X' % cpu.ee.mem[0x1FC4]
    cpu.uart_in.extend(b'B'); cpu.run(1_000_000)
    cpu.key = 'PTT'; cpu.run(300_000)
    assert cpu.resets == [0x55], cpu.resets
    cpu = boot(ee=bytes(cpu.ee.mem), key="PTT")   # the loader's restart into the new firmware, PTT still down
    cpu.run(2_000_000)
    cpu.key = None
    for b in (b'S', b'Q', b'S'):
        cpu.uart_in.extend(b); cpu.run(1_000_000)
    cpu.run(4 * 800_000)
    assert cpu.ee.mem[0x1FC4] & 4, 'DUAL WATCH off after the flash: %02X' % cpu.ee.mem[0x1FC4]
    assert any('DW' in t for t in remote_texts(cpu)), 'no DW on the screen: %r' % remote_texts(cpu)


def test_flash_restart(cpu):               # 'B' arms; PTT down then restarts into the loader before it keys up
    menu(cpu, 'REMOTE', ['UP', 'EXIT'])
    # Armed, then 10 s with nothing from the computer: disarmed, so PTT transmits as ever.
    cpu.uart_in.extend(b'B')
    cpu.run(11 * 800_000)
    cpu.key = 'PTT'
    cpu.run(300_000)
    p = pins(cpu)
    assert not cpu.resets and (p['TXU'] or p['TXV']), 'still armed after 10 s of silence'
    cpu.key = None
    cpu.run(2_000_000)
    cpu.uart_out.clear()
    cpu.uart_in.extend(b'B')
    cpu.run(3_000_000)
    assert cpu.uart_out == b'S\xa5\x5a', "'B' not answered as 'S' is, at 1 Mbaud: %r" % bytes(cpu.uart_out[:16])
    assert cpu.uart_reload() == 26, 'armed but not at the loader\'s 115200'
    assert not cpu.resets, 'restarted with no PTT'
    n = len(cpu.bk.writes)
    cpu.key = 'PTT'
    cpu.run(300_000)
    assert cpu.resets == [0x55], 'no restart into the loader: %r' % cpu.resets
    assert (0x30, 0xC1FE) not in cpu.bk.writes[n:], 'keyed up before the restart'
    # No loader in here, so it starts the app, XDATA as it was: PTT still held must not key up.
    scan = symbol(FW, '_key_scan')
    assert cpu.run(12_000_000, until=lambda: cpu.pc == scan), 'no boot after the restart'
    cpu.run(2_000_000)
    assert (0x30, 0xC1FE) not in cpu.bk.writes[n:] and not (pins(cpu)['TXU'] or pins(cpu)['TXV']), 'PTT held through the restart keyed up'
    cpu.key = None
    cpu.run(1_000_000)
    t = remote_texts(cpu)
    assert '146.520' in t and 'VFO' in t, 'not on the home screen after the restart: %r' % t


def test_mirror_lapses(cpu):              # REMOTE on but nobody talking: draws stop costing a serial trip
    menu(cpu, 'REMOTE', ['UP', 'EXIT'])
    cpu.uart_in.extend(b'S')
    cpu.run(2_000_000)
    assert cpu.uart_out, 'no mirror while watched'
    cpu.run(10 * 800_000)                  # 10 s of silence from the computer
    n = len(cpu.uart_out)
    keys(cpu, ['UP', 'UP'])                # draws happen...
    assert len(cpu.uart_out) == n, 'still mirroring with nobody watching'
    cpu.uart_in.append(0)                  # ...and any byte from the computer brings it back
    cpu.run(400_000)
    keys(cpu, ['DOWN'])
    assert len(cpu.uart_out) > n, 'mirror did not come back'


def test_ptt_works_locked(cpu):            # the lock is for stray key presses, not PTT
    keys(cpu, ['4', '4', '6', '0', '0', '0'])
    longpress(cpu, '*')
    cpu.key = 'PTT'
    cpu.run(400_000)
    assert pins(cpu)['TXU'], 'PTT blocked by the keypad lock'
    cpu.key = None
    cpu.run(2_000_000)


def test_dtmf(cpu):                        # a run of digits shown on the detail line, gone 10 s after; stray ones never
    menu(cpu, 'REMOTE', ['UP', 'EXIT'])
    cpu.bk.busy = lambda f: True
    cpu.run(500_000)                       # the carrier opens the squelch before any digit decodes
    for code in (1, 2):                    # speech: a stray "12", then nothing more
        cpu.bk.dtmf.append(code)
        cpu.run(150_000)
    cpu.run(3_000_000)
    assert not any('DTMF' in t for t in remote_texts(cpu)), 'a stray digit or two shown as DTMF'
    for code in (5, 5, 5, 1, 2, 3, 4, 15):  # "5551234#"
        cpu.bk.dtmf.append(code)
        cpu.run(150_000)
    cpu.run(3_000_000)
    texts = remote_texts(cpu)
    assert any('DTMF 5551234#' in t for t in texts), [t for t in texts if 'R ' in t or 'DTMF' in t]
    cpu.bk.busy = None
    cpu.run(12 * 800_000)
    texts = remote_texts(cpu)
    assert not any('DTMF' in t for t in texts), 'DTMF still shown after 10 s'


def tones(cpu, codes, on, off, again=0):   # DTMF codes at on/off ms; again: the decoder finds a held tone anew every so many ms
    for code in codes:
        for _ in range(on // again if again else 1):
            cpu.bk.dtmf.append(code)
            cpu.run((again or on) * 800)
        cpu.run(off * 800)


def test_dtmf_speech(cpu):                 # Q.24 timing: keypad digits shown, speech's tone soup never
    menu(cpu, 'REMOTE', ['UP', 'EXIT'])
    cpu.bk.busy = lambda f: True
    cpu.run(500_000)
    rnd = __import__('random').Random(7)
    tones(cpu, [rnd.randrange(16) for _ in range(100)], 20, 0)   # a voice: 2 s of short codes, no gaps
    cpu.run(500_000)
    texts = remote_texts(cpu)
    assert not any('DTMF' in t for t in texts), [t for t in texts if 'DTMF' in t]
    tones(cpu, (1, 4, 6, 14, 15), 70, 70)  # keypad "146*#", 70 ms on, 70 off
    cpu.run(500_000)
    texts = remote_texts(cpu)
    assert any('DTMF 146*#' in t for t in texts), [t for t in texts if 'DTMF' in t or 'R ' in t]
    cpu.run(12 * 800_000)                  # that line expires
    tones(cpu, (5,), 600, 70, again=20)    # one held tone the decoder keeps finding: one 5
    tones(cpu, (1, 2), 70, 70)
    cpu.run(500_000)
    texts = remote_texts(cpu)
    assert any('DTMF 512' in t for t in texts), [t for t in texts if 'DTMF' in t]


def audio(cpu):                            # the BK4819's audio out on (REG_47 not muted): squelch() sets it at once
    return int(cpu.bk.regs.get(0x47) != 0x6040)


AM_DB = {0x03FF: 0, 0x037F: 0, 0x03BE: 7, 0x035E: 7, 0x031E: 17, 0x00FF: 28, 0x007F: 28, 0x001F: 52}   # REG_13 -> dB off (ATT's too)


def test_am_gain(cpu):                     # air band: gain off in steps on strong signals so AM doesn't clip
    r = cpu.bk.regs
    menu(cpu, 'REMOTE', ['UP', 'EXIT'])
    keys(cpu, '118000')
    assert r[0x13] == 0x03FF, hex(r[0x13])
    assert r[0x43] == 0x3628 and r[0x49] == 0x1920 and r[0x14] == 0 and r[0x2B] == 0x0300, \
        [hex(r.get(k, 0)) for k in (0x43, 0x49, 0x14, 0x2B)]   # wide filter, AM AGC, no de-emphasis
    cpu.bk.busy = lambda f: True
    level = [-80]
    cpu.bk.dbm = lambda f: max(level[0] - AM_DB[r[0x13]], -130)   # the level less the gain taken off, to the noise floor
    cpu.run(500_000)
    assert r[0x13] == 0x03BE, 'a -80 dBm AM signal: one step (7 dB) off'
    level[0] = -60
    cpu.run(1_000_000)
    assert r[0x13] == 0x00FF, ('-60 dBm: 28 dB off', hex(r[0x13]))
    texts = remote_texts(cpu)
    assert ' -60' in texts, texts         # the meter adds the cut back
    level[0] = -20
    cpu.run(1_000_000)
    assert r[0x13] == 0x001F, ('-20 dBm: the last step', hex(r[0x13]))
    level[0] = -62                         # over -95 + 28 (the step back up): stays
    cpu.run(1_000_000)
    assert r[0x13] == 0x001F, 'stepped back inside the hysteresis'
    level[0] = -110
    cpu.run(2_000_000)
    assert r[0x13] == 0x03FF, ('gain not all back on a weak signal', hex(r[0x13]))
    menu(cpu, 'ATT', ['UP', 'EXIT'])       # with ATT on the steps keep ATT's LNA step
    level[0] = -70
    cpu.run(1_000_000)
    assert r[0x13] == 0x031E, hex(r[0x13])
    level[0] = -110
    cpu.run(2_000_000)
    assert r[0x13] == 0x037F, hex(r[0x13])
    menu(cpu, 'ATT', ['UP', 'EXIT'])
    keys(cpu, '146520')                    # FM keeps its own 03BE whatever the signal, and its filters
    level[0] = -40
    cpu.run(500_000)
    assert r[0x13] == 0x03BE and r[0x49] == 0x2A38 and r[0x14] == 0x19 and r[0x2B] == 0 and r[0x43] == 0x3628, \
        [hex(r.get(k, 0)) for k in (0x13, 0x49, 0x14, 0x2B, 0x43)]   # 3628: the weak-signal filter stays wide


def test_squelch_tail(cpu):                # the 55 Hz tail shuts the audio before the noise burst
    r = cpu.bk.regs
    assert r[0x07] == 0x2000 | 462 and r[0x3F] == 0x0C00 and r[0x51] == 0x904A, (hex(r[0x07]), hex(r[0x3F]), hex(r[0x51]))
    cpu.bk.busy = lambda f: True
    cpu.run(500_000)
    assert audio(cpu), 'squelch not open on the carrier'
    cpu.bk.tails = 1
    cpu.run(300_000)                       # the next pass of the main loop (slow here: it's redrawing, the tint too)
    assert cpu.bk.tails == 0 and not audio(cpu), 'audio still on after the tail'
    cpu.run(200_000)
    assert not audio(cpu), 'audio back while the tail carrier is still there'
    cpu.bk.busy = None
    cpu.run(300_000)
    cpu.bk.busy = lambda f: True           # the next over opens as usual
    cpu.run(500_000)
    assert audio(cpu), 'squelch stuck shut after a tail'
    menu(cpu, 'RXT', ['UP', 'EXIT'])       # a channel with its own RX tone: no tail detector
    assert r[0x3F] == 0x0800 and r[0x07] == 670 * 2065 // 1000, (hex(r[0x3F]), hex(r[0x07]))


def test_smeter_squelch(cpu):              # S9: the audio stays shut under -93 dBm even with the chip open
    menu(cpu, 'REMOTE', ['UP', 'EXIT'])
    menu(cpu, 'SQUELCH', ['UP'] * 15 + ['EXIT'])   # 3 -> 9, S1 ... S9
    texts = remote_texts(cpu)
    assert any('SQS9' in t for t in texts), texts
    assert cpu.bk.regs[0x78] == 0x5552 and cpu.bk.regs[0x4F] == 0x4238, 'chip squelch not at level 2'
    cpu.bk.busy = lambda f: True
    for d, on in ((-100, 0), (-90, 1), (-95, 1), (-97, 0)):   # opens at S9, closes 3 dB under it
        cpu.bk.dbm = lambda f, d=d: d
        cpu.run(1_000_000)
        assert audio(cpu) == on, (d, audio(cpu))


def test_meter(cpu):                       # one full-width bar, the reading inside it, a tick at an S-meter squelch
    import lcdshot
    green, white, track = rgb(0, 220, 80), rgb(255, 255, 255), rgb(30, 30, 30)
    dim = rgb(255, 176, 0) >> 1 & 0x7BEF   # the accent, amber, at half brightness
    menu(cpu, 'REMOTE', ['UP', 'EXIT'])
    cpu.bk.busy = lambda f: True
    cpu.bk.dbm = lambda f: -82              # S9+11: the bar to x 76 ((-82 + 130) * 8 / 5)
    cpu.run(1_000_000)
    texts = remote_texts(cpu)
    assert 'S9+11' in texts and ' -82dBm' in texts, texts
    fb = lcdshot.frame(cpu.lcd)
    assert fb[61][0] == fb[62][0] == fb[69][1] == fb[65][50] == fb[65][75] == green, 'not green to x 76, rows 61-69'
    assert fb[65][76] == fb[69][127] == rgb(6, 64, 28), 'the empty track: the RX tint while heard'
    for d in (-116, -112, -74, -59, -52):   # the edge under the text: the row above it (61) moves with
        cpu.bk.dbm = lambda f, d=d: d       # the text rows (69, the glyphs' blank foot row), not ahead of them
        cpu.run(700_000)                    # the next reading (330 ms) and its draw
        fb = lcdshot.frame(cpu.lcd)
        assert fb[61] == fb[69], (d, [x for x in range(128) if fb[61][x] != fb[69][x]])
    cpu.bk.dbm = lambda f: -82
    menu(cpu, 'SQUELCH', ['UP'] * 15 + ['EXIT'])   # S9: the tick at -93 dBm, x 59
    cpu.run(1_000_000)
    fb = lcdshot.frame(cpu.lcd)
    assert fb[65][59] == white and fb[65][58] == green and fb[65][60] == green, 'no tick at S9'
    cpu.bk.dbm = lambda f: -110             # under the squelch: shut, the bar dim amber to x 32, the tick stays
    cpu.run(1_000_000)
    fb = lcdshot.frame(cpu.lcd)
    assert fb[69][1] == dim and fb[65][31] == dim and fb[65][40] == track and fb[65][59] == white, 'weak signal: a plain track'
    cpu.bk.busy = cpu.bk.dbm = None
    menu(cpu, 'SQUELCH', ['DOWN'] * 15 + ['EXIT'])


def test_menu_vfo_items(cpu):              # on a memory channel the VFO-only items say so
    menu(cpu, 'REMOTE', ['UP', 'EXIT'])
    menu(cpu, 'POWER')
    rows = [t for t in remote_texts(cpu) if t.startswith(('SQUELCH', 'POWER'))]
    assert rows and not any('VFO' in t for t in rows), rows
    keys(cpu, ['EXIT', 'EXIT'])            # leave the menu, then V/M: memory channel 1
    menu(cpu, 'POWER')
    rows = [t for t in remote_texts(cpu) if t.startswith('POWER')]
    assert any(t.endswith('VFO LOW') for t in rows), rows
    assert any(t.startswith('RX TONE') and 'VFO' in t for t in remote_texts(cpu)), remote_texts(cpu)   # still VFO-only
    keys(cpu, ['0', '2'])                  # ATTENUATOR and SQUELCH: for the whole radio, not the VFO
    assert any(t.startswith('ATTENUATOR') and 'VFO' not in t for t in remote_texts(cpu)), remote_texts(cpu)
    assert any(t.startswith('SQUELCH') and 'VFO' not in t for t in remote_texts(cpu)), remote_texts(cpu)
    cpu.menu_last = MENU.index('ATT')


def test_menu_help(cpu):                    # the line under the list says what the lit item does
    menu(cpu, 'REMOTE', ['UP'])
    keys(cpu, ['0', '1'])
    assert 'signal needed to hear' in [t.strip() for t in remote_texts(cpu)], remote_texts(cpu)
    keys(cpu, ['DOWN'])
    assert 'cuts strong signals' in [t.strip() for t in remote_texts(cpu)], remote_texts(cpu)
    keys(cpu, num('VER'))
    assert 'firmware version' in [t.strip() for t in remote_texts(cpu)], remote_texts(cpu)
    keys(cpu, ['EXIT'])


def test_menu_digit_jump(cpu):             # 1 2 = item 12, 0 5 = item 5; one digit and a pause = that item
    menu(cpu, 'REMOTE', ['UP'])
    keys(cpu, ['1', '2'])
    assert any(t.endswith(' 12/%d' % len(MENU)) for t in remote_texts(cpu)), 'two digits did not jump'
    keys(cpu, ['0', '5'])
    assert any(t.endswith(' 05/%d' % len(MENU)) for t in remote_texts(cpu))
    keys(cpu, ['6'])
    cpu.run(2_000_000)                     # a 1 s pause ends a one-digit number
    assert any(t.endswith(' 06/%d' % len(MENU)) for t in remote_texts(cpu))


def test_auto_off(cpu):                    # AUTO OFF 30 min: radio, screen and light off; a key only wakes it
    menu(cpu, 'APO', ['UP', 'EXIT'])
    cpu.run(9_000_000)                     # 11 s without a key
    a = int(next(l.split()[0] for l in open(os.path.join(HERE, 'build', 'td-h3', 'main.rst')) if l.split()[-1:] == ['_idle10:']), 16)
    cpu.xram[a], cpu.xram[a + 1] = 179, 0  # 29 min 50 s idle
    n, m = len(cpu.bk.writes), len(cpu.lcd)
    cpu.run(9_000_000)
    assert cpu.xram[0xF140] == 0 and (0, 0x28) in cpu.lcd[m:], 'screen still on'
    assert (0x37, 0x1D00) in cpu.bk.writes[n:] and not pins(cpu)['VRX'], 'receiver still on'
    press(cpu, 'UP')                       # wakes; UP does nothing else
    cpu.run(1_000_000)
    assert cpu.xram[0xF140] == 200 and (0, 0x29) in cpu.lcd[m:], 'did not wake'
    assert freq(cpu) == 14652000 and pins(cpu)['VRX'], (freq(cpu), pins(cpu))


def test_screen_sleep(cpu):                 # the panel sleeps while the backlight is out, wakes with it, after AUTO OFF too
    menu(cpu, 'LIGHT', ['DOWN', 'EXIT'])   # 10 s
    menu(cpu, 'APO', ['UP', 'EXIT'])       # 30 min
    m = len(cpu.lcd)
    cpu.run(12 * 800_000)
    assert cpu.xram[0xF140] == 0 and (0, 0x10) in cpu.lcd[m:], 'panel not asleep with the backlight out'
    m = len(cpu.lcd)
    cpu.key = 'UP'                         # only lights it: the panel wakes first, dark, so its white start isn't seen
    for _ in range(100):
        if (0, 0x11) in cpu.lcd[m:]: break
        cpu.run(4_000)
    assert (0, 0x11) in cpu.lcd[m:] and cpu.xram[0xF140] == 0, 'lit as the panel woke: a white flash'
    cpu.run(40_000)                        # 50 ms
    assert cpu.xram[0xF140] == 0, 'lit before the panel settled (120 ms)'
    cpu.key = None
    cpu.run(700_000)
    assert cpu.xram[0xF140] == 200, 'backlight not on after the panel woke'
    cpu.run(12 * 800_000)                  # dark and asleep again, then AUTO OFF on top
    a = int(next(l.split()[0] for l in open(os.path.join(HERE, 'build', 'td-h3', 'main.rst')) if l.split()[-1:] == ['_idle10:']), 16)
    cpu.xram[a], cpu.xram[a + 1] = 179, 0
    cpu.run(9_000_000)
    m = len(cpu.lcd)
    press(cpu, 'UP')
    cpu.run(1_000_000)
    assert cpu.xram[0xF140] == 200 and (0, 0x11) in cpu.lcd[m:], 'panel left asleep after AUTO OFF'


def test_bell(cpu):                        # BELL: a call after 10 s of quiet rings once; one soon after, or with BELL off, doesn't
    bell = (0x71, 1568 * 1032444 // 100000)   # G6 on voice 1 (TONE_HZ in bk4819.c)
    def call():
        n = len(cpu.bk.writes)
        cpu.bk.busy = lambda f: f == 14652000
        cpu.run(1_000_000)
        cpu.bk.busy = None
        cpu.run(1_000_000)
        return bell in cpu.bk.writes[n:]
    menu(cpu, 'BELL', ['UP', 'EXIT'])
    cpu.run(11 * 800_000)                  # 10 s with no key
    assert call(), 'no bell for a call after a quiet spell'
    ok, on = amber_blinks(cpu)
    assert ok, 'BELL waiting: no amber blink (%d of 520 lit)' % on
    press(cpu, 'EXIT')                     # any key clears it
    assert not any(leds(cpu)), 'amber still lit after a key'
    assert not call(), 'rang again 1 s after the last call'
    menu(cpu, 'BELL', ['UP', 'EXIT'])      # off
    cpu.run(11 * 800_000)
    assert not call(), 'rang with BELL off'


def test_brightness(cpu):                  # BRIGHTNESS 5 -> 4: backlight duty 45 of 200 at once, after a timeout too, saved
    assert cpu.xram[0xF140] == 200, 'an older settings block not at full brightness'
    menu(cpu, 'BRIGHT', ['DOWN'])
    assert cpu.xram[0xF140] == 45, ('not dimmed in the menu', cpu.xram[0xF140])
    press(cpu, 'EXIT')
    menu(cpu, 'LIGHT', ['DOWN', 'EXIT'])   # 10 s
    cpu.run(11 * 800_000)
    assert cpu.xram[0xF140] == 0, 'backlight still on after its timeout'
    press(cpu, 'UP')
    assert cpu.xram[0xF140] == 45, ('a key lit it at', cpu.xram[0xF140])
    cpu.run(3_000_000)                     # saved 2 s after the last change
    assert cpu.ee.mem[0x1FC8] & 7 == 4, cpu.ee.mem[0x1FC0:0x1FF0].hex()   # level 4 is stored as 4 (5 as 0)


def test_freq_cal(cpu):
    menu(cpu, 'FCAL', ['UP'] * 10 + ['EXIT'])   # +1.0 ppm
    assert freq(cpu) == 14652000 + 146520 * 10 // 100000, freq(cpu)


def test_no_clipped_text(cpu):             # fixed text that would run off the 128 px screen
    import glob, re
    bad = []
    for f in glob.glob(os.path.join(HERE, '*.c')):
        for x, text, scale in re.findall(r'lcd_text\(\s*(\d+)\s*,\s*\w+\s*,\s*"([^"]*)"\s*,\s*(\d)', open(f).read()):
            if int(x) + 6 * int(scale) * len(text) > 128:
                bad.append('%s: "%s" at x=%s size %s' % (os.path.basename(f), text, x, scale))
    assert not bad, bad


def run(name, fn, cpu):
    fn(cpu)
    print('ok', name)


def test_first_block(cpu):                 # in order on one booted CPU: each leaves the radio as the next expects
    run('boot matches stock', test_boot_matches_stock, cpu)
    run('VHF RX path at 146.520', test_vhf_rx_path, cpu)
    run('stock hardware squelch thresholds + REG_0C bit 1', test_stock_squelch, cpu)
    run('A/B slots', test_ab_slots, cpu)
    run('keypad entry -> UHF path', test_keypad_entry_uhf, cpu)
    run('TX on 446.000 uses UHF path + factory cal', test_tx_allowed_band, cpu)
    run('TX refused on 162.550', test_tx_refused_out_of_band, cpu)
    run('memory channels from stock layout', test_memory_channels, cpu)
    run('DCS D023N RX / D754I TX from a stored channel', test_dcs, cpu)
    run('settings saved only to 0x1FC0 block', test_settings_saved_only_in_own_block, cpu)
    run('PC LINK clone protocol + protected cal', test_pc_link_clone, cpu)
    run('no PC LINK without the menu', test_no_auto_pc_link, cpu)


def test_no_cal_no_tx(cpu):
    cpu = boot(cal=False)
    keys(cpu, '446000')
    tx_refused(cpu)


def test_rxonly_never_transmits(cpu):
    if not os.path.exists(FW_RX):
        print('  (%s absent, skipped)' % FW_RX)
        return
    cpu = boot(FW_RX)
    keys(cpu, '446000')
    tx_refused(cpu)


FIRST = [
    ('first block: boot, RX path, TX band plan, clone protocol', test_first_block),
    ('no factory cal -> no TX', test_no_cal_no_tx),
    ('RX-only build never transmits', test_rxonly_never_transmits),
]

INDEPENDENT = [
    ('GROUP LINK: a scan covers several groups', test_group_link),
    ('CH GROUPS: a channel\'s groups set on the radio', test_ch_groups),
    ('CH GROUPS: all eight round-trips (byte 13 marks byte 15 ours)', test_ch_groups_all_eight),
    ('channel byte 15 FF without the mark (stock, CHIRP) reads as no groups', test_ch_groups_blank_byte),
    ('DELETE CH: MENU twice empties a channel', test_delete_ch),
    ('CH TO VFO: a channel copied into the VFO', test_ch_to_vfo),
    ('CH NAME: a channel named on the keypad', test_ch_name),
    ('LAST HEARD: newest first, MENU tunes to it', test_last_heard),
    ('REMOTE: a held key lets go without its release byte', test_remote_key_lets_go),
    ('REMOTE: a lost release lets go in FM auto presets too', test_remote_lost_release_in_fm_auto),
    ('flash restart: REMOTE OFF disarms it', partial(test_flash_restart_ends_with_remote, item='REMOTE')),
    ('flash restart: PC LINK disarms it', partial(test_flash_restart_ends_with_remote, item='PC')),
    ('flash restart: side key 1 restarts too, never transmits', test_flash_restart_side_key),
    ('REMOTE: one UP from tdh3 is one step, over a slow redraw', test_remote_up_one_step),
    ('REMOTE: a digit from tdh3 landing in a redraw still counts (menu number)', test_remote_digit_in_redraw),
    ('REMOTE: the same key in two tdh3 calls counts twice, dark screen too', test_remote_same_key_twice),
    ('REMOTE: a V/M fed a byte at a time (emuport) still counts', test_remote_paced_vm),
    ('REMOTE: bytes that land in a redraw are kept, a whole press read in turn', test_remote_bytes_kept_in_redraw),
    ('dual watch waits 3 s after a call drops (fades)', test_dual_watch_waits_after_a_fade),
    ('a scan stops for hold MENU (save)', test_scan_stops_for_save_and_tools),
    ('a scan stops for the hold-key tools (freq find)', test_scan_stops_for_freq_find),
    ('scan stop: back to the start with no hit, to the one hit (memory)', partial(test_scan_stop_goes_back_or_to_hit, mem=True)),
    ('scan stop: back to the start with no hit, to the one hit (VFO)', partial(test_scan_stop_goes_back_or_to_hit, mem=False)),
    ('scan stop: two hits open LAST HEARD to pick from (memory)', partial(test_scan_stop_picks_from_hits, mem=True)),
    ('scan stop: two hits open LAST HEARD to pick from (VFO)', partial(test_scan_stop_picks_from_hits, mem=False)),
    ('scan stop: * paused stays, V/M always back to the start (memory)', partial(test_scan_stop_paused_and_vm, mem=True)),
    ('scan stop: * paused stays, V/M always back to the start (VFO)', partial(test_scan_stop_paused_and_vm, mem=False)),
    ('a scan saves the settings once, when it ends', test_scan_saves_settings_once),
    ('a quiet scan doesn\'t stall at the 327 s tick wrap', test_scan_quiet_past_tick_wrap),
    ('TAIL TONE on and off', test_tail_tone),
    ('COMPANDER: RX, TX, BOTH', test_compander),
    ('NARROW kHz: 6.25 narrows narrow FM only', test_narrow_khz),
    ('TAIL TONE OFF moves to its 2-bit place (0xAB -> 0xAD)', test_tail_migration),
    ('POWER LOW keeps its level as MID comes in (0xAC -> 0xAD)', partial(test_power_migration, old=0, new=1, name='LOW')),
    ('POWER HIGH keeps its level as MID comes in (0xAC -> 0xAD)', partial(test_power_migration, old=1, new=3, name='HIGH')),
    ('POWER ULTRA LOW keeps its level as MID comes in (0xAC -> 0xAD)', partial(test_power_migration, old=2, new=0, name='ULTRA LOW')),
    ('MID power: sqrt(LOW x HIGH) of the factory table, never a blank one', test_mid_power),
    ('scan skip: off the scan list for good, group scans too, after power-off; hold V/M undoes', test_scan_skip_kept),
    ('TX BANDS: per-service transmit switches', test_tx_bands),
    ('a note clears itself after 3 s', test_note_times_out),
    ('FM broadcast radio (stock RDA5807 sequence)', test_fm_radio),
    ('FM seek up/down with wrap, presets save/recall', test_fm_seek_presets),
    ('FM seek finds a station over a floor under -128 dBm', test_fm_seek_low_floor),
    ('FM: side key 1 leaves and transmits on B, as PTT does on A', test_fm_side_key_leaves),
    ('FM: the weather alert look runs under the AUTO PRESETS? prompt', test_fm_auto_prompt_alert),
    ('FM: LIGHT times out, the first key on a dark screen only lights it', test_fm_light_times_out),
    ('FM: the flashlight works', test_fm_flashlight),
    ('FM in weather mode: an alert heard there sounds the alarm', test_fm_alert_in_weather_mode),
    ('FM auto presets: hold 0, confirm, cancel, frequency order', test_fm_auto_presets),
    ('FM auto presets keep the 25 strongest', test_fm_auto_presets_strongest),
    ('FM auto presets score three passes', test_fm_auto_presets_score),
    ('FM presets 1-25 on three pages', test_fm_preset_pages),
    ('FM: holding a non-digit key saves no preset', test_fm_hold_other_keys),
    ("FM grid shows each page's preset numbers", test_fm_grid_numbers),
    ('dual watch switches and returns', test_dual_watch),
    ('slot tints: green while heard, red while transmitting, per theme', test_slot_tints),
    ('tools/radio.py and tdh3 read the rows the firmware draws', test_tool_rows),
    ('a tinted slot\'s label mirrors plain, its letter inverse', test_tinted_label_reads),
    ('DIGITS ARCADE: shapes, shadow, label at the right, CLASSIC text to REMOTE, saved', partial(test_digits, style='ARCADE')),
    ('DIGITS LCD: clock segments, label at the right, CLASSIC text to REMOTE, saved', partial(test_digits, style='LCD')),
    ('DIGITS CLASSIC: the 5x7 font at 2x and the label as before, saved', partial(test_digits, style='CLASSIC')),
    ('meter: one full-width bar, the reading inside, the squelch tick', test_meter),
    ('dual watch: a wrong-tone carrier is not held', test_dual_watch_wrong_tone),
    ('Bluetooth clone protocol on UART0', test_bluetooth),
    ('save VFO to a memory channel', test_save_vfo_to_memory),
    ('the meter waits for the receiver to settle after a retune (no dips at every battery-saver wake)', test_meter_waits_for_retune),
    ('the meter: a new reading 3 times a second, a new channel\'s at once', test_meter_calm),
    ('hold UP repeats', test_hold_repeats),
    ('UP/DOWN land on the step raster', test_step_raster),
    ('8.33 and 15 kHz steps land on their rasters', test_air_band_steps),
    ('battery percent follows the 2S Li-ion curve', test_battery_curve),
    ('keypad lock', test_keypad_lock),
    ('KEY LOCK KEYS+PTT: PTT and PTT-B locked too, flashlight not', test_key_lock_ptt),
    ('scan group 1 only', test_scan_group),
    ('digit keys switch the scan group', test_scan_group_switch),
    ('empty group mid-scan: says so, keeps scanning, 2 still works', test_empty_group_mid_scan),
    ('scan resume STOP', test_scan_resume_stop),
    ('priority channel', test_priority),
    ('priority looked at every 5 s during a call', test_priority_while_receiving),
    ('no scan with the squelch open (SQL 0, monitor)', test_scan_refused_squelch_open),
    ('VFO scan outside the bands: +-1 MHz, or between A and B', test_vfo_scan_outside_bands),
    ('NOAA 1050 Hz alert', test_wx_alert),
    ('NOAA 1050 Hz alert in weather mode: alarm and blink, once per tone', test_wx_alert_in_weather_mode),
    ('NOAA alert with monitor on and at squelch 0', test_wx_alert_squelch_open),
    ('NOAA alert from the FM radio', test_wx_alert_from_fm),
    ('weather mode: long #, strongest WX, slot restored, alert follows', test_weather_mode),
    ('weather mode: no dual-watch looks at B', test_weather_mode_ignores_dual_watch),
    ('battery saver sleeps and wakes', test_battery_saver),
    ('battery saver: a refused PTT wakes the receiver', test_battery_saver_wakes_on_ptt),
    ('the MCU idles between ticks', test_mcu_idles),
    ('tools/battlog.py reads the status line', test_battlog_reads_status),
    ('flashlight, Olight-style: tap, moonlight, levels, double and triple tap, SOS', test_torch_modes),
    ('PTT-B transmits on slot B', test_ptt_b_keys_slot_b),
    ('PTT held from power-on: no TX till let go', test_ptt_held_at_boot),
    ('beeps are clear (sine) tones', test_clear_tones),
    ('FM chip powered down at boot', test_fm_chip_off_at_boot),
    ('REMOTE V n plays voice prompt n', test_remote_voice),
    ('ultra-low power = APC 0', test_ultra_low_power),
    ('MID saves as 2, High as stock\'s 1, ULP as 3', test_power_saved_as_stock),
    ('tone scan + copy (long 6)', test_tone_scan_copy),
    ('DCS tone scan: rotated inverted word -> D754I', test_tone_scan_dcs),
    ('DCS table is the 104 standard codes', test_dcs_table),
    ('freq find', test_freq_find),
    ('freq find rounds to the nearest 2.5 kHz', test_freq_find_rounds),
    ('spectrum scope sweep + tune', test_scope),
    ('frequency calibration', test_freq_cal),
    ('BRIGHTNESS dims the backlight, LIGHT timeout still works', test_brightness),
    ('BELL rings for a call after a quiet spell', test_bell),
    ('menu: VFO-only items say VFO on a memory channel', test_menu_vfo_items),
    ('menu: digits jump to an item', test_menu_digit_jump),
    ('menu: a line says what the lit item does', test_menu_help),
    ('AUTO OFF sleeps, a key wakes it', test_auto_off),
    ('the panel sleeps while dark, wakes with the light and after AUTO OFF', test_screen_sleep),
    ('scan pause: a never-ending station is left after the pause timer', test_scan_pause),
    ('scan stays on a long call (PAUSE BUSY)', test_scan_long_call_kept),
    ('scan hang time: a reply in 5 s keeps it, quiet moves on', test_scan_hang_time),
    ('scan: UP on a stop moves on at once, scan goes on', test_scan_up_skips_stop),
    ('scan: DOWN scans downward, UP back up', test_scan_direction),
    ('scan shows each channel\'s power, shift and tones', test_scan_shows_each_channel),
    ('scan resume HOLD stays the pause time, then moves on', test_scan_resume_hold),
    ('scan waits up to 400 ms for a tone channel tone', test_scan_waits_for_tone),
    ('FM preset with junk bytes reads as empty', test_fm_preset_junk),
    ('FM presets read afresh after the scope used the shared buffer', test_fm_presets_after_scope),
    ('saving to channel 300 fails instead of wrapping', test_save_to_typed_channel_over_199),
    ('saving with reverse on stores the channel the right way round', test_save_with_reverse_on),
    ('PRIORITY follows a channel saved over it', test_save_over_priority_channel),
    ('a number typed before PTT-B changes slot ends, never past 8 digits', test_entry_after_slot_change),
    ('saving over a used channel asks first', test_save_over_used_channel_asks),
    ('UP/DOWN stay in the scan group', test_up_down_stay_in_group),
    ('UP/DOWN: an all-groups channel is in every group', test_up_down_all_groups),
    ('DTMF digits shown, then cleared', test_dtmf),
    ('DTMF: keypad tones shown, speech and held tones not', test_dtmf_speech),
    ('AM: gain off in steps on strong signals, wide filter, AM AGC, no de-emphasis', test_am_gain),
    ('55 Hz squelch tail shuts the audio', test_squelch_tail),
    ('S-meter squelch S1-S9', test_smeter_squelch),
    ('PTT works with the keypad locked', test_ptt_works_locked),
    ('no fixed text runs off the screen', test_no_clipped_text),
    ('screen mirror lapses after 10 s of silence', test_mirror_lapses),
    ('flash restart: B arms, PTT restarts into the loader', test_flash_restart),
    ('flash restart: a setting changed just before is saved first', test_flash_restart_saves),
    ('a flash keeps DUAL WATCH on', test_flash_keeps_dw),
    ('hold 2 on a memory channel steps its saved power', test_channel_power),
    ('PC LINK: settings reset restarts with every default', test_pc_link_settings_reset),
    ('PC LINK: the light times out, 10 quiet minutes leave it', test_pc_link_light_and_idle),
    ('fresh settings start A/B on the first two channels, dual watch on', test_fresh_settings_use_channels),
    ('one bad settings byte keeps the rest', test_settings_survive_one_bad_value),
    ('a corrupt slot mode byte resets that slot', test_settings_bad_slot_mode),
    ('attenuator: REG_13 LNA down on FM and AM, ATT shown, saved', test_attenuator),
    ('TX-locked channel never keys', test_tx_lock),
    ('SSB never transmits (USB and AM refused, FM keys again)', test_ssb_never_transmits),
    ('USB in the channel record: our mark only, saved and read back', test_ssb_mode_bits),
    ('USB: SSB step, 10 Hz digits, RSSI squelch', test_ssb_tuning_and_squelch),
    ('busy channel lockout', test_busy_lockout),
    ('busy channel lockout stays with its channel, not the VFO', test_busy_lockout_not_on_vfo),
    ('busy channel lockout from the other slot and in dual watch', test_busy_lockout_other_slot),
    ('mic gain + 55 Hz tail on unkey', test_tail_and_mic),
    ('TX timeout warning beep', test_tot_warning),
    ('VOX: the chip set, a voice keys A, VOX DELAY holds, quiet unkeys', test_vox),
    ('VOX never keys over a station, out of band, in the menu, locked, or right after a timeout', test_vox_safety),
    ('keys read while PTT or PTT-B is held, no row driven high', test_keys_with_ptt),
    ('DTMF send: keys with PTT and PTT-B, MENU 1-8 a stored code, a blank one nothing', test_dtmf_send),
    ('DTMF CODES: typed, a digit deleted, saved as stock stores them, 7 at most', test_dtmf_codes),
    ('CALL ID: OFF by default, item 20, sends nothing', test_call_id_off),
    ('CALL ID: typed, Morse once in 10 minutes, never on GMRS', test_call_id),
    ('CALL ID: PTT cuts it off, TX TIMEOUT ends it', test_call_id_cut),
    ('CALL ID: still sent after the 10 s clock wraps', test_call_id_wrap),
    ('PTT wakes the backlight', test_ptt_wakes_backlight),
    ('screen dark: the first key only wakes it', test_dark_key_only_wakes),
    ('basics: decimal point, no such channel, empty group, torch when locked', test_basics),
    ('menu times out after 20 s', test_menu_times_out),
    ('REMOTE: screen mirror, keys, status, no PTT', test_remote),
    ('SHIFT AUTO follows the band plan', test_auto_shift),
    ('SHIFT AUTO on fresh settings', test_auto_shift_fresh),
    ('OFFSET is one per band', test_offset_per_band),
    ('settings saved with one OFFSET load right (old OFFSET 0)', partial(test_offset_old_settings, old=0)),
    ('settings saved with one OFFSET load right (old OFFSET 1)', partial(test_offset_old_settings, old=1)),
    ('settings saved with one OFFSET load right (old OFFSET 4)', partial(test_offset_old_settings, old=4)),
    ('settings saved with one OFFSET load right (old OFFSET 6)', partial(test_offset_old_settings, old=6)),
    ('a shift of 10 MHz or more shows in full', test_shift_digits),
    ('OFFSET dials 0.05 to 12.75 MHz and goes round', test_offset_dial),
    ('CH TO VFO brings any 50 kHz offset', partial(test_ch_to_vfo_offset, tx=44610000 - 765000, shift='-', off='7.65')),
    ('CH TO VFO makes any other offset simplex', partial(test_ch_to_vfo_offset, tx=44610000 - 60001, shift='OFF', off='5.00')),
    ('typed digits show on the RX-only build', test_entry_rxonly),
    ('V/M leaves a remotely started PC LINK', test_remote_pc_link_exit),
    ('menu walk: every setting, its text and saved bytes', test_menu_walk),
]


SP_LIMIT = 0xF0                            # deepest stack allowed in a test: 16 bytes spare for untested paths


def one(item):
    name, fn = item
    try:
        cpu = boot()
        fn(cpu)
        assert cpu.sp_max <= SP_LIMIT, 'stack reached 0x%02X (limit 0x%02X)' % (cpu.sp_max, SP_LIMIT)
        return 'ok %s [sp %02X]' % (name, cpu.sp_max)
    except AssertionError as e:
        return 'FAIL %s: %s' % (name, e)


def timed(item):
    import time
    t = time.time()
    return item[0], one(item), time.time() - t


def check_quick_reference():               # every manual page's menu numbers are the firmware's
    src = open(os.path.join(HERE, 'main.c')).read()
    names = re.findall(r'"([^"]+)"', src[src.index('mnames[M_N] = {'):].split('};')[0])
    man = os.path.join(HERE, '..', 'docs', 'manual')
    for page in sorted(glob.glob(os.path.join(man, '*.md'))):
        text = open(page).read()
        refs = re.findall(r'\| (\d+) \| ([A-Z][A-Z ]+?)(?= \(|\s*\|)', text)          # | 23 | TX BANDS |
        refs += [(n, i) for i, n in re.findall(r'([A-Z][A-Z ]*[A-Z])\** \(item (\d+)\)', text)]   # LAST HEARD (item 37)
        refs += [(a + b, i) for a, b, i in re.findall(r'\*\*(\d)\*\* \*\*(\d)\*\*,? (?:\(|to reach \*\*)([A-Z][A-Z ]*[A-Z])', text)]   # **3** **7** (LAST HEARD)
        for n, item in refs:
            assert 0 < int(n) <= len(names) and names[int(n) - 1].startswith(item.strip()), \
                '%s: item %s is %s, not %s' % (os.path.basename(page), n, names[int(n) - 1] if 0 < int(n) <= len(names) else '-', item)

def check_menu_help():                     # fw/help.c is tools/mkhelp.py's output, one line per menu item
    sys.path.insert(0, os.path.join(HERE, '..', 'tools'))
    import mkhelp
    src = open(os.path.join(HERE, 'main.c')).read()
    names = re.findall(r'"([^"]+)"', src[src.index('mnames[M_N] = {'):].split('};')[0])
    assert len(mkhelp.HELP) == len(names), 'mkhelp.py has %d lines for %d menu items' % (len(mkhelp.HELP), len(names))
    got = [int(w, 16) for w in re.findall(r'0x([0-9A-F]{4})', open(os.path.join(HERE, 'help.c')).read())]
    assert got == mkhelp.pack(mkhelp.HELP), 'fw/help.c is stale: run python3 tools/mkhelp.py'

if __name__ == '__main__':
    import json
    import multiprocessing
    check_quick_reference()
    check_menu_help()
    items = FIRST + INDEPENDENT
    if sys.argv[1:]:                       # python3 test_fw.py scan fm: just the tests whose names match
        items = [i for i in items if any(w.lower() in i[0].lower() for w in sys.argv[1:])]
        if not items:
            sys.exit('no test name contains ' + ' or '.join(sys.argv[1:]))
    # Longest first, from the last run's times (a new test counts as longest), so no long test starts last.
    times_file = os.path.join(HERE, 'build', 'test-times')
    try:
        times = json.load(open(times_file))
    except (OSError, ValueError):
        times = {}
    jobs = sorted(items, key=lambda i: -times.get(i[0], 1e9))
    # TEST_JOBS caps the workers where RAM, not cores, is the limit (CI: each PyPy worker is a few hundred MB).
    pool = multiprocessing.Pool(min(len(jobs), int(os.environ.get('TEST_JOBS', 0)) or os.cpu_count() or 4))
    done = {name: (line, t) for name, line, t in pool.imap_unordered(timed, jobs)}
    results = [done[name][0] for name, _ in items]
    print('\n'.join(results))
    times.update((name, round(t, 2)) for name, (_, t) in done.items())
    json.dump({n: times[n] for n, _ in FIRST + INDEPENDENT if n in times}, open(times_file, 'w'), indent=0)
    failed = [r for r in results if r.startswith('FAIL')]
    if sys.argv[1:]:
        sys.exit(1 if failed else 0)
    peak = max(int(r[-3:-1], 16) for r in results if r.startswith('ok ') and r.endswith(']'))
    print('  (stack peak 0x%02X of 0xFF)' % peak)
    open(os.path.join(HERE, 'build', 'stack-peak'), 'w').write('%02X\n' % peak)   # for tools/budget.py
    if failed:
        sys.exit('%d feature tests failed' % len(failed))
    print('all passed')
