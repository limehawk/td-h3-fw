package main

import (
	"bytes"
	"errors"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/charmbracelet/x/ansi"
)

// fake is a scripted radio: reply() answers each write.
type fake struct {
	in    bytes.Buffer
	reply func(w []byte) []byte
	sent  [][]byte
}

func (f *fake) Write(b []byte) (int, error) {
	f.sent = append(f.sent, append([]byte{}, b...))
	f.in.Write(f.reply(b))
	return len(b), nil
}
func (f *fake) Read(b []byte) (int, error)         { return f.in.Read(b) }
func (f *fake) SetReadTimeout(time.Duration) error { return nil }

func TestHelloMatchesCapturedHandshake(t *testing.T) {
	if len(hello) != 36 || !bytes.Equal(hello[:6], []byte{0xA0, 0xEE, 0x74, 0x71, 0x07, 0x74}) || sum8(hello[4:]) != hello[3] {
		t.Fatalf("hello = % X", hello)
	}
}

func TestPacketsAgainstCapture(t *testing.T) {
	// First packet captured from TIDRADIO's updater (firmware_flash_protocol.txt): A1 00 00 FE.
	data := []byte{0x02, 0xC3, 0xCA, 0xE4, 0xFB, 0xFD, 0x7F, 0x24, 0x02, 0xEC, 0xAD, 0x02, 0x76, 0xA4, 0xE4, 0xF5,
		0x38, 0x75, 0x39, 0x51, 0x85, 0x38, 0x3A, 0xF5, 0x37, 0xAF, 0x3A, 0x05, 0x3A, 0x75, 0x6A, 0xFF}
	pk := packets(append(append([]byte{}, data...), make([]byte, 40)...))
	if !bytes.Equal(pk[0][:4], []byte{0xA1, 0x00, 0x00, 0xFE}) {
		t.Fatalf("first header % X", pk[0][:4])
	}
	if last := pk[len(pk)-1]; len(pk) != 3 || last[0] != 0xA2 || last[2] != 2 {
		t.Fatalf("last header % X (n=%d)", last[:4], len(pk))
	}
}

func TestCheckRefusesDangerousImages(t *testing.T) {
	ok := append([]byte{0x02, 0x00, 0x10}, make([]byte, 0x20)...)
	if err := check(ok); err != nil {
		t.Fatal(err)
	}
	for name, img := range map[string][]byte{
		"too big":      append([]byte{0x02, 0x00, 0x10}, make([]byte, appMax)...),
		"no ljmp":      append([]byte{0xFF}, ok[1:]...),
		"jump outside": append([]byte{0x02, 0xF0, 0x00}, ok[3:]...),
		"empty":        {},
	} {
		if check(img) == nil {
			t.Errorf("%s: accepted", name)
		}
	}
}

func TestFlashTalksToBootloader(t *testing.T) {
	img := append([]byte{0x02, 0x00, 0x03}, make([]byte, 61)...) // 2 blocks
	f := &fake{reply: func(w []byte) []byte {
		if len(w) == 36 && w[0] == 0xA1 || len(w) == 36 && w[0] == 0xA2 {
			return []byte{0xA3}
		}
		return nil
	}}
	f.in.Write([]byte{0xA5, 0x5A, 0x1C, 0xA5, 0xE0}) // our firmware's REMOTE stream: lone A5s, not the bootloader
	f.in.Write(bytes.Repeat([]byte{0xA5}, 10))       // the bootloader: nothing but A5
	var last float64
	if err := flash(f, img, func(flashEvent) {}, func(p float64) { last = p }); err != nil {
		t.Fatal(err)
	}
	if !bytes.Equal(f.sent[0], hello) || f.sent[1][0] != 0xA1 || f.sent[2][0] != 0xA2 || last != 1 {
		t.Fatalf("sent %d writes, first % X", len(f.sent), f.sent[0][:6])
	}
}

func TestHandshakeSurvivesALateExtraAck(t *testing.T) {
	hellos := 0
	f := &fake{reply: func(w []byte) []byte {
		switch {
		case bytes.Equal(w, magic):
			if hellos++; hellos == 1 {
				return nil // slow: the retry goes out before this ack...
			}
			return []byte{6, 6} // ...so two acks arrive together
		case bytes.Equal(w, []byte{2}):
			return []byte("P31183\xff\xff")
		case bytes.Equal(w, []byte{6}):
			return []byte{6}
		}
		return nil
	}}
	ident, err := handshake(f)
	if err != nil || string(ident) != "P31183\xff\xff" {
		t.Fatalf("ident %q, err %v", ident, err)
	}
}

