package main

import (
	"bytes"
	"encoding/hex"
	"slices"
	"strings"
	"testing"
)

func unhex(s string) []byte {
	b, err := hex.DecodeString(strings.ReplaceAll(s, " ", ""))
	if err != nil {
		panic(err)
	}
	return b
}

// header is CHIRP's own column order, with the columns this tool ignores left in.
const header = "Location,Name,Frequency,Duplex,Offset,Tone,rToneFreq,cToneFreq,DtcsCode,DtcsPolarity,RxDtcsCode,CrossMode,Mode,TStep,Skip,Power,Comment,URCALL,RPT1CALL,RPT2CALL,DVCODE\n"

func TestToneBytes(t *testing.T) {
	// The examples from the EEPROM map, through the real record() so byte order is covered too.
	ct := func(s string) tone { v, err := ctcss(s); must(t, err); return v }
	dc := func(s string, inv bool) tone { v, err := dcs(s, inv); must(t, err); return v }
	for _, tc := range []struct {
		name string
		tone tone
		want string // bytes 8-9 of the record, then String()
		str  string
	}{
		{"none", noTone, "ff ff", "-"},
		{"88.5", ct("88.5"), "85 08", "88.5"},
		{"254.1", ct("254.1"), "41 25", "254.1"},
		{"67.0", ct("67.0"), "70 06", "67.0"},
		{"D023N", dc("023", false), "23 80", "D023N"},
		{"D23N", dc("23", false), "23 80", "D023N"},
		{"D754I", dc("754", true), "54 c7", "D754I"},
	} {
		r := channel{rxTone: tc.tone, txTone: noTone}.record()
		if got := hex.EncodeToString(r[8:10]); got != strings.ReplaceAll(tc.want, " ", "") {
			t.Errorf("%s: rx tone bytes %s, want %s", tc.name, got, tc.want)
		}
		if tc.tone.String() != tc.str {
			t.Errorf("%s: String() = %q, want %q", tc.name, tc.tone.String(), tc.str)
		}
	}
	for _, bad := range []string{"", "abc", "10.0", "300", "NaN"} {
		if _, err := ctcss(bad); err == nil {
			t.Errorf("ctcss(%q) accepted", bad)
		}
	}
	for _, bad := range []string{"", "0", "789", "1000", "x"} {
		if _, err := dcs(bad, false); err == nil {
			t.Errorf("dcs(%q) accepted", bad)
		}
	}
}

func TestFrequencyBytes(t *testing.T) {
	for _, tc := range []struct{ mhz, want string }{
		{"146.520000", "00 20 65 14"},
		{"446.00625", "25 06 60 44"},
		{"0.00001", "01 00 00 00"},
		{"999.99999", "99 99 99 99"},
	} {
		u, err := units(tc.mhz)
		must(t, err)
		r := channel{rx: uint32(u), rxTone: noTone, txTone: noTone}.record()
		if !bytes.Equal(r[:4], unhex(tc.want)) {
			t.Errorf("%s MHz: % X, want %s", tc.mhz, r[:4], tc.want)
		}
	}
}

func TestParseGroups(t *testing.T) {
	for in, want := range map[string]byte{
		"":               0,
		"G1 G3":          0b00000101,
		"g2,g5":          0b00010010,
		"G8":             0x80,
		"ARES (G1)":      1,
		"net g4/g6":      0b00101000,
		"G9 G0 g Gx 12":  0,
		"G1 g1":          1,
		"LISTA 1":        1,
		"LISTA 3 TXLOCK": 0b100,
		"list 2, G4":     0b1010,
		"LISTA":          0,
	} {
		if got := parseGroups(in); got != want {
			t.Errorf("parseGroups(%q) = %08b, want %08b", in, got, want)
		}
	}
	if groupList(0b00010101) != "1,3,5" || groupList(0) != "-" {
		t.Errorf("groupList: %q %q", groupList(0b00010101), groupList(0))
	}
}

func TestNameBytes(t *testing.T) {
	for in, want := range map[string]string{
		"Call":         "43414c4c ffffffff",
		"  Call  ":     "43414c4c ffffffff",
		"":             "ffffffffffffffff",
		"EmergencyNet": "454d455247454e43",
		"12345678":     "3132333435363738",
		"café":         "4341463f ffffffff",
	} {
		if got := nameBytes(in); !bytes.Equal(got, unhex(want)) {
			t.Errorf("nameBytes(%q) = % X, want %s", in, got, want)
		}
	}
}

