// FM broadcast on the RDA5807M (U33). Bus: SDA P3.6, SCL P3.7, write address 0x20.
// Everything here is stock's sequence: soft reset (REG02=0002), the 36-register table
// at 0xDA05 written from REG02, then REG02=D2E1 + REG03=channel to tune (0xE7DD).
// REG03's band is 76-108 MHz with 100 kHz spacing, so channel = MHz*10 - 760.
// The MCU cannot read P3.6 back on this board (it reads 0 even right after driving it high,
// seen on the radio 2026-11-03), so nothing is read from the RDA5807: no RDS, stereo flag or
// chip seek. Signal strength and seek come from the BK4819, which hears broadcast FM on its
// VHF path (a 103.5 station read ~11 dB over the floor, a strong one ~45 dB).
#include "fw.h"

#define SDA_OUT() (P3TRIS = 0xEF)
#define SDA_IN()  (P3TRIS = 0xAF)

static const __code uint8_t table[72] = {
    0x92,0xE1, 0x00,0x08, 0x04,0x00, 0xC3,0xAD, 0x60,0x00, 0x42,0x12, 0x00,0x00, 0x00,0x00,
    0x00,0x00, 0x00,0x00, 0x00,0x00, 0x00,0x00, 0x00,0x00, 0x00,0x00, 0x00,0x00, 0x00,0x19,
    0x2A,0x11, 0xB0,0x42, 0x2A,0x11, 0xB8,0x31, 0xC0,0x00, 0x2A,0x91, 0x94,0x00, 0x00,0xA8,
    0xC4,0x00, 0xF7,0xCF, 0x12,0x14, 0x80,0x6F, 0x46,0x08, 0x00,0x86, 0x06,0x61, 0x00,0x00,
    0x10,0x9E, 0x23,0xC8, 0x04,0x06, 0x0E,0x1C,
};

static void start(void)
{
    SDA_OUT(); FM_SDA = 1; FM_SCL = 1; udelay();
    FM_SDA = 0; udelay(); FM_SCL = 0;
}

static void stop(void)
{
    SDA_OUT(); FM_SCL = 0; FM_SDA = 0; udelay();
    FM_SCL = 1; udelay(); FM_SDA = 1;
}

static void tx(uint8_t b) __reentrant
{
    SDA_OUT();
    for (uint8_t i = 0; i < 8; i++, b <<= 1) {
        FM_SCL = 0; FM_SDA = b & 0x80; udelay();
        FM_SCL = 1; udelay();
    }
    FM_SCL = 0; SDA_IN(); udelay();         // ACK clock; the chip's answer is not needed
    FM_SCL = 1; udelay();
    FM_SCL = 0;
}

static void regs(const __code uint8_t *d, uint8_t n) __reentrant   // sequential write from REG02
{
    start();
    tx(0x20);
    while (n--) tx(*d++);
    stop();
}

// The chip keeps playing through an MCU restart (heard on the radio 2026-11-03: a station
// came through every time the speaker amp switched on), so boot powers it down too.
void fm_off(void)
{
    static const __code uint8_t off[2] = {0x00, 0x00};
    regs(off, 2);
}

// The FM screen's working buffers, in the screen buffer after the presets (pre, below): only fm_mode
// and what it calls use them, and none of them nest.
#define entry ((__xdata char *)scr_buf + 50)      // 12: fm_mode's typed MHz or message
#define fm_s8 ((__xdata char *)scr_buf + 62)      // draw's frequency text
#define fm_s6 ((__xdata char *)scr_buf + 70)      // draw_presets' cell text
#define fm_b (scr_buf + 76)                       // 4: an RDA5807 write or a preset's BCD bytes
#define score (scr_buf + 80)                      // 100: auto presets' score of each channel, 88.1-107.9

static void tune(uint16_t f) __reentrant
{
    uint16_t r3 = (f - 760) << 6 | 0x10 | 0x08;  // channel, TUNE, band 76-108
    start();
    tx(0x20); tx(0xD2); tx(0xE1); tx(r3 >> 8); tx(r3);   // regs() from REG02: 02 = D2E1, 03 = r3
    stop();
}

// ---- signal and seek, measured by the BK4819 while the RDA5807 plays ----
static int8_t floor_db;                     // band noise floor, dBm

static void bk_listen(uint8_t on) __reentrant           // BK4819 awake on the VHF path with its audio muted, or asleep
{
    if (on) { radio_rx_path(0); bk_mode(0, 0); bk_rx_on(); bk_af(0); }   // wide FM: the weather look's mode
    else radio_off();
}

