// Channel model, stock EEPROM layout, band plan, RF path switching, TX.
// Layout (TIDRADIO's map, confirmed by stock code and CHIRP tdh8.py minus its 8-byte ident):
//   channel n (1..199): 16 bytes at 0x10*n   rx[4] tx[4] rxtone[2] txtone[2] - flags13 flags14 -
//   name n: 8 bytes at 0x0D40 + 8*(n-1)       valid bit n-1 at 0x1900
// Frequencies are little-endian BCD in 10 Hz; tones are BCD 0.1 Hz, FFFF = none, bit 0x8000 = DCS.
#include "fw.h"

__xdata chan_t ch;
uint8_t sql_level;                          // 0-9, set by the UI
uint8_t wx_watch;                           // set by main.c
int8_t freq_cal;                            // TCXO trim, 0.1 ppm
static uint8_t cal_ok;                      // the factory TX power table at EEPROM 0x1F50 is there

#define MHZ(x) ((uint32_t)(x) * 100000UL)
#define NAME(n) (0x0D38 + 8 * (n))          // channel n's name: 0x0D40 + 8 * (n - 1)

// Where this firmware will transmit: the services switched on in TX BANDS (tx_bands, set from cfg).
// Everything else (aircraft, marine, public safety, government) is receive-only, no override.
// HAM 2 m and 70 cm; GMRS 462.550-462.725 and the 467 MHz repeater inputs on the 25 kHz raster;
// FRS the same 462 channels plus 467.5625-467.7125 (channels 8-14, 0.5 W); MURS its five channels.
uint8_t tx_bands;
#ifndef NO_TX
typedef struct { uint8_t b; uint32_t lo, span; } txr_t;   // lo to lo + span, for the services in b
static const __code txr_t txr[] = {
    {TXB_HAM, MHZ(144), MHZ(4)}, {TXB_HAM, MHZ(420), MHZ(30)}, {TXB_GMRS | TXB_FRS, 46255000, 17500},
    {TXB_MURS, 15182000, 0}, {TXB_MURS, 15188000, 0}, {TXB_MURS, 15194000, 0}, {TXB_MURS, 15457000, 0},
    {TXB_MURS, 15460000, 0},
};
#endif
uint8_t tx_allowed(uint32_t f) __reentrant
{
#ifdef NO_TX
    (void)f;
    return 0;
#else
    uint8_t b = tx_bands;
    for (const __code txr_t *r = txr; r != txr + sizeof txr / sizeof *txr; r++)
        if (b & r->b && f - r->lo <= r->span) return 1;   // one compare: f - lo wraps high under lo
    if (f - 46755000UL <= 17500) {        // 467: GMRS inputs on the 25 kHz raster, FRS 8-14 halfway between
        uint16_t m = f - 46755000UL;
        while (m >= 2500) m -= 2500;      // % 2500 (0-17500 here) without the modulo routine
        if (b & TXB_GMRS && !m || b & TXB_FRS && m == 1250) return 1;
    }
    return 0;
#endif
}

uint32_t bcd32(const __xdata uint8_t *b) __reentrant
{
    uint32_t v = 0;
    for (int8_t i = 3; i >= 0; i--) v = v * 100 + (uint8_t)((b[i] >> 4) * 10 + (b[i] & 15));   // two digits: 0-165
    return v;
}

// FFFF none; 0x80 in the high byte = DCS normal, 0xC0 = DCS inverted, digits octal in BCD
// ("23 80" = D023N); otherwise CTCSS BCD in 0.1 Hz ("41 25" = 254.1).
static uint16_t tone(const __xdata uint8_t *b) __reentrant
{
    uint8_t d2 = b[1] & 15, d1 = b[0] >> 4, d0 = b[0] & 15;
    if (b[1] == 0xFF) return 0;
    if (b[1] & 0x80) {
        if ((d2 | d1 | d0) > 7) return 0;   // a digit over 7: not octal
        return (uint16_t)(0x80 | b[1] & 0x40 | d2 >> 2) << 8 | (uint8_t)(d2 << 6 | d1 << 3 | d0);   // TONE_DCS, TONE_INV, 9 bits
    }
    return (uint16_t)(uint8_t)((b[1] >> 4) * 10 + d2) * 100 + (uint8_t)(d1 * 10 + d0);   // b[1] < 0x80 here: 0-85, 0-165
}

// The used (0x1900) and scan-list (0x1920) bitmaps, read once: a scan step used to read 50 bytes.
// ee_write clears maps_ok when a write touches them. They live in the screen buffer (fw.h), so every
// full-screen mode clears maps_ok too; the next channel step reads them again.
#define used scr_buf
#define list (scr_buf + 25)
// One record buffer for chan_save and chan_load (a 16-byte channel record, then its 8-byte name) and
// the single bytes the rest read and write: they never run nested. In the screen buffer after the
// bitmaps: no full-screen mode loads or saves a channel, steps channels or transmits.
#define rec (scr_buf + 50)
__bit maps_ok;
static uint8_t maps(void)
{
    if (!maps_ok) maps_ok = ee_read(0x1900, used, 25) && ee_read(0x1920, list, 25);
    return maps_ok;
}