func TestPower(t *testing.T) {
	for in, want := range map[string]byte{"": powerLow, "Low": powerLow, "high": powerHigh, "HIGH": powerHigh,
		"Mid": powerMid, "ULow": powerULow, "ULP": powerULow, "Ultra Low": powerULow, "5.0W": powerHigh, "4W": powerHigh,
		"3.9W": powerMid, "3.0W": powerMid, "2.4W": powerLow, "1.0W": powerLow, "0.5W": powerULow, "0.24W": powerULow} {
		if got, err := power(in); err != nil || got != want {
			t.Errorf("power(%q) = %v, %v; want %v", in, got, err, want)
		}
	}
	if _, err := power("loud"); err == nil {
		t.Error("power(loud) accepted")
	}
}

func TestRowToRecord(t *testing.T) {
	for _, tc := range []struct {
		name string
		row  string // after Location,Name,Frequency,Duplex,Offset,Tone,rToneFreq,cToneFreq,DtcsCode,DtcsPolarity,RxDtcsCode,CrossMode,Mode,TStep,Skip,Power,Comment
		rec  string // 16 bytes
		scan bool
		warn string // substring of a warning, "" for none
	}{
		{"simplex wide high", "1,Call,146.520000,,0.600000,,88.5,88.5,023,NN,023,Tone->Tone,FM,5.00,,5.0W,G1 G3",
			"00206514 00206514 ffff ffff 00 01 10 05", true, ""}, // High is 1, as stock and CHIRP
		{"repeater minus, tone, narrow, skip", "2,Rptr,146.940000,-,0.600000,Tone,100.0,100.0,023,NN,023,Tone->Tone,NFM,5.00,S,Low,\"g2,g5\"",
			"00406914 00406314 ffff 0010 00 01 08 12", false, ""},
		{"repeater plus", "2,Rptr,146.940000,+,0.600000,,88.5,88.5,023,NN,023,Tone->Tone,FM,5.00,,Low,",
			"00406914 00407514 ffff ffff 00 01 00 00", true, ""}, // 147.54 -> 14754000
		{"split", "3,Spl,146.520000,split,147.520000,,88.5,88.5,023,NN,023,Tone->Tone,FM,5.00,,Low,",
			"00206514 00207514 ffff ffff 00 01 00 00", true, ""},
		{"duplex off: no transmit (all-FF TX)", "3,Rx,146.520000,off,0.000000,,88.5,88.5,023,NN,023,Tone->Tone,FM,5.00,,Low,",
			"00206514 ffffffff ffff ffff 00 01 00 00", true, ""},
		{"TXLOCK in the comment: no transmit", "3,Wx,162.550000,,0.000000,,88.5,88.5,023,NN,023,Tone->Tone,FM,5.00,,Low,LISTA 3 TXLOCK",
			"00502516 ffffffff ffff ffff 00 01 00 04", true, ""},
		{"TSQL both ways from cToneFreq", "4,Sq,446.000000,,0.000000,TSQL,67.0,88.5,023,NN,023,Tone->Tone,FM,12.50,,High,",
			"00006044 00006044 8508 8508 00 01 10 00", true, ""},
		{"DTCS polarity RN: TX inverted, RX normal", "5,Dcs,446.000000,,0.000000,DTCS,88.5,88.5,754,RN,754,Tone->Tone,FM,12.50,,1.0W,",
			"00006044 00006044 5487 54c7 00 01 00 00", true, ""},
		{"DTCS polarity NR", "5,Dcs,446.000000,,0.000000,DTCS,88.5,88.5,023,NR,023,Tone->Tone,FM,12.50,,1.0W,",
			"00006044 00006044 23c0 2380 00 01 00 00", true, ""},
		{"cross Tone->DTCS", "6,X,446.000000,,0.000000,Cross,67.0,88.5,023,NR,023,Tone->DTCS,FM,12.50,,Low,",
			"00006044 00006044 23c0 7006 00 01 00 00", true, ""},
		{"cross DTCS->Tone uses RxDtcsCode-free cToneFreq", "6,X,446.000000,,0.000000,Cross,67.0,254.1,023,RN,023,DTCS->Tone,FM,12.50,,Low,",
			"00006044 00006044 4125 23c0 00 01 00 00", true, ""},
		{"cross ->DTCS has no TX tone, own RX code", "6,X,446.000000,,0.000000,Cross,67.0,88.5,023,NN,754,->DTCS,FM,12.50,,Low,",
			"00006044 00006044 5487 ffff 00 01 00 00", true, ""},
		{"AM", "7,Air,121.500000,,0.000000,,88.5,88.5,023,NN,023,Tone->Tone,AM,25.00,,Low,",
			"00001512 00001512 ffff ffff 00 01 04 00", true, ""},
		{"USB", "7,Ssb,144.200000,,0.000000,,88.5,88.5,023,NN,023,Tone->Tone,USB,5.00,,Low,",
			"00004214 00004214 ffff ffff 00 09 00 00", true, ""},
		{"LSB is not supported", "7,Ssb,3.800000,,0.000000,,88.5,88.5,023,NN,023,Tone->Tone,LSB,5.00,,Low,",
			"00003800 00003800 ffff ffff 00 01 00 00", true, "not supported"},
		{"cross without CrossMode value", "6,X,446.000000,,0.000000,Cross,67.0,88.5,023,NN,023,,FM,12.50,,Low,",
			"00006044 00006044 8508 7006 00 01 00 00", true, "assumed Tone->Tone"},
		{"long name cut", "8,EmergencyNet,146.520000,,0.600000,,88.5,88.5,023,NN,023,Tone->Tone,FM,5.00,,Low,",
			"00206514 00206514 ffff ffff 00 01 00 00", true, "cut to 8"},
	} {
		res, err := parseCSV(strings.NewReader(header + tc.row + "\n"))
		if err != nil {
			t.Errorf("%s: %v", tc.name, err)
			continue
		}
		if len(res.chans) != 1 {
			t.Errorf("%s: %d channels", tc.name, len(res.chans))
			continue
		}
		c := res.chans[0]
		if r := c.record(); !bytes.Equal(r[:], unhex(tc.rec)) {
			t.Errorf("%s: record % X, want %s", tc.name, r, tc.rec)
		}
		if c.scan != tc.scan {
			t.Errorf("%s: scan = %v", tc.name, c.scan)
		}
		if got := strings.Join(res.warns, "|"); (tc.warn == "") != (got == "") || !strings.Contains(got, tc.warn) {
			t.Errorf("%s: warnings %q, want one containing %q", tc.name, got, tc.warn)
		}
	}
}

