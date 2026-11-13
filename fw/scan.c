// Spectrum scope and freq find: two full-screen modes that hand back a frequency.
#include "fw.h"

#define N 64                                // points, 2 px each
__xdata uint8_t scr_buf[SCR_BUF];          // 2 * N + 10 + sizeof sc here
// In the screen buffer (fw.h): the bar heights now, the peaks (both in px, as drawn), the readout
// text, and the sweep's state.
#define now_h scr_buf
#define peak (scr_buf + N)
#define s ((__xdata char *)scr_buf + 2 * N)
struct sc {
    uint8_t row;                            // the waterfall's row being written, 0 at the top
    uint8_t over, reach;                    // this sweep's readings 2 dB or more over the scale's foot; 1 dB or more
    uint16_t sum;                           // the sweep's readings added up
    uint8_t base, prev;                     // the scale's foot (255 till the first sweep's end: no bars), and as it was last sweep
    uint8_t slow;                           // 5: SLOW sweep
    uint8_t cur;                            // the cursor's point
};                                          // readings and the foot in dBm + 160 (0-255): no signed or 16-bit maths
#define sc (*(__xdata struct sc *)(scr_buf + 2 * N + 10))
#define CLR (2 * N + 10 + 5)                // 2/8 and 0 start these again: bars, peaks, waterfall, sum
#define TOP 12
#define TALL 54                             // px: 36 dB over the scale's foot, 1.5 px a dB
#define WF 68                               // the waterfall: WF_H rows, one a sweep, row 0 at the top
#define WF_H 36

static void freq_txt(uint32_t f) __reentrant { dec(s, f / 10, 7, 4); }   // "146.5200" from 10 Hz units, in s

static uint8_t height(uint8_t u, uint8_t b) __reentrant  // px for reading u on the scale from b
{
    if (u <= b) return 0;
    u -= b;
    return u >= 36 ? TALL : u + (u >> 1);
}

static const __code label_t scope_l[] = {{0, BOT_Y - 12, 1, P_DIM, "\x7f\x80 2/8 move  5"}, {0, BOT_Y, 1, P_DIM, "MENU tune  V/M exit"}};
static const __code label_t ff_l[] = {{11, 0, 1, P_DIM, "FREQ FIND"}, {0, 30, 2, P_FG, "key radio"}, {0, 48, 2, P_FG, "nearby"}, {0, BOT_Y, 1, P_DIM, "MENU take  V/M exit"}};

// The six colours' tops, px over the foot: the first 12 dB wide, so the band's noise, sloped across
// the window as it is on a real receiver, stays in it; then about 5 dB each.
static const __code uint8_t edge[6] = {18, 26, 33, 41, 48, TALL};
static uint8_t band(uint8_t px) __reentrant  // the colour of the pixel px up
{
    uint8_t b = 0;
    while (px >= edge[b]) b++;
    return b;
}

static void heat(uint8_t x, uint8_t o, uint8_t h)      // a bar from o px up to h, in its colours
{
    while (o < h) {
        uint8_t b = band(o), e = edge[b];
        if (e > h) e = h;
        lcd_fill(x, TOP + TALL - e, 2, e - o, theme_c(H0 + b));
        o = e;
    }
}

// Point i: h px now, its peak p (on this sweep's scale). Only what changed is drawn; the cursor's
// column is drawn whole, lit, its bar in the text colour. The amber tick is the peak.
static void column(uint8_t i, uint8_t h, uint8_t p, uint8_t cursor)
{
    uint8_t x = 2 * i, y = TOP + TALL, o = now_h[i], q = peak[i];
    if (h > p) p = h;
    if (cursor) {
        lcd_fillp(x, TOP, 2, TALL - h, P_BAR);
        lcd_fillp(x, y - h, 2, h, P_FG);
    } else {
        if (q > o && q > h && q != p) lcd_fillp(x, y - q, 2, 1, P_BG);   // the tick where the old scale had it
        if (h > o) heat(x, o, h);
        else if (h < o) lcd_fillp(x, y - o, 2, o - h, P_BG);
    }
    if (p > h) lcd_fillp(x, y - p, 2, 1, P_ACC);
    now_h[i] = h;
    peak[i] = p;
}