static int8_t dbm(uint16_t f) __reentrant   // 100 kHz -> 10 Hz; under -128 dBm (the chip reads to -160) as -128, not +127
{
    int16_t d = bk_dbm_at((uint32_t)f * 10000);
    return d < -128 ? -128 : d;
}

// Weather alert (MENU WX ALERT), as the main screen's: every 5 s, 250 ms on the weather channel
// for the 1050 Hz tone. The RDA5807 plays on; the next signal reading tunes the BK4819 back.
// Out of fm_mode's own locals: there they cost 700 bytes of spills.
static uint8_t wx_at;                       // MENU WX ALERT: weather channel 1-7, 0 = off
static uint8_t wx_looks;

static uint8_t wx_tick(void)                // twice a second: 1 = the alert is on
{
    if (!wx_at || wx_at > 7 || ++wx_looks < 10) return 0;
    wx_looks = 0;
    bk_dbm_at(wx_freq(wx_at - 1));
    bk_tone(TONE_1050);
    for (uint8_t i = 0; i < 25; i++) { delay_ms(10); if (bk_tone_heard(TONE_1050)) return wx_at = 0x80; }   // heard: 0x80 tells fm_mode's caller
    return 0;
}

// Seek walks the US broadcast raster: 88.1-107.9 MHz on odd tenths. Fine tune (* #) and typed
// frequencies still reach all of 76-108.
#define SEEK_LO 881
#define SEEK_HI 1079

static void find_floor(void) __reentrant                // the quietest of a sample across the band
{
    floor_db = 0;
    for (uint16_t f = SEEK_LO; f <= SEEK_HI; f += 22) { int8_t d = dbm(f); if (d < floor_db) floor_db = d; }
}

static uint16_t next_odd(uint16_t g, uint8_t up) __reentrant    // next channel on the raster, wrapping
{
    uint8_t s = 1 + (g & 1);                // to the next odd tenth
    if (up) g += s; else g -= s;
    if ((uint16_t)(g - SEEK_LO) > SEEK_HI - SEEK_LO) g = up ? SEEK_LO : SEEK_HI;   // off either end: wrap
    return g;
}

static int8_t away(uint16_t g, uint8_t up, uint8_t n) __reentrant   // the reading n channels up or down the raster
{
    while (n--) g = next_odd(g, up);
    return dbm(g);
}

// Next station: 10 dB over the band floor and 3 dB over the channels 0.4 MHz either side (a
// station stands out; the floor's humps don't; a strong one spreads over the channel beside it,
// so that one isn't the yardstick), and no weaker than the next channel up, so it lands on the
// peak. One lap of the band; returns f if nothing qualifies.
// ponytail: thresholds from one evening on the radio (103.5 at +11 dB, humps at +8), retune here
static uint16_t seek(uint16_t f, uint8_t up) __reentrant
{
    uint16_t g = f;
    for (uint8_t n = 0; n < 100; n++) {
        g = next_odd(g, up);
        int8_t d = dbm(g);
        if (d >= floor_db + 10 && d >= away(g, 1, 2) + 3 && d >= away(g, 0, 2) + 3
            && d >= away(g, 1, 1)) return g;   // over channels 0.4 MHz away, and the peak of its spread
        WDT_KICK();
    }
    return f;
}

// Presets 1-25, stock's fmmode[25] (CHIRP tdh8): 4 bytes little-endian BCD, MHz x 10, FF = empty.
// The keys show them 9 at a time: page 0 is 1-9, page 1 10-18, page 2 19-25.
#define NPRE 25
#define PRESET(n) (0x0CCC + (uint8_t)((n) << 2))  // 0x0CD0 + 4 * (n - 1); n 1-25 keeps the shift in 8 bits

static uint16_t preset_get(uint8_t n) __reentrant
{
    __xdata uint8_t *b = fm_b;
    uint32_t v;                             // all 4 bytes: cut to 16 bits, junk could land in the band
    if (!ee_read(PRESET(n), b, 4) || b[0] == 0xFF) return 0;
    v = bcd32(b);
    return v >= 760 && v <= 1080 ? v : 0;
}

static void preset_put(uint8_t n, uint16_t f) __reentrant   // f 0: empty (FF, as stock leaves it)
{
    __xdata uint8_t *b = fm_b;
    if (f) bcd_put(b, f, 4); else b[0] = b[1] = b[2] = b[3] = 0xFF;
    ee_write(PRESET(n), b, 4);
    // CHIRP shows (and on upload keeps) only presets whose bit is set in fmusedflags, a
    // little-endian bitmap at 0x1940 (tdh8 MEM_FORMAT_H3): keep this preset's bit in step.
    n--;
    uint16_t a = 0x1940 + (n >> 3);
    uint8_t m = 1 << (n & 7);
    ee_read(a, b, 1);
    b[0] = f ? b[0] | m : b[0] & ~m;
    ee_write(a, b, 1);
}

