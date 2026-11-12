// tdh3 channels list, export, set, clear, move, lock, unlock: the channels 1-199 one at a time.
//
// Every command that changes the radio reads all of it first and saves that to backups/, then
// works out byte edits against what it read, so whatever a command isn't asked to change on a
// channel (its busy lockout, say) stays. A bad value or slot writes nothing. Only the channel
// records, names and the used/scan bitmaps are written (writable), each block read back.
package main

import (
	"bytes"
	"encoding/binary"
	"encoding/csv"
	"errors"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"slices"
	"strconv"
	"strings"
	"time"

	"github.com/spf13/cobra"
)

// parseSlots turns "1-78,81,90-94" into channel numbers, in order, each once.
func parseSlots(s string) ([]int, error) {
	var out []int
	for _, part := range strings.Split(s, ",") {
		a, b, isRange := strings.Cut(strings.TrimSpace(part), "-")
		lo, err := strconv.Atoi(a)
		hi := lo
		if err == nil && isRange {
			hi, err = strconv.Atoi(b)
		}
		if err != nil || lo < 1 || hi > maxChan || lo > hi {
			return nil, fmt.Errorf("channels %q: give numbers 1-%d, like 1-78,81,90-94", part, maxChan)
		}
		for n := lo; n <= hi; n++ {
			if !slices.Contains(out, n) {
				out = append(out, n)
			}
		}
	}
	return out, nil
}

// decode reads channel n as the firmware does (chan_load); tx 0 is a channel that can't transmit.
func decode(mem []byte, n int) channel {
	r := mem[0x10*n : 0x10*n+16]
	word := func(i int) tone {
		if t := tone(binary.LittleEndian.Uint16(r[i:])); t != 0 {
			return t
		}
		return noTone
	}
	c := channel{loc: n, name: chanName(mem, n), rx: unbcd(binary.LittleEndian.Uint32(r)),
		rxTone: word(8), txTone: word(10), narrow: r[14]>>3&1 == 1, power: r[14] >> 4 & 3, scan: bitOn(mem, scanBase, n)}
	if !bytes.Equal(r[4:8], []byte{0xFF, 0xFF, 0xFF, 0xFF}) {
		c.tx = unbcd(binary.LittleEndian.Uint32(r[4:]))
	}
	if r[13]&3 == 1 { // td-h3-fw's mark: byte 15 is the groups, and the mode bits count
		c.groups, c.mode = r[15], r[14]>>2&1|r[13]>>2&2
	}
	return c
}

// chanEdits collects byte edits by block.
type chanEdits map[int][]edit

func (e chanEdits) put(a int, mask, val byte) { e[a&^31] = append(e[a&^31], edit{a, mask, val}) }
func (e chanEdits) bytes(a int, b []byte) {
	for i, v := range b {
		e.put(a+i, 0xFF, v)
	}
}
func (e chanEdits) bit(base, n int, on bool) {
	m := byte(1) << ((n - 1) & 7)
	e.put(base+(n-1)/8, m, m*b2u(on))
}
func (e chanEdits) freq(n, at int, u uint32) {
	var b [4]byte
	binary.LittleEndian.PutUint32(b[:], bcd(u))
	e.bytes(0x10*n+at, b[:])
}
func (e chanEdits) noTX(n int) { e.bytes(0x10*n+4, []byte{0xFF, 0xFF, 0xFF, 0xFF}) }
func (e chanEdits) tone(n, at int, t tone) {
	e.bytes(0x10*n+at, []byte{byte(t), byte(t >> 8)})
}
func (e chanEdits) name(n int, s string) { e.bytes(nameBase+8*(n-1), nameBytes(s)) }

func plural(n int) string {
	if n == 1 {
		return "1 channel"
	}
	return strconv.Itoa(n) + " channels"
}

func needUsed(mem []byte, slots []int) error {
	for _, n := range slots {
		if !bitOn(mem, usedBase, n) {
			return fmt.Errorf("channel %d is empty: nothing was written", n)
		}
	}
	return nil
}

