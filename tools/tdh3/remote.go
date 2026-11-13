package main

// Remote control of td-h3-fw over USB-C (on the radio: MENU, REMOTE, UP to turn it on). The
// radio copies every LCD draw to the port and takes key presses; it refuses PTT and PTT-B, so
// nothing here can make it transmit. Protocol: fw/remote.c.

import (
	"bytes"
	"errors"
	"fmt"
	"image"
	"image/color"
	"image/png"
	"io"
	"slices"
	"sort"
	"strconv"
	"strings"
	"time"

	"go.bug.st/serial"
)

// keyCodes are fw/fw.h K_* values. PTT and SIDE1 (PTT-B) are left out on purpose.
var keyCodes = map[string]byte{
	"0": 1, "1": 2, "2": 3, "3": 4, "4": 5, "5": 6, "6": 7, "7": 8, "8": 9, "9": 10,
	"MENU": 11, "UP": 12, "DOWN": 13, "VM": 14, "EXIT": 14, "*": 15, "STAR": 15, "#": 16, "HASH": 16,
	"SIDE2": 19, "TORCH": 19,
}

var sync = []byte{'S', 0xA5, 0x5A}

type text struct {
	x, y, scale int
	s           string
	inv         bool // the background brighter than the text; lit (sc.lit) is the minority way round
}

// screen is what the radio's panel shows, rebuilt from the mirrored draws.
type screen struct {
	px    [128][128]uint16
	texts []text
	query string // last status line ('q' record)
	end   bool   // the radio sent 'E': the redraw is complete
}

func overlaps(t text, x, y, w, h int) bool {
	tw, th := 6*t.scale*len(t.s), 8*t.scale
	return t.x < x+w && x < t.x+tw && t.y < y+h && y < t.y+th
}

func (sc *screen) drop(x, y, w, h int) {
	keep := sc.texts[:0]
	for _, t := range sc.texts {
		if !overlaps(t, x, y, w, h) {
			keep = append(keep, t)
		}
	}
	sc.texts = keep
}

func (sc *screen) fill(x, y, w, h int, c uint16) {
	for j := y; j < min(y+h, 128); j++ {
		for i := x; i < min(x+w, 128); i++ {
			sc.px[j][i] = c
		}
	}
	sc.drop(x, y, w, h)
}

// draw follows lcd_text: 6x8 cells at scale, stops before a cell would cross the right edge.
func (sc *screen) draw(x, y, scale int, s string, fg, bg uint16) {
	cw, n := 6*scale, 0
	for ; n < len(s) && x+(n+1)*cw <= 128; n++ {
		ch := s[n]
		if ch < 0x20 || ch > 0x80 {
			ch = '?'
		}
		f := font[ch-0x20]
		for row := 0; row < 8; row++ {
			for col := 0; col < 6; col++ {
				c := bg
				if col < 5 && f[col]&(1<<row) != 0 {
					c = fg
				}
				for dy := 0; dy < scale; dy++ {
					for dx := 0; dx < scale; dx++ {
						if py := y + row*scale + dy; py < 128 {
							sc.px[py][x+n*cw+col*scale+dx] = c
						}
					}
				}
			}
		}
	}
	t := text{x, y, scale, s[:n], luma(bg) > luma(fg)}
	for i, o := range sc.texts { // same row, same size, on the character grid: overlay, don't drop
		if o.y == y && o.scale == scale && (x-o.x)%cw == 0 && x >= o.x && x < o.x+cw*len(o.s) {
			at := (x - o.x) / cw
			r := []byte(o.s)
			for len(r) < at+n {
				r = append(r, ' ')
			}
			copy(r[at:], t.s)
			sc.texts[i].s = string(r)
			return
		}
	}
	sc.drop(t.x, t.y, cw*n, 8*scale)
	if strings.TrimSpace(t.s) != "" {
		sc.texts = append(sc.texts, t)
	}
}

