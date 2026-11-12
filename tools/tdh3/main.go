// tdh3 talks to a TIDRADIO TD-H3 over its USB-C port.
//
//	tdh3 eeprom backup  -p /dev/ttyUSB1 backups/td-h3-eeprom.bin
//	tdh3 flash
//	tdh3 update
//	tdh3 firmware flash -p /dev/ttyUSB1 fw/build/td-h3-rx.bin
//	tdh3 firmware extract firmware/
//	tdh3 channels import -p /dev/ttyUSB1 list.csv
//	tdh3 channels copy -p /dev/ttyUSB1 radio1.bin
//	tdh3 channels list -p /dev/ttyUSB1
//	tdh3 channels set -p /dev/ttyUSB1 88-94 --power ulow
//	tdh3 reset -p /dev/ttyUSB1
//	tdh3 settings restore -p /dev/ttyUSB1 backups/td-h3-eeprom.bin
//	tdh3 remote screen -p /dev/ttyUSB1 -o screen.png
//	tdh3 remote keys -p /dev/ttyUSB1 1 4 6 5 2 0
//	tdh3 remote tune -p /dev/ttyUSB1 146.520
//	tdh3 remote wait -p /dev/ttyUSB1 "squelch open"
//
// Commands are noun then verb: tdh3 eeprom backup, tdh3 channels import.
package main

import (
	"context"
	"errors"
	"fmt"
	"math"
	"os"
	"path/filepath"
	"regexp"
	"strconv"
	"strings"
	"time"

	"charm.land/fang/v2"
	"charm.land/log/v2"
	"github.com/spf13/cobra"
	"go.bug.st/serial"
)

var logger = log.NewWithOptions(os.Stderr, log.Options{ReportTimestamp: true, TimeFormat: time.Kitchen})

func open(port string, baud int) (serial.Port, error) {
	if port == "" { // no -p: the one radio cable plugged in (channels import --dry-run never gets here)
		switch ports := radioPorts(); len(ports) {
		case 0:
			return nil, errors.New("no radio cable found: plug the radio's USB-C cable into the computer")
		case 1:
			port = ports[0]
		default:
			return nil, manyCables(ports)
		}
	}
	p, err := serial.Open(port, &serial.Mode{BaudRate: baud})
	if err != nil {
		return nil, fmt.Errorf("open %s: %w (check ls -l /dev/serial/by-id/)", port, err)
	}
	return p, nil
}

