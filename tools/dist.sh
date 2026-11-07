#!/bin/sh
# Release files for tag $1 (v0.60.0) into dist/: both images, the tdh3 flasher for each computer
# (with both images inside it; the Macs' zipped), SHA256SUMS. Both forges' release jobs run it after building with `make -C fw all rxonly`.
# The images must say exactly the tag's version (0.5.1, never 0.5.1-2 or 0.5.1+), in
# build/version and as the NUL-ended VERSION string the radio and the flasher show.
set -e
ver=${1#v}
[ "$(cat fw/build/version)" = "$ver" ] || { echo "build reads $(cat fw/build/version), tag is $ver"; exit 1; }
for f in fw/build/td-h3.bin fw/build/td-h3-rx.bin; do
  python3 -c "import sys; sys.exit(b'\0' + sys.argv[2].encode() + b'\0' not in open(sys.argv[1], 'rb').read())" $f "$ver" \
    || { echo "$f has no VERSION string $ver"; exit 1; }
done
rm -rf dist && mkdir dist
cp fw/build/td-h3.bin dist/td-h3-fw-$ver.bin
cp fw/build/td-h3-rx.bin dist/td-h3-fw-$ver-rx.bin
# Each flasher carries both images (-tags release, builtin_release.go): one download flashes a radio.
mkdir -p tools/tdh3/builtin
cp fw/build/td-h3.bin fw/build/td-h3-rx.bin tools/tdh3/builtin/
for t in windows/amd64/windows.exe darwin/arm64/macos-apple darwin/amd64/macos-intel linux/amd64/linux-x64 linux/arm64/linux-arm64; do
  set -- $(echo $t | tr / ' ')
  (cd tools/tdh3 && CGO_ENABLED=0 GOOS=$1 GOARCH=$2 go build -tags release -trimpath -ldflags=-s -o ../../dist/tdh3-$3 .)
done
# A Mac drops the run permission of a file downloaded on its own, not of one unzipped from a zip.
for m in macos-apple macos-intel; do
  python3 -c "import sys, zipfile; z = zipfile.ZipFile(sys.argv[1], 'w', zipfile.ZIP_DEFLATED); i = zipfile.ZipInfo('tdh3-' + sys.argv[2]); i.external_attr = 0o100755 << 16; i.compress_type = zipfile.ZIP_DEFLATED; z.writestr(i, open(sys.argv[3], 'rb').read()); z.close()" \
    dist/tdh3-$m.zip $m dist/tdh3-$m
  rm dist/tdh3-$m
done
# LICENSES.txt: the notices the firmware's and the flasher's licences ask to travel with binaries:
# this project's MIT licence, NOTICE and the Apache-2.0 text (fw/bk4819.c), the fonts', and the
# licence files of every Go module linked into the flasher.
{
  echo "td-h3-fw $ver: licences and notices for the firmware and the tdh3 flasher"
  for f in LICENSE NOTICE LICENSE-APACHE docs/FONTS.md; do printf '\n\n===== %s =====\n\n' "$f"; cat "$f"; done
  (cd tools/tdh3 && go list -deps -f '{{with .Module}}{{if not .Main}}{{.Path}} {{.Dir}}{{end}}{{end}}' . | sort -u) |
  while read -r mod dir; do
    for f in "$dir"/LICENSE* "$dir"/LICENCE* "$dir"/COPYING* "$dir"/NOTICE*; do
      if [ -f "$f" ]; then printf '\n\n===== %s (%s) =====\n\n' "$mod" "${f##*/}"; cat "$f"; fi
    done
  done
} > dist/LICENSES.txt
cd dist && sha256sum * > SHA256SUMS && cat SHA256SUMS
