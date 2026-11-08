package main

// `tdh3 flash`: the newest build onto the radio, finding the port and the image itself; holding PTT
// is the go-ahead. `tdh3 firmware flash IMAGE` shares the rest (flashFile).

import (
	"bufio"
	"errors"
	"fmt"
	"io"
	"os"
	"os/signal"
	"path/filepath"
	"regexp"
	"strconv"
	"strings"
	gosync "sync"
	"sync/atomic"
	"syscall"
	"time"
)

// manyCables is findPort's error with two or more radio cables plugged in: their ports.
type manyCables []string

func (m manyCables) Error() string {
	return fmt.Sprintf("%d radio cables plugged in (%s): pick one with -p", len(m), strings.Join(m, ", "))
}

// findPort waits for the radio's cable to show up and returns its port. Two or more: manyCables.
var findPort = func() (string, error) { // a var: tests stand in for the cable
	var sp *spinner
	defer func() { sp.stop() }()
	for {
		switch ports := radioPorts(); len(ports) {
		case 0:
			if sp == nil {
				sp = spin("plug the radio's USB-C cable into the computer")
			}
			time.Sleep(500 * time.Millisecond)
		case 1:
			return ports[0], nil
		default:
			return "", manyCables(ports)
		}
	}
}

var askEach = 2 * time.Second // how long pickCable waits on each radio; tests shorten it

// pickCable lists the radio cables, numbered, with what each radio says, and waits for a number
// key. It returns the port picked and what findRadio heard there.
func pickCable(ports manyCables) (port, ver string, inFlash bool) {
	if len(ports) > 9 {
		ports = ports[:9] // ponytail: one key each; a tenth cable needs -p
	}
	vers, flash := make([]string, len(ports)), make([]bool, len(ports))
	sp := spin(fmt.Sprintf("%d radio cables plugged in: asking each radio", len(ports)))
	for i, p := range ports {
		vers[i], flash[i] = findRadio(p, askEach, nil)
	}
	sp.stop()
	say(fmt.Sprintf("%d radio cables plugged in:", len(ports)))
	for i, p := range ports {
		what := noteS.Render("not answering (off, REMOTE off, or TIDRADIO's firmware)")
		switch {
		case flash[i]:
			what = "in flash mode"
		case vers[i] != "":
			what = "running " + valS.Render(vers[i])
		}
		say(fmt.Sprintf("  %s  %-14s %s", ambS.Render(strconv.Itoa(i+1)), p, what))
	}
	defer showPrompt(fmt.Sprintf("Type the number of the radio to flash (1-%d)", len(ports)) + noteS.Render(", Ctrl-C stops"))()
	for len(digitKey) > 0 { // typed before the list showed: not a pick
		<-digitKey
	}
	for {
		if k := int(<-digitKey - '1'); k < len(ports) {
			return ports[k], vers[k], flash[k]
		}
	}
}

// builtInName is the name a release gives this image, so a saved copy flashes like a downloaded one.
func builtInName(rx bool, img []byte) string {
	if rx {
		return "td-h3-fw-" + imageVersion("", img) + "-rx.bin"
	}
	return "td-h3-fw-" + imageVersion("", img) + ".bin"
}

// extractFirmware saves the images this flasher carries into dir, making it if needed, and returns their paths.
func extractFirmware(dir string) ([]string, error) {
	if builtIn(false) == nil {
		return nil, errors.New("this copy of tdh3 carries no firmware: download the flasher from a release")
	}
	if err := os.MkdirAll(dir, 0o755); err != nil {
		return nil, err
	}
	var saved []string
	for _, rx := range []bool{false, true} {
		img := builtIn(rx)
		p := filepath.Join(dir, builtInName(rx, img))
		if err := os.WriteFile(p, img, 0o644); err != nil {
			return saved, err
		}
		saved = append(saved, p)
	}
	return saved, nil
}

