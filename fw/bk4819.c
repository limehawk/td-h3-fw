// BK4819 transceiver on a bit-banged 3-wire bus (SDA P5.2, SCN P5.3, SCK P5.4).
// Register values: stock firmware 250317 for this board (init table at 0x6950, RX/TX
// sequences at 0x8555/0x7EB1). Tone, scan and counter registers: the Beken BK4819
// datasheet's register table.
//
// MIT (LICENSE) but for these parts, adapted from Apache-2.0 code and changed for this board and MCU
// (rewritten in our own form; the register words and gain tables are theirs):
//  - the 55 Hz squelch-tail detector set-up (CTCSS 67.0 Hz, REG_07 CTC2 = 462) and the interrupt
//    acknowledge through REG_02 (bk_dtmf_on, bk_irq): DualTachyon's uv-k5-firmware, driver/bk4819.c
//    and app/app.c. Copyright 2023 Dual Tachyon, https://github.com/DualTachyon
//  - the SSB receive set-up in bk_mode (REG_47 AF type 5, REG_3D, REG_73 bit 4): RADIO_SetModulation in Armel
//    / F4HWN's radio.c, https://github.com/armel/uv-k5-firmware-custom (from egzumer's and DualTachyon's).
//    Copyright 2023 Dual Tachyon and contributors
//  - the VOX detector set-up (bk_vox_on: REG_46, REG_79, REG_7A's low bits, REG_31 bit 2, the found and
//    lost interrupts on REG_3F/REG_02 bits 5:4): DualTachyon's driver/bk4819.c BK4819_EnableVox and
//    app/app.c; our thresholds and our own hang (REG_7A bits 15:12 at 0).
//  - the LNA gain steps (att_lna) and the AM gain table (am_r13, am_db): OneOfEleven's am_fix.c, as
//    carried in https://github.com/armel/uv-k5-firmware-custom. Copyright 2023 OneOfEleven
//  - the scope's RSSI read in bk_dbm_at (wait while REG_63's glitch count is 200 or more, at most 50
//    reads, then drop the first RSSI read): GetRssi in fagci's spectrum.c, as carried in the UV-K1 /
//    UV-K5 V3 port, https://github.com/armel/uv-k1-k5v3-firmware-custom. Copyright 2023 fagci
// Licensed under the Apache License, Version 2.0 (the "License"); you may not use these parts except
// in compliance with the License. You may obtain a copy of the License at
// http://www.apache.org/licenses/LICENSE-2.0 (also LICENSE-APACHE). Unless required by applicable
// law or agreed to in writing, software distributed under the License is distributed on an "AS IS"
// BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the License
// for the specific language governing permissions and limitations under the License.
#include "fw.h"

uint8_t mic_gain;                           // set from cfg at boot (no initialized XDATA)
static const __code uint8_t mic_tab[10] = {0x05, 0x07, 0x08, 0x09, 0x0A, 0x0B, 0x0C, 0x0D, 0x0E, 0x11};   // stock 0x69BB

static void out8(uint8_t b) __reentrant
{
    for (uint8_t i = 0; i < 8; i++, b <<= 1) {
        BK_SCK = 0; udelay();
        BK_SDA = b & 0x80; udelay();
        BK_SCK = 1;
    }
}

void bk_write(uint8_t reg, uint16_t v) __reentrant
{
    BK_SCN = 0; udelay();
    out8(reg); out8(v >> 8); out8(v);
    BK_SCK = 0; udelay();
    BK_SDA = 0; BK_SCN = 1;
}

uint16_t bk_read(uint8_t reg) __reentrant
{
    uint16_t v = 0;
    BK_SCN = 0; udelay();
    out8(reg | 0x80);
    BK_SCK = 0;
    P5TRIS &= ~0x04;                        // SDA in
    for (uint8_t i = 0; i < 16; i++) {
        BK_SCK = 1; udelay();
        v = v << 1 | BK_SDA;
        BK_SCK = 0; udelay();
    }
    BK_SCN = 1;
    P5TRIS |= 0x04;
    return v;
}

