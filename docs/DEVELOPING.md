# Developing

How to build, emulate, test and reverse-engineer this firmware. Hardware facts live in
[HARDWARE.md](HARDWARE.md); this file is about the workflow.

## Layout

| Path | What |
|---|---|
| `fw/` | the firmware (SDCC C), `Makefile`, `test_fw.py` |
| `fw/asm/` | hand-written assembly of the biggest functions, one per file (Assembly) |
| `tools/asmdiff.py` | calls each asm function and its C original with the same inputs and compares (Assembly) |
| `tools/emu51.py` | 8051 core plus TD-H3 peripheral models |
| `tools/dis51.py` | disassembler with CMS8S6990 SFR names |
| `tools/stockcalls.py` | constant arguments at every call of a stock function |
| `tools/schpins.py` | chip pin to net map from the schematic PDF |
| `tools/budget.py` | flash, XDATA, stack and settings left, priced by scarcity (Hardware budget) |
| `tools/tdh3/` | Go CLI: EEPROM backup, firmware flash, CHIRP channel import |
| `stock/` | TIDRADIO images and updaters, see `stock/README.md` |
| `backups/` | EEPROM dumps from your radio (git-ignored, never overwrite) |

## Screen layout

Screens are laid out for the panel's exact 128 x 128 pixels, with no rows or columns left over:

- The bottom line sits at `BOT_Y` (`fw/fw.h`, row 120): a line of text is 7 glyph rows and a blank
  row, so it ends on the last row, 127. Lines above it step up from there (`BOT_Y - 12`, `- 24`).
- Text runs the full width: 21 characters at x 0 fill 126 px, each glyph's blank column on its right.
- Gaps between the blocks of a screen are spread evenly; the home screen's layout comment above
  `METER_Y` in `fw/main.c` lists its rows. A new screen or line takes its rows from that budget,
  not from wherever the last line happened to end.
- The boot screen is the exception: `tools/tdh3` redraws the same pixels, so it moves only with the
  flasher.

## Build

SDCC 4.5.0 is unpacked in `.toolchain/` (git-ignored); the Makefile finds it.

```bash
cd fw && make && make rxonly
```

- `build/td-h3.bin` transmits only on the bands in `radio.c` (`tx_allowed`).
- `build/td-h3-rx.bin` (`-DNO_TX`) can never transmit. Flash this one first after big changes.

### Changes and releases

**Every change goes through a PR**: a branch, pushed, a PR to main, CI green, then merged. Nobody
pushes to main directly (the forge refuses it). CI runs once per push to the PR; main isn't run
again after the merge, because the PR tested that commit.

**A release is a tag on main**, made when there is something worth putting on radios, not after
every merge:

```bash
git switch main && git pull
make -C fw bump BUMP=minor     # minor: a new feature; patch (default): fixes;
git push --follow-tags         # major: a change that resets settings or the channel layout
```

The tag runs CI once more and then the release job (CI, below). `make bump` refuses anything but
main as it is on the forge.

The radio shows the version on the splash and in MENU → VERSION. A build from a tagged commit reads
`0.5.1`; one past the tag reads `0.5.1-2` (2 commits on); a trailing `+` means uncommitted changes in
`fw/`. The Makefile reads `git describe`; there is no version to edit in the source. `make -C fw
bump` rebuilds both images after tagging, so the image on disk always says the tag. `tdh3 flash` and
`tdh3 firmware flash` refuse a `-N` or `+` build: a test build between releases needs `--force`.

The build fails rather than produce something dangerous:

- over 63,488 bytes (0xF800), the image would program into the bootloader's sector;
- any initialized XDATA (`l_XINIT` non-zero in `fw.map`), see SDCC traps below.

## Test

```bash
cd fw && make test                 # everything, ~13 s under PyPy
cd fw && make test T="scan fm"     # only tests whose names contain these words
```

`make test` uses PyPy when uv has it (`uv python install pypy3.11`, user-level, no system
packages) and plain `python3` otherwise (about 2.5 minutes). `test_fw.py` boots the real `.bin` in the emulator with a fixture EEPROM
(factory cal, channels 1-4 with scan groups) and checks what reaches the pins and buses. The
first block runs in order on one booted CPU (boot compared live with stock, RX path, TX band
plan, clone protocol); `INDEPENDENT` tests each get a fresh boot and run in a process pool.

Run a few independent tests by name:

