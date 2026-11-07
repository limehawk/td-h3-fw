// tdh3 channels import: load a CHIRP CSV export into the radio's memory channels.
//
//	tdh3 channels import -p /dev/ttyUSB1 list.csv          write channels 1-199 from the CSV
//	tdh3 channels import --dry-run list.csv                parse and print a table, no port needed
//	tdh3 channels import --clear -p /dev/ttyUSB1 list.csv  also clear every channel not in the CSV
//
// Columns are found by header name (Location, Name, Frequency, Duplex, Offset, Tone, rToneFreq,
// cToneFreq, DtcsCode, DtcsPolarity, RxDtcsCode, CrossMode, Mode, Skip, Power, Comment; the rest
// are ignored). G1..G8 in Comment set the scan groups; Skip "S" keeps a channel out of the scan list.
// Any bad row aborts before the radio is touched. Locations outside 1-199 are skipped with a warning.
//
// Only three areas of the EEPROM are ever written, one 32-byte block at a time (read, patch,
// write back only if changed): channel records 0x0000-0x0C7F, names 0x0D40-0x13BF and the
// used/scan bitmaps 0x1900-0x193F. The factory TX calibration (0x1F50-0x1F7F, 0x1FF0-0x1FFF) is
// refused in importBlocks and by the tests (tdh3 reset, reset.go, adds the settings' first block
// and nothing else). Layout: TIDRADIO's map, which is CHIRP chirp/drivers/tdh8.py minus its
// 8-byte ident header.
package main

import (
	"bytes"
	"cmp"
	"encoding/binary"
	"encoding/csv"
	"fmt"
	"io"
	"maps"
	"math"
	"os"
	"slices"
	"strconv"
	"strings"
	"time"
	"unicode"

	"charm.land/lipgloss/v2"
	"charm.land/lipgloss/v2/table"
)

const (
	maxChan  = 199
	nameBase = 0x0D40 // 8 bytes per channel, ASCII padded with 0xFF
	usedBase = 0x1900 // bitmap: channel exists
	scanBase = 0x1920 // bitmap: channel is in the scan list
)

// Half-open, 32-byte aligned. A block is written only if it sits inside writeOK and touches neverWrite nowhere.
var (
	writeOK    = [][2]int{{0x0000, 0x0C80}, {0x0D40, 0x13C0}, {0x1900, 0x1940}}
	neverWrite = [][2]int{{0x1F50, 0x1F80}, {0x1FF0, 0x2000}}
)

func writable(addr int) error {
	if addr < 0 || addr%32 != 0 {
		return fmt.Errorf("block %04X is not 32-byte aligned", addr)
	}
	for _, r := range neverWrite {
		if addr < r[1] && addr+32 > r[0] {
			return fmt.Errorf("block %04X overlaps %04X-%04X (calibration): refused", addr, r[0], r[1]-1)
		}
	}
	for _, r := range writeOK {
		if addr >= r[0] && addr+32 <= r[1] {
			return nil
		}
	}
	return fmt.Errorf("block %04X is outside the channel, name and bitmap areas: refused", addr)
}

// ---- encoding: pure functions ----

// bcd packs the decimal digits of n into nibbles (14652000 -> 0x14652000).
func bcd(n uint32) (b uint32) {
	for i := 0; n > 0; i, n = i+4, n/10 {
		b |= n % 10 << i
	}
	return
}

// tone is the 16-bit EEPROM word: FFFF none, BCD tenths of Hz for CTCSS, 0x8000|BCD code (0x4000 more if inverted) for DCS.
type tone uint16

const noTone tone = 0xFFFF

func ctcss(hz string) (tone, error) {
	f, err := strconv.ParseFloat(hz, 64)
	if err != nil || !(f >= 60 && f <= 260) {
		return 0, fmt.Errorf("CTCSS %q is not a tone between 60.0 and 260.0 Hz", hz)
	}
	return tone(bcd(uint32(math.Round(f * 10)))), nil
}