static const __code uint8_t init_tab[] = {   // reg, hi, lo — stock's table, in order
    0x37,0x1D,0x0F, 0x13,0x03,0xBE, 0x12,0x03,0x7B, 0x11,0x02,0x7B, 0x10,0x00,0x7A,
    0x14,0x00,0x19, 0x49,0x2A,0x38, 0x7B,0x84,0x20, 0x7D,0xE9,0x4C, 0x7E,0x30,0x2E,
    0x2E,0x02,0x04, 0x40,0x34,0xF1, 0x2A,0x4F,0x18, 0x28,0x56,0x38, 0x29,0xAB,0x24,
    0x31,0x00,0x00, 0x47,0x60,0x40, 0x1F,0xA6,0x56, 0x1E,0x4C,0x58, 0x3E,0xA0,0x37,
    0x34,0x00,0x00, 0x35,0x00,0x00, 0x33,0xBD,0x00, 0x4D,0xA0,0x29, 0x4E,0x6F,0x22,
    0x53,0xE6,0x6E, 0x77,0x88,0xEF,
};
static const __code uint8_t agc_tab[16] = {  // REG_09 low bytes, high byte = index << 4
    0x6F,0x6B,0x67,0x62,0x50,0x47,0x3A,0x2C,0x41,0x37,0x25,0x17,0xE4,0xCB,0xB5,0x9F,
};

static void bk_table(const __code uint8_t *t, uint8_t n) __reentrant;
static uint16_t af_on, af_now;              // REG_47 value with audio enabled; as written now
static uint8_t md;                          // the MOD_* the chip is in (bk_init: none yet)
static uint8_t st;                          // BK_SLEEP, BK_RX or BK_TX: what bk_chime puts back
enum { BK_SLEEP, BK_RX, BK_TX };

void bk_init(void)
{
    bk_write(0x00, 0x8000);
    bk_write(0x00, 0x0000);
    delay_ms(50);
    bk_table(init_tab, sizeof init_tab);
    for (uint8_t i = 0; i < 16; i++)
        bk_write(0x09, (uint16_t)i << 12 | agc_tab[i]);
    af_on = 0x6140;
    md = 0xFF;
}

void bk_freq(uint32_t f) __reentrant
{
    bk_write(0x39, f >> 16);
    bk_write(0x38, f);
}

// REG_13, the front-end gain: bits 9:8 LNA short, 7:5 LNA, 4:3 mixer, 2:0 PGA. Every REG_13 write
// goes through here. ATT (the big rigs' fixed ~10 dB pad) steps the LNA down about 10 dB: its steps
// are -24 -19 -14 -9 -6 -4 -2 0 dB (OneOfEleven's am_fix.c, Apache-2.0: top of file), so FM's
// 0x03BE (LNA 5) becomes 0x035E.
uint8_t bk_att;                             // set from cfg at boot and on a menu change
static const __code uint8_t att_lna[8] = {0, 0, 0, 1, 2, 2, 2, 3};
void bk_gain(uint16_t r13) __reentrant
{
    if (bk_att) r13 = r13 & ~0x00E0 | att_lna[r13 >> 5 & 7] << 5;
    bk_write(0x13, r13);
}

