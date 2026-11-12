# TD-H3 hardware

Facts this firmware is built on, each with its source. "Stock" means TIDRADIO firmware
`H3_250317.BIN` (stock/td-h3/250317), read with `tools/dis51.py`; addresses are code addresses
in that image. "Schematic" means TIDRADIO's `TD-H3 sch.pdf` as nicsure posted it in TD-H3-Engineering (not in this repo).

## MCU

| Fact | Value | Source |
|---|---|---|
| Part | Marked TA3782F; schematic symbol says "SC95F7767". **It is a Cmsemicon CMS8S6990-family 1T 8051, 48-pin**, not SinOne SC95F. | Stock uses CMS8S6990 registers throughout: `TA` (0x96) + `WDCON` (0x97) timed access, `SCON1`/`SBUF1` at 0xEA/0xEB, baud timer `BRTCON` at XDATA 0xF5C0, `PxTRIS` direction registers, pin-function registers `PxyCFG` at XDATA 0xF000+. Names from Cmsemicon `cms8s6990.h`. The SinOne SC95F761x map does not fit (stock's UART/port writes land on unrelated SC95F registers). |
| Extra ports | P4 at 0xC0, P5 at 0xD8, `P4TRIS` 0xA4, `P5TRIS` 0xA5 (not in the 32-pin CMS8S6990 header) | Stock port init at 0xE4FC writes 0xA4/0xA5 right after `P0TRIS`..`P3TRIS`, and the values match the schematic's pin directions exactly. |
| Clock | 48 MHz | UART1 reload 0xFFB2 gives 38400 only at 48 MHz (Fsys/16/78 = 38462, +0.16%; ours uses 0xFFFD, 48 MHz/16/3 = 1 Mbaud exactly, for REMOTE, and 0xFFE6, 48 MHz/16/26 = 115385, +0.16%, while armed for the flash restart); UART0 reload 0xFEC8 gives 9600; Timer0 reload 0xB1E0 at Fsys/12 gives a 5 ms tick (stock 0xEE1B). Stock never writes `CLKDIV`: the reset clock is already right. |
| Flash | 64 KB; app 0x0000-0xF7FF, bootloader 0xF800-0xFFFF | Bootloader (below). |
| XRAM | at least 0x04E5 bytes: stock addresses XDATA up to 0x04E4; this firmware declares 1280 B | Stock XDATA addresses; our `fw.map`. |
| Watchdog | Stock sets `CKCON[7:5]=7`, then `TA=AA,55; WDCON|=0x02`, and kicks with `TA=AA,55; WDCON|=0x01` | Stock 0xEF28, 0xF040. Copied as is. |
| Flash controller | `MLOCK`/`MADRL`/`MADRH`/`MDATA`/`MCTRL` at 0xFB-0xFF | Bootloader uses them; app firmware must never touch them. The emulator test fails on any access. |
| `MOVX @Ri` paging | Unknown whether P2 pages `@R0/@R1` on this part | Not documented in what we have. SDCC's startup copies initialized XDATA that way, so this firmware has none (Makefile check) and the emulator refuses `MOVX @Ri`. |

## Bootloader (h3bootloader.bin, 2 KB at 0xF800, runs at address 0 in boot mode)

Addresses here are chip addresses: h3bootloader.bin starts with a 2-byte header (`02 7D`), so file offset = chip address + 2.

- At power-on it samples **P2.3, P2.4, P2.5** (PTT, side key 1, side key 2). Any held: stays in flash mode and sends `A5`. None held: leaves to the app (`BOOTCON` via timed access, then software reset). Before it samples them it sets `P2TRIS=C6` (P2.3-P2.5 inputs) and `P2=00` (0x0033-0x003C), and its flash-mode flag (IRAM 0x08) is written at start and set only by that pin read (0x0051-0x006A, read twice with a short delay between), so the app can't leave a key "held" across a reset: only a physically held key gives flash mode. There is no UART-only way in either; with no key it leaves at once (0x006B-0x0074).
- It leaves through 0x0477: `TA=AA TA=55 WDCON=00`, then XSFR **0xF691** (`BOOTCON`) = 0xAA, `TA=AA TA=55 WDCON=80` (software reset, into the app). 0x0477 clears R7 first; entered at 0x0479 with R7=1 it writes 0x55 instead, a branch the loader never takes. Our firmware takes it (`remote_loader`, REMOTE `B` then PTT): 0x55 should restart into the loader, which then reads the keys as at power-on. XDATA survives the software reset, so `main` clears `ptt_ok` itself.
- UART1, 115200 8N1 on P1.1/P1.3 (USB-C CH340N).
- Packet = 36 bytes `[id][block BE16][sum8][32 data]`. `A0` (handshake `A0 EE 74 71 07 74 55×30`) sets `MLOCK=AA` and erases 512-byte sectors from 0x0000 **up to 0xF7FF only**. `A1`/`A2` program 32 bytes at an address counter starting at 0, read them back, compare, then send `A3`. `A2` is the last block and starts the app.
- It erases right after the handshake without reading the UART (0x01A3-0x01CB) and sends nothing when done; bytes that arrive meanwhile are lost but for one, and a part packet is dropped after a short idle (0x0101-0x0124). After the handshake it waits for block 0 with no timeout, so `tdh3` sends block 0 again each second until the `A3` (a block 0 sent 500 ms after the `A5`s stop often landed mid-erase).
- So: the bootloader cannot erase itself; an image longer than 0xF800 would program into its sector. `tools/tdh3 firmware flash` refuses those.
- Sources: our disassembly of h3bootloader.bin (0x0040-0x0260, 0x0477-0x049D; the dump is nicsure's), nicsure's `firmware_flash_protocol.txt`, and the bytes nicsure's TDH3Flash sends. Facts only: `tools/tdh3` is our own code.

## Pin map

Read from the schematic's vector data (U2 pin numbers joined to net labels through the wires), then checked against stock's direction and initial values (`P0TRIS=FF P1TRIS=DD P2TRIS=C6 P3TRIS=EF P4TRIS=FE P5TRIS=FD`, `P0=0F P1=0A P2=40 P3=00 P4=B1 P5=20`, stock 0xE4FC).

| Pin | Port | Net | Use here |
|---|---|---|---|
| 1 | P0.2 | KEY_R2 | keypad row (DOWN) |
| 2 | P0.3 | KEY_R1 | keypad row (MENU, UP, EXIT) |
| 3 | P0.4 | LCD_CLK | hardware SPI SCK (`PS_SCK`=0x04, `P04CFG`=3) |
| 4 | P0.5 | LCD_SDA | hardware SPI MOSI (`PS_MOSI`=0x05, `P05CFG`=3) |
| 5 | P0.6 | LCD_CD | 0 = command |
| 6 | P0.7 | LCD_RST | |
| 8 | P5.0 | SDA24 | AT24C64 SDA (bit-banged; input only for ACK/read) |
| 9 | P5.1 | DECODE | BK4819 interrupt line, unused |
| 10 | P5.2 | SDA4819 | BK4819 3-wire data |
| 11 | P5.3 | SCN4819 | BK4819 select, low active |
| 12 | P5.4 | SCK4819 | BK4819 clock |
| 13 | P5.5 | URX_CTRL | UHF RX path, **0 = on** (PNP DTA014YE from 3V3) |
| 14 | P4.7 | TXU_C | UHF TX path, **0 = on** (PNP BC807) |
| 15 | P4.6 | BEEP | square wave from Timer1 |
| 17 | P4.5 | TXV_C | VHF TX path, **0 = on** |
| 18 | P4.4 | VRX_CTRL | VHF RX path, **0 = on** |
| 19 | P4.3 | POW2822 | TDA2822 speaker amp, 1 = on |
| 20 | P4.2 | AUDIOSCK | voice chip U4 clock (data is P3.6 via R38 100R) |
| 21 | P4.1 | APC | PA bias, PWM1 (`P41CFG`=5), period 200 |
| 22 | P4.0 | BT/LED | input: the Bluetooth module's LED output (see below) |
| 23 | P3.7 | SCK5802 | RDA5807M FM receiver I2C clock |
| 24 | P3.6 | SDA5802 | RDA5807M data (100R) |
| 25 | P3.5 | H-LED | flashlight, PWM5 (`P35CFG`=5), period 200. On this chip `PxnCFG`=5 (stock's code for its PWM pins) put P3.5 on PG5: a probe build drove PWM2-5 in turn and only PG5 lit the LED. The CMS8S6990C manual's PG2 code 0x14 left it dark |
| 26 | P3.4 | BATT_AD | ADC channel 8 (`P34CFG`=1) |
| 27 | P3.3 | BT_ON/OFF | Bluetooth module power |
| 28 | P3.2 | KEY_LED | keypad backlight |
| 29 | P3.1 | TX_VLED | R-LED TX diode D32, **1 = lit** (NPN Q26 sinks it from BATT1 via R42 150R; the Bluetooth module's BTLED also sinks it through Q28) |
| 30 | P3.0 | RX_VLED | R-LED RX diode D29, **1 = lit** (NPN Q18, R909 150R; BTLED via Q27) |
| 31 | P1.0 | RX_ULED | L-LED RX diode D30, **1 = lit** (pin drives it through R9 150R) |
| 32 | P1.1 | PTT/RXD232 | UART1 RX (USB-C), shared with external-mic PTT |
| 33 | P1.2 | TX_ULED | L-LED TX diode D31, **1 = lit** (R41 150R) |
| 34 | P1.3 | TXD232 | UART1 TX |
| 35 | P1.4 | CS | GT21L16S2W font ROM select (unused; held high) |
| 36 | P1.5 | SO | font ROM data |
| 37 | P1.6 | SCK24 | AT24C64 SCL |
| 38 | P1.7 | SCLK | font ROM clock |
| 39 | P2.0 | BT_TXD | UART0 RX (Bluetooth, 9600) |
| 40 | P2.1 | BT_RXD | UART0 TX |
| 41 | P2.2 | LCD_BLC_PWM | backlight, PWM0 (`P22CFG`=5), period 200 |
| 42 | P2.3 | KEY_L1 | keypad column; PTT shorts it to ground |
| 43 | P2.4 | KEY_L2 | column; side key 1 |
| 44 | P2.5 | KEY_L3 | column; side key 2 |
| 45 | P2.6 | KEY_R6 | row (* 0 #) |
| 46 | P2.7 | KEY_R5 | row (7 8 9) |
| 47 | P0.0 | KEY_R4 | row (4 5 6) |
| 48 | P0.1 | KEY_R3 | row (1 2 3) |

Font ROM SI goes through R92 (1k) to SCK5802 (P3.7). The ROM is read-only. Stock reads it at 0xD92B with fast read `0x0B` + 3 address bytes + a dummy byte, MSB first, data set while SCLK is low and taken as it rises; 8x16 ASCII is at `0x3B7C0 + (ch-0x20)*16` (stock 0xE527). Its tables: Peripherals, Font ROM.

### Keypad (schematic page 2, S3-S19)

```
         L3 (P2.5)  L2 (P2.4)  L1 (P2.3)
R1 P0.3  MENU/blue  UP         EXIT/red
R2 P0.2             DOWN
R3 P0.1  1          2          3
R4 P0.0  4          5          6
R5 P2.7  7          8          9
R6 P2.6  *          0          #
side     SK2 (TP8)  SK1 (TP7)  PTT (TP6)   <- straight to ground, 10k pull-ups
```

Side keys read with no row driven. This firmware scans the rows open drain (the row scanned an output at 0, the rest inputs with the MCU's own pull-ups, P0UP 0xF00A bits 0-3 and P2UP 0xF02A bits 6-7; stock never sets them): PTT grounds L1 and PTT-B L2, so with one held, a row that reads low before it's driven has its key in that column down, and the rest of the keypad reads as usual. That's how keys send DTMF while transmitting. Stock skips the matrix whenever PTT is down (0x6EE2) and drives the other rows high, which a key in the grounded column would short to ground. Stock's key table at 0x6EE2 gives SK1 code 0x1A and SK2 code 0x12 (its programmable side-key functions); this firmware uses SK1 as PTT-B (transmit on slot B) and SK2 as the flashlight.

## Peripherals

**LCD.** 1.44" 128×128, ST7735S command set, write-only SPI. Stock init (0x7811): reset 100/100/150 ms, `11`, 120 ms, `B1/B2/B3` frame rate, `B4 03`, `C0`-`C5` power, `36 A0`, `E0`/`E1` gamma, `3A 05`, window, `2C`, `29`. Column window starts at **32** with MADCTL 0xA0 (stock 0xE162: `2A 00 20 00 A0`, `2B 00 00 00 80`). Backlight levels stock uses: 200, 55, 50, 45, 40 of 200 (table at 0xBC47). The panel is **BGR** (seen on glass 2026-11-03); `RGB()` in `fw.h` swaps the fields.

**Font ROM (GT21L16S2W).** Genitop mask ROM, 256 KB, read-only, fitted on every TD-H3 (stock draws all its text from it). Pins and read protocol: Pin map above. Tables found in a dump of radio 2 (2026-11-04; the dump itself is Genitop's data and stays out of the repo, `stock/gt21l16s2w.bin`, untracked). Every table stores bytes as columns, LSB at the top; a 16-row glyph has its rows 8-15 in a second run of column bytes after the first.

| Address | Table | Bytes per glyph | Index |
|---|---|---|---|
| `0x00000` | GB2312 Chinese, 15x16 | 32: 16 columns rows 0-7, then 16 columns rows 8-15 | GB2312 row/cell |
| `0x066C0` | ASCII 7x8, bold (6 px wide, no spacing column) | 8 | `(ch-0x20)*8` |
| `0x3B7C0` | ASCII 8x16 (thin strokes; digits 11 rows tall) | 16: 8 columns rows 0-7, then rows 8-15 | `(ch-0x20)*16` |
| `0x3BFC0` | ASCII 5x7 (5 columns used, near glcdfont) | 8 | `(ch-0x20)*8` |
| `0x3C2C0` | ASCII Arial 16, proportional | 34: 2-byte width (digits 9, `.` 4, `M` 13), then 16x16 as above | `(ch-0x20)*34` |

No 6x12 or 8x12 ASCII. `0x3CF80` and `0x3D580` hold something else (not bold 8x16 or a second proportional font). A glyph read is 5 command bytes and 8-34 data bytes, bit-banged: about 25-60 us at 48 MHz.

What it's good for in our UI: the small text can come from the 5x7 table and our 485 B `font[]` goes (only the two arrows stay); a bigger readable line (8x16) or a proportional heading (Arial 16) costs no flash for glyphs at all; and a Chinese UI is possible without storing a single glyph. Costs: every text draw shares P3.7 with the RDA5807's SCL (harmless so far, put back after each read), and the emulator needs the dump to draw text the way the radio does.

**BK4819.** 3-wire, MSB first, data valid while SCK high, reads switch P5.2 to input (stock 0xC8FC read, 0xE74F write `(reg, hi, lo)`). Init (stock 0x9F8F): `REG_00=8000, 0000`, 50 ms, 25-entry table at 0x6950, `REG_53=E66E`, `REG_77=88EF`, `REG_09` AGC ×16 (identical to DualTachyon's UV-K5 values). RX enable (stock 0x8555): `37=1F0F 31 28=5638 29=AB24 75=E102 54=9009 55=31A9 48=B2A8 3F 30=BFF1`. TX enable (stock 0x7EB1): `28=6B00 29=AB24 74=E102 44=9109 45=32A9 40=34BA 7D=E9xx 37=1D0F 52=0310 30=0 30=C1FE 50=3B20`. Mic gain table at 0x69BB, default step 5 → `7D=E94B`. CTCSS word = tone(0.1 Hz) × 2065 / 1000 into `REG_07` with `REG_51=904A` (matches nicsure's CTCSS table and DualTachyon's driver). Frequencies are written `REG_39` then `REG_38`, 10 Hz units. Stock never writes `REG_36`.

**Squelch.** Hardware squelch from stock's tables at 0x6A05-0x6B1C: four rows (UHF wide, UHF narrow, VHF wide, VHF narrow; split at 300 MHz) of 10 levels × 4 bytes. Per level `REG_78` = RSSI open/close and `REG_4F` = noise close/open. The chip then reports open in `REG_0C` bit 1 (stock reads it at 0x57D6). An RSSI threshold alone opened on band noise on the radio.

**Other BK4819 functions** (Beken BK4819 datasheet register table; the same chip as the UV-K5, whose open firmwares use these registers the same way; checked in the emulator):

| Function | Registers |
|---|---|
| RSSI | `REG_67 & 0x1FF` / 2 - 160 = dBm |
| RSSI at another frequency (scope) | set frequency, `REG_30=0200` then `BFF1`, settle ~1.5 ms, read `REG_67` |
| 1050 Hz NOAA alert detect | `REG_51=944A`, tone word 2625 in `REG_07`, flag `REG_0C[11:10]` |
| Tone scan: CTCSS heard | `REG_68`: bit 15 = none, else `(r & 0x1FFF) × 4843 / 10000` = 0.1 Hz |
| Tone scan: DCS heard | `REG_69`/`REG_6A` low 12 bits each = 23-bit word, bit 15 of `REG_69` = none |
| Frequency counter (freq find) | `REG_32=0245` on, `0244` off; `REG_0D` bit 15 = counting, else `(REG_0D & 0x7FF) << 16 | REG_0E` = 10 Hz |
| Roger beep | mute mic `REG_50=BB20`, tone `REG_70=E000`, `REG_71` = tone word, `REG_50=3B20` |
| Tones on the speaker (ours) | `REG_47=6340` (AF = tone), `REG_30=0302` (AF DAC + DSP), `REG_70` tone 1 bit 15 + gain 14:8, tone 2 bit 7 + gain 6:0, `REG_72` = tone 2 word (same scale as `REG_71`). Stock does the same with `REG_30=C3FA` while transmitting its tone burst (0xC1C4); its own beeps are only the P4.6 square wave. |
| Mic level while transmitting | `REG_64 & 0x7FFF` |
| VOX (voice detect) | `REG_31` bit 2 on (stock sets it on RX and TX when its VOX is on, 0x8574 / 0x7E9D); `REG_46` = `A000` + find level, `REG_79` = `1800` + lose level, `REG_7A` = `089A` (bits 15:12 the chip's own hang, 0 here), found/lost interrupts `REG_3F`/`REG_02` bits 5/4 (DualTachyon's BK4819_EnableVox). Stock itself compares `REG_64` in software with its table at 0x693C (gain 1-5: 1226, 1018, 810, 602, 384; then 308, 232), 5 samples in a row. Ours: VOX 1-9 = 1226 down to 232 in steps of x0.81, lose at 3/4 of find, VOX DELAY in our code |
| Sleep (battery saver) | `REG_30=0000`, `REG_37=1D00` |
| USB (SSB) receive (ours) | `REG_47=6540` (AF type 5, baseband), `REG_3D=0000` (IF 0; `2AAB` otherwise), `REG_73` bit 4 set (AFC off, in every mode but FM), plus the AM AGC and `REG_43=1248`: F4HWN's RADIO_SetModulation, the Apache-2.0 part of `fw/bk4819.c` (NOTICE). Stock writes neither `REG_3D` nor `REG_73`. Whether the baseband path is single or double sideband is unmeasured; no open firmware has LSB. The chip's noise squelch can't work without a carrier: in USB the firmware squelches on `REG_67` alone. |

**DCS.** `REG_51=8033` (CxCSS on, CDCSS, 23-bit, TX gain 51), `REG_07=2775`, codeword in `REG_08` as low 12 bits then `0x8000 | high 11 bits`; codeword = TIA-603 DCS: data `100` + 9-bit code in bits 11:0, Golay(23,12) parity (generator 0xC75) in bits 22:12; inverted codes XOR `0x7FFFFF`. Match flag `REG_0C[15:14] == 1` (CTCSS: `REG_0C[11:10]`). Stored on the TD-H3 as octal digits in BCD with `0x80` (normal) or `0xC0` (inverted) in the high byte.

**Speaker path (schematic pages 1 and 3).** BK4819 EARO (pin 8) → R80 2K7, C56 47 nF to ground (a low-pass near 1.25 kHz) → C60 → R81 2K2 → net AUDIO. BEEP (P4.6) joins AUDIO through C47 100 nF + R130 220K; the RDA5807 joins it too. AUDIO → volume pot SW401 → C802 → TDA2822L (U7) IN, powered by POW2822 (P4.3) through Q24. The volume knob sets every sound's level.

**Voice chip (U4).** Fitted on the test radio, though the schematic marks it NC: 8 pins (F, SCK, SDA, VDD x2, VOT, OSC, VSS) on 3V3, OSC to VDD through R26 390K, VOT through C22 and R29 68K to AUDIO. Clock AUDIOSCK = P4.2, data AUDIOSDA = P3.6 through R38 100R, shared with the RDA5807 (whose own clock stays still). Stock 0xED16 + 0xDE41: both lines low 20 ms, clock high 10 ms, then the prompt byte MSB first, data set while the clock is low and taken as it rises; the chip then plays the prompt by itself with the amp on. Stock's prompt codes are 0x0F-0x63 (lengths in its table at 0xD6B8 + code, 10 ms units) and it adds 0x50 for English, so the English prompts are 0x5F-0xB3 (heard on the radio 2026-11-03: 0x61 up speak; 0x00-0x5F give only the amp's click). The external OSC resistor marks a factory-programmed part: its recordings can't be rewritten in circuit. REMOTE `V` n plays prompt n (tools/voicedump.py).

**FM broadcast (RDA5807M, U33).** Bit-banged I2C, SDA P3.6 (`P3TRIS` 0xEF out / 0xAF in), SCL P3.7, write address 0x20. Stock 0xCF2E: `REG02=0002` (soft reset), 50 ms, the 36-register table at 0xDA05 written from REG02 (`92E1 0008 0400 C3AD …`), 200 ms; tune (0xE7DD): `REG02=D2E1`, `REG03 = channel<<6 | TUNE | band 76-108`; mute `REG02=92E1` (0xEAEE). Audio shares the speaker amp. This firmware powers the chip down (`REG02=0000`) when leaving FM. **P3.6 doesn't read back as a GPIO input** (it reads 0 even right after being driven high, pull-up on or off; both RDA5807 read modes return 0000). Untried: routing P3.6/P3.7 to the MCU's hardware I2C master, and whether stock's own FM search works on this radio. Until one of those reads, the RDA5807's status, RSSI and RDS registers are out of reach. FM seek and signal strength use the BK4819 instead, which hears broadcast FM on its VHF path.

**Status LEDs.** Two spots, L-LED and R-LED, each an RX diode and a TX diode (the schematic gives all four as GL-0603QYGC, so no colour; the pin names say RX and TX). None of the four nets is an RF switch: those are P5.5, P4.7, P4.5, P4.4. Stock lights one spot by the selected slot (XDATA 0x00B8 bit 0, or the heard slot at 0x02B4 in dual watch): RX at 0x585C (P3.0 or P1.0 as the squelch opens), TX at 0x7C83 (P3.1 or P1.2), all off at 0x58BD/0x7D90. This firmware lights L-LED for slot A and R-LED for slot B (checked on the radio: RX diodes green, TX red): green on the heard slot's spot, red on the transmitting slot's, both spots amber in a short blink for a weather alert or a waiting BELL call (`leds()` in main.c).

**Bluetooth.** Module on UART0 (P2.0 RX from module, P2.1 TX), 9600: `SCON0` mode 1, `FUNCCR[2:0]=4`, second baud timer `BRT2` at XDATA 0xF5C4-0xF5C6 = 0xFEC8, `PCON.SMOD0`, `P20CFG`=`P21CFG`=2 (stock 0xDE80). P3.3 `BT_ON/OFF` enables it (stock 0xED88). P4.0 `BT/LED` is an input from the module (`P4TRIS` bit 0 = 0; stock reads it at 0xE797). Stock buffers UART0 bytes at XDATA 0x047F and requires `P` first: the clone protocol.

**Band split.** RX and TX paths switch at **300.000 MHz** (stock 0xDB67, constant 0x01C9C380). Below: VRX/TXV. Above: URX/TXU.

**TX power.** APC PWM1 duty = a byte from the factory calibration block (stock 0x5443-0x5536):

| TX frequency | Low power | High power |
|---|---|---|
| 136-176 MHz, segment s = 10 MHz digit (3..7) | cal[s-3] | cal[16+s-3] |
| 200-350 MHz | cal[13] | cal[29] |
| 350-600 MHz, s (0 below 400, 7 at 480+) | cal[5+s] | cal[21+s] |

cal = EEPROM 0x1F50-0x1F7F, valid when EEPROM 0x1FF0 = 0x85 (stock 0xC8E3). Stock's defaults when blank (0xC7AA) are around 45-57 low and 120-130 high. The test radio has factory values low 40-57, high 120-130. This firmware adds ULP: APC duty 0 (PA unbiased; nicsure's Ultra Low Power idea), and MID: the whole part of sqrt(low × high), the geometric mean of the segment's two factory duties (both radios: 2 m 50/130 → 80, 70 cm and GMRS 40/130 → 72, 220 57/130 → 86). Geometric because PA output saturates with bias, so the plain average would land near high. Measured on the test radio into a dummy load (SWR 1.01, battery 7.44-7.53 V), forward watts ULP / LOW / MID / HIGH: 2 m (146.520) 0.47 / 1.89 / 3.24 / 5.22; 70 cm (446.000) 0.48 / 1.60 / 3.17 / 4.85; GMRS (462.5625) 0.24 / 1.40 / 2.95 / 4.20. ULP is the PA's leakage with no bias. 220 MHz is not measured: this firmware doesn't transmit there. MID needs both bytes valid (10-199), as LOW and HIGH do. Stock writes 0 to PWMD1 when TX ends (0xCD0A).

**Battery.** ADC channel 8, 12-bit left-aligned (`ADRESH<<4 | ADRESL>>4`), stock config `ADCON1 = ch | 0x60 | 0x80`, `ADCLDO=0xA0` (0xE4A4). Stock's bars at raw 3106/3492/3789 and it waits at boot until raw > 2807. Volts per count is not on the schematic page we read: 2.058 mV/count (`BATT_UV_PER_COUNT` in `fw/sys.c`) read 7.4 V on the test radio, which is plausible for a 2S pack; BCAL in the menu trims it in 20 mV steps.

**UART1 (USB-C).** `SCON1` mode 1, `FUNCCR` selects the baud timer, `BRTDL/H`=0xFFB2 (38400; ours 0xFFFD = 1 Mbaud while REMOTE is on, 0xFFE6 = 115200 while armed, `uart_usb_fast`), `PCON.SMOD1`, `PS_RXD1`(0xF69E)=0x11, `P11CFG`=`P13CFG`=2 (stock 0xDBAD). Stock flips `P11CFG` to 0 to read the external PTT on the same pin. Its interrupt is number 6, vector 0x0033, enabled by `ES1` (IE bit 6); TI1 and RI1 share it and both are cleared in software (CMS8S3680/69xx Reference Manual V1.0.9, 6.1 and 19.5). Stock never enables it (its only vectors are Timer0 0x0B and ADC 0x9B); ours does, for `uart1_isr`'s 16-byte receive buffer.

## EEPROM (AT24C64, 8 KB)

TIDRADIO's own map (as nicsure tabulated it in `H3.xlsx`), confirmed by stock reads and by CHIRP `tdh8.py` once CHIRP's 8-byte ident header is subtracted:

| Address | Contents |
|---|---|
| 0x0010 + 0x10·(n-1) | channel n (1-199): rx[4] tx[4] little-endian BCD in 10 Hz; rxtone[2] txtone[2] BCD 0.1 Hz (FFFF none, 0x80xx DCS N, 0xC0xx DCS I); byte 12 stock's scrambler, 0 off or 1-16 (stock 0x6C16 copies it to 0x032A; nonzero sets REG_31 bit 1 at 0x7E90 and loads the word at 0x699B + 2·(n-1), 0xE6E1; CHIRP `unused1`), which this firmware writes 0; byte 13 PTT-ID/BCL; byte 14 bit3 narrow, bits5:4 power (0 low, 1 high, as stock and CHIRP; **2 = MID, 3 = ULP, ours**: CHIRP's TD-H3 driver has only Low and High and shows both as Low. Firmware before 0.50 wrote high as 2: a channel saved by it and never re-saved since reads as MID now, less power, not more), bits1:0 shift; byte 15 unused by stock (CHIRP `unused10`; CHIRP zeroes the whole record when it writes a channel), **ours: scan-group bitmask G1-G8**. FF is also a blank channel's byte (stock; CHIRP's empty fill), so FF means all eight only when byte 13 bits 1:0 are **01, ours** (written with every channel and by CH GROUPS; stock tests neither bit and CHIRP calls them unused; an erased record has 11, a CHIRP-written one 00); otherwise FF reads as none. Byte 14 bit 2 and byte 13 bit 3 (unused by stock and CHIRP), also **ours only with that 01 mark**: the channel's mode, bit 0 and bit 1 of 0 FM (AM on the air band, 108-137 MHz, as every stock or CHIRP channel reads), 1 AM, 2 USB (receive only); 3 reads as 0 |
| 0x0C90 | key assignments (6) |
| 0x0C98, 0x0CA0 | settings (CHIRP's `settings` struct) |
| 0x0CB0 | VFO offsets |
| 0x0CD0-0x0D33 | FM broadcast presets 1-25 (stock fmmode[25]), 4 bytes little-endian BCD MHz x 10, FF empty (CHIRP writes 0) |
| 0x0D40 + 8·(n-1) | channel names |
| 0x1800 / 0x1810 | DTMF stun / kill codes (CHIRP `skcode`; unused here) |
| 0x1820 | DTMF ID code, 3 digits (CHIRP `icode`; unused here) |
| 0x1830 + 0x10·(n-1) | DTMF codes 1-8 (CHIRP `group1`-`group8`): 7 bytes, one digit each, 0-9, 0x0A-0x0D A-D, 0x0E *, 0x0F # (stock's encoding, as its group-code byte at 0x182F), FF ends a code; bytes 7-15 of each slot untouched. A factory radio has codes 4-8 all 00, which this firmware reads as blank. Sent with PTT + MENU + n; edited in DTMF CODES. CHIRP's charset has no * or #: it shows them as E and F |
| 0x1900 | channel-valid bitmap (bit n-1) |
| 0x1920 | scan-list bitmap |
| 0x1940 | FM preset-used bitmap (CHIRP `fmusedflags`, bit n-1 = preset n): CHIRP shows and keeps only presets with their bit set, so this firmware sets and clears it with each preset |
| 0x1950 / 0x1960 | VFO A / B, channel format |
| 0x1C00 | power-on messages |
| 0x1E00-0x1E07 | **ours**: CALL ID's callsign, up to 8 characters, NUL after the last (as a channel name); a NUL or FF first byte (a new radio's) is OFF. In the gap 0x1C30-0x1F1F: stock's EEPROM reads at boot and in the menu (logged in the emulator) never touch it and its code has no address there, CHIRP's TD-H3 map defines nothing there (it writes back the bytes it read), and a radio's backup holds FF there |
| 0x1F20 / 0x1F30 | mic gain / Bluetooth |
| **0x1F50-0x1F7F** | **factory TX power calibration — never written by this firmware** |
| 0x1FC0-0x1FEF | this firmware's settings (`cfg` in `main.c`, magic 0xAD; `group` is a mask of scan groups): small settings share bytes 0x1FC2-0x1FC9 (`tools/budget.py` counts the free bytes and bits); 0xAB and 0xAC blocks are moved over on boot (0xAC: POWER 0 low, 1 high, 2 ULP becomes 1, 3, 0), an out-of-range value gets its default, an older or bad magic loads defaults |
| **0x1FF0** | **stock's "initialized" magic 0x85 — never written** |

## Clone protocol (38400 8N1)

`PVOJH\x5C\x14` → `06`; `02` → 8-byte ident (`P31183\xFF\xFF` TD-H3, `P31184` GMRS, `P31185` HAM); `06` → `06`; `R AH AL 20` → `W AH AL 20` + 32 data + sum8; `W AH AL 20` + 32 data + sum8 → `06`; `E` → `06`. Blocks are 32-byte aligned. Sources: nicsure's `protocol.txt`, CHIRP `tdh8.py` `_read_block`/`_write_block`, and stock's handler. Facts only: `fw/clone.c` and `tools/tdh3` are our own code.