static uint8_t bit(const __xdata uint8_t *map, uint8_t n) __reentrant   // map: used or list
{
    return --n < 199 && maps() && (map[n >> 3] >> (n & 7) & 1);   // n 1-199
}
#define is_valid(n) bit(used, n)

static void set_bit(uint16_t map, uint8_t n, uint8_t on) __reentrant
{
    uint8_t m = 1 << (--n & 7);
    map += n >> 3;
    if (!ee_read(map, rec, 1)) return;
    rec[0] = on ? rec[0] | m : rec[0] & ~m;
    ee_write(map, rec, 1);
}

// Scan list: stock's scan-add bitmap at 0x1920.
uint8_t chan_in_scan(uint8_t n) __reentrant { return bit(list, n); }
uint8_t chan_used(uint8_t n) __reentrant { return is_valid(n); }
void chan_scan_toggle(uint8_t n) __reentrant { set_bit(0x1920, n, !chan_in_scan(n)); }

uint8_t chan_free(void)                     // first unused channel, 0 if all 199 are taken
{
    if (!maps()) return 0;
    for (uint8_t n = 0; n < 199; n++)
        if (!(used[n >> 3] >> (n & 7) & 1)) return n + 1;
    return 0;
}

void bcd_put(__xdata uint8_t *b, uint32_t v, uint8_t n) __reentrant
{
    while (n--) { uint32_t q = v / 100; uint8_t r = (uint8_t)v - (uint8_t)q * 100; *b++ = r % 10 | (r / 10) << 4; v = q; }
}

static void tone_put(__xdata uint8_t *b, uint16_t t) __reentrant   // b is FF FF already: no tone
{
    if (!t) return;
    if (t & TONE_DCS) {                     // octal digits 2, 1, 0 from bits 8:6, 5:3, 2:0
        uint8_t lo = t, hi = t >> 8;
        b[0] = lo << 1 & 0x70 | lo & 7;
        b[1] = 0x80 | hi & 0x40 | (hi << 2 | lo >> 6) & 7;   // 0x40: TONE_INV
        return;
    }
    bcd_put(b, t, 2);
}

static uint8_t rec_write(uint8_t n) __reentrant { return ee_write(0x10 * n, rec, 16); }   // channel n's 16 bytes from rec
static uint8_t rec_read(uint8_t n) __reentrant { return ee_read(0x10 * n, rec, 16); }     // and into rec

// Power in byte 14 bits 5:4: 0 low, 1 high (stock, CHIRP), 2 mid, 3 ultra-low (ours). CHIRP's
// TD-H3 driver knows only Low and High and shows 2 and 3 as Low.
static const __code uint8_t pwr_bits[4] = {3, 0, 2, 1}, bits_pwr[4] = {PWR_LOW, PWR_HIGH, PWR_MID, PWR_ULP};

// Write `ch` as channel n in stock's format, mark it used and in the scan list.
uint8_t chan_save(uint8_t n)
{
    uint8_t i;
    if (n < 1 || n > 199) return 0;
    xset(rec, 0xFF, 24);                    // no TX, no tones, the name's blank tail
    bcd_put(rec, ch.rx, 4);
    if (ch.tx) bcd_put(rec + 4, ch.tx, 4);
    tone_put(rec + 8, ch.rx_tone);
    tone_put(rec + 10, ch.tx_tone);
    rec[12] = 0;
    rec[13] = ch.bcl << 2 | (ch.mode & 2) << 2 | GRP_OURS;   // bits 1:0 01: byte 15 is ours, FF all eight groups
    rec[14] = ch.narrow << 3 | (ch.mode & 1) << 2 | pwr_bits[ch.power] << 4;
    rec[15] = ch.groups;
    for (i = 0; i < 8 && ch.name[i]; i++) rec[16 + i] = ch.name[i];
    if (!rec_write(n) || !ee_write(NAME(n), rec + 16, 8)) return 0;
    set_bit(0x1900, n, 1);
    set_bit(0x1920, n, 1);
    return 1;
}

// Step channel n's power (ULTRA LOW, LOW, MID, HIGH, round again) in its record; nothing else changes.
void chan_power(uint8_t n)
{
    static const __code uint8_t up[4] = {0x20, 0x30, 0x10, 0x00};   // bits 5:4 one step up: low, high, mid, ultra-low
    uint8_t b;
    if (!rec_read(n)) return;
    b = rec[14];
    rec[14] = b & 0xCF | up[b >> 4 & 3];
    rec_write(n);
}

