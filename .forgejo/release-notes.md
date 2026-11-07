Open firmware for the TIDRADIO TD-H3 (not the TD-H3 Plus, a different radio inside that this firmware can't run on).

**To put it on your radio:**

1. **Download the flasher for your computer** from the files below. It's the only file you need:
   the firmware is inside it.

   | Computer | Download |
   |---|---|
   | Windows | tdh3-windows.exe |
   | Mac with an Apple chip (M1 or newer) | tdh3-macos-apple.zip |
   | Mac with an Intel chip | tdh3-macos-intel.zip |
   | Linux | tdh3-linux-x64 |
   | Raspberry Pi | tdh3-linux-arm64 |

2. **Plug the radio into the computer** with a USB-C cable.
3. **Open the flasher.** Windows: double-click it (if Windows says it protected your PC, click
   **More info**, then **Run anyway**). Mac: double-click the zip, then the flasher that comes out
   of it (the first time, go to **System Settings**, **Privacy & Security** and click **Open
   Anyway**). Linux: `chmod +x tdh3-linux-*`, then run it.
4. **Do what it says:** hold **PTT** when it tells you to.

It puts v{ver} on the radio in about a minute, and your channels and settings stay. Step by step,
with what to do if something goes wrong: [Updating the firmware](https://github.com/limehawk/td-h3-fw/wiki/Updating).

The firmware on its own, for other tools: td-h3-fw-{ver}.bin (receive and transmit) and
td-h3-fw-{ver}-rx.bin (receive only). SHA256SUMS lists each file's checksum, SHA256SUMS.sig is its
signature (what `tdh3 update` checks), and LICENSES.txt the
licences and notices for the firmware and the flasher.
