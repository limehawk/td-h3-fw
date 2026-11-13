"""Hardware budget: what each scarce resource has left, and what a byte of it is worth.

    python3 tools/budget.py                  # measure fw/build (make -C fw budget; make test runs it)
    python3 tools/budget.py --compare v0.28.0  # build and test that rev, print the delta

A resource is over when its free space falls below its reserve (exit 1, named). Its price per
byte is 1 / (free - reserve): the share of the spendable headroom one byte uses. A trade is worth
it when sum(price x bytes spent) is negative, which is the same as the pressure total
(sum of ln(spendable / headroom)) going down. See docs/DEVELOPING.md, "Hardware budget".
"""
import math
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# The policy: bytes each resource keeps free. Change it here and only here (DEVELOPING.md says why).
RESERVE = {
    'flash': 2048,      # room for one mid-size feature or a fix late in a release (FM was ~1.5 KB)
    'xdata': 48,        # one new buffer or a function's plain (non-reentrant) locals
    'stack': 24,        # below test_fw's 0xF0 (itself 16 B short of 0xFF): ~3 more nested frames
    'settings': 4,      # a few settings before the block needs a new layout (a major version)
}
CFG_BLOCK = 0x1FF0 - 0x1FC0                 # fw/main.c: settings live at 0x1FC0, never into 0x1FF0
SP_LIMIT = 0xF0                             # fw/test_fw.py
SIZES = {'uint8_t': 1, 'int8_t': 1, 'char': 1, 'uint16_t': 2, 'int16_t': 2, 'uint32_t': 4, 'int32_t': 4}


def settings(main_c):
    """cfg_t in fw/main.c: (bytes used, free bytes, free bits). free_* fields and unnamed bitfields are free."""
    src = open(main_c).read()
    body = struct(src, 'cfg_t')
    sizes = dict(SIZES)
    for typ in {d.split()[0] for d in decls(body)} - set(SIZES):   # helper structs (slot_t)
        sizes[typ] = sum(field_bytes(d, SIZES)[0] for d in decls(struct(src, typ)))
    used = free = bits = 0
    for d in decls(body):
        n, spare = field_bytes(d, sizes)
        used += n
        bits += spare
        free += n if re.search(r'\bfree_', d) else 0
    return used, free + CFG_BLOCK - used, bits


def struct(src, name):
    return re.search(r'typedef struct \{([^{}]*)\}\s*%s;' % name, src).group(1)


def decls(body):
    body = re.sub(r'//[^\n]*', '', body)
    return [d.strip() for d in body.split(';') if d.strip()]


