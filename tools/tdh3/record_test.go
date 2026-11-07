package main

import (
	"bytes"
	"image/gif"
	"strings"
	"testing"
	"time"
)

// A recording replays to the screen the radio drew, one GIF frame per change, with its timing.
func TestReplayRecording(t *testing.T) {
	var s []byte
	s = append(s, "noise"...)                       // left over from before the sync
	s = append(s, sync...)                          //
	s = append(s, 'f', 0, 0, 128, 128, 0, 0)        // clear
	s = append(s, 't', 0, 14, 3, 0xFF, 0xFF, 0, 0)  //
	s = append(s, "146.520\x00E"...)                // redraw done
	s = append(s, "q146.520 -112 O S 7400\n"...)    // the status line, from a 'Q'
	s = append(s, 't', 0, 120, 1, 0xFF, 0xFF, 0, 0) // a key typed later
	s = append(s, "FREQ 1\x00"...)                  //

	var rec bytes.Buffer
	rec.WriteString(recMagic)
	at := []int{0, 10, 60} // the stream as it came: split mid-record, in three pieces
	cut := []int{0, 20, len(s) - 15, len(s)}
	for i, ms := range at {
		writeChunk(&rec, time.Duration(ms)*time.Millisecond, s[cut[i]:cut[i+1]])
	}
	writeChunk(&rec, 2*time.Second, nil)

	cs, err := readRecording(rec.Bytes())
	if err != nil || len(cs) != 4 {
		t.Fatal(len(cs), err)
	}
	var shots []shot
	sc := replay(cs, func(t time.Duration, sc *screen) { shots = append(shots, shot{t, sc.px}) })
	if got := sc.Text(); !strings.Contains(got, " 14   0  146.520  (x3)") || !strings.Contains(got, "FREQ 1") {
		t.Fatalf("text:\n%s", got)
	}
	if status(sc.query) != "146.520 MHz  -112 dBm  squelch open  speaker on  battery 7.40 V" {
		t.Fatalf("status %q", sc.query)
	}

	var out bytes.Buffer
	if err := gif.EncodeAll(&out, toGIF(shots, cs[len(cs)-1].t)); err != nil {
		t.Fatal(err)
	}
	g, err := gif.DecodeAll(&out)
	if err != nil {
		t.Fatal(err)
	}
	if len(g.Image) != 2 || g.Delay[0] != 5 || g.Delay[1] != 194 { // the first piece only clears a black screen: no frame
		t.Fatalf("%d frames, delays %v", len(g.Image), g.Delay)
	}
	if b := g.Image[1].Bounds(); b.Dx() != 512 || b.Dy() != 512 {
		t.Fatal("size", b)
	}
	if _, err := readRecording([]byte("PNG")); err == nil {
		t.Fatal("not a recording, read as one")
	}
}
