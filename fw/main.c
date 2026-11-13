// TD-H3 open firmware: dual VFO (A/B, each a VFO or one of the radio's memory channels),
// S-meter, battery, scan lists and groups, dual watch, priority, NOAA weather mode and alert, battery saver,
// scope, freq find, CTCSS/DCS with tone scan, hard TX band limits, PC LINK for CHIRP.
#include <stddef.h>
#include "fw.h"


// Our settings live in a small block the stock map leaves unnamed (0x1FC0-0x1FEF).
#define CFG_ADDR 0x1FC0
#define CFG_MAGIC 0xAD                      // 0xAC: POWER without MID; 0xAB: TAIL TONE a bit of 0x1FC4 (cfg_load moves both up); older blocks load defaults
#define NTHEMES 4                           // Classic, Night amber, Green, Night red
typedef struct { uint32_t vfo; uint8_t mem, chn; } slot_t;    // A and B: a VFO or a memory channel
// The settings block, 48 bytes at 0x1FC0. Small settings share bytes (SDCC fills a byte from bit 0);
// every other field keeps its place. free_* bytes are unused.
typedef struct {
    uint8_t magic, sql;
    uint8_t step:4, mic:4;                                              // 0x1FC2
    uint8_t power:2, shift:2, save:2, light:2;                          // 0x1FC3
    uint8_t narrow:1, att:1, dw:1, :1, beep:1, bt:1, remote:1, bell:1;   // 0x1FC4; bit 3: TAIL TONE OFF before 0xAC
    uint8_t apo:2, theme:2, :3, klock:1;                                // 0x1FC5; klock: KEY LOCK stops PTT too; bits 4-6: the old single OFFSET, not read (old radios still hold it)
    uint8_t pause:3, resume:3, digits:2;                                // 0x1FC6; digits: DIGITS, D_*
    uint8_t tot;
    uint8_t bright:3, vmode:4, :1;          // 0x1FC8; vmode: each VFO's MOD_* (A bits 4:3, B 6:5), 0 = by band
    uint8_t comp:2, tail:2, nar6:1, sstep:2, :1;   // 0x1FC9, read as bk_opt by bk4819.c and radio.c (BKO_* in fw.h); sstep: SSB's
    uint8_t off[2];                         // 0x1FCA: OFFSET under 300 MHz, then from 300 MHz up (off_n)
    uint8_t free_12;
    uint8_t act, prio, wx;
    uint8_t vox:4, voxd:3, :1;                                          // 0x1FD0: VOX 0 off or 1-9, VOX DELAY
    uint8_t txb, group;                     // txb: TX BANDS, bits HAM GMRS MURS FRS; group: bit g-1 = scan group g, 0 = scan list
    uint8_t free_19;
    int8_t fcal, mcal, bcal;                // 0.1 ppm, dB, 20 mV
    uint16_t rxt, txt, fm;                  // tone menu index; FM in 100 kHz
    slot_t s[2];
    uint8_t free_41[7];
} cfg_t;
typedef char cfg_fits[sizeof(cfg_t) <= 0x1FF0 - CFG_ADDR ? 1 : -1];   // never into 0x1FF0+ (SDCC only warns on _Static_assert)
__xdata cfg_t cfg;
static __xdata slot_t *__data sl;            // &cfg.s[cfg.act], set by load_ch
#define SL (*sl)
uint8_t sl_mem(void) { return SL.mem; }   // reads: a call is smaller than the pointer load
static uint8_t sl_chn(void) { return SL.chn; }

// New steps go at the end so a saved STEP keeps its meaning. 833 is 8.33 kHz (air band): 25 kHz / 3.
static const __code uint16_t steps[] = {250, 500, 625, 1000, 1250, 2000, 2500, 5000, 10000, 833, 1500, 3000};    // 10 Hz
const __code char *const __code step_s[] = {"2.5", "5", "6.25", "10", "12.5", "20", "25", "50", "100", "8.33", "15", "30"};
#define NSTEP (sizeof steps / sizeof steps[0])
static const __code uint8_t ssteps[] = {5, 10, 100, 1};   // USB tunes in its own steps (10 Hz), as the big rigs do; 0.05 first: the default
const __code char *const __code sstep_s[] = {"0.05", "0.1", "1", "0.01"};
static const __code uint16_t tones[] = {
    670, 693, 719, 744, 770, 797, 825, 854, 885, 915, 948, 974, 1000, 1035, 1072, 1109, 1148, 1188,
    1230, 1273, 1318, 1365, 1413, 1462, 1514, 1567, 1598, 1622, 1655, 1679, 1713, 1738, 1773, 1799,
    1835, 1862, 1899, 1928, 1966, 1995, 2035, 2065, 2107, 2181, 2257, 2291, 2336, 2418, 2503, 2541,
};
#define NTONES 50
// DCS codes: the 104 standard TIA-603 codes as C octal literals (leading 0: 0754 is code 754).
#define U(c) ((c) & 0377)                  // codes 0400 and up keep their low 8 bits: bit 8 is i >= 64
static const __code uint8_t dcs_lo[] = {
    0023, 0025, 0026, 0031, 0032, 0036, 0043, 0047, 0051, 0053, 0054, 0065, 0071, 0072, 0073, 0074,
    0114, 0115, 0116, 0122, 0125, 0131, 0132, 0134, 0143, 0145, 0152, 0155, 0156, 0162, 0165, 0172,
    0174, 0205, 0212, 0223, 0225, 0226, 0243, 0244, 0245, 0246, 0251, 0252, 0255, 0261, 0263, 0265,
    0266, 0271, 0274, 0306, 0311, 0315, 0325, 0331, 0332, 0343, 0346, 0351, 0356, 0364, 0365, 0371,
    U(0411), U(0412), U(0413), U(0423), U(0431), U(0432), U(0445), U(0446), U(0452), U(0454), U(0455), U(0462), U(0464), U(0465), U(0466), U(0503),
    U(0506), U(0516), U(0523), U(0526), U(0532), U(0546), U(0565), U(0606), U(0612), U(0624), U(0627), U(0631), U(0632), U(0654), U(0662), U(0664),
    U(0703), U(0712), U(0723), U(0731), U(0732), U(0734), U(0743), U(0754),
};
#undef U
static uint16_t dcs(uint8_t i) __reentrant { return dcs_lo[i] | (i >= 64 ? 0x100 : 0); }
#define NDCS 104
#define NTONE_MENU (1 + NTONES + 2 * NDCS)    // off, CTCSS, DCS N, DCS I
static uint16_t tone_of(uint16_t i) __reentrant
{
    if (!i) return 0;
    if (i <= NTONES) return tones[i - 1];
    i -= NTONES + 1;
    return i < NDCS ? TONE_DCS | dcs(i) : TONE_DCS | TONE_INV | dcs(i - NDCS);
}
static uint16_t index_of(uint16_t t) __reentrant   // inverse of tone_of
{
    for (uint16_t i = 1; i < NTONE_MENU; i++) if (tone_of(i) == t) return i;
    return 0;
}
const __code uint8_t tots[] = {0, 30, 60, 120, 180};
static const __code uint8_t light_s[] = {10, 30, 0};   // seconds on, 0 = always
static const __code uint32_t wx_f[7] = {16240000, 16242500, 16245000, 16247500, 16250000, 16252500, 16255000};
static const __code uint8_t save_ms[] = {0, 24, 48, 96};  // battery saver sleep, 5 ms units (1:2, 1:4, 1:8)

// Bands the VFO scan stays inside (10 Hz units), and their labels. After them, for SHIFT AUTO
// (Yaesu ARS, Kenwood auto offset), the repeater outputs; anything else is simplex. 0.6 MHz on 2 m,
// 5 MHz above. 2 m and 70 cm from the ARRL band plan (arrl.org/band-plan); its 70 cm repeater
// segments 442-445 and 447-450 pair 5 MHz apart (442-445 out +5, 447-450 out -5). GMRS repeater
// outputs 462.550-462.725, inputs 5 MHz up. Each repeater range lies inside a band, so a search
// from 0 always stops at the band first.
static const __code uint32_t bands[][2] = {
    {10800000, 13700000}, {14400000, 14800000}, {15600000, 16200000}, {16240000, 16255000},
    {42000000, 45000000}, {46255000, 46272500}, {46755000, 46772500},
    {14510000, 14550000}, {14661000, 14697000}, {14700000, 14739000}, {44200000, 44499999},
    {44700000, 45000000}, {46255000, 46272500},
};
#define ARS_UP 0x2C                         // which of them shift up: 147.0, 442-445, GMRS
static const __code char *const __code band_s[] = {"AIR", "2M", "MARINE", "WX", "70CM", "GMRS", "GMRS"};
#define NBANDS 7
static int8_t range_of(uint32_t f, uint8_t i) __reentrant
{
    for (; i < sizeof bands / sizeof *bands; i++) if (f >= bands[i][0] && f <= bands[i][1]) return i;
    return -1;
}
#define band_of(f) range_of(f, 0)

#define DIGIT(k) ((uint8_t)((k) - K_0) < 10)   // K_0-K_9: one compare
#define UPDOWN(k) ((uint8_t)((k) - K_UP) < 2)   // UP or DOWN
#define DIR(k) ((int8_t)(2 * K_UP + 1 - 2 * (k)))   // of UP or DOWN: 1 or -1
#define LIST_D(k) ((int8_t)(2 * (k) - 2 * K_UP - 1))   // a list cursor: UP -1 (a row up), DOWN 1
__xdata char buf[48];               // lines are built long, then cut at 21 chars: the longest status line is 41
__xdata char entry[8];
// No initializers on RAM variables: SDCC copies initialized XDATA with MOVX @R0 and P2
// paging, which this MCU may not honor. The Makefile fails the build if any appear.
uint8_t torch, peek_turn, nskip;
__data uint8_t entry_n, menu, peek, saving;   // internal RAM: the most-used bytes, about 2 B smaller per access
__bit tx, open, scanning, dirty, fast, locked, mon, rev, asleep, alert;   // bits: a test or set is half the code
static __bit vox, vox_arm, vox_tx;          // VOX: a voice found; armed; this TX is VOX's (vox_wants)
#define SQL_HELD (!cfg.sql || mon)          // squelch 0 or monitor: the audio is open whatever comes in
__xdata chan_t main_ch, other, prio_ch;   // other: the slot not selected
static __data int8_t sdir;                 // scan direction: 1 up, -1 down (UP/DOWN while scanning)
__data uint8_t tlevel;              // flashlight: 0 moonlight, 1 low, 2 medium, 3 high
__bit overwrite;                     // saving: the typed channel is in use, MENU again writes over it
static __xdata uint32_t skip[8];            // nuisance frequencies skipped until power-off (FT5DR skip search)
static __xdata uint32_t scan_r[2];          // a VFO scan's range (low, high), from where it started
static __xdata slot_t scan_from;            // the slot as the scan started: its channel, or its VFO frequency
uint16_t t_tx, t_open, t_key, t_save, t_meter, t_tone, t_scan, t_peek, t_wake, t_sleep, t_hit, heard;
int16_t dbm;
__xdata char dtmf[17];               // DTMF digits heard, shown for 10 s after the last
// Shown only as a run of DTMF_RUN or more, each within 1.5 s of the last: speech decodes as a stray
// digit or two now and then (the BK4819's decoder, stock's threshold), touch-tones come in runs.
// ITU-T Q.24: a digit is a tone of 40 ms or more, then a pause of 40 ms or more, so the next digit
// starts 80 ms or more later. The BK4819 reports only each tone it finds (REG_02 bit 11, the code
// in REG_0B), so a find under DTMF_GAP after the last is the same tone found again (same code) or
// speech (another code: a voice slides through tone pairs, 1A3A1A). A tone becomes a digit once the
// next tone, or DTMF_GAP of quiet, shows it was a whole one. The line is drawn once the burst
// pauses (DTMF_DRAW): the chip keeps only the newest code, so a redraw mid-burst would lose digits.
#define DTMF_RUN 3
#define DTMF_GAP 12                         // ticks, 60 ms: Q.24's 80 ms less the decoder's jitter
#define DTMF_DRAW 40                        // ticks, 200 ms
__data uint8_t dtmf_n;
static uint16_t t_dtmf;                     // the last tone found
static __data char dtmf_p;                         // the tone being heard, not yet a digit; 1: speech
static __bit dtmf_new;                      // digits not drawn yet
__xdata uint8_t dt_on, dt_mem;       // DTMF send: the key held now (its tone on, if it has one); MENU: a code number next
static uint16_t t_batt;
static uint16_t t_voice;                    // a voice prompt is playing: speaker on (REMOTE V)
static uint8_t t_tail;                     // a 55 Hz squelch tail was heard: audio shut till the carrier goes
static __bit low_batt;
enum { PEEK_NONE, PEEK_OTHER, PEEK_PRIO, PEEK_WX };
__bit wxm;                           // weather mode on
static __xdata uint8_t wx_tone;              // weather mode: the 1050 Hz alert tone was heard at the last look
__bit rang;                          // BELL: a call came in, the status bar shows a bell till a key
static __xdata uint8_t wx_ch;               // its NOAA channel, 0-6 for WX1-WX7, kept for next time

void text_bg(uint8_t x, uint8_t y, uint8_t f, uint8_t b) __reentrant { lcd_text_bg(x, y, buf, 1, pal[f], pal[b]); }   // buf at scale 1 on colour b
void text1(uint8_t x, uint8_t y, uint8_t c) __reentrant { text_bg(x, y, c, P_BG); }   // buf at scale 1

__xdata char *u2s(__xdata char *p, uint16_t v, uint8_t width) __reentrant   // zero-padded decimal
{
    p += width;
    *p = 0;
    while (width--) { uint16_t q = v / 10; *--p = '0' + (uint8_t)(v - q * 10); v = q; }
    return p;
}

__xdata char *num(__xdata char *p, uint8_t v) __reentrant   // 0-255, no padding: returns the end
{
    uint8_t w = v > 99 ? 3 : v > 9 ? 2 : 1;
    u2s(p, v, w);
    return p + w;
}

static __xdata char *cat(__xdata char *d, const __code char *s) __reentrant { while ((*d = *s++)) d++; return d; }
__xdata char *xcat(__xdata char *d, const __xdata char *s) __reentrant { while ((*d = *s++)) d++; return d; }   // cat from RAM
void pad_to(__xdata char *p, uint8_t n) __reentrant { while (p < buf + n) *p++ = ' '; *p = 0; }   // buf from p to n characters

__xdata char *level_s(__xdata char *p, uint8_t v) __reentrant   // 0-9, or S1-S9 for 10-18 (the S-meter squelch); returns the end
{
    if (v > 9) { *p++ = 'S'; v -= 9; }
    *p++ = '0' + v;
    *p = 0;
    return p;
}

// w digits with a '.' before the last frac; a leading 0 blanked when there are 2+ integer digits.
__xdata char *dec(__xdata char *p, uint32_t v, uint8_t w, uint8_t frac) __reentrant
{
    uint8_t n = w + (frac != 0), i, d = w - frac;   // d integer digits: no frac, no '.' (i stays under w)
    for (i = n; i--; ) {
        if (i == d) p[i] = '.';
        else { uint32_t q = v / 10; p[i] = '0' + (uint8_t)((uint8_t)v - (uint8_t)q * 10); v = q; }
    }
    if (p[0] == '0' && d > 1) p[0] = ' ';
    p[n] = 0;
    return p + n;
}

static __xdata char *sdec(__xdata char *p, int16_t v, uint8_t w, uint8_t frac) __reentrant
{
    *p++ = v < 0 ? '-' : '+';
    return dec(p, v < 0 ? -v : v, w, frac);
}

static __xdata char *s2s(__xdata char *p, int16_t v, uint8_t width) __reentrant  // signed, "+012": sdec's, its blank put back
{
    __xdata char *e = sdec(p, v, width, 0);
    if (p[1] == ' ') p[1] = '0';
    return e;
}

static uint16_t since(const __xdata uint16_t *t) __reentrant { return now() - *t; }
uint16_t secs(const __xdata uint16_t *t) __reentrant { return since(t) / 200; }

static void stamp(__xdata uint16_t *t) __reentrant { *t = now(); }   // t = now: a call is smaller

static void wake(void) { asleep = 0; radio_tune(); stamp(&t_wake); }

static void stamp1(__xdata uint16_t *t) __reentrant { *t = now() | 1; }   // never 0: 0 is not started
static void save_soon(void) { stamp1(&t_save); }
static void no_wait(void) { t_open = now() - 1000; }   // past the longest resume wait: a scan moves on now

static uint8_t slot_ok(const __xdata slot_t *x) __reentrant { return x->vfo >= 1800000UL && x->vfo <= 99999999UL && x->chn <= 199 && x->mem < 2; }   // mem 2-255: V/M's ^= 1 never reached 0

enum { M_SQL, M_ATT, M_COMP, M_NAR, M_BW, M_MODE, M_STEP, M_RXT, M_POW, M_SHIFT, M_OFFS, M_TXT, M_TXB, M_TOT, M_TAIL, M_MIC, M_VOX, M_VOXD, M_DTMF, M_CALL,   // the menu's order
       M_GROUP, M_PAUSE, M_RESUME, M_DW, M_PRIO, M_GLINK, M_GRP, M_SCOPE, M_FF, M_FM, M_WXM, M_LH, M_NAME, M_TOVFO, M_DEL,
       M_LIGHT, M_BRIGHT, M_THEME, M_DIGITS, M_BEEP, M_KLOCK, M_BELL, M_WX, M_APO, M_SAVE, M_PC, M_REM, M_BT, M_FCAL, M_MCAL, M_BCAL, M_VER, M_N };
