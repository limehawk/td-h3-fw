package main

import (
	"bytes"
	"reflect"
	"strings"
	"testing"
)

func TestParseSlots(t *testing.T) {
	got, err := parseSlots("1-3, 5,2,199")
	must(t, err)
	if !reflect.DeepEqual(got, []int{1, 2, 3, 5, 199}) {
		t.Errorf("got %v", got)
	}
	for _, bad := range []string{"", "0", "200", "5-3", "a", "1-"} {
		if _, err := parseSlots(bad); err == nil {
			t.Errorf("%q accepted", bad)
		}
	}
}

// blank is an EEPROM with no channels; add writes channels the way import does.
func blank() []byte { return bytes.Repeat([]byte{0xFF}, 0x2000) }

func add(mem []byte, chans ...channel) {
	for i := 0; i < 64; i++ {
		mem[usedBase+i] = 0
	}
	apply := func(es map[int][]edit) {
		for addr, list := range es {
			b, err := patch(addr, mem[addr:addr+32], list)
			if err != nil {
				panic(err)
			}
			copy(mem[addr:], b)
		}
	}
	apply(plan(chans, false))
}

var sample = []channel{
	{loc: 1, name: "CALL", rx: 14652000, tx: 14652000, rxTone: noTone, txTone: noTone, scan: true, groups: 0x05, power: powerHigh},
	{loc: 2, name: "RPT", rx: 14694000, tx: 14634000, rxTone: noTone, txTone: 0x1000, scan: true, power: powerMid},
	{loc: 3, name: "TSQ", rx: 44500000, tx: 45000000, rxTone: 0x1413, txTone: 0x1413, narrow: true, power: powerULow},
	{loc: 4, name: "DCS", rx: 44610000, tx: 44610000, rxTone: 0xC754, txTone: 0x8023},
	{loc: 5, name: "SAME", rx: 44620000, tx: 44620000, rxTone: 0x8023, txTone: 0x8023},
	{loc: 6, name: "CROSS", rx: 14665500, tx: 14605500, rxTone: 0x0770, txTone: 0x1000, scan: true},
	{loc: 7, name: "WX1", rx: 16255000, rxTone: noTone, txTone: noTone},
	{loc: 8, name: "TONEDCS", rx: 44630000, tx: 44630000, rxTone: 0x8125, txTone: 0x1000},
	{loc: 9, name: "USB", rx: 14520000, tx: 14520000, rxTone: noTone, txTone: noTone, mode: 2},
}

func TestDecodeReadsWhatImportWrites(t *testing.T) {
	mem := blank()
	add(mem, sample...)
	for _, c := range sample {
		if got := decode(mem, c.loc); !reflect.DeepEqual(got, c) {
			t.Errorf("channel %d:\n got %+v\nwant %+v", c.loc, got, c)
		}
	}
}

func TestExportThenImportGivesTheSameChannels(t *testing.T) {
	var b bytes.Buffer
	must(t, exportCSV(&b, sample))
	res, err := parseCSV(strings.NewReader(b.String()))
	must(t, err)
	if len(res.warns) != 0 {
		t.Errorf("warnings: %v", res.warns)
	}
	if !reflect.DeepEqual(res.chans, sample) {
		for i := range sample {
			if !reflect.DeepEqual(res.chans[i], sample[i]) {
				t.Errorf("channel %d:\n got %+v\nwant %+v", sample[i].loc, res.chans[i], sample[i])
			}
		}
	}
}

func applyEdits(t *testing.T, mem []byte, e chanEdits) {
	t.Helper()
	for addr, list := range e {
		must(t, writable(addr))
		b, err := patch(addr, mem[addr:addr+32], list)
		must(t, err)
		copy(mem[addr:], b)
	}
}

func TestSetChangesOnlyWhatIsGiven(t *testing.T) {
	mem := blank()
	add(mem, sample...)
	mem[0x10*2+13] |= 1 << 2 // busy lockout, which no flag touches
	before := bytes.Clone(mem)
	p := powerULow
	n := true
	e, err := chanChange{power: &p, narrow: &n}.edits(mem, []int{2})
	must(t, err)
	applyEdits(t, mem, e)
	c, want := decode(mem, 2), sample[1]
	want.power, want.narrow = powerULow, true
	if !reflect.DeepEqual(c, want) {
		t.Errorf("got %+v", c)
	}
	if mem[0x10*2+13]>>2&1 != 1 {
		t.Error("busy lockout lost")
	}
	mem[0x10*2+14], before[0x10*2+14] = 0, 0
	if !bytes.Equal(mem, before) {
		t.Error("something besides channel 2's byte 14 changed")
	}
}

