// tdh3 settings restore: the settings from a `tdh3 eeprom backup` file put back over PC LINK; the
// channels and the factory calibration stay as the radio has them.
//
// The settings are 0x1FC0-0x1FEF (cfg in fw/main.c). Only those bytes are edited: the second block,
// 0x1FE0, also holds 0x1FF0-0x1FFF of the calibration, which goes back exactly as read from the radio
// (and td-h3-fw's ee_write refuses it anyway). td-h3-fw restarts after the E that ends a session that
// wrote 0x1FC0 (fw/clone.c), so it loads the restored settings at once.
package main

import (
	"errors"
	"fmt"
	"os"
)

// settingsMagics are the settings magics td-h3-fw loads (cfg_load moves 0xAB and 0xAC up).
var settingsMagics = []byte{0xAB, 0xAC, 0xAD}

// restoreWritable is writable plus the two settings blocks, for tdh3 settings restore only.
func restoreWritable(addr int) error {
	if addr == cfgAddr || addr == cfgAddr+32 {
		return nil
	}
	return writable(addr)
}

// restorePlan sets 0x1FC0-0x1FEF to mem's, or says why mem holds no settings to restore.
func restorePlan(mem []byte) (map[int][]edit, error) {
	m := mem[cfgAddr]
	ok := false
	for _, v := range settingsMagics {
		ok = ok || m == v
	}
	if !ok {
		return nil, fmt.Errorf("the backup holds no td-h3-fw settings (check byte %02X): nothing was written", m)
	}
	es := map[int][]edit{}
	for a := cfgAddr; a < 0x1FF0; a++ {
		es[a&^31] = append(es[a&^31], edit{a, 0xFF, mem[a]})
	}
	return es, nil
}

func runRestore(portName, file string, ask bool) error {
	mem, err := loadImage(file)
	if err != nil {
		return err
	}
	es, err := restorePlan(mem)
	if err != nil {
		return err
	}
	if portName == "" {
		if portName, err = findPort(); err != nil {
			return err
		}
	}
	if ask && !isTerm(os.Stdin) {
		return errors.New("not a terminal to press Enter in: pass --yes")
	}
	say("")
	row("radio", valS.Render(portName))
	row("restores", "every setting from "+valS.Render(file)+" (the menu, both slots' frequencies, the FM radio station)")
	row("keeps", "the memory channels")
	row("never", "the factory TX calibration")
	if ask {
		end := watchStop()
		err := enter(ambS.Render("Enter") + " restores" + noteS.Render(" (radio in PC LINK: MENU, 4 5, MENU), Ctrl-C stops"))
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
	n, err := importBlocks(p, es, restoreWritable, func(m string, kv ...any) { logger.Debug(m, kv...) })
	if err != nil {
		if errors.Is(err, errNoHello) {
			return errors.New("the radio didn't answer: put it in PC LINK first (MENU, 4 5, MENU), then run this again")
		}
		return err
	}
	if n == 0 {
		okLine("the radio already has these settings: nothing was written")
		return nil
	}
	if stillInPCLink(p) {
		okLine("settings restored")
		warnLine("this firmware doesn't restart by itself: switch the radio off and on now, without pressing V/M")
		return nil
	}
	okLine("settings restored: the radio restarted with them")
	return nil
}
