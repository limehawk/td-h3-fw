package main

import (
	"archive/zip"
	"bytes"
	"crypto/ed25519"
	"crypto/sha256"
	"encoding/base64"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"os"
	"os/exec"
	"os/signal"
	"path/filepath"
	"runtime"
	"strconv"
	"strings"
	"time"

	"github.com/spf13/cobra"
)

// tdh3 update: the newest release's flasher replaces this one, then flashes the firmware it
// carries. A release's SHA256SUMS is signed (tools/sign, in tools/dist.sh); the signature is
// checked against releaseKey before the checksum, and nothing is replaced until both pass.

const releaseRepo = "limehawk/td-h3-fw" // GitHub redirects the API if the repo is ever renamed

var (
	releaseAPI = "https://api.github.com/repos/" + releaseRepo + "/releases/latest"
	releaseKey = mustKey("iiUT6PcV3a+zbYHOPPyb4GMTpGOocLO9gvTJ7J96By4=")
	web        = &http.Client{Timeout: 5 * time.Minute}
)

func mustKey(b64 string) ed25519.PublicKey {
	k, err := base64.StdEncoding.DecodeString(b64)
	if err != nil || len(k) != ed25519.PublicKeySize {
		panic("bad release key")
	}
	return k
}

type ghRelease struct {
	Tag    string `json:"tag_name"`
	Assets []struct {
		Name string `json:"name"`
		URL  string `json:"browser_download_url"`
	} `json:"assets"`
}

func (r *ghRelease) asset(name string) string {
	for _, a := range r.Assets {
		if a.Name == name {
			return a.URL
		}
	}
	return ""
}

// assetName is this computer's flasher in a release, as tools/dist.sh names it.
func assetName(goos, goarch string) (string, error) {
	n, ok := map[string]string{
		"windows/amd64": "tdh3-windows.exe",
		"darwin/arm64":  "tdh3-macos-apple.zip",
		"darwin/amd64":  "tdh3-macos-intel.zip",
		"linux/amd64":   "tdh3-linux-x64",
		"linux/arm64":   "tdh3-linux-arm64",
	}[goos+"/"+goarch]
	if !ok {
		return "", fmt.Errorf("releases have no flasher for %s/%s: build tdh3 from source", goos, goarch)
	}
	return n, nil
}

// newer reports whether version a is later than b ("0.61.0" > "0.60.3" > "0.60.3-rc1").
func newer(a, b string) bool {
	num := func(v string) ([3]int, bool) {
		v = strings.TrimPrefix(v, "v")
		core, _, pre := strings.Cut(v, "-")
		var n [3]int
		for i, f := range strings.SplitN(core, ".", 3) {
			n[i], _ = strconv.Atoi(f)
		}
		return n, pre
	}
	na, pa := num(a)
	nb, pb := num(b)
	if na != nb {
		return na[0] > nb[0] || na[0] == nb[0] && (na[1] > nb[1] || na[1] == nb[1] && na[2] > nb[2])
	}
	return !pa && pb
}

func get(url string) ([]byte, error) {
	req, err := http.NewRequest("GET", url, nil)
	if err != nil {
		return nil, err
	}
	req.Header.Set("User-Agent", "tdh3")
	resp, err := web.Do(req)
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		return nil, fmt.Errorf("%s: %s", url, resp.Status)
	}
	b, err := io.ReadAll(io.LimitReader(resp.Body, 64<<20+1))
	if err == nil && len(b) > 64<<20 {
		err = fmt.Errorf("%s: over 64 MB", url)
	}
	return b, err
}

func latestRelease() (*ghRelease, error) {
	b, err := get(releaseAPI)
	if err != nil {
		return nil, fmt.Errorf("can't reach GitHub to look for a new release: %w", err)
	}
	var r ghRelease
	if err := json.Unmarshal(b, &r); err != nil || r.Tag == "" {
		return nil, fmt.Errorf("GitHub's answer about the latest release made no sense: %v", err)
	}
	return &r, nil
}

// fetchFlasher downloads the release's asset name, SHA256SUMS and SHA256SUMS.sig, checks the
// signature with key and then the checksum, and returns the flasher program (unzipped for a Mac).
func fetchFlasher(r *ghRelease, name string, key ed25519.PublicKey) ([]byte, error) {
	var files [3][]byte
	for i, n := range []string{"SHA256SUMS", "SHA256SUMS.sig", name} {
		url := r.asset(n)
		if url == "" && n == "SHA256SUMS.sig" {
			return nil, fmt.Errorf("release %s isn't signed (no SHA256SUMS.sig), so it can't be checked; nothing was changed", r.Tag)
		}
		if url == "" {
			return nil, fmt.Errorf("release %s has no %s; nothing was changed", r.Tag, n)
		}
		var err error
		if files[i], err = get(url); err != nil {
			return nil, fmt.Errorf("download %s: %w", n, err)
		}
	}
	sums, sig, bin := files[0], files[1], files[2]
	s, err := base64.StdEncoding.DecodeString(strings.TrimSpace(string(sig)))
	if err != nil || !ed25519.Verify(key, sums, s) {
		return nil, fmt.Errorf("release %s: SHA256SUMS isn't signed with this project's key; nothing was changed", r.Tag)
	}
	got := sha256.Sum256(bin)
	ok := false
	for _, line := range strings.Split(string(sums), "\n") {
		if f := strings.Fields(line); len(f) == 2 && strings.TrimPrefix(f[1], "*") == name {
			ok = f[0] == hex.EncodeToString(got[:])
			break
		}
	}
	if !ok {
		return nil, fmt.Errorf("release %s: %s doesn't match its checksum in SHA256SUMS; nothing was changed", r.Tag, name)
	}
	if strings.HasSuffix(name, ".zip") {
		return unzipOne(bin)
	}
	return bin, nil
}

