package main

// What `tdh3 firmware flash` shows, in fixed columns of single-width glyphs (ASCII plus the
// bar's blocks and braille): the radio tower, what the radio runs and what goes on, one "[ok]" line per step,
// a spinner while it waits on the radio, a bar while it writes, and a last line once the radio
// answers from the new firmware.

import (
	"bytes"
	"fmt"
	"go.bug.st/serial"
	"io"
	"os"
	"regexp"
	"strings"
	"sync/atomic"
	"time"

	"charm.land/lipgloss/v2"
)

var (
	white = lipgloss.Color("#F2F2F2")
	valS  = lipgloss.NewStyle().Foreground(white).Bold(true)
	noteS = lipgloss.NewStyle().Foreground(grey)
	okS   = lipgloss.NewStyle().Foreground(green).Bold(true)
	ambS  = lipgloss.NewStyle().Foreground(amber).Bold(true)
)

// out is where the flash's story goes (a test swaps it for a buffer).
var out io.Writer = os.Stderr

func say(line string) {
	if ui != nil {
		ui.add(line)
		return
	}
	fmt.Fprintln(out, "  "+line)
}

// row prints "  key       value" with the key in a 10-wide grey column.
func row(key, value string) { say(noteS.Render(fmt.Sprintf("%-10s", key)) + " " + value) }

func okLine(text string)   { say(okS.Render("[ok]") + " " + text) }
func warnLine(text string) { say(ambS.Render("[!!]") + " " + text) }

// frames is a six-dot snake running round two braille cells, so "[⣏⠀]" is four columns wide like "[ok]".
var frames = strings.Fields("⠉⠹ ⠈⢹ ⠀⣹ ⢀⣸ ⣀⣰ ⣄⣠ ⣆⣀ ⣇⡀ ⣏⠀ ⡏⠁ ⠏⠉ ⠋⠙")

// spinner shows "[⣏⠀] msg" with the snake turning until stop; set changes msg while it runs.
type spinner struct {
	msg        atomic.Value
	quit, gone chan struct{}
}

func spin(msg string) *spinner {
	s := &spinner{quit: make(chan struct{}), gone: make(chan struct{})}
	s.msg.Store(msg)
	if ui != nil { // the live picture draws it
		ui.setStatus(msg, true)
		close(s.gone)
		return s
	}
	go func() {
		t := time.NewTicker(100 * time.Millisecond)
		defer t.Stop()
		for i := 0; ; i++ {
			fmt.Fprintf(out, "\r\x1b[2K  %s %s", ambS.Render("["+frames[i%len(frames)]+"]"), s.msg.Load().(string))
			select {
			case <-s.quit:
				fmt.Fprint(out, "\r\x1b[2K")
				close(s.gone)
				return
			case <-t.C:
			}
		}
	}()
	return s
}

func (s *spinner) set(msg string) {
	s.msg.Store(msg)
	if ui != nil {
		ui.setStatus(msg, true)
	}
}

// stop clears the spinner's line; a nil spinner is fine.
func (s *spinner) stop() {
	if s != nil {
		close(s.quit)
		<-s.gone
		if ui != nil {
			ui.setStatus("", false)
		}
	}
}

// steps prints a flash one line at a time. At most one spinner runs: each new line or wait stops
// the last one, so a prompt can't keep redrawing over the lines and the bar after it.
type steps struct{ sp *spinner }

func (s *steps) wait(msg string)  { s.end(); s.sp = spin(msg) }
func (s *steps) ok(text string)   { s.end(); okLine(text) }
func (s *steps) warn(text string) { s.end(); warnLine(text) }
func (s *steps) end()             { s.sp.stop(); s.sp = nil }

const holdPTT = "switch the radio off, then hold PTT and switch it on"

// keepArmed is how often the flasher sends REMOTE's 0x80 (no key held) while it waits for PTT:
// fw/remote.c disarms after 10 s with no byte from the computer, and 0x80 gets no answer.
const keepArmed = 2 * time.Second

// restart arms the radio's flash restart over REMOTE: 'B', answered "S" A5 5A at REMOTE's speed;
// armed, the radio waits at the bootloader's 115200, and so does p after this. The next PTT press
// then restarts it into the bootloader with PTT still held: flash mode with no power-cycle.
// Firmware without it ignores 'B', so no answer means the power-cycle way.
func restart(p port) bool {
	defer setBaud(p, 115200)
	for _, baud := range remoteBauds[:2] { // 115200: older td-h3-fw
		setBaud(p, baud)
		if _, err := p.Write([]byte("B")); err != nil {
			return false
		}
		var got []byte // a byte at a time, so the bootloader's A5s after the answer stay for flash
		for t := time.Now(); time.Since(t) < time.Second; {
			if got = append(got, readN(p, 1, 100*time.Millisecond)...); bytes.HasSuffix(got, []byte("S\xA5\x5A")) {
				return true
			}
		}
	}
	return false
}

// setBaud changes a serial port's speed (the test fakes have none).
func setBaud(p port, baud int) {
	if s, ok := p.(serial.Port); ok {
		s.SetMode(&serial.Mode{BaudRate: baud})
	}
}

// armed waits for PTT with no time limit, keeping the radio armed, until stop closes. gone
// closes when it's done with sp. The power-cycle way stays on the line for a radio that went quiet.
func armed(p port, sp *spinner, stop <-chan struct{}, gone chan<- struct{}, fallback string) {
	defer close(gone)
	sp.set(fmt.Sprintf("press and hold %s now  %s", ambS.Render("PTT"), noteS.Render("(or "+fallback+")")))
	for {
		select {
		case <-stop:
			return
		case <-time.After(keepArmed):
			p.Write([]byte{0x80})
		}
	}
}