// Empty channel n (1-199) as CHIRP does: its record all FF, its used bit off (name, scan bit stay).
uint8_t chan_delete(uint8_t n)
{
    xset(rec, 0xFF, 16);
    if (!rec_write(n)) return 0;
    set_bit(0x1900, n, 0);
    return 1;
}

uint8_t chan_load(uint8_t n)
{
    if (!is_valid(n) || !rec_read(n)) return 0;
    ch.rx = bcd32(rec);
    ch.tx = (rec[4] & rec[5] & rec[6] & rec[7]) == 0xFF ? 0 : bcd32(rec + 4);   // FF..: no TX (CHIRP "off")
    if (ch.rx - MHZ(18) > MHZ(1300 - 18)) return 0;   // erased or garbage: under 18 or over 1300 MHz
    ch.rx_tone = tone(rec + 8);
    ch.tx_tone = tone(rec + 10);
    ch.narrow = rec[14] >> 3 & 1;
    ch.bcl = rec[13] >> 2 & 1;                // CHIRP tdh8 `bcl`
    ch.groups = rec[15] == 0xFF && (rec[13] & 3) != GRP_OURS ? 0 : rec[15];   // byte 15 is unused by stock: our scan
                                            // groups. FF without our mark is a blank channel's (stock, CHIRP): none
    ch.power = bits_pwr[rec[14] >> 4 & 3];
    // The mode: bits 14.2 and 13.3 (MOD_*), unused by stock and CHIRP, ours only with our mark; 0 is FM, or AM on the
    // air band, 108-137 MHz (stock and CHIRP channels read so).
    ch.mode = (rec[13] & 3) == GRP_OURS ? rec[14] >> 2 & 1 | rec[13] >> 2 & 2 : 0;
    if (ch.mode > MOD_USB) ch.mode = 0;     // 3: no such mode
    if (!ch.mode && ch.rx - MHZ(108) < MHZ(137 - 108)) ch.mode = MOD_AM;
    name_read(NAME(n), ch.name);
    return 1;
}

uint8_t name_read(uint16_t a, __xdata char *s) __reentrant   // 8 name bytes at a into s, NUL after: its length
{
    uint8_t i;
    ee_read(a, (__xdata uint8_t *)s, 8);
    for (i = 0; i < 8 && (uint8_t)(s[i] - 0x20) < 0x5F; i++) ;   // printable: 0x20-0x7E
    s[i] = 0;
    return i;
}

// Next used channel: any, or only those on the scan list (inlist), or in any of these groups (bit
// g-1 for group g, channel byte 15).
uint8_t chan_next(uint8_t n, int8_t dir, uint8_t inlist, uint8_t groups) __reentrant
{
    if (!maps()) return 0;
    for (uint8_t i = 0; i < 199; i++) {
        if (dir > 0) { if (n >= 199) n = 0; n++; }   // 199 wraps to 1, 1 to 199
        else { if (n <= 1) n = 200; n--; }
        uint8_t j = n - 1, m = 1 << (j & 7);
        j >>= 3;
        if (!(used[j] & m)) continue;
        if (inlist && !(list[j] & m)) continue;
        // byte 15 FF is all eight groups when byte 13 marks it ours, none on stock's records
        if (groups && (!ee_read(0x10 * n + 13, rec, 3) || rec[2] == 0xFF && (rec[0] & 3) != GRP_OURS
                       || !(rec[2] & groups))) continue;
        return n;
    }
    return 0;
}

static void paths_off(void) { VRX_N = 1; URX_N = 1; TXV_N = 1; TXU_N = 1; }

void radio_rx_path(uint8_t uhf) __reentrant { paths_off(); if (uhf) URX_N = 0; else VRX_N = 0; }

// TCXO trim: f * freq_cal / 10^7 (0.1 ppm steps), done in two halves to stay inside 32 bits.
static uint32_t trim(uint32_t f) __reentrant
{
    uint32_t d = f / 100 * (uint8_t)(freq_cal < 0 ? -freq_cal : freq_cal) / 100000UL;
    if (freq_cal < 0) d = -d;               // f - d, as the sum wraps
    return f + d;
}

static uint16_t t_tuned;                    // when radio_tune last ran
static __bit settled;

// Right after a retune (a channel change, a dual-watch look, the battery saver waking it, a beep handing
// the chip back to the receiver) the BK4819
// reads its floor (-126 dBm) and a closed squelch until the receiver settles: seen on the radio as the
// meter dropping to nothing at every battery-saver wake. Settled, as bk_dbm_at decides it, is the
// glitch count (REG_63) under 200; 50 ms at most, if it never drops.
void rx_unsettle(void) { t_tuned = now(); settled = 0; }

uint8_t rx_settled(void)
{
    if (!settled && ((bk_read(0x63) & 0xFF) < 200 || (uint16_t)(now() - t_tuned) >= 10)) settled = 1;
    return settled;
}

