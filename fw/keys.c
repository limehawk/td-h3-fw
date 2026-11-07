// Keypad: rows R1..R6 are open drain (the row scanned an output at 0, the rest inputs with the
// MCU's pull-ups), columns L1..L3 are inputs with 10k pull-ups.
// Side keys short a column straight to ground, so they read with no row driven.
//        L3      L2     L1
// R1   MENU     UP     EXIT     (MENU = blue, EXIT = red)
// R2           DOWN
// R3     1       2       3
// R4     4       5       6
// R5     7       8       9
// R6     *       0       #
// side  SIDE2   SIDE1   PTT        SIDE1 is PTT-B, SIDE2 the flashlight (stock 0x6EE2)
// A side key held makes its column read as every key in it down. So a row is read too: through a
// key in that column it reads low against its pull-up. That gives the keys held with PTT (DTMF).
// Rows are never driven high, so no key shorts a driven row to a grounded column.
#include "fw.h"

static const __code uint8_t map[6][4] = {   // [row][column bits >> 1: L3, L2, L1, two keys]
    {K_MENU, K_UP, K_EXIT}, {K_NONE, K_DOWN, K_NONE}, {K_1, K_2, K_3},
    {K_4, K_5, K_6}, {K_7, K_8, K_9}, {K_STAR, K_0, K_HASH},
};

uint8_t ptt_ok;                             // 0 from power-on till PTT and PTT-B are seen up: holding PTT
                                            // for the bootloader never keys up our firmware
uint8_t ptt_key;
static void rows_off(void) { P0 &= 0xF0; P2 &= 0x3F; P0TRIS &= 0xF0; P2TRIS &= 0x3F; }   // latches 0, all inputs

static const __code uint8_t row_bit[6] = {0x08, 0x04, 0x02, 0x01, 0x80, 0x40};   // R1-R4 P0.3-P0.0, R5 P2.7, R6 P2.6
static uint8_t row_low(uint8_t r) __reentrant   // row r driven low; returns 0 if it read low before (a grounded key)
{
    uint8_t m = row_bit[r], in;
    rows_off();
    udelay(); udelay();
    if (r < 4) { in = P0 & m; P0TRIS |= m; } else { in = P2 & m; P2TRIS |= m; }
    return in;
}

static uint8_t cols(void) { udelay(); udelay(); return (!KEY_L3) | (!KEY_L2) << 1 | (!KEY_L1) << 2; }

uint8_t key_scan(void)
{
    static __data uint8_t speed;
    uint8_t c, g, k = K_NONE;
    // PC LINK turns remote off: 38400 for CHIRP. Armed, the loader's 115200, so the flasher is already
    // at its speed when PTT restarts into it; 1 Mbaud again once the arming lapses.
    c = remote ? (loader_armed ? USB_LOADER : USB_REMOTE) : USB_CLONE;
    if (c != speed) {
        uart_usb_fast(speed = c);
        if (!remote) mirror = loader_armed = 0;   // remote_tick no longer runs to let them lapse
    }
    if (remote) { remote_tick(); remote_poll(); if (rkey) return rkey; }   // a late byte is judged after the let-go
    rows_off();
    g = cols();
    if (!(g & 6)) ptt_ok = 1;
    else if (!ptt_ok) return K_NONE;        // still held from power-on: nothing (it grounds a keypad column too)
    if ((g & 6) && loader_armed) remote_loader();   // PTT or PTT-B: neither may key up while armed
    for (uint8_t r = 0; r < 6; r++) {
        c = row_low(r) ? 0 : g;             // low undriven: its key in the grounded column
        c |= cols() & ~g;
        if (c) k = map[r][c >> 1];          // c 1, 2, 4: L3, L2, L1
    }
    rows_off();
    ptt_key = g & (g - 1) ? K_NONE : k;     // one side key held, PTT or PTT-B
    if (g & 4) return K_PTT;
    if (g & 2) return K_SIDE1;
    if (g & 1) return K_SIDE2;
    return k;
}

uint8_t key_held;
static uint8_t last, same, long_sent;
static uint16_t t_down, t_rep;

#define REPEATS(k) ((uint8_t)((k) - K_UP) < 2)     // K_UP or K_DOWN

// Call as often as you like (main loop ~10 ms, scope ~2.5 ms). A state counts once it has read
// the same for 2 ticks (5-10 ms), whatever the call rate. UP/DOWN fire on press and repeat
// every 0.1 s after 0.5 s. Other keypad keys report on release (short) or once at 0.8 s
// (k | K_LONG). PTT and side keys report on press and live in key_held.
uint8_t key_event(void) __reentrant
{
    uint8_t k = key_scan(), prev;
    uint16_t t = now();
    if (k != last) { last = k; same = 0; t_down = t; return K_NONE; }
    if (!same) {
        if ((uint16_t)(t - t_down) < 2) return K_NONE;
        same = 1;
        prev = key_held;
        key_held = k;
        t_down = t_rep = t;
        if (k == K_NONE) return prev && prev < K_PTT && !long_sent ? prev : K_NONE;
        long_sent = REPEATS(k);
        return k >= K_PTT || REPEATS(k) ? k : K_NONE;
    }
    if (REPEATS(k) && (uint16_t)(t - t_down) >= 100 && (uint16_t)(t - t_rep) >= 20) {
        t_rep = t;
        return k;
    }
    if (k && k < K_PTT && !long_sent && (uint16_t)(t - t_down) >= 160) {
        long_sent = 1;
        return k | K_LONG;
    }
    return K_NONE;
}