// ---- screen: frequency, signal, a tuning dial with the presets on it, the preset grid ----
//  y 0  FM RADIO                P1      y 50 dial 76-108 MHz, needle, preset dots
//  y 12 101.9 MHz (x3)                  y 74 this page of presets, three rows
//  y 40 signal bar                      y 120 help or message
#define pre ((__xdata uint16_t *)scr_buf)  // presets 1-25, 0 = empty: read from EEPROM each time FM opens (screen buffer, fw.h)

#define freq_s(s, f) dec(s, f, 4, 1)          // "101.9", " 88.5"

static uint8_t dial_x(uint16_t f) __reentrant { f -= 760; return 4 + (uint8_t)((f + f + f) >> 3); }   // 76-108 over 120 px: * 3 / 8 by adds

static void draw_dial(uint16_t f) __reentrant
{
    uint8_t i;
    lcd_fillp(0, 48, W, 24, P_BG);
    lcd_fillp(4, 60, 121, 1, P_DIM);                      // the band
    for (i = 0; i <= 16; i++) {                           // a tick every 2 MHz, taller every 10
        uint8_t t = i % 5 == 2 ? 2 : 0;
        lcd_fillp(4 + i * 15 / 2, 57 - t, 1, 3 + t, P_DIM);
    }
    static const __code label_t l[] = {{13, 64, 1, P_DIM, "80"}, {50, 64, 1, P_DIM, "90"}, {85, 64, 1, P_DIM, "100"}};
    draw_labels(l, 3);
    for (i = 0; i < NPRE; i++) if (pre[i]) lcd_fillp(dial_x(pre[i]), 62, 1, 2, P_OK);   // presets
    lcd_fillp(dial_x(f), 49, 1, 13, P_ACC);               // the needle
}

static uint8_t pg;                          // the preset page on screen, 0-2
static __xdata uint16_t f;                  // the station playing, 100 kHz

// The page's slots top right ("M10-18"), the slot playing now beside the big frequency ("M12"),
// and a 3 x 3 laid out like the keypad: each cell a lit key to press (x+0-6), then its station
// (x+10-39); the cells 44 px apart.
static void draw_presets(void)              // f: the file's, the station playing
{
    static const __code char *const __code slots[3] = {"  M1-9", "M10-18", "M19-25"};
    __xdata char *s = fm_s6;
    __xdata uint16_t *p = pre;
    uint8_t i, x = 0, y = 76;
    lcd_text(92, 0, slots[pg], 1, P_ACC);
    for (i = 0; i < NPRE && *p != f; i++, p++) ;
    s[0] = s[1] = s[2] = s[3] = ' '; s[4] = 0;   // "M3 ", "M12 ", or blank off every preset
    if (i < NPRE) { s[0] = 'M'; *dec(s + 1, i + 1, i < 9 ? 1 : 2, 0) = ' '; }
    lcd_text(100, 12, s, 1, P_ACC);
    lcd_fillp(0, 75, W, 33, P_BG);
    p = pre + (uint8_t)(pg * 9);                    // 8 bits: mul ab
    for (i = 0; i < 9 && p < pre + NPRE; i++, p++) {
        uint16_t v = *p;
        uint8_t ki = v == f ? P_ACC : P_DIM;
        uint16_t k = pal[ki];
        lcd_fill(x, y - 1, 7, 9, k);                // the key
        s[0] = '1' + i; s[1] = 0;
        lcd_text_bg(x + 1, y, s, 1, c_bg, k);
        v = *p;                                     // read again: held over the calls above it costs more
        if (v) freq_s(s, v);
        lcd_text(x + 10, y, v ? (const char *)s : " --.-", 1, v == f ? P_ACC : v ? P_FG : P_DIM);
        x += 44;                                    // not "(x += 44) > 88": SDCC compares that signed
        if (x > 88) { x = 0; y += 11; }
    }
}

static void draw_signal(uint16_t f) __reentrant         // dB over the band floor, 0-50 over 100 px
{
    int8_t over = dbm(f) - floor_db;
    uint8_t len = over <= 0 ? 0 : over >= 50 ? 100 : over * 2;
    lcd_fillp(14, 41, len, 3, P_OK);
    lcd_fillp(14 + len, 41, 100 - len, 3, P_BAR);
}

