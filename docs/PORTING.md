# Porting to another radio or a TD-H3 variant

This firmware is built for one board: the TD-H3 in docs/HARDWARE.md. Variants of it may turn up:
a new board revision, another screen, another band split, no Bluetooth. A newer radio might also
use the same BK4819. This page says how to add them without forking the code. Read it before you
start.

## The model

Firmware that runs on many radios from one tree usually keeps three layers apart:

| Layer | What lives there |
|---|---|
| Interfaces | the calls the UI and protocols make; no pins, no registers |
| Chip drivers | one per part (RF chip, LCD controller, flash); shared by every radio that has that part |
| Boards | one per radio: the pin map, clocks, which drivers it uses, LEDs, backlight, battery, PTT |

You choose a radio when you build, and each radio gets its own image. Nothing is detected at
runtime.

**Licence.** This firmware is MIT. Many other open radio firmwares are GPL. Read their code to
learn how they work, then write ours from the datasheet and our own notes. Never paste or adapt
their code, not even one function: a file that contains GPL code becomes GPL.

## What we copy, and what we don't

Those firmwares mostly run on 32-bit MCUs with RAM to spare, under an RTOS. We have an 8051 with 1280 bytes of
XDATA, and images must stay under 0xF800 bytes. Under SDCC every function pointer and every extra
layer of calls costs real bytes. So:

- **Copy:** one image per board, selected at compile time; one header per board; drivers named
  after the chip, not the radio.
- **Don't copy:** interface structs with function pointers, a runtime board detect, an RTOS, a HAL
  layer between `radio.c` and `bk4819.c`. Board differences are `#define`s and `#if`s that cost
  nothing.
- **Don't build it early.** Nothing below exists yet. Do the split when a second board is in your
  hand, not before. This page is the plan for that day.

## Where the board lives today

| File | Board-specific | Shared by any BK4819 radio |
|---|---|---|
| `fw/hw.h` | everything: SFRs, XDATA config registers, pin map, watchdog kick | — |
| `fw/bk4819.c` | the 3-wire bus pins (`BK_SDA/SCN/SCK`), `AMP_ON` | the register driver |
| `fw/radio.c` | RF path switches (`VRX_N`, `TXV_N`, `URX_N`, `TXU_N`), APC PWM, the VHF/UHF split | tuning, squelch, tones, TX rules |
| `fw/lcd.c` | the controller and its pins (`LCD_CD`, `LCD_RST`, SPI) | — |
| `fw/keys.c` | the matrix (`KEY_Lx`/`KEY_Rx`) and the key layout | key-event handling |
| `fw/eeprom.c` | the AT24C64 bit-bang pins | — |
| `fw/fm.c` | RDA5807M on `FM_SDA/FM_SCL` | — |
| `fw/main.c` | `BT_ON`, the EEPROM map (`cfg` at 0x1FC0) | menus, the main loop |
| `fw/fw.h` | `W`/`H` (128×128), the UART ports, the TX band bits | everything else |
| `tools/emu51.py` | the peripheral models | the 8051 core |

`AMP_ON` is used in six files. On a variant where the amplifier sits on another pin, `hw.h`
changes and the other five files stay the same, because they only use the name.

## Adding a variant

1. **Find out what changed.** Dump the variant's stock firmware and compare it with stock 250317
   (DEVELOPING.md, "Reverse engineering stock"). Compare the pin setup in its reset code, the
   SPI/UART pin selects (`PS_*`), and the LCD init. Get the schematic if there is one. Write each
   difference into a new section of docs/HARDWARE.md, with its source, the same as for the TD-H3.
2. **Move the TD-H3 header.** Rename `fw/hw.h` to `fw/boards/td-h3.h`. `hw.h` then only picks a
   board:
   ```c
   #if defined(BOARD_TD_H3)
   #include "boards/td-h3.h"
   #elif defined(BOARD_<NEW>)
   #include "boards/<new>.h"
   #else
   #error "no BOARD_ set; see docs/PORTING.md"
   #endif
   ```
3. **Write the new board header.** Copy `td-h3.h` and change only the parts that differ. Put each
   board difference that code needs to test into the header as a flag, such as `HAS_BT`, `HAS_FM`,
   `LCD_ST7735` (today's panel), `RF_SPLIT_MHZ` or `EE_CAL_BASE`. Test the flag in the code (`#if HAS_FM`), never
   the board name (`#ifdef BOARD_X`). Then the next variant only has to set its flags.
4. **Add a Makefile target.** This works like the `td-h3-rx` image does now
   (`DEFS=-DNO_TX`): `$(MAKE) DEFS=-DBOARD_<NEW> OUT=<new> build/<new>.bin`. The default build
   stays the TD-H3. Each image gets its own name, so you can't flash the wrong one by mistake.
5. **Give the emulator the board.** `tools/emu51.py` models TD-H3 peripherals. Give it the same
   board switch and model the differences. Run `make -C fw test` for every board before any flash.
6. **Guard the EEPROM.** Find out where the variant keeps its factory calibration and its
   "initialized" magic. On the TD-H3 these are 0x1F50-0x1F7F and 0x1FF0. Put them in the board
   header, and make every EEPROM write refuse those addresses, as the TD-H3's `ee_write` does.
7. **Tell the flasher.** `tdh3 firmware flash` must refuse an image built for another board. Put
   the board's name next to `VERSION` in the image. Find out what the variant's bootloader
   reports about itself, if anything, and check the name against that. If it reports nothing,
   ask the user which radio it is before the flash.
8. **Manual.** Write down how the variant differs in `docs/manual/`, under the name printed on the
   radio's case.

## A different radio

Use the same steps if it also has a BK4819. `bk4819.c` and
most of `radio.c` carry over. If the radio has a different MCU (not 8051), it is a new firmware:
take the layout from this page and the BK4819 notes from HARDWARE.md, not the code.

## The TD-H3 Plus

Not supported here. The Plus shares the TD-H3's name, size and BK4819, but runs on a JieLi BR23
Bluetooth chip, so its firmware is a new one, not a variant: it has its own project,
td-h3-plus-fw (not public yet), which holds its hardware notes and the plan.
