"""The hand-written assembly's guard and its constants (docs/DEVELOPING.md, Assembly).

    python3 tools/asmcheck.py link ASM CFUN     # the fw/asm modules to link (the Makefile asks)
    python3 tools/asmcheck.py defs ASM CFUN     # -DC_<NAME> for every function built from C
    python3 tools/asmcheck.py stale             # the asm functions whose C changed since the asm was written
    python3 tools/asmcheck.py inc               # fw/build/asm.inc: layout and constants, from the C
    python3 tools/asmcheck.py update <fn>       # after redoing <fn>'s asm: record its C's hash in the .asm

Each fw/asm/<fn>.asm starts with
    ; asm-for: <file>.c <fn>
    ; c-sha256: <16 hex digits>
naming the C it replaces: the #ifdef C_<FN> ... #endif block in that file. When the block's text no
longer hashes to the recorded value, the asm is stale: the build says "asm stale: <fn>, using C" and
compiles the C instead. A file without an asm (or ASM=0) is C too. asm-for: none marks a helper file
(asmlib), linked whenever ASM=1.
"""
import hashlib
import os
import re
import subprocess
import sys

FW = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'fw')
ASMDIR = os.path.join(FW, 'asm')
SRC = ('main.c', 'sys.c', 'lcd.c', 'eeprom.c', 'bk4819.c', 'keys.c', 'radio.c', 'clone.c', 'fm.c', 'scan.c',
       'remote.c', 'glyphs.c', 'tones.c', 'help.c')

# What the asm may name, as C expressions evaluated by SDCC against main.c (so fw.h, hw.h and main.c's
# own types and defines are all in scope). NAME = value in asm.inc.
CONSTS = {
    'W': 'W', 'H': 'H', 'BOT_Y': 'BOT_Y', 'METER_Y': 'METER_Y', 'RED': 'RED',
    'P_BG': 'P_BG', 'P_FG': 'P_FG', 'P_DIM': 'P_DIM', 'P_ACC': 'P_ACC', 'P_OK': 'P_OK', 'P_HI': 'P_HI',
    'P_BAR': 'P_BAR', 'P_RED': 'P_RED', 'T_RX': 'T_RX', 'T_TX': 'T_TX',
    'PEEK_OTHER': 'PEEK_OTHER', 'PEEK_PRIO': 'PEEK_PRIO',
    'CFG_SQL': 'offsetof(cfg_t, sql)', 'CFG_TOT': 'offsetof(cfg_t, tot)', 'CFG_ACT': 'offsetof(cfg_t, act)',
    'CFG_PRIO': 'offsetof(cfg_t, prio)', 'CFG_WX': 'offsetof(cfg_t, wx)', 'CFG_GROUP': 'offsetof(cfg_t, group)',
    'CFG_BCAL': 'offsetof(cfg_t, bcal)', 'CFG_S': 'offsetof(cfg_t, s)',
    'SLOT_MEM': 'offsetof(slot_t, mem)', 'SLOT_CHN': 'offsetof(slot_t, chn)', 'SLOT_SIZE': 'sizeof(slot_t)',
    'CHAN_RX': 'offsetof(chan_t, rx)', 'CHAN_TX': 'offsetof(chan_t, tx)', 'CHAN_RXT': 'offsetof(chan_t, rx_tone)',
    'CHAN_TXT': 'offsetof(chan_t, tx_tone)', 'CHAN_MODE': 'offsetof(chan_t, mode)', 'CHAN_NARROW': 'offsetof(chan_t, narrow)',
    'CHAN_POWER': 'offsetof(chan_t, power)', 'CHAN_NAME': 'offsetof(chan_t, name)', 'CHAN_SIZE': 'sizeof(chan_t)',
    'MOD_USB': 'MOD_USB', 'WORDS': 'WORDS', 'D_CLASSIC': 'D_CLASSIC', 'PWR_HIGH': 'PWR_HIGH', 'PWR_ULP': 'PWR_ULP', 'DTMF_RUN': 'DTMF_RUN',
}
# cfg_t bitfields: NAME = its byte, NAME_BIT = its lowest bit, NAME_MASK = its bits in that byte,
# NAME_ACC = the lowest bit's address in ACC (sdas takes no acc.<symbol>)
BITS = {'CFG_ATT': 'att', 'CFG_DW': 'dw', 'CFG_BT': 'bt', 'CFG_THEME': 'theme', 'CFG_STEP': 'step', 'CFG_SSTEP': 'sstep',
        'CFG_VOX': 'vox', 'CFG_KLOCK': 'klock', 'CFG_DIGITS': 'digits'}
