# What's new

What changed on the radio and in the tdh3 flasher, newest release first. Each release's page
starts with its part of this list.

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
