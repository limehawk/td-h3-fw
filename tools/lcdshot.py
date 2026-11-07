"""Turn the LCD byte stream the emulator captured (cpu.lcd) into a PNG, as the panel would show it.

    from lcdshot import png
    open('shot.png', 'wb').write(png(cpu.lcd))

Follows CASET (0x2A, column offset 32), RASET (0x2B) and RAMWR (0x2C); pixels fill the window
x-fastest, RGB565 with red and blue swapped (the TD-H3 panel is BGR).
"""
import struct
import zlib

W = H = 128
X_OFS = 32


class Panel:
    """The panel's pixels, fed the byte stream as it grows: feed() reads only what's new."""
    def __init__(self):
        self.fb = [[0] * W for _ in range(H)]
        self.cmd, self.args, self.win, self.pos, self.half, self.at = None, [], (0, 0, 0, 0), 0, None, 0

    def feed(self, lcd):
        fb, cmd, args, win, pos, half = self.fb, self.cmd, self.args, self.win, self.pos, self.half
        for is_data, b in lcd[self.at:]:
            if not is_data:
                cmd, args, half = b, [], None
                if b == 0x2C:
                    pos = 0
                continue
            if cmd in (0x2A, 0x2B):
                args.append(b)
                if len(args) == 4:
                    lo, hi = args[1] - (X_OFS if cmd == 0x2A else 0), args[3] - (X_OFS if cmd == 0x2A else 0)
                    win = (lo, hi, win[2], win[3]) if cmd == 0x2A else (win[0], win[1], lo, hi)
            elif cmd == 0x2C:
                if half is None:
                    half = b
                    continue
                v, half = half << 8 | b, None
                x0, x1, y0, y1 = win
                w = x1 - x0 + 1
                x, y = x0 + pos % w, y0 + pos // w
                pos += 1
                if 0 <= x < W and 0 <= y < H and y <= y1:
                    fb[y][x] = v
        self.cmd, self.args, self.win, self.pos, self.half, self.at = cmd, args, win, pos, half, len(lcd)
        return fb


def frame(lcd):
    return Panel().feed(lcd)


def png(lcd, scale=3, fb=None):
    rows = []
    for line in fb or frame(lcd):
        px = bytearray()
        for v in line:
            b, g, r = v >> 11 & 31, v >> 5 & 63, v & 31      # BGR panel
            px += bytes((r << 3 | r >> 2, g << 2 | g >> 4, b << 3 | b >> 2)) * scale
        rows += [b'\0' + bytes(px)] * scale

    def chunk(t, d):
        return struct.pack('>I', len(d)) + t + d + struct.pack('>I', zlib.crc32(t + d))
    return (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', W * scale, H * scale, 8, 2, 0, 0, 0))
            + chunk(b'IDAT', zlib.compress(b''.join(rows))) + chunk(b'IEND', b''))
