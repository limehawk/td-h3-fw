package main

// tdh3 remote record / replay: save what the radio's screen mirror sends, with the time each
// piece came, and play it back through the same screen model as tdh3 remote screen.
//
// A recording file is "TDH3REC1\n" and then chunks, each:
//
//	4 bytes  milliseconds since the recording started (big-endian)
//	4 bytes  length n (big-endian)
//	n bytes  exactly what the radio sent in that time (fw/remote.c's records)
//
// The last chunk is empty (n = 0): its time is when the recording stopped.

import (
	"bytes"
	"context"
	"encoding/binary"
	"errors"
	"fmt"
	"image"
	"image/color"
	"image/color/palette"
	"image/gif"
	"io"
	"os"
	"os/signal"
	"time"

	"github.com/spf13/cobra"
)

const recMagic = "TDH3REC1\n"

type chunk struct {
	t time.Duration
	b []byte
}

func writeChunk(w io.Writer, t time.Duration, b []byte) error {
	var h [8]byte
	binary.BigEndian.PutUint32(h[:], uint32(t.Milliseconds()))
	binary.BigEndian.PutUint32(h[4:], uint32(len(b)))
	_, err := w.Write(append(h[:], b...))
	return err
}

func readRecording(b []byte) ([]chunk, error) {
	if !bytes.HasPrefix(b, []byte(recMagic)) {
		return nil, errors.New("not a tdh3 remote recording")
	}
	b = b[len(recMagic):]
	var cs []chunk
	for len(b) >= 8 {
		n := int(binary.BigEndian.Uint32(b[4:]))
		if len(b) < 8+n {
			break // cut short (the computer stopped mid-write): keep what's whole
		}
		cs = append(cs, chunk{time.Duration(binary.BigEndian.Uint32(b)) * time.Millisecond, b[8 : 8+n]})
		b = b[8+n:]
	}
	return cs, nil
}

// record saves everything the radio sends until ctx ends. 'S' asks for a full redraw once; a 'Q'
// a second keeps the radio's 10 s mirror window open and records the status line.
func record(ctx context.Context, p port, w io.Writer) (time.Duration, int, error) {
	if _, err := w.Write([]byte(recMagic)); err != nil {
		return 0, 0, err
	}
	readN(p, 1<<16, 100*time.Millisecond) // drop what openRemote's probe left mid-flight
	start, total, q := time.Now(), 0, time.Now()
	p.Write([]byte("S"))
	var head []byte // the first bytes, until the sync shows the radio answered
	for answered := false; ctx.Err() == nil; {
		if time.Since(q) >= time.Second {
			p.Write([]byte("Q"))
			q = time.Now()
		}
		b := readN(p, 1<<16, 50*time.Millisecond)
		if !answered {
			head = append(head, b...)
			if answered = bytes.Contains(head, sync); !answered && time.Since(start) > 2*time.Second {
				return 0, 0, errors.New("no answer from the radio: on the radio set MENU, REMOTE to ON")
			}
		}
		if len(b) == 0 {
			continue
		}
		if err := writeChunk(w, time.Since(start), b); err != nil {
			return 0, 0, err
		}
		total += len(b)
	}
	d := time.Since(start)
	return d, total, writeChunk(w, d, nil)
}

type shot struct {
	t  time.Duration
	px [128][128]uint16
}

// replay runs the chunks through one screen and calls show after every chunk that changed it.
// A byte lost on the cable loses track of the stream: it picks up again at the next sync.
func replay(cs []chunk, show func(t time.Duration, sc *screen)) *screen {
	sc, synced := &screen{}, false
	var pend []byte
	for _, c := range cs {
		pend = append(pend, c.b...)
		before, text, query := sc.px, sc.Text(), sc.query
		for len(pend) > 0 {
			if !synced {
				i := bytes.Index(pend, sync)
				if i < 0 {
					pend = pend[max(0, len(pend)-len(sync)+1):]
					break
				}
				pend, synced = pend[i:], true
			}
			n, err := sc.feed(pend)
			pend = pend[n:]
			if err == nil {
				break // the rest is a record still coming
			}
			pend, synced = pend[1:], false
		}
		if sc.px != before || sc.Text() != text || sc.query != query {
			show(c.t, sc)
		}
	}
	return sc
}