// newestImage finds the image to flash, its name and bytes: the one this flasher carries (a
// release's, see builtin_release.go; before any .bin lying about, which could be an older one from
// an earlier download), else a downloaded release (td-h3-fw-0.59.6.bin, -rx.bin with rx; the newest
// if there are several) in the folder we run in or beside tdh3, else fw/build/td-h3.bin
// (td-h3-rx.bin) in a checkout, from the folder we run in or tdh3's own folder upwards.
func newestImage(rx bool) (string, []byte, error) {
	name := "td-h3.bin"
	if rx {
		name = "td-h3-rx.bin"
	}
	var dirs []string
	if wd, err := os.Getwd(); err == nil {
		dirs = append(dirs, wd)
	}
	if exe, err := os.Executable(); err == nil {
		dirs = append(dirs, filepath.Dir(exe))
	}
	if img := builtIn(rx); img != nil {
		return builtInName(rx, img) + " (built in)", img, nil
	}
	for _, d := range dirs {
		if p := newestRelease(d, rx); p != "" {
			return readImage(p)
		}
	}
	for _, d := range dirs {
		for ; ; d = filepath.Dir(d) {
			if p := filepath.Join(d, "fw", "build", name); fileExists(p) {
				return readImage(p)
			}
			if filepath.Dir(d) == d {
				break
			}
		}
	}
	return "", nil, errors.New("no firmware here: download the flasher from a release (it carries the firmware), or give an image to tdh3 firmware flash")
}

func readImage(p string) (string, []byte, error) {
	img, err := os.ReadFile(p)
	return p, img, err
}

// newestRelease is the highest-numbered td-h3-fw-N.N.N.bin (-rx.bin) in dir, or "".
func newestRelease(dir string, rx bool) string {
	re := regexp.MustCompile(`^td-h3-fw-(\d+)\.(\d+)\.(\d+)\.bin$`)
	if rx {
		re = regexp.MustCompile(`^td-h3-fw-(\d+)\.(\d+)\.(\d+)-rx\.bin$`)
	}
	entries, _ := os.ReadDir(dir)
	best, bestV := "", [3]int{-1}
	for _, e := range entries {
		m := re.FindStringSubmatch(e.Name())
		if m == nil {
			continue
		}
		var v [3]int
		for i := range v {
			v[i], _ = strconv.Atoi(m[i+1])
		}
		if v[0] > bestV[0] || v[0] == bestV[0] && (v[1] > bestV[1] || v[1] == bestV[1] && v[2] > bestV[2]) {
			best, bestV = filepath.Join(dir, e.Name()), v
		}
	}
	return best
}

func fileExists(p string) bool { fi, err := os.Stat(p); return err == nil && !fi.IsDir() }

// flashFile flashes the image at path, flashImage the image img named path. ask: look for the radio
// briefly and ask which cable when there are several (false, for scripts: wait for the radio as long
// as it takes; several cables need -p).
// force: flash an untagged build too.
var release = regexp.MustCompile(`^\d+\.\d+\.\d+$`)

func flashFile(port, path string, ask, force bool) error {
	img, err := os.ReadFile(path)
	if err != nil {
		return err
	}
	return flashImage(port, path, img, ask, force)
}

