//go:build !windows

package main

import (
	"bytes"
	"io"
	"os"
	"runtime"
	"strings"
	"sync/atomic"
	"syscall"
	"testing"
	"time"
)

type closeSpy struct{ closed atomic.Bool }

func (c *closeSpy) Close() error { c.closed.Store(true); return nil }

// stopped arms stopFlash with a fake exit, terminal and port; it returns the port, whether the
// terminal was handed back, and the exit code once stopFlash runs.
func stopped(t *testing.T) (*closeSpy, *atomic.Bool, chan int, *bytes.Buffer) {
	var buf bytes.Buffer
	codes, restored, p := make(chan int, 1), &atomic.Bool{}, &closeSpy{}
	oldOut, oldExit, oldRestore := out, exit, restoreTTY
	out, exit = &buf, func(c int) { codes <- c; runtime.Goexit() }
	restoreTTY = func() { restored.Store(true) }
	t.Cleanup(func() { out, exit, restoreTTY = oldOut, oldExit, oldRestore; hold(nil); erased.Store(false) })
	hold(p)
	erased.Store(false)
	return p, restored, codes, &buf
}

func code(t *testing.T, codes chan int) int {
	select {
	case c := <-codes:
		return c
	case <-time.After(time.Second):
		t.Fatal("still running a second after Ctrl-C")
		return 0
	}
}

// Ctrl-C as the byte 3 (the terminal sends it so with ISIG off) while it waits for the radio: the
// flasher hands the terminal back, closes the port and exits 130 at once (on the radio it kept
// /dev/ttyUSB0 open).
func TestCtrlCByteWhileWaitingStops(t *testing.T) {
	p, restored, codes, buf := stopped(t)
	r, w := io.Pipe()
	go readKeys(r)
	w.Write([]byte("x\r\x03"))
	if c := code(t, codes); c != 130 || !p.closed.Load() || !restored.Load() {
		t.Fatalf("exit %d, port closed %v, terminal back %v", c, p.closed.Load(), restored.Load())
	}
	if !strings.Contains(buf.String(), "nothing written") || !strings.Contains(buf.String(), "\x1b[?25h") {
		t.Fatalf("said %q", buf.String())
	}
	<-enterKey // the \r before it was an Enter
}

// A kill mid-write asks nothing: it stops the same way and says the radio stays in flash mode.
func TestKillMidFlashStops(t *testing.T) {
	if isTerm(os.Stdin) {
		t.Skip("watchStop would take this terminal's keys")
	}
	p, restored, codes, buf := stopped(t)
	end := watchStop()
	defer end()
	restoreTTY = func() { restored.Store(true) } // watchStop found no terminal to set
	erased.Store(true)
	syscall.Kill(os.Getpid(), syscall.SIGTERM)
	if c := code(t, codes); c != 130 || !p.closed.Load() || !restored.Load() {
		t.Fatalf("exit %d, port closed %v, terminal back %v", c, p.closed.Load(), restored.Load())
	}
	if !strings.Contains(buf.String(), "stays in flash mode") {
		t.Fatalf("said %q", buf.String())
	}
}

// Two cables: each listed with what its radio says; a number past the list is ignored, the next
// one picks.
func TestPickCable(t *testing.T) {
	var buf bytes.Buffer
	oldOut, oldWait := out, askEach
	out, askEach = &buf, 10*time.Millisecond
	t.Cleanup(func() { out, askEach = oldOut, oldWait; hold(nil) })
	r, w := io.Pipe()
	go readKeys(r)
	w.Write([]byte("1")) // while it asks the radios: dropped
	go func() {
		time.Sleep(time.Second)
		w.Write([]byte("3"))
		time.Sleep(50 * time.Millisecond)
		w.Write([]byte("2"))
	}()
	port, ver, inFlash := pickCable(manyCables{"/nonexistent/a", "/nonexistent/b"})
	if port != "/nonexistent/b" || ver != "" || inFlash {
		t.Fatalf("picked %q %q %v", port, ver, inFlash)
	}
	if s := buf.String(); !strings.Contains(s, "2 radio cables plugged in:") || strings.Count(s, "not answering") != 2 {
		t.Fatalf("said %q", s)
	}
}