func TestBackupReadsWholeEEPROMAndNeverWrites(t *testing.T) {
	mem := make([]byte, 0x2000)
	for i := range mem {
		mem[i] = byte(i * 7)
	}
	mem[0x1FF0] = 0x85
	hellos := 0
	f := &fake{reply: func(w []byte) []byte {
		switch {
		case bytes.Equal(w, magic):
			if hellos++; hellos < 2 { // first hello lost, like td-h3-fw entering PC LINK
				return nil
			}
			return []byte{6}
		case bytes.Equal(w, []byte{2}):
			return []byte("P31183\xff\xff")
		case bytes.Equal(w, []byte{6}), bytes.Equal(w, []byte("E")):
			return []byte{6}
		case len(w) == 4 && w[0] == 'R':
			a := int(w[1])<<8 | int(w[2])
			d := mem[a : a+32]
			return append(append([]byte{'W', w[1], w[2], 0x20}, d...), sum8(d))
		}
		t.Fatalf("unexpected write % X", w)
		return nil
	}}
	ident, got, err := backup(f, func(int) {})
	if err != nil {
		t.Fatal(err)
	}
	if string(ident) != "P31183\xff\xff" || !bytes.Equal(got, mem) || !calibrated(got) {
		t.Fatalf("ident %q, %d bytes", ident, len(got))
	}
	for _, w := range f.sent {
		if w[0] == 'W' {
			t.Fatal("backup sent a write command")
		}
	}
}

func TestAckSkipsStrayBytes(t *testing.T) {
	f := &fake{}
	f.in.Write([]byte{0xE0, 0xA3}) // a stray byte, then the ack (block 0 once failed on the 0xE0)
	if junk, ok := ack(f, time.Second); !ok || !bytes.Equal(junk, []byte{0xE0}) {
		t.Fatalf("ack = % X, %v", junk, ok)
	}
	f.in.Write([]byte{0xE0})
	if _, ok := ack(f, time.Second); ok {
		t.Fatal("no 0xA3 must not count as an ack")
	}
}

func TestImageVersion(t *testing.T) {
	img := append([]byte{0x02, 0x00, 0x03, 0x00}, []byte("0.17.2-1-gabc\x00")...)
	if v := imageVersion("/nowhere/td-h3.bin", img); v != "0.17.2-1-gabc" {
		t.Fatalf("version = %q", v)
	}
	if v := imageVersion("/nowhere/stock.bin", []byte{0x02, 0, 3}); v != "?" {
		t.Fatalf("stock version = %q", v)
	}
}

// bootSim is the bootloader as h3bootloader.bin behaves: A5s until the handshake, then an erase
// that loses the next `lost` packets (it doesn't read the UART while it erases), then each block
// in order answered A3 and nothing for any other. After block0Tries lost block 0s the user
// power-cycles it, holding PTT, while the flasher drains the line.
//
// remote: the radio runs our firmware instead, answers REMOTE's 'B' as 'S' and, PTT pressed at
// once, restarts into the bootloader. armDead: firmware without 'B', which never answers it.
type bootSim struct {
	fake
	lost, blocks, lost0, cycle int
	remote, armDead            bool
	cycleAt                    time.Time // armDead: the user power-cycles once the flasher stops waiting for an answer
}

func (b *bootSim) power(lost int) {
	b.lost, b.blocks = lost, 0
	b.in.Write(bytes.Repeat([]byte{0xA5}, 10))
}

func (b *bootSim) Write(w []byte) (int, error) {
	b.sent = append(b.sent, append([]byte{}, w...))
	if b.remote && string(w) == "B" {
		if b.armDead {
			b.cycleAt = time.Now().Add(1100 * time.Millisecond)
		} else {
			b.in.Write([]byte("t\x00\x0c\x01S\xA5\x5A"))
			b.power(0)
		}
	}
	if len(w) == 36 && (w[0] == 0xA1 || w[0] == 0xA2) {
		switch blk := int(w[1])<<8 | int(w[2]); {
		case b.lost > 0:
			b.lost--
			if blk == 0 {
				if b.lost0++; b.lost0 == block0Tries {
					b.cycle = 3 // the ack's and the drain's empty reads, then the power-cycle lands
				}
			}
		case blk == b.blocks:
			b.blocks++
			b.in.WriteByte(0xA3)
		}
	}
	return len(w), nil
}

func (b *bootSim) Read(p []byte) (int, error) {
	if !b.cycleAt.IsZero() && time.Now().After(b.cycleAt) {
		b.cycleAt = time.Time{}
		b.power(0)
	}
	if b.in.Len() == 0 && b.cycle > 0 {
		if b.cycle--; b.cycle == 0 {
			b.power(0)
		}
	}
	return b.in.Read(p)
}

// flashSim runs flashSteps on b and returns what stays on screen: each line's text after its last
// clear. It fails on a flasher that hangs waiting for A5s that never come.
func flashSim(t *testing.T, b *bootSim) []string {
	var buf bytes.Buffer
	out = &buf
	defer func() { out = os.Stderr }()
	img := append([]byte{0x02, 0x00, 0x03}, make([]byte, 61)...)
	done := make(chan error, 1)
	go func() { _, err := flashSteps(b, img, !b.remote, b.remote); done <- err }()
	select {
	case err := <-done:
		if err != nil {
			t.Fatal(err)
		}
	case <-time.After(3 * time.Second):
		t.Fatal("flash hung")
	}
	if b.blocks != 2 {
		t.Fatalf("%d of 2 blocks written", b.blocks)
	}
	var lines []string
	for _, l := range strings.Split(ansi.Strip(buf.String()), "\n") {
		if i := strings.LastIndex(l, "\r"); i >= 0 {
			l = l[i+1:]
		}
		if l = strings.TrimSpace(l); l != "" {
			lines = append(lines, l)
		}
	}
	if after := buf.String()[strings.Index(buf.String(), "erased"):]; strings.Contains(after, "switch the radio off") {
		t.Fatal("a power-cycle prompt drew after erased")
	}
	return lines
}