// chanChange says what `channels set` changes; a nil field isn't touched.
type chanChange struct {
	name           *string
	power          *byte
	narrow         *bool
	rx             *uint32 // 10 Hz units
	duplex         *string // "+", "-", "simplex" or "off"
	offset         *uint32
	rxTone, txTone *tone
	groups         *byte
	scan           *bool
}

func parseTone(s string) (tone, error) {
	u := strings.ToUpper(strings.TrimSpace(s))
	switch {
	case u == "OFF" || u == "NONE" || u == "-":
		return noTone, nil
	case strings.HasPrefix(u, "D"):
		code, pol := u[1:], byte('N')
		if l := len(code); l > 0 && (code[l-1] == 'N' || code[l-1] == 'I' || code[l-1] == 'R') {
			code, pol = code[:l-1], code[l-1]
		}
		return dcs(code, pol != 'N')
	}
	return ctcss(u)
}

func (ch chanChange) edits(mem []byte, slots []int) (chanEdits, error) {
	if err := needUsed(mem, slots); err != nil {
		return nil, err
	}
	e := chanEdits{}
	for _, n := range slots {
		c, rec := decode(mem, n), 0x10*n
		if ch.name != nil {
			e.name(n, *ch.name)
		}
		if ch.power != nil {
			e.put(rec+14, 0x30, *ch.power<<4)
		}
		if ch.narrow != nil {
			e.put(rec+14, 0x08, b2u(*ch.narrow)<<3)
		}
		if ch.rxTone != nil {
			e.tone(n, 8, *ch.rxTone)
		}
		if ch.txTone != nil {
			e.tone(n, 10, *ch.txTone)
		}
		if ch.groups != nil {
			e.put(rec+13, 0x03, 1) // td-h3-fw's mark: byte 15 is the groups
			e.put(rec+15, 0xFF, *ch.groups)
		}
		if ch.scan != nil {
			e.bit(scanBase, n, *ch.scan)
		}
		if ch.rx == nil && ch.duplex == nil && ch.offset == nil {
			continue
		}
		rx, tx := c.rx, c.tx
		if ch.rx != nil {
			rx = *ch.rx
			e.freq(n, 0, rx)
		}
		dup, off := "off", uint32(0) // what the channel has now
		switch {
		case tx > c.rx:
			dup, off = "+", tx-c.rx
		case tx == c.rx:
			dup = "simplex"
		case tx != 0:
			dup, off = "-", c.rx-tx
		}
		if ch.duplex != nil {
			dup = *ch.duplex
		}
		if ch.offset != nil {
			off = *ch.offset
		}
		switch dup {
		case "off":
			e.noTX(n)
			continue
		case "simplex":
			tx = rx
		case "+":
			tx = rx + off
		case "-":
			if off >= rx {
				return nil, fmt.Errorf("channel %d: offset is larger than the frequency", n)
			}
			tx = rx - off
		}
		if (dup == "+" || dup == "-") && off == 0 {
			return nil, fmt.Errorf("channel %d: shift %s needs --offset (0.6 on 2 m, 5 on 70 cm)", n, dup)
		}
		if ch.offset != nil && dup == "simplex" {
			return nil, fmt.Errorf("channel %d is simplex: give --duplex + or - with --offset", n)
		}
		e.freq(n, 4, tx)
	}
	return e, nil
}

// clearEdits empties channels as the firmware's DELETE CH does: record all FF, used bit off.
func clearEdits(mem []byte, slots []int) (chanEdits, error) {
	if err := needUsed(mem, slots); err != nil {
		return nil, err
	}
	e := chanEdits{}
	for _, n := range slots {
		e.bytes(0x10*n, bytes.Repeat([]byte{0xFF}, 16))
		e.bit(usedBase, n, false)
	}
	return e, nil
}