func TestCrossWithoutCrossModeColumn(t *testing.T) {
	csv := "Location,Name,Frequency,Duplex,Offset,Tone,rToneFreq,cToneFreq\n6,X,446.0,,0,Cross,67.0,88.5\n"
	res, err := parseCSV(strings.NewReader(csv))
	must(t, err)
	r := res.chans[0].record()
	if !bytes.Equal(r[8:12], unhex("8508 7006")) || len(res.warns) != 1 {
		t.Errorf("record % X, warns %q", r, res.warns)
	}
}

func TestParseCSVRules(t *testing.T) {
	row := func(loc, freq string) string {
		return loc + ",N," + freq + ",,0,,88.5,88.5,023,NN,023,Tone->Tone,FM,5,,Low,\n"
	}

	// location 0 and 200 skip with a warning; blank Excel rows and a BOM are tolerated
	res, err := parseCSV(strings.NewReader("\ufeff" + header + row("0", "146.52") + row("5", "146.52") + ",,,,,,,\n" + row("200", "146.52") + row("-1", "146.52")))
	must(t, err)
	if len(res.chans) != 1 || res.chans[0].loc != 5 || res.skipped != 3 || len(res.warns) != 3 {
		t.Errorf("chans %d, skipped %d, warns %q", len(res.chans), res.skipped, res.warns)
	}

	// channels come back sorted by location, columns can be in any order
	res, err = parseCSV(strings.NewReader("frequency,LOCATION,name\n146.52,9,B\n146.52,2,A\n"))
	must(t, err)
	if len(res.chans) != 2 || res.chans[0].loc != 2 || res.chans[0].name != "A" || res.chans[1].loc != 9 {
		t.Errorf("%+v", res.chans)
	}

	for name, csv := range map[string]string{
		"no Location column":  "Name,Frequency\nA,146.52\n",
		"no Frequency column": "Location,Name\n1,A\n",
		"duplicate location":  header + row("3", "146.52") + row("3", "146.94"),
		"bad location":        header + row("three", "146.52"),
		"bad frequency":       header + row("3", "abc"),
		"zero frequency":      header + row("3", "0"),
		"too high":            header + row("3", "1000"),
		"minus below zero":    header + "3,N,0.1,-,0.6,,88.5,88.5,023,NN,023,,FM,5,,Low,\n",
		"plus without offset": header + "3,N,146.52,+,,,88.5,88.5,023,NN,023,,FM,5,,Low,\n",
		"unknown duplex":      header + "3,N,146.52,?,0.6,,88.5,88.5,023,NN,023,,FM,5,,Low,\n",
		"unknown tone mode":   header + "3,N,146.52,,0,TSQL-R,88.5,88.5,023,NN,023,,FM,5,,Low,\n",
		"bad CTCSS":           header + "3,N,146.52,,0,Tone,5.0,88.5,023,NN,023,,FM,5,,Low,\n",
		"bad DCS digit":       header + "3,N,146.52,,0,DTCS,88.5,88.5,089,NN,089,,FM,5,,Low,\n",
		"bad polarity":        header + "3,N,146.52,,0,DTCS,88.5,88.5,023,XX,023,,FM,5,,Low,\n",
		"bad CrossMode":       header + "3,N,146.52,,0,Cross,88.5,88.5,023,NN,023,Tone,FM,5,,Low,\n",
	} {
		if _, err := parseCSV(strings.NewReader(csv)); err == nil {
			t.Errorf("%s: accepted", name)
		}
	}
	// a bad tone in a row whose Tone is empty is not an error (CHIRP fills those columns with junk)
	if _, err := parseCSV(strings.NewReader(header + "3,N,146.52,,0,,5.0,xx,999,XX,,Bogus,FM,5,,Low,\n")); err != nil {
		t.Errorf("unused tone columns rejected: %v", err)
	}
}

