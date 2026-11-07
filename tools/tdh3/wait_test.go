package main

import (
	"strings"
	"testing"
	"time"
)

// A radio on the home screen: 'S' redraws 146.520, each 'Q' answers the next status line.
func waitRadio(qs ...string) *fake {
	return &fake{reply: func(w []byte) []byte {
		switch w[0] {
		case 'S':
			r := append([]byte{}, sync...)
			r = append(r, 'f', 0, 0, 128, 128, 0, 0)
			r = append(r, 't', 32, 23, 2, 0xFF, 0xFF, 0, 0)
			return append(append(r, "146.520\x00"...), 'E')
		case 'Q':
			if len(qs) == 0 {
				return nil
			}
			q := "q" + qs[0] + "\n"
			qs = qs[1:]
			return []byte(q)
		}
		return nil
	}}
}

func TestWaitForScreenText(t *testing.T) {
	sc, err := waitFor(waitRadio(), "146.52", false, 2*time.Second)
	if err != nil || !strings.Contains(sc.Text(), "146.520") {
		t.Fatal(sc, err)
	}
}

func TestWaitForStatusLine(t *testing.T) {
	f := waitRadio("146.520 -130 C S 7400", "146.520 -080 O S 7400")
	sc, err := waitFor(f, "Squelch OPEN", false, 5*time.Second)
	if err != nil || sc.query != "146.520 -080 O S 7400" {
		t.Fatal(sc, err)
	}
	sc, err = waitFor(waitRadio("146.520 -080 O S 7400", "146.520 -130 C S 7400"), "squelch open", true, 5*time.Second)
	if err != nil || !strings.Contains(sc.query, " C ") {
		t.Fatal(sc, err)
	}
}

func TestWaitTimesOut(t *testing.T) {
	if _, err := waitFor(waitRadio(), "162.550", false, 300*time.Millisecond); err == nil || !strings.Contains(err.Error(), "did not show") {
		t.Fatal(err)
	}
	if _, err := waitFor(&fake{reply: func([]byte) []byte { return nil }}, "x", false, 300*time.Millisecond); err == nil || !strings.Contains(err.Error(), "REMOTE") {
		t.Fatal(err)
	}
}
