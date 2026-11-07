// The radio's sounds, played by bk_chime() on the BK4819's two tone generators. Composed by
// hand for this firmware: each is a few struck notes (a level jump, then ramps to silence).
#include "fw.h"

// Power-on, as the boot screen's mast stands: a sonar ping, a whoosh falling like a ring spreading
// out, then G6 and C7. 630 ms, the same 16 steps the old chime took.
const __code note_t snd_hello[] = {
    {0, N_HZ, 2350, 0}, {0, N_GAIN, 60, 0}, {0, N_GAIN, 0, 10},
    {4, N_V2 | N_HZ, 1600, 0}, {0, N_V2 | N_GAIN, 60, 3}, {0, N_V2 | N_HZ, 380, 40}, {3, N_V2 | N_GAIN, 0, 37},
    {39, N_HZ, 1568, 0}, {0, N_GAIN, 80, 0}, {0, N_GAIN, 26, 14}, {14, N_GAIN, 0, 56},
    {4, N_V2 | N_HZ, 2093, 0}, {0, N_V2 | N_GAIN, 60, 0}, {0, N_V2 | N_GAIN, 20, 14}, {14, N_V2 | N_GAIN, 0, 52},
    {48, N_END, 0, 0},
};

// Key press: one short tick.
const __code note_t snd_click[] = {
    {0, N_HZ, 1250, 0}, {0, N_GAIN, 64, 0}, {0, N_GAIN, 0, 5}, {6, N_END, 0, 0},
};

// Done (saved, stored, copied, unlocked): G5 then C6, a rising fourth.
const __code note_t snd_ok[] = {
    {0, N_HZ, 784, 0}, {0, N_GAIN, 90, 0}, {0, N_GAIN, 30, 10}, {10, N_GAIN, 0, 30},
    {2, N_V2 | N_HZ, 1047, 0}, {0, N_V2 | N_GAIN, 100, 0}, {0, N_V2 | N_GAIN, 33, 12}, {12, N_V2 | N_GAIN, 0, 50},
    {51, N_END, 0, 0},
};

// Not possible: A5 then F#5, a falling minor third, short and soft.
const __code note_t snd_no[] = {
    {0, N_HZ, 880, 0}, {0, N_GAIN, 80, 0}, {0, N_GAIN, 0, 24}, {14, N_V2 | N_HZ, 740, 0},
    {0, N_V2 | N_GAIN, 90, 0}, {0, N_V2 | N_GAIN, 30, 8}, {8, N_V2 | N_GAIN, 0, 30}, {33, N_END, 0, 0},
};

// Battery low: E6 C6 A5, slow and falling.
const __code note_t snd_batt[] = {
    {0, N_HZ, 1319, 0}, {0, N_GAIN, 70, 0}, {0, N_GAIN, 23, 12}, {12, N_GAIN, 0, 50},
    {10, N_V2 | N_HZ, 1047, 0}, {0, N_V2 | N_GAIN, 70, 0}, {0, N_V2 | N_GAIN, 23, 12}, {12, N_V2 | N_GAIN, 0, 50},
    {10, N_HZ, 880, 0}, {0, N_GAIN, 70, 0}, {0, N_GAIN, 23, 12}, {12, N_GAIN, 0, 56},
    {56, N_END, 0, 0},
};

// Weather alert (repeats): a two-tone siren, 1175 and 880 Hz, twice.
const __code note_t snd_alert[] = {
    {0, N_HZ, 1175, 0}, {0, N_GAIN, 100, 1}, {12, N_HZ, 880, 0}, {12, N_HZ, 1175, 0},
    {12, N_HZ, 880, 0}, {12, N_GAIN, 0, 4}, {6, N_END, 0, 0},
};

// BELL, a call after a quiet spell: G6 struck twice, like a desk bell. 300 ms.
const __code note_t snd_bell[] = {
    {0, N_HZ, 1568, 0}, {0, N_GAIN, 100, 0}, {0, N_GAIN, 30, 8}, {8, N_GAIN, 0, 20},
    {12, N_V2 | N_HZ, 1568, 0}, {0, N_V2 | N_GAIN, 100, 0}, {0, N_V2 | N_GAIN, 30, 8}, {8, N_V2 | N_GAIN, 0, 30},
    {30, N_END, 0, 0},
};