// luma is a BGR565 colour's brightness (Rec. 601 weights, 8-bit channels).
func luma(c uint16) int {
	b, g, r := int(c>>11)<<3, int(c>>5&63)<<2, int(c&31)<<3
	return 299*r + 587*g + 114*b
}

// apply parses records starting right after a sync. It stops at the first incomplete one.
func (sc *screen) apply(b []byte) error { _, err := sc.feed(b); return err }

// feed is apply that also says how many bytes it used: a stream's next read carries on from there.
func (sc *screen) feed(in []byte) (int, error) {
	b := in
	for len(b) > 0 {
		switch b[0] {
		case 'f':
			if len(b) < 7 {
				return len(in) - len(b), nil
			}
			sc.fill(int(b[1]), int(b[2]), int(b[3]), int(b[4]), uint16(b[5])<<8|uint16(b[6]))
			b = b[7:]
		case 't':
			end := bytes.IndexByte(b[min(len(b), 8):], 0)
			if len(b) < 8 || end < 0 {
				return len(in) - len(b), nil
			}
			sc.draw(int(b[1]), int(b[2]), int(b[3]), string(b[8:8+end]),
				uint16(b[4])<<8|uint16(b[5]), uint16(b[6])<<8|uint16(b[7]))
			b = b[9+end:]
		case 'q':
			end := bytes.IndexByte(b, '\n')
			if end < 0 {
				return len(in) - len(b), nil
			}
			sc.query = string(b[1:end])
			b = b[end+1:]
		case 'S':
			if len(b) < 3 {
				return len(in) - len(b), nil
			}
			b = b[3:]
		case 'E': // the radio finished the redraw (firmware 0.16+)
			sc.end = true
			b = b[1:]
		default:
			return len(in) - len(b), fmt.Errorf("lost track of the screen stream at 0x%02X: run it again", b[0])
		}
	}
	return len(in), nil
}

// Text lists what is written on the screen, top to bottom.
func (sc *screen) Text() string {
	t := append([]text{}, sc.texts...)
	sort.SliceStable(t, func(i, j int) bool { return t[i].y < t[j].y || t[i].y == t[j].y && t[i].x < t[j].x })
	var out strings.Builder
	for _, x := range t {
		size := ""
		if x.scale > 1 {
			size = fmt.Sprintf("  (x%d)", x.scale)
		}
		if sc.lit(x) {
			size += "  (lit)"
		}
		s := strings.NewReplacer("\x7f", "▲", "\x80", "▼").Replace(strings.TrimRight(x.s, " ")) // the font's arrows
		fmt.Fprintf(&out, "%3d %3d  %s%s\n", x.y, x.x, s, size)
	}
	return out.String()
}

// PNG renders the panel 4x, red and blue swapped back (the panel is BGR).
func (sc *screen) PNG(w io.Writer) error {
	const k = 4
	img := image.NewRGBA(image.Rect(0, 0, 128*k, 128*k))
	for y := range 128 {
		for x := range 128 {
			for i := range k * k {
				img.Set(x*k+i%k, y*k+i/k, rgb(sc.px[y][x]))
			}
		}
	}
	return png.Encode(w, img)
}

// rgb is a BGR565 panel colour as RGB.
func rgb(v uint16) color.RGBA {
	return color.RGBA{uint8(v & 31 << 3), uint8(v >> 5 & 63 << 2), uint8(v >> 11 << 3), 255}
}

// remoteBauds are REMOTE's speeds, newest first: 1 Mbaud, then 115200 and 38400 for older td-h3-fw.
var remoteBauds = []int{1000000, 115200, 38400}

// keyHold and keyGap time a press. Older td-h3-fw keeps one byte and loses one sent while it's
// busy, so a press is held 150 ms and the next waits 250 ms. At 1 Mbaud (v0.61 on) the radio keeps
// 16 and reads no release before it has registered the press, so a short press does.
var keyHold, keyGap = 150 * time.Millisecond, 250 * time.Millisecond

