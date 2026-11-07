//go:build !release

package main

// A plain go build carries no firmware: it flashes a downloaded .bin or fw/build (newestImage).
var builtInImg [2][]byte // full, rx; tests set them

func builtIn(rx bool) []byte {
	if rx {
		return builtInImg[1]
	}
	return builtInImg[0]
}
