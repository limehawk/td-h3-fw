; asm-for: lcd.c lcd_text_bg
; c-sha256: 0489a0e807683d31
; lcd_text_bg: one line of 5x7 text in fg on bg, each pixel scale x scale (lcd.c has the C original).
; uint8_t lcd_text_bg(uint8_t x, uint8_t y, const char *s, uint8_t scale, uint16_t fg, uint16_t bg)
; Not reentrant: x in DPL, the rest in _lcd_text_bg_PARM_2..6 (XDATA), the new x back in DPL.
; Every register, B and DPTR are the caller's to lose (SDCC saves what it needs over a call).
; Assumes scale >= 1 (every caller passes 1-3), as the C does for its 8-bit W - cw.
;
; The pixels per character:
;   window(x, y - text_pad, 6 * scale, 8 * scale + 2 * text_pad), then 10 rows: text_pad rows of bg
;   (the band over the text), font rows 0-7 (bit 1, 2, .. 0x80 of each column byte), text_pad rows
;   of bg (the band under it). Each row is 6 columns, the font's 5 and a gap, each scale pixels wide
;   and the row is sent scale times (text_pad times for the bands).
;
; Inner loop registers:
;   r7 scale       r6 the row's 6 columns, MSB first, then a stop bit (0x02 when blank)
;   r5 times the row is sent  r4 the row shifting out  r3 rows left of 10  r2 pixels of this column
;   r1 -> bg on the stack (fg at r1 + 2, text_pad at r1 + 4)  r0 -> this column's colour, low byte
;   b  the font bit of this row (0 for the bands)  dptr the glyph's 5 bytes in font[]

	.module lcd_text_bg
	.optsdcc -mmcs51 --model-large
	.include "../build/asm.inc"	; layout and constants from the C (tools/asmcheck.py inc)

	.globl _lcd_text_bg
	.globl _lcd_text_bg_PARM_2
	.globl _lcd_text_bg_PARM_3
	.globl _lcd_text_bg_PARM_4
	.globl _lcd_text_bg_PARM_5
	.globl _lcd_text_bg_PARM_6
	.globl _mirror
	.globl _text_pad
	.globl _font
	.globl _window
	.globl _remote_text
	.globl __gptrget


; The parameters in the order remote_text takes them off the stack (last first, low byte first),
; so one upward walk pushes them all. The callers only know the names.
	.area XSEG    (XDATA)
_lcd_text_bg_PARM_6:	.ds 2	; bg
_lcd_text_bg_PARM_5:	.ds 2	; fg
_lcd_text_bg_PARM_4:	.ds 1	; scale
_lcd_text_bg_PARM_3:	.ds 3	; s, a generic pointer: low, high, space
_lcd_text_bg_PARM_2:	.ds 1	; y
tb_x:	.ds 1			; x, the next character's column

	.area CSEG    (CODE)
_lcd_text_bg:
	mov	a,dpl
	mov	dptr,#tb_x
	movx	@dptr,a
; if (mirror) remote_text(x, y, s, scale, fg, bg);   reentrant: x in DPL, the rest pushed
	mov	dptr,#_mirror
	movx	a,@dptr
	jz	00001$
	mov	dptr,#_lcd_text_bg_PARM_6
	mov	r7,#9
00002$:	movx	a,@dptr
	push	acc
	inc	dptr
	djnz	r7,00002$
	mov	dptr,#tb_x
	movx	a,@dptr
	mov	dpl,a
	lcall	_remote_text
	mov	a,sp
	add	a,#-9
	mov	sp,a
; The frame: bg low, bg high, fg low, fg high, text_pad (pushed in that order)
00001$:	mov	dptr,#_lcd_text_bg_PARM_6
	mov	r7,#4
00003$:	movx	a,@dptr
	push	acc
	inc	dptr
	djnz	r7,00003$
	mov	dptr,#_text_pad
	movx	a,@dptr
	push	acc
	sjmp	00010$
; return x;  the frame dropped
00090$:	mov	a,sp
	add	a,#-5
	mov	sp,a
	mov	dptr,#tb_x
	movx	a,@dptr
	mov	dpl,a
	ret

; for (; *s; s++, x += cw): a = *s
00010$:	mov	dptr,#_lcd_text_bg_PARM_3
	movx	a,@dptr
	mov	r2,a
	inc	dptr
	movx	a,@dptr
	mov	r3,a
	inc	dptr
	movx	a,@dptr
	mov	b,a
	mov	dpl,r2
	mov	dph,r3
	lcall	__gptrget		; a = *s; keeps r0-r7
	jz	00090$
	mov	r6,a			; r6 = the character