// openRemote opens the port at REMOTE's speed. A sync ('S', answered on every screen) picks the
// one the radio answers; at the wrong speed a byte reads as noise there, which is no key. None:
// 1 Mbaud, and the command says no answer.
func openRemote(name string) (serial.Port, error) {
	for _, baud := range remoteBauds {
		p, err := open(name, baud)
		if err != nil {
			return nil, err
		}
		p.Write([]byte("S"))
		if readUntil(p, sync, 400*time.Millisecond) {
			if baud == remoteBauds[0] {
				keyHold, keyGap = 40*time.Millisecond, 60*time.Millisecond
			}
			return p, nil
		}
		p.Close()
	}
	return open(name, remoteBauds[0])
}

// readUntil reads until pat has come in, or d has passed: false then.
func readUntil(p port, pat []byte, d time.Duration) bool {
	var got []byte
	for end := time.Now().Add(d); time.Now().Before(end); {
		if got = append(got, readN(p, 1<<16, 20*time.Millisecond)...); bytes.Contains(got, pat) {
			return true
		}
	}
	return false
}

// look asks for a redraw (and a status line) and rebuilds the screen from what comes back.
func look(p port, wait time.Duration) (*screen, error) {
	sc, _, err := lookRest(p, wait)
	return sc, err
}

// lookRest is look, and also returns the start of a record still coming in after the screen.
func lookRest(p port, wait time.Duration) (*screen, []byte, error) {
	readN(p, 1<<16, 100*time.Millisecond) // drop whatever was mid-flight
	p.Write([]byte("S"))
	// Older firmware holds one byte and reads it when its loop gets round to it: a byte sent before it
	// took the last is lost (a busy radio, a call coming in, lost 'Q's this way); newer keeps 16. So
	// 'Q' goes once 'S' is answered, and again if the redraw ends without the status line.
	// Done as soon as the redraw's 'E' and the status line are both in; older firmware sends no
	// 'E', and the screens without one (FM, scope) neither, so `wait` stays the upper limit.
	var got []byte
	var tQ time.Time
	resent, sc := false, &screen{} // one screen (32 KB of pixels), rebuilt each pass
	for end := time.Now().Add(wait); time.Now().Before(end); {
		got = append(got, readN(p, 1<<16, 50*time.Millisecond)...)
		i := bytes.Index(got, sync)
		if i < 0 {
			continue
		}
		if tQ.IsZero() {
			p.Write([]byte("Q"))
			tQ = time.Now()
		}
		*sc = screen{}
		if n, err := sc.feed(got[i:]); err == nil && sc.end {
			if sc.query != "" {
				return sc, got[i+n:], nil
			}
			if !resent && time.Since(tQ) > 400*time.Millisecond {
				p.Write([]byte("Q"))
				resent = true
			}
		}
	}
	i := bytes.Index(got, sync)
	if i < 0 {
		return nil, nil, fmt.Errorf("no answer (%d bytes): on the radio set MENU, REMOTE to ON", len(got))
	}
	*sc = screen{}
	n, err := sc.feed(got[i:])
	return sc, got[i+n:], err
}

// press holds each key, then lets go. "MENU:long" holds it past the 0.8 s long press.
func press(p port, keys []string) error {
	for _, k := range keys {
		name, long := strings.CutSuffix(strings.ToUpper(k), ":LONG")
		code, ok := keyCodes[name]
		if !ok {
			return fmt.Errorf("unknown key %q (0-9 MENU UP DOWN VM * # TORCH; PTT is never sent)", k)
		}
		hold := keyHold
		if long {
			hold = 1200 * time.Millisecond
		}
		p.Write([]byte{0x80 | code})
		for end := time.Now().Add(hold); time.Now().Before(end); {
			time.Sleep(min(100*time.Millisecond, time.Until(end)))
			if time.Now().Before(end) {
				p.Write([]byte{0x80 | code}) // still held: from 0.22.3 the radio lets go after 300 ms of nothing
			}
		}
		p.Write([]byte{0x80})
		time.Sleep(keyGap / 6)
		p.Write([]byte{0x80}) // twice: a lost release is a held key
		time.Sleep(keyGap - keyGap/6)
	}
	return nil
}

