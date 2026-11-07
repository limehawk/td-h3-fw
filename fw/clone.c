// PC LINK: TIDRADIO's clone protocol on the USB-C port (38400 8N1), so CHIRP's
// TD-H3 driver can read and write channels. Our own code; the protocol's facts come from nicsure's
// protocol.txt notes and CHIRP chirp/drivers/tdh8.py:
//   PVOJH\x5C\x14 -> 06    02 -> 8-byte ident    06 -> 06
//   R AH AL 20 -> W AH AL 20 [32] sum      W AH AL 20 [32] sum -> 06      E -> 06
// Runs as a blocking loop: the UART has a one-byte buffer, so polling it from the
// UI loop would drop bytes. Open until V/M, with no timeout. With Bluetooth on, the same
// protocol is served on UART0 at 9600: stock's BT receive path expects this hello.
#include "fw.h"

static const __code uint8_t hello[7] = {0x50, 0x56, 0x4F, 0x4A, 0x48, 0x5C, 0x14};
static const __code uint8_t ident[8] = {'P', '3', '1', '1', '8', '3', 0xFF, 0xFF};

static uint8_t link, bt;                    // port that last spoke; Bluetooth enabled
static __bit cfg_hit;                       // the computer wrote the settings' first block (CFG_ADDR, their magic)

static int16_t get(void)                    // -1 after 100 ms of nothing
{
    uint16_t t0 = now();
    int16_t c;
    for (;;) {
        if ((c = uart_get(PORT_USB)) >= 0) { link = PORT_USB; return c; }
        if (bt && (c = uart_get(PORT_BT)) >= 0) { link = PORT_BT; return c; }
        WDT_KICK();
        if ((uint16_t)(now() - t0) > 20) return -1;
    }
}

static void put(uint8_t c) __reentrant { uart_put(link, c); }

#define blk scr_buf                       // one 32-byte block, in the screen buffer (fw.h)

static void block(uint8_t cmd) __reentrant
{
    int16_t c;
    uint8_t ah, al, i, sum = 0;
    if ((c = get()) < 0) return; ah = c;
    if ((c = get()) < 0) return; al = c;
    if (get() != 0x20 || (al & 31) || ah > 0x1F) return;
    uint16_t a = (uint16_t)ah << 8 | al;

    if (cmd == 'R') {
        if (!ee_read(a, blk, 32)) return;
        put('W'); put(ah); put(al); put(0x20);
        for (i = 0; i < 32; i++) { put(blk[i]); sum += blk[i]; }
        put(sum);
        return;
    }
    for (i = 0; i < 32; i++) {
        if ((c = get()) < 0) return;
        blk[i] = c; sum += c;
    }
    if (get() != sum) return;
    // Halves that hit the factory calibration or stock's magic are skipped
    // (ee_write refuses them); CHIRP still gets its ack.
    ee_write(a, blk, 16);
    ee_write(a + 16, blk + 16, 16);
    if (a == 0x1FC0) cfg_hit = 1;
    put(0x06);
}

void clone_session(uint8_t bt_on) __reentrant
{
    uint8_t h = 0, i;
    int16_t c;
    bt = bt_on;
    maps_ok = 0;                            // the screen buffer holds radio.c's bitmaps too (fw.h)
    cfg_hit = 0;
    while (key_scan() != K_EXIT) {          // open till V/M: no timeout, so cables and CHIRP can take their time
        if ((c = get()) < 0) continue;
        if (c == hello[h]) {
            if (++h == 7) { put(0x06); h = 0; }
            continue;
        }
        h = c == hello[0];
        if (c == 0x02) for (i = 0; i < 8; i++) put(ident[i]);
        else if (c == 0x06 || c == 'E') {
            put(0x06);
            // Settings written (tdh3 reset, CHIRP): start afresh, as at power-on, so the ones in
            // memory can't be saved back over them. Only E comes after a W (0x06 is the handshake's),
            // so cfg_hit alone picks it. No key held: the loader goes straight on to the app.
            if (cfg_hit) remote_loader();
        }
        else if (c == 'R' || c == 'W') block(c);
    }
    while (key_scan() == K_EXIT) WDT_KICK();   // let go of V/M before the UI sees it
}
