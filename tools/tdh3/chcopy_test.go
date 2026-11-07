package main

import (
	"bytes"
	"errors"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

// srcEEPROM is radio 1: a different pattern from seededEEPROM everywhere, so any byte copied by
// mistake shows up.
func srcEEPROM() []byte {
	mem := make([]byte, 0x2000)
	for i := range mem {
		mem[i] = byte(i*13 + 101)
	}
	return mem
}

// The copied byte ranges, written out by hand from the EEPROM map (docs/HARDWARE.md).
func copiedBits(a int, fm bool) byte {
	switch {
	case a >= 0x0010 && a < 0x0C80, a >= 0x0D40 && a < 0x1378, a >= 0x1900 && a < 0x1918, a >= 0x1920 && a < 0x1938:
		return 0xFF
	case a == 0x1918 || a == 0x1938: // channels 193-199; bit 7 would be channel 200
		return 0x7F
	case fm && (a >= 0x0CD0 && a < 0x0D34 || a >= 0x1940 && a < 0x1943):
		return 0xFF
	case fm && a == 0x1943: // preset 25
		return 0x01
	}
	return 0
}

func TestCopyWritesOnlyChannelBytes(t *testing.T) {
	for _, fm := range []bool{true, false} {
		src, seed := srcEEPROM(), seededEEPROM()
		mem := bytes.Clone(seed)
		f, writes := cloneRadio(t, mem)
		var saved []byte
		spans := spansFor(fm)
		n, err := copySession(f, copyPlan(src, spans), spans, func(ident, m []byte) error {
			saved = append(bytes.Clone(ident), m...)
			return nil
		})
		must(t, err)
		if !bytes.Equal(saved[8:], seed) || string(saved[:8]) != "P31183\xff\xff" {
			t.Fatalf("fm=%v: backup is not the radio as it was before the copy", fm)
		}
		for a := range mem {
			m := copiedBits(a, fm)
			if want := seed[a]&^m | src[a]&m; mem[a] != want {
				t.Fatalf("fm=%v: byte %04X = %02X, want %02X (copied mask %02X)", fm, a, mem[a], want, m)
			}
		}
		for _, a := range *writes {
			if a >= 0x1FC0 || a+32 > 0x1F50 && a < 0x1F80 {
				t.Fatalf("fm=%v: wrote block %04X (settings or calibration)", fm, a)
			}
		}
		if n != len(*writes) || n == 0 {
			t.Fatalf("fm=%v: %d written, %d writes", fm, n, len(*writes))
		}
		if last := f.sent[len(f.sent)-1]; string(last) != "E" {
			t.Errorf("session ended with % X, want E", last)
		}
	}
}

func TestCopyVerifyCatchesAMismatch(t *testing.T) {
	src, mem := srcEEPROM(), seededEEPROM()
	f, _ := cloneRadio(t, mem)
	honest := f.reply
	f.reply = func(w []byte) []byte { // the radio acks block 0D40 but stores one name byte wrong
		r := honest(w)
		if len(w) == 37 && w[0] == 'W' && w[1] == 0x0D && w[2] == 0x40 {
			mem[0x0D41] ^= 1
		}
		return r
	}
	spans := spansFor(true)
	_, err := copySession(f, copyPlan(src, spans), spans, func(_, _ []byte) error { return nil })
	if err == nil || !strings.Contains(err.Error(), "0D41") {
		t.Fatalf("err = %v, want a read-back mismatch at 0D41", err)
	}
}

func TestCopyWritesNothingWithoutABackup(t *testing.T) {
	f, writes := cloneRadio(t, seededEEPROM())
	spans := spansFor(true)
	if _, err := copySession(f, copyPlan(srcEEPROM(), spans), spans, func(_, _ []byte) error { return errors.New("disk full") }); err == nil {
		t.Fatal("copy went on after the backup failed")
	}
	if len(*writes) != 0 {
		t.Fatalf("%d blocks written", len(*writes))
	}
}

func TestLoadImageChecksSizeAndIdent(t *testing.T) {
	dir := t.TempDir()
	put := func(name string, b []byte) string {
		p := filepath.Join(dir, name)
		must(t, os.WriteFile(p, b, 0o644))
		return p
	}
	good := append([]byte("P31183\xff\xff"), srcEEPROM()...)
	if mem, err := loadImage(put("good.bin", good)); err != nil || len(mem) != 0x2000 {
		t.Fatalf("good image: %v", err)
	}
	for name, b := range map[string][]byte{
		"short.bin": good[:len(good)-1],
		"bare.bin":  srcEEPROM(), // 8192 bytes, no ident
		"other.bin": append([]byte("BF-F8HP\x00"), srcEEPROM()...),
	} {
		if _, err := loadImage(put(name, b)); err == nil {
			t.Errorf("%s accepted", name)
		}
	}
}

