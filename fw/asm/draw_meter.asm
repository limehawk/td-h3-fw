; asm-for: main.c draw_meter
; c-sha256: f0847c7b7fca6cb4
; draw_meter: the meter row between the slots, with its private helpers m_fill, m_text and bar_x
; (main.c has the C originals). On receive the S-meter bar and "S9+11 ... -102dBm", on transmit the
; mic level and the TX clock "   116s". A station heard: its receive timer " 0:42" in the middle.
; void draw_meter(void) __reentrant
; Every register, B and DPTR are the caller's to lose.
;
; dbm is signed: for the compares it's held as u = dbm ^ 0x8000 in r7:r6 (high:low), so dbm >= v is
; u >= v + 0x8000, the carry out of u + (0x8000 - v): GE below, given the C's own v.
; The frame from the branches' end: sp-1 len (the bar's filled width), sp a (the right bar's start).

	.module draw_meter
	.optsdcc -mmcs51 --model-large
	.include "../build/asm.inc"	; layout and constants from the C (tools/asmcheck.py inc)

	.globl _draw_meter
	.globl _tx
	.globl _open
	.globl _dbm
	.globl _m_dbm
	.globl _m_open
	.globl _m_len
	.globl _m_sec
	.globl _m_col
	.globl _m_trk
	.globl _m_fg
	.globl _t_tx
	.globl _rx_min
	.globl _rx_sec
	.globl _tots
	.globl _cfg
	.globl _pal
	.globl _buf
	.globl _bk_mic
	.globl _secs
	.globl _u2s
	.globl _num
	.globl _theme_c
	.globl _lcd_fill
	.globl _lcd_text_bg
	.globl _lcd_text_bg_PARM_2
	.globl _lcd_text_bg_PARM_3
	.globl _lcd_text_bg_PARM_4
	.globl _lcd_text_bg_PARM_5
	.globl _lcd_text_bg_PARM_6
	.globl icat
	.globl fillr


; C = (lo, hi) >= v as signed 16 bits, the pair holding the value with its sign bit flipped. Changes A.
	.macro	GE lo, hi, v
	mov	a,lo
	add	a,#<(0x8000 - (v))
	mov	a,hi
	addc	a,#>(0x8000 - (v))
	.endm

	.area CSEG    (CODE)
_draw_meter:
; m_fg = c_fg
	mov	dptr,#(_pal + 2 * P_FG)
	lcall	rd2
	mov	dptr,#_m_fg
	lcall	wr2
	jb	_tx,00100$
	ljmp	00200$

; Transmitting: the mic level, log scale, and the TX clock at the right end.
00100$:	mov	dptr,#_m_open
	mov	a,#0xFF
	movx	@dptr,a
; m = bk_mic(); while (m) { len += 8; m >>= 1; }   r7 = len
	lcall	_bk_mic
	mov	r7,#0
00101$:	mov	a,dpl
	orl	a,dph
	jz	00102$
	mov	a,r7
	add	a,#8
	mov	r7,a
	clr	c
	mov	a,dph
	rrc	a
	mov	dph,a
	mov	a,dpl
	rrc	a
	mov	dpl,a
	sjmp	00101$
00102$:	push	ar7			; len
	clr	a
	push	acc			; a = 0: no left text, the bar from x 0
; s = tots[cfg.tot]; m = secs(&t_tx); if (s) { s -= m; if (s <= 10) m_fg = pal[P_RED]; } else s = m;   r6 = s
	mov	dptr,#(_cfg + CFG_TOT)
	movx	a,@dptr
	mov	dptr,#_tots
	movc	a,@a+dptr
	push	acc
	mov	dptr,#_t_tx
	lcall	_secs
	pop	acc
	jz	00103$
	clr	c
	subb	a,dpl
	mov	r6,a
	add	a,#-11			; C: s > 10
	jc	00104$
	mov	dptr,#(_pal + 2 * P_RED)
	lcall	rd2
	mov	dptr,#_m_fg
	lcall	wr2
	sjmp	00104$
00103$:	mov	r6,dpl
; "   116s": buf[0-2] spaces, u2s(buf + 3, s, 3), buf[6] = 's', buf[7] = buf[8] = 0
00104$:	mov	dptr,#_buf
	mov	a,#' '
	movx	@dptr,a
	inc	dptr
	movx	@dptr,a
	inc	dptr
	movx	@dptr,a
	inc	dptr
	mov	a,#3
	push	acc
	push	ar6
	clr	a
	push	acc
	lcall	_u2s
	mov	a,sp
	add	a,#-3
	mov	sp,a
	mov	dptr,#(_buf + 6)
	mov	a,#'s'
	movx	@dptr,a
	inc	dptr
	clr	a
	movx	@dptr,a
	inc	dptr
	movx	@dptr,a
; m_col = pal[P_RED]
	mov	dptr,#(_pal + 2 * P_RED)
	lcall	rd2
	mov	dptr,#_m_col
	lcall	wr2
	ljmp	00300$

; Receiving. Drawn 3 times a second: only when the reading or the squelch changed, else (open) just the
; timer once a second. if (dbm == m_dbm && open == m_open) { if (open && rx_sec != m_sec) rx_text(); return; }
; m_dbm = dbm; m_open = open;
00200$:	lcall	ldu
	mov	dptr,#_m_dbm
	movx	a,@dptr
	xrl	a,r6
	jnz	00201$
	inc	dptr
	movx	a,@dptr
	xrl	a,r7
	xrl	a,#0x80			; r7 is dbm's high byte with the sign flipped
	jnz	00201$
	mov	dptr,#_m_open
	movx	a,@dptr
	mov	r5,a
	clr	a
	mov	c,_open
	rlc	a
	xrl	a,r5
	jnz	00201$
	jnb	_open,00209$
	mov	dptr,#_rx_sec
	movx	a,@dptr
	mov	r3,a
	mov	dptr,#_m_sec
	movx	a,@dptr
	xrl	a,r3
	jz	00209$
	lcall	rxtxt
	ljmp	stick
00209$:	ret
00201$:	mov	dptr,#_m_dbm
	mov	a,r6
	movx	@dptr,a
	inc	dptr
	mov	a,r7
	xrl	a,#0x80
	movx	@dptr,a
	mov	dptr,#_m_open
	clr	a
	mov	c,_open
	rlc	a
	movx	@dptr,a
; if (dbm > -130) len = dbm >= -50 ? W : bar_x(dbm + 130);   r5 = len
	mov	r5,#0
	GE	r6, r7, -129		; C: dbm >= -129
	jnc	00202$
	mov	r5,#W
	GE	r6, r7, -50		; C: dbm >= -50
	jc	00202$
	mov	a,r6
	add	a,#130
	lcall	barx
	mov	r5,a
00202$:	push	ar5			; len
	mov	a,#31
	push	acc			; a = 31: the bar right of "S9+11"
; s = dbm > 0 ? 0 : dbm < -199 ? 199 : -dbm;   r4 = s
	mov	r4,#0
	GE	r6, r7, 1		; C: dbm >= 1
	jc	00203$
	mov	r4,#199
	GE	r6, r7, -199		; C: dbm >= -199
	jnc	00203$
	clr	a
	clr	c
	subb	a,r6
	mov	r4,a
; u2s(buf + 1, s, 3); for (a = 0; a < 2 && buf[a + 1] == '0'; a++) buf[a] = ' '; buf[a] = '-';
00203$:	mov	a,#3
	push	acc
	push	ar4
	clr	a
	push	acc
	mov	dptr,#(_buf + 1)
	lcall	_u2s
	mov	a,sp
	add	a,#-3
	mov	sp,a
	mov	dptr,#(_buf + 1)
	movx	a,@dptr
	cjne	a,#'0',00204$
	mov	dptr,#_buf
	mov	a,#' '
	movx	@dptr,a
	mov	dptr,#(_buf + 2)
	movx	a,@dptr
	cjne	a,#'0',00205$
	mov	dptr,#(_buf + 1)
	mov	a,#' '
	movx	@dptr,a
	mov	dptr,#(_buf + 2)	; "  -5"
	sjmp	00206$
00204$:	mov	dptr,#_buf		; "-102"
	sjmp	00206$
00205$:	mov	dptr,#(_buf + 1)	; " -99"
00206$:	mov	a,#'-'
	movx	@dptr,a
; cat(buf + 4, "dBm");
	mov	dptr,#(_buf + 4)
	lcall	icat
	.asciz	"dBm"
	lcall	ldu			; icat took r6 and r7
; if (dbm > -93) pad_to(num(cat(buf + 8, "S9+"), dbm + 93 > 99 ? 99 : dbm + 93), 13);
	GE	r6, r7, -92		; C: dbm >= -92
	jnc	00210$
	mov	a,r6			; t = dbm + 93 in 16 bits, as the C's int (it wraps as the C's does)
	add	a,#93
	mov	r4,a
	mov	a,r7
	addc	a,#0
	mov	r3,a
	GE	r4, r3, 100		; C: t > 99
	jnc	00207$
	mov	r4,#99
