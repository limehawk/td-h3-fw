; asm-for: lcd.c lcd_fill
; c-sha256: daad65a33e9b5793
; lcd_fill: a w x h block of colour c at x, y (lcd.c has the C original).
; void lcd_fill(uint8_t x, uint8_t y, uint8_t w, uint8_t h, uint16_t c) __reentrant
; Reentrant: x in DPL; the caller pushes c (low, high), h, w, y and drops them after the return.
; Every register, B and DPTR are the caller's to lose.
;
; The frame after the first push (sp at x):
;   sp: x   sp-1, sp-2: return address   sp-3: y   sp-4: w   sp-5: h   sp-6: c high   sp-7: c low
; remote_fill takes the same arguments and window() the middle three, in the same order, so both
; are passed by pushing a copy of the caller's bytes.

	.module lcd_fill
	.optsdcc -mmcs51 --model-large
	.include "../build/asm.inc"	; layout and constants from the C (tools/asmcheck.py inc)

	.globl _lcd_fill
	.globl _mirror
	.globl _window
	.globl _remote_fill


	.area CSEG    (CODE)
_lcd_fill:
	push	dpl			; x
; if (mirror) remote_fill(x, y, w, h, c);
	mov	dptr,#_mirror
	movx	a,@dptr
	jz	00001$
	mov	a,sp
	add	a,#-7
	mov	r0,a			; -> c low
	mov	r6,#5
00002$:	mov	a,@r0			; c low, c high, h, w, y
	push	acc
	inc	r0
	djnz	r6,00002$
	inc	r0			; r0 -> the return address: x is past it
	inc	r0
	mov	dpl,@r0
	lcall	_remote_fill
	mov	a,sp
	add	a,#-5
	mov	sp,a
; window(x, y, w, h);
00001$:	mov	r0,sp
	mov	ar7,@r0			; x
	mov	a,sp
	add	a,#-5
	mov	r0,a			; -> h
	mov	r6,#3
00003$:	mov	a,@r0			; h, w, y
	push	acc
	inc	r0
	djnz	r6,00003$
	mov	dpl,r7
	lcall	_window
	dec	sp
	dec	sp
	dec	sp
; for (; h; h--) for (i = w; i; i--) { SPI(hi); SPI(lo); }   r3:r2 = c, r4 = h, r5 = w
	mov	a,sp
	add	a,#-7
	mov	r0,a
	mov	ar2,@r0
	inc	r0
	mov	ar3,@r0
	inc	r0
	mov	ar4,@r0
	inc	r0
	mov	ar5,@r0
	mov	a,r4
	jz	00009$
	mov	a,r5
	jz	00009$
00004$:	mov	ar6,r5
00005$:	mov	SPSR,#0
	mov	SPDR,r3
00006$:	mov	a,SPSR
	jnb	acc.7,00006$
	mov	SPSR,#0
	mov	SPDR,r2
00007$:	mov	a,SPSR
	jnb	acc.7,00007$
	djnz	r6,00005$
	djnz	r4,00004$
; WDT_KICK(): the watchdog, behind its timed-access unlock
00009$:	mov	TA,#0xAA
	mov	TA,#0x55
	orl	WDCON,#0x01
	dec	sp			; the frame's x
	ret