static void frame(void)                     // the parts that never change
{
    static const __code label_t l[] = {{11, 0, 1, P_ACC, "FM RADIO"}, {0, 40, 1, P_DIM, "S"},
                                       {0, BOT_Y - 10, 1, P_DIM, "0 page  *# fine"}};   // the keys, with help below
    lcd_clear();
    draw_labels(l, 3);
    lcd_icon(0, 0, icon_note, P_ACC);
}

static void draw(const char *msg, uint8_t mc) __reentrant   // mc: a P_* index
{
    __xdata char *s = fm_s8;
    freq_s(s, f);
    lcd_text(4, 12, s, 3, P_FG);
    lcd_text(100, 26, "MHz", 1, P_DIM);
    draw_dial(f);
    draw_presets();
    lcd_fillp(0, BOT_Y, W, 8, P_BG);
    lcd_text(0, BOT_Y, msg, 1, mc);
}

#define help "UP/DN seek  V/M back"         // one copy: SDCC merges a file's equal literals
static void hint(uint8_t typing) __reentrant { draw(typing ? "type MHz, MENU ok" : help, typing ? P_HI : P_DIM); }   // the keys, or the typing prompt

static void load_presets(void) { for (uint8_t i = 0; i < NPRE; i++) pre[i] = preset_get(i + 1); }

static __xdata char *say(const __code char *m, uint8_t v) __reentrant   // entry = m then v ("SAVED M12"), copied: no initialized RAM
{
    uint8_t i;
    for (i = 0; (entry[i] = m[i]); i++) ;
    dec(entry + i, v, v < 10 ? 1 : 2, 0);
    return entry;
}

// Auto presets (hold 0, then 0 to confirm), as Yaesu's broadcast auto-memory and car-radio
// autostore: three passes over 88.1-107.9, then the 25 best-scoring stations to presets 1-25 in
// frequency order; the other presets are emptied. A channel's score is its weakest reading of the
// three, in dB over the band floor: a real station holds up every pass, a noise spike or a flicker
// doesn't. Then a channel goes if it scores under 10 dB or a channel 0.2 MHz away scores 3 dB more
// (its splash). Any key stops it before anything is written. Returns the frequency to play: preset 1, or f.
#define CHANS ((SEEK_HI - SEEK_LO) / 2 + 1)
#define TAKEN 0xFF                          // score: picked (a reading is at most 128 dB over the floor)

static void auto_presets(void)
{
    uint8_t n, i, j, p, k, v, prev;
    __xdata uint8_t *q;
    draw("AUTO PRESETS? 0 = YES", P_HI);
    for (j = 0; !(i = key_event()); )       // the weather alert look goes on while it asks
        if (delay_ms(10), ++j == 50) { j = 0; if (wx_tick()) return; }
    if (i == K_0) {
        for (p = 1; p <= 3; p++)
            for (i = j = 0, q = score; i < CHANS; i++, q++) {
                int8_t d = dbm(SEEK_LO + 2 * i);
                v = d > floor_db ? d - floor_db : 0;   // 1-255 over the floor: fits 8 bits
                if (p == 1 || v < *q) *q = v;
                if (*q >= 10 && j < 99) j++;
                draw_dial(SEEK_LO + 2 * i);         // the needle, "PASS 2/3 FOUND 14"
                say("PASS 1/3 FOUND ", j)[5] = '0' + p;
                lcd_text(0, BOT_Y, entry, 1, P_HI);
                WDT_KICK();
                if ((k = key_scan())) {             // stopped: wait for the key to go up so it does nothing else (PTT goes on to leave FM)
                    while (k && k < K_PTT) { delay_ms(10); k = key_scan(); }
                    goto done;
                }
            }
        for (i = prev = 0, q = score; i < CHANS; i++, q++) {   // floor and splash, against the scores as read
            v = *q;
            uint8_t w = i < CHANS - 1 ? q[1] : 0;    // the next channel's score; none past the top
            if (v < 10 || prev > v && (uint8_t)(prev - v) >= 3 || w > v && (uint8_t)(w - v) >= 3) *q = 0;   // 3 dB under either: 8 bits, no wrap
            prev = v;
        }
        for (n = 0; n < NPRE; n++) {                // the best 25, marked TAKEN
            for (i = 0, j = 0xFF, v = 0, q = score; i < CHANS; i++, q++)
                if (*q != TAKEN && *q > v) { v = *q; j = i; }
            if (j == 0xFF) break;
            score[j] = TAKEN;
        }
        if (n) {                                    // in frequency order
            xset(pre, 0, 2 * NPRE);                 // the rest empty
            for (i = j = 0, q = score; i < CHANS; i++, q++) if (*q == TAKEN) pre[j++] = SEEK_LO + 2 * i;
            for (i = 0; i < NPRE; i++) preset_put(i + 1, pre[i]);
            tune(f = pre[0]);
            pg = 0;
            bk_chime(snd_ok);
            draw(say("SAVED ", n), P_OK);
        } else {
            bk_chime(snd_no);
            draw("NO STATIONS FOUND", P_RED);
        }
        return;
    }
done:
    hint(0);                                    // not now, or stopped: the keys again
}