// status turns "146.520 -112 O S 7400" (and from 0.20 the version after it) into words.
func status(q string) string {
	f := strings.Fields(q)
	if len(f) != 5 && len(f) != 6 {
		return q
	}
	sq, spk := "closed", "off"
	if f[2] == "O" {
		sq = "open"
	}
	if f[3] == "S" {
		spk = "on"
	}
	var mv int
	fmt.Sscan(f[4], &mv)
	s := fmt.Sprintf("%s MHz  %s dBm  squelch %s  speaker %s  battery %d.%02d V", f[0], f[1], sq, spk, mv/1000, mv%1000/10)
	if len(f) == 6 {
		s += "  firmware " + f[5]
	}
	return s
}

// font is fw/lcd.c's 5x7 font (0x20-0x7E, columns LSB-top), copied so screenshots match the glass.
var font = [97][5]byte{
	{0x00, 0x00, 0x00, 0x00, 0x00}, {0x00, 0x00, 0x5F, 0x00, 0x00}, {0x00, 0x07, 0x00, 0x07, 0x00}, {0x14, 0x7F, 0x14, 0x7F, 0x14}, {0x24, 0x2A, 0x7F, 0x2A, 0x12}, {0x23, 0x13, 0x08, 0x64, 0x62},
	{0x36, 0x49, 0x55, 0x22, 0x50}, {0x00, 0x05, 0x03, 0x00, 0x00}, {0x00, 0x1C, 0x22, 0x41, 0x00}, {0x00, 0x41, 0x22, 0x1C, 0x00}, {0x08, 0x2A, 0x1C, 0x2A, 0x08}, {0x08, 0x08, 0x3E, 0x08, 0x08},
	{0x00, 0x50, 0x30, 0x00, 0x00}, {0x08, 0x08, 0x08, 0x08, 0x08}, {0x00, 0x60, 0x60, 0x00, 0x00}, {0x20, 0x10, 0x08, 0x04, 0x02}, {0x3E, 0x51, 0x49, 0x45, 0x3E}, {0x00, 0x42, 0x7F, 0x40, 0x00},
	{0x42, 0x61, 0x51, 0x49, 0x46}, {0x21, 0x41, 0x45, 0x4B, 0x31}, {0x18, 0x14, 0x12, 0x7F, 0x10}, {0x27, 0x45, 0x45, 0x45, 0x39}, {0x3C, 0x4A, 0x49, 0x49, 0x30}, {0x01, 0x71, 0x09, 0x05, 0x03},
	{0x36, 0x49, 0x49, 0x49, 0x36}, {0x06, 0x49, 0x49, 0x29, 0x1E}, {0x00, 0x36, 0x36, 0x00, 0x00}, {0x00, 0x56, 0x36, 0x00, 0x00}, {0x08, 0x14, 0x22, 0x41, 0x00}, {0x14, 0x14, 0x14, 0x14, 0x14},
	{0x00, 0x41, 0x22, 0x14, 0x08}, {0x02, 0x01, 0x51, 0x09, 0x06}, {0x32, 0x49, 0x79, 0x41, 0x3E}, {0x7E, 0x11, 0x11, 0x11, 0x7E}, {0x7F, 0x49, 0x49, 0x49, 0x36}, {0x3E, 0x41, 0x41, 0x41, 0x22},
	{0x7F, 0x41, 0x41, 0x22, 0x1C}, {0x7F, 0x49, 0x49, 0x49, 0x41}, {0x7F, 0x09, 0x09, 0x01, 0x01}, {0x3E, 0x41, 0x41, 0x51, 0x32}, {0x7F, 0x08, 0x08, 0x08, 0x7F}, {0x00, 0x41, 0x7F, 0x41, 0x00},
	{0x20, 0x40, 0x41, 0x3F, 0x01}, {0x7F, 0x08, 0x14, 0x22, 0x41}, {0x7F, 0x40, 0x40, 0x40, 0x40}, {0x7F, 0x02, 0x04, 0x02, 0x7F}, {0x7F, 0x04, 0x08, 0x10, 0x7F}, {0x3E, 0x41, 0x41, 0x41, 0x3E},
	{0x7F, 0x09, 0x09, 0x09, 0x06}, {0x3E, 0x41, 0x51, 0x21, 0x5E}, {0x7F, 0x09, 0x19, 0x29, 0x46}, {0x46, 0x49, 0x49, 0x49, 0x31}, {0x01, 0x01, 0x7F, 0x01, 0x01}, {0x3F, 0x40, 0x40, 0x40, 0x3F},
	{0x1F, 0x20, 0x40, 0x20, 0x1F}, {0x7F, 0x20, 0x18, 0x20, 0x7F}, {0x63, 0x14, 0x08, 0x14, 0x63}, {0x03, 0x04, 0x78, 0x04, 0x03}, {0x61, 0x51, 0x49, 0x45, 0x43}, {0x00, 0x7F, 0x41, 0x41, 0x00},
	{0x02, 0x04, 0x08, 0x10, 0x20}, {0x00, 0x41, 0x41, 0x7F, 0x00}, {0x04, 0x02, 0x01, 0x02, 0x04}, {0x40, 0x40, 0x40, 0x40, 0x40}, {0x00, 0x01, 0x02, 0x04, 0x00}, {0x20, 0x54, 0x54, 0x54, 0x78},
	{0x7F, 0x48, 0x44, 0x44, 0x38}, {0x38, 0x44, 0x44, 0x44, 0x20}, {0x38, 0x44, 0x44, 0x48, 0x7F}, {0x38, 0x54, 0x54, 0x54, 0x18}, {0x08, 0x7E, 0x09, 0x01, 0x02}, {0x08, 0x14, 0x54, 0x54, 0x3C},
	{0x7F, 0x08, 0x04, 0x04, 0x78}, {0x00, 0x44, 0x7D, 0x40, 0x00}, {0x20, 0x40, 0x44, 0x3D, 0x00}, {0x00, 0x7F, 0x10, 0x28, 0x44}, {0x00, 0x41, 0x7F, 0x40, 0x00}, {0x7C, 0x04, 0x18, 0x04, 0x78},
	{0x7C, 0x08, 0x04, 0x04, 0x78}, {0x38, 0x44, 0x44, 0x44, 0x38}, {0x7C, 0x14, 0x14, 0x14, 0x08}, {0x08, 0x14, 0x14, 0x18, 0x7C}, {0x7C, 0x08, 0x04, 0x04, 0x08}, {0x48, 0x54, 0x54, 0x54, 0x20},
	{0x04, 0x3F, 0x44, 0x40, 0x20}, {0x3C, 0x40, 0x40, 0x20, 0x7C}, {0x1C, 0x20, 0x40, 0x20, 0x1C}, {0x3C, 0x40, 0x30, 0x40, 0x3C}, {0x44, 0x28, 0x10, 0x28, 0x44}, {0x0C, 0x50, 0x50, 0x50, 0x3C},
	{0x44, 0x64, 0x54, 0x4C, 0x44}, {0x00, 0x08, 0x36, 0x41, 0x00}, {0x00, 0x00, 0x7F, 0x00, 0x00}, {0x00, 0x41, 0x36, 0x08, 0x00}, {0x02, 0x01, 0x02, 0x04, 0x02},
	{0x30, 0x38, 0x3E, 0x38, 0x30}, {0x06, 0x0E, 0x3E, 0x0E, 0x06}, // 0x7F up, 0x80 down triangles
}

