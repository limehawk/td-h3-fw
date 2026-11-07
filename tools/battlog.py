#!/usr/bin/env python3
"""Log the radio's battery over REMOTE (MENU -> REMOTE ON) to measure idle drain overnight.

    python3 tools/battlog.py [CSV [SAMPLE_S [ROW_MIN]]]    # default /tmp/td-h3-battlog-<date>.csv 60 5

Every SAMPLE_S seconds it sends 'Q' and reads the status line (frequency, dBm, squelch O/C,
speaker S/-, battery mV, version). Every ROW_MIN minutes it writes one CSV row: the median mV,
how much of the time the squelch was open and the speaker on, and the drop in mV per hour since
the start. Ctrl-C stops it. A 'Q' doesn't light the screen or press anything; each one opens
REMOTE's 10 s screen mirror, so keep SAMPLE_S well over 10 to leave the radio as it idles.

The USB-C cable may charge the pack: if the mV climbs, it is charging and the run measures
nothing. Use a data-only cable (no VBUS) or a laptop on battery with charging off. The cable and
PC also put noise on the receiver (DEVELOPING.md), which can open the squelch: the open column
shows whether it did.
"""
import os
import statistics
import sys
import termios
import time

PORT = os.environ.get('TDH3_PORT') or '/dev/serial/by-id/usb-1a86_USB_Serial-if00-port0'


def open_port():
    fd = os.open(PORT, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
    a = termios.tcgetattr(fd)
    a[0] = a[1] = a[3] = 0                              # raw: no input/output/local processing
    a[2] = termios.CS8 | termios.CREAD | termios.CLOCAL
    a[4] = a[5] = getattr(termios, "B1000000", 0o010010)  # REMOTE (115200 or 38400 on older td-h3-fw)
    termios.tcsetattr(fd, termios.TCSANOW, a)
    return fd


def status(fd):                             # ['146.520', '-112', 'C', '-', '7400', '0.28.0'] or None
    for _ in range(3):                      # the radio drops a byte when busy: ask again
        try:
            os.read(fd, 65536)              # what the mirror sent since last time
        except BlockingIOError:
            pass
        os.write(fd, b'Q')
        end, got = time.time() + 1.5, b''
        while time.time() < end:
            try:
                got += os.read(fd, 4096)
            except BlockingIOError:
                time.sleep(0.02)
            f = parse(got)
            if f:
                return f
    return None


def parse(got):                             # the last 'q' line that parses (mirror bytes hold 'q's too)
    i = got.rfind(b'q')
    while i >= 0:
        j = got.find(b'\n', i)
        f = got[i + 1:j].decode('ascii', 'replace').split() if j > 0 else []
        if len(f) >= 5 and f[2] in ('O', 'C') and f[4].isdigit():
            return f
        i = got.rfind(b'q', 0, i)
    return None


def main():
    arg = sys.argv[1:] + [None] * 3
    path = arg[0] or time.strftime('/tmp/td-h3-battlog-%Y%m%d-%H%M.csv')
    sample, row = float(arg[1] or 60), float(arg[2] or 5) * 60
    fd = open_port()
    new = not os.path.exists(path)
    out = open(path, 'a')
    if new:
        out.write('time,hours,mv,open_pct,speaker_pct,mv_per_hour,samples,missed\n')
    start, mv0, h0 = time.time(), None, 0
    mvs, opens, spk, missed, t_row = [], 0, 0, 0, time.time()
    print('logging to %s every %g s, a row every %g min' % (path, sample, row / 60), flush=True)
    while True:
        f = status(fd)
        if f:
            mvs.append(int(f[4])); opens += f[2] == 'O'; spk += f[3] == 'S'
        else:
            missed += 1                     # no answer: FM screen, menu open, cable out
        if time.time() - t_row >= row and mvs:
            mv, h = statistics.median(mvs), (time.time() - start) / 3600
            if mv0 is None:
                mv0, h0 = mv, h
            rate = (mv0 - mv) / (h - h0) if h - h0 > 0.1 else 0
            n = len(mvs)
            out.write('%s,%.2f,%d,%.0f,%.0f,%.0f,%d,%d\n' % (time.strftime('%H:%M:%S'), h, mv,
                      100 * opens / n, 100 * spk / n, rate, n, missed))
            out.flush()
            print('%s  %5.2f h  %4d mV  open %3.0f%%  speaker %3.0f%%  %+5.0f mV/h' % (
                time.strftime('%H:%M'), h, mv, 100 * opens / n, 100 * spk / n, -rate), flush=True)
            if rate < -5 and h - h0 > 0.5:
                print('  the pack is gaining: the cable is charging it, this run measures nothing', flush=True)
            mvs, opens, spk, missed, t_row = [], 0, 0, 0, time.time()
        time.sleep(sample)


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        pass