KEYWORDS = {'A', 'B', 'ACC', 'DPL', 'DPH', 'SP', 'PSW', 'C', 'AB', 'CY'} | {'R%d' % i for i in range(8)}


def block(path, fn):
    """The text of #ifdef C_<FN> ... its #endif in path, or None."""
    lines = open(path).read().split('\n')
    tag = '#ifdef C_' + fn.upper()
    for i, l in enumerate(lines):
        if l.strip() == tag:
            depth = 0
            for j in range(i + 1, len(lines)):
                s = lines[j].strip()
                if s.startswith('#if'):
                    depth += 1
                elif s.startswith('#endif'):
                    if not depth:
                        return '\n'.join(x.rstrip() for x in lines[i + 1:j])
                    depth -= 1
    return None


def digest(text):
    return hashlib.sha256(text.encode()).hexdigest()[:16]


def header(fn):
    """(c file, function, recorded hash) from fw/asm/<fn>.asm's first lines; c file None for a helper."""
    head = open(os.path.join(ASMDIR, fn + '.asm')).read(2000)
    m = re.search(r'^; asm-for: (\S+)(?: (\w+))?\s*$', head, re.M)
    h = re.search(r'^; c-sha256: ([0-9a-f]{16})\s*$', head, re.M)
    if not m:
        sys.exit('fw/asm/%s.asm: no "; asm-for:" header' % fn)
    if m.group(1) == 'none':
        return None, None, None
    return m.group(1), m.group(2), h.group(1) if h else None


def asm_files():
    return sorted(f[:-4] for f in os.listdir(ASMDIR) if f.endswith('.asm')) if os.path.isdir(ASMDIR) else []


def c_functions():                              # every function with a C original behind #ifdef C_<NAME>
    names = set()
    for f in SRC:
        names |= {n.lower() for n in re.findall(r'^#ifdef C_(\w+)\s*$', open(os.path.join(FW, f)).read(), re.M)}
    return names


def stale(quiet=False):
    out = []
    for fn in asm_files():
        cfile, cname, h = header(fn)
        if cfile is None:
            continue
        text = block(os.path.join(FW, cfile), cname)
        if text is None or digest(text) != h:
            out.append(fn)
            if not quiet:
                print('asm stale: %s, using C' % fn, file=sys.stderr)
    return out


def linked(asm, cfun, quiet=False):
    if asm != '1':
        return []
    skip = set(cfun) | set(stale(quiet))
    return [fn for fn in asm_files() if fn not in skip]