// toGIF turns frames into an animated GIF at PNG's 4x, each frame shown until the next one
// came; the last until end. The panel's colours, if they fit in 256; else the nearest of Plan 9's.
func toGIF(frames []shot, end time.Duration) *gif.GIF {
	const k = 4
	seen := map[uint16]bool{}
	var pal color.Palette
	for _, f := range frames {
		for _, row := range f.px {
			for _, v := range row {
				if !seen[v] && len(seen) <= 256 {
					seen[v] = true
					pal = append(pal, rgb(v))
				}
			}
		}
	}
	if len(seen) > 256 {
		pal = palette.Plan9
	}
	g := &gif.GIF{}
	for i, f := range frames {
		img := image.NewPaletted(image.Rect(0, 0, 128*k, 128*k), pal)
		idx := map[uint16]uint8{}
		for y := range 128 {
			for x := range 128 {
				v := f.px[y][x]
				c, ok := idx[v]
				if !ok {
					c = uint8(pal.Index(rgb(v)))
					idx[v] = c
				}
				for j := range k {
					o := img.PixOffset(x*k, y*k+j)
					for n := range k {
						img.Pix[o+n] = c
					}
				}
			}
		}
		next := end
		if i+1 < len(frames) {
			next = frames[i+1].t
		}
		g.Image = append(g.Image, img)
		g.Delay = append(g.Delay, max(2, int((next-f.t)/(10*time.Millisecond)))) // browsers slow < 2
	}
	return g
}

func recordCmds(port *string) []*cobra.Command {
	rec := &cobra.Command{
		Use:   "record FILE",
		Short: "Save everything the screen shows, as it happens, into FILE until Ctrl-C",
		Long: "Records the radio's screen with the time of every change, for tdh3 remote replay. Use the\n" +
			"radio as usual while it records; nothing is pressed for you.",
		Args: cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			p, err := openRemote(*port)
			if err != nil {
				return err
			}
			defer p.Close()
			f, err := os.Create(args[0])
			if err != nil {
				return err
			}
			defer f.Close()
			ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt)
			defer stop()
			logger.Info("recording the screen", "file", args[0], "stop", "Ctrl-C")
			d, n, err := record(ctx, p, f)
			if err != nil {
				return err
			}
			logger.Info("saved", "file", args[0], "seconds", fmt.Sprintf("%.1f", d.Seconds()), "bytes", n)
			return f.Close()
		},
	}
	var fast bool
	var out string
	rep := &cobra.Command{
		Use:   "replay FILE",
		Short: "Play a recording back as text, at the speed it happened (-o movie.gif makes a GIF)",
		Args:  cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			b, err := os.ReadFile(args[0])
			if err != nil {
				return err
			}
			cs, err := readRecording(b)
			if err != nil {
				return fmt.Errorf("%s: %w", args[0], err)
			}
			var frames []shot
			start := time.Now()
			sc := replay(cs, func(t time.Duration, sc *screen) {
				if out != "" {
					frames = append(frames, shot{t, sc.px})
				}
				if !fast {
					time.Sleep(time.Until(start.Add(t)))
				}
				fmt.Println(noteS.Render(fmt.Sprintf("--- %.1fs", t.Seconds())))
				fmt.Print(sc.Text())
				if sc.query != "" {
					fmt.Println(status(sc.query))
				}
			})
			if len(sc.texts) == 0 && sc.query == "" {
				return fmt.Errorf("%s: no screen in it", args[0])
			}
			if out == "" {
				return nil
			}
			f, err := os.Create(out)
			if err != nil {
				return err
			}
			defer f.Close()
			if err := gif.EncodeAll(f, toGIF(frames, cs[len(cs)-1].t)); err != nil {
				return err
			}
			logger.Info("saved", "file", out, "frames", len(frames))
			return f.Close()
		},
	}
	rep.Flags().BoolVar(&fast, "fast", false, "don't wait: play it all at once")
	rep.Flags().StringVarP(&out, "output", "o", "", "also write the recording to this animated GIF")
	return []*cobra.Command{rec, rep}
}
