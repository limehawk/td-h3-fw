// One header for the whole firmware. Small radio, small program.
#ifndef FW_H
#define FW_H

#include <stdint.h>
#include "hw.h"

// sys.c
// SDCC emits an interrupt vector only when the ISR prototype is visible in main's file.
void t0_isr(void) __interrupt(1);
void t1_isr(void) __interrupt(3);
extern volatile uint16_t ticks;            // 5 ms
void sys_init(void);
uint16_t now(void);                        // ticks, read safely
void xcpy(__xdata void *d, const __xdata void *s, uint8_t n) __reentrant;   // memcpy, XDATA only
void xset(__xdata void *d, uint8_t c, uint8_t n) __reentrant;               // memset, XDATA only
void delay_ms(uint16_t ms) __reentrant;
void udelay(void);                         // ~1 us, for bit-banged buses
void backlight(uint8_t duty) __reentrant;                // 0..200
void torch_pwm(uint8_t duty) __reentrant;                // flashlight brightness, 0-200 (PWM5 on P3.5)
void apc(uint8_t duty) __reentrant;                    // TX PA bias, 0..200
void beep(uint16_t r, uint8_t n) __reentrant;   // Timer1 reload and 5 ms ticks: use BEEP
#define BEEP(hz, ms) beep(65536UL - 2000000UL / (hz), ((ms) + 4) / 5)   // toggle at 2*hz
void voice(uint8_t n) __reentrant;                     // voice chip U4: play prompt n
extern volatile uint8_t beep_left;         // 5 ms units, nonzero while beeping
uint16_t batt_mv(void);
void uart_init(void);
#define PORT_BT  0                         // UART0, Bluetooth, 9600
#define PORT_USB 1                         // UART1, USB-C, 115200 with REMOTE on, else 38400
int16_t uart_get(uint8_t port) __reentrant; // -1 if nothing
void uart_put(uint8_t port, uint8_t c) __reentrant;
void uart_usb_fast(uint8_t fast) __reentrant;   // USB-C at 115200 (REMOTE) or 38400 (clone protocol)

// lcd.c (128x128, RGB565)
#define W 128
#define H 128
// Screens are laid out for exactly 128 x 128: the bottom line's 7 glyph rows and blank row end on
// the panel's last row, and the lines above it step up from there (BOT_Y - 12, - 24).
#define BOT_Y (H - 8)
// The panel is BGR (seen on the radio 2026-11-03: amber showed blue). Packed here, not via
// MADCTL, so the init sequence stays byte-identical to stock's.
// Each part is cast: SDCC 4.5 folded RGB(42, 24, 0) to a signed char and stored 0xFFC5 (seen on the radio).
#define RGB(r, g, b) ((uint16_t)((uint16_t)((b) & 0xF8) << 8 | (uint16_t)((g) & 0xFC) << 3 | (uint16_t)((r) >> 3)))
#define BLACK  0x0000
#define WHITE  0xFFFF
#define GREY   RGB(120, 120, 120)
#define AMBER  RGB(255, 176, 0)
#define GREEN  RGB(0, 220, 80)
#define RED    RGB(240, 40, 40)
#define CYAN   RGB(0, 200, 230)
void lcd_init(void) __reentrant;
void lcd_fill(uint8_t x, uint8_t y, uint8_t w, uint8_t h, uint16_t c) __reentrant;
void lcd_fillp(uint8_t x, uint8_t y, uint8_t w, uint8_t h, uint8_t i) __reentrant;   // lcd_fill in palette colour i (P_*)
void lcd_clear(void);
void lcd_on(uint8_t on) __reentrant;                   // display on/off (AUTO OFF)
// Draws text at scale s (1 = 6x8 cell). Returns x after the text.
extern __xdata uint8_t text_pad;
extern const __code uint16_t menu_help_bits[];   // help.c, made by tools/mkhelp.py
uint8_t lcd_text_bg(uint8_t x, uint8_t y, const char *s, uint8_t scale, uint16_t fg, uint16_t bg);
uint8_t lcd_text(uint8_t x, uint8_t y, const char *s, uint8_t scale, uint8_t fg) __reentrant;   // fg: a P_* palette index
void lcd_icon(uint8_t x, uint8_t y, const __code uint8_t *icon, uint8_t fi) __reentrant;   // fi: a P_* index; icon: width, height, rows (MSB left)
extern const __code uint8_t icon_lock[], icon_bt[], icon_warn[], icon_note[], icon_pulse[], icon_target[],
    icon_link[], icon_bell[];                   // glyphs.c: Open Iconic, 8x8

