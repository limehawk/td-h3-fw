#!/usr/bin/env python3
"""Live checks on the real radio over REMOTE (MENU -> REMOTE ON), after a flash.

    python3 tools/livetest.py            # quick: version, home screen, signal (under a minute)
    python3 tools/livetest.py light      # quick plus what the update changed
    python3 tools/livetest.py all        # every check (~8 minutes); pictures in /tmp/td-h3-live/

Every step reads the screen back (tools/radio.py) and the radio is left as it was found: same
slot, same channel or frequency, keypad unlocked, light off, scan stopped, home screen. Nothing
here transmits (REMOTE refuses PTT) or changes a saved setting.
"""
import os
import re
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from radio import Radio, SLOT_Y, TDH3

OUT = '/tmp/td-h3-live'
_src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'fw', 'main.c')).read()
MENU = re.findall(r'"([^"]+)"', _src[_src.index('mnames[M_N] = {'):].split('};')[0])   # the menu, in order
r = Radio()
results = []


def shot(name):
    os.makedirs(OUT, exist_ok=True)
    subprocess.run([TDH3, '-p', r.port, 'remote', 'screen', '-o', os.path.join(OUT, name + '.png')],
                   capture_output=True)


def status_line():                          # tdh3's last line: frequency, dBm, squelch, speaker, battery
    for _ in range(3):                      # the radio drops a byte from the PC when it is busy (a call
        out = subprocess.run([TDH3, '-p', r.port, 'remote', 'screen'], capture_output=True, text=True,
                             errors='replace').stdout.strip().splitlines()   # coming in): ask again
        if out and 'dBm' in out[-1]:
            return out[-1]
    return out[-1] if out else ''


def check(name, ok, detail=''):
    results.append((name, ok, detail))
    print(('PASS ' if ok else 'FAIL ') + name + ('  ' + detail if detail else ''))


def where():                                # the selected slot's state, to put back afterwards
    s = r.home()
    slot = r.slot(s)
    y = SLOT_Y.get(slot, 0)
    label, f = s.get((y + 13, 0), '').split('  (')[0].strip(), r.freq(s, y)   # "*M20", never "(lit)"
    if not (y and label and f):             # the rows moved: say so, never compare blanks
        raise RuntimeError('cannot read the selected slot (slot %r, label %r, freq %r): rows in SLOT_Y wrong?' % (slot, label, f))
    return slot, label, f


def restore(state):
    slot, label, f = state
    r.select(slot)
    now = where()
    if label.lstrip('*').startswith('M'):
        if not now[1].lstrip('*').startswith('M'):
            r.key('VM')
        n = int(label.lstrip('*M'))
        if where()[1] != label:
            r.channel(n)
    else:
        if now[1].lstrip('*').startswith('M'):
            r.key('VM')
        if r.freq(r.screen(), SLOT_Y[slot]) != f:
            r.tune(int(f.replace('.', '').strip()))
    back = where()
    check('radio left as found', back[:2] == state[:2] and back[2] == f, '%s -> %s' % (state, back))


def item(n):                                # menu item n by its number (digit jump), the selected row back
    r.key('MENU'); time.sleep(1.2)
    # Both digits in one tdh3 call, 0.4 s apart: a call each put 1.2 s between them (tdh3 starting,
    # its 0.4 s speed probe), past the radio's 1 s wait after one digit, so 15 opened 01 and then 05.
    r._run('keys', *'%02d' % n)
    time.sleep(0.5)
    return next((t for t in r.screen().values() if t.endswith('(lit)') and not t.startswith(('A ', 'B '))), '')


def t_version():
    want = open(os.path.join(os.path.dirname(__file__), '..', 'fw', 'build', 'version')).read().strip()
    r.home()
    got = item(MENU.index('VERSION') + 1)
    r.home()
    check('version is the build', want in got, got or 'VERSION not found')


def t_home():
    s = r.home()
    shot('home')
    a, b = s.get((27, 0), ''), s.get((86, 0), '')
    check('home: both slots drawn', r.is_home(s) and a and b, '%s / %s' % (a, b))
    bat = s.get((0, 92), '').strip()
    check('home: status bar has the battery %', bat.endswith('%'), bat)
    check('home: meter reading', s.get((62, 1), '').startswith('S'), s.get((62, 1), ''))   # "S9+11" at x 1, inside the bar


def t_menu():                               # each item where the firmware's menu table says it is, its section up top
    for name, sec in (('ATTENUATOR', 'RECEIVE'), ('SCAN PAUSE', 'SCAN'), ('WEATHER', 'TOOLS'), ('TX BANDS', 'TRANSMIT'),
                      ('AUTO OFF', 'DISPLAY & SOUND'), ('REMOTE', 'SETUP')):
        r.home()
        got = item(MENU.index(name) + 1)
        title = r.screen().get((0, 0), '')
        check('menu item %d is %s, in %s' % (MENU.index(name) + 1, name, sec), got.startswith(name) and title.startswith(sec + ' '),
              '%s / %s' % (got, title))
    r.home()