// dcs takes the code as written ("023", "23", "754": octal digits).
func dcs(code string, inverted bool) (tone, error) {
	n, err := strconv.Atoi(code)
	if err != nil || n < 1 || n > 777 {
		return 0, fmt.Errorf("DCS code %q is not 1-777", code)
	}
	for d := n; d > 0; d /= 10 {
		if d%10 > 7 {
			return 0, fmt.Errorf("DCS code %q has a digit above 7", code)
		}
	}
	t := tone(0x8000 | bcd(uint32(n)))
	if inverted {
		t |= 0x4000
	}
	return t, nil
}

func (t tone) String() string { // uint16(t): %x on a tone would call String again
	switch {
	case t == noTone:
		return "-"
	case t&0x8000 != 0:
		pol := 'N'
		if t&0x4000 != 0 {
			pol = 'I'
		}
		return fmt.Sprintf("D%03x%c", uint16(t)&0xFFF, pol)
	}
	return fmt.Sprintf("%x.%x", uint16(t)>>4, uint16(t)&0xF)
}

// cleanName: upper case, printable ASCII only (anything else becomes '?'), at most 8 characters.
func cleanName(s string) string {
	var out []rune
	for _, r := range strings.ToUpper(strings.TrimSpace(s)) {
		if r < 0x20 || r > 0x7E {
			r = '?'
		}
		out = append(out, r)
	}
	return string(out[:min(len(out), 8)])
}

func nameBytes(s string) []byte {
	b := bytes.Repeat([]byte{0xFF}, 8)
	copy(b, cleanName(s))
	return b
}

// parseGroups reads G1..G8 tokens ("G1 G3", "g2,g5") from a comment into a mask, bit g-1 = group g.
// F4HWN-style "LISTA 2" / "LIST 2" (the UV-K5 scan lists) count as group 2.
func parseGroups(comment string) (m byte) {
	words := strings.FieldsFunc(comment, func(r rune) bool { return !unicode.IsLetter(r) && !unicode.IsDigit(r) })
	for i, w := range words {
		if len(w) == 2 && (w[0] == 'g' || w[0] == 'G') && w[1] >= '1' && w[1] <= '8' {
			m |= 1 << (w[1] - '1')
		}
		if u := strings.ToUpper(w); (u == "LISTA" || u == "LIST") && i+1 < len(words) {
			if n := words[i+1]; len(n) == 1 && n[0] >= '1' && n[0] <= '8' {
				m |= 1 << (n[0] - '1')
			}
		}
	}
	return
}

func groupList(m byte) string {
	var g []string
	for i := range 8 {
		if m>>i&1 == 1 {
			g = append(g, strconv.Itoa(i+1))
		}
	}
	if g == nil {
		return "-"
	}
	return strings.Join(g, ",")
}

// power is the Power column as byte 14's bits 5:4: Low (blank too), Mid, High, ULow (or "Ultra Low"),
// or a wattage, which picks the level nearest under it as measured (Transmitting.md): 0.5 W and less
// ultra-low, under 2.5 W low, under 4 W mid, 4 W and more high. CHIRP's 1.0W and 5.0W are Low and High.
func power(s string) (byte, error) {
	switch p := strings.ReplaceAll(strings.ToLower(s), " ", ""); p {
	case "", "low":
		return powerLow, nil
	case "mid":
		return powerMid, nil
	case "high":
		return powerHigh, nil
	case "ulow", "ultralow":
		return powerULow, nil
	default:
		w, err := strconv.ParseFloat(strings.TrimSuffix(p, "w"), 64)
		switch {
		case err != nil:
			return powerLow, fmt.Errorf("power %q is not ULow, Low, Mid, High or a wattage like 5.0W", s)
		case w <= 0.5:
			return powerULow, nil
		case w < 2.5:
			return powerLow, nil
		case w < 4:
			return powerMid, nil
		}
		return powerHigh, nil
	}
}

