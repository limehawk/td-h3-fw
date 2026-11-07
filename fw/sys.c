// Clock-independent basics: ports, 5 ms tick, watchdog, PWM, ADC, beep, UART1.
// Every register value here mirrors stock firmware 250317 (see docs/HARDWARE.md).
#include "fw.h"

volatile uint16_t ticks;
volatile uint8_t beep_left;                 // 5 ms units
static volatile uint8_t beep_th, beep_tl;

// Timer clocks are Fsys/12 = 4 MHz. 20000 counts = 5 ms.
void t0_isr(void) __interrupt(1)
{
    TH0 = 0xB1; TL0 = 0xE0;
    ticks++;
    if (beep_left && !--beep_left) {
        TCON &= ~0x40;                      // TR1 off
        BEEP = 0;
    }
}

void t1_isr(void) __interrupt(3)
{
    TH1 = beep_th; TL1 = beep_tl;
    BEEP = !BEEP;
}

// memcpy and memset for XDATA: no generic pointers or library routines
void xcpy(__xdata void *d, const __xdata void *s, uint8_t n) __reentrant
{
    __xdata uint8_t *a = d;
    const __xdata uint8_t *b = s;
    while (n--) *a++ = *b++;
}

void xset(__xdata void *d, uint8_t c, uint8_t n) __reentrant
{
    __xdata uint8_t *a = d;
    while (n--) *a++ = c;
}

uint16_t now(void)
{
    uint16_t t;
    __bit ea = EA;
    EA = 0; t = ticks; EA = ea;
    return t;
}

void delay_ms(uint16_t ms) __reentrant
{
    uint16_t start = now(), n = (ms + 4) / 5;
    while ((uint16_t)(now() - start) < n) {
        WDT_KICK();
        // IDLE (PCON bit 0): the CPU stops until an interrupt, the 5 ms tick at the latest; timers,
        // PWM and the UARTs run on. 6 mA -> 4 mA at 48 MHz (CMS8S69xx datasheet). Never with EA off:
        // nothing would wake it. The NOPs: the maker's IDLE() macro, not the bare bit, is advised.
        if (EA) { PCON |= 0x01; __asm__("nop"); __asm__("nop"); }
    }
}

void udelay(void)
{
    volatile uint8_t i = 6;
    while (--i) ;
}

static void pwm_duty(uint8_t bit, uint8_t duty) __reentrant
{
    PWMCNTE &= ~bit;
    if (bit == 1) PWMD0L = duty; else if (bit == 2) PWMD1L = duty; else PWMD5L = duty;
    PWMCNTE |= bit;
}
void backlight(uint8_t duty) __reentrant { pwm_duty(1, duty); }
void apc(uint8_t duty) __reentrant { pwm_duty(2, duty); }
void torch_pwm(uint8_t duty) __reentrant { pwm_duty(0x20, duty); }

void beep(uint16_t r, uint8_t n) __reentrant   // BEEP (fw.h) works out r and n when it compiles
{
    beep_th = r >> 8; beep_tl = r;
    AMP_ON = 1;
    EA = 0; beep_left = n; EA = 1;
    TH1 = beep_th; TL1 = beep_tl;
    TCON |= 0x40;                           // TR1
}

// Voice chip U4 (stock 0xED16 then 0xDE41): clock P4.2, data P3.6, shared with the RDA5807,
// whose own clock stays still meanwhile. Start: both low 20 ms, clock high 10 ms. Then the prompt
// byte MSB first, data set while the clock is low, taken as it rises. Stock adds 0x50 for its
// English prompts. The chip plays the prompt by itself; the speaker amp must be on.
static void vwait(void) { for (uint8_t i = 80; i; i--) udelay(); }   // stock 0xEFED
void voice(uint8_t n) __reentrant
{
    VOICE_SCK = 0; FM_SDA = 0; delay_ms(20);
    VOICE_SCK = 1; delay_ms(10);
    for (uint8_t i = 8; i; i--, n <<= 1) {
        VOICE_SCK = 0; vwait();
        FM_SDA = n >> 7; vwait();
        VOICE_SCK = 1; vwait();
    }
    VOICE_SCK = 0; FM_SDA = 0;
}

// P3.4 = AIN8. Divider and reference are not on the schematic page we read;
// 2058 uV/count makes stock's bar thresholds (raw 3106/3492/3789) land at 6.4/7.2/7.8 V.
// ponytail: MENU -> BATT CAL trims it per radio; for another board, measure the pack with a meter and adjust BATT_UV_PER_COUNT.
#define BATT_UV_PER_COUNT 2058UL
uint16_t batt_mv(void)
{
    uint8_t t = 200;
    ADCON0 |= 0x02;                         // GO
    while ((ADCON0 & 0x02) && --t) udelay();
    return (((uint16_t)ADRESH << 4 | ADRESL >> 4) * BATT_UV_PER_COUNT) / 1000;
}

// UART1: P1.1/P1.3, USB-C CH340N, 38400 (115200 while REMOTE is on). UART0: P2.0/P2.1, Bluetooth module, 9600.
// Both from stock 0xDBAD / 0xDE80.
#define BRT2CON XREG(0xF5C4)
#define BRT2DL  XREG(0xF5C5)
#define BRT2DH  XREG(0xF5C6)
#define P20CFG  XREG(0xF020)
#define P21CFG  XREG(0xF021)
__sfr __at(0x98) SCON0;
__sfr __at(0x99) SBUF0;

