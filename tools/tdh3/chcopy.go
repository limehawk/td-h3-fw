// tdh3 channels copy: put the channel list of another TD-H3 (its `tdh3 eeprom backup` file) on
// the connected radio. Only the bytes in copySpans (and fmSpans with --fm) change; every other byte
// of the radio's EEPROM, settings and calibration included, is written back as it was read, and a
// block is refused if it would change anything else. Order: show the plan, wait for Enter, read the
// whole radio and save it to backups/, write the changed blocks, read every planned block back.
package main

import (
	"bytes"
	"encoding/binary"
	"errors"
	"fmt"
	"maps"
	"os"
	"path/filepath"
	"slices"
	"strings"
	"time"
)

// span is a half-open byte range copied from the source; last masks its final byte (a bitmap's
// last byte holds fewer than 8 entries: bits past channel 199 or preset 25 stay as they are).
type span struct {
	from, to int
	last     byte
	what     string
}

var (
	copySpans = []span{
		{0x0010, 0x0C80, 0xFF, "channel records 1-199 (frequency, tones, power, scan groups)"},
		{0x0D40, 0x1378, 0xFF, "channel names"},
		{0x1900, 0x1919, 0x7F, "which channels exist (bitmap)"},
		{0x1920, 0x1939, 0x7F, "scan list (bitmap)"},
	}
	fmSpans = []span{
		{0x0CD0, 0x0D34, 0xFF, "FM radio presets 1-25"},
		{0x1940, 0x1944, 0x01, "which FM presets exist (bitmap)"},
	}
)

const imageSize = 8 + 0x2000 // what `eeprom backup` writes: the 8-byte ident, then the EEPROM

// tdh3Ident: P31183 (TD-H3), P31184 (GMRS) or P31185 (HAM), as the clone protocol answers 02.
func tdh3Ident(id []byte) bool {
	return len(id) == 8 && bytes.HasPrefix(id, []byte("P3118")) && id[5] >= '3' && id[5] <= '5'
}

// loadImage reads an `eeprom backup` file and returns its EEPROM bytes.
func loadImage(path string) ([]byte, error) {
	b, err := os.ReadFile(path)
	if err != nil {
		return nil, err
	}
	if len(b) != imageSize {
		return nil, fmt.Errorf("%s is %d bytes, an eeprom backup file is %d: not a TD-H3 backup", path, len(b), imageSize)
	}
	if !tdh3Ident(b[:8]) {
		return nil, fmt.Errorf("%s starts %q, not a TD-H3 ident (P31183): not a TD-H3 backup", path, b[:8])
	}
	return b[8:], nil
}

func spansFor(fm bool) []span {
	if fm {
		return append(slices.Clone(copySpans), fmSpans...)
	}
	return copySpans
}

// mask is the copied bits of byte a (0: the byte is not copied).
func mask(a int, spans []span) byte {
	for _, s := range spans {
		if a >= s.from && a < s.to {
			if a == s.to-1 {
				return s.last
			}
			return 0xFF
		}
	}
	return 0
}

// copyPlan lists the edits that make the copied bits equal src, by 32-byte block.
func copyPlan(src []byte, spans []span) map[int][]edit {
	es := map[int][]edit{}
	for _, s := range spans {
		for a := s.from; a < s.to; a++ {
			es[a&^31] = append(es[a&^31], edit{a, mask(a, spans), src[a]})
		}
	}
	return es
}

// ---- reading an image ----

func bitOn(mem []byte, base, n int) bool { return mem[base+(n-1)/8]>>((n-1)&7)&1 == 1 }

func unbcd(v uint32) (n uint32) {
	for i := 28; i >= 0; i -= 4 {
		n = n*10 + v>>i&0xF
	}
	return
}

func chanName(mem []byte, n int) string {
	b := mem[nameBase+8*(n-1) : nameBase+8*n]
	if i := bytes.IndexByte(b, 0xFF); i >= 0 {
		b = b[:i]
	}
	return strings.TrimRight(string(b), "\x00 ")
}

func usedChannels(mem []byte) (ns []int) {
	for n := 1; n <= maxChan; n++ {
		if bitOn(mem, usedBase, n) {
			ns = append(ns, n)
		}
	}
	return
}

// fmCount counts the presets the FM radio would show (fm.c preset_get: not FF, 76.0-108.0 MHz).
func fmCount(mem []byte) (k int) {
	for a := 0x0CD0; a < 0x0D34; a += 4 {
		if v := unbcd(binary.LittleEndian.Uint32(mem[a:])); mem[a] != 0xFF && v >= 760 && v <= 1080 {
			k++
		}
	}
	return
}

// ---- the clone session ----

