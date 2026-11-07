package main

import (
	"bytes"
	"strings"
	"testing"
)

func TestScreenFromMirror(t *testing.T) {
	var s []byte
	s = append(s, 'f', 0, 0, 128, 128, 0, 0)        // clear
	s = append(s, 't', 0, 14, 3, 0xFF, 0xFF, 0, 0)  // white on black, x3
	s = append(s, "146.520\x00"...)                 //
	s = append(s, 't', 0, 120, 1, 0xFF, 0xFF, 0, 0) // bottom line
	s = append(s, "FREQ 1\x00"...)                  //
	s = append(s, 'f', 0, 120, 128, 8, 0, 0)        // bottom line cleared
	s = append(s, 't', 0, 120, 1, 0xFF, 0xFF, 0, 0) //
	s = append(s, "FREQ 14\x00"...)                 //
	s = append(s, "q146.520 -112 O S 7400\n"...)    //
	s = append(s, 't', 0, 0, 1, 0xFF, 0xFF, 0, 0)   // label, then the slot letter over it
	s = append(s, "A 010*SHARP\x00"...)             //
	s = append(s, 't', 0, 0, 1, 0x53, 0x5F, 0, 0)   //
	s = append(s, "A\x00"...)                       //
	s = append(s, 't', 120, 0, 1, 0, 0, 0, 0)       // would cross the edge: drawn as nothing
	s = append(s, "XY\x00"...)                      //
	sc := &screen{}
	if err := sc.apply(append(append([]byte{}, sync...), s...)); err != nil {
		t.Fatal(err)
	}
	got := sc.Text()
	if !strings.Contains(got, "  0   0  A 010*SHARP") || !strings.Contains(got, " 14   0  146.520  (x3)") || !strings.Contains(got, "FREQ 14") || strings.Contains(got, "FREQ 1\n") || strings.Contains(got, "XY") {
		t.Fatalf("text:\n%s", got)
	}
	if sc.px[14][6] != 0xFFFF || sc.px[14][0] != 0 { // '1' at x3: column 2 lit, column 0 dark
		t.Fatal("pixels not drawn")
	}
	if status(sc.query) != "146.520 MHz  -112 dBm  squelch open  speaker on  battery 7.40 V" {
		t.Fatalf("status %q", status(sc.query))
	}
	var png bytes.Buffer
	if err := sc.PNG(&png); err != nil || !bytes.HasPrefix(png.Bytes(), []byte("\x89PNG")) {
		t.Fatal("png", err)
	}
}

func TestKeysNeverSendPTT(t *testing.T) {
	for _, k := range []string{"PTT", "SIDE1", "PTTB"} {
		if _, ok := keyCodes[k]; ok {
			t.Fatalf("%s must not be pressable", k)
		}
	}
	for _, c := range keyCodes {
		if c == 17 || c == 18 {
			t.Fatalf("code %d is PTT/PTT-B", c)
		}
	}
}

func TestStatusWithVersion(t *testing.T) {
	if s := status("146.520 -112 O S 7400 0.20.0"); s != "146.520 MHz  -112 dBm  squelch open  speaker on  battery 7.40 V  firmware 0.20.0" {
		t.Fatalf("status %q", s)
	}
	for in, want := range map[string]string{
		"xxq146.520 -112 O S 7400 0.20.0-3-gabc\n": "0.20.0-3-gabc", // 0.20.1 on
		"q146.520 -080 C -  0.20.0\n":              "0.20.0",        // 0.20.0: no battery digits
		"q146.520 -112 O S 7400\n":                 "older than 0.20",
	} {
		if v, ok := statusVersion([]byte(in)); !ok || v != want {
			t.Fatalf("%q: %q, %v", in, v, ok)
		}
	}
	if _, ok := statusVersion([]byte("t\x00\x09garbage\n")); ok {
		t.Fatal("no status line read as one")
	}
}

func TestLitIsInverseVideo(t *testing.T) {
	var s []byte
	s = append(s, 't', 0, 27, 1, 0xFF, 0xFF, 0x1A, 0x00) // a heard slot's label: white on its green tint
	s = append(s, "*M20 \x00"...)
	s = append(s, 't', 1, 14, 1, 0x1A, 0x00, 0x05, 0x9F) // its selected letter: tint on amber
	s = append(s, "A\x00"...)
	sc := &screen{}
	if err := sc.apply(append(append([]byte{}, sync...), s...)); err != nil {
		t.Fatal(err)
	}
	if l, lit := sc.at(27, 0); l != "*M20" || lit {
		t.Fatalf("label %q lit %v", l, lit)
	}
	if _, lit := sc.at(14, 1); !lit {
		t.Fatal("the selected letter not lit")
	}
}
