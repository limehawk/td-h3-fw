// sign writes FILE.sig, the ed25519 signature of FILE (base64, one line), with the key in
// TDH3_SIGNING_KEY (the base64 32-byte seed). tools/dist.sh signs SHA256SUMS with it, and
// `tdh3 update` checks that signature against the public key built into it (update.go).
//
//	TDH3_SIGNING_KEY=... go run tools/sign/main.go dist/SHA256SUMS
package main

import (
	"crypto/ed25519"
	"encoding/base64"
	"fmt"
	"os"
	"strings"
)

func main() {
	if len(os.Args) != 2 {
		fail("usage: sign FILE")
	}
	seed, err := base64.StdEncoding.DecodeString(strings.TrimSpace(os.Getenv("TDH3_SIGNING_KEY")))
	if err != nil || len(seed) != ed25519.SeedSize {
		fail("TDH3_SIGNING_KEY is not a base64 ed25519 seed")
	}
	msg, err := os.ReadFile(os.Args[1])
	if err != nil {
		fail(err.Error())
	}
	key := ed25519.NewKeyFromSeed(seed)
	sig := base64.StdEncoding.EncodeToString(ed25519.Sign(key, msg))
	if err := os.WriteFile(os.Args[1]+".sig", []byte(sig+"\n"), 0o644); err != nil {
		fail(err.Error())
	}
	fmt.Printf("signed %s with key %s\n", os.Args[1], base64.StdEncoding.EncodeToString(key.Public().(ed25519.PublicKey)))
}

func fail(msg string) {
	fmt.Fprintln(os.Stderr, "sign:", msg)
	os.Exit(1)
}