static __xdata uint32_t start;              // the scope's window: point i is at(i)
static __xdata uint16_t step;
static uint32_t at(int8_t i) __reentrant { return start + (int32_t)step * i; }
static void readout(void) { freq_txt(at(sc.cur)); lcd_text(0, 0, s, 1, P_FG); }   // the cursor's frequency
static void frame(void)                     // the title and key lines; 5's says what it switches to
{
    readout();
    draw_labels(scope_l, 2);
    lcd_text(90, BOT_Y - 12, sc.slow ? "fast" : "slow", 1, P_DIM);
    lcd_icon(49, 1, icon_pulse, P_DIM);
}

// Point i: one look at it (SLOW watches it 35-40 ms and keeps the strongest reading, so a short
// transmission, a data burst or one slot of a digital voice signal, is not missed between looks),
// its bar, its pixel in the waterfall, and on the cursor its reading in the title.

// The look reads short: after a retune the chip's RSSI climbs for about 20 ms, and bk_dbm_at reads it
// at 11.8 ms. Measured on the radio (RSSI read every 1 ms after a retune, 11 sweeps): 8 dB under its
// level 20 ms on, on a -89 dBm station and on -111 dBm noise alike. METER CAL as the home meter has it.
#define RISE 8
__xdata int8_t sc_cal;                      // METER CAL (main.c sets it)
static void look(uint8_t i)
{
    uint8_t u = bk_dbm_at(at(i)) + (160 + RISE), w, h, p = peak[i];
    if (sc.slow)                            // by now the RSSI is there: no RISE
        for (w = ticks; (uint8_t)((uint8_t)ticks - w) < 8; ) {
            h = bk_dbm() + 160;
            if (h > u) u = h;
        }
    u += sc_cal;
    if (i == sc.cur) {                      // "-87dBm "
        s[0] = '-';
        cat(num(s + 1, 160 - u), "dBm ");
        lcd_text(60, 0, s, 1, P_FG);
    }
    sc.sum += u;
    if (p && sc.prev != sc.base) p = height((uint8_t)(2 * p + 1) / 3 + sc.prev, sc.base);   // px back to dB, onto the new scale
    h = height(u, sc.base);
    if (h) {                                // 1 dB or more over the foot (1 px); 2 dB or more is 3 px
        sc.reach++;
        if (h >= 3) sc.over++;
    }
    column(i, h, p, i == sc.cur);
    lcd_fill(2 * i, WF + sc.row, 2, 1, theme_c(H0 + band(h - (h != 0))));   // the bar's top colour; the first under the foot too
}

