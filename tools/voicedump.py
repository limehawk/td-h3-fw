#!/usr/bin/env python3
"""Play every voice chip (U4) prompt over REMOTE, one at a time, while someone listens.

    python3 tools/voicedump.py [FIRST [LAST [SECONDS]]]      # default 0 255 3.5

Needs MENU -> REMOTE ON and a firmware with REMOTE 'V' n (fw/remote.c). Prints each prompt
number as it plays and the radio's answer, so what you hear can be matched to the number.
"""
import os
import sys
import termios
import time

PORT = '/dev/ttyUSB1'


def open_port():
    fd = os.open(PORT, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
    a = termios.tcgetattr(fd)
    a[0] = a[1] = a[3] = 0                              # raw: no input/output/local processing
    a[2] = termios.CS8 | termios.CREAD | termios.CLOCAL
    a[4] = a[5] = termios.B1000000                   # REMOTE (115200 or 38400 on older td-h3-fw)
    termios.tcsetattr(fd, termios.TCSANOW, a)
    return fd


def answer(fd, wait):
    end, got = time.time() + wait, b''
    while time.time() < end:
        try:
            got += os.read(fd, 4096)
        except BlockingIOError:
            time.sleep(0.02)
    i = got.find(b'qVOICE ')
    return got[i + 1:got.index(b'\n', i)].decode() if i >= 0 and b'\n' in got[i:] else None


def main():
    arg = sys.argv[1:] + [None] * 3
    first, last, secs = int(arg[0] or 0), int(arg[1] or 255), float(arg[2] or 3.5)
    fd = open_port()
    for n in range(first, last + 1):
        os.write(fd, b'V'); time.sleep(0.05)            # one byte per command, 20 ms+ apart
        os.write(fd, bytes([n]))
        t = time.time()                                 # to cut a recording of the speaker into prompts
        print('%3d  0x%02X  %.3f  %s' % (n, n, t, answer(fd, secs) or 'no answer'), flush=True)


if __name__ == '__main__':
    main()
