#!/usr/bin/env python3
"""The emulated radio on a fake serial port, REMOTE already ON, so tdh3 can drive it with no radio.

    pypy3 tools/emuport.py [fw/build/td-h3.bin]        # prints the port, e.g. /dev/pts/7
    tools/tdh3/tdh3 -p /dev/pts/7 remote screen

The port's speed is the one tdh3 sets: as on the cable, the radio only hears it at its own.
The firmware runs in tools/emu51.py at the radio's own speed (one 5 ms tick per 4000
instructions) with fw/test_fw.py's test EEPROM. Nothing is saved: every start is a fresh radio.
The emulator draws ~15x slower than the radio (a full screen is 600K instructions, 0.75 s), so a
byte landing mid-draw would be lost here where it is not on the glass: the computer's bytes are
handed over one at a time, each once the last was read and at least 150 ms after it, and a key's
press or release only once the firmware has registered the one before (as test_fw.press waits).
"""
import os
import sys
import termios
import time
import tty

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', 'fw'))
import test_fw  # noqa: E402

RATE = 800_000                              # instructions per second: 4000 per 5 ms tick
SLICE = 4000
GAP = RATE * 15 // 100                     # 150 ms: a key is held at least this long
BAUDS = {termios.B38400: 38400, termios.B115200: 115200}
DEBUG = os.environ.get('EMU_DEBUG')

cpu = test_fw.boot(sys.argv[1] if len(sys.argv) > 1 else test_fw.FW)
test_fw.menu(cpu, 'REMOTE', ['UP', 'EXIT'])
cpu.uart_out.clear()
master, slave = os.openpty()
tty.setraw(slave)
os.set_blocking(master, False)
print(os.ttyname(slave), flush=True)

start, done, last, held, pending = time.monotonic(), 0, 0, None, bytearray()
while True:
    try:
        pending += os.read(master, 256)
    except BlockingIOError:
        pass
    registered = held is None or bool(cpu.xram[cpu.key_held_at]) == held or done - last > 2 * RATE
    if pending and not cpu.uart_in and not cpu.sfr[0xEA] & 1 and done - last >= GAP and registered:
        b = pending.pop(0)
        cpu.uart_in.append(b)
        held = b > 0x80 if b & 0x80 else None   # a refused key (PTT) never registers: the 2 s timeout
        last = done
    cpu.uart_baud = BAUDS.get(termios.tcgetattr(slave)[4])   # the speed tdh3 set: a mismatch drops bytes
    cpu.run(SLICE)
    done += SLICE
    if DEBUG and done % RATE == 0:
        print('t', done // RATE, 'lost', cpu.uart_lost, 'lag', round(time.monotonic() - start - done / RATE, 2), file=sys.stderr, flush=True)
    if cpu.uart_out:
        try:
            os.write(master, bytes(cpu.uart_out))
        except BlockingIOError:
            pass                           # nobody reading: dropped, as on an unplugged cable
        cpu.uart_out.clear()
    ahead = done / RATE - (time.monotonic() - start)
    if ahead > 0:
        time.sleep(ahead)
