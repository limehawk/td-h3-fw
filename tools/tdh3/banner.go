package main

import (
	"fmt"
	"image/color"
	"os"
	"strings"
	"time"

	"charm.land/fang/v2"
	"charm.land/lipgloss/v2"
	"github.com/charmbracelet/x/ansi"
)

// Classic theme colours, the radio's own (fw/main.c themes[0]).
var (
	amber = lipgloss.Color("#FFB000")
	grey  = lipgloss.Color("#787878")
	green = lipgloss.Color("#00DC50")
)

// The banner is the radio's boot screen (splash() in fw/main.c), drawn pixel for pixel on the top
// of the 128-wide LCD and shown in braille: two pixels a dot, 2x4 dots a cell. Each pixel keeps
// its part of the picture, and each part has one colour.
const lcdW, lcdH = 128, 82 // the mast, the rings and the ground; the name below them is drawn in dots (title)

type part uint8

const (
	pNone   part = iota
	pBrace       // the X braces inside the mast: left out, they make it read as solid
	pGround      // dim; also the mast not yet written while flashing
	pMast        // legs, rungs and antenna: grey
	pDone        // the mast once the flash is written: green
	pLit         // the mast written so far: amber
	pRingF       // a fading ring: dim
	pRing        // amber
	pTipOff      // the tip light between blinks: white
	pTip         // amber
	pName        // the name under the tower: white
)

var partS = map[part]lipgloss.Style{
	pGround: lipgloss.NewStyle().Foreground(lipgloss.Color("#4A4A4A")),
	pMast:   noteS,
	pDone:   okS,
	pLit:    ambS,
	pRingF:  lipgloss.NewStyle().Foreground(lipgloss.Color("#B07800")),
	pRing:   lipgloss.NewStyle().Foreground(amber),
	pTipOff: valS,
	pTip:    ambS,
	pName:   valS,
}

type lcd [lcdH][lcdW]part

func (l *lcd) px(x, y int, p part) {
	if x >= 0 && y >= 0 && x < lcdW && y < lcdH {
		l[y][x] = p
	}
}
func (l *lcd) fill(x, y, w, h int, p part) {
	for j := y; j < y+h; j++ {
		for i := x; i < x+w; i++ {
			l.px(i, j, p)
		}
	}
}
func (l *lcd) pair(d, y int, p part) { l.px(64-d, y, p); l.px(64+d, y, p) }

// ring is the radio's ring of radius r round the tip, ~40 degrees either side of level.
func (l *lcd) ring(r int, p part) {
	const tip = 7
	dx := r
	for dy := 0; dy <= 2*r/3; dy++ {
		for dx*dx+dy*dy > r*r {
			dx--
		}
		if dx < 64 {
			l.pair(dx, tip+dy, p)
			if dy <= tip {
				l.pair(dx, tip-dy, p)
			}
		}
	}
}

// splash draws the boot animation as the radio does, calling frame at each of its delay_ms()
// with the delay. It returns the picture as it ends.
func splash(frame func(*lcd, time.Duration)) *lcd {
	brace := [8]int{0, 3, 2, 1, 0, 1, 2, 3}
	l := &lcd{}
	l.fill(16, 80, 96, 1, pGround)
	for y := 79; y >= 16; y-- {
		w, j := (y-8)/6, y&7
		if j != 0 {
			l.pair(w*brace[j]>>2, y, pBrace)
		} else {
			l.fill(65-w, y, 2*w-1, 1, pMast)
		}
		l.pair(w, y, pMast)
		if j == 0 {
			frame(l, 25*time.Millisecond)
		}
	}
	l.fill(64, 9, 1, 7, pMast)
	for y := 0; y < 13; y++ {
		for j := 0; j <= 4; j += 4 {
			w := y - j
			if w < 0 || w > 8 {
				continue
			}
			if w > 0 {
				l.ring(w*11-3, pNone)
			}
			if w < 8 {
				p := pRing
				if w >= 3 {
					p = pRingF
				}
				l.ring(w*11+8, p)
			}
		}
		tip := pTip
		if y&2 != 0 {
			tip = pTipOff
		}
		l.fill(63, 6, 3, 3, tip)
		frame(l, 27*time.Millisecond)
	}
	return l
}

// still is the banner's resting picture: the end of the animation with its first two rings
// (radius 19 and 41: steps 1 and 3, both inside the picture) left in amber.
func still() *lcd {
	l := splash(func(*lcd, time.Duration) {})
	l.ring(19, pRing)
	l.ring(41, pRing)
	l.fill(63, 6, 3, 3, pTip)
	return l
}

// written is the tower as the flash progress: the mast amber from the ground up to f of its
// height (rows y 80 to 9), the rest dim, no rings and the tip dark. At f >= 1 the mast is green
// and the tip lit.
func written(f float64) *lcd {
	l := splash(func(*lcd, time.Duration) {})
	top := 81 - int(f*72+0.5) // the lowest unwritten row is top-1
	for y := range lcdH {
		for x := range lcdW {
			switch p := &l[y][x]; {
			case *p == pRing || *p == pRingF:
				*p = pNone
			case *p == pTip || *p == pTipOff:
				*p = pGround
				if f >= 1 {
					*p = pTip
				}
			case (*p == pMast || *p == pGround) && y >= top:
				*p = pLit
				if f >= 1 {
					*p = pDone
				}
			case *p == pMast:
				*p = pGround
			}
		}
	}
	return l
}

