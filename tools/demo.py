#!/usr/bin/env python3
"""The demo GIF for the README and for posting: a short tour of the real firmware in the emulator.

    make -C fw demo          # screenshots first (for the boot frames), then capture + render

capture: boots the full build (fw/build/td-h3.bin), plays the tour below and writes each still to fw/build/demo/
         (frame-NNN.png) with how long it shows (demo.txt). Stills are taken between draws, as the
         radio redraws in ~40 ms but the emulator in about a second of its own clock.
render:  the boot screen from `make screenshots`, the tour, then the boot animation back to the start, at 4x on a dark margin:
         docs/img/demo.gif, and docs/img/demo.mp4 when ffmpeg is there (for sites that want video).
"""
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
RAW = os.path.join(ROOT, 'fw', 'build', 'demo')
SHOTS = os.path.join(ROOT, 'fw', 'build', 'screens')
OUT = os.path.join(ROOT, 'docs', 'img')


def capture():
    sys.path.insert(0, os.path.join(ROOT, 'fw'))
    sys.path.insert(0, HERE)
    import test_fw as t
    from lcdshot import Panel, png
    os.makedirs(RAW, exist_ok=True)
    shown = []

    def shot(cpu, ms):                     # the screen as it is between draws, shown for ms
        cpu.run(3_000_000, until=lambda: cpu.pc == cpu.scan_at)
        name = 'frame-%03d.png' % len(shown)
        open(os.path.join(RAW, name), 'wb').write(png(None, scale=1, fb=cpu.panel.feed(cpu.lcd)))
        shown.append('%s %d' % (name, ms))

    def tap(cpu, key, ms=350):
        t.press(cpu, key)
        shot(cpu, ms)

    class Charged(t.CPU):                  # a charged pack (8.2 V) from power-on, so the first draw says so
        def __init__(self, *a, **k):
            super().__init__(*a, **k)
            self.adc = 3985
    t.CPU = Charged

    def fresh():
        cpu = t.boot(t.FW)
        cpu.scan_at = t.symbol(t.FW, '_key_scan')
        cpu.panel = Panel()
        cpu.run(400_000)
        return cpu

    # Home, then 146.940 typed in and a station coming in, the meter riding it
    cpu = fresh()
    shot(cpu, 1500)
    for d in '146940':
        tap(cpu, d, 300)
    cpu.bk.busy = lambda f: True
    for dbm in (-96, -88, -79, -74, -77, -71, -75, -82, -78, -73):
        cpu.bk.dbm = lambda f, d=dbm: d
        cpu.run(300_000)
        shot(cpu, 180)
    cpu.bk.busy, cpu.bk.dbm = (lambda f: False), (lambda f: -127)
    cpu.run(600_000)
    shot(cpu, 700)

    # Dual watch (hold 9): A listens on its channel while B, in the background, hears a station
    cpu = fresh()
    t.longpress(cpu, '9')
    assert cpu.run(20_000_000, until=lambda: cpu.bk.freq() == 44600000)
    cpu.bk.busy = lambda f: True
    cpu.bk.dbm = lambda f: -82
    cpu.run(2_000_000)
    shot(cpu, 1800)

    # A memory scan stepping through the channels, stopping on one with a station
    cpu = fresh()
    t.menu(cpu, 'GROUP', ['UP', 'EXIT'])
    t.press(cpu, 'EXIT')
    t.press(cpu, '*')
    for _ in range(5):
        cpu.run(250_000)
        shot(cpu, 160)
    hit = cpu.bk.freq()
    cpu.bk.busy = lambda f: f == hit
    cpu.bk.dbm = lambda f: -70 if f == hit else -127
    cpu.run(2_000_000)
    shot(cpu, 1800)

    # The band scope (hold 3): stations coming and going over the noise, the peaks held
    cpu = fresh()
    sweep = [0]

    def band(f):
        i, ch = sweep[0], f // 2500
        if abs(f - 14652000) < 2000: return -72 - (i * 5 % 9)
        if abs(f - 14670000) < 6000 and i % 4 < 3: return -90 - (i * 3 % 7)
        if abs(f - 14600000) < 2000 and i > 3: return -84
        return -124 + (ch * 7 + i * 13) % 9
    cpu.bk.dbm = band
    t.menu(cpu, 'SCOPE', ['UP'])
    for i in range(10):
        sweep[0] = i
        cpu.run(1_400_000)
        shot(cpu, 220)
    shot(cpu, 900)

    # Freq find (hold 7): finds the frequency of a radio keyed right beside it
    cpu = fresh()
    cpu.bk.counter = lambda: 14700000
    t.menu(cpu, 'FF', ['UP'])
    cpu.run(3_000_000)
    shot(cpu, 1600)

    # SSB: upper sideband on 144.200 MHz, tuned in 50 Hz steps
    cpu = fresh()
    t.menu(cpu, 'MODE', ['UP', 'UP', 'EXIT'])
    t.keys(cpu, list('144200'))
    cpu.bk.busy = lambda f: True
    cpu.bk.dbm = lambda f: -105
    for _ in range(4):
        tap(cpu, 'UP', 300)
    shot(cpu, 900)

    # The menu, a few rows down
    cpu = fresh()
    tap(cpu, 'MENU', 700)
    for _ in range(5):
        tap(cpu, 'DOWN', 300)

    # FM broadcast radio
    cpu = fresh()
    cpu.bk.dbm = lambda f: -75 if f // 10000 == 1019 else -120
    t.menu(cpu, 'FM', ['UP'])
    cpu.run(4_000_000)
    shot(cpu, 1800)

    # A weather alert: the 1050 Hz tone on the NOAA channel
    cpu = fresh()
    t.menu(cpu, 'WX', ['UP', 'EXIT'])
    cpu.bk.alert = lambda f: f == 16240000
    cpu.run(9_000_000)
    shot(cpu, 2200)

    # Transmit (the full build) on 446.000, the mic level bar moving and touch-tones going out
    cpu = fresh()
    t.keys(cpu, list('446000'))
    cpu.key = 'PTT'
    for lvl in (0x0300, 0x0700, 0x0500, 0x0900):
        cpu.bk.regs[0x64] = lvl
        cpu.run(1_000_000)
        shot(cpu, 250)
    for d in '146':
        cpu.key = ('PTT', d); cpu.run(400_000)
        cpu.key = 'PTT'; cpu.run(400_000)
        shot(cpu, 350)
    shot(cpu, 1200)
    cpu.key = None

    open(os.path.join(RAW, 'demo.txt'), 'w').write('\n'.join(shown) + '\n')
    print('%d stills in %s' % (len(shown), RAW))


