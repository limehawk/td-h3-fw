// Remote control over USB-C, on while MENU -> REMOTE is ON. Every LCD draw is copied to the
// port and the computer may hold a key (tools/tdh3 remote). PTT and PTT-B are refused, so the
// radio never transmits from here. PC LINK switches it off while it owns the port. 1 Mbaud 8N1
// while on (115200 while armed by 'B'), 38400 (the clone protocol's speed) while off: key_scan
// switches it.
//
//   radio -> computer   'f' x y w h c16             fill
//                       't' x y scale fg16 bg16 text NUL
//                                                   scale 255 (WORDS): the words alone, no pixels: the
//                                                   fills around it draw them (DIGITS ARCADE and LCD)
//                       'S' A5 5A                    sync: answers 'S', then the screen redraws
//                       'q' text '\n'               status: answers 'Q' (main loop only); ends in the version
//                       'E'                          the redraw after 'S' is complete (home and menu)
//   computer -> radio   0x80 | code                  hold key code (fw.h K_*), again every 100 ms while
//                                                     held; 0x80 lets go, and so do 300 ms of nothing
//                       'S'  'Q'
//                       'V' n                       play voice chip prompt n: answers 'q' "VOICE n"
//                       'B'                         arm the flash restart: answers 'S' A5 5A, then 115200; the next PTT press
//                                                    restarts into the vendor loader (tdh3 flash), 10 s of
//                                                    nothing from the computer disarms it
// One byte per command. uart1_isr keeps up to 16 that land while the loop is busy; this reads at
// most one key byte a pass (every ~10 ms), so each hold and release is seen in turn.
#include "fw.h"

uint8_t remote, rkey, mirror, remote_voice;
__data uint8_t remote_req;                  // internal RAM: read on every main loop pass
static uint8_t want_voice;                  // 'V' came: the next byte is the prompt
static uint8_t rprev;                       // the last key byte read: a hold's code; 0 a release or an 'S'
static uint16_t t_heard, t_poll;
__bit loader_armed;                         // 'B' came: key_scan restarts into the loader on PTT

// Mirroring costs every draw a trip down the wire, which slows busy screens such as the scope. So
// the screen is copied only while a computer is talking: any byte from it opens a 10 s window.
void remote_tick(void)
{
    uint16_t quiet = now() - t_heard;       // ticks since the computer last spoke
    if (mirror && (!remote || quiet > 2000)) mirror = loader_armed = 0;
    // A held key lets go by itself after 300 ms without a byte (tdh3 repeats the hold every 100 ms):
    // a release byte lost while the radio was busy held MENU into a long press, "SAVE TO CH 3".
    // Never before key_event has registered it: a hold read late, then a slow pass (a mirrored draw),
    // was let go unseen and the press lost. After 1 s of silence it lets go regardless: a loop that
    // reads key_scan alone (FM's auto-preset stop) never registers it, and waited on it for good.
    if (rkey && quiet > 60 && (key_held == rkey || quiet > 200)) rkey = K_NONE;
}

static void put(uint8_t c) __reentrant { uart_put(PORT_USB, c); }
static void put16(uint16_t v) __reentrant { put(v >> 8); put(v); }

void remote_fill(uint8_t x, uint8_t y, uint8_t w, uint8_t h, uint16_t c) __reentrant
{
    put('f'); put(x); put(y); put(w); put(h); put16(c);
}

void remote_text(uint8_t x, uint8_t y, const char *s, uint8_t scale, uint16_t fg, uint16_t bg) __reentrant
{
    put('t'); put(x); put(y); put(scale); put16(fg); put16(bg);
    do put(*s); while (*s++);
}

void remote_end(void) { put('E'); }

void remote_puts(const __xdata char *s) __reentrant
{
    put('q');
    while (*s) put(*s++);
    put('\n');
}

void remote_poll(void) __reentrant          // from key_scan, so every screen hears the computer
{
    int16_t r;
    uint8_t c;
    uint16_t t = now(), was = t_poll;
    t_poll = t;
    // A byte is dated from the poll before it, not now: UART1 keeps the first byte and drops the
    // rest, so after a slow redraw (a mirrored menu, 200 ms and more) the one waiting is tdh3's
    // second hold and both releases are gone. Dated now, that old hold held UP past the 0.5 s
    // repeat and THEME stepped twice. No older than 50 ticks, so a first hold read late (tdh3's 'S'
    // redraw was still going) is held 50 ms, a short press: at 64, past remote_tick's 60, it was let go
    // unseen and "15" opened 05.
    // The UART keeps the first byte to land, so a hold read right after a hold of the same key is that
    // press's repeat, however late (tdh3 repeats every 100 ms): once let go it is dropped, not a new
    // press ("15" became 1, 5, 5). A new press comes after a release or tdh3's 'S', so a second V/M
    // from the next tdh3 call still counts (v0.47.3 dropped it: rgone outlived the press).
    if ((uint16_t)(t - was) > 50) was = t - 50;
    // Nothing is read while a held key isn't yet registered: after a slow pass the hold and its release
    // wait together in uart1_isr's buffer, and read in one go the press would be let go unseen.
    // remote_tick's 1 s let-go still ends a hold key_event never registers.
    while (!(rkey && key_held != rkey) && (r = uart_get(PORT_USB)) >= 0) {
        c = r;
        mirror = 1;
        t_heard = was;
        if (want_voice) { want_voice = 0; remote_voice = c; remote_req |= REQ_VOICE; }
        else if (c == 'V') want_voice = 1;
        else if (c & 0x80) {
            c &= 0x7F;
            if (!(c && c == rprev && !rkey)) rkey = c >= K_PTT && c != K_SIDE2 ? K_NONE : c;
            rprev = c;
            break;                          // one key byte a pass: a hold and its release read together never pressed
        }
        else if (c == 'B') {                // answered at 1 Mbaud, then the loader's speed: no redraw
            loader_armed = 1;
            put('S'); put(0xA5); put(0x5A);
            delay_ms(1);                    // the last stop bit out before the speed changes
            uart_usb_fast(USB_LOADER);
        }
        else if (c == 'S') { sync: rprev = 0; put('S'); put(0xA5); put(0x5A); remote_req |= REQ_REDRAW; }
        else if (c == 'Q') remote_req |= REQ_STATUS;
    }
}

// The vendor loader's own restart (its 0x0477) with 0x55 where it has 0xAA: BOOTCON (XSFR 0xF691)
// picks the loader, then a software reset. The loader reads PTT and stays in flash mode while it's
// held, so this runs the moment PTT goes down, before the radio keys up: held first, it would
// transmit. 20 ms first for the contact to settle; EA off so no interrupt splits a timed access.
void remote_loader(void)
{
    delay_ms(20);
    EA = 0;
    TA = 0xAA; TA = 0x55; WDCON = 0;
    *(volatile __xdata uint8_t *)0xF691 = 0x55;
    TA = 0xAA; TA = 0x55; WDCON = 0x80;
}
