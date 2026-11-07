package main

import (
	"bytes"
	"errors"
	"fmt"
	"io"
	"time"
)

// port is what both protocols need from a serial port (go.bug.st/serial satisfies it).
type port interface {
	io.ReadWriter
	SetReadTimeout(time.Duration) error
}

// readN reads n bytes or gives up after d.
func readN(p port, n int, d time.Duration) []byte {
	p.SetReadTimeout(50 * time.Millisecond)
	buf, tmp, end := []byte{}, make([]byte, 64), time.Now().Add(d)
	for len(buf) < n && time.Now().Before(end) {
		k, err := p.Read(tmp[:min(64, n-len(buf))])
		if err != nil {
			break
		}
		buf = append(buf, tmp[:k]...)
	}
	return buf
}

func sum8(b []byte) (s byte) {
	for _, x := range b {
		s += x
	}
	return
}

// ---- clone protocol (38400). Facts from nicsure's protocol.txt notes and CHIRP chirp/drivers/tdh8.py ----

var magic = []byte("PVOJH\x5c\x14")

var errNoHello = errors.New("no answer to the TD-H3 hello: radio off, wrong port, or other firmware")

// handshake starts a clone session: hello (retried), ident, accept. It returns the radio's ident.
func handshake(p port) (ident []byte, err error) {
	ok := false
	for range 20 { // td-h3-fw drops the first hello while it switches to PC LINK
		p.Write(magic)
		if bytes.Equal(readN(p, 1, 300*time.Millisecond), []byte{6}) {
			ok = true
			break
		}
	}
	if !ok {
		return nil, errNoHello
	}
	readN(p, 64, 400*time.Millisecond) // a late ack to a retried hello would shift every reply after it
	p.Write([]byte{2})
	ident = readN(p, 8, time.Second)
	p.Write([]byte{6})
	if a := readN(p, 1, time.Second); !bytes.Equal(a, []byte{6}) {
		return nil, fmt.Errorf("radio refused clone (% x)", a)
	}
	return ident, nil
}

// readBlock reads the 32 bytes at addr (32-byte aligned).
func readBlock(p port, addr int) ([]byte, error) {
	cmd := []byte{'R', byte(addr >> 8), byte(addr), 0x20}
	p.Write(cmd)
	r := readN(p, 37, 2*time.Second)
	if len(r) != 37 || r[0] != 'W' || !bytes.Equal(r[1:4], cmd[1:]) {
		return nil, fmt.Errorf("block %04X: bad reply % x", addr, r)
	}
	data := r[4:36] // W a a n [32] sum, as clone.c, stock and CHIRP send it (HARDWARE.md)
	if sum8(data) != r[36] {
		return nil, fmt.Errorf("block %04X: checksum", addr)
	}
	return data, nil
}

// backup reads all 8 KB of EEPROM. It only ever sends hello, ident, R and E.
func backup(p port, onBlock func(int)) (ident, mem []byte, err error) {
	if ident, err = handshake(p); err != nil {
		return nil, nil, err
	}
	defer p.Write([]byte("E"))
	mem, err = readAll(p, onBlock)
	return ident, mem, err
}

// readAll reads all 8 KB inside a clone session that has already started.
func readAll(p port, onBlock func(int)) (mem []byte, err error) {
	for addr := 0; addr < 0x2000; addr += 0x20 {
		data, err := readBlock(p, addr)
		if err != nil {
			return nil, err
		}
		mem = append(mem, data...)
		onBlock(addr / 0x20)
	}
	return mem, nil
}

// calibrated reports whether the factory TX-power block is there (stock's magic 0x85 at 0x1FF0).
func calibrated(mem []byte) bool { return len(mem) > 0x1FF0 && mem[0x1FF0] == 0x85 }

// ---- bootloader (115200). Facts from our disassembly of h3bootloader.bin and nicsure's
// firmware_flash_protocol.txt notes; the same bytes nicsure's TDH3Flash sends, none of its code ----

const appMax = 0xF800 // bootloader owns 0xF800-0xFFFF and erases only below it

func check(img []byte) error {
	if len(img) == 0 || len(img) > appMax {
		return fmt.Errorf("%d bytes: must be 1..%d (0xF800); larger would program over the bootloader", len(img), appMax)
	}
	if len(img) < 3 || img[0] != 0x02 {
		return fmt.Errorf("no LJMP reset vector at 0x0000 (first byte 0x%02X)", img[0])
	}
	if t := int(img[1])<<8 | int(img[2]); t >= len(img) {
		return fmt.Errorf("reset vector jumps to 0x%04X, outside the %d-byte image", t, len(img))
	}
	return nil
}

// packets: 36 bytes each, id (A1, last A2), block BE16, sum8, 32 data zero-padded.
func packets(img []byte) [][]byte {
	n := (len(img) + 31) / 32
	out := make([][]byte, n)
	for blk := range n {
		data := make([]byte, 32) // the last block is zero-padded
		copy(data, img[blk*32:])
		id := byte(0xA1)
		if blk == n-1 {
			id = 0xA2
		}
		out[blk] = append([]byte{id, byte(blk >> 8), byte(blk), sum8(data)}, data...)
	}
	return out
}