// units turns MHz text into 10 Hz units (146.52 -> 14652000). Offsets may be zero or negative.
func units(mhz string) (int64, error) {
	f, err := strconv.ParseFloat(mhz, 64)
	if err != nil || !(math.Abs(f) < 1000) {
		return 0, fmt.Errorf("%q is not a frequency in MHz", mhz)
	}
	return int64(math.Round(f * 1e5)), nil
}

func mhz(u uint32) string {
	if u == 0 {
		return "no TX"
	}
	return fmt.Sprintf("%d.%05d", u/100000, u%100000)
}

type channel struct {
	loc            int
	name           string
	rx, tx         uint32 // 10 Hz units
	rxTone, txTone tone
	narrow         bool
	power          byte // byte 14 bits 5:4 (powerLow ...)
	mode           byte // td-h3-fw's: 0 FM (AM on the air band), 1 AM, 2 USB
	scan           bool
	groups         byte
}

// record is the 16-byte EEPROM entry at 0x10*loc.
func (c channel) record() (r [16]byte) {
	binary.LittleEndian.PutUint32(r[0:], bcd(c.rx))
	if c.tx == 0 { // no transmit: td-h3-fw and CHIRP mark it with an all-FF TX frequency
		binary.LittleEndian.PutUint32(r[4:], 0xFFFFFFFF)
	} else {
		binary.LittleEndian.PutUint32(r[4:], bcd(c.tx))
	}
	binary.LittleEndian.PutUint16(r[8:], uint16(c.rxTone))
	binary.LittleEndian.PutUint16(r[10:], uint16(c.txTone))
	if c.narrow {
		r[14] |= 1 << 3
	}
	r[14] |= c.power << 4
	r[13] = 1                // bits 1:0 01, td-h3-fw's mark: byte 15 is its scan groups, FF all eight (a blank channel's FF is none)
	r[13] |= c.mode & 2 << 2 // td-h3-fw's mode: bit 1 in byte 13 bit 3, bit 0 in byte 14 bit 2 (with the mark only)
	r[14] |= c.mode & 1 << 2
	r[15] = c.groups
	return
}

// Byte 14 bits 5:4, the TX power, as stock and CHIRP (tdh8 TDH3) store it; 2 is td-h3-fw's mid, 3 its
// ultra-low. Copies keep all four as they are.
const (
	powerLow byte = iota
	powerHigh
	powerMid
	powerULow
)

// tones reads the Tone column and its helpers. Byte order in the EEPROM is RX then TX.
func tones(get func(string) string) (rx, tx tone, warn string, err error) {
	pol := cmp.Or(strings.ToUpper(get("DtcsPolarity")), "NN") // first letter TX, second RX; R = inverted
	half := func(what, freq, code string, i int) (tone, error) {
		switch strings.ToLower(what) {
		case "":
			return noTone, nil
		case "tone":
			return ctcss(freq)
		case "dtcs":
			if len(pol) != 2 || strings.Trim(pol, "NR") != "" {
				return 0, fmt.Errorf("DtcsPolarity %q is not NN, NR, RN or RR", pol)
			}
			return dcs(code, pol[i] == 'R')
		}
		return 0, fmt.Errorf("unknown tone type %q", what)
	}
	r, c, code := get("rToneFreq"), get("cToneFreq"), get("DtcsCode")
	switch mode := get("Tone"); strings.ToLower(mode) {
	case "":
		return noTone, noTone, "", nil
	case "tone": // TX tone only
		tx, err = half("tone", r, "", 0)
		return noTone, tx, "", err
	case "tsql": // same tone both ways, from cToneFreq
		rx, err = half("tone", c, "", 1)
		return rx, rx, "", err
	case "dtcs":
		if tx, err = half("dtcs", "", code, 0); err == nil {
			rx, err = half("dtcs", "", code, 1)
		}
		return rx, tx, "", err
	case "cross": // CrossMode is TX->RX, e.g. Tone->DTCS
		cm := get("CrossMode")
		if cm == "" {
			warn, cm = "Cross tone without a CrossMode value: assumed Tone->Tone", "Tone->Tone"
		}
		t, x, ok := strings.Cut(cm, "->")
		if !ok {
			return 0, 0, "", fmt.Errorf("CrossMode %q is not like Tone->DTCS", cm)
		}
		if tx, err = half(strings.TrimSpace(t), r, code, 0); err == nil {
			rx, err = half(strings.TrimSpace(x), c, cmp.Or(get("RxDtcsCode"), code), 1)
		}
		return rx, tx, warn, err
	default:
		return 0, 0, "", fmt.Errorf("Tone %q is not one of '', Tone, TSQL, DTCS, Cross", mode)
	}
}

