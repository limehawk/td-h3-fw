package main

import (
	"archive/tar"
	"archive/zip"
	"bytes"
	"compress/gzip"
	"crypto/ed25519"
	"crypto/rand"
	"crypto/sha256"
	"encoding/base64"
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"runtime"
	"strings"
	"testing"
)

func TestAssetName(t *testing.T) {
	for in, want := range map[string]string{
		"windows/amd64": "tdh3.exe",
		"darwin/arm64":  "tdh3-macos-apple.zip",
		"darwin/amd64":  "tdh3-macos-intel.zip",
		"linux/amd64":   "tdh3-linux-x64.tar.gz",
		"linux/arm64":   "tdh3-linux-arm64.tar.gz",
		"freebsd/amd64": "",
		"linux/386":     "",
	} {
		goos, goarch, _ := strings.Cut(in, "/")
		got, err := assetName(goos, goarch)
		if got != want || (err != nil) != (want == "") {
			t.Errorf("assetName(%s) = %q, %v; want %q", in, got, err, want)
		}
	}
}

func TestNewer(t *testing.T) {
	for _, c := range []struct {
		a, b string
		want bool
	}{
		{"0.61.0", "0.60.3", true},
		{"0.60.10", "0.60.9", true},
		{"1.0.0", "0.99.99", true},
		{"0.60.3", "0.60.3", false},
		{"0.60.3", "0.61.0", false},
		{"v0.60.4", "0.60.3", true},
		{"0.60.3", "0.60.3-rc1", true},
		{"0.60.3-rc1", "0.60.3", false},
		{"0.60.3-rc1", "0.60.2", true},
	} {
		if got := newer(c.a, c.b); got != c.want {
			t.Errorf("newer(%s, %s) = %v", c.a, c.b, got)
		}
	}
}

// serveRelease serves a latest release with these files.
func serveRelease(t *testing.T, files map[string][]byte) *httptest.Server {
	var srv *httptest.Server
	srv = httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path == "/latest" {
			rel := map[string]any{"tag_name": "v9.9.9"}
			var assets []map[string]string
			for n := range files {
				assets = append(assets, map[string]string{"name": n, "browser_download_url": srv.URL + "/dl/" + n})
			}
			rel["assets"] = assets
			json.NewEncoder(w).Encode(rel)
			return
		}
		b, ok := files[strings.TrimPrefix(r.URL.Path, "/dl/")]
		if !ok {
			http.NotFound(w, r)
			return
		}
		w.Write(b)
	}))
	t.Cleanup(srv.Close)
	return srv
}

func signedFiles(t *testing.T, name string, bin []byte) (map[string][]byte, ed25519.PublicKey, ed25519.PrivateKey) {
	pub, priv, err := ed25519.GenerateKey(rand.Reader)
	if err != nil {
		t.Fatal(err)
	}
	other := []byte("other")
	sums := fmt.Sprintf("%x  %s\n%x  %s\n", sha256.Sum256(other), "td-h3-fw-9.9.9.bin", sha256.Sum256(bin), name)
	return map[string][]byte{
		name:                 bin,
		"td-h3-fw-9.9.9.bin": other,
		"SHA256SUMS":         []byte(sums),
		"SHA256SUMS.sig":     []byte(base64.StdEncoding.EncodeToString(ed25519.Sign(priv, []byte(sums))) + "\n"),
	}, pub, priv
}

func fetch(t *testing.T, files map[string][]byte, name string, key ed25519.PublicKey) ([]byte, error) {
	t.Helper()
	srv := serveRelease(t, files)
	old := releaseAPI
	releaseAPI = srv.URL + "/latest"
	defer func() { releaseAPI = old }()
	r, err := latestRelease()
	if err != nil {
		t.Fatal(err)
	}
	if r.Tag != "v9.9.9" {
		t.Fatalf("tag %q", r.Tag)
	}
	return fetchFlasher(r, name, key)
}

func TestFetchSigned(t *testing.T) {
	bin := []byte("new flasher")
	files, pub, _ := signedFiles(t, "tdh3-linux-x64", bin)
	got, err := fetch(t, files, "tdh3-linux-x64", pub)
	if err != nil || !bytes.Equal(got, bin) {
		t.Fatalf("got %q, %v", got, err)
	}
}

