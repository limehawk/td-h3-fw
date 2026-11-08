package main

import (
	"bytes"
	"fmt"
	"os"
	"testing"
	"time"
)

// TestManualPicture writes the flasher's finished screen for the manual, drawn by the flasher's own
// code (a real flash needs a radio). Only make -C fw screenshots runs it.
func TestManualPicture(t *testing.T) {
	file := os.Getenv("TDH3_SHOT")
	if file == "" {
		t.Skip("make -C fw screenshots runs this")
	}
	img, err := os.ReadFile("../../fw/build/td-h3.bin")
	if err != nil {
		t.Fatal(err)
	}
	ver := os.Getenv("TDH3_SHOT_VER") // the release number alone, as on the radio's pictures
	var b bytes.Buffer
	out = &b
	defer func() { out = os.Stderr; ui = nil }()
	ui = &live{st: stDone, since: time.Now().Add(-time.Second), cap: caption(okS, "v"+ver+" running"), fd: ^uintptr(0)}
	ui.fit(100, 40)
	row("radio", noteS.Render("/dev/ttyUSB0"))
	row("flashing", valS.Render("v"+ver)+"  "+noteS.Render("full (transmit)"))
	row("image", noteS.Render(fmt.Sprintf("td-h3.bin  %d blocks  %.1f KB", (len(img)+31)/32, float64(len(img))/1024)))
	say("")
	okLine("radio found, running " + valS.Render("v0.60.0"))
	okLine("radio ready to restart into flash mode")
	okLine("flash mode")
	okLine("erased")
	okLine(okS.Render("v" + ver + " is running on your radio"))
	say("     " + noteS.Render("(was v0.60.0, 11s, 4.6 KB/s)"))
	ui.draw()
	if err := os.WriteFile(file, b.Bytes(), 0o644); err != nil {
		t.Fatal(err)
	}
}