func TestSetFrequencyAndShift(t *testing.T) {
	mem := blank()
	add(mem, sample...)
	rx := uint32(14700000)
	e, err := chanChange{rx: &rx}.edits(mem, []int{2}) // minus 0.6 is kept
	must(t, err)
	applyEdits(t, mem, e)
	if c := decode(mem, 2); c.rx != 14700000 || c.tx != 14640000 {
		t.Errorf("rx %d tx %d", c.rx, c.tx)
	}
	plus, off := "+", uint32(500000)
	e, err = chanChange{duplex: &plus, offset: &off}.edits(mem, []int{1})
	must(t, err)
	applyEdits(t, mem, e)
	if c := decode(mem, 1); c.tx != 14652000+500000 {
		t.Errorf("tx %d", c.tx)
	}
	if _, err := (chanChange{duplex: &plus}).edits(mem, []int{5}); err == nil {
		t.Error("+ with no offset on a simplex channel accepted")
	}
	if _, err := (chanChange{offset: &off}).edits(mem, []int{5}); err == nil {
		t.Error("offset alone on a simplex channel accepted")
	}
	dup := "off"
	e, err = chanChange{duplex: &dup}.edits(mem, []int{2})
	must(t, err)
	applyEdits(t, mem, e)
	if decode(mem, 2).tx != 0 {
		t.Error("duplex off still transmits")
	}
}

func TestParseTone(t *testing.T) {
	for in, want := range map[string]tone{"off": noTone, "100.0": 0x1000, "D023N": 0x8023, "d754i": 0xC754, "D125": 0x8125, "D754R": 0xC754} {
		if got, err := parseTone(in); err != nil || got != want {
			t.Errorf("%s = %x, %v", in, uint16(got), err)
		}
	}
	for _, bad := range []string{"loud", "D999", "10"} {
		if _, err := parseTone(bad); err == nil {
			t.Errorf("%q accepted", bad)
		}
	}
}

func TestGroupsSetsTheMark(t *testing.T) {
	mem := blank()
	add(mem, sample...)
	mem[0x10*4+13] &^= 3 // a channel CHIRP wrote: no mark
	g := byte(0x82)
	e, err := chanChange{groups: &g}.edits(mem, []int{4})
	must(t, err)
	applyEdits(t, mem, e)
	if c := decode(mem, 4); c.groups != 0x82 {
		t.Errorf("groups %02x", c.groups)
	}
}

func TestClearMoveLock(t *testing.T) {
	mem := blank()
	add(mem, sample...)
	e, err := moveEdits(mem, 2, 50)
	must(t, err)
	applyEdits(t, mem, e)
	got, want := decode(mem, 50), sample[1]
	want.loc = 50
	if !reflect.DeepEqual(got, want) || bitOn(mem, usedBase, 2) {
		t.Errorf("moved: %+v, old used %v", got, bitOn(mem, usedBase, 2))
	}
	if _, err := moveEdits(mem, 1, 50); err == nil {
		t.Error("move onto a used channel accepted")
	}
	if _, err := moveEdits(mem, 2, 60); err == nil {
		t.Error("move of an empty channel accepted")
	}
	e, err = lockEdits(mem, []int{1}, true)
	must(t, err)
	applyEdits(t, mem, e)
	if decode(mem, 1).tx != 0 {
		t.Error("locked channel still transmits")
	}
	e, err = lockEdits(mem, []int{1}, false)
	must(t, err)
	applyEdits(t, mem, e)
	if c := decode(mem, 1); c.tx != c.rx {
		t.Errorf("unlocked tx %d", c.tx)
	}
	e, err = clearEdits(mem, []int{1})
	must(t, err)
	applyEdits(t, mem, e)
	if bitOn(mem, usedBase, 1) || !bytes.Equal(mem[0x10:0x20], bytes.Repeat([]byte{0xFF}, 16)) {
		t.Error("not cleared")
	}
	if _, err := clearEdits(mem, []int{1}); err == nil {
		t.Error("clearing an empty channel accepted")
	}
}

func TestEditsWriteNothingOnABadSlot(t *testing.T) {
	mem := blank()
	add(mem, sample...)
	p := powerLow
	if _, err := (chanChange{power: &p}).edits(mem, []int{1, 77}); err == nil {
		t.Error("empty channel in the list accepted")
	}
}
