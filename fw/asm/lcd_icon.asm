; asm-for: lcd.c lcd_icon
; c-sha256: b24e512d927b3c70
; lcd_icon: a 1-bit icon (width, height, then a byte per row, MSB left) in pal[fi] on c_bg
; (lcd.c has the C original).
; void lcd_icon(uint8_t x, uint8_t y, const __code uint8_t *ic, uint8_t fi) __reentrant
; Reentrant: x in DPL; the caller pushes fi, ic (low, high), y and drops them after the return.
; Every register, B and DPTR are the caller's to lose.
;
; The frame after the first push (sp at x):
;   sp: x   sp-1, sp-2: return address   sp-3: y   sp-4: ic high   sp-5: ic low   sp-6: fi

	.module lcd_icon
	.optsdcc -mmcs51 --model-large
	.include "../build/asm.inc"	; layout and constants from the C (tools/asmcheck.py inc)

	.globl _lcd_icon
	.globl _mirror
	.globl _pal
	.globl _window
	.globl _remote_fill


	.area CSEG    (CODE)
_lcd_icon:
	push	dpl			; x
	mov	dptr,#_mirror
	movx	a,@dptr
	jz	00020$

; if (mirror): to a watching computer as fills, the cell and then each lit run of each row
	mov	r0,sp
	mov	ar7,@r0			; r7 = x
	dec	r0
	dec	r0
	dec	r0
	mov	ar6,@r0			; r6 = y
	dec	r0
	mov	dph,@r0
	dec	r0
	mov	dpl,@r0			; dptr = ic
	clr	a
	movc	a,@a+dptr
	mov	r5,a			; r5 = w
	inc	dptr
	clr	a
	movc	a,@a+dptr
	mov	r4,a			; r4 = h, the rows left
	inc	dptr			; dptr -> the first row
; remote_fill(x, y, w, h, c_bg)
	mov	ar0,r7
	mov	ar1,r6
	mov	ar2,r5
	mov	ar3,r4
	mov	b,#0
	lcall	00090$
; for each row: r6 = y + r, r5 = its bits left, r7 = px, this bit's column
00010$:	mov	a,r4
	jz	00020$
	clr	a
	movc	a,@a+dptr
	inc	dptr
	mov	r5,a
	mov	r0,sp
	mov	ar7,@r0			; px = x
00011$:	mov	a,r5
	jz	00018$			; no lit bit left in the row
	jnb	acc.7,00013$
; a run: n = 0; do { b <<= 1; n++; } while (b & 0x80);
	mov	r2,#0
00012$:	add	a,acc
	inc	r2
	jb	acc.7,00012$
	mov	r5,a
; remote_fill(px, y + r, n, 1, pal[fi]); px += n
	mov	a,sp
	add	a,#-6
	mov	r0,a
	mov	b,@r0			; fi
	mov	ar0,r7
	mov	ar1,r6
	mov	r3,#1
	mov	a,r7
	add	a,r2
	mov	r7,a
	lcall	00090$
; px++, b <<= 1: over the clear bit that ended the run, or this unlit one
00013$:	inc	r7
	mov	a,r5
	add	a,acc
	mov	r5,a
	sjmp	00011$
00018$:	inc	r6
	dec	r4
	sjmp	00010$

; window(x, y, w, h)
00020$:	mov	r0,sp
	mov	ar7,@r0			; x
	dec	r0
	dec	r0
	dec	r0
	mov	ar6,@r0			; y
	dec	r0
	mov	dph,@r0
	dec	r0
	mov	dpl,@r0			; ic
	mov	a,#1
	movc	a,@a+dptr
	push	acc			; h
	clr	a
	movc	a,@a+dptr
	push	acc			; w
	push	ar6
	mov	dpl,r7
	lcall	_window
	dec	sp
	dec	sp
	dec	sp

; r1:r0 = pal[fi] (high:low), r3:r2 = c_bg, r7 = w, r4 = h, dptr = ic + 2
	mov	a,sp
	add	a,#-6
	mov	r1,a
	mov	a,@r1			; fi
	inc	r1
	mov	ar4,@r1
	inc	r1
	mov	ar5,@r1			; r5:r4 = ic
	mov	b,#2
	mul	ab
	add	a,#_pal
	mov	dpl,a
	mov	a,b
	addc	a,#(_pal >> 8)
	mov	dph,a
	movx	a,@dptr
	mov	r0,a
	inc	dptr
	movx	a,@dptr
	mov	r1,a
	mov	dptr,#_pal
	movx	a,@dptr
	mov	r2,a
	inc	dptr
	movx	a,@dptr
	mov	r3,a
	mov	dpl,r4
	mov	dph,r5
	clr	a
	movc	a,@a+dptr
	mov	r7,a
	jz	00039$			; w = 0: no pixels
	inc	dptr
	clr	a
	movc	a,@a+dptr
	mov	r4,a
	jz	00039$			; h = 0: no rows
	inc	dptr
; for (; h; h--, ic++) for (b = *ic, i = w; i; i--, b <<= 1): fg if b & 0x80, else bg
00030$:	clr	a
	movc	a,@a+dptr
	inc	dptr
	mov	r6,a
	mov	ar5,r7
00031$:	mov	a,r6
	add	a,acc			; C = this pixel
	mov	r6,a
	mov	a,r1
	mov	b,r0
	jc	00032$
	mov	a,r3
	mov	b,r2
00032$:	mov	SPSR,#0			; SPI(high); SPI(low), as SPI() in lcd.c
	mov	SPDR,a
00033$:	mov	a,SPSR
	jnb	acc.7,00033$
	mov	a,b
	mov	SPSR,#0
	mov	SPDR,a
00034$:	mov	a,SPSR
	jnb	acc.7,00034$
	djnz	r5,00031$
	djnz	r4,00030$
00039$:	dec	sp			; the frame's x
	ret

; remote_fill(r0, r1, r2, r3, pal[b]), keeping dptr and r4-r7 for the loop above
00090$:	push	dpl
	push	dph
	push	ar4
	push	ar5
	push	ar6
	push	ar7
	mov	a,b
	mov	b,#2
	mul	ab
	add	a,#_pal
	mov	dpl,a
	mov	a,b
	addc	a,#(_pal >> 8)
	mov	dph,a
	movx	a,@dptr
	push	acc
	inc	dptr
	movx	a,@dptr
	push	acc			; c
	push	ar3			; h
	push	ar2			; w
	push	ar1			; y
	mov	dpl,r0			; x
	lcall	_remote_fill
	mov	a,sp
	add	a,#-5
	mov	sp,a
	pop	ar7
	pop	ar6
	pop	ar5
	pop	ar4
	pop	dph
	pop	dpl
	ret