void radio_tune(void)
{
    uint8_t vhf = ch.rx < MHZ(300), ssb = ch.mode == MOD_USB, tail;   // stock splits bands at 300 MHz
    if (ssb) ch.rx_tone = 0;                // USB: no tone squelch, no 55 Hz tail detector (speech would set it off)
    // The 55 Hz tail detector with no RX tone of its own; in weather mode with WX ALERT on, the 1050 Hz alert
    // detector instead, which main.c's peeks reads; neither on USB.
    tail = !ch.rx_tone && !ssb && !wx_watch;
    radio_rx_path(!vhf);
    bk_freq(trim(ch.rx));
    bk_mode(ch.mode, ch.narrow);
    bk_tone(ch.rx_tone ? ch.rx_tone : tail ? 670 : ssb ? 0 : TONE_1050); // no RX tone: CTCSS on at 67.0 Hz (DualTachyon, Apache-2.0: bk4819.c) for the tail detector
    bk_squelch(sql_level, vhf, ch.narrow);
    bk_rx_on();
    bk_dtmf_on(tail);
    bk_af(0);
    rx_unsettle();
}

void radio_init(void)
{
    cal_ok = ee_read(0x1FF0, rec, 1) && rec[0] == 0x85;
}

// Stock's power lookup: VHF/220/UHF bands, 10 MHz segments, low/high tables. Returns the APC
// duty, or 0xFF for no TX. Ultra-low power is APC 0: the driver stage alone.
// MID is ours: the whole part of the square root of LOW x HIGH, the segment's two factory duties'
// geometric mean (the PA saturates with bias, so their plain average would sit near HIGH).
// The segment's two bytes are read from the factory table (EEPROM 0x1F50, read only) at each key-up: no RAM copy.
#ifndef NO_TX
static uint8_t tx_duty(uint32_t f) __reentrant
{
    uint16_t m = f / 100000UL;              // whole MHz: every edge below is one
    uint8_t seg = (uint8_t)(m / 10) % 10u, d, lo;   // 8 bits hold every TX band's 13-59
    if (m >= 136 && m < 176) {
        if (seg < 3) seg = 3;
        seg -= 3;
    } else if (m >= 200 && m < 350) {
        seg = 13;
    } else if (m >= 350 && m < 600) {
        if (m < 400) seg = 0;
        else if (m >= 480) seg = 7;
        seg += 5;
    } else return 0xFF;
    if (!ee_read(0x1F50 + seg, rec, 17)) return 0xFF;
    lo = rec[0]; d = rec[16];               // the segment's LOW and HIGH
    if ((uint8_t)(lo - 10) >= 190 || (uint8_t)(d - 10) >= 190) return 0xFF;   // 10-199 only: a blank or corrupt table never keys the PA
    if (ch.power == PWR_MID) {
        uint16_t p = lo * d;                // under 200 x 200
        for (d = lo; (uint16_t)(d + 1) * (d + 1) <= p; d++) ;
    }
    return ch.power == PWR_ULP ? 0 : ch.power == PWR_LOW ? lo : d;
}
#endif

uint8_t radio_tx(uint8_t on) __reentrant
{
#ifdef NO_TX
    if (on) return 0;
#else
    if (on) {
        uint8_t d = cal_ok ? tx_duty(ch.tx) : 0xFF;
        if (ch.mode || !tx_allowed(ch.tx) || d == 0xFF) return 0;   // FM only: never AM or USB
        AMP_ON = 0;
        bk_af(0);
        paths_off();
        bk_freq(trim(ch.tx));
        bk_tone(ch.tx_tone);
        bk_tx_on();
        if (ch.tx < MHZ(300)) TXV_N = 0; else TXU_N = 0;
        delay_ms(10);
        apc(d);
        return 1;
    }
#endif
    if (!(bk_opt & BKO_NOTAIL)) {           // stock 0xCCCB: lets other radios close their squelch cleanly
        bk_write(0x50, 0xBB20);             // mic muted
        if (ch.tx_tone & TONE_DCS) { bk_write(0x07, 2775); bk_write(0x51, 0x9040); }
        else if (bk_opt & BKO_REVERSE && ch.tx_tone) bk_write(0x52, 0xC310);   // reverse burst: the tone's phase turned
                                            // 180 deg (bits 15:13), stock's thresholds kept (F4HWN's BK4819_GenTail(2))
        else { bk_write(0x51, 0x903E); bk_write(0x07, 0x046F); }   // 55 Hz: 0x046F = 55 x 20.64888
        delay_ms(250);
    }
    apc(0);
    delay_ms(20);
    radio_tune();
    return 0;
}

void radio_off(void)
{
    apc(0);
    paths_off();
    AMP_ON = 0;
    bk_sleep();
}