static const __code char *const __code theme_s[NTHEMES] = {"CLASSIC", "NIGHT AMBER", "GREEN", "NIGHT RED"};
enum { D_ARC, D_LCD, D_CLASSIC };          // DIGITS: 0, a new radio's, is ARCADE
static const __code char *const __code digits_s[] = {"ARCADE", "LCD", "CLASSIC"};
static const __code char *const __code pow_s[] = {"ULTRA LOW", "LOW", "MID", "HIGH"};   // PWR_*: lowest to highest
const __code char *const __code pow_d[] = {"ULOW ", "LOW ", "MID ", "HIGH "};   // the slot's detail row
static const __code char *const __code bw_s[] = {"WIDE", "NARROW"};
const __code char *const __code mode_s[] = {"FM", "AM", "USB"};
static const __code char *const __code shift_s[] = {"OFF", "+", "-", "AUTO"};   // AUTO last: saved 0-2 keep their meaning
// Scanning as the big rigs do it (Icom's pause and resume timers): PAUSE is the longest it stays on a
// busy channel (BUSY, the default: no limit, carrier-operated as Yaesu and Kenwood), RESUME how long it
// waits once the signal drops (5 s: the other side of a conversation is heard). RESUME starts at 2 s so older settings
// blocks (0 CARRIER, 1 AFTER 5 s, 2 STOP) land on 2, 3 and 5 s; the list wraps, so it still reads in order.
static const __code char *const __code pause_s[] = {"2 s", "5 s", "10 s", "20 s", "BUSY"};
static const __code char *const __code resume_s[] = {"2 s", "3 s", "5 s", "HOLD", "0 s", "1 s"};
static const __code char *const __code save_s[] = {"OFF", "SHORT", "MIDDLE", "LONG"};   // as Icom: sleep 120, 240, 480 ms
static const __code char *const __code tot_s[] = {"OFF", "030 s", "060 s", "120 s", "180 s"};
static const __code char *const __code lightv_s[] = {"10 s", "30 s", "ON"};
// BRIGHTNESS 1-5 as backlight PWM duty (of 200). 5 is stored as 0, so blocks from before it read full;
// the list wraps, so it still reads 1-5 in order. The panel looks full from about 70 up (3, 4 and 5 at
// 70, 120, 200 looked the same), so 1-4 sit under that, about 1.5x apart; stock's dim levels are 40-55.
static const __code char *const __code bright_s[] = {"5", "1", "2", "3", "4"};
static const __code uint8_t bright_duty[] = {200, 10, 20, 30, 45};
static const __code char *const __code apo_s[] = {"OFF", "30 min", "60 min", "120 min"};
static const __code char *const __code off_on[] = {"OFF", "ON"};
static const __code char *const __code comp_s[] = {"OFF", "RX", "TX", "BOTH"};
static const __code char *const __code tail_s[] = {"ON", "OFF", "REVERSE"};
static const __code char *const __code nar_s[] = {"12.5", "6.25"};
static const __code char *const __code klock_s[] = {"KEYS", "KEYS+PTT"};
// VOX 1-9: the chip's find level (bk4819.c bk_vox), from stock's least sensitive (1226, its gain 1)
// to the most its table holds (232), in 9 even steps of x 0.81. VOX DELAY: how long it stays keyed
// after the voice goes, in 5 ms ticks; 1 s, the default, is 0, so blocks from before it read it (the
// list wraps, so it still reads in order).
static const __code uint16_t vox_thr[9] = {1226, 996, 809, 657, 534, 434, 352, 286, 232};
static const __code char *const __code voxd_s[] = {"1 s", "1.5 s", "2 s", "3 s", "0.5 s"};
static const __code uint16_t vox_hang[] = {200, 300, 400, 600, 100};
// Menu items that pick one of n values of a setting: its byte, n, default, where it sits in the
// byte (bit << 4 | width), labels (0 = the digit).
typedef struct { uint8_t off, n, def, bits; const __code char *const __code *lab; } item_t;
#define O(f) offsetof(cfg_t, f)
static const __code item_t items[M_N] = {   // SQUELCH 0-9, then S1-S9 (10-18): the S-meter squelch
    [M_SQL] = {O(sql), 19, 3, 0x08, 0}, [M_TOT] = {O(tot), 5, 3, 0x08, tot_s}, [M_TXB] = {O(txb), 16, 3, 0x08, 0},
    [M_STEP] = {2, NSTEP, 6, 0x04, step_s}, [M_MIC] = {2, 10, 5, 0x44, 0},
    [M_POW] = {3, 4, PWR_LOW, 0x02, pow_s}, [M_SHIFT] = {3, 4, 3, 0x22, shift_s}, [M_SAVE] = {3, 4, 2, 0x42, save_s}, [M_LIGHT] = {3, 3, 1, 0x62, lightv_s}, [M_BRIGHT] = {8, 5, 0, 0x03, bright_s},
    [M_BW] = {4, 2, 0, 0x01, bw_s}, [M_ATT] = {4, 2, 0, 0x11, off_on}, [M_DW] = {4, 2, 1, 0x21, off_on}, [M_TAIL] = {9, 3, 0, 0x22, tail_s},
    [M_MODE] = {8, 3, 0, 0x32, mode_s},   // the selected slot's: A bits 4:3, B 6:5 (item_get adds 2 for B)
    [M_BEEP] = {4, 2, 1, 0x41, off_on}, [M_BT] = {4, 2, 0, 0x51, off_on}, [M_REM] = {4, 2, 0, 0x61, off_on}, [M_BELL] = {4, 2, 0, 0x71, off_on},
    [M_APO] = {5, 4, 0, 0x02, apo_s}, [M_THEME] = {5, NTHEMES, 0, 0x22, theme_s}, [M_KLOCK] = {5, 2, 0, 0x71, klock_s}, [M_VOX] = {16, 10, 0, 0x04, 0}, [M_VOXD] = {16, 5, 0, 0x43, voxd_s},
    [M_COMP] = {9, 4, 0, 0x02, comp_s}, [M_NAR] = {9, 2, 0, 0x41, nar_s},
    [M_PAUSE] = {6, 5, 4, 0x03, pause_s}, [M_RESUME] = {6, 6, 2, 0x33, resume_s}, [M_DIGITS] = {6, 3, D_ARC, 0x62, digits_s},
    [M_WX] = {O(wx), 8, 0, 0x08, 0},       // OFF, WX1-WX7: shown as its frequency (menu_value)
};
typedef char cfg_layout[O(tot) == 7 && O(act) == 13 && O(txb) == 17 && O(fcal) == 20 && O(s) == 29 ? 1 : -1];   // saved blocks keep reading right
#define CFGB ((__xdata uint8_t *)&cfg)

static uint8_t ch_uhf(void) { return ch.rx >= 30000000UL; }   // the tuned frequency is 300 MHz or more
// OFFSET per band, as the big rigs keep it: cfg.off[0] under 300 MHz, [1] from 300 MHz up, in 50 kHz steps
// (0.05-12.75 MHz). 0, what older settings hold, means the band's standard: 0.6 or 5.0 MHz.
static uint8_t off_n(uint8_t u) __reentrant { uint8_t v = cfg.off[u]; return v ? v : u ? 100 : 12; }
static uint32_t off_d(uint8_t n) __reentrant { return (uint32_t)n * 5000; }   // n steps in 10 Hz

static uint8_t item_get(uint8_t i) __reentrant
{
    const __code item_t *it = &items[i];
    return CFGB[it->off] >> (uint8_t)((it->bits >> 4) + (i == M_MODE ? 2 * cfg.act : 0)) & (uint8_t)((1 << (it->bits & 15)) - 1);
}

static void item_set(uint8_t i, uint8_t v) __reentrant
{
    const __code item_t *it = &items[i];
    uint8_t sh = (it->bits >> 4) + (i == M_MODE ? 2 * cfg.act : 0), m = (uint8_t)((1 << (it->bits & 15)) - 1) << sh;
    CFGB[it->off] = CFGB[it->off] & ~m | v << sh & m;
}

static void slot_reset(uint8_t i)   // VFO on the 2 m (A) or 70 cm (B) calling frequency
{
    __xdata slot_t *x = &cfg.s[i];
    x->vfo = i ? 44600000UL : 14652000UL; x->mem = 0; x->chn = 1;
}

static uint8_t nxt(uint8_t n, int8_t d) __reentrant { return chan_next(n, d, 0, 0); }

// One out-of-range value resets that value, not every setting. (A flash once came back with
// all settings at defaults: a whole-block check is too blunt.)
static const __code uint8_t pow_ac[4] = {PWR_LOW, PWR_HIGH, PWR_ULP, PWR_LOW};   // 0xAC's LOW, HIGH, ULTRA LOW
#define FIX(bad, field, v) do { if (bad) cfg.field = v; } while (0)
static void cfg_load(void)
{
    uint8_t i;
    if (!ee_read(CFG_ADDR, (uint8_t *)&cfg, sizeof cfg)) cfg.magic = 0;
    if (cfg.magic == 0xAB) { CFGB[9] |= CFGB[4] >> 1 & 4; CFGB[4] &= ~8; cfg.magic = 0xAC; }   // TAIL TONE gains REVERSE: OFF
                                            // (0x1FC4 bit 3) to tail's 1 (0x1FC9 bit 2); bytes, not bitfields: half the code
    if (cfg.magic == 0xAC) { cfg.power = pow_ac[cfg.power]; cfg.magic = CFG_MAGIC; }   // POWER gains MID, in order
    if (cfg.magic != CFG_MAGIC) xset(&cfg, 0, sizeof cfg);   // defaults: all 0 (magic too, till the end), then what the checks put in
    for (i = 0; i < M_N; i++) if (items[i].n && (!cfg.magic || item_get(i) >= items[i].n)) item_set(i, items[i].def);
    FIX(cfg.act >= 2, act, 0); FIX(cfg.prio > 199, prio, 0); FIX((uint8_t)(cfg.fcal + 100) > 200, fcal, 0);
    FIX((uint8_t)(cfg.mcal + 40) > 80, mcal, 0); FIX((uint8_t)(cfg.bcal + 50) > 100, bcal, 0);   // +-n: one compare
    FIX(cfg.rxt >= NTONE_MENU, rxt, 0); FIX(cfg.txt >= NTONE_MENU, txt, 0);
    FIX((uint16_t)(cfg.fm - 760) > 320, fm, 1019);   // 76-108 MHz
    for (i = 0; i < 2; i++)
        if (!slot_ok(&cfg.s[i])) slot_reset(i);
    if (!cfg.magic) {                       // defaults: channels loaded? then A and B start on the first two
        uint8_t a = nxt(199, 1), b = a ? nxt(a, 1) : 0;
        cfg.magic = CFG_MAGIC;
        if (a) { cfg.s[0].mem = 1; cfg.s[0].chn = a; }
        if (b && b != a) { cfg.s[1].mem = 1; cfg.s[1].chn = b; }
    }
}

// Themes; the panel is BGR, RGB() packs for it. bg, fg, dim, accent, signal, highlight, bar, and
// the tints behind a slot being heard and one transmitting (draw_slot reads those two from here).
#define T_RX 7
#define T_TX 8
static const __code uint16_t themes[NTHEMES][9] = {
    {BLACK, WHITE, RGB(120, 120, 120), RGB(255, 176, 0), RGB(0, 220, 80), RGB(0, 200, 230), RGB(30, 30, 30), RGB(6, 64, 28), RGB(74, 12, 0)},
    {BLACK, RGB(255, 176, 0), RGB(138, 90, 0), RGB(255, 176, 0), RGB(255, 204, 64), RGB(255, 208, 112), RGB(42, 24, 0), RGB(40, 26, 0), RGB(88, 44, 0)},
    {BLACK, RGB(51, 255, 102), RGB(26, 138, 51), RGB(51, 255, 102), RGB(153, 255, 170), RGB(204, 255, 216), RGB(6, 32, 16), RGB(6, 48, 16), RGB(16, 84, 28)},
    {BLACK, RGB(255, 24, 0), RGB(128, 8, 0), RGB(255, 24, 0), RGB(255, 64, 32), RGB(255, 104, 80), RGB(40, 0, 0), RGB(40, 0, 0), RGB(84, 0, 0)},   // keeps night vision
};
__xdata uint16_t pal[8];
void draw_labels(const __code label_t *l, uint8_t n) __reentrant
{
    for (; n; n--, l++) lcd_text(l->x, l->y, l->s, l->scale, l->col);
}
static void tx_prefs(void)                  // the settings radio.c, bk4819.c and remote.c read from their own variables
{
    remote = cfg.remote;
    mic_gain = cfg.mic;
    bk_att = cfg.att;
    bk_opt = CFGB[9];
    tx_bands = cfg.txb;
    bk_vox = cfg.vox ? vox_thr[cfg.vox - 1] : 0;
}

uint16_t theme_c(uint8_t i) __reentrant { return ((const __code uint16_t *)themes)[(uint8_t)(cfg.theme * 9 + i)]; }   // colour i of the current theme (an 8-bit index: no int multiply)

void theme_apply(void)
{
    for (uint8_t i = 0; i < 7; i++) pal[i] = theme_c(i);
    pal[P_RED] = RED;
}

uint32_t wx_freq(uint8_t n) __reentrant { return wx_f[n]; }   // a call is 40 bytes smaller than wx_f[n]

static void wx_fill(uint8_t n) __reentrant  // NOAA channel n (0-6) into `ch`: weather mode and the alert look
{
    xset(&ch, 0, sizeof ch);              // no TX, no tones, FM wide
    ch.rx = wx_freq(n);
    ch.name[0] = 'W'; ch.name[1] = 'X'; ch.name[2] = '1' + n;
}

static uint8_t on_rx(const __xdata uint32_t *f) __reentrant { return *f == ch.rx; }   // f is where ch listens
static void from_ch(__xdata chan_t *d) __reentrant { xcpy(d, &ch, sizeof ch); }         // a copy of ch
static void to_ch(const __xdata chan_t *s) __reentrant { xcpy(&ch, s, sizeof ch); }     // ch from a copy

// Fill `ch` from slot i: its memory channel, or its VFO with the shared VFO settings.
static void fill_ch(uint8_t i) __reentrant
{
    __xdata slot_t *x = &cfg.s[i];
    int8_t b;
    uint8_t sh, o, u;
    if (wxm && i == cfg.act) { wx_fill(wx_ch); return; }   // weather mode: the slot itself is left as it was
    if (!(x->mem && chan_load(x->chn))) {
        x->mem = 0;
        ch.rx = x->vfo;
        sh = cfg.shift; u = ch_uhf(); o = off_n(u);
        if (sh == 3) {                      // AUTO: the band plan's shift and offset, or simplex
            b = range_of(ch.rx, NBANDS) - NBANDS;
            // GMRS: only the 8 main channels have repeaters; the interstitials 12.5 kHz between them are
            // simplex. In 10 Hz units the main channels are multiples of 4 and the interstitials 2 more.
            if (b == 5 && (uint8_t)ch.rx & 3) b = -1;
            sh = b < 0 ? 0 : (uint8_t)ARS_UP >> (uint8_t)b & 1 ? 1 : 2;
            o = u ? 100 : 12;               // the band's standard: 0.6 or 5.0
        }
        {
            uint32_t d = sh ? off_d(o) : 0;   // sh: 0 simplex, 1 up, 2 down
            if (sh == 2) d = -d;
            ch.tx = ch.rx + d;
        }
        ch.rx_tone = tone_of(cfg.rxt);
        ch.tx_tone = tone_of(cfg.txt);
        b = band_of(ch.rx);
        ch.mode = CFGB[8] >> (3 + 2 * i) & 3;   // the VFO's MODE; 0: FM, or AM on AIR, 108 up to 137 MHz (137.000 too)
        if (ch.mode > MOD_USB) ch.mode = 0;
        if (!ch.mode && !b && ch.rx != 13700000UL) ch.mode = MOD_AM;
        ch.narrow = cfg.narrow;
        ch.power = cfg.power;
        ch.groups = ch.bcl = 0;             // no busy lockout on a frequency (a channel loaded before left it set)
        cat(ch.name, b < 0 ? "VFO" : band_s[(uint8_t)b]);
    }
    if (rev && i == cfg.act && ch.tx && !on_rx(&ch.tx)) {   // reverse: listen on the input
        __xdata uint8_t *f = (__xdata uint8_t *)&ch.rx;   // rx, then tx: swapped a byte at a time
        for (b = 0; b < 4; b++, f++) { uint8_t c = *f; *f = f[4]; f[4] = c; }
    }
}

// Tune the selected slot; keep a copy of the other one for the screen and dual watch.
static __bit keep_other;                    // a scan step: the other slot can't have changed

static void load_ch(void)
{
    sl = &cfg.s[cfg.act];
    sql_level = cfg.sql > 9 ? 2 : cfg.sql;  // S1-S9: a moderate chip squelch, the S-meter does the rest
    wx_watch = wxm ? cfg.wx : 0;
    freq_cal = cfg.fcal;
    if (!keep_other) { fill_ch(!cfg.act); from_ch(&other); }
    fill_ch(cfg.act);
    radio_tune();
}

static void load_prio(void) __reentrant     // cache the priority channel; ch is kept by a swap through prio_ch
{
    __xdata uint8_t *a = (__xdata uint8_t *)&ch, *b = (__xdata uint8_t *)&prio_ch;
    uint8_t t;
    from_ch(&prio_ch);
    if (!cfg.prio || !chan_load(cfg.prio)) { cfg.prio = 0; to_ch(&prio_ch); return; }
    for (t = sizeof ch; t--; ) { uint8_t c = a[t]; a[t] = b[t]; b[t] = c; }
}

__xdata char *freq_s(__xdata char *p, uint32_t f) __reentrant   // "146.520": all 8 digits, the last two cut (no f / 100)
{
    p = dec(p, f, 8, 5) - 2;
    *p = 0;
    return p;
}

__xdata char *tone_s(__xdata char *p, uint16_t t) __reentrant
{
    if (!t) return cat(p, "OFF");
    if (t & TONE_DCS) {
        __xdata char *q = p;
        *q++ = 'D'; *q++ = '0' + (t >> 6 & 7); *q++ = '0' + (t >> 3 & 7); *q++ = '0' + (t & 7);
        *q++ = t & TONE_INV ? 'I' : 'N'; *q = 0;
        return q;
    }
    return dec(p, t, 4, 1);
}

// What tone is the station sending? The nearest standard CTCSS within 1.5 Hz, or the DCS code
// whose codeword (or its complement: an inverted code) is a cyclic rotation of the word heard.
// Golay(23,12) is cyclic and the receiver can lock on at any of the 23 bits.
static uint16_t tone_heard(void) __reentrant
{
    uint16_t c = bk_ctcss_measured(), t;
    uint32_t w = bk_dcs_measured();
    uint8_t i, m;
    if (c) {
        for (i = 0; i < NTONES; i++)            // |c - t| < 10 in one compare: +-1 Hz, 67.0 and 69.3 sit 2.3 Hz apart
            if ((uint16_t)(c - (t = tones[i]) + 9) < 19) return t;   // +-1 Hz: 67.0 and 69.3 sit 2.3 Hz apart
        return 0;
    }
    if (!w) return 0;
    // Try each of the 23 rotations of the word (and of its inverse) as a codeword: data in the low
    // 12 bits with the 100 marker at bits 11-9, then check its Golay parity. Of several matches the
    // lowest table entry wins, normal before inverted, as a search through the table would find.
    m = 0xFF;                               // none yet (2i + inv is 0-207)
    for (uint8_t inv = 0; inv < 2; inv++, w ^= 0x7FFFFF)
        for (uint8_t r = 0; r < 23; r++, w = (w << 1 | (uint8_t)(w >> 16) >> 6) & 0x7FFFFF)   // w >> 22: w is 24 bits
            if (((uint16_t)w >> 9 & 7) == 4 && bk_golay(w & 0x1FF) == w)
                for (i = 0; i < NDCS; i++)
                    if (dcs(i) == (uint16_t)(w & 0x1FF)) { i += i + inv; if (i < m) m = i; break; }   // 2i + inv: 0-207
    return m == 0xFF ? 0 : TONE_DCS | (m & 1 ? TONE_INV : 0) | dcs(m >> 1);
}