def render():
    from PIL import Image
    k, pad = 4, 16
    size = 128 * k + 2 * pad

    def framed(path):
        img = Image.new('RGB', (size, size), (28, 28, 28))
        img.paste(Image.open(path).convert('RGB').resize((128 * k, 128 * k), Image.NEAREST), (pad, pad))
        return img

    # It opens on the boot screen's finished still (the first frame is the preview chats and feeds
    # show), tours, then builds the tower back up to that still: the loop has no seam.
    boot = [int(v) for v in open(os.path.join(SHOTS, 'boot.txt')).read().split()]
    still = framed(os.path.join(SHOTS, 'boot-%02d.png' % (len(boot) - 1)))
    frames, ms = [still], [1500]
    for line in open(os.path.join(RAW, 'demo.txt')):
        name, d = line.split()
        frames.append(framed(os.path.join(RAW, name)))
        ms.append(int(d))
    frames += [framed(os.path.join(SHOTS, 'boot-%02d.png' % i)) for i in range(len(boot) - 1)]
    ms += boot[:-1]
    os.makedirs(OUT, exist_ok=True)
    gif = os.path.join(OUT, 'demo.gif')
    frames[0].save(gif, save_all=True, append_images=frames[1:], duration=ms, loop=0, optimize=True)
    print('docs/img/demo.gif  %d frames, %.1f s, %d KB' % (len(frames), sum(ms) / 1000, os.path.getsize(gif) // 1024))
    try:                                   # video for sites that turn GIFs into it anyway
        subprocess.run(['ffmpeg', '-y', '-loglevel', 'error', '-i', gif, '-movflags', 'faststart', '-pix_fmt', 'yuv420p',
                        '-vf', 'fps=30', os.path.join(OUT, 'demo.mp4')], check=True)
        print('docs/img/demo.mp4  %d KB' % (os.path.getsize(os.path.join(OUT, 'demo.mp4')) // 1024))
    except (OSError, subprocess.CalledProcessError) as e:
        print('no demo.mp4 (%s)' % e)


if __name__ == '__main__':
    {'capture': capture, 'render': render}[sys.argv[1]]()
