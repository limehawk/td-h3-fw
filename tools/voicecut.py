#!/usr/bin/env python3
"""Cut a microphone recording of tools/voicedump.py into one clip per prompt, and say which had sound.

    pw-record --rate 16000 --channels 1 --format s16 rec.wav &   # started with its time noted:
    date +%s.%N > rec.start
    python3 tools/voicedump.py 0 255 | tee dump.log
    python3 tools/voicecut.py rec.wav rec.start dump.log OUTDIR

Prints per prompt: loudness over the room's noise (dB), how long it sounded, the strongest pitch.
"""
import os
import sys
import wave

import numpy as np


def main():
    wav, start, log, out = sys.argv[1:5]
    w = wave.open(wav)
    rate = w.getframerate()
    a = np.frombuffer(w.readframes(w.getnframes()), np.int16).astype(float)
    t0 = float(open(start).read())
    rows = [l.split() for l in open(log) if l.strip() and l.split()[0].isdigit()]
    times = [(int(r[0]), float(r[2])) for r in rows]
    floor = np.percentile(np.abs(a), 50) + 1
    os.makedirs(out, exist_ok=True)
    for i, (n, t) in enumerate(times):
        end = times[i + 1][1] if i + 1 < len(times) else t + 3.5
        s, e = int((t - t0) * rate), int((end - t0) * rate)
        clip = a[max(s, 0):min(e, len(a))]
        if not len(clip):
            continue
        frames = clip[: len(clip) // 160 * 160].reshape(-1, 160)          # 10 ms
        rms = np.sqrt((frames ** 2).mean(1))
        loud = rms > 4 * floor
        db = 20 * np.log10(rms.max() / floor)
        spec = np.abs(np.fft.rfft(clip * np.hanning(len(clip))))
        hz = np.fft.rfftfreq(len(clip), 1 / rate)[spec[1:].argmax() + 1]
        with wave.open(os.path.join(out, '%03d.wav' % n), 'wb') as o:
            o.setnchannels(1); o.setsampwidth(2); o.setframerate(rate)
            o.writeframes(clip.astype(np.int16).tobytes())
        print('%3d  %5.1f dB  %4.2f s  %5.0f Hz  %s' % (n, db, loud.sum() / 100, hz, 'SOUND' if loud.sum() > 5 else ''))


if __name__ == '__main__':
    main()
