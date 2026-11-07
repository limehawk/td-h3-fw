// TD-H3 hardware: CMS8S6990-family MCU (marked TA3782F), 48 MHz, 1T 8051.
// Register addresses: Cmsemicon cms8s6990.h. P4/P5 and their TRIS (0xC0/0xD8/0xA4/0xA5)
// and every init value below are taken from what stock firmware 250317 does on this board.
// Sources for each pin: docs/HARDWARE.md.
#ifndef HW_H
#define HW_H

#include <stdint.h>

__sfr __at(0x80) P0;
__sfr __at(0x87) PCON;
__sfr __at(0x88) TCON;
__sfr __at(0x89) TMOD;
__sfr __at(0x8A) TL0;
__sfr __at(0x8B) TL1;
__sfr __at(0x8C) TH0;
__sfr __at(0x8D) TH1;
__sfr __at(0x8E) CKCON;
__sfr __at(0x90) P1;
__sfr __at(0x91) FUNCCR;
__sfr __at(0x96) TA;
__sfr __at(0x97) WDCON;
__sfr __at(0x9A) P0TRIS;
__sfr __at(0xA0) P2;
__sfr __at(0xA1) P1TRIS;
__sfr __at(0xA2) P2TRIS;
__sfr __at(0xA3) P3TRIS;
__sfr __at(0xA4) P4TRIS;
__sfr __at(0xA5) P5TRIS;
__sfr __at(0xA8) IE;
__sfr __at(0xB0) P3;
__sfr __at(0xC0) P4;
__sfr __at(0xD8) P5;
__sfr __at(0xDC) ADRESL;
__sfr __at(0xDD) ADRESH;
__sfr __at(0xDE) ADCON1;
__sfr __at(0xDF) ADCON0;
__sfr __at(0xEA) SCON1;
__sfr __at(0xEB) SBUF1;
__sfr __at(0xEC) SPCR;
__sfr __at(0xED) SPSR;
__sfr __at(0xEE) SPDR;
// 0xFB..0xFF is the flash controller (MLOCK/MADRx/MDATA/MCTRL). Never touch it.

#define XREG(a) (*(volatile __xdata uint8_t *)(a))
#define P04CFG    XREG(0xF004)
#define P0UP      XREG(0xF00A)  // pull-ups, 1 = on (keypad rows while PTT is down; stock never sets them)
#define P05CFG    XREG(0xF005)
#define P11CFG    XREG(0xF011)
#define P13CFG    XREG(0xF013)
#define P22CFG    XREG(0xF022)
#define P2UP      XREG(0xF02A)
#define P34CFG    XREG(0xF034)
#define P35CFG    XREG(0xF035)
#define P41CFG    XREG(0xF041)
#define PWMCON    XREG(0xF120)
#define PWMOE     XREG(0xF121)
#define PWMPINV   XREG(0xF122)
#define PWM01PSC  XREG(0xF123)
#define PWM45PSC  XREG(0xF125)
#define PWMCNTE   XREG(0xF126)
#define PWMCNTM   XREG(0xF127)
#define PWMLOADEN XREG(0xF129)
#define PWM0DIV   XREG(0xF12A)
#define PWM1DIV   XREG(0xF12B)
#define PWM5DIV   XREG(0xF12F)
#define PWMP0L    XREG(0xF130)
#define PWMP0H    XREG(0xF131)
#define PWMP1L    XREG(0xF132)
#define PWMP1H    XREG(0xF133)
#define PWMP5L    XREG(0xF13A)
#define PWMP5H    XREG(0xF13B)
#define PWMD0L    XREG(0xF140)
#define PWMD0H    XREG(0xF141)
#define PWMD1L    XREG(0xF142)
#define PWMD1H    XREG(0xF143)
#define PWMD5L    XREG(0xF14A)
#define PWMD5H    XREG(0xF14B)
#define BRTCON    XREG(0xF5C0)
#define BRTDL     XREG(0xF5C1)
#define BRTDH     XREG(0xF5C2)
#define ADCLDO    XREG(0xF692)
#define PS_SCK    XREG(0xF698)  // SPI clock pin select (stock writes 0x04 = P0.4)
#define PS_MOSI   XREG(0xF699)  // SPI data pin select (stock writes 0x05 = P0.5)
#define PS_RXD1   XREG(0xF69E)  // UART1 RX pin select (stock writes 0x11 = P1.1)

// Pins (schematic net names). Active-low RF path enables sit behind PNP drivers.
__sbit __at(0x80) KEY_R4;    // P0.0
__sbit __at(0x81) KEY_R3;    // P0.1
__sbit __at(0x82) KEY_R2;    // P0.2
__sbit __at(0x83) KEY_R1;    // P0.3
__sbit __at(0x86) LCD_CD;    // P0.6  0 = command
__sbit __at(0x87) LCD_RST;   // P0.7
__sbit __at(0x90) LED_RXU;   // P1.0
__sbit __at(0x92) LED_TXU;   // P1.2
__sbit __at(0x94) FONT_CS;   // P1.4  GT21L16S2W, unused
__sbit __at(0x96) EE_SCL;    // P1.6  AT24C64
__sbit __at(0xA3) KEY_L1;    // P2.3  column + PTT
__sbit __at(0xA4) KEY_L2;    // P2.4  column + side key 1
__sbit __at(0xA5) KEY_L3;    // P2.5  column + side key 2
__sbit __at(0xA6) KEY_R6;    // P2.6
__sbit __at(0xA7) KEY_R5;    // P2.7
__sbit __at(0xB0) LED_RXV;   // P3.0
__sbit __at(0xB1) LED_TXV;   // P3.1
// Two LED spots, each a green (RX) and a red (TX) LED, 1 = lit: L-LED (slot A) on P1.0/P1.2, direct;
// R-LED (slot B) on P3.0/P3.1, NPN, shared with the Bluetooth module's LED. main.c leds() drives them.
__sbit __at(0xB2) KEY_LED;   // P3.2
__sbit __at(0xB3) BT_ON;     // P3.3  Bluetooth module enable
__sbit __at(0xB6) FM_SDA;    // P3.6  RDA5807M
__sbit __at(0xB7) FM_SCL;    // P3.7
__sbit __at(0xC2) VOICE_SCK; // P4.2  voice chip U4 clock; its data line is P3.6 (FM_SDA)
__sbit __at(0xC3) AMP_ON;    // P4.3  TDA2822 power, 1 = on
__sbit __at(0xC4) VRX_N;     // P4.4  VHF RX path, 0 = on
__sbit __at(0xC5) TXV_N;     // P4.5  VHF TX path, 0 = on
__sbit __at(0xC6) BEEP;      // P4.6
__sbit __at(0xC7) TXU_N;     // P4.7  UHF TX path, 0 = on
__sbit __at(0xD8) EE_SDA;    // P5.0
__sbit __at(0xDA) BK_SDA;    // P5.2  BK4819 3-wire bus
__sbit __at(0xDB) BK_SCN;    // P5.3
__sbit __at(0xDC) BK_SCK;    // P5.4
__sbit __at(0xDD) URX_N;     // P5.5  UHF RX path, 0 = on
__sbit __at(0xAF) EA;

// Watchdog kick: timed-access write, exactly as stock does it.
#define WDT_KICK() do { TA = 0xAA; TA = 0x55; WDCON |= 0x01; } while (0)

#endif