// ---------- screen: two slots under a status bar ----------
// y 0   status bar: what's on, icons, battery %          y 61-69  the meter, the screen's width:
// y 14  A: its letter (lit when selected), name centred           "S9+11" left, "-102dBm" right
// y 23  A: *M20 (memory, * = scan list), frequency at 2x         inside the bar (TX: mic level, clock)
// y 41  A: mode, power, shift ... bandwidth             y 73-116  B the same
// y 50  A: tones ... step (or DTMF / HEARD while heard) y 120     (BOT_Y) messages and typing
// 128 rows exactly: 3 between the bar's rule and A, A and the meter, B and the bottom line, 2 over B.
// A slot heard or transmitting sits on its theme tint (y 13-60 or 70-116: up to the meter).
#define METER_Y 62                          // the meter's text row; the bar starts a row above

__xdata char shown[2][8];            // the digits on screen, so a scan step redraws only what changed
__xdata uint16_t shown_c[2];
int16_t m_dbm;                       // what the meter last drew: its reading, and the squelch (0xFF forces a redraw)
uint8_t m_open;
static __bit m_wait;                 // squelch() found the receiver unsettled: dbm is the last channel's, don't draw it

// buf from p to 21 characters: a then b right-aligned at the end, spaces between. Reentrant, so
// its locals go on the stack, not into the full XDATA.
void right(__xdata char *p, const __code char *a, const __code char *b) __reentrant
{
    uint8_t n = 0, m = 0;
    while (a[n]) n++;
    while (b[m]) m++;
    n += m;
    if (p > buf + 20 - n) p = buf + 20 - n;
    while (p < buf + 21 - n) *p++ = ' ';
    cat(cat(p, a), b);
}

__xdata uint8_t tinted[2];           // the tint each slot's block is on (0: none), so going back to bg fills it too
// DIGITS: how the slots' frequencies look. CLASSIC is the 5x7 font at 2x (lcd_text). ARCADE and LCD
// draw each character as fills, not mirrored: REMOTE gets CLASSIC's text instead, so a computer reads
// the frequency, a picture shows it in the 5x7 font, and the wire carries no more. Big digits from x 5,
// 16 apart, the point 5; the two small digits below 1 kHz follow, bottom-aligned. All end on row y + 25.
// ARCADE: the owner's 7x9 shapes, each pixel 2 wide, shape rows 2 and 6 1 tall and the rest 2
// (14x16 at y + 9), over a shadow 1 px right and down at 40%; the point 3x3; small, the shapes at
// 1x, 8 apart. LCD: a clock's seven segments, 3 px strokes with mitred corners (14x18 at y + 8);
// the point 3x3; small 7x11, 1 px strokes, 9 apart.
// Each shape is a list of rectangles, two bytes each: x << 4 | w, y << 3 | h - 1.
const __code uint8_t arc_r[] = {     // ARCADE in shape units: columns 0-6, rows 0-8
    0x02, 0x0E, 0x52, 0x0E, 0x15, 0x00, 0x15, 0x40,   // 0
    0x32, 0x07, 0x16, 0x40, 0x12, 0x10, 0x21, 0x08,   // 1
    0x07, 0x40, 0x52, 0x0A, 0x15, 0x00, 0x23, 0x28, 0x02, 0x08, 0x42, 0x20, 0x12, 0x30, 0x02, 0x38,   // 2
    0x51, 0x07, 0x15, 0x40, 0x14, 0x00, 0x23, 0x20, 0x52, 0x0A, 0x52, 0x2A, 0x02, 0x08, 0x02, 0x38,   // 3
    0x52, 0x07, 0x05, 0x28, 0x32, 0x08, 0x22, 0x10, 0x12, 0x18, 0x02, 0x20, 0x52, 0x40, 0x41, 0x00,   // 4
    0x02, 0x03, 0x52, 0x23, 0x07, 0x00, 0x15, 0x40, 0x06, 0x18, 0x02, 0x38,   // 5
    0x02, 0x0E, 0x52, 0x2A, 0x15, 0x00, 0x15, 0x40, 0x06, 0x20, 0x52, 0x08,   // 6
    0x22, 0x2B, 0x07, 0x00, 0x52, 0x09, 0x42, 0x18, 0x32, 0x20,   // 7
    0x11, 0x07, 0x51, 0x07, 0x15, 0x40, 0x14, 0x00, 0x14, 0x20, 0x01, 0x0A, 0x52, 0x0A, 0x01, 0x2A, 0x52, 0x2A,   // 8
    0x52, 0x0E, 0x02, 0x0A, 0x15, 0x00, 0x15, 0x40, 0x14, 0x20, 0x02, 0x38,   // 9
};
const __code uint8_t arc_i[11] = {0, 4, 8, 16, 24, 32, 38, 44, 49, 58, 64};   // digit d's rectangles: arc_i[d] to arc_i[d + 1]
const __code uint8_t rowy[10] = {0, 2, 4, 5, 7, 9, 11, 12, 14, 16};   // a big shape row's top: rows 2 and 6 1 tall
const __code uint8_t seg_r[] = {     // LCD segments a-g in px: big, three each (outer to inner), then small, one each
    0x1C, 0x00, 0x2A, 0x08, 0x38, 0x10,  0xD1, 0x0E, 0xC1, 0x15, 0xB1, 0x1B,  0xD1, 0x4F, 0xC1, 0x4E, 0xB1, 0x54,
    0x38, 0x78, 0x2A, 0x80, 0x1C, 0x88,  0x01, 0x4F, 0x11, 0x4E, 0x21, 0x54,  0x01, 0x0E, 0x11, 0x15, 0x21, 0x1B,
    0x38, 0x38, 0x2A, 0x40, 0x38, 0x48,
    0x15, 0x00, 0x61, 0x0B, 0x61, 0x33, 0x15, 0x50, 0x01, 0x33, 0x01, 0x0B, 0x15, 0x28,
};
const __code uint8_t seg7[10] = {0x3F, 0x06, 0x5B, 0x4F, 0x66, 0x6D, 0x7D, 0x07, 0x7F, 0x6F};   // bit 0 a (top) .. 6 g (middle)
const __code uint8_t dots[] = {0x03, 0x6A, 0x03, 0x7A};   // the point, 3x3 on the bottom rows: ARCADE (at its row 13), LCD (15)
// The frequency from buf + k (0-6: the big digits from the first that changed; 20: the two small
// ones) in slot y's frequency rows, colour fc.
// asm/freq_text.asm; this C is the reference (make CFUN=freq_text builds it)
void freq_text(uint8_t k, uint8_t y, uint8_t fc) __reentrant;
#ifdef C_FREQ_TEXT
// internal RAM, as the asm keeps it: SDCC's stack frames made this C too slow for a mirrored redraw
static __data uint8_t d_x, d_y, d_units, r_x, r_w, r_y, r_h;   // the cell's top left; d_units: ARCADE's big shape units (x 2, rows by rowy)
static __data uint16_t d_ink;

static void rects(const __code uint8_t *p, uint8_t n) __reentrant   // n rectangles from p
{
    for (; n; n--) {
        r_x = *p >> 4; r_w = *p++ & 15; r_y = *p >> 3; r_h = (*p++ & 7) + 1;
        if (d_units) { r_x += r_x; r_w += r_w; r_h = rowy[r_y + r_h] - rowy[r_y]; r_y = rowy[r_y]; }
        lcd_fill(d_x + r_x, d_y + r_y, r_w, r_h, d_ink);
    }
}

void freq_text(uint8_t k, uint8_t y, uint8_t fc) __reentrant
{
    __xdata char *s = buf + k;
    uint8_t big = k < 7, a = cfg.digits == D_ARC, x = 5, c, o, t, m = mirror;
    if (cfg.digits == D_CLASSIC) { lcd_text(big ? 32 + 12 * k : 116, y + (big ? 9 : 17), s, big + 1, fc); return; }
    if (m) remote_text(big ? 32 + 12 * k : 116, y + (big ? 9 : 17), s, big + 1, pal[fc], c_bg);
    mirror = 0;
    for (o = 0; o < k && o < 7; o++) x += buf[o] == '.' ? 5 : 16;
    t = y + (big ? 8 : 15) + a;             // the top row: ARCADE's a row lower, its shadow ending where LCD does
    // the cells from this one on, in one fill: to x 104 (the last big digit's
    // shadow) or 121 (the small ones'); ARCADE's 2 rows shorter, and its shadow's
    lcd_fill(x, t, (big ? 105 : 122) - x, (big ? 18 : 11) - a, c_bg);
    for (; c = *s; s++) {
        d_units = 0;
        for (o = a; o != 0xFF; o--) {       // ARCADE: o 1 the shadow, 1 right and down, at 40% (each channel x 0.406); o 0 the character
            d_ink = pal[fc];
            if (o) d_ink = (d_ink >> 1 & 0x7BEF) - (d_ink >> 3 & 0x18E3) + (d_ink >> 5 & 0x0020);
            d_x = x + o; d_y = t + o;
            if (c == '.') rects(dots + 2 - 2 * a, 1);
            else if ((uint8_t)(c - '0') < 10) {   // a space, or anything else: the cleared cell
                uint8_t d = c - '0';
                if (a) { d_units = big; rects(arc_r + 2 * arc_i[d], arc_i[d + 1] - arc_i[d]); }
                else for (uint8_t m = seg7[d], j = 0; m; m >>= 1, j++) if (m & 1) rects(seg_r + (big ? 6 * j : 42 + 2 * j), big ? 3 : 1);
            }
        }
        x += !big ? 9 - a : c == '.' ? 5 : 16;
    }
    mirror = m;
}
#endif

// The memory label, buf padded to 5: CLASSIC at x 0 on the frequency row; ARCADE and LCD at the name
// row's right end, 1 px from the edge (the name row is cleared each draw).
void slot_label(uint8_t y, uint8_t c) __reentrant
{
    uint8_t n = 0;
    if (cfg.digits == D_CLASSIC) { text1(0, y + 13, c); return; }
    while (buf[n] > ' ') n++;
    buf[n] = 0;
    text1(W - 6 * n, y, c);
}

__xdata chan_t dc;                   // draw_slot's copy of the slot's channel: fixed addresses read far cheaper
__bit sel, live;                     // draw_slot's: its slot is the selected one, the one tuned
// asm/draw_slot.asm; this C is the reference (make CFUN=draw_slot builds it)
void draw_slot(uint8_t i) __reentrant;
#ifdef C_DRAW_SLOT
void draw_slot(uint8_t i) __reentrant
{
    // `ch` is whatever is tuned: normally the selected slot. During a dual-watch peek it is the
    // other slot (the selected one waits in main_ch); during a priority or weather peek neither.
    uint8_t y = i ? 73 : 14, k = 0;
    sel = i == cfg.act;
    __xdata char *p;
    live = peek ? peek == PEEK_OTHER && !sel : sel;   // this slot is the one in `ch`
    xcpy(&dc, live ? &ch : sel ? &main_ch : &other, sizeof dc);
    // A slot being heard sits on the theme's RX tint, the one transmitting on its TX tint: the tint
    // stands in for c_bg while the slot draws, so the text's background matches it.
    uint8_t s = !live ? 0 : tx ? T_TX : open ? T_RX : 0;
    if (s) { pal[0] = theme_c(s); c_dim = c_fg; }   // dim grey is lost on a tint: full brightness there
    uint8_t fc = s == T_TX ? P_RED : s ? P_OK : sel ? P_FG : P_DIM, tc = sel ? P_FG : P_DIM;
    // the slot letter (a lit block when selected), the name centred; the whole block when the tint
    // comes or goes (a fast redraw keeps the tint it has)
    lcd_fillp(0, y - 1, W, !fast && (s | tinted[i]) ? 45 : 9, P_BG);
    tinted[i] = s;
    if (sel) lcd_fillp(0, y - 1, 8, 9, P_ACC);
    buf[0] = 'A' + i; buf[1] = 0;
    text_bg(1, y, sel ? P_BG : P_DIM, sel ? P_ACC : P_BG);
    for (k = 0; dc.name[k]; k++) ;
    lcd_text(64 - 3 * k, y, dc.name, 1, tc);

    p = buf;                                // memory number, 5 characters: "*M20 ", "M199 ", "VFO  "
    if (sel && wxm) p = cat(p, "WX");       // weather mode, lit so it's plain which slot is on it
    else if (cfg.s[i].mem) {
        k = cfg.s[i].chn;
        if (chan_in_scan(k)) *p++ = '*';
        *p++ = 'M';
        p = num(p, k);
    } else p = cat(p, "VFO");
    pad_to(p, 5);
    slot_label(y, sel && wxm ? P_ACC : P_DIM);

    k = 0;
    dec(buf, live && tx ? dc.tx : dc.rx, 8, 5);   // all 8 digits, once: the big ones end at 1 kHz, and the
    buf[20] = buf[7]; buf[21] = buf[8]; buf[22] = 0; buf[7] = 0;   // two below it wait at buf + 20 for the small ones
    if (fast && shown_c[i] == pal[fc]) while (k < 7 && buf[k] == shown[i][k]) k++;
    if (k < 7) freq_text(k, y, fc);
    xcat(shown[i], buf); shown_c[i] = pal[fc];   // the colour, not the index: as drawn
    freq_text(20, y, fc);                   // the 100 Hz and 10 Hz digits, small after it, always: 146.520 00, 462.562 50

    p = cat(buf, mode_s[dc.mode]);          // mode, power, shift ... bandwidth; USB: receive only
    *p++ = ' ';
    if (dc.mode == MOD_USB) p = cat(p, "RXONLY");
    else {
    p = cat(p, pow_d[dc.power]);
    if (!dc.tx) p = cat(p, "TXLOCK");
    else if (dc.tx != dc.rx) {
        uint32_t d = dc.tx - dc.rx;         // both under 2^31: the sign bit is the shift's
        *p++ = '+';
        if ((int32_t)d < 0) { d = -d; p[-1] = '-'; }
        p = dec(p, d, 6 + (d >= 1000000UL) + (d >= 10000000UL), 5) - 2;   // 1-3 digits of MHz, to 10 Hz, its last two dropped: d / 100 without a long divide
        while (p[-1] == '0') p--;           // 0.600 -> 0.6, 5.000 -> 5
        if (p[-1] == '.') p--;
    } else if (!tx_allowed(dc.tx)) p = cat(p, "RXONLY");
    right(p, dc.narrow ? "NARR" : "WIDE", "");
    }
    text1(0, y + 27, P_DIM);

    p = buf;                                // tones ... step; DTMF digits or the tone heard take it over
    if (live && dtmf_n >= DTMF_RUN) { cat(buf, "DTMF "); p = xcat(buf + 5, dtmf); k = P_HI; }
    else if (live && heard && open) { cat(buf, "HEARD "); p = cat(tone_s(buf + 6, heard), " 6:copy"); k = P_OK; }
    else {
        k = P_DIM;
        if (dc.tx_tone && dc.tx_tone == dc.rx_tone) { cat(buf, "TSQL"); p = tone_s(buf + 4, dc.tx_tone); }
        else {
            if (dc.tx_tone) { buf[0] = 'T'; p = tone_s(buf + 1, dc.tx_tone); *p++ = ' '; }
            if (dc.rx_tone) { *p++ = 'R'; p = tone_s(p, dc.rx_tone); }
        }
        right(p, dc.mode == MOD_USB ? sstep_s[cfg.sstep] : step_s[cfg.step], "K");
        p = buf + 21;
    }
    pad_to(p, 21);
    text1(0, y + 36, k);
    theme_apply();                          // c_bg and c_dim back
}
#endif

__xdata uint8_t m_len;               // the meter bar's filled width, its colour and the empty track's
__xdata uint16_t m_col, m_trk, m_fg;   // m_fg: the readings' colour
// asm/draw_meter.asm (with m_fill, m_text and bar_x); this C is the reference (make CFUN=draw_meter builds it)
void draw_meter(void) __reentrant;
#ifdef C_DRAW_METER
// The bar from x a to b, filled up to m_len: the row above the text at METER_Y - 1, else the 8 text rows.
static void m_fill(uint8_t a, uint8_t b, uint8_t y) __reentrant
{
    uint8_t m = m_len < a ? a : m_len > b ? b : m_len, h = y < METER_Y ? 1 : 8;
    if (m > a) lcd_fill(a, y, m - a, h, m_col);
    if (b > m) lcd_fill(m, y, b - m, h, m_trk);
}
// Text inside the bar at x: in c_bg over the filled part, m_fg on the empty track, as two runs (a
// character takes the side its middle is on). An edge that falls in the text moves to that split,
// so the rows above and beside it (m_fill, drawn after) end where the text's do.
// ponytail: whole characters, so the bar's edge moves in 6 px steps under text; split a glyph if it shows.
static void m_text(uint8_t x, __xdata char *p) __reentrant
{
    __xdata char *q = p;
    uint8_t e = x;
    while (*q && (uint8_t)(e + 3) < m_len) { q++; e += 6; }
    char c = *q;
    if (c && m_len > x) m_len = e;
    *q = 0;
    if (q != p) lcd_text_bg(x, METER_Y, p, 1, c_bg, m_col);
    *q = c;
    if (c) lcd_text_bg(e, METER_Y, q, 1, m_fg, m_trk);
}

static uint8_t bar_x(uint8_t d) __reentrant { return d + (uint8_t)(d * 3) / 5; }   // d * 8 / 5 for d 0-85 (callers: 0-79): the bar's x for dBm + 130