00207$:	push	ar4
	mov	dptr,#(_buf + 8)
	lcall	icat
	.asciz	"S9+"
	lcall	_num
	dec	sp
	lcall	pad13
	sjmp	00220$
; else { buf[8] = 'S'; buf[9] = '0' + (dbm < -147 ? 0 : (uint8_t)(dbm + 147) / 6); pad_to(buf + 10, 13); }
00210$:	GE	r6, r7, -147		; C: dbm >= -147
	clr	a
	jnc	00211$
	mov	a,r6
	add	a,#147
	mov	b,#6
	div	ab
00211$:	add	a,#'0'
	mov	r4,a
	mov	dptr,#(_buf + 8)
	mov	a,#'S'
	movx	@dptr,a
	inc	dptr
	mov	a,r4
	movx	@dptr,a
	inc	dptr
	lcall	pad13
; if (!open) m_fg = c_dim; m_col = open ? c_ok : (c_acc >> 1 & 0x7BEF) + (c_bg >> 1 & 0x7BEF);
00220$:	jb	_open,00221$
	mov	dptr,#(_pal + 2 * P_DIM)
	lcall	rd2
	mov	dptr,#_m_fg
	lcall	wr2
	mov	dptr,#_pal		; halfway from the background to the accent, each 565 field halved
	lcall	half
	push	ar2
	push	ar3
	mov	dptr,#(_pal + 2 * P_ACC)
	lcall	half
	pop	b
	pop	acc
	add	a,r2			; no field carries: each is two halves
	mov	r2,a
	mov	a,b
	addc	a,r3
	mov	r3,a
	sjmp	00222$