// flashSteps runs flash on p and tells it in order: (PTT or power-cycle prompt,) flash mode,
// erasing, erased, the bar. inFlash says findRadio already heard the bootloader; running, that it
// answered REMOTE, so it may restart into flash mode on PTT. It returns when the first block
// went, for the speed.
func flashSteps(p port, img []byte, inFlash, running bool) (start time.Time, err error) {
	st, draw := &steps{}, progressBar(len(img))
	if ui != nil {
		facts := progressFacts(len(img))
		draw = func(f float64) { ui.write(f, facts(f)) }
	}
	listen := func() { show(stListen, caption(ambS, "switch on holding PTT")) }
	ptt := strings.Replace(holdPTT, "PTT", ambS.Render("PTT"), 1)
	needPTT, erasing := !inFlash, false
	var stop, gone chan struct{} // armed runs: close stop, wait for gone
	switch {
	case needPTT && running && restart(p):
		st.ok("radio ready to restart into flash mode")
		show(stListen, caption(ambS, "hold PTT"))
		st.wait("")
		stop, gone = make(chan struct{}), make(chan struct{})
		go armed(p, st.sp, stop, gone, holdPTT)
	case needPTT:
		listen()
		st.wait(ptt)
	}
	err = flash(p, img, func(e flashEvent) {
		switch e {
		case flashFound:
			erased.Store(true)
			if stop != nil {
				close(stop)
				<-gone
				stop = nil
			}
			if needPTT {
				st.ok("flash mode")
				needPTT = false
			}
			show(stErase, caption(ambS, "erasing"))
			st.wait("erasing")
			erasing = true
		case flashNoAnswer:
			// Mostly a bootloader left waiting by an earlier try (the radio was already in flash
			// mode when we started): it takes the handshake but never answers block 0.
			st.warn("no answer from flash mode")
			listen()
			st.wait(ptt)
			needPTT, erasing = true, false
		}
	}, func(f float64) {
		if erasing {
			st.ok("erased")
			erasing, start = false, time.Now()
		}
		draw(f)
	})
	if stop != nil { // flash gave up before flash mode came
		close(stop)
		<-gone
	}
	st.end()
	return start, err
}

func isTerm(w io.Writer) bool {
	f, ok := w.(*os.File)
	if !ok {
		return false
	}
	fi, err := f.Stat()
	return err == nil && fi.Mode()&os.ModeCharDevice != 0
}

// The REMOTE status line "q146.520 -112 O S 7400 0.20.0\n": the version is its last field from
// 0.20 on (0.20.0 itself lost the battery digits to it).
var statusLine = regexp.MustCompile(`q(\d+\.\d+ -?\d+ [OC] [S-][ 0-9A-Za-z.+-]*)\n`)

func statusVersion(b []byte) (string, bool) {
	m := statusLine.FindSubmatch(b)
	if m == nil {
		return "", false
	}
	f := strings.Fields(string(m[1]))
	if v := f[len(f)-1]; strings.Contains(v, ".") && len(f) >= 5 {
		return v, true
	}
	return "older than 0.20", true
}

// findRadio waits for the radio to show up, for up to `wait` (0: until Ctrl-C): running, answering
// REMOTE's status request with its version (at any of remoteBauds); or in flash mode,
// the bootloader's run of A5s at 115200. hint is called once after 4 s of nothing.
func findRadio(portName string, wait time.Duration, hint func()) (ver string, flashMode bool) {
	start, hinted := time.Now(), false
	for wait == 0 || time.Since(start) < wait {
		if p, err := open(portName, 115200); err == nil { // listen only: a stray byte can upset a waiting bootloader
			hold(p)
			b := readN(p, 64, 500*time.Millisecond)
			p.Close()
			if strings.Contains(string(b), strings.Repeat("\xA5", bootA5s)) {
				return "", true
			}
		} else {
			time.Sleep(200 * time.Millisecond) // the cable is out: don't spin a core waiting for it
		}
		for _, baud := range remoteBauds {
			if p, err := open(portName, baud); err == nil {
				hold(p)
				var got []byte
				for t := time.Now(); time.Since(t) < 900*time.Millisecond; {
					p.Write([]byte("Q"))
					got = append(got, readN(p, 2048, 300*time.Millisecond)...)
					if v, ok := statusVersion(got); ok {
						p.Close()
						return v, false
					}
				}
				p.Close()
			}
		}
		if !hinted && time.Since(start) > 4*time.Second && hint != nil {
			hint()
			hinted = true
		}
	}
	return "", false
}

// trackS is the bar's empty part: the same █ as the filled part, dark grey, so it reads as one
// solid track (░'s dot pattern looked spotty beside the solid fill).
var trackS = lipgloss.NewStyle().Foreground(lipgloss.Color("#3A3A3A"))

// bar draws "██████████  54% ": filled amber turning green when done, then the percent two
// spaces after it at any value, padded to four so the columns after it stay put.
func bar(f float64, width int) string {
	n := int(f*float64(width) + 0.5)
	fill := ambS
	if f >= 1 {
		fill = okS
	}
	return fill.Render(strings.Repeat("█", n)) + trackS.Render(strings.Repeat("█", width-n)) +
		fmt.Sprintf("  %-4s", fmt.Sprintf("%d%%", int(f*100)))
}