// remote.c (MENU -> REMOTE): screen mirror and key presses over USB-C, never PTT
extern uint8_t remote, rkey;                // on; key the computer holds
extern __data uint8_t remote_req;           // REQ_* for the main loop
extern uint8_t mirror;                      // a computer spoke in the last 10 s: copy draws to it
void remote_tick(void);                     // main loop: lets the mirror lapse when nobody's watching
#define REQ_REDRAW 1
#define REQ_STATUS 2
#define REQ_VOICE 4                         // play remote_voice on U4
#define REQ_END 8                           // a redraw for REMOTE is due: send 'E' once it is drawn
extern uint8_t remote_voice;
void remote_fill(uint8_t x, uint8_t y, uint8_t w, uint8_t h, uint16_t c) __reentrant;
void remote_text(uint8_t x, uint8_t y, const char *s, uint8_t scale, uint16_t fg, uint16_t bg) __reentrant;
void remote_puts(const __xdata char *s) __reentrant;
void remote_end(void);                     // 'E': the screen REMOTE asked for is complete
void remote_poll(void) __reentrant;
extern __bit loader_armed;                  // 'B' came: PTT down calls remote_loader
void remote_loader(void);                   // restart into the vendor loader (flash mode while PTT is held)

// eeprom.c (AT24C64)
uint8_t ee_read(uint16_t addr, __xdata uint8_t *buf, uint8_t n);   // 1 = ok
uint8_t ee_write(uint16_t addr, const __xdata uint8_t *buf, uint8_t n);