void draw_meter(void) __reentrant
{
    uint8_t len = 0, s, a = 31;
    m_fg = c_fg;                            // the readings: full brightness on a tinted track, dim on noise
    if (tx) {                               // mic level, log scale, and the TX clock at the right end
        m_open = 0xFF;
        uint16_t m = bk_mic();
        while (m) { len += 8; m >>= 1; }
        s = tots[cfg.tot];
        m = secs(&t_tx);
        if (s) { s -= m; if (s <= 10) m_fg = RED; } else s = m;
        cat(buf, "   ");
        u2s(buf + 3, s, 3);
        buf[6] = 's'; buf[7] = buf[8] = 0;  // "   116s"; no left text: the bar from x 0
        a = 0;
        m_col = RED;
    } else {
        // S9 = -93 dBm, 6 dB per S-unit, read the usual way: S7, S9, S9+20. The bar spans -130..-50
        // dBm: green with the squelch open, the accent at half brightness on noise, so noise never
        // reads as a signal.
        if (dbm == m_dbm && open == m_open) return;   // drawn 10 times a second: only when something changed
        m_dbm = dbm; m_open = open;
        if (dbm > -130) len = dbm >= -50 ? W : bar_x(dbm + 130);
        s = dbm > 0 ? 0 : dbm < -199 ? 199 : -dbm;
        u2s(buf + 1, s, 3);
        for (a = 0; a < 2 && buf[a + 1] == '0'; a++) buf[a] = ' ';   // "-102", " -99", "  -5"
        buf[a] = '-';
        a = 31;
        cat(buf + 4, "dBm");
        if (dbm > -93) {
            s = dbm + 93 > 99 ? 99 : dbm + 93;
            pad_to(num(cat(buf + 8, "S9+"), s), 13);
        } else { buf[8] = 'S'; buf[9] = '0' + (dbm < -147 ? 0 : (uint8_t)(dbm + 147) / 6); pad_to(buf + 10, 13); }   // S0-S9
        if (!open) m_fg = c_dim;
        m_col = open ? c_ok : c_acc >> 1 & 0x7BEF;   // noise: the accent at half brightness (each 565 field halved)
    }
    // The bar is 9 rows, the text's 8 under one more, so the letters (blank row at their foot) sit
    // centred; "S9+11" at x 1-30 and "-102dBm" at x 86-127 (each glyph's blank column on its right).
    m_len = len;                            // the track in the tint of the slot heard or transmitting, else plain
    s = tx ? T_TX : open ? T_RX : P_BAR;
    m_trk = theme_c(s);
    m_text(1, buf + 8);
    m_text(86, buf);
    m_fill(0, W, METER_Y - 1);
    m_fill(0, 1, METER_Y);
    m_fill(a, 86, METER_Y);
    if (cfg.sql >= 10 && !tx) {             // S1-S9 squelch: a tick where it opens, to show a station's margin
        int8_t d = cfg.sql * 6 - 71;        // its dBm + 130
        lcd_fillp(d < 0 ? 0 : bar_x(d), METER_Y - 1, 1, 9, P_FG);
    }
}
#endif

void draw_bottom(const char *msg, uint8_t c) __reentrant   // c: a P_* index
{
    lcd_fillp(0, BOT_Y, W, 8, P_BG);
    lcd_text(0, BOT_Y, msg, 1, c);
}

void dt_line(void) __reentrant      // the bottom line while transmitting: "DTMF 146#", "DTMF CODE 1-8?"
{
    __xdata char *p = cat(buf, "DTMF ");
    if (dt_mem) cat(p, "CODE 1-8?"); else xcat(p, dtmf);
    draw_bottom(buf, P_HI);
}

const __code char *__data note;                 // one-off message on the bottom line, gone at the next key or after 3 s
static uint16_t t_note;                     // when it went up: 0 not yet stamped (main() does)
static const char __code low_s[] = "LOW BATTERY";   // the one note that stays until a key

uint16_t batt_cal(void) { return batt_mv() + 20 * cfg.bcal; }   // mV with BATTERY CAL's 20 mV steps

// Battery percent along a 2S lithium-ion pack's discharge curve: (mV - 6000) / 10 at 0, 10 ... 100%
// (6.8 V, where LOW BATTERY warns, to 8.4 V). Most of the charge sits between 7.4 and 8.0 V.
uint8_t batt_pct(uint16_t mv) __reentrant
{
    static const __code uint8_t curve[] = {80, 136, 148, 154, 158, 164, 174, 184, 196, 212, 240};
    uint8_t i = 0, v;
    if (mv <= 6800) return 0;
    if (mv >= 8400) return 100;
    v = (mv - 6000) / 10;
    while (v >= curve[i + 1]) i++;
    uint8_t d = v - curve[i];
    return (uint8_t)(i * 10) + (uint16_t)(d * (uint8_t)10) / (uint8_t)(curve[i + 1] - curve[i]);
}

__xdata char *groups_s(__xdata char *p, uint8_t word) __reentrant;

// asm/draw_status.asm; this C is the reference (make CFUN=draw_status builds it)
void draw_status(void) __reentrant;
#ifdef C_DRAW_STATUS
void draw_status(void) __reentrant
{
    static const __code char *const __code torch_s[] = {"", "MOON ", "STROBE ", "SOS ", "LOW ", "MED ", "HIGH "};
    __xdata char *p = buf;
    uint8_t n, pct;
    int16_t mv;
    // the bottom line: a message, or what's being typed, else empty
    if (tx && (dtmf_n || dt_mem)) dt_line();
    else if (note) { draw_bottom(note, P_RED); lcd_icon(120, BOT_Y, icon_warn, P_RED); }
    else if (saving || entry_n) {
        p = cat(buf, overwrite ? "REPLACE " : saving ? "SAVE CH " : sl_mem() ? "CHANNEL " : "FREQ ");
        if (saving && !entry_n) p = u2s(p, saving, 3) + 3;
        for (uint8_t i = 0; i < entry_n; i++) { if (i == 3 && !sl_mem()) *p++ = '.'; *p++ = entry[i]; }
        cat(p, saving ? "? MENU=YES" : "");    // "SAVE CH 005? MENU=YES", "REPLACE 003? MENU=YES"
        draw_bottom(buf, overwrite ? P_RED : P_HI);
    } else lcd_fillp(0, BOT_Y, W, 8, P_BG);

    // the status bar: the squelch level first, so it never moves, then what's on, then lock and
    // Bluetooth icons, battery % and its icon
    p = level_s(cat(buf, "SQ"), cfg.sql);
    *p++ = ' ';
    if (scanning) {
        p = cat(p, "SCAN");
        if (sl_mem() && cfg.group) { *p++ = ' '; p = groups_s(p, 0); }   // "SCAN G13": groups 1 and 3
        *p++ = ' ';
    }
    {
        static const __code char *const __code flag_s[] = {"DW ", "PRI ", "WX ", "MON ", "REV ", "ATT ", "VOX "};
        uint8_t f = cfg.dw | (cfg.prio != 0) << 1 | (cfg.wx != 0) << 2 | mon << 3 | rev << 4 | cfg.att << 5 | (cfg.vox != 0) << 6;
        for (uint8_t i = 0; i < 7; i++) if (f >> i & 1) {
            p = cat(p, flag_s[i]);
            if (i < 2 && i + 1 == peek) { p[-1] = '!'; *p++ = ' '; }   // "DW! ", "PRI! ": PEEK_OTHER, PEEK_PRIO
        }
    }
    n = torch;                              // the flashlight's mode, or its level when steady
    if (n == 1 && tlevel) n = 3 + tlevel;
    p = cat(p, torch_s[n]);
    p[-1] = 0;                              // every item ends in a space: drop the last
    mv = batt_cal();
    pct = batt_pct(mv);
    n = 86 - 9 * (locked + cfg.bt + rang);  // the icons sit left of the %; text stops there
    buf[n / 6] = 0;
    uint8_t dim = pct <= 20 ? P_RED : P_DIM;   // the bar stays plain: the LEDs and the slot tints show RX and TX
    lcd_fillp(0, 0, W, 9, P_BG);
    text1(0, 0, P_ACC);
    if (locked) { lcd_icon(n, 1, icon_lock, cfg.klock ? P_RED : P_ACC); n += 9; }   // red: PTT locked too
    if (cfg.bt) { lcd_icon(n, 0, icon_bt, P_ACC); n += 9; }
    if (rang) lcd_icon(n, 0, icon_bell, P_ACC);
    n = num(buf, pct) - buf;                // its digits, then %, right-aligned to the battery
    buf[n] = '%'; buf[n + 1] = 0;
    text1(104 - 6 * n, 0, dim);
    lcd_fillp(112, 1, 12, 7, dim);          // battery: a 12x7 case and its tip, filled in 4 steps, red when nearly empty
    lcd_fillp(113, 2, 10, 5, P_BG);
    lcd_fillp(124, 3, 2, 3, dim);
    lcd_fillp(114, 3, (uint8_t)(pct + 12) / 25 * 2, 3, dim);
}
#endif

static void draw_main(void)
{
    uint8_t now_ok = rx_settled();          // before the slots' 40 ms: a retune this pass reads unsettled here
    if (alert) {
        lcd_clear();
        static const __code label_t l[] = {{1, 20, 3, P_RED, "WEATHER"}, {4, 50, 3, P_RED, "ALERT"}, {0, BOT_Y, 1, P_DIM, "any key to stop"}};
        draw_labels(l, 3);
        lcd_icon(60, 6, icon_bell, P_RED);
        freq_s(buf, ch.rx);
        lcd_text(4, 86, buf, 2, P_FG);
        return;
    }
    lcd_fillp(0, 9, W, 1, P_BAR);            // under the status bar
    for (uint8_t i = 0; i < 2; i++) {       // each slot, then the rows between it and the meter in its tint
        draw_slot(i);                       // (bg when none): a slot heard or transmitting runs to the meter
        lcd_fill(0, i ? 70 : 58, W, 3 - i, theme_c(tinted[i]));
    }
    m_open = 0xFF;                          // just retuned: the main loop draws the new channel's first settled reading
    if (now_ok) draw_meter();
    draw_status();
}

// CALL ID: a callsign, sent in Morse when PTT is let go on a ham band, at most once in 10 minutes
// (the first time after power-on). 8 characters at CALL_ADDR, NUL after the last, as CH NAME saves a
// name; blank (a new radio's FF too) is OFF. 0x1E00 is in a gap that stock never reads and CHIRP's
// TD-H3 map leaves out.
#define CALL_ADDR 0x1E00

// ---------- menu ----------
static const __code char *const __code mnames[M_N] = {
    "SQUELCH", "ATTENUATOR", "COMPANDER", "NARROW kHz", "BANDWIDTH", "MODE", "STEP kHz", "RX TONE", "POWER", "SHIFT", "OFFSET MHz", "TX TONE", "TX BANDS", "TX TIMEOUT", "TAIL TONE", "MIC GAIN", "VOX", "VOX DELAY", "DTMF CODES", "CALL ID",
    "SCAN GROUP", "SCAN PAUSE", "SCAN RESUME", "DUAL WATCH", "PRIORITY", "GROUP LINK", "CH GROUPS",
    "SCOPE", "FREQ FIND", "FM RADIO", "WEATHER", "LAST HEARD", "CH NAME", "CH TO VFO", "DELETE CH",
    "LIGHT", "BRIGHTNESS", "THEME", "DIGITS", "KEY BEEP", "KEY LOCK", "BELL", "WX ALERT", "AUTO OFF", "BATT SAVE",
    "PC LINK", "REMOTE", "BLUETOOTH", "FREQ CAL", "METER CAL", "BATT CAL", "VERSION",
};


static void cal_s(uint8_t k)                // buf + 26: FREQ CAL "+0.5 ppm", METER CAL "+5 dB", "-12 dB", BATT CAL "+7.40 V"
{
    static const __code char *const __code unit[] = {" ppm", " dB", " V"};
    static const __code uint8_t w[] = {2, 1, 3}, frac[] = {1, 0, 2};
    int8_t c = (&cfg.fcal)[k];              // fcal, mcal, bcal: neighbours
    // dB: 1 or 2 digits; ppm: 2, or 3 for +-10.0 (it showed "+0.0")
    cat(sdec(buf + 26, k == 2 ? 2 * c : c, w[k] + (k == 1 ? (uint8_t)(c + 9) > 18 : !k && (uint8_t)(c + 99) > 198), frac[k]), unit[k]);
}

static void txb_s(void) __reentrant         // buf + 26: TX BANDS, HAM GMRS; with three or four on, H G M F
{
    static const __code char *const __code bn[] = {"HAM", "GMRS", "MURS", "FRS"};
    __xdata char *p = buf + 26;
    uint8_t b, k = 0xE880u >> cfg.txb & 1;   // three or four bits on: txb 7, 11, 13, 14, 15
    for (b = 0; b < 4; b++) if (cfg.txb >> b & 1) {
        __xdata char *e = p + (p != buf + 26);
        *p = ' ';
        p = cat(e, bn[b]);
        if (k) *(p = e + 1) = 0;            // its initial only
    }
    if (p == buf + 26) cat(p, "NONE");
}

static void menu_value(uint8_t i) __reentrant   // into the back half of buf, buf + 26
{
    __xdata char *p = buf + 26;
    if (i >= M_BW && i <= M_TXT && sl_mem()) p = cat(p, "VFO ");   // these set the VFO, not the channel
    if (i == M_TXB) { txb_s(); return; }
    const __code item_t *it = &items[i];
    if (it->n && i != M_WX) {
        uint8_t v = item_get(i);
        if (it->lab) cat(p, it->lab[v]); else if (i == M_VOX && !v) cat(p, "OFF"); else level_s(p, v);
        return;
    }
    __xdata char *const v = buf + 26;       // the other items have no VFO in front
    if (i == M_RXT || i == M_TXT) tone_s(p, tone_of(i == M_RXT ? cfg.rxt : cfg.txt));
    else if (i == M_GROUP || i == M_GLINK) { if (cfg.group) groups_s(v, 1); else cat(v, "SCAN LIST"); }
    else if (i == M_OFFS) dec(p, off_n(ch_uhf()) * 5, 4, 2);   // " 0.60": 10 kHz units
    else if (i == M_PRIO && cfg.prio) { cat(v, "CH"); u2s(v + 2, cfg.prio, 3); }
    else if (i == M_WX && cfg.wx) freq_s(v, wx_freq(cfg.wx - 1));
    else if ((uint8_t)(i - M_FCAL) < 3) cal_s(i - M_FCAL);
    else if (i == M_CALL) { if (!name_read(CALL_ADDR, v)) cat(v, "OFF"); }
    else cat(v, i == M_VER ? VERSION : i == M_PRIO || i == M_WX ? "OFF" : ">");   // ">": the actions, SCOPE, CH NAME ...
}

// The menu is one list in sections (RECEIVE, TRANSMIT, ...), each under a dim heading row the
// cursor skips. Six rows 15 px apart around the selected item, each the name with its value
// right-aligned in the accent colour, the selected row a 12 px band; the section's name and where
// you are (12/43, items only) in the title, a scroll bar on the right, and under the list what the
// lit item does (help.c, lower case in the highlight colour). It opens where you left it.
// Two digits jump to that item (Kenwood). UP/DOWN move, MENU selects: a setting's value lights
// <like this> and UP/DOWN change it until MENU or V/M; an action (value ">") starts. V/M goes back.
#define MROWS 6
#define M_ACT(i) ((i) >= M_GLINK && (i) <= M_DEL || (i) == M_PC || (uint8_t)((i) - M_DTMF) < 2)   // DTMF CODES, CALL ID
static uint8_t mlast;
static __bit medit;                       // the selected item's value is being changed

// buf as a list row at y (rows 12 px apart), in a band of colour k when lit: one row over the text
// and a column either side, so the letters (blank row at their foot) sit centred in it.
static void list_row(uint8_t y, uint8_t sel, uint8_t k) __reentrant   // k: a P_* index
{
    if (!sel) k = P_BG;
    lcd_fillp(0, y - 1, W, 1, k);
    lcd_fillp(0, y, 1, 8, k);
    lcd_fillp(127, y, 1, 8, k);
    text_bg(1, y, sel ? P_BG : P_FG, k);
}

// Sections: each starts at its first item, its name a dim row the cursor skips and the title above.
#define NSEC 6
#define NROW (M_N + NSEC)
static const __code char *const __code sname[NSEC] = {"RECEIVE ", "TRANSMIT ", "SCAN ", "TOOLS ", "DISPLAY & SOUND ", "SETUP "};
static const uint8_t __code sfirst[NSEC] = {M_SQL, M_POW, M_GROUP, M_SCOPE, M_LIGHT, M_PC};

static uint8_t name_val(const __code char *name) __reentrant   // buf: the name, then the value (in buf + 26) right-aligned to 21; the value's column
{                                                         // no value: a dashed rule to the end
    uint8_t n = cat(buf, name) - buf, v = 0;
    char pad = buf[26] ? ' ' : '-';
    while (buf[26 + v]) v++;
    while (n < (uint8_t)(21 - v)) buf[n++] = pad;
    xcat(buf + n, buf + 26);
    buf[21] = 0;
    return n;
}

static void menu_row(uint8_t i, uint8_t y) __reentrant   // the name, the value in the accent colour; lit: inverse
{                                                        // i | 0x80: the heading of section i, a dim rule
    uint8_t n;
    if (i & 0x80) { buf[26] = 0; name_val(sname[i & 7]); text1(0, y, P_DIM); return; }
    menu_value(i);                          // the value, in the back half of buf
    n = name_val(mnames[i]);
    if (i == menu) text_bg(0, y, P_BG, medit ? P_FG : P_ACC);   // lit (white: its value is changing)
    else {                                  // the value again in the accent colour. REMOTE has it in the row: not sent,
        text1(0, y, P_FG);
        uint8_t m = mirror;                 // as a mirrored draw holds the keys until the serial port has it
        mirror = 0;
        lcd_text(6 * n, y, buf + 26, 1, P_ACC);
        mirror = m;
    }
}

static const __code label_t hint[] = {      // the same length moving and changing: each covers the other
    {0, BOT_Y, 1, P_DIM, "\x7f\x80 MENU pick V/M back"}, {0, BOT_Y, 1, P_DIM, "\x7f\x80 change MENU done  "}};

static void menu_help(uint8_t i) __reentrant   // buf: what item i does, unpacked from help.c (three characters a word)
{
    const __code uint16_t *q = menu_help_bits;
    uint8_t c, k = 0, n = 0;
    uint16_t w = 0;                         // read first when k is 0: SDCC can't see that
    for (;; k--, w >>= 5) {
        if (!k) { w = *q++; k = 3; }
        c = w & 31;
        if (!c) { if (!i--) break; }        // a 0 ends each item's text
        else if (!i) buf[n++] = c < 27 ? '`' + c : " /'"[c - 27];   // lower case: a caption
    }
    pad_to(buf + n, 21);                    // over a longer one
}

