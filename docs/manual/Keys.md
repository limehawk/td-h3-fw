# Keys

## Quick reference

| I want to… | Press |
|---|---|
| Start / stop scanning | **\***: stops on what it found, or back where you started (**V/M**: always back) |
| Swap slot A / B | **#** |
| Frequency mode / memory mode | **V/M** |
| Open the menu | **MENU** |
| Light a dark screen | Any key: the first press only lights it (**PTT** always transmits) |
| Lock / unlock the keys | Hold **\*** (**PTT**, **side key 1** and the flashlight still work locked) |
| Hear everything, noise too | Hold **1**; again to stop |
| NOAA weather | Hold **#** |
| FM broadcast radio | Hold **8** |
| Talk | Hold **PTT** (slot A) or **side key 1** (slot B) |
| Send touch-tones | While holding **PTT**: the digits, **\***, **#**; **MENU** then **1**-**8** sends a stored code |
| Flashlight | **Side key 2** |

A short press acts when you let go. A long press is about one second (it acts while you're still
holding). UP and DOWN act at once and repeat while held. Any key, PTT included, wakes the backlight.
In the menu long presses do nothing; while you're typing a number, only hold **\*** (lock) works.

| Key | Short press | Long press |
|---|---|---|
| **0-9** | Type a frequency (6 digits) or channel (3 digits). In the [Menu](Menu): type an item's number to go to it (`1` `2` = item 12; for 1-5, one digit and a second's wait) | See the table below |
| **UP / DOWN** | Next / previous frequency step or channel (with a scan group chosen, only that group's channels); while scanning, scan up / down; in weather mode, WX1-WX7 | (repeats) |
| **MENU** (blue) | Open the [Menu](Menu). In the menu and every list: select, yes | Save this slot as a memory channel: it suggests the first free one and asks first (stops a scan) |
| **V/M** (red) | Switch frequency (VFO) / memory mode; stop a scan; delete a typed digit. In the menu and every list: back, no | Memory mode: put this channel on or off the scan list; while scanning, take this channel off the scan list for good (a frequency: skip it until you switch off) |
| **#** | Swap the selected slot, A ↔ B | [Weather](Channels-and-Scanning#weather) on or off: NOAA weather radio in the selected slot (stops a scan) |
| **\*** | Start or stop scanning | Lock or unlock the keypad (stops a scan) |
| **PTT** (big side key) | Transmit on slot A (or on the slot you're hearing in dual watch); A becomes the selected slot | |
| **Side key 1** (small) | Transmit on slot B; B becomes the selected slot | |
| **Side key 2** | Flashlight: tap on/off; double tap high; triple tap strobe | Hold while off: moonlight (5 s: SOS). Hold while on: moonlight, low, medium, high |

## In the menu and every list

The menu, LAST HEARD, CH GROUPS, GROUP LINK and DELETE CH all work the same way, and the bottom of
each screen says what the keys do there.

| Key | Does |
|---|---|
| **UP / DOWN** | Move the lit row (past the last row it goes back to the first) |
| **MENU** (blue) | Select it: change a setting, put a group on or off, tune to it; **yes** when the radio asks |
| **V/M** (red) | Back one step (to the menu, then to the radio); **no** when the radio asks |

PTT doesn't transmit while the menu, a list or the save question (`SAVE CH 005? MENU=YES`) is open:
close it with **V/M** first.

Anything that can't be undone asks first, in words: `DELETE 002 GMRS1?`, `REPLACE 005? MENU=YES`.
Blue MENU is yes, red V/M is no.

## Long-press number keys

The legends printed on the keys:

| Key | Does |
|---|---|
| 1 | **Monitor**: open the squelch to hear everything, even noise. Again to close. |
| 2 | **Power**: low → mid → high → ultra-low (frequency mode only; a channel keeps its own). |
| 3 | **Scope**: a live spectrum around this frequency. See [Tools](Tools). Stops a scan. |
| 4 | **Step**: the next tuning step (STEP kHz in the [Menu](Menu)). In USB, the next fine step: 0.05, 0.1, 1, 0.01 kHz. |
| 5 | **Reverse**: listen on the repeater's input, to hear a station directly. `REV` shows in the status bar; again, **#** or **V/M** turns it off. A channel saved meanwhile is saved the normal way round. |
| 6 | **Copy the tone you're hearing** (`HEARD 100.0 6:copy` on the home screen) into RX TONE and TX TONE, with two rising notes. Frequency mode only. |
| 7 | **Freq find**: grab the frequency of a nearby transmitter. See [Tools](Tools). Stops a scan. |
| 8 | **[FM radio](FM-Radio)**. Stops a scan. |
| 9 | **Dual watch** on/off: check the other slot every half second (`DW` in the status bar). |
| 0 | **Wide / narrow** FM (frequency mode). |

## While scanning

| Key | Does |
|---|---|
| **1-8** | Switch to that scan group straight away |
| **0** | Switch to the whole scan list |
| **V/M** (long) | Skip this channel from now on: it comes off the scan list (`CHANNEL SKIPPED`, its `*` goes) and every scan passes it, group scans too, even after you switch off. Hold **V/M** on it, not scanning, to put it back. In frequency mode: skip this frequency until you switch off (the last 8 are kept) |
| **\*** | Stop: stays on the station it's on; goes to the one station it found; two or more, the LAST HEARD list to pick from (**UP**/**DOWN**, **MENU**; **V/M** back); found nothing, back where you started |
| **V/M** | Stop and go back where you started |
| Hold **MENU**, **3**, **7**, **8** or **#** | Stop the scan as **\*** does, then save, scope, freq find, FM or weather |
| Hold **\*** | Stop the scan where it is, and lock |

In frequency mode, a number key stops the scan and starts typing a frequency. In memory mode,
**9** stops the scan and starts typing a channel number.

A scan group you pick (SCAN GROUP in the menu, or **1**-**8** while scanning) stays picked: in
memory mode **UP** / **DOWN** then step only through that group's channels. **0** while scanning,
or SCAN GROUP set back to SCAN LIST, brings back all of them.

## Sounds

The radio's sounds are soft tones, not buzzes. Each one tells you something:

| You hear | It means |
|---|---|
| A ping, a falling whoosh and two rising notes | The radio has switched on (only with KEY BEEP on) |
| A tiny tick | A key press (turn it off with MENU, KEY BEEP) |
| Two rising notes | Done: saved, stored, copied or unlocked |
| Two falling notes | Not possible: refused, empty, failed, or the keys are locked |
| Three slow falling notes | The battery is low |
| A ding-ding | Someone called while BELL was on (see the [Menu](Menu)) |
| A two-tone siren, again and again | A weather alert: any key stops it |
| A plain beep while you talk | 10 seconds left before the transmit timeout |

The volume knob sets how loud they are.