// bk4819.c
void bk_write(uint8_t reg, uint16_t v) __reentrant;
uint16_t bk_read(uint8_t reg) __reentrant;
void bk_init(void);
void bk_freq(uint32_t f) __reentrant;                  // 10 Hz units
void bk_rx_on(void);
void bk_tx_on(void);
void bk_mode(uint8_t mode, uint8_t narrow) __reentrant;   // MOD_*
void bk_gain(uint16_t r13) __reentrant;    // write REG_13, ~10 dB less LNA gain when bk_att
extern uint8_t bk_att;
extern uint8_t bk_opt;                      // settings byte 0x1FC9, as main.c's cfg has it:
#define BKO_CRX 1                           // COMPANDER on receive
#define BKO_CTX 2                           // and on transmit
#define BKO_NOTAIL 4                        // TAIL TONE OFF: no 55 Hz / DCS tail on unkey
#define BKO_REVERSE 8                       // TAIL TONE REVERSE: a CTCSS channel's tail is the tone, phase reversed
#define BKO_NAR6 16                         // NARROW kHz 6.25: narrow FM narrower still
void bk_af(uint8_t on) __reentrant;
// Tones: 0 none; CTCSS in 0.1 Hz; DCS = TONE_DCS | [TONE_INV] | 9-bit octal code (023 = 0x13).
#define TONE_DCS 0x8000
#define TONE_INV 0x4000
#define TONE_1050 2625                     // 262.5 Hz CTC1 word = NOAA 1050 Hz alert detect
void bk_tone(uint16_t t) __reentrant;
uint32_t bk_golay(uint16_t code) __reentrant;
uint16_t bk_ctcss_measured(void);          // 0.1 Hz, 0 if none
uint32_t bk_dcs_measured(void);            // 23-bit word, 0 if none
void bk_fscan(uint8_t on) __reentrant;
uint32_t bk_fscan_result(void);
// A sound for bk_chime: wait 5 ms ticks after the previous step, then move voice 1 (or 2 with
// N_V2) to `to` in n ticks, 0 = at once: N_GAIN its level (0-127) or N_HZ its pitch (Hz).
// N_END plays on for its wait, then stops. tools/tones.py makes these from sound files.
typedef struct { uint8_t wait, op; uint16_t to; uint8_t n; } note_t;
#define N_GAIN 0
#define N_HZ 1
#define N_NOP 0x7E                          // only waits (a gap longer than 255 ticks)
#define N_END 0x7F
#define N_V2 0x80
uint8_t bk_chime(const __code note_t *s);    // blocks while it plays; 0 = not played (on the air)
void leds(void) __reentrant;                 // main.c: the status LEDs, also run by bk_chime while it blocks
void chime_no(void);                         // bk_chime(snd_no)
void chime_ok(void);                         // bk_chime(snd_ok)
extern const __code note_t snd_hello[], snd_click[], snd_ok[], snd_no[], snd_batt[], snd_alert[], snd_bell[];   // tones.c
uint16_t bk_mic(void);
int16_t bk_dbm_at(uint32_t f) __reentrant;
uint8_t bk_tone_heard(uint16_t t) __reentrant;
int16_t bk_dbm(void);
void bk_squelch(uint8_t level, uint8_t vhf, uint8_t narrow) __reentrant;  // level 0-9
uint8_t bk_sql_open(void);                 // REG_0C bit 1
uint8_t bk_sql_near(void);                 // a carrier at or over the squelch's open level (RSSI and noise): worth waiting for
void bk_dtmf_on(uint8_t tail) __reentrant;              // DTMF decoder on (with RX); tail: the 55 Hz tail detector too
#define BK_IRQ_DTMF 0x0800                  // REG_02: a DTMF digit decoded
#define BK_IRQ_TAIL 0x0400                  // REG_02: a 55 Hz squelch tail heard
#define BK_IRQ_VOXF 0x0020                  // REG_02: VOX found a voice
#define BK_IRQ_VOXL 0x0010                  // and lost it
#define BK_IRQ_VOX 0x0030
extern uint16_t bk_vox;                     // VOX: the level that finds a voice (REG_46), 0 = off
uint16_t bk_irq(void);                      // the interrupts since the last call, or 0
char bk_dtmf(void);
extern const __code char dtmf_chars[];     // "0123456789ABCD*#": DTMF code n's character                         // the digit, after BK_IRQ_DTMF
void bk_dtmf_tx(uint8_t d) __reentrant;   // while transmitting: DTMF digit d (0-9, A-D 10-13, * 14, # 15) on, over 15 off
uint8_t bk_am_gain(int16_t dbm) __reentrant;   // AM: gain off in steps on strong signals; returns the dB off
void bk_sleep(void);

// keys.c
enum { K_NONE, K_0, K_1, K_2, K_3, K_4, K_5, K_6, K_7, K_8, K_9,
       K_MENU, K_UP, K_DOWN, K_EXIT, K_STAR, K_HASH, K_PTT, K_SIDE1, K_SIDE2 };
uint8_t key_scan(void);                    // key held right now
#define K_LONG 0x80
uint8_t key_event(void) __reentrant;                   // short press (on release), k|K_LONG at 0.8 s, or K_NONE
extern uint8_t key_held;
extern uint8_t ptt_key;                    // keys.c: the keypad key held with PTT or PTT-B alone (DTMF), else K_NONE
extern uint8_t ptt_key;                    // the keypad key held with PTT or PTT-B (DTMF), else K_NONE
extern uint8_t ptt_ok;                     // keys.c: PTT seen up since power-on