static void draw_menu(void) __reentrant     // rows paint their own background: no full clear
{
    uint8_t s = 0, i = 0, p, y;
    while (s < NSEC - 1 && sfirst[s + 1] <= menu) s++;
    p = menu + s + 1;                       // the cursor's row in the list, headings included
    p = p < MROWS / 2 ? 0 : p > NROW - 1 - MROWS / 2 ? NROW - MROWS : p - MROWS / 2;   // the first row shown
    mlast = menu;
    u2s(buf + 26, menu + 1, 2); buf[28] = '/'; buf[29] = '0' + M_N / 10; buf[30] = '0' + M_N % 10; buf[31] = 0;   // "12/45"
    if (entry_n) { buf[26] = entry[0]; buf[27] = '-'; }   // the first digit of an item number
    name_val(sname[s]);
    text1(0, 0, P_ACC);
    text_pad = 2;                           // the rows' bands 12 px tall, 15 px apart
    for (s = 0, y = 15; y < 15 + 15 * MROWS; ) {
        uint8_t h = s < NSEC && i == sfirst[s];
        if (p) p--;
        else {
            menu_row(h ? 0x80 | s : i, y);
            y += 15;
        }
        if (h) s++; else i++;
    }
    text_pad = 0;
    p = mirror;                             // the scroll bar: where the cursor is in the list. Not sent to REMOTE,
    mirror = 0;                             // whose every record holds the keys
    lcd_fillp(126, 13, 2, 15 * MROWS, P_BAR);
    lcd_fillp(126, 13 + 2 * menu, 2, 6, P_ACC);
    mirror = p;
    menu_help(menu);                        // what the lit item does: a caption
    text1(0, BOT_Y - 12, P_HI);
    draw_labels(medit ? hint + 1 : hint, 1);
}

static void open_menu(void)
{
    medit = 0;
    lcd_clear();
    draw_menu();
}

// The computer is talking (clone protocol). Hand the radio over until it is done.
static void pc_link(void)
{
    remote = mirror = 0;                    // the clone protocol owns the port
    radio_off();
    lcd_clear();
    static const __code label_t l[] = {{16, 40, 2, P_HI, "PC LINK"}, {4, 70, 1, P_DIM, "waiting for computer"}, {4, BOT_Y, 1, P_DIM, "V/M to leave"}};
    draw_labels(l, 3);
    lcd_icon(4, 44, icon_link, P_HI);
    clone_session(cfg.bt);
    rkey = K_NONE;                          // its release byte went to the clone protocol: don't leave it held
    remote = cfg.remote;
    radio_init();
    load_prio();
}

static uint16_t wrap(uint16_t v, int8_t d, uint16_t n) __reentrant { return d > 0 ? (v + 1 >= n ? 0 : v + 1) : (v ? v - 1 : n - 1); }
static int8_t clamp8(int8_t v, int8_t d, int8_t lim) __reentrant { v += d; return v > lim ? lim : v < -lim ? -lim : v; }

static void item_step(uint8_t i, int8_t d) __reentrant { item_set(i, wrap(item_get(i), d, items[i].n)); }   // the next or last of its values

static uint8_t sl_chan(uint8_t n) __reentrant { SL.mem = 1; return SL.chn = n; }   // the selected slot on channel n

static void set_vfo(uint32_t f) __reentrant             // put f into the selected slot's VFO
{
    if (!f) return;
    wxm = 0;
    SL.mem = 0;
    SL.vfo = f;
}

static void retune(void);
static void codes_screen(void);

static void fm_screen(void)
{
    if (fm_mode(&cfg.fm, cfg.wx)) { peek_turn = 9; t_peek = now() - 100; }   // the weather alert: look again now
    save_soon();                            // the station you left on
}

// Weather mode, as on the Kenwood and Yaesu handhelds: the selected slot listens to a NOAA channel
// (UP/DOWN WX1-WX7) until long # again puts it back as it was. On the way in it checks all seven
// and takes the strongest (WX SCAN), the last one used on a tie.
static void wx_tuned(void)                  // wx_ch changed: WX ALERT, when on, follows it
{
    if (cfg.wx) cfg.wx = wx_ch + 1;
    retune();
}

static int16_t wx_dbm(uint8_t n) __reentrant { return bk_dbm_at(wx_freq(n)); }

static void wx_mode(void) __reentrant   // from a key only: on_key has woken the receiver and ended any look
{
    wxm = !wxm;
    if (wxm) {
        int16_t best = wx_dbm(wx_ch), d;    // one reading each; the last one used wins a tie
        for (uint8_t i = 0; i < 7; i++) if ((d = wx_dbm(i)) > best) { best = d; wx_ch = i; }
    }
    wx_tuned();
}

// Last heard: the 8 newest frequencies the squelch opened on, newest first, with the memory
// channel they were (0: none) and when, in 10 s spells since power-on (up10, counted with the
// battery check). MENU -> LAST HEARD lists them; MENU tunes the selected slot to one. A scan's hits
// (the stations it stopped on) go to the top like any other, so while it runs they are the top
// lh_scan rows: stopping it picks from them.
typedef struct { uint32_t f; uint16_t at; uint8_t chn; } heard_t;
static __xdata heard_t lh[8];
static __xdata uint8_t lh_n, lh_scan;
static __xdata uint16_t up10;

static void heard_add(void)                 // the squelch just opened on ch
{
    const __xdata slot_t *x = &cfg.s[peek == PEEK_OTHER ? !cfg.act : cfg.act];
    uint8_t i, c = peek == PEEK_PRIO ? cfg.prio : x->mem && !wxm && peek != PEEK_WX ? x->chn : 0;
    for (i = 0; i < lh_n && !on_rx(&lh[i].f); i++) ;
    if (i == lh_n) { if (lh_n < 8) lh_n++; else i = 7; }   // new: one more row, or the oldest goes
    if (scanning && i >= lh_scan) lh_scan++;   // not a hit yet: one more on top (a hit again just moves up among them)
    {                                       // the rows above it move down one (no memmove: 200 bytes of library)
        __xdata uint8_t *b = (__xdata uint8_t *)lh;
        uint8_t n = i * sizeof *lh;
        while (n--) b[n + sizeof *lh] = b[n];
    }
    lh[0].f = ch.rx; lh[0].chn = c; lh[0].at = up10;
}

// LAST HEARD, the group lists and DELETE CH: rows 12 px apart from y 14, a lit cursor UP/DOWN
// move (round past the ends), MENU picks, V/M goes back; the same two key lines under every one.
static __data uint8_t lsel; static __bit lrows;        // the cursor's row; 1 = the rows need drawing

static uint8_t list_redraw(void)            // 1 = cleared, draw it all: first time, or a watching computer asks
{
    if (!(remote_req & REQ_REDRAW)) return 0;
    remote_req &= ~REQ_REDRAW;
    lcd_clear();
    lrows = 1;
    return 1;
}

static void list_open(void) { lsel = 0; remote_req |= REQ_REDRAW; }   // cursor on the first row, all drawn next

static uint8_t list_key(uint8_t n) __reentrant          // 10 ms, then a key; UP/DOWN move the cursor over n rows
{
    uint8_t k;
    delay_ms(10);
    k = key_event();
    if (n && UPDOWN(k)) { lsel = wrap(lsel, LIST_D(k), n); lrows = 1; }
    return k;
}

static void list_frame(const __code label_t *l) __reentrant   // after list_redraw's clear: the title, what MENU does, V/M
{
    static const __code label_t back = {0, BOT_Y, 1, P_DIM, "V/M back"};
    draw_labels(l, 2);
    draw_labels(&back, 1);
}

static void heard_row(uint8_t i) __reentrant   // "M12  146.520     3m"
{
    const __xdata heard_t *h = lh + i;
    uint16_t m = up10 - h->at;              // 10 s spells ago
    uint8_t c = h->chn, hr = m >= 360;      // an hour or more
    pad_to(buf, 21);
    if (c) { buf[0] = 'M'; *num(buf + 1, c) = ' '; }   // num ends in a NUL
    freq_s(buf + 5, h->f);
    pad_to(buf + 12, 21);                   // over freq_s's NUL and dec's 10 Hz digit and NUL: the age shows
    m /= hr ? 360 : 6;                      // hours (up to 182) or minutes
    c = m > 99 ? 99 : m;
    u2s(buf + 18, c, 2); buf[20] = hr ? 'h' : 'm';
    if (buf[18] == '0') buf[18] = c ? ' ' : '<';   // "<0m" reads "<1m": less than a minute
    if (!c) buf[19] = '1';
    list_row(14 + 12 * i, i == lsel, P_ACC);
}

static uint8_t heard_screen(void)          // the picked row (lsel), or 0xFF for V/M
{
    static const __code label_t l[] = {{0, 0, 1, P_ACC, "LAST HEARD"}, {0, BOT_Y - 12, 1, P_DIM, "\x7f\x80 move  MENU tune"}};
    uint8_t i, k;
    list_open();
    for (;;) {
        if (list_redraw()) {
            list_frame(l);
            if (!lh_n) lcd_text(0, 50, "nothing heard yet", 1, P_DIM);
        }
        if (lrows) { for (i = 0; i < lh_n; i++) heard_row(i); lrows = 0; }
        k = list_key(lh_n);
        if (k == K_EXIT) return 0xFF;
        if (k == K_MENU && lh_n) return lsel;
    }
}

static void heard_tune(void) __reentrant   // the selected slot onto row lsel: its channel if it was one, else its frequency
{
    const __xdata heard_t *h = lh + lsel;
    if (h->chn && chan_used(h->chn)) { wxm = 0; sl_chan(h->chn); }
    else set_vfo(h->f);
}

// "GROUP 3" (one) or "G135" (several), for the menu (word 1) and the status bar.
__xdata char *groups_s(__xdata char *p, uint8_t word) __reentrant
{
    uint8_t g = cfg.group, i;
    p = cat(p, word && !(g & (uint8_t)(g - 1)) ? "GROUP " : "G");
    for (i = 0; i < 8; i++) if (g >> i & 1) *p++ = '1' + i;
    *p = 0;
    return p;
}

// Eight groups with a mark on each one in g: MENU puts the lit one on or off, V/M returns the
// result (kept: like a setting, it counts as soon as it is changed). CH GROUPS (link 0): a channel's groups; GROUP LINK (1): the groups a scan covers.
static uint8_t group_pick(uint8_t g, uint8_t link) __reentrant
{
    static const __code label_t l[] = {{0, 0, 1, P_ACC, "CH GROUPS"}, {0, BOT_Y - 12, 1, P_DIM, "\x7f\x80 move  MENU on/off"}, {0, 0, 1, P_ACC, "GROUP LINK"}};
    uint8_t i, k;
    list_open();
    for (;;) {
        if (list_redraw()) {
            list_frame(l);
            if (link) draw_labels(l + 2, 1);
            if (!link) { u2s(buf, sl_chn(), 3); buf[3] = ' '; xcat(buf + 4, ch.name); text1(60, 0, P_DIM); }   // whose groups
        }
        if (lrows) {
            for (i = 0; i < 8; i++) {
                cat(buf, "GROUP 1           [ ]");
                buf[6] = '1' + i;
                if (g >> i & 1) buf[19] = '*';
                list_row(14 + 12 * i, i == lsel, P_ACC);
            }
            lrows = 0;
        }
        k = list_key(8);
        if (k == K_EXIT) return g;
        if (k == K_MENU) { g ^= 1 << lsel; lrows = 1; }
    }
}

static void groups_screen(void)             // CH GROUPS: written to the channel's byte 15 (and 13's mark) only
{
    uint8_t g = group_pick(ch.groups, 0);
    uint16_t a = 0x10 * sl_chn() + 13;     // bytes 13-15: our mark too, so all eight (FF) reads back
    __xdata uint8_t *b = (__xdata uint8_t *)buf;
    if (g != ch.groups && ee_read(a, b, 3)) {
        b[0] = b[0] & ~3 | GRP_OURS; b[2] = g;
        if (ee_write(a, b, 3)) { ch.groups = g; chime_ok(); }
    }
}

// DELETE CH: the memory channels as a list, on the selected slot's channel first. MENU asks
// "DELETE 012 GMRS1?" in red, MENU again deletes it, V/M keeps it. Until V/M, so several can go.

static __xdata char *ch_row(uint8_t n) __reentrant   // "012 GMRS1    462.562" into buf; the end of the name
{
    __xdata char *p;
    chan_load(n);
    pad_to(buf, 21);
    u2s(buf, n, 3); buf[3] = ' ';
    *(p = xcat(buf + 4, ch.name)) = ' ';
    freq_s(buf + 13, ch.rx);
    return p;
}

static void delete_screen(void) __reentrant   // reentrant: its temporaries on the stack, not in DATA
{
    static const __code label_t l[] = {{0, 0, 1, P_ACC, "DELETE CH"}, {0, BOT_Y - 12, 1, P_DIM, "\x7f\x80 move  MENU delete"}, {0, BOT_Y - 24, 1, P_RED, "DELETE"}, {0, BOT_Y - 12, 1, P_FG, "MENU = YES"}, {0, BOT_Y, 1, P_FG, "V/M  = NO, keep it"}};
    uint8_t n = sl_mem() ? sl_chn() : nxt(199, 1), k, sure = 0, i, c, cnt;
    if (!n) { note = "NO CHANNELS LOADED"; chime_no(); return; }
    remote_req |= REQ_REDRAW;
    for (;;) {
        if (list_redraw()) {
            if (sure) {                     // "DELETE 012 GMRS1?"
                draw_labels(l, 1);
                draw_labels(l + 2, 3);
                cat(ch_row(n), "?");
                text1(42, BOT_Y - 24, P_RED);
            } else list_frame(l);
        }
        if (lrows) {                        // up to 7 rows: all of them, or 3 either side of n
            for (cnt = 0, c = 0; cnt < 7; cnt++) { i = c; c = nxt(i ? i : 199, 1); if (c <= i) break; }   // round to the start: all counted
            c = n;
            if (cnt < 7) c = nxt(199, 1);
            else for (i = 0; i < 3; i++) c = nxt(c, -1);
            for (i = 0; i < cnt; i++, c = nxt(c, 1)) {
                ch_row(c);
                list_row(14 + 12 * i, c == n, sure ? P_RED : P_ACC);
            }
            lrows = 0;
        }
        k = list_key(0);
        if (k == K_EXIT && !sure) return;
        if (UPDOWN(k) && !sure) { n = nxt(n, LIST_D(k)); lrows = 1; }
        if (k == K_MENU && sure) {
            if (chan_delete(n)) chime_ok();
            load_prio();                    // the priority channel may be gone
            if (!(n = nxt(n, 1))) return;   // the last one: nothing left to list
        }
        if (k == K_MENU || k == K_EXIT) { sure ^= 1; remote_req |= REQ_REDRAW; }
    }
}

// CH NAME: the 8 name characters typed as on a phone. A key's letters, then its digit, one per
// press; another key, or the same one after a second, moves on. UP/DOWN step the letter under the
// cursor through ABC, and * and # move the cursor left and right, where they sit on the keypad.
// Saved to the name bytes only, as CHIRP writes them (NUL after the last character). CALL ID
// types its callsign with it too.
static const __code char *__code keyset[10] = {" 0", "-/.1", "ABC2", "DEF3", "GHI4", "JKL5", "MNO6", "PQRS7", "TUV8", "WXYZ9"};
static const __code char abc[] = " ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-/.";

static void name_screen(uint16_t at) __reentrant   // ch.name, saved to EEPROM at; titled as its menu item (CH NAME, CALL ID). The menu reloads ch after
{
    static const __code label_t l[] = {{0, BOT_Y - 24, 1, P_DIM, "0-9 or \x7f\x80 letter"}, {0, BOT_Y - 12, 1, P_DIM, "* < > #  MENU save"}, {0, BOT_Y, 1, P_DIM, "V/M back"}};
    uint8_t i, k, last = 0, n = 0, age = 0;     // age: 10 ms rounds left to tap the same key again
    for (i = 0; ch.name[i]; i++) ;
    while (i < 8) ch.name[i++] = ' ';
    list_open();
    for (;;) {
        if (list_redraw()) { draw_labels(l, 3); lcd_text(0, 0, mnames[menu], 1, P_ACC); }
        if (lrows) {
            lcd_text(16, 50, ch.name, 2, P_FG);
            lcd_fillp(16, 68, 96, 2, P_BG);
            lcd_fillp(16 + 12 * lsel, 68, 10, 2, P_ACC);   // the cursor
            lrows = 0;
        }
        k = list_key(0);
        if (age) age--;
        if (k == K_EXIT) return;
        if (k == K_MENU) break;
        if (UPDOWN(k) || k == K_STAR || k == K_HASH) { last = 0; lrows = 1; }
        if (k == K_STAR && lsel) lsel--;
        if (k == K_HASH && lsel < 7) lsel++;
        if (UPDOWN(k)) {
            for (i = 0; abc[i] && abc[i] != ch.name[lsel]; i++) ;
            ch.name[lsel] = abc[wrap(abc[i] ? i : 0, DIR(k), sizeof abc - 1)];
        }
        if (DIGIT(k)) {
            const __code char *s = keyset[k - K_0];
            if (k == last && age) { if (!s[++n]) n = 0; }
            else { if (last && lsel < 7) lsel++; n = 0; }
            ch.name[lsel] = s[n];
            last = k;
            age = 100;                      // about a second
            lrows = 1;
        }
    }
    for (i = 8; i && ch.name[i - 1] == ' '; ) ch.name[--i] = 0;
    if (ee_write(at, (uint8_t *)ch.name, 8)) chime_ok();
}

// CH TO VFO, the big rigs' "MR to VFO": the selected slot's channel into its VFO, and the VFO
// settings take its tones, shift, bandwidth and power. A shift OFFSET can't hold (not a multiple of 50 kHz, or over 12.75 MHz) comes over as simplex.
static void to_vfo(void) __reentrant
{
    uint32_t d = ch.tx - ch.rx;
    uint8_t o, u = ch_uhf(), up = (int32_t)d > 0;   // frequencies are under 2^31
    if (!up) d = -d;
    cfg.rxt = index_of(ch.rx_tone);
    cfg.txt = index_of(ch.tx_tone);
    cfg.narrow = ch.narrow;
    cfg.power = ch.power;
    cfg.shift = 0;
    for (o = 1; o; o++) if (off_d(o) == d) { cfg.off[u] = o; cfg.shift = 2 - up; }
    set_vfo(ch.rx);
}