// AM (air band) against FM: the wide RF filter (stock's AM one is the narrowest and cut off-centre
// planes), the AGC's AM thresholds (REG_49, REG_14) and, in AM, no FM de-emphasis or 3 kHz low-pass
// (REG_2B bits 8, 9): AM isn't pre-emphasized. Values: F4HWN's BK4819_InitAGC and its UV-K1 port's AM
// profile (REG_2B bits per its BK4829 notes; 0 is every filter on, as it writes for FM).
static const __code uint8_t am_tab[] = {0x43,0x36,0x28, 0x49,0x19,0x20, 0x14,0x00,0x00, 0x2B,0x03,0x00};
static const __code uint8_t fm_tab[] = {0x49,0x2A,0x38, 0x14,0x00,0x19, 0x4A,0x54,0x50, 0x2B,0x00,0x00};
// USB takes AM's AGC, gain steps and REG_2B (no de-emphasis: SSB isn't pre-emphasized), then the
// SSB demodulator: REG_47 AF type 5 (baseband) with the IF at 0 (REG_3D; the chip's own 0x2AAB otherwise) and
// AFC off (REG_73 bit 4) in every mode but FM: F4HWN's RADIO_SetModulation (Apache-2.0: top of file). The
// narrowest RF filter, NARROW 6.25's, as F4HWN does for its narrowest.
static __xdata uint8_t am_i;                // bk_am_gain's step, 0 = full gain
#define ssb (md == MOD_USB)                  // the squelch goes by RSSI alone
void bk_mode(uint8_t mode, uint8_t narrow) __reentrant
{
    if (mode != md) {                       // only on a change: dual watch retunes every few hundred ms
        md = mode;
        bk_write(0x3D, ssb ? 0 : 0x2AAB);
        bk_write(0x73, bk_read(0x73) & ~0x10 | (mode ? 0x10 : 0));
    }
    if (mode) {
        bk_table(am_tab, sizeof am_tab);
        af_on = 0x6740;
        if (ssb) { bk_write(0x43, 0x1248); af_on = 0x6540; }
        am_i = 0;
        bk_gain(0x03FF);                    // ATT: 0x037F
    } else {
        bk_table(fm_tab, sizeof fm_tab);
        bk_write(0x43, !narrow ? 0x3628 : bk_opt & BKO_NAR6 ? 0x1248 : 0x4848);
        // Bits 11:9, the weak-signal filter, as wide as the strong one: stock's 3028 / 4048 narrow weak stations
        // to the minimum (F4HWN v3.6). NARROW kHz 6.25: F4HWN's NARROWER RF filters (its 1348, 2.0 kHz) with
        // bits 8:6 kept at our narrow's 2.5 kHz TX audio low-pass, as transmit keeps what receive left here.
        af_on = 0x6140;
        bk_gain(0x03BE);
    }
}

void bk_af(uint8_t on) __reentrant { bk_write(0x47, af_now = on ? af_on : 0x6040); }

static const __code uint8_t rx_tab[] = {
    0x37,0x1F,0x0F, 0x31,0x00,0x00, 0x28,0x56,0x38, 0x29,0xAB,0x24, 0x75,0xE1,0x02, 0x54,0x90,0x09,
    0x55,0x31,0xA9, 0x48,0xB2,0xA8, 0x3F,0x00,0x00, 0x30,0x00,0x00, 0x30,0xBF,0xF1,
};
static const __code uint8_t tx_tab[] = {     // REG_19 1041: mic AGC on (F4HWN's init; stock never writes it)
    0x19,0x10,0x41, 0x31,0x00,0x00, 0x28,0x6B,0x00, 0x29,0xAB,0x24, 0x74,0xE1,0x02, 0x44,0x91,0x09, 0x45,0x32,0xA9,
    0x40,0x34,0xBA,
};
static const __code uint8_t tx_tab2[] = {
    0x37,0x1D,0x0F, 0x52,0x03,0x10, 0x30,0x00,0x00, 0x30,0xC1,0xFE, 0x50,0x3B,0x20,
};
static void bk_table(const __code uint8_t *t, uint8_t n) __reentrant
{
    for (; n; n -= 3, t += 3) bk_write(t[0], (uint16_t)t[1] << 8 | t[2]);
}

// COMPANDER (bk_opt): the expander, 1:2, on receive (REG_28 6B38) and the compressor, 2:1, on transmit
// (REG_29 AB40), each with REG_31 bit 3 on: F4HWN's BK4819_SetCompander values. Off: stock's 28, 29, 31.
uint8_t bk_opt;                             // set from cfg at boot and on a menu change
static const __code uint8_t comp_tab[] = {0x28,0x6B,0x38, 0x31,0x00,0x08, 0x29,0xAB,0x40};