00221$:	mov	dptr,#(_pal + 2 * P_OK)
	lcall	rd2
00222$:	mov	dptr,#_m_col
	lcall	wr2

; Both: m_len = len; m_trk = theme_c(tx ? T_TX : open ? T_RX : P_BAR);
00300$:	mov	a,sp
	dec	a
	mov	r0,a
	mov	a,@r0
	mov	dptr,#_m_len
	movx	@dptr,a
	mov	a,#T_TX
	jb	_tx,00301$
	mov	a,#T_RX
	jb	_open,00301$
	mov	a,#P_BAR
00301$:	mov	dpl,a
	lcall	_theme_c
	mov	r2,dpl
	mov	r3,dph
	mov	dptr,#_m_trk
	lcall	wr2
; m_text(1, buf + 8); m_text(86, buf);
	mov	r7,#1
	mov	r5,#8
	lcall	mtext
	mov	r7,#86
	mov	r5,#0
	lcall	mtext
; if (!tx && open) { rx_text(); m_fill(a, 43, METER_Y); a = 73; }
	jb	_tx,00310$
	jnb	_open,00310$
	lcall	rxtxt
	mov	r0,sp
	mov	ar7,@r0			; a
	mov	r6,#43
	mov	r5,#METER_Y
	lcall	mfill
	mov	r0,sp
	mov	@r0,#73
00310$:
; m_fill(0, W, METER_Y - 1); m_fill(0, 1, METER_Y); m_fill(a, 86, METER_Y);
	mov	r7,#0
	mov	r6,#W
	mov	r5,#(METER_Y - 1)
	lcall	mfill
	mov	r7,#0
	mov	r6,#1
	mov	r5,#METER_Y
	lcall	mfill
	mov	r0,sp
	mov	ar7,@r0			; a
	mov	r6,#86
	mov	r5,#METER_Y
	lcall	mfill
	dec	sp
	dec	sp