static void menu_change(int8_t d) __reentrant
{
    const __code item_t *it = &items[menu];
    uint8_t leave = (uint8_t)(menu - M_SCOPE) <= M_WXM - M_SCOPE || menu == M_PC || menu == M_TOVFO;   // the tools and what tunes go home

    if (it->n) { item_step(menu, d); theme_apply(); tx_prefs(); BT_ON = cfg.bt; }
    else switch (menu) {
    case M_OFFS: { uint8_t u = ch_uhf(); cfg.off[u] = wrap(off_n(u) - 1, d, 255) + 1; break; }
    case M_RXT: case M_TXT: { __xdata uint16_t *t = menu == M_RXT ? &cfg.rxt : &cfg.txt; *t = wrap(*t, d, NTONE_MENU); break; }
    case M_GROUP: {                         // one at a time, as always: SCAN LIST, GROUP 1-8
        uint8_t i = 0;
        if (cfg.group && !(cfg.group & cfg.group - 1)) for (i = 1; cfg.group >> (i - 1) != 1; i++) ;
        i = wrap(i, d, 9);
        cfg.group = i ? 1 << (i - 1) : 0;
        break;
    }
    case M_GLINK: cfg.group = group_pick(cfg.group, 1); break;
    case M_PRIO: {                          // OFF, then each valid channel, then OFF again
        uint8_t n = nxt(cfg.prio ? cfg.prio : d > 0 ? 199 : 1, d);
        if (cfg.prio && (d > 0 ? n <= cfg.prio : n >= cfg.prio)) n = 0;
        cfg.prio = n;
        load_prio();
        break;
    }
    case M_SCOPE: set_vfo(scope(ch.rx, steps[cfg.step])); break;
    case M_GRP: case M_TOVFO: case M_NAME:   // the channel afresh: ch may be reversed or weather
        if (!sl_mem() || !chan_load(sl_chn())) { note = "MEMORY CHANNELS ONLY"; chime_no(); leave = 1; }
        else if (menu == M_GRP) groups_screen();
        else if (menu == M_NAME) name_screen(0x0D40 + 8 * (sl_chn() - 1));
        else to_vfo();
        break;
    case M_LH: {
        if (heard_screen() == 0xFF) break;
        leave = 1;
        heard_tune();
        break;
    }
    case M_DEL: delete_screen(); break;
    case M_DTMF: codes_screen(); break;
    case M_CALL: name_read(CALL_ADDR, ch.name); name_screen(CALL_ADDR); break;
    case M_FF: set_vfo(freq_find()); break;
    case M_FM: fm_screen(); break;
    case M_WXM: wx_mode(); break;
    case M_FCAL: case M_MCAL: case M_BCAL: {   // fcal, mcal, bcal: neighbours, +-100, +-40, +-50
        static const __code int8_t lim[] = {100, 40, 50};
        __xdata int8_t *c = &cfg.fcal + (uint8_t)(menu - M_FCAL);
        *c = clamp8(*c, d, lim[menu - M_FCAL]);
        break;
    }
    case M_PC: pc_link(); break;
    }
    save_soon();
    stamp(&t_key);
    if (leave) {
        menu = 0xFF;
        lcd_clear();
        retune();
        return;
    }
    if (M_ACT(menu)) { load_ch(); open_menu(); }   // V/M from a list: back to the menu, ch the slot's again
    else draw_menu();                       // THEME: the title and help lines take the new colours too (no clear)
}

// ---------- actions ----------
static void unpeek(void)                    // back to the selected slot after a peek
{
    if (peek == PEEK_OTHER) from_ch(&other);
    if (peek) { to_ch(&main_ch); radio_tune(); open = 0; }   // squelch() opens it again (and its audio)
    peek = PEEK_NONE;
    if (alert) lcd_clear();                 // the full-screen alert: draw_main paints only its own cells
    alert = 0;
}

static void retune(void)
{
    peek = PEEK_NONE;
    alert = vox = 0;                        // the chip starts listening afresh
    load_ch();
    open = 0;
    heard = 0;
    t_hit = 0;
    dirty = 1;
    save_soon();
}

static uint8_t skipped(void) __reentrant      // ch.rx is one of the skipped
{
    for (uint8_t i = 0; i < nskip; i++) if (on_rx(&skip[i])) return 1;
    return 0;
}

// A scan starts: where it starts (the channel, or the VFO frequency) kept for stopping, no hits yet.
// A VFO scan keeps to the band it starts in. Outside the bands: between A and B when the other slot is
// a frequency on the same side of 300 MHz, else 1 MHz either side of the start (Kenwood's MHz scan).
static void scan_begin(void) __reentrant      // reentrant: its temporaries on the stack, not in DATA
{
    const __xdata slot_t *o = &cfg.s[cfg.act ^ 1];
    uint8_t b;
    lh_scan = 0;
    xcpy(&scan_from, sl, sizeof scan_from);
    b = band_of(scan_r[0] = scan_r[1] = SL.vfo);
    if (b < NBANDS) { scan_r[0] = bands[b][0]; scan_r[1] = bands[b][1]; }
    else if (!o->mem && o->vfo != scan_r[0] && (o->vfo < 30000000UL) == (scan_r[0] < 30000000UL))
        scan_r[o->vfo > scan_r[0]] = o->vfo;                    // the other slot is the far end
    else { scan_r[0] -= 100000UL; scan_r[1] += 100000UL; }    // 1 MHz either side
}

#define STEP_833 9                          // steps[9]: 8.33 kHz, worked in thirds of 10 Hz on the 25 kHz raster
static uint8_t is833(void) { if (ch.mode == MOD_USB) return 0; return cfg.step == STEP_833; }   // no &&: SDCC kept its result in a bit
static uint16_t raster(void) { return ch.mode == MOD_USB ? ssteps[cfg.sstep] : is833() ? 2500 : steps[cfg.step]; }   // the step, 10 Hz
static uint32_t snap(uint32_t f) __reentrant { uint16_t s = raster(); return f / s * s; }   // down onto the raster

// Next frequency or channel. A scan uses the group or scan list, keeps a VFO inside its range
// and steps over skipped frequencies.
static void step_freq(int8_t d) __reentrant
{
    uint8_t tries = 10;
    do {
        if (sl_mem()) {
            uint8_t n = chan_next(sl_chn(), d, scanning, cfg.group);   // groups are banks: UP/DOWN stay in them; a scan skips what's off the scan list
            if (scanning && n == sl_chn()) return;    // a one-channel group: listen, don't retune it every 30 ms
            if (n) sl_chan(n); else note = "GROUP IS EMPTY";
        } else {
            uint32_t f = SL.vfo, lo = 1800000UL, hi = 99999000UL;
            uint16_t s = raster();
            if (scanning) { lo = scan_r[0]; hi = scan_r[1]; }
            uint8_t m = is833();                    // 8.33 kHz: the 25 kHz / 3 raster, in thirds of 10 Hz
            if (m) f += f + f + d;                  // (+-1: past the rounding of the last point)
            f = snap(d > 0 ? f + s : f - 1);        // next point on the step raster
            if (m) f = (f + 1) / 3;                 // back to the nearest 10 Hz
            if (f > hi) f = snap(lo + s - 1); else if (f < lo) f = snap(hi);   // wrap inside the range (or 1.8-999.99 MHz)
                                                    // on a point of both rasters (every 25 kHz is an 8.33 one)
            SL.vfo = f;
        }
        keep_other = 1; retune(); keep_other = 0;
    } while (scanning && skipped() && --tries);
}

static uint32_t entry_value(void) __reentrant
{
    uint32_t v = 0;
    for (uint8_t i = 0; i < entry_n; i++) v = v * 10 + entry[i] - '0';
    return v;
}

static void entry_key(uint8_t k) __reentrant            // a digit, or K_EXIT: delete the last one
{
    if (k == K_EXIT) entry_n--; else entry[entry_n++] = '0' + k - K_0;
    dirty = 1;
}

static void menu_jump(void)                 // the item number typed in the menu
{
    uint8_t n = entry_value();
    entry_n = 0;
    if (n && n <= M_N) menu = n - 1; else chime_no();
    draw_menu();
}

static void entry_done(void) __reentrant
{
    uint32_t v = entry_value();
    if (sl_mem()) {
        if (v >= 1 && v <= 199 && chan_load((uint8_t)v)) sl_chan(v);
        else note = "NO SUCH CHANNEL";
    } else {
        for (uint8_t i = entry_n; i < 8; i++) v *= 10;   // typed digits are MHz first; then kHz -> 10 Hz
        // Six digits stop at 1 kHz, so 12.5 kHz channels like GMRS 1 (462.5625) can't be typed whole: a
        // last digit of 2 or 7 gets its half kilohertz, as other radios fill it in (462.562 -> 462.5625).
        if (entry_n == 6 && (entry[5] == '2' || entry[5] == '7')) v += 50;
        if (v >= 1800000UL) set_vfo(v);        // six digits cap it at 999.999 MHz (typing ended weather mode)
        else note = "18-999 MHz ONLY";
    }
    entry_n = 0;
    retune();
}

static void save_done(uint8_t ok) __reentrant           // write the tuned slot into the suggested or typed channel
{
    uint32_t v = entry_n ? entry_value() : saving;
    uint8_t n = v <= 199 ? v : 0;            // a typed 300 must not wrap to channel 44
    if (ok && !overwrite && chan_used(n)) { saving = n; entry_n = 0; overwrite = 1; dirty = 1; return; }   // ask before writing over it
    saving = 0;
    entry_n = 0;
    overwrite = 0;
    if (ok) {
        uint8_t r = rev;
        rev = 0; fill_ch(cfg.act); rev = r;   // saved the right way round: reverse only listens on the input
        if (!sl_mem()) freq_s(ch.name, ch.rx);   // "146.520", not "2M" on every channel
        ok = n >= 1 && n <= 199 && chan_save(n) ? 2 : 1;
        load_prio();                        // it may have been the priority channel
    }
    retune();
    if (ok) {
        draw_main();                        // before the message: draw_status builds the status bar in buf
        dirty = 0;
        if (ok == 2) { cat(buf, "SAVED CH "); u2s(buf + 9, n, 3); draw_bottom(buf, P_HI); chime_ok(); }
        else { draw_bottom("SAVE FAILED", P_RED); chime_no(); }
    }
}

static void scan_home(void) { xcpy(sl, &scan_from, sizeof scan_from); retune(); }   // back to where the scan started

// A key stops the scan. Paused on a station: it stays (the newest hit). Else back to where it started
// with no hit, onto the one hit, or LAST HEARD with the newest hit lit to pick from (V/M there: the start).
static void scan_stop(void)
{
    uint8_t n = lh_scan;
    if (!scanning) return;
    scanning = 0;
    dirty = 1;
    if (t_hit) return;                      // heard here since it tuned here: paused on it
    lsel = 0;                               // the newest hit
    if (n > 1) { if (heard_screen() == 0xFF) n = 0; lcd_clear(); fast = 0; }   // (not paused: the audio is off)
    if (n) { heard_tune(); retune(); }
    else scan_home();
}

static void on_long(uint8_t k) __reentrant
{
    switch (k) {
    case K_MENU: scan_stop(); if ((saving = chan_free())) { entry_n = 0; dirty = 1; } else chime_no(); break;   // save what it stopped on
    case K_EXIT:                            // scan list in/out; while scanning, off it for good (Kenwood's lockout)
        if (sl_mem()) {                     // and on: a frequency, skipped until power-off, the oldest of 8 dropped
            chan_scan_toggle(sl_chn()); dirty = 1;
            if (!scanning) break;
            note = "CHANNEL SKIPPED";
        } else if (scanning) {
            if (nskip == 8) xcpy(skip, skip + 1, 28), nskip = 7;
            xcpy(&skip[nskip++], &ch.rx, 4);
        } else break;
        step_freq(sdir);
        break;
    case K_1: mon ^= 1; dirty = 1; break;                                      // monitor
    case K_2: if (sl_mem()) chan_power(sl_chn()); else item_step(M_POW, 1); retune(); break;   // a channel's own, saved
    case K_3: scan_stop(); menu = M_SCOPE; menu_change(1); break;   // the tools: a scan would retune off their pick
    case K_4: if (ch.mode == MOD_USB) cfg.sstep++; else item_step(M_STEP, 1); dirty = 1; save_soon(); break;
    case K_5: rev ^= 1; retune(); break;                                       // reverse
    case K_6:                                                                  // copy heard tone
        if (heard && !sl_mem()) { cfg.rxt = cfg.txt = index_of(heard); retune(); chime_ok(); }
        break;
    case K_7: scan_stop(); menu = M_FF; menu_change(1); break;
    case K_8: scan_stop(); menu = M_FM; menu_change(1); break;
    case K_9: cfg.dw ^= 1; dirty = 1; save_soon(); break;
    case K_0: if (!sl_mem()) { cfg.narrow ^= 1; retune(); } break;
    case K_HASH: scan_stop(); wx_mode(); break;                              // weather mode in/out
    }
}

static __bit click;                          // a key click is due

static uint8_t empty(uint8_t g) __reentrant   // g as cfg.group has it: 1 = no channel in it to scan (the note says so)
{
    if (chan_next(sl_chn(), 1, 1, g)) return 0;
    note = g ? "GROUP IS EMPTY" : "SCAN LIST EMPTY";
    return 1;
}

static void on_key(uint8_t k) __reentrant
{
    uint8_t lng = k & K_LONG;
    k &= ~K_LONG;
    stamp(&t_key);
    if (note) { note = 0; dirty = 1; }
    if (asleep) { wake(); }
    if (alert) { unpeek(); dirty = 1; return; }
    if (k == K_SIDE2) return;               // the flashlight: torch_run() times the presses, locked, in the menu, anywhere
    if (locked) {
        if (lng && k == K_STAR) { locked = 0; dirty = 1; chime_ok(); }
        else if (k < K_PTT) chime_no();
        return;
    }
    click = cfg.beep && k < K_PTT;   // played after the key's work: see main()
    if (peek) { unpeek(); dirty = 1; }   // (K_SIDE2 has returned)

    if (menu != 0xFF) {
        if (lng) return;
        if (DIGIT(k)) {                     // an item number: two digits, or one and a pause (main)
            medit = 0;
            entry_key(k);
            if (entry_n == 2 || (uint8_t)(k - K_0) > M_N / 10) menu_jump();
            else { draw_menu(); stamp(&t_key); }   // the second from when it shows: a REMOTE-mirrored draw takes ~0.8 s
            return;
        }
        entry_n = 0;
        if (UPDOWN(k)) {
            if (medit) menu_change(DIR(k));
            else { menu = wrap(menu, LIST_D(k), M_N); draw_menu(); }
        } else if (k == K_MENU && M_ACT(menu)) menu_change(1);
        else if (k == K_MENU && menu != M_VER || k == K_EXIT && medit) { medit ^= 1; draw_menu(); }
        else if (k == K_EXIT) { menu = 0xFF; lcd_clear(); retune(); }
        return;
    }
    if (saving) {
        if (lng) return;
        if (DIGIT(k) && entry_n < 3) { overwrite = 0; entry_key(k); }
        else if (k == K_MENU) save_done(1);
        else if (k == K_EXIT) { if (entry_n) entry_key(k); else save_done(0); }
        return;
    }
    if (lng && k == K_STAR) { locked = 1; scanning = 0; dirty = 1; return; }
    if (lng) { if (!entry_n) on_long(k); return; }
    if (wxm && (k <= K_9 || k >= K_EXIT)) wx_mode();   // a frequency, V/M, scan, A/B: weather mode ends first
    if (scanning && sl_mem() && (uint8_t)(k - K_0) < 9) {    // while scanning: 1-8 pick a group, 0 the scan list
        uint8_t g = k == K_0 ? 0 : 1 << (k - K_1);
        if (empty(g)) {                     // say so, keep scanning the old one
            chime_no();
            dirty = 1;
            return;
        }
        cfg.group = g;
        no_wait();                          // no resume wait: straight to the new group's next channel
        step_freq(sdir);
        dirty = 1;
        save_soon();
        return;
    }
    if (DIGIT(k)) {
        scanning = 0;
        entry_key(k);
        if (entry_n >= (sl_mem() ? 3 : 6)) entry_done();   // >=: PTT can change the slot mid-number
        return;
    }
    if (entry_n) {
        if (k == K_MENU) entry_done();
        else if (k == K_EXIT) entry_key(k);
        return;
    }
    switch (k) {
    case K_UP: case K_DOWN:                 // while scanning (Icom, Yaesu): scan that way, and move on now without the resume wait
        if (scanning) { no_wait(); sdir = DIR(k); }   // past the longest resume wait
        if (wxm) { wx_ch = (uint8_t)(wx_ch + (k == K_UP ? 1 : 6)) % (uint8_t)7; wx_tuned(); }   // weather mode: WX1-WX7
        else step_freq(DIR(k));
        break;
    case K_MENU: scanning = 0; open = 0; bk_af(0); menu = mlast; open_menu(); break;
    case K_HASH: scanning = 0; rev = 0; cfg.act ^= 1; retune(); break;           // A/B
    case K_STAR:                                                               // scan, or stop it
        if (scanning) { scan_stop(); break; }
        if (SQL_HELD) note = "SQUELCH IS OPEN";   // it would stop on every channel
        else if (sl_mem()) empty(cfg.group);
        if (note) { chime_no(); dirty = 1; break; }
        scanning = 1; sdir = 1; stamp(&t_scan); scan_begin(); dirty = 1; break;
    case K_EXIT:                                                                // V/M, or stop scan: back to its start
        if (scanning) { scanning = 0; scan_home(); break; }   // always back to the start
        rev = 0;
        SL.mem ^= 1;
        if (sl_mem() && !chan_load(sl_chn()) && !sl_chan(nxt(sl_chn(), 1))) {
            SL.mem = 0;
            note = "NO CHANNELS LOADED";
            chime_no();
        }
        retune();
        break;
    }
}

// ---------- receive ----------
// DTMF, every pass while receiving: digits heard as a run, shown for 10 s after the last (main)
static __xdata uint16_t irqs;               // the BK4819's interrupts this pass of the main loop
static void dtmf_poll(void) __reentrant
{
    uint16_t irq = irqs;         // read every pass (main) so a digit decoded from noise is cleared,
    char d = irq & BK_IRQ_DTMF ? bk_dtmf() : 0;
    if (!open) d = 0;            // but kept only while a station is heard
    if (irq & BK_IRQ_TAIL && open) t_tail = (uint8_t)now() | 1;   // the station's 55 Hz tail: squelch() shuts the audio
    uint16_t t = now(), e = t - t_dtmf;   // e: ticks since the last tone found
    if (d && dtmf_p && e < DTMF_GAP) {   // too soon for a new digit
        if (d != dtmf_p) { dtmf_p = 1; if (dtmf_n < DTMF_RUN) dtmf_n = 0; }   // speech: no digit
        t_dtmf = t; e = 0;
    } else if (d || (dtmf_p && e >= DTMF_GAP)) {
        if (dtmf_p > 1) {       // a whole tone; after the last line expired, a new line
            if (dtmf_n == 16) { xcpy(dtmf, dtmf + 1, 15); dtmf_n = 15; }   // (xcpy copies up from the start)
            dtmf[dtmf_n++] = dtmf_p; dtmf[dtmf_n] = 0;
            dtmf_new = 1;
        }
        dtmf_p = d;
        if (d) { t_dtmf = t; e = 0; }
    }
    if (dtmf_new && dtmf_n >= DTMF_RUN && e > DTMF_DRAW) {
        dtmf_new = 0;
        t_key = t;
        if (!dirty && !peek) fast = 1;   // only the slot's detail line changed
        dirty = 1;
    }
    if (dtmf_n && dtmf_n < DTMF_RUN && e > 300) dtmf_n = 0;   // no run followed
    if (dtmf_n && e >= 2000) { dtmf_n = 0; dirty = 1; }   // 10 s
}

