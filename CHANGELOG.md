# What's new

What changed on the radio and in the tdh3 flasher, newest release first. Each release's page
starts with its part of this list.

## Next release

- BRIGHTNESS: each of the five levels now looks different. 3, 4 and 5 used to look the same; 1 is
  dimmer than before, for night use.
- New: CALL ID (MENU 20) sends your ham callsign in Morse code when you let go of PTT, on 2 m and
  70 cm only, the first time after you switch on and then at most every 10 minutes. It stays off
  until you type a callsign.
- The menu items from SCAN GROUP on each moved down one place: SCAN GROUP is now 21, PC LINK 45,
  REMOTE 46 and VERSION 51.
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
