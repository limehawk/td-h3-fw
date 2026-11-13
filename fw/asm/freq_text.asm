; asm-for: main.c freq_text
; c-sha256: b5f728ad60dd23c4
; freq_text: a slot's frequency from buf + k in the DIGITS style: CLASSIC through lcd_text, ARCADE
; and LCD as fills (each shape a list of rectangles in main.c's tables), not mirrored: REMOTE gets
; CLASSIC's text.
; main.c has the C original.
; void freq_text(uint8_t k, uint8_t y, uint8_t fc) __reentrant
; k in DPL, y at SP-2 and fc at SP-3 on entry. Every register, B and DPTR are the caller's to lose.
; ARCADE and LCD keep their state in 8 bytes of internal RAM (direct addressing: a third of the
; code a frame on the stack takes); nothing here runs from an interrupt or calls itself.

	.module freq_text
	.optsdcc -mmcs51 --model-large
	.include "../build/asm.inc"	; layout and constants from the C (tools/asmcheck.py inc)

	.globl _freq_text
	.globl _cfg
	.globl _buf
	.globl _pal
	.globl _mirror
	.globl _arc_r
	.globl _arc_i
	.globl _rowy
	.globl _seg_r
	.globl _seg7
	.globl _dots
	.globl _lcd_fill
	.globl _lcd_text
	.globl _remote_text

	.area DSEG    (DATA)
fbig:	.ds	1			; 1 for the big digits (k < 7), 0 for the small ones
fa:	.ds	1			; 1 for ARCADE, 0 for LCD
fy:	.ds	1			; the digits' top row
fx:	.ds	1			; this character's left column
fs:	.ds	1			; its index in buf
fc:	.ds	1			; the character
fo:	.ds	1			; the pass: 1 ARCADE's shadow, 1 right and down; 0 the character
fp:	.ds	1			; fc, the palette index of the digits' colour

	.area CSEG    (CODE)
_freq_text:
	mov	r7,dpl			; k
	clr	c
	mov	a,r7
	subb	a,#7
	clr	a
	rlc	a
	mov	r5,a			; big = k < 7
	mov	fbig,a
	mov	dptr,#(_cfg + CFG_DIGITS)
	movx	a,@dptr
	anl	a,#CFG_DIGITS_MASK
	mov	r6,a
; CLASSIC's place: x = big ? 32 + 12 * k : 116 (r3), y + (big ? 9 : 17) (r4 the 9 or 17)
	mov	r3,#116
	mov	r4,#17
	mov	a,r5
	jz	00001$
	mov	a,r7
	mov	b,#12
	mul	ab
	add	a,#32
	mov	r3,a
	mov	r4,#9
00001$:	mov	a,r6
	cjne	a,#(D_CLASSIC << CFG_DIGITS_BIT),00010$
; CLASSIC: lcd_text(x, y + (big ? 9 : 17), buf + k, big + 1, fc); return;
	mov	a,sp
	add	a,#-3
	mov	r0,a
	mov	a,@r0
	push	acc			; fc
	mov	a,r5
	inc	a
	push	acc			; scale
	mov	a,r7
	add	a,#_buf
	push	acc
	clr	a
	addc	a,#(_buf >> 8)
	push	acc
	mov	a,#GPTR_XDATA
	push	acc			; buf + k
	mov	a,sp
	add	a,#-7
	mov	r0,a
	mov	a,@r0
	add	a,r4
	push	acc			; y
	mov	dpl,r3
	lcall	_lcd_text
	mov	a,sp
	add	a,#-6
	mov	sp,a
	ret

; ARCADE and LCD: a = ARCADE; s = k; the top row t = y + (big ? 8 : 15) + a (fy); fc
00010$:	clr	a
	cjne	r6,#0,00011$
	inc	a
00011$:	mov	fa,a
	mov	fs,r7
	mov	r1,#15
	mov	a,r5
	jz	00012$
	mov	r1,#8
00012$:	mov	a,r1
	add	a,fa
	mov	r1,a
	mov	a,sp
	add	a,#-2
	mov	r0,a
	mov	a,@r0			; y
	add	a,r1
	mov	fy,a
	dec	r0
	mov	fp,@r0			; fc
; if (m = mirror) remote_text(CLASSIC's x and y, buf + k, big + 1, pal[fc], c_bg); mirror = 0; m kept on the stack
	inc	r0
	mov	a,@r0
	add	a,r4
	mov	r4,a
	mov	dptr,#_mirror
	movx	a,@dptr
	push	acc
	jz	00009$
	mov	dptr,#_pal
	movx	a,@dptr
	push	acc
	inc	dptr
	movx	a,@dptr
	push	acc			; c_bg
	mov	fo,#0
	lcall	ink
	push	ar2
	push	ar7			; pal[fc]
	mov	a,fbig
	inc	a
	push	acc			; scale
	mov	a,fs
	add	a,#_buf
	push	acc
	clr	a
	addc	a,#(_buf >> 8)
	push	acc
	mov	a,#GPTR_XDATA
	push	acc			; buf + k
	push	ar4
	mov	dpl,r3
	lcall	_remote_text
	mov	a,sp
	add	a,#-9
	mov	sp,a
	mov	dptr,#_mirror
	clr	a
	movx	@dptr,a
00009$:
; x = 5; for (o = 0; o < k && o < 7; o++) x += buf[o] == '.' ? 5 : 16;   r3 = x, r2 = o
	mov	r3,#5
	mov	r2,#0
	mov	dptr,#_buf
00014$:	mov	a,r2
	cjne	a,fs,00015$
	sjmp	00017$
00015$:	cjne	a,#7,00016$
	sjmp	00017$
00016$:	movx	a,@dptr
	inc	dptr
	inc	r2
	lcall	advb
	add	a,r3
	mov	r3,a
	sjmp	00014$
00017$:	mov	fx,r3
; the cells from this one on, in one fill (one REMOTE record): to x 104 (the last big digit's
; shadow) or 121 (the small ones'); ARCADE's 2 rows shorter, and its shadow's:
; lcd_fill(x, t, (big ? 105 : 122) - x, (big ? 18 : 11) - a, c_bg)
	mov	dptr,#_pal
	movx	a,@dptr
	push	acc
	inc	dptr
	movx	a,@dptr
	push	acc
	mov	r2,#11
	mov	r4,#122
	mov	a,fbig
	jz	00022$
	mov	r2,#18
	mov	r4,#105
00022$:	mov	a,r2
	clr	c
	subb	a,fa
	push	acc			; rows
	mov	a,r4
	clr	c
	subb	a,r3
	push	acc			; width
	push	fy
	mov	dpl,r3
	lcall	_lcd_fill
	mov	a,sp
	add	a,#-5
	mov	sp,a

; for (; c = buf[s]; s++)
00020$:	mov	a,fs
	add	a,#_buf
	mov	dpl,a
	clr	a
	addc	a,#(_buf >> 8)
	mov	dph,a
	movx	a,@dptr
	jnz	00021$
	ljmp	00040$
00021$:	mov	fc,a
	mov	fo,fa			; ARCADE: the shadow first

; a pass: the point (dots, or dots + 2 for LCD); a digit; anything else nothing
00026$:	mov	a,fc
	cjne	a,#'.',00027$
	mov	dptr,#(_dots + 2)
	mov	a,fa
	jz	00030$
	mov	dptr,#_dots
00030$:	mov	r7,#1
	lcall	rects
; the next pass while o > 0; then x moves on: small 9 - a; big 16, the point 5
00031$:	mov	a,fo
	jz	00034$
	dec	fo
	sjmp	00026$
00034$:	mov	a,#9
	clr	c
	subb	a,fa
	mov	r2,fbig
	cjne	r2,#1,00035$
	mov	a,fc
	lcall	advb
00035$:	add	a,fx
	mov	fx,a
	inc	fs
	ljmp	00020$
00027$:	add	a,#-'0'
	mov	r7,a
	add	a,#-10
	jc	00031$
	mov	a,fa
	jz	00028$
; ARCADE: rects(arc_r + 2 * arc_i[d], arc_i[d + 1] - arc_i[d]), in big shape units for the big ones
	mov	dptr,#_arc_i
	mov	a,r7
	movc	a,@a+dptr
	mov	b,a
	mov	a,r7
	inc	a
	movc	a,@a+dptr
	clr	c
	subb	a,b
	xch	a,b
	add	a,acc
	add	a,#_arc_r
	mov	dpl,a
	clr	a
	addc	a,#(_arc_r >> 8)
	mov	dph,a
	mov	r7,b
	lcall	rects
	sjmp	00031$
; LCD: m = seg7[d]; each segment's 2 * big + 1 rectangles from seg_r (big) or seg_r + 42 (small), in
; order: rects leaves DPTR past what it drew, a segment that's off is stepped over
00028$:	mov	a,r7
	mov	dptr,#_seg7
	movc	a,@a+dptr
	mov	r6,a
	mov	dptr,#_seg_r
	mov	a,fbig
	jnz	00029$
	mov	dptr,#(_seg_r + 42)
00029$:	mov	a,r6
00036$:	jz	00031$
	clr	c
	rrc	a			; m >>= 1, its bit in C
	push	acc			; m, kept over rects
	mov	a,fbig
	rl	a
	inc	a
	mov	r7,a			; n = 2 * big + 1 (C still the bit)
	jc	00033$
00032$:	inc	dptr
	inc	dptr
	djnz	r7,00032$
	sjmp	00037$
00033$:	lcall	rects
00037$:	pop	acc
	sjmp	00036$

; mirror = m
00040$:	pop	acc
	mov	dptr,#_mirror
	movx	@dptr,a
	ret

; advb: A = the big character in A's advance: 16, the point 5
advb:	cjne	a,#'.',00043$
	mov	a,#5
	ret
00043$:	mov	a,#16
	ret

; ink: R7:R2 = this pass's colour: pal[fc], for the shadow (o 1) at 40%:
; (c >> 1 & 0x7BEF) - (c >> 3 & 0x18E3) + (c >> 5 & 0x0020), each channel x 0.406, none borrowing
; from the next (each is c/2 - c/8)
ink:	mov	a,fp
	add	a,acc
	add	a,#_pal
	mov	dpl,a
	clr	a
	addc	a,#(_pal >> 8)
	mov	dph,a
	movx	a,@dptr
	mov	r2,a
	inc	dptr
	movx	a,@dptr
	mov	r7,a
	mov	a,fo
	jz	00053$
	mov	a,r7
	clr	c
	rrc	a
	mov	r1,a
	mov	a,r2
	rrc	a
	mov	r0,a			; r1:r0 = c >> 1
	mov	a,r1
	anl	a,#3
	rr	a
	rr	a
	mov	b,a
	mov	a,r0
	rr	a
	rr	a
	anl	a,#0x3F
	orl	a,b			; c >> 3, low
	anl	a,#0xE3
	mov	b,a
	mov	a,r0
	anl	a,#0xEF
	clr	c
	subb	a,b
	mov	r2,a
	mov	a,r1
	rr	a
	rr	a
	anl	a,#0x18			; c >> 3, high: & 0x18 keeps none of the bits rr brought round
	mov	b,a
	mov	a,r1
	anl	a,#0x7B
	subb	a,b
	xch	a,r7
	anl	a,#0x04			; c >> 5 & 0x20: c's bit 10
	jz	00053$
	mov	a,#0x20
	add	a,r2
	mov	r2,a
	clr	a
	addc	a,r7
	mov	r7,a
00053$:	ret

; rects: R7 rectangles from the code at DPTR, each x << 4 | w, y << 3 | h - 1, filled at x + o, y + o
; in pass o's colour; an ARCADE big digit's (a, big, not the point) are in shape units: x and w
; double and the rows go by rowy. DPTR ends past them.
rects:	push	dpl
	push	dph
	push	ar7
	clr	a
	movc	a,@a+dptr
	mov	r6,a
	mov	a,#1
	movc	a,@a+dptr
	mov	r5,a
	mov	a,r6
	swap	a
	anl	a,#0x0F
	mov	r4,a			; x
	mov	a,r6
	anl	a,#0x0F
	mov	r6,a			; w
	mov	a,r5
	anl	a,#0x07
	inc	a
	mov	r3,a			; h
	mov	a,r5
	rr	a
	rr	a
	rr	a
	anl	a,#0x1F
	mov	r5,a			; y
	mov	a,fc
	cjne	a,#'.',00049$
	sjmp	00050$
00049$:	mov	a,fa
	anl	a,fbig
	jz	00050$
	mov	a,r4
	add	a,acc
	mov	r4,a
	mov	a,r6
	add	a,acc
	mov	r6,a
	mov	dptr,#_rowy		; h = rowy[y + h] - rowy[y]; y = rowy[y]
	mov	a,r5
	add	a,r3
	movc	a,@a+dptr
	mov	r3,a
	mov	a,r5
	movc	a,@a+dptr
	mov	r5,a
	xch	a,r3
	clr	c
	subb	a,r3
	xch	a,r3
00050$:	lcall	ink
	push	ar2			; lcd_fill(x + o + rx, y + o + ry, w, h, that colour)
	push	ar7
	push	ar3
	push	ar6
	mov	a,fy
	add	a,fo
	add	a,r5
	push	acc
	mov	a,fx
	add	a,fo
	add	a,r4
	mov	dpl,a
	lcall	_lcd_fill
	mov	a,sp
	add	a,#-5
	mov	sp,a
	pop	ar7
	pop	dph
	pop	dpl
	inc	dptr
	inc	dptr
	djnz	r7,rects
	ret
