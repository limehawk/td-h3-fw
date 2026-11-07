package main

// The flash on a terminal is one live picture, redrawn in place about 20 times a second, that never
// grows past the window it started in (like the radio's own screen, nothing scrolls): the radio's
// screen with its tower acting out each stage, and beside it (under it in a narrow window) the
// steps so far and one status line (a spinner, the Enter prompt or the numbers while it writes).

import (
	"fmt"
	"os"
	"strings"
	gosync "sync" // remote.go has a sync of its own
	"time"

	"github.com/charmbracelet/x/ansi"
	"github.com/charmbracelet/x/term"
)

type stage int

const (
	stBoot   stage = iota // the radio's boot animation, then still
	stListen              // waves out of the tip: waiting on the radio
	stErase               // a dim band wiping down the mast
	stWrite               // the mast filling from the ground up
	stDone                // green, the tip lit, one last wave, then still
	stIdle                // the banner's still
)

// live is the picture; ui is the one running, nil when the output isn't a terminal.
type live struct {
	mu       gosync.Mutex
	st       stage
	since    time.Time
	f        float64
	cap      string // the screen's caption, styled
	lines    []string
	status   string
	spinning bool
	drawn    int
	side     bool // steps beside the screen; else under it
	fd       uintptr
	width    int // the window's: no line may wrap
	height   int
	room     int // lines for the steps and status
	quit     chan struct{}
	gone     chan struct{}
}

var ui *live

// boot is the radio's boot animation as frames, each with the time it shows from.
var boot = func() (fr []struct {
	at time.Duration
	l  lcd
}) {
	var at time.Duration
	splash(func(l *lcd, d time.Duration) {
		fr = append(fr, struct {
			at time.Duration
			l  lcd
		}{at, *l})
		at += d
	})
	return fr
}()

// startLive takes over the terminal for the flash, if it is one tall enough for the picture and
// the steps under it; else the flash prints plain lines.
func startLive(sub string) {
	f, ok := out.(*os.File)
	if !ok || !isTerm(out) {
		return
	}
	w, h, err := term.GetSize(f.Fd())
	if err != nil {
		return
	}
	u := &live{since: time.Now(), cap: caption(ambS, sub), quit: make(chan struct{}), gone: make(chan struct{}), fd: f.Fd()}
	if !u.fit(w, h) {
		return
	}
	ui = u
	fmt.Fprint(out, "\n\x1b[?25l")
	go func() { // Ctrl-C and kills are stopFlash's (ezflash.go)
		t := time.NewTicker(50 * time.Millisecond)
		defer t.Stop()
		for {
			u.draw()
			select {
			case <-u.quit:
				u.draw()
				fmt.Fprint(out, "\x1b[?25h")
				close(u.gone)
				return
			case <-t.C:
			}
		}
	}()
}

// fit lays the picture out for a w by h window; false if it's too small for one.
func (u *live) fit(w, h int) bool {
	u.width, u.height = w, h
	switch {
	case w >= scrW+4+56 && h >= scrRows+2: // beside (the numbers line is 56 wide): the screen's height is the whole picture
		u.side, u.room = true, scrRows-1
	case w >= scrW+2 && h >= scrRows+2+stepRows: // under it
		u.side, u.room = false, h-scrRows-2
	default: // shrunk mid-flash: under it, cut to fit
		u.side, u.room = false, max(1, h-scrRows-2)
		return false
	}
	return true
}

// stopLive draws the last picture and hands the terminal back.
func stopLive() {
	if ui != nil {
		close(ui.quit)
		<-ui.gone
		ui = nil
	}
}

func (u *live) stage(st stage, cap string) {
	u.mu.Lock()
	defer u.mu.Unlock()
	if u.st != st {
		u.st, u.since = st, time.Now()
	}
	u.cap = cap
}

func (u *live) add(line string) {
	u.mu.Lock()
	u.lines = append(u.lines, line)
	u.mu.Unlock()
}

func (u *live) setStatus(s string, spinning bool) {
	u.mu.Lock()
	u.status, u.spinning = s, spinning
	u.mu.Unlock()
}

// write shows f of the image written, with the numbers on the status line.
func (u *live) write(f float64, facts string) {
	pct := fmt.Sprintf("%-4s", fmt.Sprintf("%d%%", int(f*100)))
	if f >= 1 {
		u.stage(stDone, caption(okS, "written"))
		u.setStatus(okS.Render(pct)+"  "+facts, false)
		return
	}
	u.stage(stWrite, caption(ambS, "writing "+fmt.Sprintf("%d%%", int(f*100))))
	u.mu.Lock()
	u.f = f
	u.mu.Unlock()
	u.setStatus(ambS.Render(pct)+"  "+facts, false)
}