// dotX maps a pixel column to a dot column so the mast's centre line (pixel 64) falls between two
// cells and the picture mirrors cell for cell: pixel 64 lights both dots beside the line.
func dotX(x int) []int {
	switch d := x - 64; {
	case d == 0:
		return []int{lcdW/4 - 1, lcdW / 4}
	case d > 0:
		return []int{lcdW/4 + (d-1)/2}
	default:
		return []int{lcdW/4 - 1 - (-d-1)/2}
	}
}

// The screen: the picture down to the name (y 0-103) in cells, then a caption row, a blank row
// above and below it all and two blank columns either side, on the screen's dark grey: 36 x 16
// cells, as square as the radio's 128 x 128 LCD.
const bCols, bRows = lcdW / 4, 104 / 8
const scrW, scrRows = bCols + 4, bRows + 3

var scrBg = lipgloss.Color("#1A1A1A")

// title is the radio's "TD-H3" (fw/main.c splash(): x 34, y 88, the 5x7 font at scale 2) in dots,
// one dot a font pixel, so its strokes stay even across the centre line. Columns LSB-top, as in
// fw/lcd.c's font (the public glcdfont glyphs).
var title = [5][5]byte{
	{0x01, 0x01, 0x7F, 0x01, 0x01}, // T
	{0x7F, 0x41, 0x41, 0x22, 0x1C}, // D
	{0x08, 0x08, 0x08, 0x08, 0x08}, // -
	{0x7F, 0x08, 0x08, 0x08, 0x7F}, // H
	{0x21, 0x41, 0x45, 0x4B, 0x31}, // 3
}

// screen renders the picture as braille on the screen, the name under the tower when named, and
// caption centred on the row under that.
func (l *lcd) screen(named bool, caption string) []string {
	bits := [4][2]rune{{0x01, 0x08}, {0x02, 0x10}, {0x04, 0x20}, {0x40, 0x80}}
	var dot [bRows][bCols]rune
	var ink [bRows][bCols]part
	set := func(dx, dy int, p part) {
		cx, cy := dx/2, dy/4
		dot[cy][cx] |= bits[dy%4][dx%2]
		ink[cy][cx] = max(ink[cy][cx], p)
	}
	for y := range lcdH {
		for x := range lcdW {
			if p := l[y][x]; p != pNone && p != pBrace {
				for _, dx := range dotX(x) {
					set(dx, y/2, p)
				}
			}
		}
	}
	if named {
		for i, g := range title {
			for c, col := range g {
				for r := range 7 {
					if col>>r&1 != 0 {
						set(17+6*i+c, 44+r, pName)
					}
				}
			}
		}
	}
	bg := lipgloss.NewStyle().Background(scrBg)
	blank := bg.Render(strings.Repeat(" ", scrW))
	out := []string{blank}
	for cy := range bRows {
		var b strings.Builder
		b.WriteString(bg.Render("  "))
		for cx := range bCols {
			if dot[cy][cx] == 0 {
				b.WriteString(bg.Render(" "))
			} else {
				b.WriteString(partS[ink[cy][cx]].Background(scrBg).Render(string(0x2800 + dot[cy][cx])))
			}
		}
		b.WriteString(bg.Render("  "))
		out = append(out, b.String())
	}
	pad := scrW - ansi.StringWidth(caption)
	out = append(out, bg.Render(strings.Repeat(" ", pad/2))+caption+bg.Render(strings.Repeat(" ", pad-pad/2)), blank)
	for i := range out {
		out[i] = "  " + out[i]
	}
	return out
}

// classic is the radio's Classic theme for help and errors; fang's own is pink and purple.
func classic(c lipgloss.LightDarkFunc) fang.ColorScheme {
	s := fang.DefaultColorScheme(c)
	s.Title, s.Program, s.Command = amber, amber, amber
	s.Flag, s.QuotedString = green, green
	s.DimmedArgument, s.Comment, s.FlagDefault = grey, grey, grey
	s.Codeblock = c(lipgloss.Color("#EEEEEE"), lipgloss.Color("#2A2A2A"))
	s.ErrorHeader = [2]color.Color{lipgloss.Color("#000000"), amber}
	return s
}

// banner shows the radio's boot screen: animated on a terminal (~0.7 s, the radio's own timing),
// the still alone when piped; sub is its caption.
func banner(sub string) {
	fmt.Fprintln(os.Stderr)
	if isTerm(os.Stderr) {
		fmt.Fprint(os.Stderr, "\x1b[?25l") // no cursor flicker while it draws
		defer fmt.Fprint(os.Stderr, "\x1b[?25h")
		splash(func(l *lcd, d time.Duration) {
			fmt.Fprint(os.Stderr, strings.Join(l.screen(false, ""), "\n")+"\n\x1b["+fmt.Sprint(scrRows)+"A")
			time.Sleep(d)
		})
	}
	fmt.Fprintln(os.Stderr, strings.Join(still().screen(true, caption(ambS, sub)), "\n"))
	fmt.Fprintln(os.Stderr)
}

// caption renders text in st on the screen's background.
func caption(st lipgloss.Style, text string) string { return st.Background(scrBg).Render(text) }