// VOX: the chip's own voice detector (the Apache-2.0 part, top of file). bk_vox is the level that
// finds a voice (REG_46, 11 bits; the TD-H3's own scale: stock compares REG_64 with 1226 down to 232,
// its table at 0x693C); it's lost under three quarters of that (REG_79). The hang is main.c's VOX
// DELAY, so the chip's own (REG_7A bits 15:12) is 0. Stock sets REG_31 bit 2 on receive and transmit
// too (0x8574, 0x7E9D). 0: VOX off, and the tables leave REG_31 and REG_3F without it.
uint16_t bk_vox;
static void bk_vox_on(void)
{
    if (!bk_vox) return;
    bk_write(0x46, 0xA000 | bk_vox);
    bk_write(0x79, 0x1800 | (bk_vox - (bk_vox >> 2)));
    bk_write(0x7A, 0x089A);
    bk_write(0x31, bk_read(0x31) | 4);
}

void bk_rx_on(void)
{
    bk_table(rx_tab, sizeof rx_tab);
    if (bk_opt & BKO_CRX && !ssb) bk_table(comp_tab, 6);   // no expander on SSB
    bk_vox_on();
    st = BK_RX;
}

void bk_tx_on(void)
{
    bk_table(tx_tab, sizeof tx_tab);
    if (bk_opt & BKO_CTX) bk_table(comp_tab + 3, 6);
    bk_write(0x7D, 0xE940 | mic_tab[mic_gain]);   // mic gain step 0-9, stock's default is 5
    bk_vox_on();
    bk_table(tx_tab2, sizeof tx_tab2);
    st = BK_TX;
}

// DCS (TIA-603): 12 data bits, binary 100 then the 9-bit code, followed by the 11 parity
// bits of the cyclic Golay(23,12) code, generator x^11+x^10+x^6+x^5+x^4+x^2+1 (0xC75):
// the remainder of data * x^11 divided by the generator. Data in bits 11:0, parity 22:12.
uint32_t bk_golay(uint16_t code) __reentrant
{
    uint16_t data = code | 0x800, d = data, r = 0;
    for (uint8_t i = 12; i; i--, d <<= 1) {   // data bits, high first, through the 11-bit remainder
        r <<= 1;
        if ((d ^ r) & 0x800) r ^= 0x475;      // x^11 out, with the data bit: subtract the generator
        r &= 0x7FF;
    }
    return data | (uint32_t)r << 12;
}

void bk_tone(uint16_t t) __reentrant
{
    if (!t) { bk_write(0x51, 0x0000); return; }
    if (t & TONE_DCS) {
        uint32_t cw = bk_golay(t & 0x1FF);
        if (t & TONE_INV) cw ^= 0x7FFFFF;
        bk_write(0x51, 0x8033);             // CxCSS on, CDCSS 23-bit, TX gain 51
        bk_write(0x07, 2775);
        bk_write(0x08, cw & 0xFFF);
        bk_write(0x08, 0x8000 | (uint16_t)(cw >> 8) >> 4);   // bits 23:12
        return;
    }
    bk_write(0x51, t == TONE_1050 ? 0x944A : 0x904A);   // CTCSS, TX gain 74; 944A adds 1050/4 Hz detect
    bk_write(0x07, (uint32_t)t * 2065 / 1000);
}

// REG_0C: bits 15:14 CDCSS match (1 = programmed word), bits 11:10 CTC match.
uint8_t bk_tone_heard(uint16_t t) __reentrant
{
    uint16_t r = bk_read(0x0C);
    return ((t & TONE_DCS ? r >> 14 : r >> 10) & 3) == 1;
}

