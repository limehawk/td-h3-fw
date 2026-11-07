package main

import (
	"os"

	"golang.org/x/sys/windows"
)

// The console draws the flasher's colours and cursor moves only with VT processing on (Windows
// Terminal has it already; the old console window doesn't).
func init() {
	h := windows.Handle(os.Stdout.Fd())
	var m uint32
	if windows.GetConsoleMode(h, &m) == nil {
		windows.SetConsoleMode(h, m|windows.ENABLE_VIRTUAL_TERMINAL_PROCESSING)
	}
}

// keysMode stops the console echoing, buffering lines and turning Ctrl-C into a signal, so readKeys
// gets Enter and Ctrl-C as bytes. restore puts it back as it was.
func keysMode(fd int) (restore func(), err error) {
	h := windows.Handle(fd)
	var old uint32
	if err := windows.GetConsoleMode(h, &old); err != nil {
		return nil, err
	}
	if err := windows.SetConsoleMode(h, old&^(windows.ENABLE_LINE_INPUT|windows.ENABLE_ECHO_INPUT|windows.ENABLE_PROCESSED_INPUT)); err != nil {
		return nil, err
	}
	return func() { windows.SetConsoleMode(h, old) }, nil
}