// parseRow builds one channel from a CSV row; get returns the trimmed cell for a header name.
func parseRow(loc int, get func(string) string) (c channel, warns []string, err error) {
	warn := func(f string, a ...any) { warns = append(warns, fmt.Sprintf(f, a...)) }
	c.loc = loc
	if n := strings.TrimSpace(get("Name")); len([]rune(n)) > 8 {
		warn("name %q cut to 8 characters", n)
	}
	c.name = cleanName(get("Name"))

	rx, err := units(get("Frequency"))
	if err != nil {
		return c, nil, fmt.Errorf("Frequency: %w", err)
	}
	tx := rx
	switch d := get("Duplex"); d {
	case "":
	case "+", "-":
		off, err := units(get("Offset"))
		if err != nil {
			return c, nil, fmt.Errorf("Offset: %w", err)
		}
		if d == "-" {
			off = -off
		}
		tx = rx + off
	case "split": // Offset holds the TX frequency
		if tx, err = units(get("Offset")); err != nil {
			return c, nil, fmt.Errorf("Offset: %w", err)
		}
	case "off": // no transmit on this channel
	default:
		return c, nil, fmt.Errorf("Duplex %q is not one of '', +, -, split, off", d)
	}
	for _, f := range []int64{rx, tx} {
		if f < 1 || f > 99999999 {
			return c, nil, fmt.Errorf("frequency %s MHz is outside 0.00001-999.99999", mhz(uint32(max(f, 0))))
		}
	}
	c.rx, c.tx = uint32(rx), uint32(tx)
	if get("Duplex") == "off" || strings.Contains(strings.ToUpper(get("Comment")), "TXLOCK") {
		c.tx = 0
	}

	var tw string
	if c.rxTone, c.txTone, tw, err = tones(get); err != nil {
		return c, nil, err
	}
	if tw != "" {
		warn("%s", tw)
	}

	switch m := strings.ToUpper(get("Mode")); m {
	case "", "FM":
	case "NFM":
		c.narrow = true
	case "AM":
		c.mode = 1
	case "USB":
		c.mode = 2 // receive only on the radio
	default:
		warn("mode %s is not supported: stored as FM", m)
	}
	pw, perr := power(get("Power"))
	if perr != nil {
		warn("%v: stored as low", perr)
	}
	c.power = pw
	c.scan = !strings.EqualFold(get("Skip"), "S")
	c.groups = parseGroups(get("Comment"))
	return c, warns, nil
}

// ---- CSV ----

type csvResult struct {
	chans   []channel // sorted by location
	skipped int
	warns   []string
}

