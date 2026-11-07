#!/usr/bin/env python3
"""Tiny 8051 disassembler with CMS8S6990-family SFR names (the TD-H3 MCU, marked TA3782F).

Names come from Cmsemicon's cms8s6990.h; P4/P5 and their TRIS at 0xC0/0xD8/0xA4/0xA5 are
inferred from how stock firmware drives the 48-pin part.

Usage: dis51.py IMAGE [START [END]]   (addresses in hex)
Linear sweep; good enough to read stock init code and register tables.
"""
import sys

SFR = {
    0x80: "P0", 0x81: "SP", 0x82: "DPL", 0x83: "DPH", 0x84: "DPL1", 0x85: "DPH1", 0x86: "DPS", 0x87: "PCON", 0x88: "TCON", 0x89: "TMOD",
    0x8A: "TL0", 0x8B: "TL1", 0x8C: "TH0", 0x8D: "TH1", 0x8E: "CKCON", 0x8F: "CLKDIV", 0x90: "P1", 0x91: "FUNCCR", 0x93: "DPX0", 0x95: "DPX1",
    0x96: "TA", 0x97: "WDCON", 0x98: "SCON0", 0x99: "SBUF0", 0x9A: "P0TRIS", 0xA0: "P2", 0xA1: "P1TRIS", 0xA2: "P2TRIS", 0xA3: "P3TRIS",
    0xA4: "P4TRIS", 0xA5: "P5TRIS", 0xA8: "IE", 0xAA: "EIE2", 0xAC: "P0EXTIE", 0xAD: "P1EXTIE", 0xAE: "P2EXTIE", 0xAF: "P3EXTIE", 0xB0: "P3",
    0xB2: "EIF2", 0xB4: "P0EXTIF", 0xB5: "P1EXTIF", 0xB6: "P2EXTIF", 0xB7: "P3EXTIF", 0xB8: "IP", 0xB9: "EIP1", 0xBA: "EIP2", 0xBC: "WUTCRL",
    0xBD: "WUTCRH", 0xBE: "BUZDIV", 0xBF: "BUZCON", 0xC0: "P4", 0xC2: "CCL1", 0xC3: "CCH1", 0xC4: "CCL2", 0xC5: "CCH2", 0xC6: "CCL3", 0xC7: "CCH3",
    0xC8: "T2CON", 0xC9: "T2IF", 0xCA: "RLDL", 0xCB: "RLDH", 0xCC: "TL2", 0xCD: "TH2", 0xCE: "CCEN", 0xCF: "T2IE", 0xD0: "PSW", 0xD1: "ADCMPC",
    0xD2: "T34MOD", 0xD3: "ADDLYL", 0xD4: "ADCMPL", 0xD5: "ADCMPH", 0xD8: "P5", 0xDA: "TL3", 0xDB: "TH3", 0xDC: "ADRESL", 0xDD: "ADRESH",
    0xDE: "ADCON1", 0xDF: "ADCON0", 0xE0: "ACC", 0xE2: "TL4", 0xE3: "TH4", 0xE9: "ADCON2", 0xEA: "SCON1", 0xEB: "SBUF1", 0xEC: "SPCR",
    0xED: "SPSR", 0xEE: "SPDR", 0xEF: "SSCR", 0xF0: "B", 0xF1: "I2CSADR", 0xF2: "I2CSCR", 0xF3: "I2CSBUF", 0xF4: "I2CMSA", 0xF5: "I2CMCR",
    0xF6: "I2CMBUF", 0xF7: "I2CMTP", 0xFB: "MLOCK", 0xFC: "MADRL", 0xFD: "MADRH", 0xFE: "MDATA", 0xFF: "MCTRL",
}

def d(a): return SFR.get(a, "0x%02X" % a)
def bit(b):
    base = b & 0xF8 if b >= 0x80 else 0x20 + (b >> 3)
    return "%s.%d" % (SFR.get(base, "0x%02X" % base), b & 7)