; sql_tick: if (cfg.sql >= 10 && !tx) { int8_t d = cfg.sql * 6 - 71; lcd_fillp(d < 0 ? 0 : bar_x(d), METER_Y - 1, 1, 9, P_FG); }
stick:	jb	_tx,00309$
	mov	dptr,#(_cfg + CFG_SQL)
	movx	a,@dptr
	cjne	a,#10,00302$
00302$:	jc	00309$
	mov	b,#6
	mul	ab
	add	a,#-71
	jnb	acc.7,00303$
	clr	a
	sjmp	00304$
00303$:	lcall	barx
00304$:	mov	r7,a
	mov	r6,#(METER_Y - 1)
	mov	r5,#1
	mov	r4,#9
	mov	r3,#P_FG
	ljmp	fillr
00309$:	ret

; rx_text: buf[14-19] = " 0:42" (rx_min, its leading 0 a space; rx_sec), m_sec = rx_sec, m_text(43, buf + 14)
rxtxt:	mov	dptr,#_rx_sec
	movx	a,@dptr
	mov	r3,a
	mov	dptr,#_m_sec
	movx	@dptr,a
	mov	dptr,#_rx_min
	movx	a,@dptr
	mov	dptr,#(_buf + 14)
	lcall	dig2
	mov	a,#':'
	movx	@dptr,a
	inc	dptr
	mov	a,r3
	lcall	dig2
	clr	a
	movx	@dptr,a
	mov	dptr,#(_buf + 14)
	movx	a,@dptr
	cjne	a,#'0',00701$
	mov	a,#' '
	movx	@dptr,a
00701$:	mov	r7,#43
	mov	r5,#14
	ljmp	mtext

; A as two digits at DPTR, then DPTR past them. Changes B.
dig2:	mov	b,#10
	div	ab
	add	a,#'0'
	movx	@dptr,a
	inc	dptr
	mov	a,b
	add	a,#'0'
	movx	@dptr,a
	inc	dptr
	ret

; r7:r6 = dbm with its sign bit flipped (u)
ldu:	mov	dptr,#_dbm
	movx	a,@dptr
	mov	r6,a
	inc	dptr
	movx	a,@dptr
	xrl	a,#0x80
	mov	r7,a
	ret

; bar_x(A): d + (uint8_t)(d * 3) / 5, the bar's x for dBm + 130 (d * 8 / 5). Changes B, R4.
barx:	mov	r4,a
	mov	b,#3
	mul	ab
	mov	b,#5
	div	ab
	add	a,r4
	ret

; r3:r2 = the 16 bits at dptr; and back
; half: R3:R2 = the 16 bits at DPTR, each 565 field halved
half:	lcall	rd2
	clr	c
	mov	a,r3
	rrc	a
	anl	a,#0x7B
	mov	r3,a
	mov	a,r2
	rrc	a
	anl	a,#0xEF
	mov	r2,a
	ret
rd2:	movx	a,@dptr
	mov	r2,a
	inc	dptr
	movx	a,@dptr
	mov	r3,a
	ret
wr2:	mov	a,r2
	movx	@dptr,a
	inc	dptr
	mov	a,r3
	movx	@dptr,a
	ret

; pad_to(dptr, 13): spaces up to buf + 13, then the NUL
pad13:	mov	a,dpl
	clr	c
	subb	a,#_buf
	add	a,#-13
	jc	00401$
	mov	a,#' '
	movx	@dptr,a
	inc	dptr
	sjmp	pad13
00401$:	clr	a
	movx	@dptr,a
	ret

; m_fill(r7 = a, r6 = b, r5 = y): the bar from x a to b, filled up to m_len; one row above the text
; (y < METER_Y), else the 8 text rows.   r4 = m, the fill's end; r3 = h
mfill:	mov	dptr,#_m_len
	movx	a,@dptr
	mov	r4,a
	clr	c
	subb	a,r7
	jnc	00501$
	mov	ar4,r7			; m_len < a: m = a
	sjmp	00502$
00501$:	mov	a,r6
	clr	c
	subb	a,r4
	jnc	00502$
	mov	ar4,r6			; m_len > b: m = b
00502$:	mov	r3,#8
	cjne	r5,#METER_Y,00503$
00503$:	jnc	00504$
	mov	r3,#1