func main() {
	var port string
	root := &cobra.Command{
		Use:   "tdh3",
		Short: "Back up, program and flash a TIDRADIO TD-H3 over USB-C",
	}
	root.PersistentFlags().StringVarP(&port, "port", "p", "", "serial port of the radio, e.g. /dev/ttyUSB1 (found by itself with one radio cable plugged in)")

	eeprom := &cobra.Command{Use: "eeprom", Short: "The radio's 8 KB settings and calibration memory"}
	eeprom.AddCommand(&cobra.Command{
		Use:   "backup FILE",
		Short: "Read the whole EEPROM into FILE (read-only: never writes to the radio)",
		Long: "Uses the clone protocol at 38400. Works with stock firmware, and with td-h3-fw once\n" +
			"the radio is in PC LINK (MENU, 4 5, MENU). FILE is the 8-byte ident followed by\n" +
			"the 8192 EEPROM bytes. -p is found by itself when there is one radio cable.",
		Args: cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			if port == "" { // one radio cable plugged in: use it
				var err error
				if port, err = findPort(); err != nil {
					return err
				}
			}
			p, err := open(port, 38400)
			if err != nil {
				return err
			}
			defer p.Close()
			ident, mem, err := backup(p, func(n int) { logger.Debug("block", "n", n) })
			if err != nil {
				return err
			}
			if err := os.WriteFile(args[0], append(ident, mem...), 0o644); err != nil {
				return err
			}
			logger.Info("saved", "file", args[0], "ident", fmt.Sprintf("%q", ident), "bytes", len(mem),
				"calibration", calibrated(mem))
			return nil
		},
	})

	firmware := &cobra.Command{Use: "firmware", Short: "The application image in flash"}
	var forceFw bool
	fwFlash := &cobra.Command{
		Use:   "flash IMAGE",
		Short: "Write IMAGE through the bootloader (hold PTT while switching the radio on)",
		Long: "Refuses images over 63488 bytes (they would reach the bootloader at 0xF800) and\n" +
			"images without an LJMP reset vector, and untagged builds without --force. The bootloader\n" +
			"itself is never written.",
		Args: cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error { return flashFile(port, args[0], false, forceFw) },
	}
	fwFlash.Flags().BoolVar(&forceFw, "force", false, "flash a build that isn't a tagged release (\"0.54.2-1\", \"+\")")
	firmware.AddCommand(fwFlash, &cobra.Command{
		Use:   "extract [FOLDER]",
		Short: "Save the firmware this flasher carries as .bin files (full and receive-only)",
		Long:  "Writes td-h3-fw-VERSION.bin and td-h3-fw-VERSION-rx.bin into FOLDER, the current one if you\ngive none. Doesn't need a radio.",
		Args:  cobra.MaximumNArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			saved, err := extractFirmware(append(args, ".")[0])
			for _, p := range saved {
				fmt.Println(p)
			}
			return err
		},
	})

	var wipe, dry bool
	channels := &cobra.Command{Use: "channels", Short: "The radio's memory channels (1-199)"}
	imp := &cobra.Command{
		Use:   "import FILE",
		Short: "Load a CHIRP CSV export into the radio (-p is not needed with --dry-run)",
		Long: "Reads FILE (columns found by header name), checks every row, then patches only the\n" +
			"channel, name and used/scan-list areas of the EEPROM, a 32-byte block at a time, and\n" +
			"writes back only the blocks that changed. The factory TX calibration is never written.\n" +
			"Locations outside 1-199 are skipped. G1..G8 in the Comment column set the scan groups.",
		Args: cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error { return runImport(port, args[0], wipe, dry) },
	}
	imp.Flags().BoolVar(&wipe, "clear", false, "also clear every channel 1-199 that is not in the CSV")
	imp.Flags().BoolVar(&dry, "dry-run", false, "parse and print the channels without opening the port")
	channels.AddCommand(imp)
	var fm, yesCopy bool
	cp := &cobra.Command{
		Use:   "copy FROM.bin",
		Short: "Copy another TD-H3's channel list (its eeprom backup file) onto this radio",
		Long: "FROM.bin is a `tdh3 eeprom backup` of the radio to copy from. Shows what it will do and\n" +
			"waits for Enter, saves a backup of this radio to backups/, then writes only the channel\n" +
			"records, names, used and scan-list bitmaps (and with --fm, on by default, the FM presets),\n" +
			"and reads them back to check. Settings and calibration are never written. -p is found\n" +
			"by itself when there is one radio cable.",
		Args: cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error { return runCopy(port, args[0], fm, !yesCopy) },
	}
	cp.Flags().BoolVar(&fm, "fm", true, "also copy the 25 FM radio presets (--fm=false keeps this radio's)")
	cp.Flags().BoolVarP(&yesCopy, "yes", "y", false, "don't wait for Enter")
	channels.AddCommand(cp)
	channels.AddCommand(chanCmds(&port)...)

	var resetCh, yesReset bool
	reset := &cobra.Command{
		Use:   "reset",
		Short: "Put every setting back to its default (the channels stay; --channels clears them too)",
		Long: "Over PC LINK (MENU, 4 5, MENU): clears the settings' check byte so the radio loads every\n" +
			"default, and td-h3-fw restarts by itself to use them. Shows what it will do and waits for\n" +
			"Enter. The memory channels stay unless --channels; the factory TX calibration is never\n" +
			"written. -p is found by itself when there is one radio cable.",
		Args: cobra.NoArgs,
		RunE: func(cmd *cobra.Command, args []string) error { return runReset(port, resetCh, !yesReset) },
	}
	reset.Flags().BoolVar(&resetCh, "channels", false, "also clear every memory channel 1-199")
	reset.Flags().BoolVarP(&yesReset, "yes", "y", false, "don't wait for Enter")

	var yesRestore bool
	settings := &cobra.Command{Use: "settings", Short: "The radio's menu settings"}
	restore := &cobra.Command{
		Use:   "restore BACKUP.bin",
		Short: "Put the settings from a `tdh3 eeprom backup` file back (the channels stay)",
		Long: "Over PC LINK (MENU, 4 5, MENU): writes only the settings (0x1FC0-0x1FEF) from BACKUP.bin,\n" +
			"which may come from another TD-H3, reads them back, and td-h3-fw restarts with them. Shows\n" +
			"what it will do and waits for Enter. The memory channels and the factory TX calibration on\n" +
			"the radio are never written. -p is found by itself when there is one radio cable.",
		Args: cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error { return runRestore(port, args[0], !yesRestore) },
	}
	restore.Flags().BoolVarP(&yesRestore, "yes", "y", false, "don't wait for Enter")
	settings.AddCommand(restore)

	var shot string
	remote := &cobra.Command{
		Use:   "remote",
		Short: "See the radio's screen and press its keys (td-h3-fw, MENU, REMOTE, ON)",
		Long: "td-h3-fw mirrors every screen draw to USB-C and takes key presses while REMOTE is ON.\n" +
			"It refuses PTT and PTT-B: nothing here can make the radio transmit.",
	}
	var wait time.Duration
	remote.PersistentFlags().DurationVar(&wait, "wait", 1500*time.Millisecond, "how long a screen read waits for the radio to redraw")
	scr := &cobra.Command{
		Use:   "screen",
		Short: "Print what the screen shows (and save it as a PNG with -o)",
		Args:  cobra.NoArgs,
		RunE: func(cmd *cobra.Command, args []string) error {
			p, err := openRemote(port)
			if err != nil {
				return err
			}
			defer p.Close()
			sc, err := look(p, wait)
			if err != nil {
				return err
			}
			fmt.Print(sc.Text())
			if sc.query != "" {
				fmt.Println(status(sc.query))
			}
			if shot == "" {
				return nil
			}
			f, err := os.Create(shot)
			if err != nil {
				return err
			}
			defer f.Close()
			return sc.PNG(f)
		},
	}
	scr.Flags().StringVarP(&shot, "output", "o", "", "also write the screen to this PNG file")
	var every time.Duration
	watch := &cobra.Command{
		Use:   "watch",
		Short: "Print the signal and whether the squelch is open, five times a second, until Ctrl-C",
		Long: "For finding why audio cuts out: if the signal holds while the squelch shows closed, the\n" +
			"firmware closed it; if the signal itself drops, it's the antenna or RF.",
		Args: cobra.NoArgs,
		RunE: func(cmd *cobra.Command, args []string) error {
			p, err := openRemote(port)
			if err != nil {
				return err
			}
			defer p.Close()
			start, last := time.Now(), ""
			var buf []byte
			for {
				p.Write([]byte("Q"))
				buf = append(buf, readN(p, 4096, every)...)
				m := statusLine.FindSubmatchIndex(buf)
				if m == nil {
					if len(buf) > 8192 {
						buf = buf[len(buf)-8192:]
					}
					continue
				}
				f := strings.Fields(string(buf[m[2]:m[3]]))
				buf = buf[m[1]:]
				dbm, _ := strconv.Atoi(f[1])          // decimal: Sscan would read "-077" as octal
				n := min(max((dbm+130)*30/80, 0), 30) // -130 .. -50 dBm
				sq := noteS.Render("closed")
				if f[2] == "O" {
					sq = okS.Render("OPEN  ")
				}
				mark := ""
				if last == "O" && f[2] == "C" {
					mark = ambS.Render("  <- closed")
				}
				last = f[2]
				fmt.Printf("  %6.1fs  %s MHz  %4d dBm  [%s%s]  %s%s\n", time.Since(start).Seconds(), f[0], dbm,
					ambS.Render(strings.Repeat("#", n)), noteS.Render(strings.Repeat("-", 30-n)), sq, mark)
			}
		},
	}
	watch.Flags().DurationVar(&every, "every", 200*time.Millisecond, "time between readings")
	remote.AddCommand(watch)
	var until time.Duration
	var gone bool
	wt := &cobra.Command{
		Use:   "wait TEXT",
		Short: "Wait until TEXT shows on the screen or in the status line (\"squelch open\", 146.520)",
		Long: "Follows the screen as the radio draws it and stops as soon as TEXT shows, in any case, in\n" +
			"the screen text or the status line in words (as remote screen prints them), then prints both.\n" +
			"--gone waits for it to go instead. With --timeout it gives up with an error after that long.",
		Args: cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			p, err := openRemote(port)
			if err != nil {
				return err
			}
			defer p.Close()
			sc, err := waitFor(p, args[0], gone, until)
			if err != nil {
				return err
			}
			fmt.Print(sc.Text())
			if sc.query != "" {
				fmt.Println(status(sc.query))
			}
			return nil
		},
	}
	wt.Flags().DurationVar(&until, "timeout", 0, "give up after this long, e.g. 30m; 0 waits for ever")
	wt.Flags().BoolVar(&gone, "gone", false, "wait for TEXT to disappear instead")
	remote.AddCommand(wt)
	remote.AddCommand(recordCmds(&port)...)
	remote.AddCommand(scr, &cobra.Command{
		Use:   "keys KEY...",
		Short: "Press keys: 0-9 MENU UP DOWN VM * # TORCH, add :long to hold (MENU:long)",
		Args:  cobra.MinimumNArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			p, err := openRemote(port)
			if err != nil {
				return err
			}
			defer p.Close()
			return press(p, args)
		},
	})

	// The step commands check the screen after every key and print the home screen when done.
	step := func(use, short string, args cobra.PositionalArgs, do func(r *rig, args []string) (*screen, error)) *cobra.Command {
		return &cobra.Command{Use: use, Short: short, Args: args, RunE: func(cmd *cobra.Command, a []string) error {
			p, err := openRemote(port)
			if err != nil {
				return err
			}
			defer p.Close()
			sc, err := do(&rig{p: p, wait: wait}, a)
			if err != nil {
				return err
			}
			fmt.Print(sc.Text())
			if sc.query != "" {
				fmt.Println(status(sc.query))
			}
			return nil
		}}
	}
	remote.AddCommand(
		step("home", "Back to the home screen: leaves menus, drops half-typed entries", cobra.NoArgs,
			func(r *rig, _ []string) (*screen, error) { return r.home() }),
		step("tune MHZ", "Put the selected slot on a frequency, e.g. 146.520 (switches it to VFO)", cobra.ExactArgs(1),
			func(r *rig, a []string) (*screen, error) {
				f, err := strconv.ParseFloat(a[0], 64)
				khz := int(math.Round(f * 1000))
				if err != nil || khz < 1000 || khz > 999999 {
					return nil, fmt.Errorf("frequency %q: give MHz with up to 3 decimals, e.g. 146.520", a[0])
				}
				return r.tune(khz)
			}),
		step("channel N", "Put the selected slot on memory channel N (1-199)", cobra.ExactArgs(1),
			func(r *rig, a []string) (*screen, error) {
				n, err := strconv.Atoi(a[0])
				if err != nil || n < 1 || n > 199 {
					return nil, fmt.Errorf("channel %q: give 1-199", a[0])
				}
				return r.channel(n)
			}),
		step("slot A|B", "Select slot A (top) or B (bottom)", cobra.ExactArgs(1),
			func(r *rig, a []string) (*screen, error) {
				s := strings.ToUpper(a[0])
				if s != "A" && s != "B" {
					return nil, fmt.Errorf("slot %q: A or B", a[0])
				}
				return r.slot(s)
			}),
		&cobra.Command{
			Use:   "settings",
			Short: "List every menu setting and its value (reads only, changes nothing)",
			Args:  cobra.NoArgs,
			RunE: func(cmd *cobra.Command, args []string) error {
				p, err := openRemote(port)
				if err != nil {
					return err
				}
				defer p.Close()
				items, err := (&rig{p: p, wait: wait}).settings()
				for i, s := range items {
					fmt.Printf("%2d  %s\n", i+1, s)
				}
				return err
			},
		},
	)

	var rx, yes, force bool
	ez := &cobra.Command{
		Use:   "flash",
		Short: "Flash the firmware this flasher carries (what a double-click does)",
		Long: "Finds the radio's cable (-p picks one when there are several) and the firmware: the one\n" +
			"this flasher carries (a release's; the full transmit build, --rx for receive-only), else\n" +
			"the newest td-h3-fw-*.bin in this folder or beside tdh3, else fw/build in a copy of the\n" +
			"source. Shows what it will do, then asks you to hold PTT: nothing changes on the radio\n" +
			"until you do. tdh3 firmware flash IMAGE flashes any other image.",
		Args: cobra.NoArgs,
		RunE: func(cmd *cobra.Command, args []string) error {
			path, img, err := newestImage(rx)
			if err != nil {
				return err
			}
			return flashImage(port, path, img, !yes, force)
		},
	}
	ez.Flags().BoolVar(&rx, "rx", false, "the receive-only build (td-h3-fw-*-rx.bin)")
	ez.Flags().BoolVarP(&yes, "yes", "y", false, "for scripts: wait for the radio as long as it takes (several cables need -p)")
	ez.Flags().BoolVar(&force, "force", false, "flash a build that isn't a tagged release (\"0.54.2-1\", \"+\")")

	root.AddCommand(ez, eeprom, channels, reset, settings, firmware, remote, updateCmd(&port))
	root.Args, root.RunE = cobra.NoArgs, ez.RunE // no command (a double-click): flash
	ver := "built from source"                   // a release flasher is the version of the firmware inside it
	if img := builtIn(false); img != nil {
		ver = "v" + imageVersion("", img)
	}
	err := fang.Execute(context.Background(), root, fang.WithVersion(ver), fang.WithColorSchemeFunc(classic))
	closeWait(false)
	if err != nil {
		os.Exit(1)
	}
}

// imageVersion is the first "N.N.N..." string in the image itself, else the version the Makefile
// stamped (build/version beside it), else "?" (a stock image). The image comes first: build/version
// follows git at every make, so after a tag it already names a version the image wasn't built as.
func imageVersion(path string, img []byte) string {
	if m := regexp.MustCompile(`\x00(\d+\.\d+\.\d+[0-9A-Za-z.+-]*)\x00`).FindSubmatch(img); m != nil {
		return string(m[1])
	}
	if b, err := os.ReadFile(filepath.Join(filepath.Dir(path), "version")); err == nil {
		return strings.TrimSpace(string(b))
	}
	return "?"
}