// UP/DOWN move the cursor, 2/8 move the window, 0 clears the peaks, 5 SLOW, MENU tunes, V/M leaves.
// The scale's foot follows the band's quietest quarter: set from the first sweep's average less 3 dB,
// then once a sweep it moves 1 dB so about a fifth of the readings sit at it or under: up when over
// 52 of the 64 are 2 dB or more over it, down when under 44 are 1 dB or more over. Stations, and
// noise rising across the window, are above the quarter and don't lift it. 5 starts afresh (a slow
// sweep reads every point higher: its strongest of many). The waterfall writes a row a sweep, moving
// down and wrapping to the top: the newest sweep is just over the blank row, the oldest under it.
uint32_t scope(uint32_t center, uint16_t st) __reentrant
{
    uint8_t i, k;
    step = st;
    start = center;
    start = at(-N / 2);

    maps_ok = 0;                            // the screen buffer holds radio.c's bitmaps too (fw.h)
    bk_af(0);
    AMP_ON = 0;
    bk_write(0x43, 0x0058);                 // narrowest RF filter (6.25 kHz): a strong station stays in its own column; leaving retunes
    lcd_clear();
    xset(scr_buf, 0, (uint8_t)(2 * N + 10 + sizeof sc - 1));
    sc.base = 255;
    sc.cur = N / 2;
    for (;;) {
        sc.sum = 0;
        sc.over = sc.reach = 0;
        for (i = 0; i < N; i++) {
            look(i);
            WDT_KICK();
            k = key_event();
            if (!k) continue;
            if (k == K_EXIT) return 0;
            if (k == K_MENU) return at(sc.cur);
            if (k == K_UP || k == K_DOWN) {
                uint8_t old = sc.cur, h = now_h[old];
                sc.cur = (old + (k == K_UP ? 1 : N - 1)) & (N - 1);   // one step, wrapping
                lcd_fillp(2 * old, TOP, 2, TALL - h, P_BG);       // redraw the old column plain
                now_h[old] = 0;
                column(old, h, peak[old], 0);
                lcd_fillp(60, 0, 42, 8, P_BG);  // the new cursor's reading comes at its next look
            } else if (k == K_2 || k == K_8 || k == K_0 || k == K_5) {   // new window, peaks cleared, or SLOW: start clean
                if (k == K_5) {             // and a new scale
                    sc.slow ^= 1;
                    sc.base = 255;
                } else if (k != K_0) start = at(k == K_8 ? N / 4 : -N / 4);
                xset(scr_buf, 0, (uint8_t)CLR);
                lcd_fillp(0, TOP, W, WF + WF_H - TOP, P_BG);
                break;
            }
            readout();
        }
        if (i == N) {                       // a whole sweep: the scale follows the noise
            k = sc.prev = sc.base;
            if (k == 255) sc.base = sc.prev = (sc.sum >> 6) - 3;   // the first: the average, less 3 dB
            else {
                if (sc.over > 52) sc.base++;
                else if (sc.reach < 44) sc.base--;
                if (++sc.row == WF_H) sc.row = 0;   // the next row down, and the one under it blanked
                if ((k = sc.row + 1) == WF_H) k = 0;
                lcd_fillp(0, WF + k, W, 1, P_BG);
            }
        }
        frame();
    }
}

// The BK4819 frequency counter: key a radio next to this one and it
// reports the carrier. The TD-H3 has separate VHF and UHF front ends, so alternate them.
static void show(uint32_t f) __reentrant { freq_txt(f); lcd_text(0, 76, s, 2, P_OK); }
uint32_t freq_find(void) __reentrant
{
    uint32_t f, last = 0, found = 0;
    uint8_t uhf = 0, hits = 0, k;
    uint16_t t0 = now();

    maps_ok = 0;                            // the screen buffer holds radio.c's bitmaps too (fw.h)
    bk_af(0);
    AMP_ON = 0;
    remote_req |= REQ_REDRAW;
    for (;;) {
        if (remote_req & REQ_REDRAW) {      // first time, and when a watching computer asks
            remote_req &= ~REQ_REDRAW;
            lcd_clear();
            draw_labels(ff_l, 4);
            lcd_icon(0, 0, icon_target, P_DIM);
            if (found) show(found);
        }
        if ((uint16_t)(now() - t0) > 200 || !t0) {     // switch band every second
            uhf ^= 1;
            radio_rx_path(uhf);
            bk_tone(0);
            bk_freq(uhf ? 43500000UL : 14500000UL);
            bk_rx_on();
            bk_fscan(1);
            t0 = now() | 1;
        }
        delay_ms(10);
        k = key_event();
        if (k == K_EXIT) { bk_fscan(0); return 0; }
        if (k == K_MENU && found) { bk_fscan(0); return found; }
        f = bk_fscan_result();
        if (!f) continue;
        bk_fscan(1);                        // restart counting
        if (last && f - last + 99 < 199) hits++;   // two reads within 1 kHz: |f - last| < 100 as one unsigned compare
        else hits = 0;
        last = f;
        if (hits) {
            t0 = now() | 1;                 // stay on this band
            f = (f + 125) / 250 * 250;      // nearest 2.5 kHz; redraw only when it changes
            if (found == f) continue;
            found = f;
            show(f);
        }
    }
}