static void squelch(void)
{
    uint8_t want, hw;
    if (!rx_settled()) { m_wait = 1; return; }   // just retuned: keep the meter and the squelch as they were
    m_wait = 0;
    hw = bk_sql_open();
    dbm = bk_dbm() + cfg.mcal;
    if (ch.mode) dbm += bk_am_gain(dbm);   // AM and USB
    if (t_tail && (!hw || (uint8_t)((uint8_t)now() - t_tail) > 100)) t_tail = 0;   // the carrier went (or 0.5 s passed)
    // S1-S9 (sql 10-18, Icom's S-meter squelch): the chip's squelch and the meter's level both, S9 = -93 dBm,
    // 6 dB an S-unit. Once open it closes 3 dB under that level, so a station right at it doesn't chop.
    want = SQL_HELD || alert || hw && !t_tail && (cfg.sql < 10 || dbm >= (uint8_t)(cfg.sql * 6) - 201 - (open ? 3 : 0));
    if (want && ch.rx_tone && !open && !mon && !alert) want = bk_tone_heard(ch.rx_tone);   // the tone opens it; the carrier keeps it open
    if (want != open) {
        open = want;
        bk_af(open);
        if (open) {
            t_hit = (now() - 1) | 1; heard_add();   // t_hit 0: nothing heard since the last retune; never a tick ahead (secs() would wrap)
            // BELL (Icom's pocket beep): a call after 10 s with no key, TX or signal (t_key counts all
            // three) rings once and marks the status bar. Not for a scan stop, a peek or a held squelch.
            if (cfg.bell && !scanning && !peek && !SQL_HELD && secs(&t_key) >= 10) { rang = 1; bk_chime(snd_bell); }
        }
        else heard = 0;
        dirty = 1; fast = 0;                // the tint comes or goes: the whole screen (a fast redraw keeps a slot's tint)
    }
    if (open) { stamp(&t_open); if (lh_n && on_rx(&lh[0].f)) lh[0].at = up10; }   // last heard: still on
}

// Status LEDs, every pass: red on the transmitting slot's spot (left A, right B), green on the
// heard slot's (a priority or weather look: the selected slot's). A weather alert or a BELL call
// blinks amber, both chips on both spots, 0.1 s every 1.3 s (an alert: only that, no green between).
// bk_chime calls it every tick it plays, so a blink isn't frozen lit or dark through an alert's chime.
void leds(void) __reentrant
{
    uint8_t g = 0, r = 0;
    if (tx) r = cfg.act + 1;
    else if (open && !alert) g = ((peek == PEEK_OTHER) ^ cfg.act) + 1;
    if ((rang || alert) && !tx && (uint8_t)now() < 20) g = r = 3;
    LED_RXU = g & 1; LED_RXV = g >> 1;
    LED_TXU = r & 1; LED_TXV = r >> 1;
}

// Peeks: every 0.5 s on a quiet radio, look at the other slot (dual watch) or the priority
// channel for 30 ms, and every 5 s listen to the weather channel for the 1050 Hz alert. While a
// station is heard, only the priority channel, every 5 s (FT-5D, TH-D75). Stay while busy, go back
// 2 s after it drops. With the squelch held open (squelch 0, monitor) only the weather look runs,
// while no carrier is at the chip's squelch: the other two need it to tell a call.
static void peek_at(uint8_t which, const __xdata chan_t *t) __reentrant
{
    from_ch(&main_ch);
    if (t) to_ch(t);
    else { wx_fill(cfg.wx - 1); ch.rx_tone = TONE_1050; }   // the weather look
    radio_tune();
    // Looks every 10 ms. Weather: 250 ms for the 1050 Hz alert (it lasts 8-10 s). Otherwise 30 ms, and
    // up to 150 ms while a carrier is at the squelch's open level (the chip's open delay). The slot's
    // RX tone must be heard in that time too: a carrier with the wrong tone doesn't take the receiver.
    for (uint8_t i = 1; i <= 25; i++) {
        delay_ms(10);
        if (which == PEEK_WX) {
            if (bk_tone_heard(TONE_1050)) { ch.rx_tone = 0; radio_tune(); alert = 1; goto take; }
        } else if (i >= 3) {
            if (bk_sql_open() && (!ch.rx_tone || bk_tone_heard(ch.rx_tone))) goto take;
            if (i >= 15 || !bk_sql_near() && !bk_sql_open()) break;
        }
    }
    to_ch(&main_ch);
    radio_tune();
    bk_af(open);                            // looked away mid-call or with the squelch held open: audio back
    stamp(&t_scan);                         // a scan's dwell starts over: the look took its time
    return;
take:
    peek = which; stamp(&t_open); dirty = 1;
    open = 0;                               // squelch() opens it on the new channel afresh (audio, LED, pause timer)
}

static void peeks(void)
{
    if (peek == PEEK_WX) {                  // alarm until a key stops it
        if (since(&t_peek) > 300) { bk_chime(snd_alert); stamp(&t_peek); }
        return;
    }
    if (peek) {
        if (!open && since(&t_open) > 400) { unpeek(); dirty = 1; }
        return;
    }
    if (since(&t_peek) < 100) return;
    if (wxm && peek_turn != 9) {            // weather mode: no looks away, as on the big rigs (9: FM heard an alert, fm_screen:
        uint8_t h = 0;                      // look now). WX ALERT is this channel: its own 1050 Hz detector (wx_watch),
        if (wx_watch) h = bk_tone_heard(TONE_1050);   // the alarm when the tone starts, not again while it lasts
        stamp(&t_peek);
        if (h > wx_tone) peek_at(PEEK_WX, 0);   // the usual look: it hears the tone again and raises the alarm
        wx_tone = h;
        return;
    }
    // No look away for 3 s after a call drops: a fade closes the squelch for a moment, and a look
    // then missed the station's return and reopened late (heard as the TD-H3 cutting out next to a
    // UV-K5). Quansheng firmwares wait the same way (dual_watch_count_after_rx).
    if (t_hit && !open && since(&t_open) < 600) return;
    if (SQL_HELD ? bk_sql_open() : open) {  // a call; with the squelch held open, a carrier
        if (since(&t_peek) < 1000) return;
        stamp(&t_peek);                     // receiving: a look at the priority channel, unless on it
        if (!SQL_HELD && cfg.prio && !on_rx(&prio_ch.rx)) peek_at(PEEK_PRIO, &prio_ch);
        return;
    }
    stamp(&t_peek);
    peek_turn++;
    if (cfg.wx && peek_turn % 10 == 0) peek_at(PEEK_WX, 0);
    else if (SQL_HELD) ;                    // squelch held open: the weather look only
    else if (cfg.prio && (peek_turn & 3) == 0) peek_at(PEEK_PRIO, &prio_ch);
    else if (cfg.dw && !scanning) peek_at(PEEK_OTHER, &other);
}

// Scan: on a busy channel it stays while the signal is there (PAUSE BUSY), or until the pause timer
// runs out (a weather broadcast never stops); once the signal drops it waits the resume timer and moves
// on, and a reply within it (t_open is the last tick heard) keeps it there. RESUME HOLD (Icom): it stays
// the whole pause time, even after the signal drops; with PAUSE BUSY it stops on the first station
// (Kenwood's seek). UP/DOWN move on at once and set the direction.
static void scan(void)
{
    static const __code uint8_t pause_secs[] = {2, 5, 10, 20, 0};            // 0 = BUSY
    static const __code uint16_t resume_ticks[] = {400, 600, 1000, 0, 0, 200}; // 5 ms ticks
    if (peek) return;
    if (open) {
        if (cfg.resume == 3 && !pause_secs[cfg.pause]) { scanning = 0; dirty = 1; }
        else if (pause_secs[cfg.pause] && secs(&t_hit) >= pause_secs[cfg.pause]) {
            no_wait();                      // moving on: no resume wait for a station still there
            step_freq(sdir);
        }
        return;
    }
    if (t_hit && since(&t_open) < resume_ticks[cfg.resume]) return;   // t_hit: heard here (on a quiet scan the 16-bit
                                            // ticks wrap under t_open's stamp: a 5 s stall every 327 s)
    if (cfg.resume == 3 && t_hit && secs(&t_hit) < pause_secs[cfg.pause]) return;   // HOLD: a station was here
    // 30 ms a channel (20 ms in VFO) when it's empty. A carrier over the squelch's open level (strong and
    // quiet, not band noise) gets up to 150 ms: the chip's squelch has an open delay (REG_4E), and a
    // strong station was being skipped.
    if (since(&t_scan) < (sl_mem() ? 6 : 4)) return;
    if (since(&t_scan) < 30 && bk_sql_near()) return;
    // A carrier on a tone-squelched channel gets up to 400 ms for its tone: CTCSS takes a few
    // cycles of a 67-254 Hz tone to decode, longer than the carrier wait above.
    if (since(&t_scan) < 80 && ch.rx_tone && bk_sql_open()) return;
    fast = !dirty;                          // redraw just this slot, unless something else needs the whole screen
    step_freq(sdir);
    stamp(&t_scan);                         // the dwell starts once retuned: the EEPROM reads come off it otherwise
}

// Battery saver: when nothing is happening, sleep the BK4819 and wake it for 60 ms to look.
// Asleep, the RX front end (VRX/URX) is off too: radio_off(); wake() retunes, which turns it back on.
static void saver(void)
{
    if (!cfg.save || cfg.vox || tx || open || scanning || peek || entry_n || saving || secs(&t_key) < 5) {   // VOX: the chip listens to the mic
        if (asleep) { wake(); }
        return;
    }
    if (!asleep && (!rx_settled() || bk_sql_open() || bk_sql_near())) stamp(&t_wake);   // settling, or a carrier: stay awake (the 60 ms listen starts once settled)
    if (!asleep && since(&t_wake) > 12) { radio_off(); asleep = 1; stamp(&t_sleep); }
    else if (asleep && since(&t_sleep) > save_ms[cfg.save]) { wake(); }
}

// Flashlight: steady, strobe (50 ms every 300 ms), SOS (200 ms Morse unit).
// Flashlight on side key 2, as on Olight's one-button lights (Baton, Warrior): tap on/off at the
// last level; hold from off for moonlight; hold while on to step MOON, LOW, MED, HIGH once a second;
// double tap for HIGH; triple tap for strobe; hold 5 s from off for SOS. Any tap stops strobe or
// SOS. Brightness is PWM5's duty on P3.5, out of 200 (the radio's chip routes a pin with stock's
// PWM code 5 to PG5 there: found with a probe build, HARDWARE.md).
#define SIDE_HELD 1
#define SIDE_OFF 2                          // this press began with the light off
#define SIDE_LONG 4                         // this press acted as a hold: its release is no tap
static __xdata uint16_t t_side;             // when side key 2 last went down or up
static __xdata uint8_t side, taps, tsteps, tduty;
static void torch_run(void) __reentrant
{
    static const __code uint8_t sos[5] = {0x15, 0x77, 0x47, 0x05, 0x00};   // 101010001110111011100010101000000, bit i of 33 (LSB first)
    static const __code uint8_t lv[4] = {1, 15, 60, 200};       // moonlight, low, medium, high
    uint16_t t = now(), d = t - t_side;
    uint8_t held = key_held == K_SIDE2, was = torch, lvl = tlevel, duty;
    if (held != (side & SIDE_HELD)) {       // the key went down or up
        if (held) { side = SIDE_HELD | (torch ? 0 : SIDE_OFF); tsteps = 0; }
        else { if (!(side & SIDE_LONG)) taps++; side = 0; }
        t_side = t; d = 0;
    }
    if (held) {
        if (side & SIDE_OFF) {              // from off: moonlight at 0.5 s, SOS at 5 s
            if (d >= 1000) { torch = 3; side |= SIDE_LONG; }
            else if (d >= 100 && !(side & SIDE_LONG)) { torch = 1; tlevel = 0; side |= SIDE_LONG; }
        } else if (torch == 1 && d >= 100 + tsteps * (uint8_t)200) {   // on: MOON, LOW, MED, HIGH, MOON...
            tsteps++; tlevel = (tlevel + 1) & 3; side |= SIDE_LONG;
        }
    } else if (taps && d >= 70) {           // no more taps within 350 ms: act on the count
        if (torch >= 2) torch = 0;          // any tap stops strobe or SOS
        else if (taps == 1) torch = !torch;
        else if (taps == 2) { torch = 1; tlevel = 3; }
        else torch = 2;
        taps = 0;
    }
    if (torch != was || tlevel != lvl) dirty = 1;
    d = t / 40;                             // t % 60 and t / 40 % 33 without the modulo routine, in 8 bits (each is under 256)
    duty = torch == 1 ? lv[tlevel] : (torch == 2 && (uint8_t)((uint8_t)t - (uint8_t)(t / 60) * 60) < 10 || torch == 3 && (d = (uint8_t)((uint8_t)d - (uint8_t)(d / 33) * 33), sos[(uint8_t)d >> 3] >> ((uint8_t)d & 7) & 1)) ? 200 : 0;
    if (duty != tduty) torch_pwm(tduty = duty);
}

// The backlight (LIGHT), the first key on a dark screen and the flashlight, every pass of the main
// loop and of FM's. Screen dark: the first key only lights it, as on the big rigs. PTT and PTT-B
// always work, and so does a watching computer (REMOTE), which can't see the light. Returns k, or 0
// for a key that only lit the screen.
static __xdata uint8_t lit;                 // the backlight duty now; 0xFF: unknown (boot)
static __xdata uint16_t t_lcdon;            // the panel was woken, its light not yet on
uint8_t lights(uint8_t k) __reentrant
{
    static __bit eat;                       // the key went down on a dark screen: its press only lit it
    uint8_t light;
    if (!lit && !mirror && (uint8_t)(key_held - 1) < K_PTT - 1) eat = 1;   // a keypad key (key_held is 0-K_SIDE2)
    if (k && eat) { eat = 0; k = 0; }
    if (k || key_held || tx || open || alert) stamp(&t_key);   // keys (lit while held, PTT too), a call, an alert
    light = light_s[cfg.light];             // seconds on after a key, 0 = always
    light = !light || tx || open || alert || secs(&t_key) < light ? bright_duty[cfg.bright] : 0;
    if (light != lit) {                     // a BRIGHTNESS change shows at once
        // The panel sleeps while dark (lit 0xFF: unknown, so awake). Woken, it shows white for its
        // first 120 ms (ST7735S SLPOUT): the light waits that out, the loop runs on meanwhile.
        if (light && !lit && !t_lcdon) { lcd_sleep(0); stamp1(&t_lcdon); }
        if (!(light && !lit) || (int16_t)since(&t_lcdon) >= 24) {
            if (!light && lit) lcd_sleep(1);
            t_lcdon = 0; lit = light; backlight(light); KEY_LED = light != 0;
        }
    }
    torch_run();
    return k;
}

// AUTO OFF: the power switch is the volume knob, so "off" is this sleep: receiver, screen, backlight
// and flashlight off until a key, which only wakes it (PTT too: it doesn't key up).
static __xdata uint16_t idle10;             // 10 s spells with no key, signal or scan
static void doze(void)
{
    radio_off(); backlight(0); KEY_LED = 0; lcd_on(0);
    torch = 0; torch_pwm(tduty = 0);
    while (!key_event()) delay_ms(10);      // delay_ms kicks the watchdog
    while (key_event() || key_held) delay_ms(10);   // that key only wakes: wait till it's let go
    lcd_on(1);
    asleep = 0; idle10 = 0;
    retune();
    stamp(&t_key);
}

// ---------- DTMF send ----------
// While transmitting on PTT or PTT-B, the keys held with it (keys.c's ptt_key): 0-9, * and # send
// their tone for as long as they're held, 100 ms at least; MENU, then 1-8, sends stored code n. The
// codes are stock's own: 8 at 0x1830 + 16 (n - 1), 7 digits, one a byte (0-9, A-D, * 14, # 15), FF
// ends one, as CHIRP edits them; seven 0s are a factory radio's blank. The bottom line shows what's sent.
#define CODES (scr_buf + 100)               // code n at + 8n; outside the full-screen modes scr_buf is free past radio.c's 74
static uint8_t code_load(uint8_t n) __reentrant   // code n (0-7) to CODES + 8n, FF after its digits: its length
{
    uint8_t i, z = 0, j = n << 3;
    ee_read(0x1830 + (uint8_t)(n << 4), CODES + j, 7);
    for (i = 0; i < 7; i++) z |= CODES[j + i];
    for (i = 0; z && i < 7 && CODES[j] < 16; i++, j++) ;   // seven 0s: blank
    for (z = i; z < 8; z++, j++) CODES[j] = 0xFF;
    return i;
}
static uint8_t key_digit(uint8_t k) __reentrant   // a key's DTMF digit: 0-9, * 14, # 15; else 0xFF
{
    return k == K_STAR ? 14 : k == K_HASH ? 15 : (uint8_t)(k - K_0) < 10 ? k - K_0 : 0xFF;
}

static __xdata uint16_t t_dt;               // when the tone went on or off
static void dt_send(uint8_t d) __reentrant  // tone d on, and onto the bottom line (its last 16)
{
    bk_dtmf_tx(d);
    if (dtmf_n == 16) { xcpy(dtmf, dtmf + 1, 15); dtmf_n = 15; }
    dtmf[dtmf_n++] = dtmf_chars[d]; dtmf[dtmf_n] = 0;
    dt_line();                              // not a whole redraw: the tone's timing holds
}
static void dtmf_send(void) __reentrant
{
    uint8_t k = ptt_key, d, n;
    if (dt_on) {
        if (k == dt_on || since(&t_dt) < 20) return;   // still held, or not 100 ms yet
        bk_dtmf_tx(0xFF); dt_on = 0; stamp(&t_dt);
        return;
    }
    if (!k || since(&t_dt) < 10) return;    // 50 ms between tones
    dt_on = k; stamp(&t_dt);
    d = key_digit(k);
    if (dt_mem) {                           // MENU, then a code's number: its digits, 100 ms each and a 100 ms gap
        dt_mem = 0;
        if ((uint8_t)(--d) < 8) for (n = code_load(d), d <<= 3; n; n--, d++) {
            dt_send(CODES[d]); delay_ms(100); bk_dtmf_tx(0xFF); delay_ms(100);
        }
        dt_line();
    } else if (k == K_MENU) { dt_mem = 1; dt_line(); }
    else if (d < 16) dt_send(d);
}