// copySession reads the whole radio, hands it to save, writes the planned blocks that change and
// reads every planned block back. Nothing is written if save fails.
func copySession(p port, es map[int][]edit, spans []span, save func(ident, mem []byte) error) (written int, err error) {
	ident, err := handshake(p)
	if err != nil {
		return 0, err
	}
	defer func() {
		p.Write([]byte("E"))
		if a := readN(p, 1, time.Second); err == nil && !bytes.Equal(a, []byte{6}) {
			err = fmt.Errorf("radio did not acknowledge the end of the clone (% x)", a)
		}
	}()
	if !tdh3Ident(ident) {
		return 0, fmt.Errorf("the connected radio says %q, not a TD-H3: nothing written", ident)
	}
	mem, err := readAll(p, func(int) {})
	if err != nil {
		return 0, fmt.Errorf("reading the radio before writing: %w (nothing written)", err)
	}
	if err := save(ident, mem); err != nil {
		return 0, fmt.Errorf("saving the backup: %w (nothing written)", err)
	}
	addrs := slices.Sorted(maps.Keys(es))
	for _, addr := range addrs {
		old := mem[addr : addr+32]
		blk, err := patch(addr, old, es[addr])
		if err != nil {
			return written, err
		}
		if bytes.Equal(blk, old) {
			continue
		}
		for i := range blk { // last check before the wire: only copied bits may change
			if (blk[i]^old[i])&^mask(addr+i, spans) != 0 {
				return written, fmt.Errorf("block %04X would change byte %04X, outside the channel areas: refused", addr, addr+i)
			}
		}
		if err := sendBlock(p, addr, blk); err != nil {
			return written, err
		}
		written++
	}
	var bad []string
	for _, addr := range addrs {
		got, err := readBlock(p, addr)
		if err != nil {
			return written, fmt.Errorf("reading back: %w", err)
		}
		want, _ := patch(addr, mem[addr:addr+32], es[addr])
		for i := range got {
			if got[i] != want[i] {
				bad = append(bad, fmt.Sprintf("%04X", addr+i))
			}
		}
	}
	if bad != nil {
		return written, fmt.Errorf("read-back differs at %d bytes (%s): run the copy again", len(bad), strings.Join(bad[:min(len(bad), 8)], " "))
	}
	return written, nil
}

// ---- command ----

func runCopy(portName, from string, fm, ask bool) error {
	src, err := loadImage(from)
	if err != nil {
		return err
	}
	used := usedChannels(src)
	if len(used) == 0 {
		return fmt.Errorf("%s has no channels: copying it would empty this radio, so nothing was done", from)
	}
	if portName == "" {
		if portName, err = findPort(); err != nil {
			return err
		}
	}
	if ask && !isTerm(os.Stdin) {
		return errors.New("not a terminal to press Enter in: pass --yes")
	}
	spans := spansFor(fm)
	backupPath := filepath.Join("backups", "td-h3-"+time.Now().Format("20060102-150405")+".bin")

	say("")
	row("from", valS.Render(filepath.Base(from))+noteS.Render(fmt.Sprintf("  %d channels", len(used))))
	for _, n := range used[:min(len(used), 5)] {
		rx := unbcd(binary.LittleEndian.Uint32(src[0x10*n:]))
		say(noteS.Render(fmt.Sprintf("           %3d  %-8s  %s", n, chanName(src, n), mhz(rx))))
	}
	if len(used) > 5 {
		say(noteS.Render(fmt.Sprintf("           ... and %d more", len(used)-5)))
	}
	row("to", valS.Render("the radio on "+portName)+noteS.Render(": its channels 1-199 become these"))
	if fm {
		row("FM", fmt.Sprintf("%d presets copied too", fmCount(src))+noteS.Render(" (--fm=false keeps this radio's)"))
	} else {
		row("FM", "this radio's presets are kept")
	}
	row("writes", "")
	for _, s := range spans {
		say(noteS.Render(fmt.Sprintf("           %04X-%04X  %s", s.from, s.to-1, s.what)))
	}
	row("keeps", "every setting, the calibration, VFOs, keys, power-on message: all the rest")
	row("backup", "this radio first, to "+valS.Render(backupPath))
	if ask {
		end := watchStop()
		err := enter(ambS.Render("Enter") + " copies" + noteS.Render(" (radio in PC LINK: MENU, 4 4, MENU), Ctrl-C stops"))
		end()
		if err != nil {
			return err
		}
	}

	p, err := open(portName, 38400)
	if err != nil {
		return err
	}
	defer p.Close()
	sp := spin("reading this radio")
	written, err := copySession(p, copyPlan(src, spans), spans, func(ident, mem []byte) error {
		if err := os.MkdirAll(filepath.Dir(backupPath), 0o755); err != nil {
			return err
		}
		if err := os.WriteFile(backupPath, append(bytes.Clone(ident), mem...), 0o644); err != nil {
			return err
		}
		sp.set("writing the channels")
		return nil
	})
	sp.stop()
	if fileExists(backupPath) {
		okLine("backup saved: " + backupPath)
	}
	if err != nil && written > 0 {
		return fmt.Errorf("stopped part-way, the radio may hold a mix of old and new channels (run the copy again; its old memory is in %s): %w", backupPath, err)
	}
	if err != nil {
		return err
	}
	okLine(fmt.Sprintf("%d channels copied and checked (%d blocks written)", len(used), written))
	return nil
}
