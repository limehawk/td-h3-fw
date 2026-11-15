# What's new

What changed on the radio and in the tdh3 flasher, newest release first. Each release's page
starts with its part of this list.

## Next release

- The home screen's bottom line now shows the keys that work right now: `* scan  # A/B  V/M ch` on
  a frequency (`V/M vfo` on a channel), the scan's own keys while scanning (`* stay  hold V/M skip` while it waits on a station), how to leave weather
  mode, how to turn monitor off, and how to unlock the keys. While you type a number it says
  `MENU ok`. The menu, the lists and the FM radio list their keys first and the arrows last.
- Every key line on the screen draws its keys in the key's own colour: **MENU** blue, **V/M**
  orange, the other keys bright, and what each does in a softer colour. Every theme keeps it easy to
  read; the night themes keep their one colour.
- A key pressed from the computer (REMOTE) during a slow screen update is no longer lost.
- The up and down arrows and the warning triangle are redrawn as clean, even triangles with a one-pixel
  tip, on the radio and in `tdh3 remote screen`.
- `tdh3 update --rx` flashes the receive-only build after it replaces the flasher, the same as
  `tdh3 flash --rx`.

## 0.68.0

- On Windows, `tdh3.exe` now has its own icon (the radio tower from the flasher) and shows its
  version, maker and description under Properties > Details. A plain start no longer touches any
  file next to it; the old copy that `tdh3 update` sets aside is removed on the next start, as before.
- `tdh3` no longer fills a log with spinner frames when its output goes to a file or a pipe
  (`| tail`, a script, CI): each step shows once, like "... reading the radio", followed by its
  result. On a terminal it looks the same as before.
- `tdh3 flash` shows a band scope under the tower while it flashes: a spectrum that fills from the
  left as the firmware goes on, over a waterfall. It needs a window at least 27 lines tall; in a
  smaller one the flasher looks as before.
- tdh3's help and messages say "restore" and "reset" where they said "put back": `tdh3 restore` is
  "Restore a backup to the radio", and `tdh3 reset` is "Reset every setting to its default".
- Windows: double-clicking `tdh3.exe` opens the flasher every time. Before, a double-click could
  show "This is a command line tool" and close instead.
- BRIGHTNESS: each step up is now visible. Before, levels 6 to 10 all looked the same; now 1 to 9
  climb in even steps and 10 is full.
- `tdh3 settings set` works with REMOTE on, without PC LINK: it changes the setting on the radio's
  own menu, reading the screen after every key, so `tdh3 settings set BRIGHTNESS 10` brings back a
  screen you can't see. Over REMOTE it can also set MODE and OFFSET MHz for the selected slot.

## 0.67.1

- BRIGHTNESS has 10 levels, and every one lights the screen: in the last two versions levels 1 to 3
  left it black. Each step looks about the same size, from 1 (dimmest) to 10 (full, the default).
  Your level stays as it was after the update.

## 0.67.0

- The scope (hold 3) colours each bar by strength, dark for the band's noise up to the brightest for
  a strong station (navy to red in CLASSIC; each theme uses its own colours), and keeps a waterfall
  of the last sweeps under the bars. Its scale sets itself to the band's noise, the cursor's signal
  shows in dBm beside its frequency, and 5 switches to a slow sweep that catches short transmissions.
- tdh3's help and messages give the right keys for PC LINK (MENU, 4 6, MENU), say where the
  backups it saves by itself go and how to restore a backup.
- `tdh3 channels import`, `tdh3 reset` and `tdh3 settings restore` now save a backup of the whole
  radio first, like the other commands that change it. Two backups made in the same second no
  longer share a name: the second gets `-2` after the time.
- New: `tdh3 restore BACKUP.bin` restores a whole backup in one step: channels, FM presets,
  settings, the CALL ID and the stored DTMF codes. It lists what will change and asks first.
