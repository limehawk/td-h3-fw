#!/usr/bin/env python3
"""Screenshots for the manual, rendered from the real firmware in the emulator.

    make -C fw screenshots          # capture (PyPy if uv has it) + annotate (needs Pillow)

capture: boots fw/build/td-h3-rx.bin (and td-h3.bin for the transmit screen), drives it to each
         screen and writes plain PNGs to fw/build/screens/, and every frame of the boot animation.
annotate: adds numbered callouts and writes docs/manual/img/<name>.png at 3x. The numbers match
         the legends in docs/manual/*.md: change one, change the other. The boot frames become
         docs/manual/img/boot.gif, framed like the stills.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
RAW = os.path.join(ROOT, 'fw', 'build', 'screens')
OUT = os.path.join(ROOT, 'docs', 'manual', 'img')

# name: [(number, x, y), ...] in the radio's 128 x 128 pixels: where each callout points
CALLOUTS = {
    'home': [(1, 3, 17), (2, 64, 17), (3, 10, 27), (4, 75, 30), (5, 5, 44), (6, 115, 44), (7, 15, 53),
             (8, 118, 53), (9, 50, 66), (10, 108, 66), (11, 75, 88), (12, 15, 4), (13, 118, 4)],
    'typing': [(1, 30, 123)],
    'scanning': [(1, 15, 4), (2, 75, 30)],
    'menu': [(1, 12, 3), (2, 110, 3), (3, 60, 18), (4, 30, 33), (5, 112, 33), (6, 30, 63), (7, 127, 16), (8, 70, 111), (9, 40, 123)],
    'fm': [(1, 20, 3), (2, 40, 20), (3, 60, 42), (4, 70, 57), (5, 92, 62), (6, 30, 80), (7, 100, 115), (8, 108, 15)],
    'scope': [(1, 20, 3), (2, 66, 60), (3, 50, 110)],
    'freqfind': [(1, 40, 38), (2, 50, 123)],
    'pclink': [(1, 40, 48)],
    'locked': [(1, 80, 4)],
    'usb': [(1, 30, 44), (2, 121, 33), (3, 112, 53)],
    'tx': [(1, 75, 30), (2, 20, 66), (3, 112, 66), (4, 60, 52), (5, 40, 123)],
    'dtmfcodes': [(1, 30, 17), (2, 30, 29), (3, 60, 111)],
    'alert': [(1, 50, 30)],
}


def capture():
    sys.path.insert(0, os.path.join(ROOT, 'fw'))
    sys.path.insert(0, HERE)
    import test_fw as t
    from lcdshot import png
    os.makedirs(RAW, exist_ok=True)

    def save(cpu, name):
        # wait for the next key scan: the firmware is between draws, so no half-drawn frequency
        cpu.run(3_000_000, until=lambda: cpu.pc == scan_at)
        open(os.path.join(RAW, name + '.png'), 'wb').write(png(cpu.lcd, scale=1))
    scan_at = t.symbol(t.FW_RX, '_key_scan')

    ee = t.eeprom()
    for n, f in ((1, 1019), (2, 887), (3, 1043), (5, 975)):    # FM presets for the FM screen
        ee[0x0CD0 + 4 * (n - 1):0x0CD0 + 4 * n] = bytes([f % 10 | (f // 10 % 10) << 4, f // 100 % 10 | (f // 1000 % 10) << 4, 0, 0])

    # The boot animation: a frame at each delay_ms() in splash(), and the still it ends on. A frame
    # lasts its delay plus the drawing before it (the radio runs ~15 instructions a microsecond).
    cpu = t.CPU(open(t.FW_RX, 'rb').read(), bytearray(ee))
    dl, bk = t.symbol(t.FW_RX, '_delay_ms'), t.symbol(t.FW_RX, '_bk_init')
    cpu.run(5_000_000, until=lambda: cpu.pc == t.symbol(t.FW_RX, '_backlight'))   # lit just before splash()
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
    t.longpress(cpu, '9')
    on(cpu, 44600000)                      # the station starts while dual watch looks at B
    cpu.bk.busy = lambda f: True
    cpu.bk.dbm = lambda f: -82
    cpu.run(2_000_000)
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

    cpu = fresh()
    cpu.bk.dbm = lambda f: -70 if abs(f - 14652000) < 2000 else -95 if abs(f - 14670000) < 6000 else -127
    t.menu(cpu, 'SCOPE', ['UP'])
    cpu.run(14_000_000)
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


if __name__ == '__main__':
    {'capture': capture, 'annotate': annotate}[sys.argv[1]]()