func parseCSV(r io.Reader) (res csvResult, err error) {
	cr := csv.NewReader(r)
	cr.FieldsPerRecord = -1
	hdr, err := cr.Read()
	if err != nil {
		return res, fmt.Errorf("read the header row: %w", err)
	}
	col := map[string]int{}
	for i, h := range hdr {
		col[strings.ToLower(strings.TrimSpace(strings.TrimPrefix(h, "\ufeff")))] = i
	}
	for _, need := range []string{"location", "frequency"} {
		if _, ok := col[need]; !ok {
			return res, fmt.Errorf("no %q column in the header row (is this a CHIRP CSV?)", need)
		}
	}
	seen := map[int]int{} // location -> line
	for {
		rec, err := cr.Read()
		if err == io.EOF {
			break
		}
		if err != nil {
			return res, err
		}
		if strings.TrimSpace(strings.Join(rec, "")) == "" {
			continue // Excel leaves comma-only rows behind
		}
		line, _ := cr.FieldPos(0)
		get := func(name string) string {
			if i, ok := col[strings.ToLower(name)]; ok && i < len(rec) {
				return strings.TrimSpace(rec[i])
			}
			return ""
		}
		loc, err := strconv.Atoi(get("Location"))
		if err != nil {
			return res, fmt.Errorf("line %d: Location %q is not a number", line, get("Location"))
		}
		if loc < 1 || loc > maxChan {
			res.warns = append(res.warns, fmt.Sprintf("line %d: location %d is outside 1-%d: skipped", line, loc, maxChan))
			res.skipped++
			continue
		}
		if first, dup := seen[loc]; dup {
			return res, fmt.Errorf("line %d: location %d is already used on line %d", line, loc, first)
		}
		seen[loc] = line
		c, ws, err := parseRow(loc, get)
		if err != nil {
			return res, fmt.Errorf("line %d (location %d): %w", line, loc, err)
		}
		for _, w := range ws {
			res.warns = append(res.warns, fmt.Sprintf("line %d (location %d): %s", line, loc, w))
		}
		res.chans = append(res.chans, c)
	}
	slices.SortFunc(res.chans, func(a, b channel) int { return cmp.Compare(a.loc, b.loc) })
	return res, nil
}

// ---- patching ----

// edit sets the bits of mask at addr to val.
type edit struct {
	addr      int
	mask, val byte
}

// plan lists the byte edits for the channels, grouped by the 32-byte block that holds them.
// With wipe it also clears the used bit of every channel 1-199 that is not in chans.
func plan(chans []channel, wipe bool) map[int][]edit {
	es := map[int][]edit{}
	put := func(addr int, mask, val byte) { es[addr&^31] = append(es[addr&^31], edit{addr, mask, val}) }
	bit := func(base, n int, on bool) {
		m := byte(1) << ((n - 1) & 7)
		put(base+(n-1)/8, m, m*b2u(on))
	}
	have := map[int]bool{}
	for _, c := range chans {
		have[c.loc] = true
		for i, v := range c.record() {
			put(0x10*c.loc+i, 0xFF, v)
		}
		for i, v := range nameBytes(c.name) {
			put(nameBase+8*(c.loc-1)+i, 0xFF, v)
		}
		bit(usedBase, c.loc, true)
		bit(scanBase, c.loc, c.scan)
	}
	if wipe {
		for n := 1; n <= maxChan; n++ {
			if !have[n] {
				bit(usedBase, n, false)
			}
		}
	}
	return es
}

func b2u(b bool) byte {
	if b {
		return 1
	}
	return 0
}

// patch returns blk (the 32 bytes read from the radio at addr) with the edits applied. The callers
// check the area: importBlocks with its ok, channels copy byte by byte.
func patch(addr int, blk []byte, es []edit) ([]byte, error) {
	if len(blk) != 32 {
		return nil, fmt.Errorf("block %04X: %d bytes, want 32", addr, len(blk))
	}
	out := bytes.Clone(blk)
	for _, e := range es {
		if e.addr < addr || e.addr >= addr+32 {
			return nil, fmt.Errorf("edit at %04X is outside block %04X", e.addr, addr)
		}
		i := e.addr - addr
		out[i] = out[i]&^e.mask | e.val&e.mask
	}
	return out, nil
}

// ---- radio ----

// sendBlock writes one block; the caller has checked its area.
func sendBlock(p port, addr int, data []byte) error {
	if len(data) != 32 {
		return fmt.Errorf("block %04X: %d bytes, want 32", addr, len(data))
	}
	frame := append([]byte{'W', byte(addr >> 8), byte(addr), 0x20}, data...)
	if _, err := p.Write(append(frame, sum8(data))); err != nil {
		return fmt.Errorf("block %04X: %w", addr, err)
	}
	if a := readN(p, 1, 2*time.Second); !bytes.Equal(a, []byte{6}) {
		return fmt.Errorf("block %04X: radio did not acknowledge the write (% x)", addr, a)
	}
	return nil
}