```bash
cd fw && python3 -c "
import sys, multiprocessing, test_fw as t
items = [i for i in t.INDEPENDENT if 'scope' in i[0] or 'lock' in i[0]]
print('\n'.join(multiprocessing.Pool(len(items)).map(t.one, items)))"
```

Helpers: `press(cpu, key)` (short, fires on release), `longpress`, `keys(cpu, [...])`,
`menu(cpu, 'SCOPE', ['UP'])` (MENU list order must match `main.c`), `freq(cpu)` (BK4819
`REG_39:38`, 10 Hz), `pins(cpu)` (RF path enables and APC duty).

## Hardware budget

`tools/budget.py` decides RAM, flash, stack and settings trade-offs, so nobody has to say "we have
RAM spare". The full `make test` prints it after `all passed`; `make -C fw budget` prints it alone
(its stack figure is the last full test's).

```
resource    used  limit   free reserve   price/B  verdict note
flash      60815  63488   2673    2048   0.00160  tight   RX build 59909
xdata       1015   1280    265      48   0.00461  plenty  RX build 1011
stack        163    240     77      24   0.01887  plenty  peak 0xA3; DATA ends and the stack starts at 0x55
settings      35     48     13       4   0.11111  plenty  +8 spare bits; the only EEPROM block we own
pressure 9.317 (lower is better; a trade is worth it when it lowers this)
```

- **flash**: the image size against 0xF800. **xdata**: `EXTERNAL RAM` in `fw.mem`. Both are the
  full build's; the RX-only build is a subset, shown in the note.
- **stack**: the deepest SP over the whole suite against test_fw's 0xF0. Internal RAM is DATA
  (registers, `__data` variables, overlays) and then the stack, so a byte more DATA is a byte less
  stack: this row covers both.
- **settings**: the `free_*` bytes of `cfg_t` in `fw/main.c` (parsed, not hand-counted), out of
  the 48-byte block at 0x1FC0; spare bitfield bits are in the note. That block is the only EEPROM
  this firmware owns (the rest is stock's map, HARDWARE.md), so it is the EEPROM budget.

**The policy** is the `RESERVE` table at the top of `tools/budget.py`, the only place it lives:

| Resource | Reserve | Why |
|---|---|---|
| flash | 2 KB | one mid-size feature (FM was ~1.5 KB) or a late fix without cutting something first |
| xdata | 48 B | one new buffer, or a function's plain (non-reentrant) locals |
| stack | 24 B | about three more nested calls with locals, on top of the 16 B 0xF0 already keeps for untested paths |
| settings | 4 B | a few settings before the block needs a new layout, which is a major version |

**Verdict** per resource: *over* below its reserve (the budget, and so `make test`, fails and names
it); *tight* under twice the reserve; *watch* under three times; *plenty* above.

**Price.** A byte of a resource costs `1 / (free - reserve)`: the share of its spendable headroom it
uses. The same byte is cheap where there is lots left and dear near the reserve, so bytes of
different resources can be added up. The *pressure* is the sum over resources of
`ln(spendable / headroom)`, whose slope is that price: 0 with everything free, rising without limit
at the reserves.

**Judging a trade.** A change is worth it when it lowers the pressure; for a small change that is
when `sum(price x bytes it adds)` is negative. Example at the numbers above: saving 60 B of XDATA for
100 B more flash costs `-60/217 + 100/625 = -0.12`, so it is worth it; saving 10 B of stack for
300 B more flash costs `-10/53 + 300/625 = +0.29`, not worth it while flash is tight. For a branch,
measure instead of estimating:

```bash
cd fw && make test                            # this tree's numbers, stack included
python3 ../tools/budget.py --compare main     # builds and tests main in fw/build/compare
```

`--compare` builds and tests the rev itself (about a minute), then prints each resource's delta,
its change in pressure and its verdict before and after, and `better` or `worse` overall, e.g.
flash -451, XDATA -15, stack +0: better. The comparison worktree stays in `fw/build/compare` for the next run.
Change a reserve only with a reason, written in the table above and next to the number.

### Size work so far

v0.54.0's squeeze took 9.4 KB out with no change on the screen: `__bit` flags, strings read from code
space, palette indices instead of 16-bit colours, 8-bit maths and no library divides, and own
`xcpy`/`xset`. v0.54.6 moved the most-used bytes into DATA and drove more of the menu from its table
(-556 B). Each commit message names its saving. What's left is mostly calls to the reentrant drawing
routines; the next real step is hand-written assembly for the biggest functions, after the features.

Hand-written assembly (Assembly, below): linked at ASM=1, the default since v0.59.2.
SDCC to asm bytes, each with its strings and private helpers:

| Function | C | asm | Image |
|---|---|---|---|
| lcd_text_bg | 559 | 310 | -249 |
| lcd_icon | 506 | 305 | -201 |
| lcd_fill | 181 | 122 | -59 |
| draw_status | 1,236 + strings | 832 | -542 |
| draw_meter (+ m_fill, m_text, bar_x) | 1,408 | 896 | -524 |
| draw_slot | 2,326 + strings | 1,521 | -847 |

Image: each one's saving, from a build with only it in C (v0.58.0). asmlib, which the screens share, is
about 145 B, paid once: all of them together save 2,277 B.

Measured, and bigger, so don't redo them without a new reason (a new SDCC, say):

| Tried | Result | Why |
|---|---|---|
| One list engine for DELETE CH, CH GROUPS, LAST HEARD and the group picker | +77 B | the skeleton (`list_open`, `list_key`, `list_row`) is already shared; SDCC's calls through a function pointer cost more than the copies they replace; CH NAME is a text editor, not a list |
| `draw_status` not reentrant | +121 B | its locals move to XDATA, and every access gets longer |
| `draw_status` locals as XDATA statics | +161 B | the same |
| The battery gauge from a table | +37 B | four fills are shorter than the table and its loop |
| SDCC flags on v0.55.1: `--max-allocs-per-node` 1000-200000, `--peep-return`, `--peep-asm`, `--allow-unsafe-read`, `--nooverlay` | +0 B, every one | the 8051 port doesn't use them for size here; `--nogcse` (+2189) and `--nolabelopt` (+640) only grow it |
| More variables in DATA past v0.54.6's | not worth it | a byte of DATA is a byte of stack (about 65 B of flash by the prices above); `mirror` and `lh_n` must stay in XDATA, which survives the flash-restart reset |

Also tried and reverted: a squelch more sensitive than stock's (RSSI open halved, noise open doubled).
SQ3 opened on hiss on empty channels, and even SQ9 let some through (v0.51.2); keep stock's tables.
Considered and not wanted: receive audio profiles and a louder receive gain.

### Emulator timing

Not cycle-accurate. Timer0 fires every `TICK` = 4000 instructions, so 1 emulated tick (5 ms)
is 4000 instructions, about 25× fewer than the real 48 MHz part runs in 5 ms. Two consequences:

- Anything tick-based (`delay_ms`, key repeat and long-press, timeouts) runs at the right
  emulated time. Busy-wait code (`udelay`, bit-banged buses, LCD fills) runs ~25× slow in
  emulated time. A scope sweep point costs ~108K instructions (27 ticks) here, ~2.5 ms on
  the radio.
- Key presses are in instructions: `press` holds 250K (62 ticks), `longpress` 1.4M. Key
  debounce is time-based (stable for 2 ticks) so it works whatever the caller's poll rate.

### What the emulator models

| Model | Hooks for tests |
|---|---|
| BK4819 3-wire bus (P5.2-P5.4) | `cpu.bk.writes` / `reads`, `regs`; optional callbacks `dbm(f)` (RSSI), `busy(f)` (`REG_0C` bit 1, squelch open), `ctcss(f)` (`REG_68`), `counter()` (frequency counter `REG_0D/0E` when `REG_32=0245`), `alert(f)` (1050 Hz) |
| AT24C64 on bit-banged I2C | `cpu.ee.mem`, `cpu.ee.writes` |
| RDA5807M I2C | `cpu.fm.txns` (sniffer only) |
| LCD over SPI | `cpu.lcd` = list of `(is_data, byte)` |
| Keypad matrix and side keys | `cpu.key = 'UP'` / `None` |
| ADC with interrupt | `cpu.adc` (raw battery count) |
| UART1 (USB-C) / UART0 (Bluetooth) | `cpu.uart_in` / `uart_out`, `cpu.uart0_in` / `uart0_out` (bytes are spaced out like a real line) |
| Timer0, Timer1, watchdog | `cpu.wdt_kicks`, `cpu.t1_count` (beep) |

It raises on any write to the flash controller SFRs (0xFB-0xFF), and with `cpu.strict_ri` on
any `MOVX @Ri`.

### Running stock in the emulator

Stock 250317 runs too, which is the fastest way to answer "what does stock write when X":

```python
# cd fw && python3
from test_fw import CPU, STOCK, eeprom
st = CPU(open(STOCK, 'rb').read(), eeprom())
st.code[0xEAB6] = 0x22      # RET: skip stock's busy-wait ms delay (timing only)
st.run(15_000_000)
st.key = 'MENU'; st.run(300_000); st.key = None; st.run(2_000_000)
print([(hex(r), hex(v)) for r, v in st.bk.writes[-20:]])
```

Stock needs the ADC interrupt modelled (it waits for it at boot) and stalls in its delay
loop without the `0xEAB6` patch. `test_boot_matches_stock` compares our LCD and BK4819 init
against stock's this way on every run.

## CI

Every push to a PR, and every `v*` release tag, runs `.forgejo/workflows/ci.yml` once on the
Forgejo runner (label `docker`); a newer push to the same PR cancels the older run.
A push to main isn't run: main takes changes only through a green PR.
A PR to main merges only with all three jobs green and its branch up to date with main. The emulator suite runs 4 workers there
(`TEST_JOBS=4`: each PyPy worker takes a few hundred MB); locally it uses one per core.

| Job | Runs | Fails when |
|---|---|---|
| `firmware` | `make -C fw all rxonly`, then `make -C fw test`, then `make -C fw cref` and `tools/asmdiff.py` | SDCC prints any warning (`--Werror`: an undeclared function or a constant that doesn't fit stops the build); an image reaches 0xF800 or has initialized XDATA; any test fails (no `all passed`); any resource is under its reserve (`budget: ... below reserve`); an asm function does anything its C original doesn't (`<fn> case <n> differs in ...`) |
| `cli` | `go vet ./... && go test ./...` in `tools/tdh3` | a vet warning or a failing Go test |
| `secrets` | TruffleHog over the whole history, the same two scans as the git hooks | a verified or unknown secret, or a plaintext `*_TOKEN`/`*_SECRET`/... value (`.forgejo/trufflehog.yaml`) |

Tool versions: go, uv and trufflehog in `mise.toml`; SDCC 4.5.0 (with its sha256) and PyPy 3.11.17
in the workflow, each with why and when to recheck. The stock 250317 image is fetched from the URL in
`stock/README.md` and checked against the sha256s there, so the stock comparison runs in CI too.
SDCC, PyPy and the stock image are cached; a change to `ci.yml` fetches them again.

**Reading a failure.** Open the PR's checks (or the repo's Actions tab), the red job, then the red
step; the end of its log says why. For `firmware`, the step name says which: the build step prints
the image size or the XDATA error; the test step prints `FAIL <test>` lines and then the budget
table, where the resource under its reserve is named. Reproduce it with the same command locally.
A failure in a fetch step (SDCC, PyPy, stock) is the download or its checksum, not your change:
re-run it, and if a checksum really changed, find out why before changing the pin.

**Re-running.** In the run's page, *Re-run all jobs* (or one job). Or push again. Or, with no new
commit, Actions → CI → *Run workflow* on the branch (`workflow_dispatch`).

**Releases.** On a `v*` tag (`make -C fw bump`, `git push --follow-tags`) the `release` job runs
once the other three pass: it builds both images, and `tools/dist.sh` fails unless they say exactly
the tag's version (`0.5.1`, not `0.5.1-2` or `0.5.1+`), builds `tdh3` for Windows, macOS and Linux,
and puts all of it with `SHA256SUMS` in `dist/`, signed into `SHA256SUMS.sig` by `tools/sign` with the
`TDH3_SIGNING_KEY` secret (both release jobs fail without it; `tdh3 update` checks the signature
against the public key in `tools/tdh3/update.go`). The job attaches that to the Forgejo release for the
tag (made if missing; a re-run replaces the files), as the account behind `RELEASE_TOKEN`. The
forge's push mirror carries main and the tags to GitHub, where `.github/workflows/release.yml` runs
the same build and `tools/dist.sh` and publishes the GitHub release. The release text is `.forgejo/release-notes.md`,
written for people flashing their radio. A tag with a `-` makes a pre-release. To redo one, re-run
its job.

## Reverse engineering stock

```bash
python3 tools/dis51.py stock/td-h3/250317/H3_250317.BIN > /tmp/h3.dis
python3 tools/stockcalls.py /tmp/h3.dis 0xE74F     # every BK4819 write: R7=reg R5=hi R3=lo
```

`stockcalls.py` replays the simple moves before each call (Keil passes byte arguments in R7,
R5, R3); computed values print `?`, then read the listing around that address. Register
sequences (init, RX, TX, squelch, FM) in this firmware were read this way and then checked in
the emulator against stock's live traffic.

Stock addresses worth knowing (code addresses in `H3_250317.BIN`):

| Address | What |
|---|---|
| 0xE4FC | port directions and initial values |
| 0xEF28, 0xF040 | watchdog setup and kick |
| 0xEE1B | Timer0 5 ms tick |
| 0xEAB6 | busy-wait ms delay (patch to RET in the emulator) |
| 0xE74F / 0xC8FC | BK4819 write `(reg, hi, lo)` / read |
| 0x9F8F, table 0x6950 | BK4819 init |
| 0x8555 / 0x7EB1 | RX / TX enable sequences |
| 0x6A05-0x6B1C | squelch tables (REG_78 / REG_4F per level, VHF/UHF, wide/narrow) |
| 0x69BB | mic gain table |
| 0xDB67 | 300 MHz VHF/UHF split |
| 0x5443-0x5536 | TX power from the calibration block |
| 0xC8E3, 0xC7AA | calibration-valid check (0x1FF0 = 0x85), blank defaults |
| 0x6EE2 | side key mapping (SK1 0x1A, SK2 0x12) |
| 0x7811, 0xE162 | LCD init, window (column offset 32) |
| 0xCF2E, 0xE7DD, 0xEAEE, table 0xDA05 | RDA5807 init, tune, mute |
| 0xDBAD / 0xDE80, 0xED88 | UART1 / UART0 + Bluetooth enable |
| 0xE4A4 | ADC setup |
| 0xD32F | EEPROM read (R3 = count) |

The bootloader (`h3bootloader.bin`, TIDRADIO's, as nicsure read it off a radio; not in this repo) disassembles the same way; its flash routine is
at 0x0042-0x0262.

### Schematic

The schematic (`TD-H3 sch.pdf` from nicsure's TD-H3-Engineering, not in this repo) is vector, so nets can be read programmatically:

```bash
python3 -m venv /tmp/v && /tmp/v/bin/pip install pymupdf
/tmp/v/bin/python tools/schpins.py "TD-H3 sch.pdf" 2 935,455,1180,700   # MCU U2
pdftotext -f 2 -l 2 -bbox "TD-H3 sch.pdf" - | grep -i bk4819             # find a part's box
pdftoppm -f 2 -l 2 -r 288 -png "TD-H3 sch.pdf" p2                         # check by eye
```

The HARDWARE.md pin table came from `schpins.py` and was then checked against stock's port
init values. Crop the rendered page to read a region by eye.

## SDCC traps

- **No initialized XDATA.** SDCC's startup copies it with `MOVX @R0` and P2 paging, which is
  unverified on this part; on the first build it left `menu` at 0. Assign at runtime instead.
  The Makefile refuses a build with XINIT.
- **ISR prototypes must be visible in the file with `main()`**, or SDCC drops the vectors.
  They're in `fw.h`.
- **DSEG overflow** ("Could not get N consecutive bytes"): SDCC puts locals and compiler
  temporaries in fixed internal RAM. Mark big functions `__reentrant` (locals on the stack)
  and move buffers to `static __xdata`.
- **Every local and parameter of a plain function has its own fixed XDATA address** (large
  model, no overlaying), so XDATA fills with locals of functions that never run together. A
  `__reentrant` function keeps them on the stack instead. For small helpers that is usually less
  code too (arguments are pushed instead of stored one by one into XDATA): marking about 90 of them
  freed 273 bytes of RAM and 1.2 KB of code. For a big function it costs code (`fm_mode`: +360 bytes for 12),
  so measure each one: mark it, `make`, compare `EXTERNAL RAM` in `build/td-h3/fw.mem` and the
  image size. `uart_get` stays plain: reentrant, the Bluetooth PC LINK test fails. The emulator
  records the deepest the stack goes; `make test` fails any test that takes it past 0xF0 and prints
  the peak (`stack peak 0xA3 of 0xFF`); the Hardware budget wants 24 B more below that.
- **The screen buffer (`scr_buf`, fw.h) is shared**: the full-screen modes, the FM screen's text,
  and outside the modes radio.c's channel bitmaps and record. A new full-screen mode clears
  `maps_ok` as it starts, and must not load or save a channel.
- **makebin** needs `-s 63488` or it complains the buffer is too small.
- **`--xram-size 1280`**: stock uses XDATA up to 0x04E4, so at least that much exists.
- **Watch 32-bit maths.** One extra `/` on a `uint32_t` costs ~200 bytes.

## Assembly

C is the source of truth. A few of the biggest functions also have a hand-written 8051 twin in
`fw/asm/<fn>.asm`, assembled by sdas8051. **`ASM=1` is the default** (since v0.59.2):
every image links the asm, and `make ASM=0` builds all C instead. The asm saves about 2.3 KB (table in
"Size work so far").

**How it fits together**

- Each asm function keeps its C original behind `#ifdef C_<FN>` ... `#endif` in its `.c` file.
  `tools/asmcheck.py` (the Makefile runs it) defines `C_<FN>` for every function built from C.
- Each `.asm` starts with `; asm-for: <file>.c <fn>` and `; c-sha256: <hash>`, the hash of that
  `#ifdef` block. When the C changes, the hash no longer matches: the asm is **stale**, and the
  ASM=1 build prints `asm stale: <fn>, using C` and compiles the C. A C change never breaks a build or
  blocks a feature; it only loses that function's saving until the asm is redone.
- No layout is hand-written in the asm: `.include "../build/asm.inc"` brings struct offsets, bitfield
  bits, screen constants (`W`, `P_RED`, `METER_Y`, ...) and hw.h's SFR and SBIT addresses. `asm.inc` is
  generated at build time by `tools/asmcheck.py inc`, which compiles main.c with SDCC plus a line per
  name in its `CONSTS` and `BITS` tables, so the values are SDCC's own. A new name goes in those tables.
- `fw/asm/asmlib.asm` (`; asm-for: none`) holds helpers the asm screens share: `icat` (append the
  string that follows the `lcall` to the XDATA text at DPTR), `nthcat`, and register-argument wrappers
  for `lcd_fillp`, `text1` and `lcd_icon`. It's linked whenever ASM=1.
- `make asm` builds `build/td-h3-asm.bin` (ASM=1), `make cref` `build/td-h3-c.bin` (all C), side by
  side. `CFUN="a b"` builds those from C even at ASM=1, for bisecting.

**Proving it.** `cd fw && make asm cref && python3 ../tools/asmdiff.py [fn] [-n 3000]` boots both
images to the main loop, then calls each asm function and its C original with the same random
inputs. It compares the LCD bytes, both UARTs (REMOTE included), BK4819 and I2C traffic, the return
value, the port pins, every named variable in internal RAM, XDATA and bit space, and the stack after
the return, and reports each side's depth below the call. `CASES` in the tool has a generator per
function; it sets variables by name (`'cfg+%d'` offsets via `CFG()`, from `asm.inc`), pointers as
`('&', var, offset)`, an ADC reading (`@adc`) or a BK4819 register (`@bk64`). Plant a mutation
(an off-by-one, a swapped branch) once to see the cases catch it.

**CI** (`.forgejo/workflows/ci.yml`) builds the all-C images, runs the whole suite on them, runs
asmdiff on every asm function, then the suite and the budget at ASM=1, what ships. `budget.py` measures
the ASM=1 image and adds one line: the ASM=1 saving and any stale functions.

**Change a function that has an asm twin:** change the C as usual. The asm goes stale and the build
falls back to C, which ships until the asm is redone. Then either leave it (the saving is gone, nothing
else) or redo the asm:

1. Read the C diff, change the `.asm` to match.
2. `python3 tools/asmcheck.py update <fn>` records the new C's hash.
3. `make -C fw asm cref`, then asmdiff on it (add cases for what changed), `make -C fw test`.
4. Commit the C and the asm together.

**Add an asm function:**

1. Pick one where SDCC's code is long for what it does (reentrant frames, many calls with constant
   arguments, pixel loops). Measure: a rewrite stays only if it saves 15% or more of the function.
2. Put the C behind `#ifdef C_<FN>` ... `#endif` with a prototype above it (callers keep compiling at
   ASM=1); what the asm must name loses `static`.
3. Write `fw/asm/<fn>.asm`: the `asm-for` header, `.module`, `.include "../build/asm.inc"`, `.globl`s,
   then the code with the C line it implements in a comment by each block. Add any new constant to
   `CONSTS`/`BITS` in `tools/asmcheck.py`.
4. `python3 tools/asmcheck.py update <fn>`, add its generator to asmdiff's `CASES`, prove it as above,
   and run `tools/budget.py --compare origin/main` for the commit message (always origin/main: a
   worktree's local `main` is often stale).

**Drop one:** delete `fw/asm/<fn>.asm` (and its `CASES` entry). The C behind its `#ifdef` builds from
then on; nothing else to undo. Remove the `#ifdef` lines too once nobody means to redo it.

**The default** is `ASM ?= 1` in fw/Makefile since v0.59.2 (flashed and live-checked first). A stale function is still C either way; `ASM ?= 0` turns all the asm off.

**Next candidates, and why they wait** (sizes of SDCC's C at v0.59.0).
The pass stopped after `draw_slot`: what's left big is code that features still change, and an asm
twin of a function that changes every week is stale almost at once, so it saves nothing for the work.

| Function | C | Why it waits |
|---|---|---|
| `main` | ~1,155 | the main loop: every feature adds a case or a timer to it |
| `menu_change`, `on_key` | ~931, ~873 | the menu and the keys: each new setting or key touches them |
| `fm_mode` | ~1,159 | until FM settles (presets, seek, the weather look) |
| `fill_ch`, `step_freq` | ~733, ~703 | channel and tuning rules still move (bands, USB, steps) |

Good next ones once a function has gone a few releases unchanged: `git log -L` on it says when.
Like the ones done, pick leaf-ish drawing and arithmetic before state machines, and keep the 15% rule.

**Rules.** Never touch SFRs 0xFB-0xFF, no initialized XDATA, images under 0xF800. The asm may use less
stack than its C, never more: asmdiff prints both depths and `make test ASM=1` the peak against the
budget's reserve. Settings stay byte-compatible: the asm reads `cfg` through `asm.inc`'s offsets.

**SDCC 4.5's calling convention** (`--model-large --fomit-frame-pointer`, as read from its output):

- **The first argument** goes in registers, by size: DPL; DPL DPH; DPL DPH B (a generic pointer:
  low, high, space 0x00 XDATA / 0x40 internal / 0x80 code); DPL DPH B A for 32 bits. Plain and
  `__reentrant` functions alike.
- **The rest, plain functions:** each in its own XDATA variable `_<fn>_PARM_<n>` (n from 2), which
  the function's own module defines. An asm function defines them itself (`.globl` in `XSEG`) and may
  lay them out in any order: callers only know the names. They're the function's to overwrite.
- **The rest, `__reentrant`:** pushed by the caller, last argument first, each low byte first, then
  the `lcall`. On entry SP points at the return address high byte, so the second argument's (first
  stacked) low byte is at SP-2, the next ones below it. The caller drops them (`dec sp` or
  `mov a,sp; add a,#-n; mov sp,a`). No `_bp`: with `--fomit-frame-pointer` SDCC addresses the frame
  from SP.
- **Return values** in the same registers as a first argument of that size; `__bit` in C.
- **Nothing is callee-saved.** A function may change R0-R7, A, B, DPTR and PSW's flags; the caller
  pushes what it needs over a call. Everything runs in register bank 0 (`ar0` = 0x00 .. `ar7` =
  0x07, as SDCC's own `push ar7`); only ISRs switch banks.
- **Library helpers** keep their own conventions: `__gptrget` reads the byte at the generic pointer in
  DPL DPH B into A and keeps R0-R7.
- **No MOVX @Ri** (the emulator raises with `strict_ri`): XDATA through DPTR only. Internal RAM
  through @R0/@R1 is fine: the asm keeps colours and counts on the stack and reads them that way.
- **Statics a function needs** must be visible to the linker: a `static` in a `.c` file can't be named
  from a `.asm`, so it loses its `static` (`font` and `window` in lcd.c; main.c's `cfg`, `buf` and the
  screen state). `__bit` variables are bit addresses (`jnb _scanning`), `__data` ones direct addresses.
- **Branch ranges:** sjmp/jz/djnz reach -128..+127 bytes; sdas8051 fails with "Branching Range
  Exceeded" rather than widening them. Move a far target near (an early-exit block next to the loop
  head), or use ljmp.

## Flashing

```bash
cd tools/tdh3 && go build && go test ./...
ls -l /dev/serial/by-id/          # the radio's CH340N; the UV-K5 cable has the same USB ID
./tdh3 -p /dev/serial/by-id/usb-1a86_USB_Serial-if00-port0 eeprom backup ../../backups/td-h3-eeprom-$(date +%F).bin
./tdh3 -p /dev/serial/by-id/usb-1a86_USB_Serial-if00-port0 firmware flash ../../fw/build/td-h3-rx.bin
```

For `eeprom` and `channels` the radio runs PC LINK (MENU → PC LINK; it stays open until V/M). For `firmware flash`, power on holding PTT: the bootloader streams `A5` and the screen
stays dark. Recovery from a bad image is the same: PTT at power-on, flash again; the
bootloader can't erase itself. Stock images are in `stock/` if you ever need to go back.

## Driving the real radio (REMOTE)

With MENU → REMOTE → ON, the radio mirrors every `lcd_fill`/`lcd_text` to USB-C at 1 Mbaud and
takes key presses (protocol at the top of `fw/remote.c`). PTT and PTT-B are refused in the
firmware and absent from the tool. `B` arms the flash restart that `tdh3 flash` uses: the radio
answers `S` A5 5A and switches to the bootloader's 115200 (tdh3 follows it), and the next physical PTT press restarts it into the vendor bootloader
(HARDWARE.md, Bootloader) before it can key up; 10 s with no byte from the computer disarms it, so tdh3 sends 0x80 (no key) every 2 s while it waits for PTT, with no time limit.
Don't send `B` from scripts that drive the radio.

```bash
tools/tdh3/tdh3 -p /dev/serial/by-id/usb-1a86_USB_Serial-if00-port0 remote screen -o /tmp/screen.png   # text of the screen + status line
tools/tdh3/tdh3 -p /dev/serial/by-id/usb-1a86_USB_Serial-if00-port0 remote keys 1 6 2 5 5 0             # type a frequency
tools/tdh3/tdh3 -p /dev/serial/by-id/usb-1a86_USB_Serial-if00-port0 remote keys 1:long                  # long press (monitor)
```

The status line gives frequency, RSSI, squelch open/closed, speaker on/off and battery, which
is how squelch and audio routing get checked without ears on the radio. Mirroring slows every
redraw (1 Mbaud, 10 µs a byte), so leave REMOTE off in normal use. PC LINK drops the
port to the clone protocol's 38400 while it runs (CHIRP, `tdh3 eeprom`/`channels`/`reset`/`settings`); `tdh3 remote`
tries 1 Mbaud first and falls back to 115200 and 38400 for older builds. The step commands read
the screen whole once, then keep it from the draws each key sets off; a check that fails on that
screen reads it whole again before it counts. In the emulator, `tools/lcdshot.py`
renders `cpu.lcd` to a PNG the same way.

`remote tune`, `channel`, `slot`, `home` and `settings` do the checked steps of `tools/radio.py`
in one command, for AI agents and people alike: docs/AI-AGENTS.md. `tools/emuport.py` puts the
emulated radio on a pty for them, and `tools/emucheck.sh` runs them all against it.

`tools/battlog.py` measures idle drain: it reads the status line once a minute and writes a CSV
row every 5 minutes (median mV, % of the time the squelch was open and the speaker on, mV lost
per hour). The cable may charge the pack (the script says so when the mV climbs) and its noise
can open the squelch (below), so for a clean number log with a data-only cable, or leave it
unplugged and read the mV with `tdh3 remote screen` at the start and end.

### Rules for driving the real radio

- **Judge reception with the USB cable unplugged.** Plugged into the PC, the cable put about 40 dB of
  noise on the receiver (a quiet 2 m channel read -77 to -81 dBm, S9+, through REMOTE) and weak
  stations cut out next to a UV-K5 that heard them fine; unplugged, the two matched. REMOTE's
  signal readings and `tdh3 remote watch` show the cable's noise, not the band's. A clip-on ferrite
  at the radio end, a USB isolator or a laptop on battery helps when the cable must stay in.

Use `tools/radio.py`,
which does all of this:

- **Check the screen after every step**, and stop on anything unexpected. Never chain keys blind:
  one dropped or doubled key corrupts every step after it (a survey typed `91200` + `97600` as one
  entry and left slot A on 601.04 MHz, then 400.107).
- **Frequencies are 6 digits, MHz first** (`091200` = 91.2 MHz). Fewer digits wait for more, so
  the next number runs into them. Clear a half-typed entry (V/M until the bottom line has no
  `FREQ`/`CHANNEL`) before typing.
- **Full-screen modes (scope, FM, freq find, PC LINK) only from the home screen**, checked, and
  check the home screen again after leaving them.
- **One byte per remote command.** The radio keeps up to 16 that land while it is busy (UART1's
  interrupt, `uart1_isr`) and reads one key byte a pass; older firmware held one byte and lost the
  rest, so tdh3 still spaces them 20 ms+ apart.
- **PC LINK owns the port**: no remote screen while it runs; V/M leaves it.
- **Never `pkill -f` a pattern that matches your own shell** (it kills the command that ran it).
- **Leave the radio as you found it**: note slot frequencies, step and mode first, and put them
  back after.

## Not yet checked on the radio

FM seek/signal readback (above), a two-way radio signal opening the
squelch with clear audio, TX power into a dummy load.
