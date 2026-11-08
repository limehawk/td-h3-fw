//go:build !release

package main

import (
	"bytes"
	"os"
	"path/filepath"
	"testing"
)

// A plain build has no firmware inside, so these find a .bin (a release flasher never gets that far).

// Downloaded from a release: the newest td-h3-fw-*.bin in the folder, by number, not by name.
func TestNewestImageFromADownload(t *testing.T) {
	dir := t.TempDir()
	for _, n := range []string{"td-h3-fw-0.9.0.bin", "td-h3-fw-0.10.0.bin", "td-h3-fw-0.10.0-rx.bin", "td-h3-fw-0.11.0-rx.bin"} {
		os.WriteFile(filepath.Join(dir, n), []byte{2}, 0o644)
	}
	t.Chdir(dir)
	for rx, want := range map[bool]string{false: "td-h3-fw-0.10.0.bin", true: "td-h3-fw-0.11.0-rx.bin"} {
		if p, _, err := newestImage(rx); err != nil || p != filepath.Join(dir, want) {
			t.Fatalf("rx=%v: %q %v", rx, p, err)
		}
	}
}

// tdh3 flash finds fw/build from anywhere in the checkout, the full build unless --rx.
func TestNewestImageFromInsideTheCheckout(t *testing.T) {
	root := t.TempDir()
	build := filepath.Join(root, "fw", "build")
	os.MkdirAll(filepath.Join(root, "tools", "tdh3"), 0o755)
	os.MkdirAll(build, 0o755)
	for _, n := range []string{"td-h3.bin", "td-h3-rx.bin"} {
		os.WriteFile(filepath.Join(build, n), []byte{2}, 0o644)
	}
	t.Chdir(filepath.Join(root, "tools", "tdh3"))
	for rx, want := range map[bool]string{false: "td-h3.bin", true: "td-h3-rx.bin"} {
		if p, _, err := newestImage(rx); err != nil || p != filepath.Join(build, want) {
			t.Fatalf("rx=%v: %q %v", rx, p, err)
		}
	}
}

// A release's flasher carries its firmware and flashes that, even with an older td-h3-fw-*.bin from
// an earlier download in the folder (tdh3 firmware flash FILE puts any other image on).
func TestNewestImageBuiltIn(t *testing.T) {
	full, rx := append([]byte{2, 0, 0, 0}, "\x000.60.0\x00"...), append([]byte{2, 0, 0, 1}, "\x000.60.0\x00"...)
	builtInImg = [2][]byte{full, rx}
	defer func() { builtInImg = [2][]byte{} }()
	dir := t.TempDir()
	t.Chdir(dir)
	for r, want := range map[bool]string{false: "td-h3-fw-0.60.0.bin (built in)", true: "td-h3-fw-0.60.0-rx.bin (built in)"} {
		p, img, err := newestImage(r)
		if err != nil || p != want || img[3] != map[bool]byte{false: 0, true: 1}[r] {
			t.Fatalf("rx=%v: %q %v", r, p, err)
		}
	}
	os.WriteFile(filepath.Join(dir, "td-h3-fw-0.59.0.bin"), []byte{2}, 0o644)
	if p, _, _ := newestImage(false); p != "td-h3-fw-0.60.0.bin (built in)" {
		t.Fatalf("a .bin in the folder beat the built-in firmware: %q", p)
	}
}

// Saved under a release's names, in a folder it makes; a plain build has nothing to save and says so.
func TestExtractFirmware(t *testing.T) {
	dir := filepath.Join(t.TempDir(), "new", "folder")
	if _, err := extractFirmware(dir); err == nil || fileExists(dir) {
		t.Fatalf("no firmware inside: err %v", err)
	}
	full, rx := append([]byte{2, 0, 0, 0}, "\x000.60.0\x00"...), append([]byte{2, 0, 0, 1}, "\x000.60.0\x00"...)
	builtInImg = [2][]byte{full, rx}
	defer func() { builtInImg = [2][]byte{} }()
	saved, err := extractFirmware(dir)
	if err != nil || len(saved) != 2 {
		t.Fatal(saved, err)
	}
	for name, want := range map[string][]byte{"td-h3-fw-0.60.0.bin": full, "td-h3-fw-0.60.0-rx.bin": rx} {
		if got, err := os.ReadFile(filepath.Join(dir, name)); err != nil || !bytes.Equal(got, want) {
			t.Fatalf("%s: %x %v", name, got, err)
		}
	}
}
