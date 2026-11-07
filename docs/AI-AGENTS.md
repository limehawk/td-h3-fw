# Letting an AI agent use the radio

An AI agent (Claude Code, Codex, or any assistant that can run commands on your computer) can
read the TD-H3's screen, press its keys, tune it, pick channels and read its settings. It does
this with `tdh3`, the flasher you downloaded from a release, over the USB-C cable, the same way
you would type the commands yourself. **It can never make the radio transmit**: the radio refuses the PTT keys from the
cable, whatever the computer sends.

No other radio firmware does this as far as we know.

## Set it up

1. Plug the radio into the computer with its USB-C cable.
2. On the radio, switch REMOTE on: press **MENU**, then **4** **5** (item 45, REMOTE), **MENU**,
   **UP** so it says **ON**, **MENU**, and **V/M** to leave the menu.
3. Use the flasher you downloaded (`tdh3-windows.exe`, `tdh3-macos-apple`, ...; below it's
   `tdh3`), or build it from the source: `cd tools/tdh3 && go build`.
4. That's all: `tdh3` finds the radio's cable by itself. With two radios plugged in, a person
   opening the flasher types a number to pick one; you pass `-p` with a name from the list it prints (`-p /dev/ttyUSB1`, `-p COM4`).

Leave REMOTE off when you are not using it: while a computer watches, every screen change also
goes down the cable, which slows the spectrum scope.

## What the agent can do

Every command starts `tdh3 remote`. The radio's *slots* are the two
frequencies on the home screen: A on top, B below. The selected one has its letter lit.

| Command | What it does |
|---|---|
| `screen` | Prints every piece of text on the screen with its position, then the status line: frequency, signal strength in dBm, squelch open or closed, speaker, battery. `-o shot.png` also saves a picture. |
| `home` | Goes back to the home screen (leaves menus, drops a half-typed number). |
| `tune 146.520` | Puts the selected slot on that frequency in MHz. |
| `channel 12` | Puts the selected slot on memory channel 12. |
| `slot B` | Selects slot A or B. |
| `settings` | Lists all the menu settings and their values. Changes nothing. Takes about 30 seconds. |
| `watch` | Prints the signal and whether the squelch is open, five times a second, until **Ctrl-C**: for finding why audio cuts out. |
| `keys 1 4 6` | Presses keys: `0`-`9` `MENU` `UP` `DOWN` `VM` `*` `#` `TORCH`. Add `:long` to hold one (`1:long`). |

`tune`, `channel` and `slot` press one key at a time and read the screen back after each one.
If a key gets lost or the radio shows something else, the command stops with a message that
says what it saw, rather than going on to the wrong frequency. When it works, it prints the
home screen so the agent can see the result.

They are careful rather than fast: each key waits for the screen to change. From firmware 0.16 the
radio says when a redraw is finished, so a screen read takes a fraction of a second instead of a
fixed 1.5 s wait (older firmware still works, at the old speed).

## Tell your agent about it

Put something like this in the project's `CLAUDE.md` or `AGENTS.md`, or paste it into the chat:

> The TD-H3 radio is plugged in over USB-C with REMOTE on. `./tdh3-linux-x64` in this folder
> (put your flasher's name here) works it: `./tdh3-linux-x64 remote --help` lists the commands.
> Read the screen after anything you do. Leave the radio on the home screen, the way you found it.

Then ask things like "what is the radio listening to?", "tune slot B to 162.550 and tell me
the signal strength", or "list the radio's settings".

## Practise without a radio

`tools/emuport.py` runs the real firmware in the emulator and gives it a pretend serial port:

    pypy3 tools/emuport.py            # prints a port, e.g. /dev/pts/7
    tools/tdh3/tdh3 -p /dev/pts/7 remote --wait 3s screen

The emulated radio draws slower than the real one, hence `--wait 3s`. `tools/emucheck.sh` runs
every command above against it (about 1.5 minutes: the emulator draws slower than the radio).

## Why there is no MCP server

MCP is a way to hand tools to an AI app that cannot run commands, such as a chat window. Agents
that run commands already have everything they need in `tdh3`, which you can also run and check
yourself. If a chat-only app ever needs the radio, a small MCP wrapper can call these same
commands.

## What the firmware could add (proposals, not built)

Everything above works with the firmware as it is: nothing new on the radio. The radio is
tight (flash about 10,600 bytes free; XDATA RAM 1,143 of 1,280 bytes; 2 spare bytes in the
settings block). So
every proposal below uses no new RAM and no settings bytes. Flash numbers are estimates for
SDCC's code here (a function call with arguments is 8-15 bytes).

| Proposal | What it gives an agent | Flash | XDATA | EEPROM |
|---|---|---|---|---|
| **End marker after a redraw. (Built in 0.16: 46 bytes.)** The radio sends `E` when the redraw that `S` asked for is finished. A spare bit of `remote_req` remembers it for one loop. | Screen reads finish in 0.1-0.3 s instead of a fixed 1.5 s wait: `tune` drops from ~15 s to ~3 s. The biggest win. | 20-35 | 0 | 0 |
| **Slot and memory in the status line.** `146.520 -112 O S 7400 A 12` (selected slot, memory number or `V`), built in the existing `buf`. | The agent knows where it is without reading screen positions. | 30-45 | 0 | 0 |
| **Settings as numbers.** `R` answers with the settings block as hex on a `q` line. | `settings` in under a second instead of 30. But the host must then know the settings layout of every firmware version, which the menu walk does not. | 35-50 | 0 | 0 |
| **Direct tune.** `F` and 4 bytes put a frequency in the selected VFO through `set_vfo`, with the same band limits. | `tune` without typing 6 digits. Mostly made redundant by the end marker. | 60-90 | 0-5 | 0 |

Recommendation: only the end marker was worth its bytes, and it is built. Add the status-line slot if
agents keep tripping over screen positions. Skip the other two.

### If agents should ever transmit

Today they cannot. If you want them to, the rules stay the ones for the keypad: you are the
operator and are responsible for what goes out, the radio's band limits apply, the RX-only
build never transmits, and the TX timeout still cuts in. What it would take:

- **A separate opt-in.** REMOTE gets a third value, `ON+TX`, in the existing `cfg.remote` byte.
  Plain `ON` keeps refusing PTT, so letting a computer see the screen never also lets it
  transmit. Flash 15-25 bytes (menu label and one compare), 0 RAM, 0 EEPROM.
- **A dead-man timer.** A lost release byte has been seen on the radio. For PTT that would leave
  the transmitter keyed until the TX timeout (120 s by default). So REMOTE PTT lets go by itself
  when no byte has come from the computer for 0.5 s, and `tdh3` sends a byte every 0.2 s while
  it holds PTT. It uses the existing `t_heard` in `remote_tick`. Flash 15-25 bytes, 0 RAM.
- **Host side:** `tdh3 remote ptt --seconds N` with a hard upper limit, and it reads the screen
  after letting go to confirm the radio went back to receive.

In total, about 30-50 bytes of flash and no RAM or settings bytes.
