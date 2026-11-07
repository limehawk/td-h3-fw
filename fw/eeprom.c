// AT24C64 on bit-banged I2C: SCL = P1.6, SDA = P5.0. Like stock, SDA is driven
// push-pull and switched to input only to read ACK/data (P5TRIS bit 0).
#include "fw.h"

#define SDA_IN()  (P5TRIS &= ~0x01)
#define SDA_OUT() (P5TRIS |= 0x01)

static void start(void)
{
    SDA_OUT(); EE_SDA = 1; EE_SCL = 1; udelay();
    EE_SDA = 0; udelay(); EE_SCL = 0;
}

static void stop(void)
{
    SDA_OUT(); EE_SDA = 0; EE_SCL = 1; udelay();
    EE_SDA = 1; udelay();
}

static uint8_t tx(uint8_t b) __reentrant                // 1 = ACK
{
    uint8_t ack;
    for (uint8_t i = 0; i < 8; i++, b <<= 1) {
        EE_SDA = b & 0x80; udelay();
        EE_SCL = 1; udelay(); EE_SCL = 0;
    }
    SDA_IN(); udelay();
    EE_SCL = 1; udelay();
    ack = !EE_SDA;
    EE_SCL = 0; SDA_OUT();
    return ack;
}

static uint8_t rx(uint8_t ack) __reentrant
{
    uint8_t b = 0;
    SDA_IN();
    for (uint8_t i = 0; i < 8; i++) {
        EE_SCL = 1; udelay();
        b = b << 1 | EE_SDA;
        EE_SCL = 0; udelay();
    }
    SDA_OUT(); EE_SDA = !ack; udelay();
    EE_SCL = 1; udelay(); EE_SCL = 0;
    return b;
}

static uint8_t addr(uint16_t a) __reentrant
{
    start();
    return tx(0xA0) && tx(a >> 8) && tx(a);
}

static uint8_t read_once(uint16_t a, __xdata uint8_t *buf, uint8_t n) __reentrant
{
    uint8_t ok;
    EA = 0;
    ok = addr(a);
    if (ok) {
        start();
        ok = tx(0xA1);
        while (ok && n--) *buf++ = rx(n != 0);
    }
    stop();
    EA = 1;
    return ok;
}

// A chip left mid-transfer (reset at the wrong moment) holds SDA low: clock it out with SDA
// released, then STOP. The standard I2C bus recovery.
static void unstick(void)
{
    SDA_IN();
    for (uint8_t i = 0; i < 9; i++) { EE_SCL = 0; udelay(); EE_SCL = 1; udelay(); }
    stop();
}

uint8_t ee_read(uint16_t a, __xdata uint8_t *buf, uint8_t n)
{
    for (uint8_t try = 0; try < 3; try++) {
        if (read_once(a, buf, n)) return 1;
        unstick();
        delay_ms(5);
    }
    return 0;
}


uint8_t ee_write(uint16_t a, const __xdata uint8_t *buf, uint8_t n)
{
    if (a < 0x1939 && a + n > 0x1900) maps_ok = 0;   // radio.c's copy of the channel bitmaps is stale
    while (n) {
        uint8_t k = 32 - (a & 31), ok, t;    // stay inside one 32-byte page
        if (k > n) k = n;
        // The factory TX-power calibration (0x1F50-0x1F7F) and stock's init magic (0x1FF0-0x1FFF)
        // are never written, from any path: a page touching either is refused.
        if (a < 0x1F80 && a + k > 0x1F50 || a + k > 0x1FF0) return 0;
        EA = 0;
        ok = addr(a);
        for (t = k; ok && t; t--) ok = tx(*buf++);
        stop();
        EA = 1;
        if (!ok) return 0;
        for (t = 20; t; t--) {              // write cycle <= 5 ms: poll for ACK
            EA = 0; start(); ok = tx(0xA0); stop(); EA = 1;
            if (ok) break;
            delay_ms(1);
        }
        a += k; n -= k;
    }
    return 1;
}