// rig drives the radio a step at a time and reads the screen back after every key, as
// tools/radio.py does: a dropped or doubled key stops the command, it never tunes the wrong place.
//
// After the first full read the screen is kept up to date from the draws the radio mirrors while
// the computer talks, so a key costs the few records it redraws, not a whole new screen (a menu
// at 115200 takes 200 ms and more). A check that fails on the kept screen reads the whole screen
// once more before it counts (sure): a missed draw is never taken for a lost key.
type rig struct {
	p    port
	wait time.Duration // how long a screen read waits for the redraw
	sc   *screen       // the screen as the mirrored draws left it; nil before the first look
	rest []byte        // the start of a record still coming in
}

func (r *rig) look() (sc *screen, err error) {
	sc, r.rest, err = lookRest(r.p, r.wait)
	r.sc = sc
	return sc, err
}

// settle reads the draws a key set off until ok holds on the kept screen or the radio has been
// quiet for 60 ms; a screen that never goes quiet (the S-meter redraws every 100 ms, the scope)
// is kept up to date regardless, so 600 ms caps it.
func (r *rig) settle(ok func(*screen) bool) (*screen, error) {
	for end := time.Now().Add(600 * time.Millisecond); time.Now().Before(end); {
		b := readN(r.p, 1<<16, 60*time.Millisecond)
		if len(b) == 0 {
			break
		}
		b = append(r.rest, b...)
		n, err := r.sc.feed(b)
		if err != nil {
			return r.look() // lost track of the stream: read it whole
		}
		r.rest = b[n:]
		if ok(r.sc) {
			break
		}
	}
	return r.sc, nil
}