def t_scan():
    state = where()
    if not state[1].lstrip('*').startswith('M'):
        check('scan', True, 'skipped: the selected slot is in frequency mode')
        return
    r.key('*'); time.sleep(1.5)
    s = r.screen()
    check('scan starts (status bar)', s.get((0, 0), '').startswith('SCAN'), s.get((0, 0), ''))
    seen = {r.freq(r.screen(), SLOT_Y[state[0]]) for _ in range(6) if not time.sleep(0.7)}
    check('scan moves or stops on a station', len(seen) > 1 or 'open' in status_line(), str(sorted(seen)))
    r.key('UP'); time.sleep(1)
    check('UP keeps scanning', r.screen().get((0, 0), '').startswith('SCAN'))
    r.key('VM'); time.sleep(1)
    check('V/M stops the scan', not r.screen().get((0, 0), '').startswith('SCAN'), r.screen().get((0, 0), ''))
    restore(state)


def t_lock():
    r.home()
    r.key('*:long'); time.sleep(1.5)
    shot('locked')
    r.key('UP'); time.sleep(0.6)            # refused while locked: nothing should change
    r.key('*:long'); time.sleep(1.5)
    shot('unlocked')
    check('lock and unlock (pictures: locked.png has the padlock, unlocked.png not)', r.is_home())


def t_light():
    r.home()
    r.key('TORCH'); time.sleep(1)
    s = r.screen().get((0, 0), '')
    r.key('TORCH'); time.sleep(1)
    s2 = r.screen().get((0, 0), '')
    on = any(w in s for w in ('MOON', 'LOW', 'MED', 'HIGH'))
    check('flashlight on then off (status bar)', on and not any(w in s2 for w in ('MOON', 'LOW', 'MED', 'HIGH')),
          '%r -> %r (does the LED light?)' % (s, s2))


def t_theme():                              # each theme in turn: the menu and home in its colours, then back
    names = []
    for i in range(4):
        r.home()
        item(MENU.index('THEME') + 1)
        r.key('MENU'); time.sleep(0.6)          # select, next theme, done
        r.key('UP'); time.sleep(1)
        r.key('MENU'); time.sleep(0.6)
        names.append(item(MENU.index('THEME') + 1))
        shot('theme%d-menu' % i)
        r.key('VM'); time.sleep(2)
        shot('theme%d-home' % i)
    check('THEME cycles back to where it was', r.is_home() and len(set(names)) == 4, ' | '.join(names))


def t_heard():                              # LAST HEARD opens from the menu; V/M back to it, again home
    r.home()
    item(MENU.index('LAST HEARD') + 1)
    r.key('MENU'); time.sleep(2)
    s = r.screen()
    shot('heard')
    check('LAST HEARD opens', any(t.startswith('LAST HEARD') for t in s.values()),
          ' | '.join(t for (y, x), t in sorted(s.items()) if 14 <= y < 110) or 'no rows yet')
    r.key('VM'); time.sleep(2)
    check('LAST HEARD: V/M back to the menu', any(t.startswith('LAST HEARD') and t.endswith('(lit)') for t in r.screen().values()))
    r.key('VM'); time.sleep(2)
    check('menu: V/M back to home', r.is_home())


def t_fm():
    r.home()
    r.key('8:long'); time.sleep(3)
    s = r.screen()
    shot('fm')
    check('FM RADIO opens', any('FM RADIO' in t for t in s.values()))
    r.key('VM'); time.sleep(2)
    check('FM back to home', r.is_home())


def t_scope():
    r.home()
    r.key('3:long'); time.sleep(4)
    s = r.screen()
    shot('scope')
    check('SCOPE opens', any('SCOPE' in t for t in s.values()))
    r.key('VM'); time.sleep(2)
    check('scope back to home', r.is_home())


def t_signal():
    lines = [status_line() for _ in range(5) if not time.sleep(0.5)]
    check('signal readings', all('dBm' in l for l in lines), ' | '.join(l.split('  battery')[0] for l in lines))



def t_note():                               # a refusal on the bottom line goes by itself after 3 s
    state = where()
    mem = state[1].lstrip('*').startswith('M')
    msg = 'NO SUCH CHANNEL' if mem else '18-999 MHz ONLY'
    for d in ('300' if mem else '000000'):  # refused: the slot doesn't change
        r.key(d); time.sleep(0.3)
    time.sleep(0.5)
    check('note shows: ' + msg, any(msg in t for t in r.screen().values()))
    time.sleep(3.5)                         # no key
    check('note gone after 3 s with no key', not any(msg in t for t in r.screen().values()))
    restore(state)

TESTS = {'version': t_version, 'home': t_home, 'menu': t_menu, 'scan': t_scan, 'lock': t_lock,
         'light': t_light, 'heard': t_heard, 'theme': t_theme, 'fm': t_fm, 'scope': t_scope, 'signal': t_signal,
         'note': t_note}

QUICK = ['version', 'home', 'signal']       # after a flash: it boots, draws and receives (under a minute)

if __name__ == '__main__':
    # no names: the quick check; name what the update changed on top (livetest.py light menu);
    # 'all': every check, about 8 minutes
    names = list(TESTS) if sys.argv[1:] == ['all'] else QUICK + [n for n in sys.argv[1:] if n not in QUICK]
    start = where()
    try:
        for n in names:
            try:
                TESTS[n]()
            except Exception as e:          # one broken check doesn't stop the rest
                check(n, False, '%s: %s' % (type(e).__name__, e))
    finally:
        r.home()
        restore(start)
    bad = [n for n, ok, _ in results if not ok]
    print('\n%d checks, %d failed%s. Pictures: %s' % (len(results), len(bad), (': ' + ', '.join(bad)) if bad else '', OUT))
    sys.exit(1 if bad else 0)