// DTMF CODES: the 8 codes; UP/DOWN pick one, MENU types it: 0-9, * and # add a digit (7 at most),
// V/M takes the last off (or, with none typed, leaves it as it was), MENU saves.
static uint8_t code_len(uint8_t j) __reentrant { uint8_t n = 0; while (CODES[j++] < 16) n++; return n; }   // code at CODES + j
static void codes_screen(void)
{
    static const __code label_t l[] = {{0, 0, 1, P_ACC, "DTMF CODES"}, {0, BOT_Y - 12, 1, P_DIM, "\x7f\x80 move  MENU edit"},
        {0, BOT_Y - 12, 1, P_DIM, "0-9 * #  MENU save"}, {0, BOT_Y, 1, P_DIM, "V/M delete digit"}};
    uint8_t i, k, n, j, ed = 0;             // ed: typing the code at the cursor
    for (i = 0; i < 8; i++) code_load(i);
    list_open();
    for (;;) {
        if (list_redraw()) { if (ed) { draw_labels(l, 1); draw_labels(l + 2, 2); } else list_frame(l); }
        if (lrows) {                        // "1 *123", "2 -" (empty); the one being typed with a cursor
            for (i = 0; i < 8; i++) {
                buf[0] = '1' + i; buf[1] = ' ';
                for (k = 2, j = i << 3; CODES[j] < 16; j++) buf[k++] = dtmf_chars[CODES[j]];
                buf[k] = ed && i == lsel ? '_' : k > 2 ? ' ' : '-';
                pad_to(buf + k + 1, 21);
                list_row(14 + 12 * i, i == lsel, ed ? P_HI : P_ACC);
            }
            lrows = 0;
        }
        k = list_key(ed ? 0 : 8);
        if (!ed) {
            if (k == K_EXIT) return;
            if (k == K_MENU) { ed = 1; remote_req |= REQ_REDRAW; }
            continue;
        }
        j = lsel << 3;
        n = code_len(j);
        i = key_digit(k);
        lrows = 1;
        if (i < 16) { if (n < 7) CODES[j + n] = i; }
        else if (k == K_EXIT && n) CODES[j + n - 1] = 0xFF;
        else if (k == K_EXIT || k == K_MENU) {
            if (k == K_EXIT) code_load(lsel);   // nothing typed: as it was
            else if (ee_write(0x1830 + (uint8_t)(lsel << 4), CODES + j, 7)) chime_ok(); else chime_no();
            ed = 0; remote_req |= REQ_REDRAW;
        }
    }
}

// CALL ID sent: 20 words a minute (a dot 60 ms, PARIS), 800 Hz. Each character of abc (CH NAME's)
// as ITU Morse, one element a bit from bit 0 (1 a dash) and a 1 above the last; the space a word gap.
// The last entry, for a character not in abc: nothing.
static const __code uint8_t morse[] = {0x01, 0x06, 0x11, 0x15, 0x09, 0x02, 0x14, 0x0B, 0x10, 0x04, 0x1E, 0x0D, 0x12, 0x07,
    0x05, 0x0F, 0x16, 0x1B, 0x0A, 0x08, 0x03, 0x0C, 0x18, 0x0E, 0x19, 0x1D, 0x13, 0x3F, 0x3E, 0x3C, 0x38, 0x30, 0x20,
    0x21, 0x23, 0x27, 0x2F, 0x61, 0x29, 0x6A, 0x01};
typedef char morse_fits[sizeof morse == sizeof abc ? 1 : -1];
#define DIT 12                              // ticks: 60 ms
static __bit id_sent;                       // an ID since power-on (internal RAM: cleared at every start)
// ponytail: up10 stops while a full-screen tool (FM, SCOPE ...) or AUTO OFF holds the main loop, so
// an ID can come late after one; a clock in the tick interrupt if that ever matters on the air.
static __xdata uint16_t id_at;              // up10 when it went

static uint8_t tot_up(void) { uint8_t tt = tots[cfg.tot]; return tt && secs(&t_tx) >= tt; }   // TX TIMEOUT reached

static uint8_t id_tone(uint8_t v) __reentrant   // REG_70's high byte (0xD0: tone 1 on at gain 0x50, as DTMF's; 0 off) | dots long. 1: PTT or TX TIMEOUT cut it
{
    bk_write(0x70, (uint16_t)(v & 0xF0) << 8);
    stamp(&t_dt);                           // DTMF send's: no touch-tone meanwhile
    v = (v & 7) * DIT;
    do {
        delay_ms(5);
        key_event();
        if ((uint8_t)(key_held - K_PTT) < 2 || tot_up()) return 1;
    } while (since(&t_dt) < v);
    return 0;
}

static uint8_t call_id(void) __reentrant    // PTT let go: the ID if it's due; 1: PTT pressed during it, still talking
{
    uint8_t i, m = band_of(ch.tx);
    __xdata char *p = cat(buf, "ID ");      // nothing draws into buf meanwhile: it holds the callsign throughout
    if (id_sent && (uint16_t)(up10 - id_at) < 60 || m != 1 && m != 4 || !name_read(CALL_ADDR, p)) return 0;   // 2M and 70CM: the ham bands
    draw_bottom(buf, P_HI);
    bk_write(0x71, TONE_HZ(800));
    bk_write(0x50, 0xBB20);                 // the mic muted
    for (; *p; p++) {
        for (i = 0; abc[i] && abc[i] != *p; i++) ;
        for (m = morse[i]; m > 1; m >>= 1)
            if (id_tone(m & 1 ? 0xD3 : 0xD1) || id_tone(1)) goto cut;
        if (id_tone(i ? 2 : 4)) goto cut;   // 3 dots between letters, 7 between words
    }
    id_sent = 1; id_at = up10;
cut:
    dirty = 1;                              // ptt() turns the tone off and the mic back on
    return (uint8_t)(key_held - K_PTT) < 2;
}

static __bit tot_warned;
// VOX: the chip finds a voice (BK_IRQ_VOXF) and loses it (BK_IRQ_VOXL; main). It keys the selected
// slot through ptt(), so every refusal there holds, and only from the home screen, with nothing
// heard (the speaker can't key it, and it never talks over a station), not scanning, looking at
// the other slot, in weather mode, an alert or the menu, nor with KEY LOCK KEYS+PTT on. After any
// unkey (a timeout, a refusal) it keys again only after VOX DELAY of quiet.
static __xdata uint16_t t_vox;              // when the voice went
static uint8_t vox_wants(void) __reentrant  // VOX keys, or keeps keyed, the selected slot
{
    if (!cfg.vox) return 0;
    if (!vox && since(&t_vox) >= vox_hang[cfg.voxd]) { vox_arm = 1; return 0; }   // quiet for VOX DELAY: armed again
    if (tx) return vox_tx;                  // keyed through VOX DELAY after the voice goes (not a PTT TX)
    return vox && vox_arm && !open && menu == 0xFF && !saving && !entry_n && !scanning && !peek && !wxm && !alert && !(locked && cfg.klock);
}

static void refuse(const __code char *why) __reentrant          // 3 s, like any note; each PTT starts it again
{
    note = why; t_note = 0; dirty = 1;
    if (!vox_tx) chime_no();                // VOX: no sound for the mic to hear
    key_held = K_NONE;
}

static void ptt(void) __reentrant
{
    uint8_t want = key_held - K_PTT, tt = tots[cfg.tot], v = 0;   // PTT-A keys A (0), PTT-B keys B (1), else 2; tt: TOT s
    if (want > 1) { want = 2; if (vox_wants()) { want = cfg.act; v = 1; } }   // v: VOX keys it
    if (want == 0 && peek == PEEK_OTHER) want = !cfg.act;   // dual watch: PTT answers the slot you're hearing
    if (want < 2 && !tx && menu == 0xFF && !saving) {     // keypad lock stops stray presses; PTT only with KEY LOCK KEYS+PTT
        if (locked && cfg.klock) { refuse("LOCKED: NO TX"); return; }   // before PTT-B can move to slot B
        vox_tx = v;
        scanning = 0;
        if (peek) unpeek();
        if (asleep) wake();                 // battery saver: the receiver back on, or a refusal leaves it off 5 s
        if (cfg.act != want) { cfg.act = want; rev = 0; wxm = 0; retune(); }
        if (!ch.tx) refuse("TX LOCKED CHANNEL");
        // Busy channel lockout. A retune (onto the other slot, or back from a dual-watch look) cleared open:
        // the chip's squelch, 30 ms on, says if the channel is busy.
        else if (ch.bcl && (open || (delay_ms(30), bk_sql_open()))) refuse("BUSY: NO TX");
        else if (batt_cal() < 6500) refuse("BATTERY LOW: NO TX");
        else if (radio_tx(1)) { tx = 1; m_wait = 0; stamp(&t_tx); open = 0; tot_warned = dtmf_n = dt_on = dt_mem = 0; dirty = 1; }
        else refuse("TX NOT ALLOWED");
        if (!tx) vox_arm = vox_tx = 0;      // refused: VOX waits for quiet before it tries again
    } else if (tx && tt && !tot_warned && secs(&t_tx) + 10 >= tt) {
        tot_warned = 1;                     // 10 s before the timeout cuts you off
        BEEP(1800, 120);
    } else if (tx && (want == 2 || tot_up())) {
        if (want == 2 && call_id()) { bk_dtmf_tx(0xFF); return; }   // the ID, still keyed; PTT again during it: talking, tone off
        bk_dtmf_tx(0xFF);                   // a DTMF tone off with the carrier
        dtmf_n = 0;
        vox_arm = vox_tx = 0;
        tx = radio_tx(0);
        key_held = K_NONE;
        dirty = 1;
    }
}

// "146.520 -112 O S 7400 0.20.0": RX frequency, dBm, squelch Open/Closed, Speaker on/-, battery mV, version.
static void remote_status(void)
{
    freq_s(buf, ch.rx);                     // every field fixed width: "146.520 -102 O S 7900 "
    buf[7] = ' ';
    s2s(buf + 8, dbm, 3);
    cat(buf + 12, open ? " O " : " C ");
    cat(buf + 15, AMP_ON ? "S " : "- ");
    u2s(buf + 17, batt_cal(), 4);
    buf[21] = ' ';
    cat(buf + 22, VERSION);                 // the flasher reads it before and after a flash
    remote_puts(buf);
}

// Boot: a lattice radio mast builds up from the ground, its tip light blinks and two radio rings
// pulse out and fade; then the name. ~0.65 s, ending on a still frame.
#define TIP 7                               // the tip light's centre row; the mast runs from 16 down to the ground at 80
static __xdata uint16_t ink;               // the colour px() draws in
static void px(uint8_t x, uint8_t y) __reentrant { lcd_fill(x, y, 1, 1, ink); }
static void pair(uint8_t d, uint8_t y) __reentrant   // pixels d either side of the centre line
{
    px(64 - d, y); px(64 + d, y);
}
static void ring(uint8_t r) __reentrant   // a radio ring of radius r round the tip, ~40 degrees each side
{
    uint8_t dx = r, dy, end = (uint8_t)(r << 1) / 3;
    for (dy = 0; dy <= end; dy++) {
        while ((uint16_t)(dx * dx + dy * dy) > (uint16_t)(r * r)) dx--;
        if (dx < 64) { pair(dx, TIP + dy); if (dy <= TIP) pair(dx, TIP - dy); }
    }
}
static void splash(void) __reentrant
{
    static const __code uint8_t brace[8] = {0, 3, 2, 1, 0, 1, 2, 3};   // the X braces' offset in quarters of the half width
    static const __code uint8_t fade[8] = {P_ACC, P_ACC, P_ACC, P_DIM, P_DIM, P_DIM, P_BAR, P_BAR};   // a ring's colour by step
    static const __code label_t l[] = {{34, 88, 2, P_FG, "TD-H3"}, {25, 106, 1, P_ACC, "open firmware"}, {(W - 6 * (sizeof VERSION - 1)) / 2, 118, 1, P_DIM, VERSION}};
    uint8_t y, w, j;
    lcd_fillp(16, 80, 96, 1, P_DIM);
    for (y = 79; y >= 16; y--) {            // the mast, row by row: legs, X braces, a rung every 8 rows
        w = (y - 8) / 6; j = y & 7;
        ink = c_dim;
        if (j) pair(w * brace[j] >> 2, y);
        else lcd_fillp(65 - w, y, 2 * w - 1, 1, P_ACC);
        ink = c_fg; pair(w, y);
        if (!j) delay_ms(25);
    }
    lcd_fillp(64, 9, 1, 7, P_FG);            // the antenna
    for (y = 0; y < 13; y++) {              // two rings, 4 frames apart, 11 px a frame, fading as they go
        for (j = 0; j <= 4; j += 4) {
            w = y - j;                      // this ring's step, 0-8 (wraps above 8 before its start)
            if (w > 8) continue;
            ink = c_bg;
            if (w) ring(w * 11 - 3);
            if (w < 8) { ink = pal[fade[w]]; ring(w * 11 + 8); }
        }
        lcd_fillp(63, TIP - 1, 3, 3, P_ACC - (y & 2));
        delay_ms(27);
    }
    draw_labels(l, 3);
}

void main(void)
{
    uint8_t k;
    static __xdata uint16_t tone_try;       // tone_heard: a real tone is the same one three readings running
    static __xdata uint8_t tone_n;

    sys_init();
    lit = 0xFF;
    ptt_ok = 0;                             // XDATA outlives a software reset (tdh3 flash's restart): PTT held through it stays off the air
    menu = 0xFF;
    lcd_init();                             // backlight stays off until there is a picture: no white flash
    cfg_load();
    tx_prefs();
    theme_apply();
    backlight(bright_duty[cfg.bright]);    // lit for the boot animation: lcd_init left the panel black
    splash();
    bk_init();
    fm_off();
    radio_init();
    BT_ON = cfg.bt;
    load_prio();
    load_ch();
    tlevel = 3;                             // the flashlight starts at high
    tone_n = wx_ch = tinted[0] = tinted[1] = 0;   // XDATA outlives a software reset: as at power-on
    t_save = 0;                             // nothing changed yet: never write settings at boot
    if (cfg.beep) bk_chime(snd_hello);
    else delay_ms(600);
    lcd_clear();
    dirty = 1;
    stamp(&t_key);
    key_scan();                             // PTT still down from power-on (missed flash mode): dead till let go
    if (!ptt_ok) note = "PTT HELD: LET GO";

    for (;;) {
        WDT_KICK();
        k = lights(key_event());
        if (since(&t_key) > 60000) t_key = now() - 60000;   // 16-bit ticks wrap at 327 s
        if ((menu != 0xFF || entry_n || saving) && secs(&t_key) >= 20) {  // half-done input times out
            menu = 0xFF; entry_n = 0; saving = 0; overwrite = 0;
            lcd_clear();
            retune();
        }
        if (menu != 0xFF && entry_n && since(&t_key) > 200) menu_jump();   // one digit, then 1 s
        irqs = asleep ? 0 : bk_irq();
        if (irqs & BK_IRQ_VOXF) vox = 1;
        if (irqs & BK_IRQ_VOXL) { vox = 0; stamp(&t_vox); }
        ptt();
        if (tx) dtmf_send();
        leds();
        if (k && rang) { rang = 0; dirty = 1; }   // any key, PTT too, clears the bell
        if (k && k != K_PTT && k != K_SIDE1 && !tx) {
            on_key(k);                      // the click comes after: a retune with the amp on (leaving the menu)
            if (click) { bk_chime(snd_click); click = 0; }   // played its pop through the speaker with the beep
        }
        if (!note) t_note = 0;              // a note flashes 3 s, the way a refusal does on an Icom or Yaesu
        else if (!t_note) stamp1(&t_note);
        else if (note != low_s && secs(&t_note) >= 3 && ptt_ok) { note = 0; dirty = 1; }   // PTT HELD stays till let go

        if (!tx && menu == 0xFF) {
            saver();
            if (!asleep) {
                dtmf_poll();
                squelch();
                if (open && !ch.rx_tone && !heard && since(&t_tone) >= 20) {
                    stamp(&t_tone);
                    // a real tone is steady: the same one three readings running (speech hits one by chance)
                    uint16_t h = tone_heard();
                    if (h && h == tone_try) { if (++tone_n >= 3) { heard = h; dirty = 1; } }
                    else { tone_try = h; tone_n = 1; }
                }
                if (scanning) scan();
                if (!entry_n && !saving) peeks();
            }
        }

        if (menu == 0xFF) {
            if (dirty) {
                if (fast) draw_slot(cfg.act); else draw_main();
                dirty = fast = 0;
                if (remote_req & REQ_END) { remote_req &= ~REQ_END; remote_end(); }   // the redraw REMOTE asked for is done
                if (scanning) stamp(&t_scan);   // a scan's dwell starts once the new channel is on screen
            }
            // The meter: a new reading 3 times a second, calm enough to read on a fluttering signal; at once
            // when the squelch opens or closes, or with a new channel's first settled reading (draw_main
            // leaves m_open 0xFF). The mic level and TX clock 10 times a second.
            else if (!alert && !m_wait && (since(&t_meter) >= (tx ? 20 : 66) || !tx && open != m_open)) { stamp(&t_meter); draw_meter(); }
        }

        if (remote_req & REQ_REDRAW) {
            if (menu == 0xFF) { lcd_clear(); dirty = 1; fast = 0; remote_req |= REQ_END; }   // drawn next pass
            else { draw_menu(); remote_end(); }
        }
        if (remote_req & REQ_STATUS) remote_status();
        if (remote_req & REQ_VOICE) {
            AMP_ON = 1; voice(remote_voice); stamp1(&t_voice);
            cat(buf, "VOICE "); u2s(buf + 6, remote_voice, 3); remote_puts(buf);
        }
        if (t_voice && since(&t_voice) > 800) t_voice = 0;   // a prompt has 4 s of speaker
        remote_req &= REQ_END;

        if (secs(&t_batt) >= 10) {
            stamp(&t_batt);
            up10++;                         // last heard's and CALL ID's clock, counted while transmitting too
            if (!tx) {                      // a 2S pack: warn once at 6.8 V, TX refused under 6.5 V
                int16_t mv = batt_cal();
                if (mv < 6800 && !low_batt) { low_batt = 1; note = low_s; bk_chime(snd_batt); dirty = 1; }
                else if (mv > 7000) low_batt = 0;
                // AUTO OFF, counted in these 10 s checks
                if (!cfg.apo || scanning || secs(&t_key) < 10) idle10 = 0;
                else if (++idle10 >= (uint16_t)90 << cfg.apo) { doze(); lit = 0; }       // 30, 60, 120 min
            }
        }

        AMP_ON = open || beep_left || t_voice;

        // 2 s after the last change; a scan's steps once it ends; at once when armed for a flash, whose
        // restart would lose it
        if (t_save && (loader_armed || !scanning && since(&t_save) > 400)) {
            ee_write(CFG_ADDR, (const uint8_t *)&cfg, sizeof cfg);
            t_save = 0;
        }
        delay_ms(10);
    }
}