// importBlocks runs the clone session: for each planned block read, patch, and write back if it changed.
// ok is the area check (writable; resetWritable for tdh3 reset): a block it refuses is never sent.
func importBlocks(p port, es map[int][]edit, ok func(int) error, say func(string, ...any)) (written int, err error) {
	ident, err := handshake(p)
	if err != nil {
		return 0, err
	}
	say("radio answered", "ident", fmt.Sprintf("%q", ident))
	defer func() {
		p.Write([]byte("E"))
		if a := readN(p, 1, time.Second); err == nil && !bytes.Equal(a, []byte{6}) {
			err = fmt.Errorf("radio did not acknowledge the end of the clone (% x)", a)
		}
	}()
	for _, addr := range slices.Sorted(maps.Keys(es)) {
		if err := ok(addr); err != nil {
			return written, err
		}
		old, err := readBlock(p, addr)
		if err != nil {
			return written, err
		}
		blk, err := patch(addr, old, es[addr])
		if err != nil {
			return written, err
		}
		if bytes.Equal(blk, old) {
			say("block unchanged", "addr", fmt.Sprintf("%04X", addr))
			continue
		}
		if err := sendBlock(p, addr, blk); err != nil {
			return written, err
		}
		// The radio acks every block, even one its EEPROM refused (clone.c, for CHIRP): read it back.
		if got, err := readBlock(p, addr); err != nil || !bytes.Equal(got, blk) {
			return written, fmt.Errorf("block %04X: the radio did not keep the write (%v)", addr, err)
		}
		written++
		say("block written", "addr", fmt.Sprintf("%04X", addr))
	}
	return written, nil
}

// ---- command ----

func printTable(chans []channel) {
	t := table.New().Border(lipgloss.NormalBorder()).
		Headers("Loc", "Name", "RX MHz", "TX MHz", "RX tone", "TX tone", "Pwr", "BW", "Scan", "Groups")
	for _, c := range chans {
		pwr, bw, scan := "LHMU"[c.power:c.power+1], "W", "-"
		if c.narrow {
			bw = "N"
		}
		if c.scan {
			scan = "yes"
		}
		t.Row(strconv.Itoa(c.loc), c.name, mhz(c.rx), mhz(c.tx), c.rxTone.String(), c.txTone.String(), pwr, bw, scan, groupList(c.groups))
	}
	lipgloss.Println(t)
}

func runImport(portName, file string, wipe, dry bool) error {
	f, err := os.Open(file)
	if err != nil {
		return err
	}
	defer f.Close()
	res, err := parseCSV(f)
	if err != nil {
		return fmt.Errorf("%s: %w", file, err)
	}
	for _, w := range res.warns {
		logger.Warn(w)
	}
	if len(res.chans) == 0 {
		return fmt.Errorf("%s: no channels in 1-%d (nothing written, not even with --clear)", file, maxChan)
	}
	if dry {
		printTable(res.chans)
		logger.Info("dry run: the radio was not touched", "channels", len(res.chans), "skipped", res.skipped)
		return nil
	}
	es := plan(res.chans, wipe)
	p, err := open(portName, 38400)
	if err != nil {
		return err
	}
	defer p.Close()
	for _, c := range res.chans {
		logger.Info("channel", "n", c.loc, "name", c.name, "rx", mhz(c.rx), "tx", mhz(c.tx),
			"rx_tone", c.rxTone.String(), "tx_tone", c.txTone.String(), "groups", groupList(c.groups))
	}
	written, err := importBlocks(p, es, writable, func(m string, kv ...any) { logger.Debug(m, kv...) })
	if err != nil {
		return fmt.Errorf("import stopped part-way (the radio may hold a mix of old and new channels; rerun to finish): %w", err)
	}
	cleared := 0
	if wipe {
		cleared = maxChan - len(res.chans)
	}
	logger.Info("done", "channels", len(res.chans), "skipped", res.skipped, "cleared", cleared,
		"blocks_written", written, "blocks_unchanged", len(es)-written)
	return nil
}