// ---- protected ranges ----

func TestWritableNeverTouchesCalibration(t *testing.T) {
	overlaps := func(addr, lo, hi int) bool { return addr < hi && addr+32 > lo }
	okCount := 0
	for addr := -64; addr <= 0x2100; addr += 32 {
		err := writable(addr)
		inside := func(lo, hi int) bool { return addr >= lo && addr+32 <= hi }
		want := inside(0x0000, 0x0C80) || inside(0x0D40, 0x13C0) || inside(0x1900, 0x1940)
		if (err == nil) != want {
			t.Errorf("writable(%04X) = %v, want ok=%v", addr, err, want)
		}
		if err == nil {
			okCount++
			if overlaps(addr, 0x1F50, 0x1F80) || overlaps(addr, 0x1FF0, 0x2000) {
				t.Errorf("block %04X overlaps calibration but is writable", addr)
			}
		}
	}
	if okCount != 100+(0x13C0-0x0D40)/32+2 { // 0x0000-0x0C7F, 0x0D40-0x13BF, 0x1900-0x193F
		t.Errorf("%d writable blocks", okCount)
	}
	for _, addr := range []int{0x1F40, 0x1F50, 0x1F60, 0x1F70, 0x1FE0, 0x1FF0, 0x1900 + 1, 0x2000, -32, 0x0C80, 0x13C0, 0x1940} {
		if writable(addr) == nil {
			t.Errorf("writable(%04X) accepted", addr)
		}
	}
}

func TestPatchBits(t *testing.T) {
	blk := bytes.Repeat([]byte{0xAA}, 32)
	out, err := patch(0x1900, blk, []edit{{0x1900, 0x07, 0x05}, {0x1901, 0xFF, 0x12}, {0x191F, 0x80, 0x00}})
	must(t, err)
	if out[0] != 0xAD || out[1] != 0x12 || out[31] != 0x2A || out[2] != 0xAA || !bytes.Equal(blk, bytes.Repeat([]byte{0xAA}, 32)) {
		t.Errorf("patched % X (input must stay as read)", out)
	}
	if _, err := patch(0x1900, blk, []edit{{0x1920, 0xFF, 1}}); err == nil {
		t.Error("edit outside the block accepted")
	}
	if _, err := patch(0x1900, blk[:31], nil); err == nil {
		t.Error("short block accepted")
	}
}

