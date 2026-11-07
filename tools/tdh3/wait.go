package main

import (
	"bytes"
	"fmt"
	"strings"
	"time"
)

// waitFor follows the screen as the radio draws it until want shows (gone: no longer shows) on
// it or in the status line in words, any case. 'Q' once a second keeps the radio mirroring (it
// stops 10 s after the last byte it got) and brings in the status line. timeout 0 waits for ever.
func waitFor(p port, want string, gone bool, timeout time.Duration) (*screen, error) {
	sc, start, low := &screen{}, time.Now(), strings.ToLower(want)
	shows := func() bool { return strings.Contains(strings.ToLower(sc.Text()+status(sc.query)), low) }
	var buf []byte
	synced := false
	p.Write([]byte("S"))
	tS, tQ, heard := start, start, start // the radio holds one byte: Q goes once S is answered
	// Judged once the status line is in (or 1.5 s after a redraw on screens that send none, the
	// menus), and only between draws: at a status line (the radio sends it between them) or when
	// the stream rests. Mid-redraw, text cleared and not yet drawn again would read as gone.
	done := func() bool {
		return (sc.query != "" || sc.end && time.Since(tS) > 1500*time.Millisecond) && shows() != gone
	}
	for timeout == 0 || time.Since(start) < timeout {
		if time.Since(tQ) >= time.Second {
			p.Write([]byte("Q"))
			tQ = time.Now()
		}
		got := readN(p, 1<<16, 50*time.Millisecond)
		if len(got) > 0 {
			heard = time.Now()
		}
		buf = append(buf, got...)
		if !synced {
			i := bytes.Index(buf, sync)
			if i < 0 {
				buf = buf[max(0, len(buf)-2):]
			} else {
				buf, synced, *sc = buf[i:], true, screen{}
				p.Write([]byte("Q"))
				tQ = time.Now()
			}
		}
		for synced {
			m := statusLine.FindIndex(buf)
			end := len(buf)
			if m != nil {
				end = m[1]
			}
			n, err := sc.feed(buf[:end])
			buf = buf[n:]
			if err != nil {
				synced = false // a lost byte: ask for the whole screen again
				break
			}
			if m == nil || n < end {
				break
			}
			if done() {
				return sc, nil
			}
		}
		if synced && len(got) == 0 && done() {
			return sc, nil
		}
		if (!synced || time.Since(heard) > 5*time.Second) && time.Since(tS) > 5*time.Second {
			p.Write([]byte("S"))
			tS, tQ, synced = time.Now(), time.Now(), false
		}
	}
	if !synced {
		return nil, fmt.Errorf("no answer: on the radio set MENU, REMOTE to ON")
	}
	if gone {
		return nil, fmt.Errorf("%q still showing after %s", want, timeout)
	}
	return nil, fmt.Errorf("%q did not show in %s", want, timeout)
}
