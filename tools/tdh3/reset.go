// tdh3 reset: every setting back to its default over PC LINK, the channels kept (--channels clears them too).
//
// It clears byte 0 of the settings' first block (0x1FC0), their magic: cfg_load (fw/main.c) loads
// every default when it is wrong. td-h3-fw restarts after the E that ends a session that wrote that
// block (fw/clone.c), so the settings in its memory can't be saved back over the defaults; older
// firmware stays in PC LINK and needs switching off and on. No other settings, VFO or calibration
// byte is written.
package main

import (
	"bytes"
	"errors"
	"os"
	"time"
)

const cfgAddr = 0x1FC0 // the settings' first block; byte 0 is their magic

// resetWritable is writable plus the settings' first block, for tdh3 reset only. 0x1FE0, the
// calibration and everything else outside writable stay refused.
func resetWritable(addr int) error {
	if addr == cfgAddr {
		return nil
	}
	return writable(addr)
}

// resetPlan clears the settings' magic and, with channels, every channel's used bit.
func resetPlan(channels bool) map[int][]edit {
	es := map[int][]edit{}
	if channels {
		es = plan(nil, true)
	}
	es[cfgAddr] = []edit{{cfgAddr, 0xFF, 0}}
	return es
}

// stillInPCLink sends one hello after the session's E: td-h3-fw that restarted doesn't answer it,
// older firmware still in PC LINK does.
func stillInPCLink(p port) bool {
	p.Write(magic)
	return bytes.Equal(readN(p, 8, time.Second), []byte{6})
}

func runReset(portName string, channels, ask bool) error {
	var err error
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
	row("resets", "every setting to its default (the menu, both slots' frequencies, the FM radio station)")
	if channels {
		row("clears", "every memory channel 1-199")
	} else {
		row("keeps", "the memory channels"+noteS.Render(" (--channels clears them too)"))
	}
	row("never", "the factory TX calibration")
	if ask {
		end := watchStop()
		err := enter(ambS.Render("Enter") + " resets" + noteS.Render(" (radio in PC LINK: MENU, 4 5, MENU), Ctrl-C stops"))
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
	if _, err := importBlocks(p, resetPlan(channels), resetWritable, func(m string, kv ...any) { logger.Debug(m, kv...) }); err != nil {
		if errors.Is(err, errNoHello) {
			return errors.New("the radio didn't answer: put it in PC LINK first (MENU, 4 5, MENU), then run this again")
		}
		return err
	}
	if channels {
		okLine("channels cleared")
	}
	if stillInPCLink(p) {
		okLine("settings reset")
		warnLine("this firmware doesn't restart by itself: switch the radio off and on now, without pressing V/M")
		return nil
	}
	okLine("settings reset: the radio restarted with every default")
	return nil
}