// unzipOne is the one file in a Mac release's zip.
func unzipOne(b []byte) ([]byte, error) {
	z, err := zip.NewReader(bytes.NewReader(b), int64(len(b)))
	if err != nil {
		return nil, err
	}
	if len(z.File) != 1 {
		return nil, fmt.Errorf("zip holds %d files, expected the flasher alone", len(z.File))
	}
	f, err := z.File[0].Open()
	if err != nil {
		return nil, err
	}
	defer f.Close()
	return io.ReadAll(io.LimitReader(f, 64<<20))
}

// oldExe is where Windows keeps the replaced flasher until the next start deletes it: a running
// .exe can be renamed but not overwritten or deleted.
func oldExe(exe string) string { return filepath.Join(filepath.Dir(exe), "tdh3.old.exe") }

// replaceExe puts bin in place of the program at exe. Elsewhere than Windows a new file next to
// it is renamed over it, in one step. On Windows the running exe moves aside to tdh3.old.exe
// first, and comes back if the new one can't be written.
func replaceExe(exe string, bin []byte, windows bool) error {
	if windows {
		old := oldExe(exe)
		os.Remove(old)
		if err := os.Rename(exe, old); err != nil {
			return err
		}
		if err := os.WriteFile(exe, bin, 0o755); err != nil {
			os.Remove(exe)
			os.Rename(old, exe)
			return err
		}
		return nil
	}
	f, err := os.CreateTemp(filepath.Dir(exe), ".tdh3-update-*")
	if err != nil {
		return err
	}
	defer os.Remove(f.Name()) // gone after the rename; cleans up after a failure
	if _, err := f.Write(bin); err != nil {
		f.Close()
		return err
	}
	if err := f.Close(); err != nil {
		return err
	}
	if err := os.Chmod(f.Name(), 0o755); err != nil {
		return err
	}
	return os.Rename(f.Name(), exe)
}

// Every start on Windows deletes the flasher an update replaced (it fails, harmlessly, while that
// one still runs: the flash an update starts).
func init() {
	if runtime.GOOS == "windows" {
		if exe, err := os.Executable(); err == nil {
			os.Remove(oldExe(exe))
		}
	}
}

func updateCmd(port *string) *cobra.Command {
	var noFlash bool
	c := &cobra.Command{
		Use:   "update",
		Short: "Replace this flasher with the newest release's, then flash the firmware it carries",
		Long: "Asks GitHub for the newest release and downloads this computer's flasher from it. Its\n" +
			"signed checksum is checked before anything is replaced, so a damaged or tampered download\n" +
			"is refused and this flasher keeps working. Then it runs the new flasher's flash, the same\n" +
			"as tdh3 flash. --no-flash stops after replacing the flasher.",
		Args: cobra.NoArgs,
		RunE: func(cmd *cobra.Command, args []string) error { return runUpdate(*port, noFlash) },
	}
	c.Flags().BoolVar(&noFlash, "no-flash", false, "only replace the flasher; don't flash the radio")
	return c
}

func runUpdate(port string, noFlash bool) error {
	name, err := assetName(runtime.GOOS, runtime.GOARCH)
	if err != nil {
		return err
	}
	r, err := latestRelease()
	if err != nil {
		return err
	}
	latest := strings.TrimPrefix(r.Tag, "v")
	if img := builtIn(false); img != nil {
		cur := imageVersion("", img)
		if cur == latest {
			logger.Info("up to date", "version", "v"+cur)
			return nil
		}
		if newer(cur, latest) {
			logger.Info("this flasher is newer than the latest release", "this", "v"+cur, "latest", r.Tag)
			return nil
		}
		logger.Info("new release", "this", "v"+cur, "latest", r.Tag)
	} else {
		logger.Info("this tdh3 was built from source, so it never counts as up to date: getting the newest release", "release", r.Tag)
	}
	bin, err := fetchFlasher(r, name, releaseKey)
	if err != nil {
		return err
	}
	exe, err := os.Executable()
	if err == nil {
		exe, err = filepath.EvalSymlinks(exe)
	}
	if err != nil {
		return err
	}
	if err := replaceExe(exe, bin, runtime.GOOS == "windows"); err != nil {
		return fmt.Errorf("can't replace %s (%w): download the new flasher from the release page instead", exe, err)
	}
	logger.Info("flasher updated", "version", r.Tag, "file", exe)
	if noFlash {
		return nil
	}
	args := []string{"flash"}
	if port != "" {
		args = append(args, "-p", port)
	}
	cmd := exec.Command(exe, args...)
	cmd.Stdin, cmd.Stdout, cmd.Stderr = os.Stdin, os.Stdout, os.Stderr
	sig := make(chan os.Signal, 1) // Ctrl-C is the new flasher's to handle: this one waits for it
	signal.Notify(sig, os.Interrupt)
	defer signal.Stop(sig)
	if err := cmd.Run(); err != nil {
		var ee *exec.ExitError
		if errors.As(err, &ee) {
			return errors.New("flashing didn't finish: run tdh3 flash to try again")
		}
		return err
	}
	return nil
}
