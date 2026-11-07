package main

import (
	"bytes"
	"image"
	"image/draw"
	"image/gif"
	"os"
	"strings"
	"testing"
	"time"

	"github.com/charmbracelet/x/ansi"
)

// The banner's picture is the radio's own boot frame: every pixel lit on the manual's boot.gif
// (made from the real firmware by tools/screens.py) is lit here, and nothing else.
func TestBannerIsTheRadiosBootFrame(t *testing.T) {
	f, err := os.Open("../../docs/manual/img/boot.gif")
	if err != nil {
		t.Fatal(err)
	}
	defer f.Close()
	g, err := gif.DecodeAll(f)
	if err != nil {
		t.Fatal(err)
	}
	img := image.NewRGBA(image.Rect(0, 0, g.Config.Width, g.Config.Height))
	for _, fr := range g.Image { // the frames only hold what changed
		draw.Draw(img, fr.Bounds(), fr, fr.Bounds().Min, draw.Over)
	}
	l := splash(func(*lcd, time.Duration) {})
	for y := range lcdH {
		for x := range lcdW {
			r, gg, b, _ := img.At(62+3*x+1, 10+3*y+1).RGBA() // tools/screens.py: 3x, 62 px margin, 10 px pad
			if lit := r|gg|b != 0; lit != (l[y][x] != pNone) {
				t.Fatalf("pixel %d,%d: radio lit %v, banner %v", x, y, lit, l[y][x])
			}
		}
	}
}

// The banner keeps fixed columns, and the picture mirrors about its centre line.
func TestBannerColumns(t *testing.T) {
	rows := still().screen(true, caption(ambS, "open firmware flasher"))
	if len(rows) != scrRows {
		t.Fatalf("%d rows, want %d", len(rows), scrRows)
	}
	for i, line := range rows {
		if w := ansi.StringWidth(line); w != scrW+2 {
			t.Fatalf("row %d is %d wide, want %d: %q", i, w, scrW+2, line)
		}
	}
	if name := ansi.Strip(rows[1+11]) + ansi.Strip(rows[1+12]); strings.TrimSpace(name) == "" {
		t.Fatal("no name under the tower")
	}
	l := still()
	for y := range lcdH - 2 { // not the ground: the radio draws it 16-111, which pairs up as dots anyway
		for d := 1; d < 64; d++ {
			if (l[y][64-d] != pNone) != (l[y][64+d] != pNone) {
				t.Fatalf("row %d not symmetric at ±%d", y, d)
			}
		}
	}
}

// While flashing, the tower fills from the ground up: count the cell rows with written (amber or
// green) mast in them, and check the tip lights only when it's all written.
func TestTowerFillsAsItWrites(t *testing.T) {
	for _, c := range []struct {
		f        float64
		min, max int
		tip      bool
	}{{0, 0, 0, false}, {0.5, 4, 6, false}, {1, 10, 10, true}} {
		l := written(c.f)
		rows, tip := map[int]bool{}, false
		for y := range lcdH {
			for x := range lcdW {
				switch l[y][x] {
				case pLit, pDone:
					rows[y/8] = true
				case pTip:
					tip = true
				case pRing, pRingF, pMast:
					t.Fatalf("%.0f%%: pixel %d,%d is %v while writing", c.f*100, x, y, l[y][x])
				}
			}
		}
		if len(rows) < c.min || len(rows) > c.max || tip != c.tip {
			t.Fatalf("%.0f%%: %d lit rows (want %d-%d), tip %v", c.f*100, len(rows), c.min, c.max, tip)
		}
		for i, line := range l.screen(true, caption(ambS, "writing")) {
			if w := ansi.StringWidth(line); w != scrW+2 {
				t.Fatalf("%.0f%% row %d is %d wide", c.f*100, i, w)
			}
		}
	}
	for y := range lcdH { // a written row is written all the way across
		if (written(0.5)[y][63] == pLit) != (written(0.5)[y][65] == pLit) {
			t.Fatalf("row %d lit on one side only", y)
		}
	}
}

// The live picture keeps to the window it started in: beside the screen, any number of steps
// stays the screen's height, and no line is wider than the window.
func TestLiveNeverScrolls(t *testing.T) {
	var buf bytes.Buffer
	out = &buf
	defer func() { out = os.Stderr }()
	u := &live{side: true, width: 100, room: scrRows - 1, since: time.Now()}
	for i := range 20 {
		u.add(okS.Render("[ok]") + " step " + strings.Repeat("x", i*5))
	}
	for _, st := range []stage{stBoot, stListen, stErase, stWrite, stDone, stIdle} {
		u.stage(st, caption(ambS, "writing 50%"))
		u.f = 0.5
		buf.Reset()
		u.draw()
		lines := strings.Split(strings.TrimSuffix(ansi.Strip(buf.String()), "\n"), "\n")
		if len(lines) != scrRows {
			t.Fatalf("stage %d: %d lines, want %d", st, len(lines), scrRows)
		}
		for _, l := range lines {
			if w := ansi.StringWidth(strings.TrimPrefix(strings.TrimPrefix(l, "\r"), "\x1b[2K")); w > u.width {
				t.Fatalf("stage %d: a line %d wide in a %d window", st, w, u.width)
			}
		}
	}
}
