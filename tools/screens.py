#!/usr/bin/env python3
"""Screenshots for the manual, rendered from the real firmware in the emulator.

    make -C fw screenshots          # capture (PyPy if uv has it) + annotate (needs Pillow)

capture: boots fw/build/td-h3-rx.bin (and td-h3.bin for the transmit screen), drives it to each
         screen and writes plain PNGs to fw/build/screens/, and every frame of the boot animation.
annotate: adds numbered callouts and writes docs/manual/img/<name>.png at 3x. The numbers match
         the legends in docs/manual/*.md: change one, change the other. The boot frames become
         docs/manual/img/boot.gif, framed like the stills.
terms:   runs the real tdh3 in a pseudo-terminal and draws what it prints as docs/manual/img/term-*.png.
"""
import os
import random
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
RAW = os.path.join(ROOT, 'fw', 'build', 'screens')
OUT = os.path.join(ROOT, 'docs', 'manual', 'img')

# name: [(number, x, y), ...] in the radio's 128 x 128 pixels: where each callout points
CALLOUTS = {
    'home': [(1, 3, 17), (2, 64, 17), (3, 118, 17), (4, 75, 30), (5, 5, 44), (6, 115, 44), (7, 15, 53),
             (8, 118, 53), (9, 30, 66), (10, 108, 66), (11, 75, 88), (12, 15, 4), (13, 118, 4), (14, 18, 17), (15, 58, 66)],
    'typing': [(1, 30, 123)],
    'scanning': [(1, 15, 4), (2, 75, 30)],
    'menu': [(1, 12, 3), (2, 110, 3), (3, 60, 18), (4, 30, 33), (5, 112, 33), (6, 30, 63), (7, 127, 16), (8, 70, 111), (9, 40, 123)],
    'fm': [(1, 20, 3), (2, 40, 20), (3, 60, 42), (4, 70, 57), (5, 92, 62), (6, 30, 80), (7, 100, 115), (8, 108, 15)],
    'scope': [(1, 80, 3), (2, 108, 42), (3, 76, 86), (4, 60, 111)],
    'freqfind': [(1, 40, 38), (2, 50, 123)],
    'pclink': [(1, 40, 48)],
    'locked': [(1, 80, 4)],
    'usb': [(1, 30, 44), (2, 114, 35), (3, 112, 53)],
    'tx': [(1, 75, 30), (2, 20, 66), (3, 112, 66), (4, 60, 52), (5, 40, 123)],
    'dtmfcodes': [(1, 30, 17), (2, 30, 29), (3, 60, 111)],
    'alert': [(1, 50, 30)],
}

# Framed the same way, with no numbers: the page's own words say what to look at.
PLAIN = ['memory', 'menuedit', 'menudigit', 'saveask', 'replace', 'deletelist', 'deleteask', 'chgroups', 'glink',
         'chname', 'weather', 'lastheard', 'flashlight', 'remote', 'version', 'notallowed', 'repeater', 'dtmfask',
         'ptthold', 'vox', 'fmauto', 'callid']
THEMES = 9          # MENU, THEME: the nine in rows of three make themes.png
DIGITS = 3          # MENU, DIGITS: ARCADE, LCD and CLASSIC side by side make digits.png