// key presses k and settles on ok, the check the caller makes next.
func (r *rig) key(k string, ok func(*screen) bool) (*screen, error) {
	if r.sc == nil {
		if _, err := r.look(); err != nil {
			return nil, err
		}
	}
	if err := press(r.p, []string{k}); err != nil {
		return nil, err
	}
	return r.settle(ok)
}

// sure is sc if ok passes on it, else the whole screen read again.
func (r *rig) sure(sc *screen, ok func(*screen) bool) (*screen, error) {
	if ok(sc) {
		return sc, nil
	}
	return r.look()
}

// lit: inverse video against the theme, the selected slot's letter or a list's cursor. Most text runs
// the theme's way round (light on dark, or dark on light on a light theme); lit text runs the other.
func (sc *screen) lit(t text) bool {
	n := 0
	for _, o := range sc.texts {
		if o.inv {
			n++
		}
	}
	return t.inv != (2*n > len(sc.texts))
}

// at is the text drawn at (y, x) and whether it is lit.
func (sc *screen) at(y, x int) (string, bool) {
	for _, t := range sc.texts {
		if t.y == y && t.x == x {
			return strings.TrimRight(t.s, " "), sc.lit(t)
		}
	}
	return "", false
}

// The home screen (draw_main): slot A at y 14, B at y 73. Each has its letter at x 1 (lit when
// selected), the frequency 2x at y+9 x 32 (as REMOTE sends it in every DIGITS look) and "VFO" or the
// memory ("*M20") at y+13 x 0, or with DIGITS ARCADE and LCD at the right end of the slot's top row.
// Typed digits show on the bottom line, y 118.
var slotY = map[string]int{"A": 14, "B": 73}

func isHome(sc *screen) bool {
	a, _ := sc.at(23, 32)
	b, _ := sc.at(82, 32)
	return a != "" && b != ""
}

func slotOf(sc *screen) string {
	for _, s := range []string{"A", "B"} {
		if _, lit := sc.at(slotY[s], 1); lit {
			return s
		}
	}
	return ""
}

func (sc *screen) bottom() string { s, _ := sc.at(120, 0); return s } // the firmware's BOT_Y