// wave draws ring step w (0-8) of the radio's boot waves, fading as it goes out.
func wave(l *lcd, w int) {
	if w < 0 || w > 7 {
		return
	}
	p := pRing
	if w >= 3 {
		p = pRingF
	}
	l.ring(w*11+8, p)
}

// frame is the screen's picture at time t into stage st.
func frame(st stage, t time.Duration, f float64) *lcd {
	step := func(d time.Duration) int { return int(t / d) }
	tip := func(l *lcd, on bool) {
		p := pTipOff
		if on {
			p = pTip
		}
		l.fill(63, 6, 3, 3, p)
	}
	switch st {
	case stBoot:
		i := len(boot) - 1
		for i > 0 && boot[i].at > t {
			i--
		}
		if i == len(boot)-1 && t > boot[i].at+30*time.Millisecond {
			return still()
		}
		l := boot[i].l
		return &l
	case stListen: // two waves 4 steps apart, every 1.2 s, the tip blinking
		l := written(0)
		mastOn(l)
		s := step(80*time.Millisecond) % 15
		wave(l, s)
		wave(l, s-4)
		tip(l, step(250*time.Millisecond)%2 == 0)
		return l
	case stErase: // a band of dim rows runs down the mast, top to ground, every 0.8 s
		l := written(0)
		mastOn(l)
		band := 9 + int(72*float64(t%(800*time.Millisecond))/float64(800*time.Millisecond))
		for y := band; y < band+10 && y < lcdH; y++ {
			for x := range lcdW {
				if l[y][x] == pMast {
					l[y][x] = pGround
				}
			}
		}
		return l
	case stWrite:
		l := written(f)
		tip(l, step(250*time.Millisecond)%2 == 0)
		return l
	case stIdle:
		return still()
	default: // stDone: one wave at the radio's pace, then rest as the banner does
		l := written(1)
		if s := step(40 * time.Millisecond); s < 9 {
			wave(l, s)
			return l
		}
		l.ring(19, pRing)
		l.ring(41, pRing)
		return l
	}
}

// mastOn turns the dim mast of written(0) grey: a tower that's there, not yet written.
func mastOn(l *lcd) {
	for y := range lcdH - 2 { // not the ground
		for x := range lcdW {
			if l[y][x] == pGround {
				l[y][x] = pMast
			}
		}
	}
}

// stepRows is the most lines a flash's steps and status take: the three rows, a gap, up to six
// steps ([!!] no answer, flash mode, ...), the prompt or spinner and the numbers.
const stepRows = 11

func (u *live) draw() {
	u.mu.Lock()
	var b strings.Builder
	// A window resized mid-flash (or a terminal tab narrowed as it's shown) rewraps what's drawn, so
	// moving up over the old picture would miss it and every frame would scroll: start clean.
	if w, h, err := term.GetSize(u.fd); err == nil && (w != u.width || h != u.height) {
		u.fit(w, h)
		b.WriteString("\x1b[H\x1b[2J")
		u.drawn = 0
	}
	t := time.Since(u.since)
	rows := frame(u.st, t, u.f).screen(u.st != stBoot || t > boot[len(boot)-1].at, u.cap)
	status := u.status
	if u.spinning {
		status = ambS.Render("["+frames[int(time.Since(u.since)/(100*time.Millisecond))%len(frames)]+"]") + " " + status
	}
	text := append(append([]string{}, u.lines...), status)
	if len(text) > u.room { // the oldest steps go first; the status stays
		text = text[len(text)-u.room:]
	}
	if u.side {
		for i, l := range text {
			rows[1+i] += "  " + ansi.Truncate(l, u.width-scrW-5, "")
		}
	} else {
		rows = append(rows, "")
		for _, l := range text {
			rows = append(rows, "  "+ansi.Truncate(l, u.width-3, ""))
		}
	}
	drawn := u.drawn
	u.drawn = len(rows)
	defer u.mu.Unlock() // held while writing, so stopFlash's last words can't land mid-picture
	if drawn > 0 {
		fmt.Fprintf(&b, "\x1b[%dA", drawn)
	}
	for _, r := range rows {
		b.WriteString("\r\x1b[2K" + r + "\n")
	}
	fmt.Fprint(out, b.String())
}

// show sets the stage and caption, when there's a live picture.
func show(st stage, cap string) {
	if ui != nil {
		ui.stage(st, cap)
	}
}
