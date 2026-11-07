"""Drive the real radio over REMOTE, checking the screen after every step (tools/tdh3 does the I/O).

    from radio import Radio
    r = Radio()                  # the radio's USB-C cable (CH340), wherever it enumerated
    r.home()                     # V/M until the home screen shows, no half-typed entry
    r.tune(103500)               # 6 digits, one at a time, each checked on the bottom line

Every step reads the screen back and raises if it does not show what the step should have
done, so a dropped or doubled key stops the run instead of corrupting the next step.
Rules learned the hard way are in docs/DEVELOPING.md ("Driving the real radio").
"""
import os
import subprocess
import time

SLOT_Y = {'A': 14, 'B': 73}                # each slot's letter row (fw/main.c draw_slot): frequency at + 9, label + 13

TDH3 = 'tools/tdh3/tdh3'


class Radio:
    def __init__(self, port=None):
        # the CH340 by its stable name: replugging moves it between ttyUSB0 and ttyUSB1
        port = port or os.environ.get('TDH3_PORT') or '/dev/serial/by-id/usb-1a86_USB_Serial-if00-port0'
        self.port = port

    def _run(self, *args):
        p = subprocess.run([TDH3, '-p', self.port, 'remote'] + list(args), capture_output=True, text=True, errors='replace')
        if p.returncode:
            raise RuntimeError('tdh3 remote %s: %s' % (' '.join(args), (p.stderr or p.stdout).strip()))
        return p.stdout

    def screen(self):                       # {(y, x): text}
        out = {}
        for line in self._run('screen').splitlines():
            parts = line.split(None, 2)
            if len(parts) == 3 and parts[0].isdigit() and parts[1].isdigit():
                out[(int(parts[0]), int(parts[1]))] = parts[2]   # may end in '  (x3)' or '  (lit)'
        return out

    def key(self, k):
        self._run('keys', k)
        time.sleep(0.2)

    def bottom(self):
        return self.screen().get((120, 0), '')

    @staticmethod
    def label(s, y):                        # a slot's memory number, "*M20" -> "20" ("VFO" stays), no "(lit)"
        return s.get((y + 13, 0), '').split('  (')[0].strip('*M ')

    @staticmethod
    def freq(s, y):                         # a slot's frequency (SLOT_Y), drawn 2x at y + 9, x 32
        return s.get((y + 9, 32), '').split('  (')[0]

    def is_home(self, s=None):
        s = s or self.screen()
        return all((y + 9, 32) in s for y in SLOT_Y.values())   # both slots' frequencies drawn

    def home(self):
        for _ in range(6):
            for _ in range(2):              # a read cut short by a redraw looks like "not home": V/M there
                s = self.screen()           # would switch the slot's mode, so look twice first
                if self.is_home(s) and not s.get((120, 0), '').startswith(('FREQ', 'CHANNEL')):
                    return s
            self.key('VM')
        raise RuntimeError('could not get back to the home screen: %r' % self.screen())

    def tune(self, khz):                    # active slot's VFO, e.g. 103500 -> 103.500 MHz
        self.home()
        digits = '%06d' % khz
        for i, d in enumerate(digits[:-1]):
            self.key(d)
            want = digits[:i + 1]
            got = self.bottom().replace('FREQ ', '').replace('.', '')
            if got != want:
                raise RuntimeError('typed %s, bottom line shows %r' % (want, self.bottom()))
        self.key(digits[-1])
        s = self.screen()
        f = self.freq(s, SLOT_Y.get(self.slot(s), 0))
        if f.replace(' ', '') != '%d.%03d' % (khz // 1000, khz % 1000):
            raise RuntimeError('tuned %s but the screen shows %r' % (digits, f))
        return f

    def slot(self, s=None):                 # 'A' or 'B': the selected slot's letter is drawn lit
        s = s or self.screen()
        return next((k for k, y in SLOT_Y.items() if s.get((y, 1), '').endswith('(lit)')), ' ')

    def select(self, which):                # make slot 'A' or 'B' the selected one
        self.home()
        if self.slot() != which:
            self.key('#')
        if self.slot() != which:
            raise RuntimeError('could not select slot %s' % which)

    def channel(self, n):                   # selected slot to memory channel n, checked on its label
        self.home()
        y = SLOT_Y.get(self.slot(), 0)
        label = self.label(self.screen(), y)
        if not label.isdigit():              # VFO: V/M into memory mode first
            self.key('VM')
            label = self.label(self.screen(), y)
            if not label.isdigit():
                raise RuntimeError('V/M did not switch to memory mode: %r' % label)
        digits = '%03d' % n
        for i, d in enumerate(digits[:-1]):
            self.key(d)
            if self.bottom().replace('CHANNEL ', '') != digits[:i + 1]:
                raise RuntimeError('typed %s, bottom line shows %r' % (digits[:i + 1], self.bottom()))
        self.key(digits[-1])
        label = self.label(self.screen(), y)
        if label != str(n):
            raise RuntimeError('wanted channel %s, label shows %r' % (digits, label))
        return label