var hello = append([]byte{0xA0, 0xEE, 0x74, 0x71, 0x07, 0x74}, bytes.Repeat([]byte{0x55}, 30)...)

const bootA5s = 8 // the bootloader sends nothing but A5 while it waits: a run this long never comes from REMOTE

// What flash tells its caller while it waits on the radio.
type flashEvent int

const (
	flashFound    flashEvent = iota // the bootloader answered: erasing
	flashNoAnswer                   // block 0 went unanswered: power-cycle with PTT held, flash starts over
)

// bootHandshake waits for bootA5s A5 bytes in a row (our firmware's REMOTE stream holds A5 bytes too,
// its sync is A5 5A, and twice a lone A5 in it passed for the bootloader, so blocks went to the
// running radio), answers hello, and returns once the A5s have stopped for 500 ms: the bootloader
// then waits for block 0. Before that, 500 ms with nothing starts the count again.
func bootHandshake(p port, on func(flashEvent)) error {
	const quiet = 500 * time.Millisecond
	run, answered, last := 0, false, time.Now()
	for {
		b := readN(p, 1, 100*time.Millisecond)
		if len(b) == 0 {
			switch {
			case time.Since(last) < quiet:
			case answered:
				return nil
			default:
				run = 0
			}
			continue
		}
		last = time.Now()
		switch {
		case b[0] != 0xA5 && answered:
			return fmt.Errorf("unexpected 0x%02X during handshake", b[0])
		case b[0] != 0xA5:
			run = 0
		case !answered:
			if run++; run == bootA5s {
				answered = true
				on(flashFound)
				if _, err := p.Write(hello); err != nil {
					return err
				}
			}
		}
	}
}

func flash(p port, img []byte, on func(flashEvent), progressFn func(float64)) error {
	pk := packets(img)
again:
	if err := bootHandshake(p, on); err != nil {
		return err
	}
	for i, pkt := range pk {
		for try := 1; ; try++ {
			if _, err := p.Write(pkt); err != nil {
				return fmt.Errorf("block %d: %w", i, err)
			}
			junk, ok := ack(p, time.Second)
			if ok {
				break
			}
			if i > 0 {
				return fmt.Errorf("block %d: no ack (% x); power-cycle with PTT held and flash again", i, junk)
			}
			if try < block0Tries {
				continue
			}
			// Still nothing: start over from a fresh power-on.
			on(flashNoAnswer)
			for len(readN(p, 64, time.Second)) > 0 {
			}
			goto again
		}
		progressFn(float64(i+1) / float64(len(pk)))
	}
	return nil
}

// block0Tries is how often block 0 goes, a second apart, before the flasher asks for a power-cycle.
// The bootloader erases right after the handshake without reading the UART (h3bootloader.bin
// 0x01A5-0x01CD: 124 sector erases, no SCON1 poll) and says nothing when it's done, so a block 0
// that comes before the erase ends is lost but for a byte, which it drops after a short idle
// (0x0103-0x0126). Sent 500 ms after the A5s stop, block 0 came too early on most first tries (a
// programmed flash likely erases slower than the blank one a power-cycle retry finds, which got
// through). The bootloader waits for it with no timeout, so sending it again is enough.
const block0Tries = 10

// ack waits up to d for the bootloader's 0xA3, skipping stray bytes before it (a block 0 once
// came back as a lone 0xE0 before the ack).
func ack(p port, d time.Duration) ([]byte, bool) {
	var junk []byte
	deadline := time.Now().Add(d)
	for time.Now().Before(deadline) {
		b := readN(p, 1, time.Until(deadline))
		if len(b) == 0 {
			break
		}
		if b[0] == 0xA3 {
			return junk, true
		}
		junk = append(junk, b[0])
	}
	return junk, false
}

// progressBar draws the bar plus blocks, kilobytes, elapsed time and time left.
func progressBar(size int) func(float64) {
	last, facts := -1, progressFacts(size)
	return func(f float64) {
		s := facts(f)                         // first, so the clock starts on the first block
		if pct := int(f * 100); pct == last { // redraw once per percent, not per block
			return
		} else {
			last = pct
		}
		fmt.Fprintf(out, "\r\x1b[2K  %s  %s", bar(f, 30), s)
		if f >= 1 {
			fmt.Fprintln(out)
		}
	}
}

// progressFacts returns the numbers line after the percent: blocks, kilobytes, elapsed time and
// time left, timed from its first call (the first block, not the wait for flash mode).
func progressFacts(size int) func(float64) string {
	blocks := (size + 31) / 32
	var start time.Time
	return func(f float64) string {
		if start.IsZero() {
			start = time.Now()
		}
		el := time.Since(start)
		left := ""
		if f > 0.02 && f < 1 {
			left = fmt.Sprintf("  %s left", (time.Duration(float64(el)/f) - el).Round(time.Second))
		}
		return noteS.Render(fmt.Sprintf("%*d/%d blocks  %4.1f/%.1f KB  %s%s",
			len(fmt.Sprint(blocks)), int(f*float64(blocks)+0.5), blocks, f*float64(size)/1024, float64(size)/1024, el.Round(time.Second), left))
	}
}