// Hardware squelch, stock's tables (0x6A05-0x6B1C): per level 0-9, REG_78 = RSSI open, close and
// REG_4F = noise close, open. Rows: UHF wide, UHF narrow, VHF wide, VHF narrow (split at 300 MHz).
// The chip then reports open/closed in REG_0C bit 1 (stock reads it at 0x57D6).
static const __code uint8_t sql_tab[4][40] = {
    {0x48,0x39,0x45,0x30,0x4C,0x49,0x42,0x3B,0x4E,0x4B,0x40,0x39,0x50,0x4D,0x3D,0x36,0x52,0x4F,0x3A,0x33,0x55,0x52,0x36,0x2C,0x58,0x55,0x2F,0x29,0x5B,0x58,0x2C,0x26,0x5D,0x5A,0x2A,0x24,0x5F,0x5C,0x28,0x22},  // UHF wide
    {0x48,0x39,0x45,0x30,0x4C,0x49,0x44,0x3C,0x4E,0x4B,0x41,0x39,0x50,0x4D,0x3E,0x36,0x52,0x4F,0x3B,0x33,0x55,0x52,0x37,0x2C,0x58,0x55,0x2F,0x29,0x5B,0x58,0x2D,0x27,0x5E,0x5B,0x2B,0x25,0x60,0x5D,0x2B,0x25},  // UHF narrow
    {0x48,0x39,0x45,0x30,0x52,0x4F,0x47,0x3D,0x55,0x52,0x42,0x38,0x57,0x54,0x3D,0x33,0x59,0x56,0x38,0x2E,0x5B,0x58,0x2F,0x29,0x5E,0x5B,0x2C,0x26,0x62,0x5D,0x27,0x20,0x65,0x62,0x23,0x1C,0x69,0x66,0x23,0x1C},  // VHF wide
    {0x48,0x39,0x45,0x30,0x52,0x4E,0x43,0x37,0x53,0x4F,0x3F,0x35,0x55,0x51,0x3C,0x32,0x56,0x52,0x39,0x2F,0x58,0x55,0x36,0x2C,0x5B,0x58,0x31,0x29,0x5E,0x5B,0x2C,0x26,0x62,0x5D,0x27,0x20,0x65,0x62,0x23,0x1C},  // VHF narrow
};

// The squelch's open thresholds: RSSI in REG_67 units / 2, and noise.
// A scan asks the chip directly, as Quansheng firmware does: a carrier reads strong with little noise,
// band noise reads strong too but noisy. Then only real stations wait for the hardware squelch.
static uint8_t sql_rssi, sql_noise;
uint8_t bk_sql_near(void)
{
    return (uint8_t)(bk_read(0x67) >> 1) >= sql_rssi && (ssb || ((uint8_t)bk_read(0x65) & 0x7F) <= sql_noise);   // REG_65: noise
}

void bk_squelch(uint8_t level, uint8_t vhf, uint8_t narrow) __reentrant
{
    const __code uint8_t *t = &sql_tab[0][0] + (uint8_t)((uint8_t)(vhf * 2 + narrow) * 40 + 4 * level);   // at most 3 * 40 + 36
    sql_rssi = t[0] >> 1; sql_noise = t[3];
    bk_write(0x78, (uint16_t)t[0] << 8 | t[1]);
    bk_write(0x4F, (uint16_t)t[2] << 8 | t[3]);
}

// SSB has no carrier for the noise detector to quieten: there the squelch is the RSSI level alone.
uint8_t bk_sql_open(void) { return ssb ? bk_sql_near() : bk_read(0x0C) >> 1 & 1; }