// mem is the selected slot's memory number, "" in VFO.
func (sc *screen) mem() string {
	s := sc.label(slotY[slotOf(sc)])
	if n := strings.TrimLeft(s, "*"); strings.HasPrefix(n, "M") {
		return n[1:]
	}
	return ""
}

// label is the slot at y's "VFO" or memory number, wherever DIGITS puts it.
func (sc *screen) label(y int) string {
	if s, _ := sc.at(y+13, 0); s != "" {
		return s
	}
	for _, t := range sc.texts {
		if t.y == y && t.scale == 1 && t.x+6*len(t.s) == 128 {
			return strings.TrimRight(t.s, " ")
		}
	}
	return ""
}

func (sc *screen) freq() string {
	s, _ := sc.at(slotY[slotOf(sc)]+9, 32)
	return strings.ReplaceAll(s, " ", "")
}

// home presses V/M until the home screen shows with nothing half typed.
// V/M goes only on a screen read whole (sure): on the home screen it would switch the mode.
func (r *rig) home() (*screen, error) {
	at := func(sc *screen) bool {
		b := sc.bottom()
		return isHome(sc) && !strings.HasPrefix(b, "FREQ") && !strings.HasPrefix(b, "CHANNEL")
	}
	sc, err := r.look()
	for range 6 {
		if err != nil {
			return nil, err
		}
		if at(sc) {
			return sc, nil
		}
		if sc, err = r.key("VM", at); err == nil {
			sc, err = r.sure(sc, at)
		}
	}
	return nil, errors.New("could not get back to the home screen")
}

// mode puts the selected slot in memory (true) or VFO (false) mode with V/M.
func (r *rig) mode(memory bool) (*screen, error) {
	sc, err := r.home()
	if err != nil || (sc.mem() != "") == memory {
		return sc, err
	}
	is := func(sc *screen) bool { return (sc.mem() != "") == memory }
	if sc, err = r.key("VM", is); err == nil {
		sc, err = r.sure(sc, is)
	}
	if err == nil && (sc.mem() != "") != memory {
		err = errors.New("V/M did not switch between frequency and memory mode")
	}
	return sc, err
}

// typeIn types digits one at a time, checking each on the bottom line ("FREQ 146.5", "CHANNEL 01").
func (r *rig) typeIn(digits, prompt string) (sc *screen, err error) {
	for i := range digits {
		shows := func(sc *screen) bool {
			return i == len(digits)-1 || strings.ReplaceAll(strings.TrimPrefix(sc.bottom(), prompt), ".", "") == digits[:i+1]
		}
		// the last digit takes the entry: the prompt leaves the bottom line
		took := func(sc *screen) bool { return !strings.HasPrefix(sc.bottom(), strings.TrimSpace(prompt)) }
		settled := shows
		if i == len(digits)-1 {
			settled = took
		}
		if sc, err = r.key(digits[i:i+1], settled); err == nil {
			sc, err = r.sure(sc, shows)
		}
		if err != nil {
			return nil, err
		}
		if b := sc.bottom(); !shows(sc) {
			return nil, fmt.Errorf("typed %s, the bottom line shows %q", digits[:i+1], b)
		}
	}
	return sc, nil
}

// tune puts the selected slot's VFO on khz (6 digits on the keypad: 146520 is 146.520 MHz).
func (r *rig) tune(khz int) (*screen, error) {
	if _, err := r.mode(false); err != nil {
		return nil, err
	}
	sc, err := r.typeIn(fmt.Sprintf("%06d", khz), "FREQ ")
	if err != nil {
		return nil, err
	}
	want := fmt.Sprintf("%d.%03d", khz/1000, khz%1000)
	if sc, err = r.sure(sc, func(sc *screen) bool { return sc.freq() == want }); err != nil {
		return nil, err
	}
	if sc.freq() != want {
		return nil, fmt.Errorf("tuned %s but the screen shows %q (outside the radio's bands?)", want, sc.freq())
	}
	return sc, nil
}