// moveEdits moves a channel to an empty slot: record, name and scan mark.
func moveEdits(mem []byte, from, to int) (chanEdits, error) {
	if err := needUsed(mem, []int{from}); err != nil {
		return nil, err
	}
	if bitOn(mem, usedBase, to) {
		return nil, fmt.Errorf("channel %d is in use: clear it first, nothing was written", to)
	}
	e, err := clearEdits(mem, []int{from})
	if err != nil {
		return nil, err
	}
	e.bytes(0x10*to, mem[0x10*from:0x10*from+16])
	e.bytes(nameBase+8*(to-1), mem[nameBase+8*(from-1):nameBase+8*from])
	e.bit(usedBase, to, true)
	e.bit(scanBase, to, bitOn(mem, scanBase, from))
	return e, nil
}

// lockEdits stops a channel transmitting (all-FF TX, as CHIRP's "off"), or makes it simplex again.
func lockEdits(mem []byte, slots []int, lock bool) (chanEdits, error) {
	if err := needUsed(mem, slots); err != nil {
		return nil, err
	}
	e := chanEdits{}
	for _, n := range slots {
		if lock {
			e.noTX(n)
		} else if c := decode(mem, n); c.tx == 0 {
			e.freq(n, 4, c.rx)
		}
	}
	return e, nil
}

// ---- CSV ----

func csvTone(c channel) (mode, r, ct, code, pol, rxCode, cross string) {
	r, ct, code, rxCode, cross = "88.5", "88.5", "023", "023", "Tone->Tone"
	kind := func(t tone) string {
		switch {
		case t == noTone:
			return ""
		case t&0x8000 != 0:
			return "DTCS"
		}
		return "Tone"
	}
	hz := func(t tone) string { return strings.TrimSpace(t.String()) }
	num := func(t tone) string { return fmt.Sprintf("%03x", uint16(t)&0xFFF) }
	p := func(t tone) byte {
		if t != noTone && t&0x4000 != 0 && t&0x8000 != 0 {
			return 'R'
		}
		return 'N'
	}
	rk, tk := kind(c.rxTone), kind(c.txTone)
	pol = string([]byte{p(c.txTone), p(c.rxTone)})
	switch {
	case rk == "" && tk == "":
	case rk == "" && tk == "Tone":
		mode, r = "Tone", hz(c.txTone)
	case c.rxTone == c.txTone && tk == "Tone":
		mode, ct = "TSQL", hz(c.txTone)
	case rk == "DTCS" && tk == "DTCS" && uint16(c.rxTone)&0xFFF == uint16(c.txTone)&0xFFF:
		mode, code = "DTCS", num(c.txTone)
		rxCode = code
	default:
		mode, cross = "Cross", tk+"->"+rk
		if tk == "Tone" {
			r = hz(c.txTone)
		} else if tk == "DTCS" {
			code = num(c.txTone)
		}
		if rk == "Tone" {
			ct = hz(c.rxTone)
		} else if rk == "DTCS" {
			rxCode = num(c.rxTone)
		}
	}
	return
}

var powerNames = map[byte]string{powerLow: "Low", powerHigh: "High", powerMid: "Mid", powerULow: "ULow"}

func exportCSV(w io.Writer, chans []channel) error {
	cw := csv.NewWriter(w)
	cw.Write([]string{"Location", "Name", "Frequency", "Duplex", "Offset", "Tone", "rToneFreq", "cToneFreq", "DtcsCode",
		"DtcsPolarity", "RxDtcsCode", "CrossMode", "Mode", "TStep", "Skip", "Power", "Comment"})
	for _, c := range chans {
		dup, off := "", uint32(0)
		switch {
		case c.tx == 0:
			dup = "off"
		case c.tx > c.rx:
			dup, off = "+", c.tx-c.rx
		case c.tx < c.rx:
			dup, off = "-", c.rx-c.tx
		}
		tm, r, ct, code, pol, rxCode, cross := csvTone(c)
		mode := "FM"
		switch {
		case c.mode == 2:
			mode = "USB"
		case c.mode == 1 || c.rx >= 10800000 && c.rx < 13700000:
			mode = "AM"
		case c.narrow:
			mode = "NFM"
		}
		skip := ""
		if !c.scan {
			skip = "S"
		}
		var groups []string
		for g := range 8 {
			if c.groups>>g&1 == 1 {
				groups = append(groups, "G"+strconv.Itoa(g+1))
			}
		}
		cw.Write([]string{strconv.Itoa(c.loc), c.name, fmt.Sprintf("%.6f", float64(c.rx)/1e5), dup, fmt.Sprintf("%.6f", float64(off)/1e5),
			tm, r, ct, code, pol, rxCode, cross, mode, "5.00", skip, powerNames[c.power], strings.Join(groups, " ")})
	}
	cw.Flush()
	return cw.Error()
}

