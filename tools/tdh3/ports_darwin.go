package main

import "path/filepath"

// radioPorts lists the radio's cable by name: macOS calls a CH340 cu.wchusbserial* (its own driver)
// or cu.usbserial-* (Apple's). The serial library's USB lookup needs cgo, which the release build
// for macOS doesn't have.
func radioPorts() []string {
	a, _ := filepath.Glob("/dev/cu.wchusbserial*")
	b, _ := filepath.Glob("/dev/cu.usbserial-*")
	return append(a, b...)
}
