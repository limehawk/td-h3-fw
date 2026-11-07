//go:build linux || darwin

package main

import "golang.org/x/sys/unix"

// keysMode stops the terminal on fd echoing, buffering lines and turning keys into signals; output
// is left alone, so plain lines still start at the left. restore puts it back as it was.
func keysMode(fd int) (restore func(), err error) {
	old, err := unix.IoctlGetTermios(fd, getTermios)
	if err != nil {
		return nil, err
	}
	t := *old
	t.Lflag &^= unix.ICANON | unix.ECHO | unix.ISIG
	t.Cc[unix.VMIN], t.Cc[unix.VTIME] = 1, 0
	if err := unix.IoctlSetTermios(fd, setTermios, &t); err != nil {
		return nil, err
	}
	return func() { unix.IoctlSetTermios(fd, setTermios, old) }, nil
}