// ---- the radio ----

func pcLinkErr(err error) error {
	if errors.Is(err, errNoHello) {
		return errors.New("the radio didn't answer: put it in PC LINK first (MENU, 4 5, MENU), then run this again")
	}
	return err
}

// readMem is the EEPROM from a backup file, or from the radio in PC LINK.
func readMem(portName, file string) ([]byte, error) {
	if file != "" {
		return loadImage(file)
	}
	var err error
	if portName == "" {
		if portName, err = findPort(); err != nil {
			return nil, err
		}
	}
	p, err := open(portName, 38400)
	if err != nil {
		return nil, err
	}
	defer p.Close()
	sp := spin("reading the radio")
	defer sp.stop()
	_, mem, err := backup(p, func(int) {})
	return mem, pcLinkErr(err)
}

func selected(mem []byte, slots string) ([]channel, error) {
	ns := usedChannels(mem)
	if slots != "" {
		want, err := parseSlots(slots)
		if err != nil {
			return nil, err
		}
		ns = slices.DeleteFunc(ns, func(n int) bool { return !slices.Contains(want, n) })
	}
	var out []channel
	for _, n := range ns {
		out = append(out, decode(mem, n))
	}
	return out, nil
}

func runList(portName, file, slots string) error {
	mem, err := readMem(portName, file)
	if err != nil {
		return err
	}
	chans, err := selected(mem, slots)
	if err != nil {
		return err
	}
	printTable(chans)
	return nil
}

func runExport(portName, file, slots, out string) error {
	mem, err := readMem(portName, file)
	if err != nil {
		return err
	}
	chans, err := selected(mem, slots)
	if err != nil {
		return err
	}
	f, err := os.Create(out)
	if err != nil {
		return err
	}
	defer f.Close()
	if err := exportCSV(f, chans); err != nil {
		return err
	}
	okLine(fmt.Sprintf("%d channels saved to %s", len(chans), out))
	return nil
}

// changeChannels reads the radio, saves it to backups/, works out the edits and writes them.
func changeChannels(portName, done string, build func(mem []byte) (chanEdits, error)) error {
	var err error
	if portName == "" {
		if portName, err = findPort(); err != nil {
			return err
		}
	}
	p, err := open(portName, 38400)
	if err != nil {
		return err
	}
	defer p.Close()
	sp := spin("reading the radio")
	ident, mem, err := backup(p, func(int) {})
	sp.stop()
	if err != nil {
		return pcLinkErr(err)
	}
	e, err := build(mem)
	if err != nil {
		return err
	}
	path := filepath.Join("backups", "td-h3-"+time.Now().Format("20060102-150405")+".bin")
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		return err
	}
	if err := os.WriteFile(path, append(bytes.Clone(ident), mem...), 0o644); err != nil {
		return err
	}
	n, err := importBlocks(p, e, writable, func(m string, kv ...any) { logger.Debug(m, kv...) })
	if err != nil {
		return fmt.Errorf("stopped part-way; the radio's old memory is in %s: %w", path, err)
	}
	if n == 0 {
		okLine("the radio already has that: nothing was written")
		return nil
	}
	okLine(done + noteS.Render("  (backup: "+path+")"))
	return nil
}