// channel puts the selected slot on memory channel n.
func (r *rig) channel(n int) (*screen, error) {
	if _, err := r.mode(true); err != nil {
		return nil, err
	}
	sc, err := r.typeIn(fmt.Sprintf("%03d", n), "CHANNEL ")
	if err != nil {
		return nil, err
	}
	if sc, err = r.sure(sc, func(sc *screen) bool { return sc.mem() == strconv.Itoa(n) }); err != nil {
		return nil, err
	}
	if sc.mem() != strconv.Itoa(n) {
		return nil, fmt.Errorf("wanted channel %d, the slot shows M%s (empty channel?)", n, sc.mem())
	}
	return sc, nil
}

// slot makes "A" or "B" the selected slot.
func (r *rig) slot(which string) (*screen, error) {
	sc, err := r.home()
	if err == nil && slotOf(sc) != which {
		is := func(sc *screen) bool { return slotOf(sc) == which }
		if sc, err = r.key("#", is); err == nil {
			sc, err = r.sure(sc, is)
		}
	}
	if err == nil && slotOf(sc) != which {
		err = fmt.Errorf("could not select slot %s", which)
	}
	return sc, err
}

// settings reads every menu item without changing one: DOWN moves the cursor (MENU would select)
// down the menu until each item has been on the screen, then it goes home.
// draw_menu: the title row ends in "14/43" (the cursor's item of all); six rows 15 px apart from
// y 15, the cursor's lit; a section heading row ("SCAN ------") is no item and is skipped.
func (r *rig) settings() ([]string, error) {
	if _, err := r.home(); err != nil {
		return nil, err
	}
	sc, err := r.key("MENU", func(sc *screen) bool { // the title with its counter, and the lit row under it
		t, _ := sc.at(0, 0)
		for k := range 6 {
			if s, lit := sc.at(15+15*k, 0); lit && s != "" {
				return strings.Contains(t, "/")
			}
		}
		return false
	})
	if err != nil {
		return nil, err
	}
	defer r.home()
	var items []string
	for moved := 0; ; {
		t, _ := sc.at(0, 0)
		f := strings.Fields(t)
		var m, n int
		if len(f) == 0 {
			return nil, fmt.Errorf("no menu title on the screen")
		}
		if _, err := fmt.Sscanf(f[len(f)-1], "%d/%d", &m, &n); err != nil || m < 1 || m > n {
			return nil, fmt.Errorf("no menu counter on the screen (%q)", t)
		}
		if items == nil {
			items = make([]string, n)
		}
		var rows []string // the item rows shown, headings left out; cur is the lit one's place
		cur := -1
		for k := range 6 {
			s, lit := sc.at(15+15*k, 0)
			if s == "" || strings.HasSuffix(s, "--") {
				continue
			}
			if lit {
				cur = len(rows)
			}
			rows = append(rows, strings.Join(strings.Fields(s), " "))
		}
		if cur < 0 {
			return nil, fmt.Errorf("no lit menu row on the screen")
		}
		for i, s := range rows {
			if j := m - 1 + i - cur; j >= 0 && j < n {
				items[j] = s
			}
		}
		if !slices.Contains(items, "") {
			break
		}
		if moved > 2*n {
			break // keys kept getting lost: report the item that never showed
		}
		// Step onto the last item row shown below the cursor, so the next screen starts where this one
		// ended: a heading can leave no item below the cursor, and a lost DOWN only costs a step,
		// because the place comes from the screen's counter, not from the presses.
		k := max(1, len(rows)-1-cur)
		if err := press(r.p, slices.Repeat([]string{"DOWN"}, k)); err != nil {
			return nil, err
		}
		moved += k
		if sc, err = r.look(); err != nil {
			return nil, err
		}
	}
	for i, s := range items {
		if s == "" {
			return nil, fmt.Errorf("menu item %d was never on the screen (a key got lost): run it again", i+1)
		}
	}
	return items, nil
}