def capture():
    sys.path.insert(0, os.path.join(ROOT, 'fw'))
    sys.path.insert(0, HERE)
    import test_fw as t
    from lcdshot import png
    os.makedirs(RAW, exist_ok=True)

    def save(cpu, name, image=None):
        # wait for the next key scan: the firmware is between draws, so no half-drawn frequency
        at = t.symbol(image, '_key_scan') if image else scan_at
        cpu.run(3_000_000, until=lambda: cpu.pc == at)
        open(os.path.join(RAW, name + '.png'), 'wb').write(png(cpu.lcd, scale=1))
    scan_at = t.symbol(t.FW_RX, '_key_scan')

    ee = t.eeprom()
    open(os.path.join(RAW, 'my-radio.bin'), 'wb').write(b'P31183\0\0' + ee)   # made-up channels, for the tdh3 pictures
    for n, f in ((1, 1019), (2, 887), (3, 1043), (5, 975)):    # FM presets for the FM screen
        ee[0x0CD0 + 4 * (n - 1):0x0CD0 + 4 * n] = bytes([f % 10 | (f // 10 % 10) << 4, f // 100 % 10 | (f // 1000 % 10) << 4, 0, 0])

    # The boot animation: a frame at each delay_ms() in splash(), and the still it ends on. A frame
    # lasts its delay plus the drawing before it (the radio runs ~15 instructions a microsecond).
    cpu = t.CPU(open(t.FW_RX, 'rb').read(), bytearray(ee))
    dl, bk, lit = t.symbol(t.FW_RX, '_delay_ms'), t.symbol(t.FW_RX, '_bk_init'), t.symbol(t.FW_RX, '_backlight')
    cpu.run(5_000_000, until=lambda: cpu.pc == lit)   # lit just before splash()
    ms, last, wait = [], cpu.icount, 0
    while True:
        cpu.run(30_000_000, until=lambda: cpu.pc in (dl, bk))
        draw = (cpu.icount - last - wait * 800) / 15000   # less the last delay's own ~800 instructions a ms
        open(os.path.join(RAW, 'boot-%02d.png' % len(ms)), 'wb').write(png(cpu.lcd, scale=1))
        if cpu.pc == bk:
            break
        wait = cpu.sfr[0x82] | cpu.sfr[0x83] << 8      # delay_ms's argument, in DPL/DPH
        ms.append(round(draw) + wait)
        last = cpu.icount
        cpu.step()
    open(os.path.join(RAW, 'boot.txt'), 'w').write(' '.join(map(str, ms + [2500])))

    def fresh():
        cpu = t.boot(t.FW_RX, ee=bytearray(ee))
        cpu.adc = 3985                     # a charged pack, 8.2 V (default reads as nearly flat)
        cpu.run(400_000)
        return cpu

    # Wait for a state, not an instruction count: a count catches dual watch or a scan wherever
    # the code's speed has got it, so a smaller build would move the picture.
    def on(cpu, f):                        # until the receiver sits on f (10 Hz units)
        assert cpu.run(20_000_000, until=lambda: cpu.bk.freq() == f), 'never tuned to %d' % f

    cpu = fresh()                          # home: A on channel 1, dual watch on, a station heard on B (VFO)
    t.keys(cpu, ['EXIT'])
    cpu.bk.busy = lambda f: True           # A heard 12 minutes ago: its age after the letter
    cpu.run(1_500_000)
    cpu.bk.busy = None
    cpu.run(1_000_000)
    lh, up10 = t.static_at(t.FW_RX, 'lh'), t.static_at(t.FW_RX, 'up10')
    t.poke16(cpu, lh + 4, t.peek16(cpu, up10) - 6 * 12)
    t.longpress(cpu, '9')
    on(cpu, 44600000)                      # the station starts while dual watch looks at B
    cpu.bk.busy = lambda f: True
    cpu.bk.dbm = lambda f: -82
    cpu.run(2_000_000)
    rx_min, rx_sec = t.symbol(t.FW_RX, '_rx_min'), t.symbol(t.FW_RX, '_rx_sec')
    cpu.xram[rx_min], cpu.xram[rx_sec] = 0, 42   # talking 42 s: the receive timer
    cpu.run(400_000)
    save(cpu, 'home')

    cpu = fresh()
    t.keys(cpu, ['1', '4', '6', '5'])
    save(cpu, 'typing')

    cpu = fresh()
    t.menu(cpu, 'GROUP', ['UP', 'EXIT'])
    t.keys(cpu, ['EXIT', '*'])
    cpu.run(2_000_000)
    on(cpu, 14652000)                      # the scan on M1
    save(cpu, 'scanning')

    cpu = fresh()
    t.keys(cpu, ['MENU'])
    cpu.run(2_000_000)                     # let the whole list draw
    save(cpu, 'menu')

    cpu = fresh()
    cpu.bk.dbm = lambda f: -75 if f // 10000 == 1019 else -120
    t.menu(cpu, 'FM', ['UP'])
    cpu.run(4_000_000)
    save(cpu, 'fm')

    # The scope: a strong station under the cursor, a medium and a weak one, one that came and went
    # (its streak in the waterfall, its peak tick left over), and the band's noise
    cpu = fresh()
    looks = [0]

    def band(f):
        p = (f - 14572000) // 2500          # the point, 0-63
        looks[0] += p == 0                  # point 0 is read twice a sweep
        s = looks[0] // 2
        if p == 32: return -68
        if p == 50: return -104
        if p == 17: return -115
        if p == 26 and 25 <= s < 34: return -86
        return random.Random(p * 1000 + s).randint(-126, -122) + p // 11   # rising 5 dB across, as a real band's
    cpu.bk.dbm = band
    t.scope_chip(cpu)
    t.menu(cpu, 'SCOPE', ['UP'])
    cpu.run(66_000_000)                     # the blank line low in the waterfall, the station's streak over it
    save(cpu, 'scope')

    cpu = fresh()
    cpu.bk.counter = lambda: 14700000
    t.menu(cpu, 'FF', ['UP'])
    cpu.run(3_000_000)
    save(cpu, 'freqfind')

    cpu = fresh()
    t.menu(cpu, 'PC', [])
    t.press(cpu, 'MENU')
    cpu.run(500_000)
    save(cpu, 'pclink')
    cpu.key = 'EXIT'; cpu.run(400_000); cpu.key = None

    cpu = fresh()
    t.longpress(cpu, '*')
    save(cpu, 'locked')

    cpu = fresh()                          # USB on 144.200 25 (MODE, then UP five 50 Hz steps), a weak station
    t.menu(cpu, 'MODE', ['UP', 'UP', 'EXIT'])
    t.keys(cpu, list('144200') + ['UP'] * 5)
    cpu.bk.busy = lambda f: True
    cpu.bk.dbm = lambda f: -105
    cpu.run(2_000_000)
    save(cpu, 'usb')

    cpu = t.boot(t.FW, ee=bytearray(ee))   # the transmit build, keyed on 446.000
    cpu.adc = 3985
    t.keys(cpu, ['4', '4', '6', '0', '0', '0'])
    cpu.bk.regs[0x64] = 0x0600             # a voice on the mic, for the level bar
    cpu.key = 'PTT'
    cpu.run(4_000_000)
    for d in '146':                        # touch-tones sent with PTT held: on the bottom line
        cpu.key = ('PTT', d); cpu.run(400_000)
        cpu.key = 'PTT'; cpu.run(400_000)
    save(cpu, 'tx')
    cpu.key = None

    ee2 = bytearray(ee)                    # two stored DTMF codes, as CHIRP or DTMF CODES writes them
    ee2[0x1830:0x1837] = bytes([14, 7, 3, 0xFF, 0xFF, 0xFF, 0xFF])
    ee2[0x1850:0x1857] = bytes([2, 7, 1, 8, 2, 8, 0xFF])
    cpu = t.boot(t.FW_RX, ee=ee2)
    cpu.adc = 3985
    t.menu(cpu, 'DTMF', ['UP'])
    cpu.run(1_000_000)
    save(cpu, 'dtmfcodes')

    cpu = fresh()
    t.menu(cpu, 'WX', ['UP', 'EXIT'])
    cpu.bk.alert = lambda f: f == 16240000  # the 1050 Hz tone on the weather channel (162.400)
    cpu.run(9_000_000)
    save(cpu, 'alert')

    def full():                            # the transmit build, for what only it shows
        cpu = t.boot(t.FW, ee=bytearray(ee))
        cpu.adc = 3985
        cpu.run(400_000)
        return cpu

    def memory():                          # the selected slot on a memory channel
        cpu = fresh()
        t.keys(cpu, ['EXIT'])
        return cpu

    def opened(name, item):                # a list or tool from the menu, on a memory channel
        cpu = memory()
        t.menu(cpu, item, ['UP'])
        cpu.run(1_000_000)
        save(cpu, name)
        return cpu

    def editing(name, item, *more):        # a setting being changed: its row is white
        cpu = fresh()
        t.menu(cpu, item)
        t.keys(cpu, ['MENU', *more])
        save(cpu, name)

    cpu = memory()                         # channel 4 has its own tones
    t.keys(cpu, ['0', '0', '4'])
    cpu.run(1_000_000)
    save(cpu, 'memory')

    editing('menuedit', 'TXB')
    editing('vox', 'VOX', 'UP')

    cpu = fresh()
    t.keys(cpu, ['MENU'])
    cpu.run(1_000_000)
    t.press(cpu, '2')
    save(cpu, 'menudigit')

    for n in range(THEMES):
        cpu = memory()
        if n:
            t.menu(cpu, 'THEME', ['UP'] * n + ['EXIT'])
        cpu.run(2_000_000)
        save(cpu, 'theme%d' % n)

    for n in range(DIGITS):                # GMRS channel 1: 462.5625, a small 50
        cpu = memory()
        t.keys(cpu, ['0', '0', '2'])
        if n:
            t.menu(cpu, 'DIGITS', ['UP'] * n + ['EXIT'])
        cpu.run(2_000_000)
        save(cpu, 'digits%d' % n)

    cpu = fresh()
    t.longpress(cpu, 'MENU')
    save(cpu, 'saveask')
    t.keys(cpu, ['0', '0', '3', 'MENU'])
    cpu.run(600_000)
    save(cpu, 'replace')

    cpu = opened('deletelist', 'DEL')
    t.keys(cpu, ['DOWN', 'MENU'])
    cpu.run(600_000)
    save(cpu, 'deleteask')
    opened('chgroups', 'GRP')
    opened('glink', 'GLINK')
    opened('chname', 'NAME')

    cpu = fresh()
    t.longpress(cpu, '#')
    cpu.run(8_000_000)
    save(cpu, 'weather')

    cpu = full()                           # a scan that found two stations, stopped: LAST HEARD opens
    t.scan_hits(cpu, True, 2)
    t.stop_scan(cpu)
    cpu.run(1_500_000)
    save(cpu, 'lastheard', t.FW)

    cpu = fresh()
    t.press(cpu, 'SIDE2', hold=700_000)    # held from off: moonlight
    cpu.run(1_000_000)
    save(cpu, 'flashlight')

    cpu = fresh()
    t.menu(cpu, 'REMOTE', ['UP'])
    cpu.run(1_000_000)
    save(cpu, 'remote')

    cpu = fresh()
    t.menu(cpu, 'VER')
    cpu.run(1_000_000)
    save(cpu, 'version')

    cpu = full()                           # PTT on a frequency no service in TX BANDS covers
    t.keys(cpu, list('155000'))
    cpu.key = 'PTT'
    cpu.run(2_000_000)
    save(cpu, 'notallowed', t.FW)
    cpu.key = None

    cpu = full()                           # SHIFT AUTO on a repeater output
    t.menu(cpu, 'SHIFT', ['UP'] * 3 + ['EXIT'])
    t.keys(cpu, list('146940'))
    cpu.run(1_000_000)
    save(cpu, 'repeater', t.FW)

    cpu = full()                           # holding PTT, MENU: which stored code?
    t.keys(cpu, list('446000'))
    cpu.key = 'PTT'
    cpu.run(3_000_000)
    cpu.key = ('PTT', 'MENU')
    cpu.run(400_000)
    cpu.key = 'PTT'
    cpu.run(400_000)
    save(cpu, 'dtmfask', t.FW)
    cpu.key = None

    cpu = full()                           # PTT let go with CALL ID on: the callsign going out in Morse
    cpu.ee.mem[0x1E00:0x1E08] = b'N0CALL\0\0'   # the usual made-up callsign
    cpu.key = 'PTT'
    cpu.run(1_500_000)
    cpu.key = None
    cpu.run(1_500_000)
    save(cpu, 'callid', t.FW)

    cpu = t.CPU(open(t.FW, 'rb').read(), bytearray(ee))   # switched on with PTT already down
    cpu.adc, cpu.key, cpu.key_held_at = 3985, 'PTT', t.symbol(t.FW, '_key_held')
    cpu.run(16_000_000)
    save(cpu, 'ptthold', t.FW)

    cpu = fresh()
    t.menu(cpu, 'FM', ['UP'])
    cpu.run(4_000_000)
    t.longpress(cpu, '0')
    cpu.run(600_000)
    save(cpu, 'fmauto')


def annotate():
    """Numbers sit in the margins level with their row; nothing is drawn over the screen itself.
    Items sharing a row on one side take two columns, in the same left-to-right order."""
    from PIL import Image, ImageDraw, ImageFont
    os.makedirs(OUT, exist_ok=True)
    k, m, pad, r, col = 3, 62, 10, 11, 26
    try:
        font = ImageFont.truetype('DejaVuSans-Bold.ttf', 14)
    except OSError:
        font = ImageFont.load_default()
    amber, black = (255, 176, 0), (0, 0, 0)
    for name in PLAIN:
        src = Image.open(os.path.join(RAW, name + '.png')).convert('RGB')
        img = Image.new('RGB', (128 * k + 2 * pad, 128 * k + 2 * pad), (28, 28, 28))
        img.paste(src.resize((128 * k, 128 * k), Image.NEAREST), (pad, pad))
        img.save(os.path.join(OUT, name + '.png'), optimize=True)
        print('docs/manual/img/%s.png' % name)
    img = Image.new('RGB', (3 * (128 * 2 + pad) + pad, (THEMES + 2) // 3 * (128 * 2 + pad) + pad), (28, 28, 28))
    for n in range(THEMES):
        src = Image.open(os.path.join(RAW, 'theme%d.png' % n)).convert('RGB')
        img.paste(src.resize((128 * 2, 128 * 2), Image.NEAREST), (pad + n % 3 * (128 * 2 + pad), pad + n // 3 * (128 * 2 + pad)))
    img.save(os.path.join(OUT, 'themes.png'), optimize=True)
    print('docs/manual/img/themes.png')
    img = Image.new('RGB', (DIGITS * (128 * 2 + pad) + pad, 128 * 2 + 2 * pad), (28, 28, 28))
    for n in range(DIGITS):
        src = Image.open(os.path.join(RAW, 'digits%d.png' % n)).convert('RGB')
        img.paste(src.resize((128 * 2, 128 * 2), Image.NEAREST), (pad + n * (128 * 2 + pad), pad))
    img.save(os.path.join(OUT, 'digits.png'), optimize=True)
    print('docs/manual/img/digits.png')
    for name, marks in CALLOUTS.items():
        src = Image.open(os.path.join(RAW, name + '.png')).convert('RGB')
        img = Image.new('RGB', (128 * k + 2 * m, 128 * k + 2 * pad), (28, 28, 28))
        img.paste(src.resize((128 * k, 128 * k), Image.NEAREST), (m, pad))
        d = ImageDraw.Draw(img)
        rows = {}
        for n, x, y in marks:
            rows.setdefault((x >= 64, y), []).append((x, n))
        for (right, y), items in rows.items():
            items.sort(reverse=right)               # nearest the screen edge first
            cy = pad + y * k
            edge = m + 128 * k if right else m
            for i, (x, n) in enumerate(items):
                cx = edge + (1 if right else -1) * (r + 6 + i * col)
                if i == 0:
                    d.line((cx + (-r if right else r), cy, edge + (2 if right else -2), cy), fill=amber, width=2)
                d.ellipse((cx - r, cy - r, cx + r, cy + r), fill=amber, outline=black, width=2)
                d.text((cx, cy), str(n), fill=black, font=font, anchor='mm')
        img.save(os.path.join(OUT, name + '.png'), optimize=True)
        print('docs/manual/img/%s.png' % name)
    ms = [int(v) for v in open(os.path.join(RAW, 'boot.txt')).read().split()]
    frames = []
    for i in range(len(ms)):
        img = Image.new('RGB', (128 * k + 2 * m, 128 * k + 2 * pad), (28, 28, 28))
        img.paste(Image.open(os.path.join(RAW, 'boot-%02d.png' % i)).convert('RGB').resize((128 * k, 128 * k), Image.NEAREST), (m, pad))
        frames.append(img)
    frames[0].save(os.path.join(OUT, 'boot.gif'), save_all=True, append_images=frames[1:], duration=ms, loop=0)
    print('docs/manual/img/boot.gif')


def terms():
    """tdh3's own output, from a real run in a pseudo-terminal, drawn cell by cell like a terminal.
    The flash picture needs a radio, so a test draws its finished screen with the flasher's own code."""
    import fcntl
    import pty
    import re
    import struct
    import subprocess
    import termios
    from PIL import Image, ImageDraw, ImageFont
    BLACK, WHITE = (28, 28, 28), (220, 220, 220)
    BASIC = [(0, 0, 0), (205, 49, 49), (13, 188, 121), (229, 229, 16), (36, 114, 200), (188, 63, 188), (17, 168, 205), (229, 229, 229)]
    FILE = subprocess.run(['fc-match', '-f', '%{file}', 'monospace:spacing=mono'], capture_output=True, text=True).stdout
    font = ImageFont.truetype(FILE, 16)
    w, h, pad = round(font.getlength('M')), 20, 12

    def run(*args, cols=100):
        master, slave = pty.openpty()
        fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack('HHHH', 60, cols, 0, 0))
        env = dict(os.environ, TERM='xterm-256color', COLORTERM='truecolor')
        p = subprocess.Popen((os.path.join(ROOT, 'fw', 'build', 'tdh3-shots'),) + args, stdin=slave, stdout=slave, stderr=slave, env=env, cwd=RAW)
        os.close(slave)
        got = b''
        while True:
            try:
                more = os.read(master, 65536)
            except OSError:
                break
            if not more:
                break
            got += more
        p.wait()
        return got.decode()

    def color(v):
        if v[0] == 5:
            n = v[1]
            if n < 8:
                return BASIC[n]
            if n < 16:
                return tuple(min(255, c + 50) for c in BASIC[n - 8])
            if n >= 232:
                return ((n - 232) * 10 + 8,) * 3
            n -= 16
            return tuple(0 if d == 0 else d * 40 + 55 for d in (n // 36, n // 6 % 6, n % 6))
        return tuple(v[1:4])

    def cells(text):
        rows, x, fg, bg, bold = [{}], 0, WHITE, BLACK, False
        for m in re.finditer(r'\x1b\[([0-9;:?]*)([A-Za-z])|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)|\x1b.|(.)', text, re.S):
            ch = m.group(3)
            if ch == '\n':
                rows.append({})
                x = 0
            elif ch == '\r':
                x = 0
            elif ch is not None and ch >= ' ':
                rows[-1][x] = (ch, fg, bg, bold)
                x += 1
            elif m.group(2) == 'm':
                v = [int(a) for a in re.split('[;:]', m.group(1)) if a] or [0]
                while v:
                    a = v.pop(0)
                    if a == 0:
                        fg, bg, bold = WHITE, BLACK, False
                    elif a == 1:
                        bold = True
                    elif a == 22:
                        bold = False
                    elif 30 <= a <= 37:
                        fg = BASIC[a - 30]
                    elif a == 39:
                        fg = WHITE
                    elif 40 <= a <= 47:
                        bg = BASIC[a - 40]
                    elif a == 49:
                        bg = BLACK
                    elif 90 <= a <= 97:
                        fg = tuple(min(255, c + 50) for c in BASIC[a - 90])
                    elif a in (38, 48):
                        n = 2 if v[0] == 5 else 4
                        c, v = color(v[:n]), v[n:]
                        if a == 38:
                            fg = c
                        else:
                            bg = c
        while rows and not rows[-1]:
            rows.pop()
        return rows

    def glyph(d, x, y, ch, fg):            # the block and dot characters, drawn so any font will do
        o = ord(ch)
        if 0x2800 <= o <= 0x28FF:
            for bit, (cx, cy) in enumerate([(0, 0), (0, 1), (0, 2), (1, 0), (1, 1), (1, 2), (0, 3), (1, 3)]):
                if o >> bit & 1:
                    px, py = x + 1 + cx * w // 2, y + 1 + cy * h // 4
                    d.rectangle((px, py, px + 2, py + 2), fill=fg)
        elif ch == '█':
            d.rectangle((x, y, x + w - 1, y + h - 1), fill=fg)
        elif ch == '▀':
            d.rectangle((x, y, x + w - 1, y + h // 2 - 1), fill=fg)
        elif ch == '▄':
            d.rectangle((x, y + h // 2, x + w - 1, y + h - 1), fill=fg)
        else:
            return False
        return True

    def draw(name, typed, text):
        rows = ([{i: (c, (150, 150, 150), BLACK, False) for i, c in enumerate('$ ' + typed)}, {}] if typed else []) + cells(text)
        cols = max(max(r, default=0) for r in rows) + 1
        img = Image.new('RGB', (cols * w + 2 * pad, len(rows) * h + 2 * pad), BLACK)
        d = ImageDraw.Draw(img)
        for j, r in enumerate(rows):
            for i, (ch, fg, bg, bold) in r.items():
                x, y = pad + i * w, pad + j * h
                if bg != BLACK:
                    d.rectangle((x, y, x + w - 1, y + h - 1), fill=bg)
                if ch != ' ' and not glyph(d, x, y, ch, fg):
                    d.text((x, y + 1), ch, fill=fg, font=font)
        img.save(os.path.join(OUT, name + '.png'), optimize=True)
        print('docs/manual/img/%s.png' % name)

    draw('term-help', 'tdh3 --help', run('--help'))
    draw('term-channels', 'tdh3 channels list --file my-radio.bin', run('channels', 'list', '--file', 'my-radio.bin'))
    draw('term-set', 'tdh3 channels set --help', run('channels', 'set', '--help'))
    draw('term-flash', 'tdh3 flash', open(os.path.join(RAW, 'flash.ans'), encoding='utf-8', newline='').read())


if __name__ == '__main__':
    {'capture': capture, 'annotate': annotate, 'terms': terms}[sys.argv[1]]()