// ---- commands ----

func changeFromFlags(cmd *cobra.Command) (chanChange, error) {
	var ch chanChange
	f, set := cmd.Flags(), cmd.Flags().Changed
	str := func(n string) string { v, _ := f.GetString(n); return strings.TrimSpace(v) }
	if set("name") {
		n := cleanName(str("name"))
		ch.name = &n
	}
	if set("power") {
		p, err := power(str("power"))
		if err != nil {
			return ch, err
		}
		ch.power = &p
	}
	if set("bw") {
		v := strings.ToLower(str("bw"))
		if v != "narrow" && v != "wide" {
			return ch, fmt.Errorf("--bw %q: narrow or wide", v)
		}
		n := v == "narrow"
		ch.narrow = &n
	}
	if set("rx") {
		u, err := units(str("rx"))
		if err != nil || u < 1 || u > 99999999 {
			return ch, fmt.Errorf("--rx %q: give MHz, like 146.520", str("rx"))
		}
		v := uint32(u)
		ch.rx = &v
	}
	if set("duplex") {
		d := strings.ToLower(str("duplex"))
		if !slices.Contains([]string{"+", "-", "simplex", "off"}, d) {
			return ch, fmt.Errorf("--duplex %q: +, -, simplex or off", d)
		}
		ch.duplex = &d
	}
	if set("offset") {
		u, err := units(str("offset"))
		if err != nil || u < 0 {
			return ch, fmt.Errorf("--offset %q: give MHz, like 0.6", str("offset"))
		}
		v := uint32(u)
		ch.offset = &v
	}
	tones := []struct {
		flag string
		to   []**tone
	}{{"tone", []**tone{&ch.rxTone, &ch.txTone}}, {"rx-tone", []**tone{&ch.rxTone}}, {"tx-tone", []**tone{&ch.txTone}}}
	for _, t := range tones {
		if !set(t.flag) {
			continue
		}
		v, err := parseTone(str(t.flag))
		if err != nil {
			return ch, fmt.Errorf("--%s: %w", t.flag, err)
		}
		for _, p := range t.to {
			*p = &v
		}
	}
	if set("groups") {
		var g byte
		if v := strings.ToLower(str("groups")); v != "none" {
			for _, part := range strings.Split(v, ",") {
				n, err := strconv.Atoi(strings.TrimSpace(strings.TrimPrefix(strings.TrimSpace(part), "g")))
				if err != nil || n < 1 || n > 8 {
					return ch, fmt.Errorf("--groups %q: numbers 1-8, like 1,3, or none", v)
				}
				g |= 1 << (n - 1)
			}
		}
		ch.groups = &g
	}
	if set("scan") {
		v := strings.ToLower(str("scan"))
		if v != "on" && v != "off" {
			return ch, fmt.Errorf("--scan %q: on or off", v)
		}
		on := v == "on"
		ch.scan = &on
	}
	return ch, nil
}

