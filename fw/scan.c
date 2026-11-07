// Spectrum scope and freq find: two full-screen modes that hand back a frequency.
#include "fw.h"

#define N 64                                // points, 2 px each
__xdata uint8_t scr_buf[SCR_BUF];          // 2 * N + 10 here
// In the screen buffer (fw.h): the bar heights now, the peaks, and the readout text.
#define now_h scr_buf
#define peak (scr_buf + N)
#define s ((__xdata char *)scr_buf + 2 * N)
#define TOP 12
#define TALL 88

static void freq_txt(uint32_t f) __reentrant { dec(s, f / 10, 7, 4); }   // "146.5200" from 10 Hz units, in s

static uint8_t height(int16_t dbm) __reentrant          // -130..-50 dBm over TALL px
{
    if (dbm <= -130) return 0;
    if (dbm >= -50) return TALL;
    uint8_t x = dbm + 130;
    return x + x / 10;                      // x * TALL / 80 (TALL 88: x * 11 / 10)
}

static const __code label_t scope_l[] = {{60, 0, 1, P_DIM, "SCOPE"}, {0, BOT_Y - 14, 1, P_DIM, "UP/DN cursor 2/8 move"}, {0, BOT_Y, 1, P_DIM, "MENU tune  V/M exit"}};
static const __code label_t ff_l[] = {{11, 0, 1, P_DIM, "FREQ FIND"}, {0, 30, 2, P_FG, "key radio"}, {0, 48, 2, P_FG, "nearby"}, {0, BOT_Y, 1, P_DIM, "MENU take  V/M exit"}};
static void column(uint8_t i, uint8_t h, uint8_t o, uint8_t cursor)
{
    uint8_t x = 2 * i, y = TOP + TALL;
    if (cursor) {                           // the cursor column is drawn whole, in its own colours
        lcd_fillp(x, TOP, 2, TALL - h, P_BAR);
        lcd_fillp(x, y - h, 2, h, P_HI);
    } else if (h > o) lcd_fillp(x, y - h, 2, h - o, P_OK);   // only what changed: grow...
    else if (h < o) {
        lcd_fillp(x, y - o, 2, o - h, P_BG);                // ...or shrink
        if (peak[i] > h && peak[i] <= o) lcd_fillp(x, y - peak[i], 2, 1, P_ACC);   // peak was under it
    }
    if (h > peak[i]) peak[i] = h;
    else if (peak[i] > h && peak[i] > o) lcd_fillp(x, y - peak[i], 2, 1, P_ACC);   // first time drawn
    now_h[i] = h;
}

static __xdata uint32_t start;              // the scope's window: point i is at(i)
static __xdata uint16_t step;
static uint32_t at(int8_t i) __reentrant { return start + (int32_t)step * i; }
static void readout(uint8_t c) __reentrant { freq_txt(at(c)); lcd_text(0, 0, s, 1, P_FG); }   // point c's frequency

// UP/DOWN move the cursor, 2/8 move the window, 0 clears the peaks, MENU tunes, V/M leaves.
uint32_t scope(uint32_t center, uint16_t st) __reentrant
{
    uint8_t cur = N / 2, i, k;
    step = st;
    start = center;
    start = at(-N / 2);

    maps_ok = 0;                            // the screen buffer holds radio.c's bitmaps too (fw.h)
    bk_af(0);
    AMP_ON = 0;
    bk_write(0x43, 0x0058);                 // narrowest RF filter (6.25 kHz): a strong station stays in its own column; leaving retunes
    lcd_clear();
    xset(scr_buf, 0, 2 * N);                // now_h and peak, side by side
    for (;;) {
        for (i = 0; i < N; i++) {
            column(i, height(bk_dbm_at(at(i))), now_h[i], i == cur);
            WDT_KICK();
            k = key_event();
            if (!k) continue;
            if (k == K_EXIT) return 0;
            if (k == K_MENU) return at(cur);
            if (k == K_UP || k == K_DOWN) {
                uint8_t old = cur, ox = 2 * old;
                cur = (cur + (k == K_UP ? 1 : N - 1)) & (N - 1);   // one step, wrapping
                lcd_fillp(ox, TOP, 2, TALL - now_h[old], P_BG);   // redraw the old column plain
                column(old, now_h[old], 0, 0);
            } else if (k == K_2 || k == K_8 || k == K_0) {   // new window, or peaks cleared: start clean
                if (k != K_0) start = at(k == K_8 ? N / 4 : -N / 4);
                xset(scr_buf, 0, 2 * N);
                lcd_fillp(0, TOP, W, TALL, P_BG);
                break;
            }
            readout(cur);
        }
        readout(cur);
        draw_labels(scope_l, 3);
        lcd_icon(49, 1, icon_pulse, P_DIM);
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