; if (m > a) lcd_fill(a, y, m - a, h, m_col);
00504$:	mov	a,r4
	clr	c
	subb	a,r7
	jc	00505$
	jz	00505$
	mov	r2,a
	push	ar3
	push	ar4
	push	ar5
	push	ar6
	mov	dptr,#_m_col
	lcall	00510$
	pop	ar6
	pop	ar5
	pop	ar4
	pop	ar3
; if (b > m) lcd_fill(m, y, b - m, h, m_trk);
00505$:	mov	a,r6
	clr	c
	subb	a,r4
	jc	00506$
	jz	00506$
	mov	r2,a
	mov	ar7,r4
	mov	dptr,#_m_trk
	sjmp	00510$
00506$:	ret
; lcd_fill(r7, r5, r2, r3, the colour at dptr)
00510$:	movx	a,@dptr
	push	acc
	inc	dptr
	movx	a,@dptr
	push	acc
	push	ar3
	push	ar2
	push	ar5
	mov	dpl,r7
	lcall	_lcd_fill
	mov	a,sp
	add	a,#-5
	mov	sp,a
	ret

; m_text(r7 = x, buf + r5): text inside the bar, c_bg over the filled part and m_fg on the track, as
; two runs (a character takes the side its middle is on); an edge inside the text moves to the split.
;   r4 = m_len, r3 = q (buf offset), r6 = e (its x), r2 = c = *q
mtext:	mov	dptr,#_m_len
	movx	a,@dptr
	mov	r4,a
	mov	ar6,r7
	mov	ar3,r5
; while (*q && (uint8_t)(e + 3) < m_len) { q++; e += 6; }
00601$:	lcall	00620$
	movx	a,@dptr
	mov	r2,a
	jz	00602$
	mov	a,r6
	add	a,#3
	clr	c
	subb	a,r4
	jnc	00602$
	inc	r3
	mov	a,r6
	add	a,#6
	mov	r6,a
	sjmp	00601$
; if (c && m_len > x) m_len = e;
00602$:	mov	a,r2
	jz	00603$
	mov	a,r7
	clr	c
	subb	a,r4
	jnc	00603$
	mov	dptr,#_m_len
	mov	a,r6
	movx	@dptr,a
; *q = 0; if (q != p) lcd_text_bg(x, METER_Y, p, 1, c_bg, m_col); *q = c;
00603$:	lcall	00620$
	clr	a
	movx	@dptr,a
	mov	a,r3
	xrl	a,r5
	jz	00604$
	push	ar2
	push	ar3
	push	ar6
	mov	dptr,#(_pal + 2 * P_BG)	; c_bg on m_col
	mov	r0,#0
	lcall	00610$
	pop	ar6
	pop	ar3
	pop	ar2
00604$:	lcall	00620$
	mov	a,r2
	movx	@dptr,a
; if (c) lcd_text_bg(e, METER_Y, q, 1, m_fg, m_trk);
	jz	00605$
	mov	ar7,r6
	mov	ar5,r3
	mov	dptr,#_m_fg
	mov	r0,#1
	sjmp	00610$
00605$:	ret
; lcd_text_bg(r7, METER_Y, buf + r5, 1, the colour at dptr, m_col (r0 = 0) or m_trk (r0 = 1))
00610$:	lcall	rd2
	mov	dptr,#_lcd_text_bg_PARM_5
	lcall	wr2
	mov	dptr,#_m_col
	mov	a,r0
	jz	00611$
	mov	dptr,#_m_trk
00611$:	lcall	rd2
	mov	dptr,#_lcd_text_bg_PARM_6
	lcall	wr2
	mov	dptr,#_lcd_text_bg_PARM_2
	mov	a,#METER_Y
	movx	@dptr,a
	mov	dptr,#_lcd_text_bg_PARM_4
	mov	a,#1
	movx	@dptr,a
	mov	dptr,#_lcd_text_bg_PARM_3
	mov	a,r5
	add	a,#_buf
	movx	@dptr,a
	inc	dptr
	clr	a
	addc	a,#(_buf >> 8)
	movx	@dptr,a
	inc	dptr
	mov	a,#GPTR_XDATA
	movx	@dptr,a
	mov	dpl,r7
	ljmp	_lcd_text_bg
; dptr = buf + r3
00620$:	mov	a,r3
	add	a,#_buf
	mov	dpl,a
	clr	a
	addc	a,#(_buf >> 8)
	mov	dph,a
	ret