simple = {
    0x00: "nop", 0x03: "rr A", 0x04: "inc A", 0x13: "rrc A", 0x14: "dec A", 0x22: "ret", 0x23: "rl A", 0x32: "reti", 0x33: "rlc A",
    0x73: "jmp @A+DPTR", 0x83: "movc A,@A+PC", 0x84: "div AB", 0x93: "movc A,@A+DPTR", 0xA3: "inc DPTR", 0xA4: "mul AB",
    0xA5: "<A5>", 0xB3: "cpl C", 0xC3: "clr C", 0xC4: "swap A", 0xD3: "setb C", 0xD4: "da A", 0xE0: "movx A,@DPTR",
    0xE4: "clr A", 0xF0: "movx @DPTR,A", 0xF4: "cpl A", 0xC5: None,
}

def dis(img, pc):
    o = img[pc]
    b1 = img[pc + 1] if pc + 1 < len(img) else 0
    b2 = img[pc + 2] if pc + 2 < len(img) else 0
    rel = lambda off, n: (pc + n + (off - 256 if off > 127 else off)) & 0xFFFF
    lo = o & 0x0F
    regs = ["@R0", "@R1"] + ["R%d" % i for i in range(8)]
    hi = o & 0xF0
    if o in simple and simple[o]: return simple[o], 1
    if lo == 1: return ("ajmp" if not o & 0x10 else "acall") + " 0x%04X" % (((pc + 2) & 0xF800) | (o >> 5) << 8 | b1), 2
    if o >= 0x06 and lo >= 6 or lo >= 8:
        r = regs[lo - 6] if lo >= 6 else None
        tbl = {0x00: ("inc %s", 1), 0x10: ("dec %s", 1), 0x20: ("add A,%s", 1), 0x30: ("addc A,%s", 1), 0x40: ("orl A,%s", 1),
               0x50: ("anl A,%s", 1), 0x60: ("xrl A,%s", 1), 0x90: ("subb A,%s", 1), 0xC0: ("xch A,%s", 1), 0xE0: ("mov A,%s", 1),
               0xF0: ("mov %s,A", 1)}
        if hi in tbl: return tbl[hi][0] % r, 1
        if hi == 0x70: return "mov %s,#0x%02X" % (r, b1), 2
        if hi == 0x80: return "mov %s,%s" % (d(b1), r), 2
        if hi == 0xA0: return "mov %s,%s" % (r, d(b1)), 2
        if hi == 0xB0: return "cjne %s,#0x%02X,0x%04X" % (r, b1, rel(b2, 3)), 3
        if hi == 0xD0:
            if lo >= 8: return "djnz %s,0x%04X" % (r, rel(b1, 2)), 2
            return "xchd A,%s" % r, 1
    x = {
        0x02: ("ljmp 0x%04X" % (b1 << 8 | b2), 3), 0x12: ("lcall 0x%04X" % (b1 << 8 | b2), 3),
        0x05: ("inc %s" % d(b1), 2), 0x15: ("dec %s" % d(b1), 2), 0x25: ("add A,%s" % d(b1), 2), 0x35: ("addc A,%s" % d(b1), 2),
        0x45: ("orl A,%s" % d(b1), 2), 0x55: ("anl A,%s" % d(b1), 2), 0x65: ("xrl A,%s" % d(b1), 2), 0x95: ("subb A,%s" % d(b1), 2),
        0xC5: ("xch A,%s" % d(b1), 2), 0xE5: ("mov A,%s" % d(b1), 2), 0xF5: ("mov %s,A" % d(b1), 2),
        0x24: ("add A,#0x%02X" % b1, 2), 0x34: ("addc A,#0x%02X" % b1, 2), 0x44: ("orl A,#0x%02X" % b1, 2),
        0x54: ("anl A,#0x%02X" % b1, 2), 0x64: ("xrl A,#0x%02X" % b1, 2), 0x74: ("mov A,#0x%02X" % b1, 2), 0x94: ("subb A,#0x%02X" % b1, 2),
        0x42: ("orl %s,A" % d(b1), 2), 0x52: ("anl %s,A" % d(b1), 2), 0x62: ("xrl %s,A" % d(b1), 2),
        0x43: ("orl %s,#0x%02X" % (d(b1), b2), 3), 0x53: ("anl %s,#0x%02X" % (d(b1), b2), 3), 0x63: ("xrl %s,#0x%02X" % (d(b1), b2), 3),
        0x75: ("mov %s,#0x%02X" % (d(b1), b2), 3), 0x85: ("mov %s,%s" % (d(b2), d(b1)), 3), 0x90: ("mov DPTR,#0x%04X" % (b1 << 8 | b2), 3),
        0x10: ("jbc %s,0x%04X" % (bit(b1), rel(b2, 3)), 3), 0x20: ("jb %s,0x%04X" % (bit(b1), rel(b2, 3)), 3),
        0x30: ("jnb %s,0x%04X" % (bit(b1), rel(b2, 3)), 3), 0x40: ("jc 0x%04X" % rel(b1, 2), 2), 0x50: ("jnc 0x%04X" % rel(b1, 2), 2),
        0x60: ("jz 0x%04X" % rel(b1, 2), 2), 0x70: ("jnz 0x%04X" % rel(b1, 2), 2), 0x80: ("sjmp 0x%04X" % rel(b1, 2), 2),
        0x72: ("orl C,%s" % bit(b1), 2), 0x82: ("anl C,%s" % bit(b1), 2), 0xA0: ("orl C,/%s" % bit(b1), 2), 0xB0: ("anl C,/%s" % bit(b1), 2),
        0x92: ("mov %s,C" % bit(b1), 2), 0xA2: ("mov C,%s" % bit(b1), 2), 0xB2: ("cpl %s" % bit(b1), 2), 0xC2: ("clr %s" % bit(b1), 2),
        0xD2: ("setb %s" % bit(b1), 2), 0xB4: ("cjne A,#0x%02X,0x%04X" % (b1, rel(b2, 3)), 3), 0xB5: ("cjne A,%s,0x%04X" % (d(b1), rel(b2, 3)), 3),
        0xC0: ("push %s" % d(b1), 2), 0xD0: ("pop %s" % d(b1), 2), 0xD5: ("djnz %s,0x%04X" % (d(b1), rel(b2, 3)), 3),
        0xE2: ("movx A,@R0", 1), 0xE3: ("movx A,@R1", 1), 0xF2: ("movx @R0,A", 1), 0xF3: ("movx @R1,A", 1),
        0x76: None,
    }
    if o in x and x[o]: return x[o]
    if o in (0x06, 0x07): return "inc %s" % regs[o - 6], 1
    if o in (0x16, 0x17): return "dec %s" % regs[o - 0x16], 1
    if o in (0x26, 0x27): return "add A,%s" % regs[o - 0x26], 1
    if o in (0x36, 0x37): return "addc A,%s" % regs[o - 0x36], 1
    if o in (0x46, 0x47): return "orl A,%s" % regs[o - 0x46], 1
    if o in (0x56, 0x57): return "anl A,%s" % regs[o - 0x56], 1
    if o in (0x66, 0x67): return "xrl A,%s" % regs[o - 0x66], 1
    if o in (0x76, 0x77): return "mov %s,#0x%02X" % (regs[o - 0x76], b1), 2
    if o in (0x86, 0x87): return "mov %s,%s" % (d(b1), regs[o - 0x86]), 2
    if o in (0x96, 0x97): return "subb A,%s" % regs[o - 0x96], 1
    if o in (0xA6, 0xA7): return "mov %s,%s" % (regs[o - 0xA6], d(b1)), 2
    if o in (0xB6, 0xB7): return "cjne %s,#0x%02X,0x%04X" % (regs[o - 0xB6], b1, rel(b2, 3)), 3
    if o in (0xC6, 0xC7): return "xch A,%s" % regs[o - 0xC6], 1
    if o in (0xD6, 0xD7): return "xchd A,%s" % regs[o - 0xD6], 1
    if o in (0xE6, 0xE7): return "mov A,%s" % regs[o - 0xE6], 1
    if o in (0xF6, 0xF7): return "mov %s,A" % regs[o - 0xF6], 1
    return "db 0x%02X" % o, 1

def main():
    img = open(sys.argv[1], "rb").read()
    pc = int(sys.argv[2], 16) if len(sys.argv) > 2 else 0
    end = int(sys.argv[3], 16) if len(sys.argv) > 3 else len(img)
    while pc < end:
        s, n = dis(img, pc)
        print("%04X  %-9s %s" % (pc, img[pc:pc + n].hex(" "), s))
        pc += n

if __name__ == "__main__":
    main()