func wantLines(t *testing.T, lines []string, want ...string) {
	t.Helper()
	if len(lines) != len(want) {
		t.Fatalf("lines %q, want %q", lines, want)
	}
	for i, w := range want {
		if !strings.Contains(lines[i], w) {
			t.Fatalf("line %d = %q, want %q (all %q)", i, lines[i], w, lines)
		}
	}
}

// The usual first try on the radio: block 0 lands while the bootloader still erases. It goes
// again, with no power-cycle and no warning (it used to say no answer and wait for PTT).
func TestBlock0LostInTheEraseGoesAgain(t *testing.T) {
	b := &bootSim{}
	b.power(1)
	wantLines(t, flashSim(t, b), "[ok] erased", "100%")
	if b.lost0 != 1 {
		t.Fatalf("block 0 lost %d times", b.lost0)
	}
}

// A bootloader that never answers block 0: after block0Tries the flasher asks for a power-cycle
// and goes on by itself once the radio is back.
func TestFlashStepsTellATrueStory(t *testing.T) {
	b := &bootSim{}
	b.power(block0Tries)
	wantLines(t, flashSim(t, b), "[!!] no answer from flash mode", "[ok] flash mode", "[ok] erased", "100%")
}

// A radio answering REMOTE restarts into flash mode on PTT: no power-cycle prompt.
func TestRestartIntoFlashMode(t *testing.T) {
	b := &bootSim{remote: true}
	wantLines(t, flashSim(t, b), "[ok] radio ready to restart into flash mode", "[ok] flash mode", "[ok] erased", "100%")
	if string(b.sent[0]) != "B" {
		t.Fatalf("first sent %q, want B", b.sent[0])
	}
}

// Firmware that never answers 'B' (older than the restart): the power-cycle way, as before.
func TestRestartUnansweredPowerCycles(t *testing.T) {
	b := &bootSim{remote: true, armDead: true}
	wantLines(t, flashSim(t, b), "[ok] flash mode", "[ok] erased", "100%")
}

func TestFlashRefusesUntagged(t *testing.T) {
	dir := t.TempDir()
	for _, ver := range []string{"0.54.2-1", "0.54.2+"} { // a release would go on to wait for the radio
		img := append([]byte{0x02, 0x00, 0x03, 0x00}, []byte(ver+"\x00")...)
		p := filepath.Join(dir, "td-h3.bin")
		if err := os.WriteFile(p, img, 0o644); err != nil {
			t.Fatal(err)
		}
		err := flashFile("/nonexistent-port", p, false, false)
		if err == nil || !strings.Contains(err.Error(), "not a release") {
			t.Errorf("v%s: not refused (%v)", ver, err)
		}
	}
	// No vX.Y.Z tag reachable (a shallow clone): the Makefile stamps "dev", only in build/version.
	os.WriteFile(filepath.Join(dir, "td-h3.bin"), []byte{0x02, 0x00, 0x03, 0x00}, 0o644)
	os.WriteFile(filepath.Join(dir, "version"), []byte("dev\n"), 0o644)
	if err := flashFile("/nonexistent-port", filepath.Join(dir, "td-h3.bin"), false, false); err == nil || !strings.Contains(err.Error(), "not a release") {
		t.Errorf("vdev: not refused (%v)", err)
	}
}

// tdh3 firmware flash IMAGE without -p looks for the cable, as tdh3 flash does (it hung on port "").
func TestFirmwareFlashFindsThePort(t *testing.T) {
	looked := false
	defer func(f func() (string, error)) { findPort = f }(findPort)
	findPort = func() (string, error) { looked = true; return "", errors.New("no cable") }
	p := filepath.Join(t.TempDir(), "H3_250317.BIN")
	os.WriteFile(p, []byte{0x02, 0x00, 0x03, 0x00}, 0o644)
	if err := flashFile("", p, false, false); !looked || err == nil || err.Error() != "no cable" {
		t.Fatalf("looked %v, err %v", looked, err)
	}
}

// A block whose trailing sum was corrupted on the wire is an error, never the data shifted by one
// byte (a "sum first" fallback accepted 1 in 256 of them, and import wrote them back).
func TestReadBlockBadSumIsAnError(t *testing.T) {
	data := make([]byte, 32)
	for i := range data {
		data[i] = byte(i + 1)
	}
	reply := append(append([]byte{'W', 0x00, 0x20, 0x20}, data...), data[0]-sum8(data[1:]))
	if got, err := readBlock(&fake{reply: func([]byte) []byte { return reply }}, 0x0020); err == nil {
		t.Fatalf("bad block accepted: % X", got[:4])
	}
}