// radio.c
typedef struct {
    uint32_t rx, tx;      // 10 Hz units
    uint16_t rx_tone, tx_tone;  // see TONE_DCS
    uint8_t mode, narrow, power;  // mode: MOD_*; power: PWR_ULP/LOW/MID/HIGH
    uint8_t groups;             // scan groups 1-8 as bits 0-7
    uint8_t bcl;                // busy channel lockout: no PTT while the squelch is open
    char name[9];
} chan_t;
extern __xdata chan_t ch;
enum { MOD_FM, MOD_AM, MOD_USB };           // receive only but FM; a record's bits 14.2 (1) and 13.3 (2), 0 = by band
#define GRP_OURS 1                         // record byte 13 bits 1:0 (stock and CHIRP leave them 11 or 00): byte 15 is ours
extern uint8_t sql_level;
extern uint8_t wx_watch;                   // weather mode with WX ALERT on: radio_tune arms the 1050 Hz alert detector
extern uint8_t mic_gain;                   // 0-9 into stock's mic table
extern int8_t freq_cal;                    // 0.1 ppm
enum { PWR_ULP, PWR_LOW, PWR_MID, PWR_HIGH };   // lowest to highest; the record's bits differ (radio.c)
uint8_t tx_allowed(uint32_t f) __reentrant;
extern uint8_t tx_bands;                   // TX BANDS: which services PTT works on
#define TXB_HAM 1
#define TXB_GMRS 2
#define TXB_MURS 4
#define TXB_FRS 8
uint8_t chan_load(uint8_t n);              // 1..199 from stock EEPROM layout, 1 = ok
uint8_t chan_next(uint8_t n, int8_t dir, uint8_t inlist, uint8_t groups) __reentrant;  // inlist: scan list only; groups: bit g-1 = group g
uint8_t chan_in_scan(uint8_t n) __reentrant;
uint8_t chan_used(uint8_t n) __reentrant;               // 1 = channel n is in use
extern __bit maps_ok;                      // radio.c's channel bitmaps are current (ee_write clears it)
void chan_scan_toggle(uint8_t n) __reentrant;
uint8_t chan_free(void);
uint8_t chan_save(uint8_t n);              // write `ch` as channel n
uint8_t chan_delete(uint8_t n);            // empty channel n (CHIRP's way)
void radio_tune(void);                     // apply ch to BK4819 and RX path
uint8_t rx_settled(void);                  // the receiver has settled since radio_tune: RSSI and the squelch read true
uint8_t radio_tx(uint8_t on) __reentrant;              // 1 = transmitting
void radio_off(void);
void radio_init(void);                     // loads factory TX calibration
void radio_rx_path(uint8_t uhf) __reentrant;
uint32_t bcd32(const __xdata uint8_t *b) __reentrant;   // 4 bytes little-endian BCD
void bcd_put(__xdata uint8_t *b, uint32_t v, uint8_t n) __reentrant;

// scan.c
// One buffer for the full-screen modes, which never run together: the scope's bars, PC LINK's
// block, the FM screen's presets, text and auto-preset scores (fm.c, the largest). Outside them radio.c keeps its channel bitmaps (0-49) and record
// (50-73) here, so each mode clears maps_ok as it starts. None of them loads or saves a channel.
#define SCR_BUF 180
extern __xdata uint8_t scr_buf[SCR_BUF];
uint32_t scope(uint32_t center, uint16_t step) __reentrant;   // spectrum scope, returns the picked frequency or 0
uint32_t freq_find(void) __reentrant;                 // frequency counter, returns what it heard or 0

// main.c: theme colours (fm.c draws with them too)
extern __xdata uint16_t pal[8];
#define c_bg pal[0]
#define c_fg pal[1]
#define c_dim pal[2]
#define c_acc pal[3]
#define c_ok pal[4]
#define c_hi pal[5]
#define c_bar pal[6]
enum { P_BG, P_FG, P_DIM, P_ACC, P_OK, P_HI, P_BAR, P_RED };
typedef struct { uint8_t x, y, scale, col; const __code char *s; } label_t;   // fixed text on a screen
void draw_labels(const __code label_t *l, uint8_t n) __reentrant;

uint8_t lights(uint8_t k) __reentrant;     // main.c: backlight timeout, flashlight; k, or 0 if it only lit the screen
uint32_t wx_freq(uint8_t n) __reentrant;    // main.c: NOAA weather channel n (0-6), 10 Hz
__xdata char *dec(__xdata char *p, uint32_t v, uint8_t w, uint8_t frac) __reentrant;   // main.c: fixed-point decimal

// clone.c
void clone_session(uint8_t bt) __reentrant;            // PC LINK, returns on EXIT; bt: also listen on Bluetooth

// fm.c
void fm_off(void);                         // RDA5807 powered down (REG02 = 0000)
uint8_t fm_mode(__xdata uint16_t *khz100, uint8_t wx);    // FM broadcast screen, returns on EXIT or PTT, or 1 for
                                           // a weather alert heard on channel wx (1-7, 0 = off)

#endif
