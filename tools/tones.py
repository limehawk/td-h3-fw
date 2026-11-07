#!/usr/bin/env python3
"""Turn short sine-based sound files into BK4819 note tables for bk_chime(), or hear a table.

    python3 tools/tones.py preview FILE.mp3 OUT.wav     # original, half a second of silence, the radio's version
    python3 tools/tones.py c NAME FILE.mp3 [MAX_S]      # the note_t table for fw/tones.c

How: every 5 ms tick (bk_chime's step) the two strongest pitches are measured. Pitches that carry
on from tick to tick join into tracks, at most two at once (the BK4819 has two tone generators).
Each track's pitch and level curves are cut down to a few straight ramps, which is what bk_chime
plays: "voice v, go to this pitch (or level) over n ticks".
ponytail: two voices, straight ramps; a sound with more partials keeps its strongest two.
"""
import subprocess
import sys
import wave

import numpy as np

RATE = 16000
TICK = RATE // 200                                  # 5 ms
PEAK = 110                                          # loudest level in a table (bk_chime gain is 0-127)


def load(path, max_s=None):
    cmd = ['ffmpeg', '-v', 'error', '-i', path, '-ac', '1', '-ar', str(RATE), '-f', 's16le', '-']
    a = np.frombuffer(subprocess.run(cmd, capture_output=True, check=True).stdout, np.int16) / 32768
    return a[: int(max_s * RATE)] if max_s else a


