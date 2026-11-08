package main

import (
	"bytes"
	"testing"
)

func TestRestoreWritableAddsOnlyTheSettingsBlocks(t *testing.T) {
	for addr := -64; addr <= 0x2100; addr += 32 {
		want := writable(addr) == nil || addr == 0x1FC0 || addr == 0x1FE0
		if got := restoreWritable(addr) == nil; got != want {
			t.Errorf("restoreWritable(%04X) ok=%v, want %v", addr, got, want)
		}
	}
}

func TestRestoreWritesOnlyTheSettingsThenRestarts(t *testing.T) {
	radio := seededEEPROM()
	radio[0x1FC0] = 0 // just reset
	backup := bytes.Clone(radio)
	for a := 0x1FC0; a < 0x1FF0; a++ {
		backup[a] = byte(a * 7)
	}
	backup[0x1FC0] = 0xAD
	for a := 0x1FF0; a < 0x2000; a++ {
		backup[a] = 0x11 // another radio's calibration: never sent
	}
	backup[5] ^= 0xFF // and its channels: never sent
	es, err := restorePlan(backup)
	must(t, err)
	mem := bytes.Clone(radio)
	f := restartingRadio(t, mem, false)
	n, err := importBlocks(f, es, restoreWritable, quiet)
	must(t, err)
	want := bytes.Clone(radio)
	copy(want[0x1FC0:0x1FF0], backup[0x1FC0:0x1FF0])
	if n != 2 || !bytes.Equal(mem, want) {
		t.Fatalf("n=%d; the EEPROM isn't the radio's with only 1FC0-1FEF from the backup", n)
	}
	if stillInPCLink(f) {
		t.Error("restarted radio taken as still in PC LINK")
	}
}

func TestRestoreRefusesABackupWithoutSettings(t *testing.T) {
	for _, m := range []byte{0x00, 0xFF, 0xAA} {
		mem := seededEEPROM()
		mem[0x1FC0] = m
		if _, err := restorePlan(mem); err == nil {
			t.Errorf("check byte %02X accepted", m)
		}
	}
}