// ---- interrupts: DTMF digits, and the 55 Hz squelch tail ----
// DTMF decoder set up as stock does (0xEEC6: REG_21, REG_24; REG_3F bit 11 its interrupt), but for
// the threshold. With tail (a channel with no RX tone of its own), the tail detector too, as
// DualTachyon's uv-k5-firmware sets it (Apache-2.0: top of file; radio.c RADIO_SetupRegisters,
// CODE_TYPE_OFF): CTCSS on at 67.0 Hz (the caller's bk_tone(670),
// REG_51 = 904A), REG_07 CTC2 = 25391 / 55 Hz = 462 (driver/bk4819.c BK4819_Set55HzTailDetection),
// its interrupt on REG_3F bit 10.
void bk_dtmf_on(uint8_t tail) __reentrant
{
    bk_write(0x21, 0x06D8);
    bk_write(0x24, 0xC17E);                 // decoder on, DTMF (not 5-tone); threshold 130, not stock's 24, which
                                            // misses other brands' radios (F4HWN's bk4819.c: 128-247 decode them)
    if (tail) bk_write(0x07, 1 << 13 | 462);
    bk_write(0x3F, (tail ? 0x0C00 : 0x0800) | (bk_vox ? BK_IRQ_VOX : 0));   // VOX found and lost too
}

// The interrupts (BK_IRQ_*) since the last call, or 0. REG_0C bit 0 = one is pending; writing REG_02
// acknowledges it and REG_02 then says which (DualTachyon app.c, APP_CheckRadioInterrupts).
uint16_t bk_irq(void)
{
    if (!(bk_read(0x0C) & 1)) return 0;
    bk_write(0x02, 0);
    return bk_read(0x02);
}

// The digit, after BK_IRQ_DTMF: REG_0B bits 11:8 (stock 0xC3F7).
const __code char dtmf_chars[] = "0123456789ABCD*#";   // DTMF code n's character
char bk_dtmf(void) { return dtmf_chars[bk_read(0x0B) >> 8 & 15]; }

// AM at full gain (REG_13 = 03FF, 0 dB) clips above about -89 dBm. Each step down the table takes
// more gain off: a reading still over -85 goes a step down, and one that would read under -95 a step
// up goes back, so each step has 10 dB of hysteresis. Returns the dB that were off for this reading,
// to add back so the meter shows the real level. Through bk_gain, so ATT stays on (it moves only the
// LNA bits). The gain words and their dB are five of the 43 rows of OneOfEleven's am_fix.c
// gain_table (Apache-2.0: top of file); the stepping is ours.
static const __code uint16_t am_r13[] = {0x03FF, 0x03BE, 0x035E, 0x00FF, 0x001F};
static const __code uint8_t am_db[] = {0, 7, 17, 28, 52};
uint8_t bk_am_gain(int16_t dbm) __reentrant
{
    uint8_t i = am_i, cut = am_db[i];
    if (dbm > -85 && i < sizeof am_db - 1) i++;
    else if (i && dbm + (uint8_t)(cut - am_db[i - 1]) < -95) i--;   // cut - am_db[i - 1]: the step back up, in dB
    else return cut;
    bk_gain(am_r13[am_i = i]);
    return cut;
}

int16_t bk_dbm(void) { return (int16_t)(uint8_t)(bk_read(0x67) >> 1) - 160; }   // REG_67 bits 8:1

void bk_sleep(void)
{
    static const __code uint8_t t[] = {0x30,0x00,0x00, 0x37,0x1D,0x00};
    bk_table(t, sizeof t);
    st = BK_SLEEP;
}

// ---- tone scan: the chip measures whatever sub-audio tone it hears ----
// REG_68: bit 15 = nothing found, 12:0 = CTCSS frequency x 20.64888. In 0.1 Hz: x 10 / 20.64888.
uint16_t bk_ctcss_measured(void)
{
    uint16_t r = bk_read(0x68);
    if (r & 0x8000) return 0;
    return (uint16_t)((uint32_t)(r & 0x1FFF) * 126953 >> 16) >> 2;   // x 100000 / 206489 exactly, as x 126953 / 2^18
}

// REG_69 (bit 15 = nothing found) and REG_6A: the received 23-bit DCS word, 12 bits each.
uint32_t bk_dcs_measured(void)
{
    uint16_t hi = bk_read(0x69), lo;
    if (hi & 0x8000) return 0;
    lo = bk_read(0x6A);
    return (uint32_t)(hi & 0x0FFF) << 12 | (lo & 0x0FFF);
}