// importBlocks checks each block's area before it reads or writes it: a refused one never reaches the port.
func TestImportSendsNothingToProtectedAddresses(t *testing.T) {
	for _, addr := range []int{0x1F40, 0x1F50, 0x1F60, 0x1FC0, 0x1FE0, 0x1FF0, 0x2000, 0x0C80} {
		f, writes := cloneRadio(t, seededEEPROM())
		if _, err := importBlocks(f, map[int][]edit{addr: {{addr, 0xFF, 0}}}, writable, quiet); err == nil {
			t.Errorf("block %04X accepted", addr)
		}
		for _, w := range f.sent {
			if w[0] == 'R' || w[0] == 'W' {
				t.Errorf("block %04X: sent % X", addr, w)
			}
		}
		if len(*writes) != 0 {
			t.Errorf("block %04X: wrote %04X", addr, *writes)
		}
	}
}

func TestSendBlockFrame(t *testing.T) {
	f := &fake{reply: func([]byte) []byte { return []byte{6} }}
	must(t, sendBlock(f, 0x0020, bytes.Repeat([]byte{1}, 32)))
	want := append(append([]byte{'W', 0x00, 0x20, 0x20}, bytes.Repeat([]byte{1}, 32)...), 32)
	if len(f.sent) != 1 || !bytes.Equal(f.sent[0], want) {
		t.Errorf("sent % X", f.sent)
	}
	f.reply = func([]byte) []byte { return []byte{0x15} }
	if sendBlock(f, 0x0020, make([]byte, 32)) == nil {
		t.Error("nack accepted")
	}
}

func TestPlanStaysInsideWritableBlocks(t *testing.T) {
	var all []channel
	for n := 1; n <= maxChan; n++ {
		all = append(all, channel{loc: n, name: "ABCDEFGH", rx: 14652000, tx: 14652000, rxTone: noTone, txTone: noTone, scan: n%2 == 0, groups: 0xFF})
	}
	for _, wipe := range []bool{false, true} {
		for _, chans := range [][]channel{all, all[:3], all[198:]} {
			es := plan(chans, wipe)
			for addr, list := range es {
				if err := writable(addr); err != nil {
					t.Errorf("plan(wipe=%v, %d channels) wants block %04X: %v", wipe, len(chans), addr, err)
				}
				for _, e := range list {
					if e.addr&^31 != addr {
						t.Errorf("edit %04X filed under block %04X", e.addr, addr)
					}
				}
			}
		}
	}
	// the last channel lands in the last record, name and bitmap bytes
	es := plan(all[198:], false)
	if _, ok := es[0x0C60]; !ok {
		t.Error("channel 199 record not in block 0C60")
	}
	if _, ok := es[0x1360]; !ok {
		t.Error("channel 199 name not in block 1360")
	}
}

// ---- full import through a fake radio ----

const threeRows = header +
	"1,Call,146.520000,,0.600000,,88.5,88.5,023,NN,023,Tone->Tone,FM,5.00,,5.0W,G1 G3,,,,\n" +
	"2,Rptr,146.940000,-,0.600000,Tone,100.0,100.0,023,NN,023,Tone->Tone,NFM,5.00,S,Low,\"g2,g5\",,,,\n" +
	"3,Dcs,446.000000,,0.000000,DTCS,88.5,88.5,754,RN,754,Tone->Tone,FM,12.50,,1.0W,,,,,\n"

// cloneRadio is a fake TD-H3 in clone mode that keeps its EEPROM in mem and logs written block addresses.
// It fails the test if anything touches the calibration areas, with the numbers written out here on purpose.
func cloneRadio(t *testing.T, mem []byte) (f *fake, writes *[]int) {
	writes = new([]int)
	f = &fake{reply: func(w []byte) []byte {
		switch {
		case bytes.Equal(w, magic):
			return []byte{6}
		case bytes.Equal(w, []byte{2}):
			return []byte("P31183\xff\xff")
		case bytes.Equal(w, []byte{6}), bytes.Equal(w, []byte("E")):
			return []byte{6}
		case len(w) == 4 && w[0] == 'R':
			a := int(w[1])<<8 | int(w[2])
			d := mem[a : a+32]
			return append(append([]byte{'W', w[1], w[2], 0x20}, d...), sum8(d))
		case len(w) == 37 && w[0] == 'W':
			a := int(w[1])<<8 | int(w[2])
			if w[3] != 0x20 || a%32 != 0 || sum8(w[4:36]) != w[36] {
				t.Fatalf("malformed write % X", w)
			}
			if a == 0x1FE0 { // settings, then 0x1FF0-0x1FFF: as td-h3-fw, only the first half is kept;
				if !bytes.Equal(w[20:36], mem[0x1FF0:0x2000]) { // the calibration half must be the radio's own
					t.Fatalf("write to 1FE0 changes the calibration at 1FF0")
				}
				copy(mem[a:a+16], w[4:20])
				*writes = append(*writes, a)
				return []byte{6}
			}
			if a+32 > 0x1F50 && a < 0x1F80 || a+32 > 0x1FF0 && a < 0x2000 || a >= 0x2000 {
				t.Fatalf("write to protected block %04X", a)
			}
			copy(mem[a:], w[4:36])
			*writes = append(*writes, a)
			return []byte{6}
		}
		t.Fatalf("unexpected write % X", w)
		return nil
	}}
	return
}

