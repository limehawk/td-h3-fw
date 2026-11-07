# td-h3-fw

Open firmware for the TIDRADIO TD-H3. The workflow is docs/DEVELOPING.md.

## The project stays MIT

A top rule. Never paste or adapt code from a GPL or other copyleft project (most open radio
firmwares are), not even one function: read it for ideas, then write ours from datasheets and our
own notes. Check the licence before taking anything from anywhere; only MIT, BSD, Apache-2.0,
ISC or public domain may come in, with its copyright notice kept. Apache-2.0 UV-K5 firmware (the
DualTachyon original and the F4HWN fork; same BK4819) may be adapted: keep its notice in the file
that holds it, say it was changed, carry any NOTICE file, and check each file's own licence first
(forks pull in GPL code too). Don't name other firmware projects in the user docs.

## Keep the manual current

Every change someone can see or feel on the radio (a key, menu item, screen, message, limit or
default) updates `docs/manual/` in the same commit:

- `make -C fw screenshots` regenerates the screenshots from the real firmware; commit the changed
  images. Callout numbers live in `tools/screens.py` and in the page's numbered list: change both.
- The **Quick reference** at the top of `docs/manual/Home.md` (the wiki's front page) changes with any key,
  menu number or flashing step it lists; `make -C fw test` checks its menu numbers against the firmware.
- A change to flashing or the `tdh3` tool updates `docs/manual/Updating.md` the same way.
- `make -C fw wiki` publishes `docs/manual/` to the GitHub wiki, in the same turn as the commit. Never edit
  the wiki in the browser.
- Write for someone who has never used a two-way radio. Details: docs/manual/Keeping-This-Manual-Current.md.

## Also

- Every change goes through a PR to main with CI green (`.forgejo/workflows/ci.yml`) and
  `make -C fw test` exiting 0; main refuses direct pushes. A release is a tag on main, cut when
  there's something worth shipping (`make -C fw bump [BUMP=minor]`, `git push --follow-tags`), not
  after every merge: DEVELOPING.md, "Changes and releases".
- Nothing the repo publishes carries internal notes: release notes (`.forgejo/release-notes.md`),
  README, manual and UI text are for the people using the radio.
- `make -C fw test` (PyPy, ~15 s) before every flash; drive the real radio only through
  `tools/radio.py`, checking the screen after every step (DEVELOPING.md, "Rules for driving the real radio").
- Never write EEPROM 0x1F50-0x1F7F or 0x1FF0+; never touch SFRs 0xFB-0xFF; images stay under 0xF800 bytes.
- C is the source of truth. For a function with an `fw/asm/` twin, change the C; the asm goes stale and
  the build falls back to C. Redo the asm only with `tools/asmdiff.py` proof, and commit the C and the
  asm together. ASM=1 is the default since v0.59.2 (DEVELOPING.md, Assembly).