// ---- freq find: the chip's frequency counter ----
// REG_32 bit 0 starts a frequency scan (0x0245: scan on with the datasheet's longer gate time);
// REG_0D bit 15 is set while it counts, bits 10:0 and REG_0E are the carrier in 10 Hz.
void bk_fscan(uint8_t on) __reentrant
{
    bk_write(0x32, 0x0244 | on);
    if (!on) bk_write(0x30, 0);
}

uint32_t bk_fscan_result(void)              // 10 Hz units, 0 while still counting
{
    uint16_t hi = bk_read(0x0D);
    if (hi & 0x8000) return 0;
    return (uint32_t)(hi & 0x07FF) << 16 | bk_read(0x0E);
}

uint16_t bk_mic(void) { return bk_read(0x64) & 0x7FFF; }   // mic amplitude while transmitting

// ---- clear tones: the same sine generators, to the speaker instead of the air ----
// REG_47 AF source 3 (tone) and REG_30 = AF DAC + DSP put tone 1 (REG_71) and tone 2 (REG_72,
// same scale) on the speaker. REG_70: tone 1 on bit 15, gain 14:8; tone 2 on bit 7, gain 6:0.
// The tone generators to the speaker (1) and back to the receiver (0).
static const __code uint8_t vco_tab[] = {0x30,0x02,0x00, 0x30,0xBF,0xF1}, rx_on[] = {0x30,0x00,0x00, 0x30,0xBF,0xF1};
static const __code uint8_t spk_tab[] = {
    0x37,0x1F,0x0F,                         // awake, in case battery save had it asleep
    0x47,0x63,0x40, 0x30,0x00,0x00, 0x30,0x03,0x02, 0x50,0x3B,0x20};
static void bk_spk(uint8_t on) __reentrant
{
    bk_write(0x70, 0);
    if (on) {
        AMP_ON = 0;                         // the amp stays off while the chip switches over: no pops
        bk_table(spk_tab, sizeof spk_tab);
        AMP_ON = 1;
        return;
    }
    delay_ms(20);                           // let the output settle silent before the amp goes off
    AMP_ON = 0;
    bk_write(0x47, af_now);
    if (st == BK_SLEEP) bk_sleep();
    else bk_table(rx_on, sizeof rx_on);
}

// A sound is a list of ramps (note_t, fw.h): each voice's level and pitch move in straight lines,
// one step per 5 ms tick. Blocks until the N_END step. Returns 0 while transmitting.
// Afterwards it puts back the state this file last set (st, af_now), not register read-backs.
// ponytail: 5 ms steps; Timer0 sub-tick steps if a fast slide ever sounds stepped.
typedef struct { uint16_t v, t; int16_t s; uint8_t n; } ramp_t;   // a level << 8 or a pitch << 3, moving to t
typedef struct { ramp_t r[4]; uint16_t hz[2]; } voice_t;   // r: voice 1 level, pitch, voice 2 level, pitch
static __xdata voice_t vc;                  // plain XDATA indexes far cheaper than the stack
static int16_t sdiv(int16_t d, uint8_t n) __reentrant  // d / n as C rounds it (toward 0), without the signed divide routine
{
    uint16_t q;
    if (d >= 0) return (uint16_t)d / n;
    q = (uint16_t)-d / n;
    return -q;
}
void chime_no(void) { bk_chime(snd_no); }   // the two sounds most keys play: a call is 3 bytes smaller
void chime_ok(void) { bk_chime(snd_ok); }

static void pitch(uint8_t v) __reentrant    // voice v's tone generator to its pitch ramp, if that moved
{
    uint16_t h = vc.r[(uint8_t)(v * 2 + 1)].v >> 3;
    if (h != vc.hz[v]) { vc.hz[v] = h; bk_write(0x71 + v, TONE_HZ(h)); }
}