func flashImage(port, path string, img []byte, ask, force bool) error {
	var err error
	if err := check(img); err != nil {
		return fmt.Errorf("refusing %s: %w", path, err)
	}
	ver, kind := imageVersion(path, img), "full (transmit)"
	if strings.Contains(filepath.Base(path), "-rx") {
		kind = "receive-only"
	}
	// "0.54.2-1": commits after the tag; "+": uncommitted changes; "dev": no tag at all. "?" is stock's.
	if ver != "?" && !release.MatchString(ver) && !force {
		return fmt.Errorf("refusing v%s: not a release (--force flashes a test build)", ver)
	}
	var many manyCables
	if port == "" { // tdh3 firmware flash IMAGE without -p too: one radio cable plugged in, use it
		if port, err = findPort(); err != nil && !(ask && errors.As(err, &many)) {
			return err // several cables with --yes: -p says which
		}
	}
	defer watchStop()()
	if many != nil && !keysOn {
		return many
	}
	erased.Store(false)
	blocks := (len(img) + 31) / 32
	if startLive("open firmware flasher"); ui == nil {
		banner("open firmware flasher")
	}
	defer stopLive()
	var before string
	var inFlash bool
	if many != nil {
		port, before, inFlash = pickCable(many)
		say("")
	}
	row("radio", noteS.Render(port))
	row("flashing", valS.Render("v"+ver)+"  "+noteS.Render(kind))
	row("image", noteS.Render(fmt.Sprintf("%s  %d blocks  %.1f KB", filepath.Base(path), blocks, float64(len(img))/1024)))
	say("")
	show(stListen, caption(ambS, "listening"))

	switch {
	case many != nil: // pickCable just asked it
	case ask:
		sp := spin("looking for the radio")
		before, inFlash = findRadio(port, 3*time.Second, nil)
		sp.stop()
	default:
		sp := spin("looking for the radio: switch it on")
		before, inFlash = findRadio(port, 0, func() {
			sp.set("no answer yet: switch the radio on (MENU, REMOTE on shows its version), or hold PTT and switch it on to flash")
		})
		sp.stop()
	}
	show(stIdle, caption(ambS, "open firmware flasher"))
	was := "unknown"
	switch {
	case inFlash:
		okLine("radio found, in flash mode")
	case before == "":
		say(noteS.Render("[--]") + " radio not answering (off, or REMOTE off): you'll switch it on holding PTT next")
	default:
		was = before
		if before[0] >= '0' && before[0] <= '9' {
			was = "v" + before
		}
		okLine("radio found, running " + valS.Render(was))
	}
	// No Enter to confirm: nothing on the radio changes until someone holds PTT (or switches it on
	// with PTT held), and that is the confirmation.

	p, err := open(port, 115200)
	if err != nil {
		return err
	}
	hold(p)
	start, err := flashSteps(p, img, inFlash, before != "")
	p.Close()
	if err != nil {
		return err
	}
	took := time.Since(start)
	sp := spin("waiting for v" + ver + " to start")
	after, _ := findRadio(port, 15*time.Second, nil)
	sp.stop()
	if after == ver {
		show(stDone, caption(okS, "v"+ver+" running"))
	} else {
		show(stDone, caption(ambS, "v"+ver+" flashed"))
	}
	stats := noteS.Render(fmt.Sprintf("(was %s, %s, %.1f KB/s)", was, took.Round(time.Second), float64(len(img))/1024/took.Seconds()))
	switch {
	case after == ver:
		okLine(okS.Render("v" + ver + " is running on your radio"))
	case after == "":
		warnLine("v" + ver + " flashed; the radio didn't answer (MENU, REMOTE on to confirm)")
	default:
		warnLine("flashed v" + ver + ", but the radio says " + after)
	}
	say("     " + stats)
	if ui == nil {
		fmt.Fprintln(os.Stderr)
	}
	return nil
}

// enter shows prompt and waits for Enter (readKeys hears it); Ctrl-C stops.
func enter(prompt string) error {
	if !keysOn {
		return errors.New("can't read keys here: pass --yes")
	}
	defer showPrompt(prompt)()
	for len(enterKey) > 0 { // an Enter pressed while it listened isn't a yes
		<-enterKey
	}
	<-enterKey
	return nil
}

// showPrompt shows prompt on the status line until the returned func takes it away.
func showPrompt(prompt string) (gone func()) {
	if ui == nil {
		fmt.Fprint(os.Stderr, "\n  "+prompt)
		return func() { fmt.Fprint(os.Stderr, "\r\x1b[2K") } // the prompt goes; the blank line above it stays
	}
	ui.setStatus(prompt, false)
	return func() { ui.setStatus("", false) }
}