func seededEEPROM() []byte {
	mem := make([]byte, 0x2000)
	for i := range mem {
		mem[i] = byte(i*7 + 3)
	}
	mem[0x1FF0] = 0x85
	return mem
}

func quiet(string, ...any) {}

func TestImportThreeRowsWritesExactBlocks(t *testing.T) {
	res, err := parseCSV(strings.NewReader(threeRows))
	must(t, err)
	if len(res.chans) != 3 || len(res.warns) != 0 {
		t.Fatalf("%d channels, warnings %q", len(res.chans), res.warns)
	}
	seed := seededEEPROM()
	mem := bytes.Clone(seed)
	f, writes := cloneRadio(t, mem)

	n, err := importBlocks(f, plan(res.chans, false), writable, quiet)
	must(t, err)
	// records 1 (0x10) in block 0000, 2 and 3 (0x20, 0x30) in block 0020; names, used and scan bitmaps
	if want := []int{0x0000, 0x0020, 0x0D40, 0x1900, 0x1920}; !slices.Equal(*writes, want) || n != 5 {
		t.Fatalf("wrote blocks %04X (n=%d), want %04X", *writes, n, want)
	}
	if last := f.sent[len(f.sent)-1]; string(last) != "E" {
		t.Errorf("session ended with % X, want E", last)
	}

	// the expected EEPROM, written out by hand from the EEPROM map
	want := bytes.Clone(seed)
	copy(want[0x10:], unhex("00206514 00206514 ffff ffff 00 01 10 05"))
	copy(want[0x20:], unhex("00406914 00406314 ffff 0010 00 01 08 12"))
	copy(want[0x30:], unhex("00006044 00006044 5487 54c7 00 01 00 00"))
	copy(want[0x0D40:], unhex("43414c4c ffffffff")) // CALL
	copy(want[0x0D48:], unhex("52505452 ffffffff")) // RPTR
	copy(want[0x0D50:], unhex("444353 ffffffffff")) // DCS
	want[0x1900] = seed[0x1900]&^0x07 | 0x07        // channels 1-3 exist
	want[0x1920] = seed[0x1920]&^0x07 | 0x05        // 1 and 3 scan, 2 is skipped
	if !bytes.Equal(mem, want) {
		for i := range mem {
			if mem[i] != want[i] {
				t.Errorf("EEPROM[%04X] = %02X, want %02X", i, mem[i], want[i])
			}
		}
	}
	if !bytes.Equal(mem[0x1F50:0x1F80], seed[0x1F50:0x1F80]) || !bytes.Equal(mem[0x1FF0:], seed[0x1FF0:]) {
		t.Error("calibration changed")
	}

	// a second run finds nothing to change and writes nothing
	*writes = nil
	n, err = importBlocks(f, plan(res.chans, false), writable, quiet)
	must(t, err)
	if len(*writes) != 0 || n != 0 {
		t.Errorf("second import wrote %04X", *writes)
	}
}