void uart_init(void)
{
    SCON1 = (SCON1 & 0x3F) | 0x40;
    FUNCCR = (FUNCCR & 0x8F) | 0x30;
    SCON1 |= 0x10;
    BRTCON &= 0xF8;
    PCON |= 0x40;
    BRTDL = 0xB2; BRTDH = 0xFF;             // 65536-78 -> 38400 at 48 MHz
    BRTCON |= 0x80;
    PS_RXD1 = 0x11;
    P11CFG = 2; P13CFG = 2;

    SCON0 = (SCON0 & 0x3F) | 0x40;
    FUNCCR = (FUNCCR & 0xF8) | 0x04;
    SCON0 |= 0x10;
    BRT2CON &= 0xF8;
    PCON |= 0x80;
    BRT2DL = 0xC8; BRT2DH = 0xFE;           // 65536-312 -> 9600
    BRT2CON |= 0x80;
    P20CFG = 2; P21CFG = 2;
}

// Fsys/16/(65536-reload): 26 gives 115385 (+0.16%), 78 gives 38462 (+0.16%). PC LINK stays at
// 38400, the clone protocol's speed (CHIRP, tdh3 eeprom/channels).
void uart_usb_fast(uint8_t fast) __reentrant
{
    BRTCON &= 0x7F;
    BRTDL = fast ? 0xE6 : 0xB2;             // BRTDH stays 0xFF
    BRTCON |= 0x80;
}

int16_t uart_get(uint8_t port) __reentrant  // read SBUF before clearing RI, or a byte landing in between overwrites it
{
    uint8_t c;
    if (port) {
        if (!(SCON1 & 0x01)) return -1;
        c = SBUF1;
        SCON1 &= ~0x01;
        return c;
    }
    if (!(SCON0 & 0x01)) return -1;
    c = SBUF0;
    SCON0 &= ~0x01;
    return c;
}

void uart_put(uint8_t port, uint8_t c) __reentrant
{
    uint16_t t = 60000;                     // > one byte at 9600 baud
    if (port) {
        SBUF1 = c;
        while (!(SCON1 & 0x02) && --t) ;
        SCON1 &= ~0x02;
    } else {
        SBUF0 = c;
        while (!(SCON0 & 0x02) && --t) ;
        SCON0 &= ~0x02;
    }
}

void sys_init(void)
{
    EA = 0;
    // Ports exactly as stock: RF paths off (active-low high), amp off, beep low.
    P0TRIS = 0xFF; P1TRIS = 0xDD; P2TRIS = 0xC6; P3TRIS = 0xEF; P4TRIS = 0xFE; P5TRIS = 0xFD;
    P34CFG = 1;                             // battery sense is analog
    P0UP = 0x0F; P2UP = 0xC0;               // keypad rows (keys.c); stock leaves them off
    P0 = 0x0F; P1 = 0x1A; P2 = 0x40; P3 = 0x00; P4 = 0xB1; P5 = 0x20;  // P1.4 high: font ROM deselected

    CKCON = (CKCON & 0x1F) | 0xE0;          // longest watchdog period
    TA = 0xAA; TA = 0x55; WDCON |= 0x02;
    WDT_KICK();

    CKCON &= ~0x18;                         // T0, T1 at Fsys/12
    TMOD = 0x11;                            // both 16-bit
    TH0 = 0xB1; TL0 = 0xE0;
    TCON |= 0x10;                           // TR0
    IE |= 0x0A;                             // ET0, ET1

    // PWM1 -> P4.1 APC, PWM0 -> P2.2 backlight; period 200.
    P41CFG = 5; PWMCON = 0; PWMOE |= 2; PWM01PSC = 5; PWMLOADEN |= 2; PWMPINV &= ~2;
    PWM1DIV = 0; PWMCNTM |= 2; PWMP1H = 0; PWMP1L = 200; PWMD1H = 0; PWMD1L = 0; PWMCNTE |= 2;
    P22CFG = 5; PWMCON = 0; PWMOE |= 1; PWM01PSC = 1; PWMLOADEN |= 1; PWMPINV &= ~1;
    PWM0DIV = 0; PWMCNTM |= 1; PWMP0H = 0; PWMP0L = 200; PWMD0H = 0; PWMD0L = 0; PWMCNTE |= 1;

    // PWM5 -> P3.5 flashlight, so it can dim. Code 5 is what stock writes for its PWM pins; on this
    // chip it put P3.5 on PG5 (a probe build cycled PWM2-5: only PG5 lit it). The CMS8S6990C
    // manual's PG2 code 0x14 left the LED dark.
    P35CFG = 5; PWMOE |= 0x20; PWM45PSC = 1; PWMLOADEN |= 0x20; PWMPINV &= ~0x20;
    PWM5DIV = 0; PWMCNTM |= 0x20; PWMP5H = 0; PWMP5L = 200; PWMD5H = 0; PWMD5L = 0; PWMCNTE |= 0x20;

    ADCON0 = 0;
    ADCON1 = 0x08 | 0x60 | 0x80;            // AIN8, clock, enable
    ADCLDO = 0xA0;

    uart_init();
    EA = 1;
}
