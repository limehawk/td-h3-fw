//go:build !darwin

package main

import (
	"strings"

	"go.bug.st/serial/enumerator"
)

// radioPorts lists the USB serial ports with the radio's cable chip on them (a CH340, VID 1a86).
func radioPorts() []string {
	list, _ := enumerator.GetDetailedPortsList()
	var ports []string
	for _, p := range list {
		if p.IsUSB && strings.EqualFold(p.VID, "1a86") {
			ports = append(ports, p.Name)
		}
	}
	return ports
}
