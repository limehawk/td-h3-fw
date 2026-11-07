package main

import (
	"bytes"
	"slices"
	"testing"
)

func TestResetWritableAddsOnlyTheSettingsMagicBlock(t *testing.T) {
	for addr := -64; addr <= 0x2100; addr += 32 {
		if got, want := resetWritable(addr) == nil, writable(addr) == nil || addr == 0x1FC0; got != want {
			t.Errorf("resetWritable(%04X) ok=%v, want %v", addr, got, want)
		}
	}
	for _, addr := range []int{0x1FA0, 0x1FC1, 0x1FE0, 0x1F40, 0x1F50, 0x1F60, 0x1F70, 0x1FF0, 0x2000} {
		if resetWritable(addr) == nil {
			t.Errorf("resetWritable(%04X) accepted", addr)
		}
	}
}

// restartingRadio is cloneRadio that, like td-h3-fw, restarts (stops answering) after the E that
// ends a session which wrote 0x1FC0; old makes it older firmware, which stays in PC LINK.
func restartingRadio(t *testing.T, mem []byte, old bool) *fake {
	f, _ := cloneRadio(t, mem)
	inner, hit, gone := f.reply, false, false
	f.reply = func(w []byte) []byte {
		if gone {
			return nil
		}
		hit = hit || len(w) == 37 && w[0] == 'W' && w[1] == 0x1F && w[2] == 0xC0
		r := inner(w)
		gone = string(w) == "E" && hit && !old
		return r
	}
	return f
}

func TestResetClearsOnlyTheMagicThenRestarts(t *testing.T) {
	seed := seededEEPROM()
	seed[0x1FC0] = 0xAD
	mem := bytes.Clone(seed)
	f := restartingRadio(t, mem, false)
	n, err := importBlocks(f, resetPlan(false), resetWritable, quiet)
	must(t, err)
	want := bytes.Clone(seed)
	want[0x1FC0] = 0
	if n != 1 || !bytes.Equal(mem, want) {
		t.Fatalf("n=%d; EEPROM changed beyond 1FC0's byte 0", n)
	}
	var cmds []string // after the handshake: read, write, read back, end
	for _, w := range f.sent[3:] {
		cmds = append(cmds, string(w[:1]))
	}
	if !slices.Equal(cmds, []string{"R", "W", "R", "E"}) {
		t.Errorf("sent %q", cmds)
	}
	if stillInPCLink(f) {
		t.Error("restarted radio taken as still in PC LINK")
	}
}

func TestResetChannelsClearsUsedBitsOnly(t *testing.T) {
	seed := seededEEPROM()
	mem := bytes.Clone(seed)
	f, writes := cloneRadio(t, mem)
	_, err := importBlocks(f, resetPlan(true), resetWritable, quiet)
	must(t, err)
	if !slices.Equal(*writes, []int{0x1900, 0x1FC0}) { // channels first, the settings last, before E
		t.Fatalf("wrote blocks %04X", *writes)
	}
	want := bytes.Clone(seed)
	for i := 0x1900; i < 0x1919; i++ { // channels 1-199; channel 200's bit (0x1918 bit 7) is not ours
		want[i] = 0
	}
	want[0x1918] = seed[0x1918] & 0x80
	want[0x1FC0] = 0
	if !bytes.Equal(mem, want) {
		for i := range mem {
			if mem[i] != want[i] {
				t.Errorf("EEPROM[%04X] = %02X, want %02X", i, mem[i], want[i])
			}
		}
	}
}

func TestResetOnOlderFirmwareSaysSwitchOff(t *testing.T) {
	f := restartingRadio(t, seededEEPROM(), true)
	_, err := importBlocks(f, resetPlan(false), resetWritable, quiet)
	must(t, err)
	if !stillInPCLink(f) {
		t.Error("older firmware (still in PC LINK) not noticed")
	}
}