func TestFetchRefused(t *testing.T) {
	bin := []byte("new flasher")
	for _, c := range []struct {
		name, want string
		mangle     func(f map[string][]byte, pub *ed25519.PublicKey)
	}{
		{"other key", "isn't signed", func(f map[string][]byte, pub *ed25519.PublicKey) {
			*pub, _, _ = ed25519.GenerateKey(rand.Reader)
		}},
		{"sums changed after signing", "isn't signed", func(f map[string][]byte, _ *ed25519.PublicKey) {
			f["SHA256SUMS"] = append(f["SHA256SUMS"], ' ')
		}},
		{"signature garbled", "isn't signed", func(f map[string][]byte, _ *ed25519.PublicKey) {
			f["SHA256SUMS.sig"] = []byte("not base64!")
		}},
		{"no signature", "isn't signed (no SHA256SUMS.sig)", func(f map[string][]byte, _ *ed25519.PublicKey) {
			delete(f, "SHA256SUMS.sig")
		}},
		{"flasher changed", "doesn't match", func(f map[string][]byte, _ *ed25519.PublicKey) {
			f["tdh3-linux-x64"] = []byte("tampered")
		}},
		{"no flasher", "has no tdh3-linux-x64", func(f map[string][]byte, _ *ed25519.PublicKey) {
			delete(f, "tdh3-linux-x64")
		}},
	} {
		t.Run(c.name, func(t *testing.T) {
			files, pub, _ := signedFiles(t, "tdh3-linux-x64", bin)
			c.mangle(files, &pub)
			if _, err := fetch(t, files, "tdh3-linux-x64", pub); err == nil || !strings.Contains(err.Error(), c.want) {
				t.Fatalf("err %v, want %q", err, c.want)
			}
		})
	}
}

func TestFetchMacZip(t *testing.T) {
	bin := []byte("mac flasher")
	var zb bytes.Buffer
	z := zip.NewWriter(&zb)
	h := &zip.FileHeader{Name: "tdh3", Method: zip.Deflate}
	h.SetMode(0o755)
	w, _ := z.CreateHeader(h)
	w.Write(bin)
	z.Close()
	files, pub, _ := signedFiles(t, "tdh3-macos-apple.zip", zb.Bytes())
	got, err := fetch(t, files, "tdh3-macos-apple.zip", pub)
	if err != nil || !bytes.Equal(got, bin) {
		t.Fatalf("got %q, %v", got, err)
	}
}

func TestReplaceExe(t *testing.T) {
	for _, windows := range []bool{false, true} {
		dir := t.TempDir()
		exe := filepath.Join(dir, "tdh3")
		if windows {
			exe = filepath.Join(dir, "tdh3.exe")
		}
		os.WriteFile(exe, []byte("old"), 0o755)
		if err := replaceExe(exe, []byte("new"), windows); err != nil {
			t.Fatal(err)
		}
		if b, _ := os.ReadFile(exe); string(b) != "new" {
			t.Errorf("windows=%v: exe holds %q", windows, b)
		}
		if fi, _ := os.Stat(exe); runtime.GOOS != "windows" && fi.Mode().Perm() != 0o755 {
			t.Errorf("windows=%v: mode %v", windows, fi.Mode())
		}
		entries, _ := os.ReadDir(dir)
		var names []string
		for _, e := range entries {
			names = append(names, e.Name())
		}
		want := []string{filepath.Base(exe)}
		if windows {
			want = []string{"tdh3.exe", "tdh3.old.exe"}
			if b, _ := os.ReadFile(oldExe(exe)); string(b) != "old" {
				t.Errorf("old exe holds %q", b)
			}
		}
		if strings.Join(names, " ") != strings.Join(want, " ") {
			t.Errorf("windows=%v: folder has %v, want %v", windows, names, want)
		}
	}
}

func TestFetchUnpacksTarGz(t *testing.T) {
	want := []byte("flasher program")
	var b bytes.Buffer
	gz := gzip.NewWriter(&b)
	tw := tar.NewWriter(gz)
	must(t, tw.WriteHeader(&tar.Header{Name: "tdh3", Mode: 0o755, Size: int64(len(want))}))
	_, err := tw.Write(want)
	must(t, err)
	must(t, tw.Close())
	must(t, gz.Close())
	files, pub, _ := signedFiles(t, "tdh3-linux-x64.tar.gz", b.Bytes())
	got, err := fetch(t, files, "tdh3-linux-x64.tar.gz", pub)
	must(t, err)
	if !bytes.Equal(got, want) {
		t.Errorf("got %q", got)
	}
}