// A flash stops at once on Ctrl-C in every phase: the terminal goes back as it was, the port
// closes and tdh3 exits 130. Keys are read unbuffered and unechoed, with ISIG off so Ctrl-C comes
// as the byte 3 (and Ctrl-Z can't suspend a write); SIGINT and SIGTERM do the same, so ^C works
// even on a terminal a killed run left with ISIG off.
var (
	stopMu     gosync.Mutex
	held       io.Closer   // the port open now
	erased     atomic.Bool // the bootloader took the handshake: the old firmware is going
	restoreTTY = func() {}
	keysOn     bool
	enterKey   = make(chan struct{}, 1)
	digitKey   = make(chan byte, 1) // 1-9, for pickCable
	exit       = os.Exit
)

// hold notes p as the port to close if the flash stops.
func hold(p io.Closer) { stopMu.Lock(); held = p; stopMu.Unlock() }

// doubleClicked: opened with no command, so its window closes as soon as tdh3 exits.
var doubleClicked = len(os.Args) == 1

// closeWait keeps a double-clicked window open until Enter, so its last lines can be read.
// onReader: called on readKeys' own goroutine, the one reading stdin once a flash has begun.
func closeWait(onReader bool) {
	if !doubleClicked || !isTerm(os.Stdin) || !isTerm(os.Stdout) {
		return
	}
	signal.Reset(os.Interrupt, syscall.SIGTERM) // Ctrl-C closes it too (stopFlash may hold stopMu here)
	fmt.Fprint(out, "\n  Press Enter to close.")
	if keysReading.Load() && !onReader { // that reader still owns stdin: take its next Enter
		select {
		case <-enterKey: // an Enter from before this question
		default:
		}
		<-enterKey
		return
	}
	bufio.NewReader(os.Stdin).ReadString('\n')
}

var keysReading atomic.Bool // readKeys runs (it never stops once started)

// stopFlash ends the flash now, from any goroutine; onReader: from readKeys.
func stopFlash(onReader bool) {
	stopMu.Lock()
	defer stopMu.Unlock()
	restoreTTY()
	if held != nil {
		held.Close()
	}
	msg := "stopped: nothing written"
	if erased.Load() {
		msg = "stopped mid-flash: the radio stays in flash mode; run tdh3 flash again"
	}
	if ui != nil { // the picture stays as it was, under the line
		ui.mu.Lock()
		defer ui.mu.Unlock()
	}
	fmt.Fprint(out, "\r\x1b[2K\x1b[?25h\n  "+ambS.Render("[!!]")+" "+msg+"\n")
	closeWait(onReader)
	exit(130)
}

// watchStop arms stopFlash for one flash; end disarms it and hands the terminal back.
func watchStop() (end func()) {
	sig, done := make(chan os.Signal, 1), make(chan struct{})
	signal.Notify(sig, os.Interrupt, syscall.SIGTERM)
	go func() {
		select {
		case <-sig:
			stopFlash(false)
		case <-done:
		}
	}()
	if old, err := keysMode(int(os.Stdin.Fd())); err == nil {
		restoreTTY, keysOn = old, true
		if keysReading.CompareAndSwap(false, true) {
			go readKeys(os.Stdin)
		}
	}
	return func() {
		signal.Stop(sig)
		close(done)
		stopMu.Lock()
		restoreTTY()
		restoreTTY, keysOn, held = func() {}, false, nil
		stopMu.Unlock()
	}
}

// readKeys turns the keys on r into Enter for enter and Ctrl-C for stopFlash.
func readKeys(r io.Reader) {
	for b := make([]byte, 1); ; {
		if _, err := r.Read(b); err != nil {
			return
		}
		switch b[0] {
		case 3:
			stopFlash(true)
			return
		case '\r', '\n':
			select {
			case enterKey <- struct{}{}:
			default:
			}
		case '1', '2', '3', '4', '5', '6', '7', '8', '9':
			select {
			case digitKey <- b[0]:
			default:
			}
		}
	}
}