- New: `tdh3 backups` lists your backups with the date, firmware and channel count.
  `tdh3 eeprom backup` with no file name saves into the `backups` folder under the date and time,
  and it no longer replaces a file you already have unless you add `--force`.
- New: `tdh3 diff` shows what differs between two backups, or between a backup and the radio:
  channels added, removed and changed, FM presets, and settings by their menu name.
- New: `tdh3 settings show` lists every menu setting with its value, from the radio or a backup,
  and `tdh3 settings set NAME VALUE` changes one, like `tdh3 settings set POWER HIGH`.

## 0.66.0

- The home screen shows two times: while you hear a station, how long it has been talking, in the
  middle of the meter bar (`0:42`); and after each quiet slot's letter, how long ago someone was
  last heard on its frequency (`<1m`, `12m`, `3h`).
- The radio keeps receiving while the menu is open: a call no longer goes quiet when you press MENU,
  on the other slot (dual watch) and the priority channel too. A weather alert closes the menu to show itself.
- New: five more colour themes in THEME (MENU 38): POCKET (dark green on pale green), NIXIE (warm
  orange on near-black), E-INK (black on off-white), FIELD (khaki on olive) and HIGH-VIS (black on
  bright yellow, for sunshine). Your theme stays as it was after the update.
- BRIGHTNESS: each of the five levels now looks different. 3, 4 and 5 used to look the same; 1 is
  dimmer than before, for night use.
- New: CALL ID (MENU 20) sends your ham callsign in Morse code when you let go of PTT, on 2 m and
  70 cm only, the first time after you switch on and then at most every 10 minutes. It stays off
  until you type a callsign.
- New: DIGITS (MENU 39) picks how the big frequency numbers look: ARCADE, chunky with a soft
  shadow (the new default); LCD, like a digital clock; or CLASSIC, the look from before. With ARCADE
  and LCD, `VFO` or the memory number sits at the top right of the slot.
- The frequency always shows its last two digits, small after it: `146.520` with a small `00`,
  GMRS channel 1 `462.562` with a small `50`. They used to show only when they weren't zero.
- The menu items from SCAN GROUP on moved down one place, and those after THEME one more: SCAN
  GROUP is now 21, KEY BEEP 40, PC LINK 46, REMOTE 47 and VERSION 52.
- tdh3: `tdh3 remote screen` reads the frequency and memory number in every DIGITS look.
- The signal meter changes three times a second instead of ten, so a fluttering signal is easier to
  read. A new channel's reading still shows at once, and so does the squelch opening or closing.
- The green (or red, while you talk) behind the slot you're hearing now runs right up to the signal
  meter, with no dark line between them.
- The meter no longer drops to nothing for a moment after a key beep.

## 0.65.0

- The screen switches off while the backlight is out, to save battery. A key press brings both
  back, showing anything that changed meanwhile.
- PC LINK: the backlight follows your LIGHT setting, and PC LINK closes by itself after 10 minutes
  with nothing from the computer, so a radio left on the cable goes back to listening.
- tdh3: `tdh3 firmware extract` saves the firmware the flasher carries as `.bin` files.
- tdh3: a channel file that gives the power as `ULP` loads it as ULTRA LOW.
- tdh3: in a narrow window, the flasher's waiting line stays on one line instead of scrolling.
- Manual: a Screenshots page with every screen in one place, and pictures on every page.

## 0.64.1

- The flasher has the same name on every computer: Windows downloads `tdh3.exe`, and the Mac and
  Linux downloads hold a program called `tdh3`. On Linux it unpacks with
  `tar xf tdh3-linux-x64.tar.gz` (`-arm64` on a Raspberry Pi) and no longer needs `chmod`.
- tdh3: if `tdh3 update` from a v0.63 or v0.64 flasher says the release has no file for your
  computer, download the new flasher by hand this once; after that `tdh3 update` works again.