uint8_t fm_mode(__xdata uint16_t *khz100, uint8_t wx)
{
    static const __code uint8_t reset[2] = {0x00, 0x02};
    uint8_t n = 0, typing = 0, k, d;
    uint16_t t_sig = 0;
    f = *khz100;

    if (f < 760 || f > 1080) f = 1019;
    wx_at = wx; wx_looks = 0;
    maps_ok = 0;                            // the screen buffer holds radio.c's bitmaps too (fw.h)
    bk_listen(1);
    pg = 0;
    load_presets();
    regs(reset, 2);
    delay_ms(50);
    regs(table, 72);
    delay_ms(200);
    tune(f);
    AMP_ON = 1;
    find_floor();
    frame();
    hint(0);

    for (;;) {
        delay_ms(10);
        if (remote_req & REQ_REDRAW) {      // a watching computer asks for the whole screen
            remote_req &= ~REQ_REDRAW;
            frame();
            hint(typing);
        }
        uint16_t t = now();
        if (t - t_sig >= 100) {             // twice a second
            t_sig = t;
            if (wx_tick()) break;           // the main screen sounds the alert
            draw_signal(f);
        }
        k = lights(key_event());            // LIGHT's timeout and the flashlight, as on the home screen
        if ((uint8_t)(key_held - K_PTT) < 2 || k == K_EXIT && !typing) break;   // PTT or PTT-B: leave, main transmits
        if (!k) continue;
        d = (k & ~K_LONG) - K_0;            // digit 0-9 when k is a digit key
        if (typing) {                       // MENU, then "1019" or "885": 101.9 / 88.5 MHz
            if ((uint8_t)(k - K_0) < 10 && n < 4) { __xdata char *e = entry + n++; e[0] = '0' + d; e[1] = 0; }
            if (k == K_EXIT) { if (n) entry[--n] = 0; else typing = 0; }
            if (k == K_MENU || n == 4) {
                uint16_t v = 0;
                for (uint8_t i = 0; i < n; i++) v = v * 10 + entry[i] - '0';
                if (v >= 760 && v <= 1080) { f = v; tune(f); }
                typing = 0;
            }
            if (typing && n) draw(entry, P_HI);   // entry[n] is 0 already: the digit or EXIT put it there
            else hint(typing);
            continue;
        }
        if (k == (K_0 | K_LONG)) { auto_presets(); if (wx_at & 0x80) break; continue; }   // hold 0: auto presets
        if (k == K_MENU) { typing = 1; n = 0; hint(1); continue; }
        if (k == K_0) { if (++pg > 2) pg = 0; hint(0); continue; }   // 0: next page of presets
        if ((uint8_t)(d - 1) < 9) {             // d 1-9
            d += pg * 9;                    // the preset number, 1-25
            if (d > NPRE) { chime_no(); continue; }   // page 3's 8 and 9: no such preset
        } else d = 0;                       // not a digit: no preset, held or not
        if ((k & K_LONG) && (uint8_t)(d - 1) < NPRE) {      // hold 1-9: save here; on its own station, clear it
            __xdata uint16_t *p = pre + (uint8_t)(d - 1);
            uint16_t v = *p == f ? 0 : f;   // f is never 0: v 0 = cleared
            preset_put(d, v);
            *p = v;
            draw(say(v ? "SAVED M" : "CLEARED M", d), P_OK);   // "SAVED M3" / "CLEARED M12"
            chime_ok();
            continue;
        }
        if ((uint8_t)(k - K_1) < 9) {                    // 1-9: go to the preset
            uint16_t v = preset_get(d);
            if (!v) { draw("EMPTY: HOLD TO SAVE", P_RED); chime_no(); continue; }
            f = v;
        } else if (k == K_UP || k == K_DOWN) {           // seek
            draw("SEEKING...", P_HI);
            f = seek(f, k == K_UP);
        } else if (k == K_STAR) f = f >= 1080 ? 760 : f + 1;   // fine tune 0.1 MHz
        else if (k == K_HASH) f = f <= 760 ? 1080 : f - 1;
        else continue;
        tune(f);
        hint(0);
    }
    fm_off();                               // power the FM chip down
    bk_listen(0);
    AMP_ON = 0;
    *khz100 = f;
    return wx_at >> 7;
}