def frames(a):                                      # per tick: up to two (Hz, amplitude)
    w = np.hanning(512)
    hz = np.fft.rfftfreq(4096, 1 / RATE)
    out = []
    for t in range(len(a) // TICK):
        seg = a[t * TICK: t * TICK + 512]
        if len(seg) < 512:
            seg = np.pad(seg, (0, 512 - len(seg)))
        sp = np.abs(np.fft.rfft(seg * w, 4096)) / 128
        sp[hz < 150] = 0
        pk = []
        for _ in range(2):
            i = int(sp.argmax())
            if sp[i] < 1e-3 or pk and sp[i] < pk[0][1] * 0.2:
                break
            l, m, r = sp[i - 1], sp[i], sp[min(i + 1, len(sp) - 1)]
            d = 0.5 * (l - r) / (l - 2 * m + r) if l - 2 * m + r else 0
            pk.append((hz[i] + d * (hz[1] - hz[0]), m))
            sp[max(i - 24, 0): i + 24] = 0
        out.append(pk)
    return out


def tracks(fr):
    live, done = [], []
    for t, pk in enumerate(fr):
        used = set()
        for f, amp in pk:
            best = None
            for k, tr in enumerate(live):
                pf, pa = tr[-1][1], tr[-1][2]
                if k not in used and abs(f - pf) < max(25, 0.04 * pf) and amp < 2.5 * pa + 1e-3:
                    best = k if best is None or abs(f - pf) < abs(f - live[best][-1][1]) else best
            if best is None:
                live.append([(t, f, amp)])
                used.add(len(live) - 1)
            else:
                live[best].append((t, f, amp))
                used.add(best)
        for tr in [x for x in live if x[-1][0] < t - 2]:
            live.remove(tr)
            done.append(tr)
    done += live
    return [tr for tr in done if len(tr) >= 3]


def simplify(p, tol):                               # Ramer-Douglas-Peucker on (t, value)
    if len(p) < 3:
        return p
    (t0, v0), (t1, v1) = p[0], p[-1]
    d = [abs(v - (v0 + (v1 - v0) * (t - t0) / (t1 - t0 or 1))) for t, v in p]
    i = int(np.argmax(d))
    if d[i] <= tol(p[i][1]):
        return [p[0], p[-1]]
    return simplify(p[: i + 1], tol)[:-1] + simplify(p[i:], tol)


def table(a):
    fr = frames(a)
    trs = tracks(fr)
    big = max(x[2] for tr in trs for x in tr)
    trs = [tr for tr in trs if max(x[2] for x in tr) > big * 0.03]
    trs.sort(key=lambda tr: -max(x[2] for x in tr))           # loudest first get the voices
    busy, ev = [[], []], []
    for tr in trs:
        s, e = tr[0][0], tr[-1][0] + 2
        v = next((v for v in (0, 1) if all(e <= b0 or s >= b1 for b0, b1 in busy[v])), None)
        if v is None:
            continue
        busy[v].append((s, e))
        gains = simplify([(t, amp / big * PEAK) for t, f, amp in tr], lambda g: 4)
        pitch = simplify([(t, f) for t, f, amp in tr], lambda f: 0.008 * f)
        ev.append((s, v, 1, round(tr[0][1]), 0))                          # pitch jump at the start
        prev = (s, 0)
        for t, g in gains:
            ev.append((prev[0], v, 0, max(0, min(127, round(g))), t - prev[0]))
            prev = (t, g)
        ev.append((prev[0], v, 0, 0, 2))                                  # 10 ms release
        for (ta, fa), (tb, fb) in zip(pitch, pitch[1:]):
            if abs(fb - fa) > 2:
                ev.append((ta, v, 1, round(fb), tb - ta))
    ev.sort(key=lambda x: (x[0], -x[2]))
    rows, now = [], 0
    for t, v, hz, to, n in ev:
        while t - now > 255:                        # a wait fits a byte: pad with a no-op ramp
            rows.append((255, 0x7E, 0, 0))
            now += 255
        n = min(n, 255)
        rows.append((t - now, (0x80 if v else 0) | hz, to, n))
        now = t
    end = max(b1 for b in busy for _, b1 in b)
    rows.append((min(max(end - now, 1), 255), 0x7F, 0, 0))
    return rows


def synth(rows):                                    # what bk_chime() plays, tick by tick
    g, gs, gn = [0.0, 0.0], [0.0, 0.0], [0, 0]
    f, fs, fn, ph = [0.0, 0.0], [0.0, 0.0], [0, 0], [0.0, 0.0]
    out, i, wait = [], 0, rows[0][0]
    while True:
        while i < len(rows) and wait == 0:
            w, op, to, n = rows[i]
            if op == 0x7F:
                break
            v = op >> 7
            if op == 0x7E:
                pass
            elif op & 1:
                if n: fs[v], fn[v] = (to - f[v]) / n, n
                else: f[v], fn[v] = to, 0
            else:
                if n: gs[v], gn[v] = (to - g[v]) / n, n
                else: g[v], gn[v] = to, 0
            i += 1
            wait = rows[i][0]
        if i < len(rows) and rows[i][1] == 0x7F and wait == 0:
            break
        t = np.arange(TICK) / RATE
        tick = np.zeros(TICK)
        for v in (0, 1):
            tick += int(g[v]) / 127 * np.sin(ph[v] + 2 * np.pi * f[v] * t)
            ph[v] += 2 * np.pi * f[v] * TICK / RATE
        out.append(tick * 0.45)
        for v in (0, 1):
            if gn[v]: g[v] += gs[v]; gn[v] -= 1
            if fn[v]: f[v] += fs[v]; fn[v] -= 1
        wait -= 1
    return np.concatenate(out) if out else np.zeros(1)


def save(path, a):
    with wave.open(path, 'wb') as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(RATE)
        w.writeframes((np.clip(a, -1, 1) * 32767).astype(np.int16).tobytes())


def c_table(name, rows, src):
    def one(w, op, to, n):
        if op in (0x7E, 0x7F):
            return '{%d, %s, 0, 0}' % (w, 'N_NOP' if op == 0x7E else 'N_END')
        return '{%d, %s%s, %d, %d}' % (w, 'N_V2 | ' if op & 0x80 else '', 'N_HZ' if op & 1 else 'N_GAIN', to, n)
    body = ',\n    '.join(', '.join(one(*r) for r in rows[i:i + 4]) for i in range(0, len(rows), 4))
    return 'const __code note_t %s[] = {   // %s, %d bytes\n    %s,\n};' % (name, src, 5 * len(rows), body)


def main():
    if sys.argv[1] == 'preview':
        a = load(sys.argv[2])
        rows = table(a)
        r = synth(rows)
        save(sys.argv[3], np.concatenate([a / max(abs(a).max(), 1e-3) * 0.45, np.zeros(RATE // 2), r]))
        print('%d steps, %d bytes, %.2f s' % (len(rows), 5 * len(rows), len(r) / RATE))
    else:
        name, path = sys.argv[2], sys.argv[3]
        rows = table(load(path, float(sys.argv[4]) if len(sys.argv) > 4 else None))
        print(c_table(name, rows, path.rsplit('/', 1)[-1]))


if __name__ == '__main__':
    main()