static void ramp(__xdata ramp_t *p, uint16_t to, uint8_t n) __reentrant   // p to `to` in n ticks, 0 = at once
{
    p->t = to; p->n = n;
    if (n) p->s = sdiv(to - p->v, n); else p->v = to;
}

static void tick(void)                      // each ramp one 5 ms step on
{
    for (__xdata ramp_t *p = vc.r; p != vc.r + 4; p++)
        if (p->n) p->v = --p->n ? p->v + p->s : p->t;
}

uint8_t bk_chime(const __code note_t *s)
{
    uint16_t t, e;
    uint8_t wait = s->wait, v, op;
    __bit amp = AMP_ON;                     // on already if the squelch is open
    if (st == BK_TX) return 0;
    bk_spk(1);
    xset(&vc, 0, sizeof vc);
    t = now();
    for (;;) {
        for (; !wait && s->op != N_END; wait = (++s)->wait) {
            if ((s->op & 0x7F) == N_NOP) continue;
            ramp(vc.r + (uint8_t)((s->op >> 6 & 2) | (s->op & N_HZ)), s->op & N_HZ ? s->to << 3 : s->to << 8, s->n);
        }
        if (!wait) break;                   // N_END's ticks have run
        pitch(0); pitch(1);
        v = vc.r[0].v >> 8; op = vc.r[2].v >> 8;   // the levels: REG_70 bit 15 / 7 is that tone on
        bk_write(0x70, (uint16_t)(v ? v | 0x80 : 0) << 8 | (op ? op | 0x80 : 0));
        while (!(e = now() - t)) WDT_KICK();
        for (t += e; e && wait; e--, wait--) tick();   // every tick that passed, however slow the loop ran
        leds();                             // the loop is blocked: keep an alert's amber blink on time
        if (!wait && s->op == N_END) break;
    }
    bk_spk(0);
    AMP_ON = amp;
    return 1;
}

// ---- DTMF while transmitting: the two tone generators to the air, the mic muted ----
// Digit d: 0-9, 10-13 A-D, 14 *, 15 #, as stock stores its DTMF codes; over 15 stops. Row and
// column tones are the standard's (697-941 Hz, 1209-1633 Hz); the column tone a little louder.
static const __code uint16_t dtmf_w[8] = {TONE_HZ(697), TONE_HZ(770), TONE_HZ(852), TONE_HZ(941),
    TONE_HZ(1209), TONE_HZ(1336), TONE_HZ(1477), TONE_HZ(1633)};
static const __code uint8_t dtmf_rc[16] = {0x35, 0x04, 0x05, 0x06, 0x14, 0x15, 0x16, 0x24, 0x25, 0x26,
    0x07, 0x17, 0x27, 0x37, 0x34, 0x36};    // row << 4 | 4 + column
void bk_dtmf_tx(uint8_t d) __reentrant
{
    uint16_t on = 0, mic = 0x3B20;
    if (d < 16) {
        d = dtmf_rc[d];
        bk_write(0x71, dtmf_w[d >> 4]);
        bk_write(0x72, dtmf_w[d & 7]);
        on = 0xD0D8; mic = 0xBB20;          // tone 1 on at gain 0x50, tone 2 at 0x58; the mic muted
    }
    bk_write(0x50, mic);
    bk_write(0x70, on);
}

// ---- scope: one RSSI sample at f, receiver already on ----
int16_t bk_dbm_at(uint32_t f) __reentrant
{
    bk_freq(f);
    bk_table(vco_tab, sizeof vco_tab);      // retrigger VCO calibration, then RX back on
    for (uint8_t i = 0; i < 50 && (bk_read(0x63) & 0xFF) >= 200; i++) ;   // settled when the glitch count is under 200
    bk_read(0x67);                          // the first RSSI read can still be moving
    return bk_dbm();                        // (fagci's GetRssi, Apache-2.0: top of file)
}