func TestImportClearOnlyUsedBits(t *testing.T) {
	res, err := parseCSV(strings.NewReader(threeRows))
	must(t, err)
	seed := seededEEPROM()
	for i := 0x1900; i < 0x1920; i++ {
		seed[i] = 0xFF
	}
	mem := bytes.Clone(seed)
	f, writes := cloneRadio(t, mem)
	_, err = importBlocks(f, plan(res.chans, true), writable, quiet)
	must(t, err)

	want := bytes.Repeat([]byte{0}, 32)
	want[0], want[24] = 0x07, 0x80 // channels 1-3 stay, 4-199 go; channel 200's bit and the bytes after 0x1918 are not ours
	for i := 25; i < 32; i++ {
		want[i] = 0xFF
	}
	if !bytes.Equal(mem[0x1900:0x1920], want) {
		t.Errorf("used bitmap % X, want % X", mem[0x1900:0x1920], want)
	}
	if !slices.Equal(*writes, []int{0x0000, 0x0020, 0x0D40, 0x1900, 0x1920}) {
		t.Errorf("wrote blocks %04X", *writes)
	}
	if !bytes.Equal(mem[0x1F50:0x1F80], seed[0x1F50:0x1F80]) || !bytes.Equal(mem[0x1FF0:], seed[0x1FF0:]) {
		t.Error("calibration changed")
	}
	// without --clear the same bytes stay as they were
	mem2 := bytes.Clone(seed)
	f2, _ := cloneRadio(t, mem2)
	_, err = importBlocks(f2, plan(res.chans, false), writable, quiet)
	must(t, err)
	if mem2[0x1903] != 0xFF {
		t.Errorf("channel 25 used bit changed without --clear: %02X", mem2[0x1903])
	}
}

func TestWipeKeepsEveryChannelInTheCSV(t *testing.T) {
	// channels at byte edges (1, 8, 9) and the last one (199); everything else off, channel 200's bit left alone
	var chans []channel
	for _, n := range []int{1, 8, 9, 199} {
		chans = append(chans, channel{loc: n, rxTone: noTone, txTone: noTone})
	}
	out, err := patch(0x1900, bytes.Repeat([]byte{0xFF}, 32), plan(chans, true)[0x1900])
	must(t, err)
	want := make([]byte, 32)
	want[0], want[1], want[24] = 0x81, 0x01, 0xC0
	for i := 25; i < 32; i++ {
		want[i] = 0xFF
	}
	if !bytes.Equal(out, want) {
		t.Errorf("used bitmap % X, want % X", out, want)
	}
}

func TestImportStopsOnNack(t *testing.T) {
	res, err := parseCSV(strings.NewReader(threeRows))
	must(t, err)
	mem := seededEEPROM()
	f, _ := cloneRadio(t, mem)
	inner := f.reply
	f.reply = func(w []byte) []byte {
		if len(w) == 37 && w[0] == 'W' {
			return []byte{0x15}
		}
		return inner(w)
	}
	n, err := importBlocks(f, plan(res.chans, false), writable, quiet)
	if err == nil || n != 0 || !strings.Contains(err.Error(), "0000") {
		t.Fatalf("n=%d err=%v", n, err)
	}
	if string(f.sent[len(f.sent)-1]) != "E" {
		t.Error("session was not closed with E")
	}
}

func must(t *testing.T, err error) {
	t.Helper()
	if err != nil {
		t.Fatal(err)
	}
}

// clone.c acks a block whose EEPROM write failed: import reads it back and says so.
func TestImportCatchesAWriteTheRadioDidNotKeep(t *testing.T) {
	res, err := parseCSV(strings.NewReader(threeRows))
	must(t, err)
	mem := seededEEPROM()
	f, _ := cloneRadio(t, mem)
	inner := f.reply
	f.reply = func(w []byte) []byte {
		if len(w) == 37 && w[0] == 'W' {
			return []byte{6} // acked, never stored
		}
		return inner(w)
	}
	if n, err := importBlocks(f, plan(res.chans, false), writable, quiet); err == nil || n != 0 || !strings.Contains(err.Error(), "0000") {
		t.Fatalf("n=%d err=%v", n, err)
	}
}

// All eight groups from a CSV: byte 15 FF with td-h3-fw's mark (byte 13 bits 1:0 01), so it reads back as eight.
func TestEightGroupsMarked(t *testing.T) {
	res, err := parseCSV(strings.NewReader(header + "1,All,146.520000,,0.000000,,88.5,88.5,023,NN,023,Tone->Tone,FM,5.00,,5.0W,G1 G2 G3 G4 G5 G6 G7 G8,,,,\n"))
	must(t, err)
	if r := res.chans[0].record(); r[15] != 0xFF || r[13]&3 != 1 {
		t.Fatalf("byte 13 %02X, 15 %02X", r[13], r[15])
	}
}