- Manual: a new page, The tdh3 program, lists every `tdh3` command in one place, and ends with a
  prompt to paste so an AI assistant that can run commands on your computer can work the radio.

## 0.64.0

- OFFSET (MENU 11), how far a repeater's transmit frequency is from the one you hear, keeps two
  values: one for VHF (under 300 MHz, such as 2 m) and one for UHF (300 MHz and up, 70 cm and
  GMRS). Setting it for a 70 cm repeater no longer makes 2 m transmit 5 MHz away, out of the band.
- OFFSET is dialed with UP and DOWN in 0.05 MHz steps, from 0.05 to 12.75 MHz, instead of picked
  from a short list. After the update VHF starts on 0.60 and UHF on 5.00.
- SHIFT AUTO (MENU 10) shifts 0.6 MHz down from 145.10 MHz instead of 145.20, as the US band
  plan's repeaters do.
- The home screen shows a repeater shift of 10 MHz or more in full; it used to show only its last
  digit.
- CH TO VFO (MENU 33) keeps a channel's shift when it's any multiple of 0.05 MHz up to 12.75 MHz;
  before, only the few old OFFSET choices came over.
- tdh3: `tdh3 channels list`, `export`, `set`, `move`, `clear`, `lock` and `unlock` show and change
  your channels from the computer, one at a time or many at once
  (`tdh3 channels set 88-94 --power low`). Only what you ask for changes, and the radio is backed
  up first.
- tdh3: `tdh3 settings restore my-radio-backup.bin` puts your menu settings back from a backup; the
  channels on the radio stay as they are.
- tdh3: `tdh3 --version` on a downloaded flasher names its release.

## 0.63.0

- tdh3: `tdh3 update` fetches the newest flasher, checks that it is the release's own signed file,
  puts it in place of the old one and flashes the radio. `--no-flash` only replaces the flasher.
- tdh3: `tdh3 reset` puts every menu setting back to its default over PC LINK (MENU 44). Your
  channels stay; `--channels` empties them too.
- When a computer changes the radio's settings over PC LINK (`tdh3 reset`, or a CHIRP upload), the
  radio restarts by itself once the computer is done, so it starts on the new settings.
- tdh3: `tdh3 channels import` reads all four power levels from a channel list: `ULow`, `Low`,
  `Mid`, `High`, or a wattage such as `0.5W`.

## 0.62.0

- Hold 2 on a memory channel changes that channel's power (low, mid, high, ultra-low) and saves it
  with the channel; its name, tones and everything else stay. Before, only frequency mode could.
- A setting changed just before you flash is kept; flashing within 2 seconds of the change used to
  lose it.
- tdh3: `tdh3 remote settings` no longer stops now and then with *No lit menu row on the screen*.

## 0.61.1

- tdh3: `tdh3 remote tune`, `channel` and `settings` are quicker on a radio with 0.61 or later:
  each key moves on as soon as the screen shows it worked.

## 0.61.0

- tdh3: `tdh3 remote wait "squelch open"` waits until that text shows on the radio's screen or
  status line, then returns: the moment someone talks on the frequency, for example. `--gone` waits
  for it to go away, and `--timeout 30m` gives up after that long.
- tdh3: `tdh3 remote record my.rec` records everything the screen shows until Ctrl-C, and
  `tdh3 remote replay my.rec -o my.gif` plays it back and makes an animated GIF of it. Recording
  never presses a key or transmits.
- With REMOTE (MENU 45) on, the radio sends its screen to the computer faster, and keys the
  computer presses while the radio is busy are no longer lost.
- A flasher from an older release can't talk to the radio at the new speed and says *radio not
  answering*; it still flashes, the switch-on-holding-PTT way.

## 0.60.3

- The first public release: open firmware for the TIDRADIO TD-H3 handheld, put on the radio with
  the `tdh3` flasher from the release page. Your channels, CHIRP and the radio's factory
  calibration keep working.