// chanCmds are the one-channel commands under `tdh3 channels`.
func chanCmds(port *string) []*cobra.Command {
	var file, slots string
	list := &cobra.Command{
		Use:   "list",
		Short: "Show the channels (radio in PC LINK, or --file a backup)",
		Args:  cobra.NoArgs,
		RunE:  func(*cobra.Command, []string) error { return runList(*port, file, slots) },
	}
	var outFile, outSlots, exportFrom string
	export := &cobra.Command{
		Use:   "export FILE.csv",
		Short: "Save the channels as a CHIRP CSV that `channels import` reads back",
		Args:  cobra.ExactArgs(1),
		RunE: func(_ *cobra.Command, a []string) error {
			outFile = a[0]
			return runExport(*port, exportFrom, outSlots, outFile)
		},
	}
	list.Flags().StringVar(&file, "file", "", "read this `tdh3 eeprom backup` file instead of the radio")
	list.Flags().StringVar(&slots, "slots", "", "only these channels, like 1-78,81")
	export.Flags().StringVar(&exportFrom, "file", "", "read this `tdh3 eeprom backup` file instead of the radio")
	export.Flags().StringVar(&outSlots, "slots", "", "only these channels, like 1-78,81")

	set := &cobra.Command{
		Use:   "set CHANNELS",
		Short: "Change only the fields you give, on one channel or many (1-78,81)",
		Long: "Radio in PC LINK (MENU, 4 5, MENU). Reads the radio, saves a backup to backups/, changes only\n" +
			"the fields given and reads them back. A bad value or an empty channel writes nothing.\n" +
			"  tdh3 channels set 88-94 --power ulow --bw narrow\n" +
			"  tdh3 channels set 11 --name \"CROSS MT\" --tone 156.7\n" +
			"  tdh3 channels set 1-39 --groups 1 --scan on",
		Args: cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, a []string) error {
			ns, err := parseSlots(a[0])
			if err != nil {
				return err
			}
			ch, err := changeFromFlags(cmd)
			if err != nil {
				return err
			}
			if cmd.Flags().NFlag() == 0 {
				return errors.New("nothing to change: give a field, like --power low (--help lists them)")
			}
			return changeChannels(*port, plural(len(ns))+" changed and checked", func(mem []byte) (chanEdits, error) { return ch.edits(mem, ns) })
		},
	}
	sf := set.Flags()
	sf.String("name", "", "name, up to 8 characters")
	sf.String("power", "", "ulow, low, mid, high, or watts like 5W")
	sf.String("bw", "", "narrow or wide")
	sf.String("rx", "", "receive frequency in MHz (the shift is kept)")
	sf.String("duplex", "", "+, -, simplex, or off (never transmits)")
	sf.String("offset", "", "shift in MHz, like 0.6 or 5")
	sf.String("tone", "", "the same tone both ways: 100.0, D023N, D023I or off")
	sf.String("rx-tone", "", "tone needed to hear")
	sf.String("tx-tone", "", "tone sent when talking")
	sf.String("groups", "", "scan groups, like 1,3, or none")
	sf.String("scan", "", "in the scan list: on or off")

	slotsCmd := func(use, short, done string, build func(mem []byte, ns []int) (chanEdits, error)) *cobra.Command {
		return &cobra.Command{
			Use: use + " CHANNELS", Short: short, Args: cobra.ExactArgs(1),
			RunE: func(_ *cobra.Command, a []string) error {
				ns, err := parseSlots(a[0])
				if err != nil {
					return err
				}
				return changeChannels(*port, plural(len(ns))+" "+done, func(mem []byte) (chanEdits, error) { return build(mem, ns) })
			},
		}
	}
	move := &cobra.Command{
		Use:   "move FROM TO",
		Short: "Move a channel to an empty slot",
		Args:  cobra.ExactArgs(2),
		RunE: func(_ *cobra.Command, a []string) error {
			from, err1 := strconv.Atoi(a[0])
			to, err2 := strconv.Atoi(a[1])
			if err1 != nil || err2 != nil || from < 1 || from > maxChan || to < 1 || to > maxChan || from == to {
				return fmt.Errorf("give two different channels, 1-%d", maxChan)
			}
			return changeChannels(*port, fmt.Sprintf("channel %d moved to %d", from, to), func(mem []byte) (chanEdits, error) { return moveEdits(mem, from, to) })
		},
	}
	return []*cobra.Command{list, export, set, move,
		slotsCmd("clear", "Empty channels (a backup is saved first)", "cleared", clearEdits),
		slotsCmd("lock", "Stop channels transmitting", "locked (receive only)", func(m []byte, ns []int) (chanEdits, error) { return lockEdits(m, ns, true) }),
		slotsCmd("unlock", "Let locked channels transmit again, simplex", "unlocked", func(m []byte, ns []int) (chanEdits, error) { return lockEdits(m, ns, false) }),
	}
}
