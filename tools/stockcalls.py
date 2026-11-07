#!/usr/bin/env python3
"""Constant arguments at every call of one stock-firmware function.

    python3 tools/dis51.py stock/td-h3/250317/H3_250317.BIN > /tmp/h3.dis
    python3 tools/stockcalls.py /tmp/h3.dis 0xE74F        # BK4819 write(R7=reg, R5=hi, R3=lo)
    python3 tools/stockcalls.py /tmp/h3.dis 0xD32F        # EEPROM read(R7=addr hi, R5=addr lo, R3=count)

Keil C51 passes the first byte arguments in R7, R5, R3. For each LCALL/LJMP to TARGET this
walks back to the previous call or jump and replays the simple moves (mov Rn,#imm, clr A,
mov A,#imm, mov Rn,A, inc A) to recover constants; anything computed shows as '?'.
This is how the BK4819 init/RX/TX sequences and the EEPROM map in docs/HARDWARE.md were read.
"""
import re
import sys

STOP = ("lcall", "ljmp", "ret", "sjmp", "ajmp", "acall")


def main():
    lines = open(sys.argv[1]).read().splitlines()
    target = sys.argv[2].upper().replace("0X", "0x")
    ins = [(int(l[:4], 16), l[16:].strip()) for l in lines if re.match(r"^[0-9A-F]{4}  ", l)]
    for i, (addr, op) in enumerate(ins):
        if op not in ("lcall " + target, "ljmp " + target):
            continue
        j = i - 1
        while j > max(i - 12, -1) and not ins[j][1].startswith(STOP):
            j -= 1
        regs, a = {}, None
        for _, t in ins[j + 1:i]:
            if m := re.match(r"mov (R\d),#0x(..)", t):
                regs[m[1]] = m[2]
            elif t == "clr A":
                a = "00"
            elif m := re.match(r"mov A,#0x(..)", t):
                a = m[1]
            elif m := re.match(r"mov (R\d),A", t):
                regs[m[1]] = a or "?"
            elif m := re.match(r"mov (R\d),", t):
                regs[m[1]] = "?"
            elif t == "inc A" and a not in (None, "?"):
                a = "%02X" % ((int(a, 16) + 1) & 255)
            elif t.startswith(("mov A,", "movx A", "add", "orl A", "anl A", "movc")):
                a = "?"
        print("%04X  R7=%s R5=%s R3=%s%s" % (addr, regs.get("R7", "?"), regs.get("R5", "?"),
                                            regs.get("R3", "?"), "  (tail jump)" if op.startswith("ljmp") else ""))


if __name__ == "__main__":
    main()