def field_bytes(d, sizes):
    """One declaration -> (bytes, spare bits). Bitfields in one declaration share whole bytes."""
    typ, rest = d.split(None, 1)
    if ':' in rest:
        widths = [(n.strip(), int(w)) for n, w in (f.split(':') for f in rest.split(','))]
        total = sum(w for _, w in widths)
        nbytes = -(-total // 8)
        return nbytes, sum(w for n, w in widths if not n) + 8 * nbytes - total
    n = 0
    for f in rest.split(','):
        m = re.match(r'\s*\**\s*\w+\s*(?:\[(\d+)\])?', f)
        n += sizes[typ] * int(m.group(1) or 1)
    return n, 0


def xdata(build):
    m = re.search(r'EXTERNAL RAM\s+\S+\s+\S+\s+(\d+)\s+(\d+)', open(os.path.join(build, 'fw.mem')).read())
    return int(m.group(1)), int(m.group(2))


def stack_peak(fw):
    """Deepest SP over the last full make test, or None. fw/test_fw.py writes build/stack-peak."""
    path = os.path.join(fw, 'build', 'stack-peak')
    if not os.path.exists(path) or os.path.getmtime(path) < os.path.getmtime(os.path.join(fw, 'build', 'td-h3.bin')):
        return None
    return int(open(path).read(), 16)


def measure(fw, peak=None):
    """Every resource: name -> (used, limit, note). Flash and XDATA are the full build's; the
    RX-only build is a subset of it and shown in the note."""
    b = os.path.join(fw, 'build')
    flash = {n: os.path.getsize(os.path.join(b, n + '.bin')) for n in ('td-h3', 'td-h3-rx')}
    xd = {n: xdata(os.path.join(b, n)) for n in ('td-h3', 'td-h3-rx')}
    peak = stack_peak(fw) if peak is None else peak
    start = re.search(r'Stack starts at: 0x([0-9a-f]+)', open(os.path.join(b, 'td-h3', 'fw.mem')).read()).group(1)
    used, free, bits = settings(os.path.join(fw, 'main.c'))
    return {
        'flash': (flash['td-h3'], 0xF800, 'RX build %d' % flash['td-h3-rx']),
        'xdata': (xd['td-h3'][0], xd['td-h3'][1], 'RX build %d' % xd['td-h3-rx'][0]),
        'stack': (peak, SP_LIMIT, 'no fresh peak: run make -C fw test' if peak is None else
                  'peak 0x%02X; DATA ends and the stack starts at 0x%s' % (peak, start)),
        'settings': (CFG_BLOCK - free, CFG_BLOCK, '+%d spare bits; the only EEPROM block we own' % bits),
    }


def price(r, used, limit):
    head = limit - used - RESERVE[r]
    return 1 / head if head > 0 else math.inf


def verdict(r, used, limit):
    free, res = limit - used, RESERVE[r]
    return 'over' if free < res else 'tight' if free < 2 * res else 'watch' if free < 3 * res else 'plenty'


def term(r, used, limit):
    """This resource's share of the pressure: ln(spendable / headroom). Its slope is the price."""
    head = limit - used - RESERVE[r]
    return math.log((limit - RESERVE[r]) / head) if head > 0 else math.inf


def pressure(m):
    """0 when everything is free, rising without limit as any resource nears its reserve."""
    return sum(term(r, u, lim) for r, (u, lim, _) in m.items() if u is not None)


def table(m):
    print('%-9s %6s %6s %6s %7s %9s  %-7s %s' % ('resource', 'used', 'limit', 'free', 'reserve', 'price/B', 'verdict', 'note'))
    for r, (used, limit, note) in m.items():
        if used is None:
            print('%-9s %6s %6d %6s %7d %9s  %-7s %s' % (r, '?', limit, '?', RESERVE[r], '?', '?', note))
            continue
        print('%-9s %6d %6d %6d %7d %9.5f  %-7s %s' % (r, used, limit, limit - used, RESERVE[r],
                                                       price(r, used, limit), verdict(r, used, limit), note))
    print('pressure %.3f (lower is better; a trade is worth it when it lowers this)' % pressure(m))
    print()
    for r, (used, limit, _) in m.items():
        if used is not None:
            print('%-9s %s  %d B spendable' % (r, bar(used, limit, RESERVE[r]), limit - used - RESERVE[r]))
    print('%-9s %s' % ('', '  '.join(bar(u, 1, r, 1) + t for u, r, t in ((1, 0, ' used'), (0, 0, ' free'), (0, 1, ' reserve'), (1, 1, ' used into the reserve')))))


# On a terminal: free dim, the reserve amber, used into it red (none for a log or NO_COLOR)
PAINT = {'\u2591': '2', '\u2592': '38;5;214', '\u2593': '31'} if sys.stdout.isatty() and not os.environ.get('NO_COLOR') else {}


def bar(used, limit, res, w=40):
    """The whole limit in w cells: used from the left, the reserve a block at the right end."""
    out = ''
    for i in range(w):
        x = (i + 0.5) * limit / w
        out += '\u2593' if x < used and x >= limit - res else '\u2588' if x < used else '\u2592' if x >= limit - res else '\u2591'
    return ''.join('\033[%sm%s\033[0m' % (PAINT[c], c) if c in PAINT else c for c in out)


def compare(rev):
    """Build and test rev in a reusable worktree under fw/build, then print this tree minus that rev."""
    fw = os.path.join(ROOT, 'fw')
    here = measure(fw)
    if here['stack'][0] is None:
        sys.exit('run make -C fw test here first: the comparison needs this tree\'s stack peak')
    wt = os.path.join(fw, 'build', 'compare')
    sha = subprocess.check_output(['git', '-C', ROOT, 'rev-parse', rev], text=True).strip()
    subprocess.check_call(['git', '-C', ROOT, 'worktree', 'prune'])
    if os.path.isdir(os.path.join(wt, 'fw')):
        subprocess.check_call(['git', '-C', wt, 'checkout', '-q', '--detach', sha])
    else:
        subprocess.check_call(['git', '-C', ROOT, 'worktree', 'add', '-q', '--detach', wt, sha])
    for link in ('.toolchain', 'stock/td-h3/250317/H3_250317.BIN'):   # untracked: borrow this tree's
        src, dst = os.path.realpath(os.path.join(ROOT, link)), os.path.join(wt, link)
        if os.path.exists(src) and not os.path.lexists(dst):
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            os.symlink(src, dst)
    print('building and testing %s in %s ...' % (rev, wt))
    run = subprocess.run(['make', '-C', os.path.join(wt, 'fw'), 'test', 'NOBUDGET=1'], capture_output=True, text=True)
    sps = [int(s, 16) for s in re.findall(r'\[sp ([0-9A-F]{2})\]', run.stdout)]
    if not sps:
        sys.exit('%s: make test printed no stack peaks\n%s' % (rev, (run.stdout + run.stderr)[-2000:]))
    if run.returncode:
        print('note: make test failed at %s; its stack peak covers the tests that ran' % rev)
    base = measure(os.path.join(wt, 'fw'), peak=max(sps))
    print('\n%s (this tree) against %s:' % (subprocess.check_output(['git', '-C', ROOT, 'describe', '--tags', '--always', '--dirty'], text=True).strip(), rev))
    print('%-9s %7s %7s %7s %9s  %s' % ('resource', rev[:7], 'here', 'delta', 'pressure', 'verdict'))
    for r in here:
        (u0, l0, _), (u1, l1, _) = base[r], here[r]
        print('%-9s %7d %7d %+7d %+9.4f  %s -> %s' % (r, u0, u1, u1 - u0, term(r, u1, l1) - term(r, u0, l0),
                                                    verdict(r, u0, l0), verdict(r, u1, l1)))
    p0, p1 = pressure(base), pressure(here)
    print('pressure %.3f -> %.3f: %s' % (p0, p1, 'better' if p1 < p0 else 'worse' if p1 > p0 else 'same'))


def asm_line(b):
    """The hand-written assembly: which image the table measured, the ASM=1 saving, and any stale asm."""
    linked = lambda out: open(os.path.join(b, out + '.asm')).read().split() if os.path.exists(os.path.join(b, out + '.asm')) else []
    size = lambda out: os.path.getsize(os.path.join(b, out + '.bin')) if os.path.exists(os.path.join(b, out + '.bin')) else None
    stale = subprocess.run([sys.executable, os.path.join(ROOT, 'tools', 'asmcheck.py'), 'stale'],
                           capture_output=True, text=True).stdout.split()
    here = 'ASM=1' if linked('td-h3') else 'ASM=0'
    c = size('td-h3') if here == 'ASM=0' else size('td-h3-c')
    a = size('td-h3') if here == 'ASM=1' else size('td-h3-asm')
    saving = '%d B (%s, %d asm files)' % (c - a, 'make asm' if here == 'ASM=0' else 'this build', len(linked('td-h3-asm') or linked('td-h3'))) \
        if a and c else 'not built (make asm)'
    print('asm: the table is the %s image; ASM=1 saves %s; stale: %s' % (here, saving, ' '.join(stale) or 'none'))


def selfcheck():
    d = 'uint8_t power:2, shift:2, save:2, light:2'
    assert field_bytes(d, SIZES) == (1, 0)
    assert field_bytes('uint8_t pause:3, resume:3, :2', SIZES) == (1, 2)
    assert field_bytes('uint8_t free_41[7]', SIZES) == (7, 0)
    assert field_bytes('uint16_t rxt, txt, fm', SIZES) == (6, 0)
    assert verdict('stack', 0xF0 - 23, 0xF0) == 'over' and verdict('stack', 0xF0 - 24, 0xF0) == 'tight'


if __name__ == '__main__':
    selfcheck()
    if sys.argv[1:2] == ['--compare']:
        compare(sys.argv[2] if len(sys.argv) > 2 else 'origin/main')
        sys.exit()
    m = measure(os.path.join(ROOT, 'fw'))
    table(m)
    asm_line(os.path.join(ROOT, 'fw', 'build'))
    over = [r for r, (u, lim, _) in m.items() if u is not None and verdict(r, u, lim) == 'over']
    if over:
        sys.exit('budget: %s below reserve' % ', '.join(over))
