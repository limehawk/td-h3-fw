//go:build release

package main

import _ "embed"

// A release's flasher carries that release's firmware, so one download flashes a radio.
// tools/dist.sh copies both images into builtin/ and builds with -tags release.

//go:embed builtin/td-h3.bin
var builtInFull []byte

//go:embed builtin/td-h3-rx.bin
var builtInRX []byte

func builtIn(rx bool) []byte {
	if rx {
		return builtInRX
	}
	return builtInFull
}