; r5 = cw = 6 * scale; stop unless x <= W - cw, as x + cw <= 128 in 9 bits
	mov	dptr,#_lcd_text_bg_PARM_4
	movx	a,@dptr
	mov	r7,a
	mov	b,#6
	mul	ab
	mov	r5,a
	mov	dptr,#tb_x
	movx	a,@dptr
	mov	r4,a			; r4 = x
	add	a,r5
	jc	00090$
	add	a,#0x7F
	jc	00090$
; window(x, y - text_pad, cw, 8 * scale + 2 * text_pad);   the character kept on the stack over it
	mov	r0,sp
	mov	a,@r0
	mov	r3,a			; r3 = text_pad (top of the frame)
	push	ar6
	mov	a,r7
	swap	a
	rr	a
	anl	a,#0xF8			; 8 * scale
	add	a,r3
	add	a,r3
	push	acc			; h
	push	ar5			; w
	mov	dptr,#_lcd_text_bg_PARM_2
	movx	a,@dptr
	clr	c
	subb	a,r3
	push	acc			; y - text_pad
	mov	dpl,r4			; x
	lcall	_window
	dec	sp
	dec	sp
	dec	sp
; i = c - 0x20, '?' past 0x80; dptr = font[i]
	mov	dptr,#_lcd_text_bg_PARM_4
	movx	a,@dptr
	mov	r7,a			; scale again: window() took the registers
	pop	acc
	add	a,#-0x20
	cjne	a,#0x61,00011$
00011$:	jc	00012$
	mov	a,#'?' - 0x20
00012$:	mov	b,#5
	mul	ab
	add	a,#_font
	mov	dpl,a
	mov	a,b
	addc	a,#(_font >> 8)
	mov	dph,a
	mov	a,sp
	add	a,#-4
	mov	r1,a			; r1 -> bg in the frame
	mov	b,#0			; bit 0: the band over the text
	mov	r3,#10

; One row: r6 = the columns where font[i][n] & bit, MSB first, the gap, then the stop bit.
; Seeded 0x02: five rlc put the columns in bits 4-0 and the seed's 010 in bits 7-5; swap and rr
; rotate left 3, giving c0 c1 c2 c3 c4 0 1 0.
00020$:	mov	r6,#0x02
	mov	r2,#0
00021$:	mov	a,r2
	movc	a,@a+dptr
	anl	a,b
	add	a,#0xFF			; C = this column is lit
	mov	a,r6
	rlc	a
	mov	r6,a
	inc	r2
	cjne	r2,#5,00021$
	swap	a
	rr	a
	mov	r6,a
; r5 = bit ? scale : text_pad, the times this row is sent
	mov	a,b
	jz	00022$
	mov	a,r7
	sjmp	00023$
00022$:	mov	a,r1
	add	a,#4
	mov	r0,a
	mov	a,@r0			; text_pad
00023$:	jz	00030$			; no band rows
	mov	r5,a
00024$:	mov	a,r6
	mov	r4,a
; One column: C = its bit, r0 -> fg or bg, then scale pixels
00025$:	mov	a,r4
	add	a,acc
	mov	r4,a
	mov	a,r1
	jnc	00026$
	inc	a
	inc	a
00026$:	mov	r0,a
	mov	ar2,r7
00027$:	inc	r0			; SPI(high); SPI(low), as SPI() in lcd.c
	mov	a,@r0
	mov	SPSR,#0
	mov	SPDR,a
00028$:	mov	a,SPSR
	jnb	acc.7,00028$
	dec	r0
	mov	a,@r0
	mov	SPSR,#0
	mov	SPDR,a
00029$:	mov	a,SPSR
	jnb	acc.7,00029$
	djnz	r2,00027$
	cjne	r4,#0x80,00025$		; the stop bit is out: 6 columns sent
	djnz	r5,00024$
; bit = bit ? bit << 1 : 1  (0x80 << 1 is 0: the band under the text)
00030$:	mov	a,b
	add	a,acc
	jnz	00031$
	jc	00031$
	inc	a
00031$:	mov	b,a
	djnz	r3,00020$

; s++; x += cw
	mov	dptr,#_lcd_text_bg_PARM_3
	movx	a,@dptr
	add	a,#1
	movx	@dptr,a
	inc	dptr
	movx	a,@dptr
	addc	a,#0
	movx	@dptr,a
	mov	a,r7
	mov	b,#6
	mul	ab
	mov	r5,a
	mov	dptr,#tb_x
	movx	a,@dptr
	add	a,r5
	movx	@dptr,a
	ljmp	00010$
