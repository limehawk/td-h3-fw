#!/usr/bin/env python3
"""Pin -> net map for one chip on a vector-PDF schematic (how docs/HARDWARE.md's MCU table was made).

    python3 -m venv /tmp/v && /tmp/v/bin/pip install pymupdf
    /tmp/v/bin/python tools/schpins.py "TD-H3 sch.pdf" 2 935,455,1180,700

PAGE is 1-based. BOX is the chip's pin-name area in PDF points (x0,y0,x1,y1); find it with
`pdftotext -bbox` (the TD-H3 MCU U2 sits at 935,455,1180,700 on page 2 of nicsure's
TD-H3-Engineering schematic @ 37f22be).

How: wires are the red line segments. Segments that touch end to end (or end on another
segment, a T-junction) are joined into nets with a union-find. A net's pin number is the
digit text at its end inside BOX; its label is the nearest word within 45 pt of one of its
ends outside BOX. Check a few results against the rendered page (pdftoppm -r 288) before
trusting them: a label next to two wires can be picked for the wrong one.
"""
import collections
import re
import sys

import pymupdf


def wires(page):
    segs = []
    for d in page.get_drawings():
        c = d.get("color")
        if c and c[0] > .9 and c[1] < .1 and c[2] < .1:      # red = wire on this schematic
            segs += [(it[1].x, it[1].y, it[2].x, it[2].y) for it in d["items"] if it[0] == "l"]
    return segs


def on_segment(px, py, s, t=0.6):
    x0, y0, x1, y1 = s
    if abs(x0 - x1) < t:
        return abs(px - x0) < t and min(y0, y1) - t <= py <= max(y0, y1) + t
    if abs(y0 - y1) < t:
        return abs(py - y0) < t and min(x0, x1) - t <= px <= max(x0, x1) + t
    return False


def nets(segs):
    parent = list(range(len(segs)))

    def root(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i
    for i, a in enumerate(segs):
        for j in range(i + 1, len(segs)):
            b = segs[j]
            if any(on_segment(x, y, b) for x, y in (a[:2], a[2:])) or any(on_segment(x, y, a) for x, y in (b[:2], b[2:])):
                parent[root(i)] = root(j)
    out = collections.defaultdict(list)
    for i, s in enumerate(segs):
        out[root(i)].append(s)
    return out.values()


def main():
    pdf, page_no, box = sys.argv[1], int(sys.argv[2]), tuple(map(float, sys.argv[3].split(",")))
    page = pymupdf.open(pdf)[page_no - 1]
    words = page.get_text("words")
    inside = lambda x, y: box[0] - 25 <= x <= box[2] + 25 and box[1] - 25 <= y <= box[3] + 25
    pin_text = [w for w in words if re.fullmatch(r"\d{1,2}", w[4]) and inside(w[0], w[1])]

    def dist(w, x, y):
        return max(w[0] - x, 0, x - w[2]), max(w[1] - y, 0, y - w[3])

    def label(x, y):
        best = None
        for w in words:
            if inside((w[0] + w[2]) / 2, (w[1] + w[3]) / 2):
                continue
            dx, dy = dist(w, x, y)
            if (dy < 6 and dx < 45) or (dx < 6 and dy < 45):
                if best is None or dx + dy < best[0]:
                    best = (dx + dy, w[4])
        return best and best[1]

    result = collections.defaultdict(set)
    for net in nets(wires(page)):
        ends = [e for s in net for e in (s[:2], s[2:])]
        pins = {int(w[4]) for x, y in ends if inside(x, y) for w in pin_text if max(dist(w, x, y)) < 3}
        labels = {label(x, y) for x, y in ends if not inside(x, y)} - {None}
        for p in pins:
            result[p] |= labels
    for p in sorted(result):
        print(p, " ".join(sorted(result[p])))


if __name__ == "__main__":
    main()