def inc():
    """fw/build/asm.inc: CONSTS and BITS through SDCC, then hw.h's SFRs and SBITs."""
    d = os.path.join(FW, 'build', 'asmk')
    os.makedirs(d, exist_ok=True)
    src = ['#include "../../main.c"']
    src += ['const __code uint16_t asmk_%s = %s;' % (k, v) for k, v in CONSTS.items()]
    src += ['const __code cfg_t asmk_b_%s = {.%s = 0x7F};' % (k, v) for k, v in BITS.items()]   # a bitfield keeps its own width
    open(os.path.join(d, 'asmk.c'), 'w').write('\n'.join(src) + '\n')
    sdcc = os.path.join(FW, '..', '.toolchain', 'sdcc-4.5.0', 'bin', 'sdcc')
    sdcc = sdcc if os.path.exists(sdcc) else 'sdcc'
    subprocess.check_call([sdcc, '-mmcs51', '--model-large', '--std-c11', '-DVERSION="0"', '--disable-warning', '110',
                           '-S', os.path.join(d, 'asmk.c'), '-o', os.path.join(d, 'asmk.asm')],
                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    data, cur = {}, None
    for line in open(os.path.join(d, 'asmk.asm')):
        m = re.match(r'_asmk_(\w+):', line)
        if m:
            cur = data.setdefault(m.group(1), [])
        elif cur is not None and re.match(r'\s+\.(byte|db)\b', line):
            cur += [int(x, 16) for x in re.findall(r'#?0x([0-9a-fA-F]+)', line.split(';')[0])]
        elif cur is not None and line.strip():
            cur = None
    out = ['; generated by tools/asmcheck.py inc from main.c, fw.h and hw.h: do not edit']
    for k in CONSTS:
        b = data['%s' % k]
        out.append('%s = 0x%04X' % (k, b[0] | b[1] << 8))
    for k in BITS:
        b = data['b_' + k]
        at = next(i for i, x in enumerate(b) if x)
        bit = (b[at] & -b[at]).bit_length() - 1
        out += ['%s = %d' % (k, at), '%s_BIT = %d' % (k, bit), '%s_MASK = 0x%02X' % (k, b[at]),
                '%s_ACC = 0x%02X' % (k, 0xE0 + bit)]   # that bit once the byte is in A: jnb CFG_BT_ACC, ...
    out += ['ar%d = 0x%02X' % (i, i) for i in range(8)]    # register bank 0's R0-R7 as direct addresses, as SDCC's own
    out += ['GPTR_XDATA = 0x00', 'GPTR_DATA = 0x40', 'GPTR_CODE = 0x80']   # SDCC's generic pointer space byte (B)
    hw = open(os.path.join(FW, 'hw.h')).read()
    for kind, addr, name in re.findall(r'__(sfr|sbit) __at\s*\((0x[0-9A-Fa-f]+)\)\s+(\w+);', hw):
        if name.upper() not in KEYWORDS:
            out.append('%s = %s' % (name, addr))
    path = os.path.join(FW, 'build', 'asm.inc')
    text = '\n'.join(out) + '\n'
    if not os.path.exists(path) or open(path).read() != text:
        open(path, 'w').write(text)


def update(fn):
    cfile, cname, _ = header(fn)
    text = block(os.path.join(FW, cfile), cname)
    if text is None:
        sys.exit('%s: no #ifdef C_%s block' % (cfile, cname.upper()))
    p = os.path.join(ASMDIR, fn + '.asm')
    s = open(p).read()
    line = '; c-sha256: %s' % digest(text)
    s = re.sub(r'^; c-sha256: .*$', line, s, count=1, flags=re.M) if re.search(r'^; c-sha256:', s, re.M) else \
        re.sub(r'^(; asm-for: .*)$', r'\1\n' + line, s, count=1, flags=re.M)
    open(p, 'w').write(s)
    print('%s: %s' % (fn, line))


def main():
    cmd, args = (sys.argv[1:2] or ['stale'])[0], sys.argv[2:]
    asm, cfun = (args + ['0', ''])[0], (args + ['0', ''])[1].split()
    if cmd == 'link':
        print(' '.join(linked(asm, cfun)))
    elif cmd == 'defs':
        lk = set(linked(asm, cfun, quiet=True))
        print(' '.join('-DC_' + fn.upper() for fn in sorted(c_functions() - lk)))
    elif cmd == 'stale':
        print(' '.join(stale(quiet=True)))
    elif cmd == 'inc':
        inc()
    elif cmd == 'update':
        for fn in args:
            update(fn)
    else:
        sys.exit(__doc__)


if __name__ == '__main__':
    main()
