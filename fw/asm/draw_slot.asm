; asm-for: main.c draw_slot
; c-sha256: 6a4e624c28bcc9a0
; draw_slot: slot A (i 0, y 14) or B (i 1, y 73): letter and name, memory number, the frequency at 2x
; (only the digits that changed on a fast redraw), USB's small 10 Hz digits, the mode/power/shift/
; bandwidth row and the tones/step row (or DTMF digits, or the tone heard). main.c has the C original.
; void draw_slot(uint8_t i) __reentrant
; i in DPL; i is 0 or 1 (the C's 1 << i and y as for those). Every register, B and DPTR are the
; caller's to lose. The text pointer p is DPTR (as in the C, built in buf).
;
; The frame, pushed first:  sp-4 i, sp-3 y, sp-2 fc (the frequency's colour), sp-1 tc (the name's),
; sp fo (CHAN_TX or CHAN_RX: the frequency shown). FR n reads frame byte n with nothing else pushed.

	.module draw_slot
	.optsdcc -mmcs51 --model-large
	.include "../build/asm.inc"	; layout and constants from the C (tools/asmcheck.py inc)

	.globl _draw_slot
	.globl _cfg
	.globl _peek
	.globl _sel
	.globl _live
	.globl _tx
	.globl _open
	.globl _fast
	.globl _wxm
	.globl _ch
	.globl _main_ch
	.globl _other
	.globl _dc
	.globl _pal
	.globl _buf
	.globl _tinted
	.globl _small
	.globl _shown
	.globl _shown_c
	.globl _dtmf
	.globl _dtmf_n
	.globl _heard
	.globl _mode_s
	.globl _pow_d
	.globl _step_s
	.globl _sstep_s
	.globl _xcpy
	.globl _theme_c
	.globl _theme_apply
	.globl _text_bg
	.globl _lcd_text
	.globl _chan_in_scan
	.globl _num
	.globl _pad_to
	.globl _xcat
	.globl _dec
	.globl _tx_allowed
	.globl _right
	.globl _tone_s
	.globl icat
	.globl ccat
	.globl fillr
	.globl text1r
	.globl push4

FI = 4
FY = 3
FC = 2
TC = 1
FO = 0

	.macro	FR n
	mov	a,#-(n)
	add	a,sp
	mov	r0,a
	mov	a,@r0
	.endm

	.area CSEG    (CODE)
_draw_slot:
	mov	a,dpl
	mov	r7,a
	push	acc			; i
	mov	a,#14			; y = i ? 73 : 14
	cjne	r7,#0,00001$
	sjmp	00002$
00001$:	mov	a,#73
00002$:	push	acc			; y
; sel = i == cfg.act; live = peek ? peek == PEEK_OTHER && !sel : sel;
	mov	dptr,#(_cfg + CFG_ACT)
	movx	a,@dptr
	xrl	a,r7
	clr	c
	jnz	00003$
	setb	c
00003$:	mov	_sel,c
	mov	a,_peek
	jz	00005$
	cjne	a,#PEEK_OTHER,00004$
	mov	c,_sel
	cpl	c
	sjmp	00006$
00004$:	clr	c
	sjmp	00006$
00005$:	mov	c,_sel
00006$:	mov	_live,c
; xcpy(&dc, live ? &ch : sel ? &main_ch : &other, sizeof dc);
	mov	a,#CHAN_SIZE
	push	acc
	mov	r6,#_ch
	mov	r7,#(_ch >> 8)
	jb	_live,00007$
	mov	r6,#_main_ch
	mov	r7,#(_main_ch >> 8)
	jb	_sel,00007$
	mov	r6,#_other
	mov	r7,#(_other >> 8)
00007$:	push	ar6
	push	ar7
	mov	dptr,#_dc
	lcall	_xcpy
	dec	sp
	dec	sp
	dec	sp
; s = !live ? 0 : tx ? T_TX : open ? T_RX : 0;   r5 = s, the tint
	mov	r5,#0
	jnb	_live,00008$
	mov	r5,#T_TX
	jb	_tx,00008$
	mov	r5,#T_RX
	jb	_open,00008$
	mov	r5,#0
; if (s) { pal[0] = theme_c(s); c_dim = c_fg; }   dim grey is lost on a tint
00008$:	mov	a,r5
	jz	00009$
	push	ar5
	mov	dpl,a
	lcall	_theme_c
	mov	r2,dpl
	mov	r3,dph
	mov	dptr,#(_pal + 2 * P_BG)
	lcall	wr2
	mov	a,#P_FG
	lcall	palrd
	mov	dptr,#(_pal + 2 * P_DIM)
	lcall	wr2
	pop	ar5
; fc = s == T_TX ? P_RED : s ? P_OK : sel ? P_FG : P_DIM; tc = sel ? P_FG : P_DIM;
00009$:	mov	a,#P_RED
	cjne	r5,#T_TX,00010$
	sjmp	00012$
00010$:	mov	a,#P_OK
	cjne	r5,#0,00012$
	mov	a,#P_FG
	jb	_sel,00012$
	mov	a,#P_DIM
00012$:	push	acc			; fc
	mov	a,#P_FG
	jb	_sel,00013$
	mov	a,#P_DIM
00013$:	push	acc			; tc
	mov	a,#CHAN_RX		; the frequency shown: live && tx ? dc.tx : dc.rx
	jnb	_live,00014$
	jnb	_tx,00014$
	mov	a,#CHAN_TX
00014$:	push	acc			; fo

; lcd_fillp(0, y - 1, W, !fast && (s | tinted[i]) ? 45 : 9, P_BG); tinted[i] = s;
	FR	FI
	add	a,#_tinted
	mov	dpl,a
	clr	a
	addc	a,#(_tinted >> 8)
	mov	dph,a
	movx	a,@dptr
	orl	a,r5
	mov	r4,#9
	jz	00015$
	jb	_fast,00015$
	mov	r4,#45			; the whole block: the tint comes or goes
00015$:	mov	a,r5
	movx	@dptr,a
	FR	FY
	dec	a
	mov	r6,a
	mov	r7,#0
	mov	r5,#W
	mov	r3,#P_BG
	lcall	fillr
; if (sel) lcd_fillp(0, y - 1, 8, 9, P_ACC);
	jnb	_sel,00016$
	FR	FY
	dec	a
	mov	r6,a
	mov	r7,#0
	mov	r5,#8
	mov	r4,#9
	mov	r3,#P_ACC
	lcall	fillr
; buf[0] = 'A' + i; buf[1] = 0; text_bg(1, y, sel ? P_BG : P_DIM, sel ? P_ACC : P_BG);
00016$:	FR	FI
	add	a,#'A'
	mov	dptr,#_buf
	movx	@dptr,a
	inc	dptr
	clr	a
	movx	@dptr,a
	mov	r6,#P_BG
	mov	r7,#P_ACC
	jb	_sel,00017$
	mov	r6,#P_DIM
	mov	r7,#P_BG
00017$:	push	ar7
	push	ar6
	FR	FY+2
	push	acc
	mov	dpl,#1
	lcall	_text_bg
	dec	sp
	dec	sp
	dec	sp
; for (k = 0; dc.name[k]; k++) ; lcd_text(64 - 3 * k, y, dc.name, 1, tc);
	mov	dptr,#(_dc + CHAN_NAME)
	mov	r7,#0
00018$:	movx	a,@dptr
	jz	00019$
	inc	dptr
	inc	r7
	sjmp	00018$
00019$:	mov	a,r7
	mov	b,#3
	mul	ab
	cpl	a
	add	a,#65			; 64 - 3k
	mov	r7,a
	FR	FY
	mov	r6,a
	mov	r4,#(_dc + CHAN_NAME)
	mov	r5,#((_dc + CHAN_NAME) >> 8)
	mov	r3,#1
	FR	TC
	mov	r2,a
	lcall	ltext

; The memory number, 5 characters: "*M20 ", "M199 ", "VFO  ", "WX" in weather mode
	mov	dptr,#_buf
	jnb	_sel,00020$
	jnb	_wxm,00020$
	lcall	icat
	.asciz	"WX"
	sjmp	00023$
00020$:	FR	FI			; cfg.s[i].mem
	mov	b,#SLOT_SIZE
	mul	ab
	add	a,#(_cfg + CFG_S + SLOT_MEM)
	mov	dpl,a
	clr	a
	addc	a,#((_cfg + CFG_S + SLOT_MEM) >> 8)
	mov	dph,a
	movx	a,@dptr
	jz	00022$
	FR	FI			; k = cfg.s[i].chn
	mov	b,#SLOT_SIZE
	mul	ab
	add	a,#(_cfg + CFG_S + SLOT_CHN)
	mov	dpl,a
	clr	a
	addc	a,#((_cfg + CFG_S + SLOT_CHN) >> 8)
	mov	dph,a
	movx	a,@dptr
	mov	r7,a
	push	ar7
	mov	dpl,a
	lcall	_chan_in_scan
	mov	a,dpl
	pop	ar7
	mov	dptr,#_buf
	jz	00021$
	mov	a,#'*'
	movx	@dptr,a
	inc	dptr
00021$:	mov	a,#'M'
	movx	@dptr,a
	inc	dptr
	push	ar7
	lcall	_num
	dec	sp
	sjmp	00023$
00022$:	mov	dptr,#_buf
	lcall	icat
	.asciz	"VFO"
00023$:	mov	a,#5			; pad_to(p, 5); text1(0, y + 13, sel && wxm ? P_ACC : P_DIM);
	push	acc
	lcall	_pad_to
	dec	sp
	mov	r5,#P_DIM
	jnb	_sel,00024$
	jnb	_wxm,00024$
	mov	r5,#P_ACC
00024$:	FR	FY
	add	a,#13
	mov	r6,a
	mov	r7,#0
	lcall	text1r

; dec(buf, f, 8, 5): all 8 digits, once; buf[20..22] = buf[7], buf[8], 0 (the two below 1 kHz, for
; the small digits); buf[7] = 0 ends the big ones at 1 kHz. Then the digits from the first that changed
; (all unless fast and the colour is the same).
	mov	a,#5
	push	acc
	mov	a,#8
	push	acc
	FR	FO+2
	lcall	dcf_a
	lcall	push4
	mov	dptr,#_buf
	lcall	_dec
	mov	a,sp
	add	a,#-6
	mov	sp,a
	mov	dptr,#(_buf + 7)
	movx	a,@dptr
	mov	r2,a
	clr	a
	movx	@dptr,a
	inc	dptr
	movx	a,@dptr
	mov	dptr,#(_buf + 21)
	movx	@dptr,a
	inc	dptr
	clr	a
	movx	@dptr,a
	mov	dptr,#(_buf + 20)
	mov	a,r2
	movx	@dptr,a
; k = 0; if (fast && shown_c[i] == pal[fc]) while (k < 7 && buf[k] == shown[i][k]) k++;   r7 = k
	mov	r7,#0
	jnb	_fast,00030$
	FR	FC
	lcall	palrd			; r3:r2 = pal[fc]
	lcall	shc
	movx	a,@dptr
	xrl	a,r2
	jnz	00030$
	inc	dptr
	movx	a,@dptr
	xrl	a,r3
	jnz	00030$
00031$:	cjne	r7,#7,00032$
	sjmp	00030$
00032$:	mov	a,r7
	lcall	bufp
	movx	a,@dptr
	mov	r2,a
	FR	FI
	rl	a			; shown[i]: i * 8 (i < 32)
	rl	a
	rl	a
	add	a,r7
	add	a,#_shown
	mov	dpl,a
	clr	a
	addc	a,#(_shown >> 8)
	mov	dph,a
	movx	a,@dptr
	xrl	a,r2
	jnz	00030$
	inc	r7
	sjmp	00031$
; if (k < 7) lcd_text(32 + 12 * k, y + 9, buf + k, 2, fc);
00030$:	cjne	r7,#7,00033$
00033$:	jnc	00034$
	mov	a,r7
	add	a,#_buf
	mov	r4,a
	clr	a
	addc	a,#(_buf >> 8)
	mov	r5,a
	mov	a,r7
	mov	b,#12
	mul	ab
	add	a,#32
	mov	r7,a
	FR	FY
	add	a,#9
	mov	r6,a
	mov	r3,#2
	FR	FC
	mov	r2,a
	lcall	ltext
; xcat(shown[i], buf); shown_c[i] = pal[fc];   the colour, not the index: as drawn
00034$:	mov	a,#_buf
	push	acc
	mov	a,#(_buf >> 8)
	push	acc
	FR	FI+2
	rl	a
	rl	a
	rl	a
	add	a,#_shown
	mov	dpl,a
	clr	a
	addc	a,#(_shown >> 8)
	mov	dph,a
	lcall	_xcat
	dec	sp
	dec	sp
	FR	FC
	lcall	palrd
	lcall	shc
	lcall	wr2
; The digits below 1 kHz small after it, kept at buf + 20: USB shows both always, other modes only
; when there are any ("5 " for a 12.5 kHz channel); then small |= 1 << i. None: clear them once,
; small &= ~(1 << i).
	mov	dptr,#(_dc + CHAN_MODE)	; USB: both digits
	movx	a,@dptr
	cjne	a,#MOD_USB,00038$
	sjmp	00039$
00038$:	mov	dptr,#(_buf + 21)	; a 10 Hz digit: both
	movx	a,@dptr
	cjne	a,#0x30,00039$
	mov	dptr,#(_buf + 20)	; neither: none
	movx	a,@dptr
	cjne	a,#0x30,00090$
	sjmp	00036$
00090$:	mov	dptr,#(_buf + 21)	; the 100 Hz digit alone: buf[21] = ' '
	mov	a,#0x20
	movx	@dptr,a
00039$:	mov	r7,#116			; lcd_text(116, y + 17, buf + 7, 1, fc)
	FR	FY
	add	a,#17
	mov	r6,a
	mov	r4,#(_buf + 20)
	mov	r5,#((_buf + 20) >> 8)
	mov	r3,#1
	FR	FC
	mov	r2,a
	lcall	ltext
	lcall	kbit
	mov	r7,a
	mov	dptr,#_small
	movx	a,@dptr
	orl	a,r7
	movx	@dptr,a
	sjmp	00037$
00036$:	lcall	kbit
	mov	r7,a
	mov	dptr,#_small
	movx	a,@dptr
	anl	a,r7
	jz	00037$
	mov	a,r7
	cpl	a
	mov	r7,a
	movx	a,@dptr
	anl	a,r7
	movx	@dptr,a
	mov	r7,#116			; lcd_fillp(116, y + 17, 12, 8, P_BG)
	FR	FY
	add	a,#17
	mov	r6,a
	mov	r5,#12
	mov	r4,#8
	mov	r3,#P_BG
	lcall	fillr

; The mode, power, shift ... bandwidth row: p = cat(buf, mode_s[dc.mode]); *p++ = ' ';   r5 = dc.mode
00037$:	mov	dptr,#(_dc + CHAN_MODE)
	movx	a,@dptr
	mov	r5,a
	mov	dptr,#_mode_s
	lcall	tblp
	mov	dptr,#_buf
	lcall	ccat
	lcall	space
; if (dc.mode == MOD_USB) p = cat(p, "RXONLY");   receive only
	cjne	r5,#MOD_USB,00040$
	lcall	icat
	.asciz	"RXONLY"
	ljmp	00049$
; p = cat(p, pow_d[dc.power]);
00040$:	push	dpl
	push	dph
	mov	dptr,#(_dc + CHAN_POWER)
	movx	a,@dptr
	mov	dptr,#_pow_d
	lcall	tblp
	pop	dph
	pop	dpl
	lcall	ccat
; r7:r6:r5:r4 = dc.tx; if (!dc.tx) TXLOCK; else d = dc.tx - dc.rx in the same registers
00043$:	push	dpl
	push	dph
	mov	dptr,#(_dc + CHAN_TX)
	lcall	ld32
	mov	a,r4
	orl	a,r5
	orl	a,r6
	orl	a,r7
	jnz	00044$
	pop	dph
	pop	dpl
	lcall	icat
	.asciz	"TXLOCK"
	ljmp	00048$
00044$:	mov	dptr,#(_dc + CHAN_RX)
	clr	c
	movx	a,@dptr
	mov	b,a
	mov	a,r4
	subb	a,b
	mov	r4,a
	inc	dptr
	movx	a,@dptr
	mov	b,a
	mov	a,r5
	subb	a,b
	mov	r5,a
	inc	dptr
	movx	a,@dptr
	mov	b,a
	mov	a,r6
	subb	a,b
	mov	r6,a
	inc	dptr
	movx	a,@dptr
	mov	b,a
	mov	a,r7
	subb	a,b
	mov	r7,a
	orl	a,r6
	orl	a,r5
	orl	a,r4
	jz	00046$			; tx == rx
; a shift: "+0.6", "-5": the sign, then dec(p, |d|, 6, 5) - 2 with its trailing 0s and '.' dropped
	pop	dph
	pop	dpl
	mov	a,#'+'
	mov	b,a
	mov	a,r7
	jnb	acc.7,00045$		; both under 2^31: the sign bit is the shift's
	mov	b,#'-'
	clr	c
	clr	a
	subb	a,r4
	mov	r4,a
	clr	a
	subb	a,r5
	mov	r5,a
	clr	a
	subb	a,r6
	mov	r6,a
	clr	a
	subb	a,r7
	mov	r7,a
00045$:	mov	a,b
	movx	@dptr,a
	inc	dptr
	mov	a,#5
	push	acc
	mov	a,#6
	push	acc
	push	ar4
	push	ar5
	push	ar6
	push	ar7
	lcall	_dec
	mov	a,sp
	add	a,#-6
	mov	sp,a
	lcall	ddec			; p = end - 2: MHz to 10 Hz, its last two dropped (d / 100 without a long divide)
	lcall	ddec
00047$:	lcall	ddec			; while (p[-1] == '0') p--;   dptr = p - 1
	movx	a,@dptr
	cjne	a,#'0',00050$
	sjmp	00047$
00050$:	cjne	a,#'.',00051$		; if (p[-1] == '.') p--;
	sjmp	00048$
00051$:	inc	dptr
	sjmp	00048$
; else if (!tx_allowed(dc.tx)) p = cat(p, "RXONLY");
00046$:	mov	dptr,#(_dc + CHAN_TX)
	lcall	ld32
	mov	dpl,r4
	mov	dph,r5
	mov	b,r6
	mov	a,r7
	lcall	_tx_allowed
	mov	a,dpl
	pop	dph
	pop	dpl
	jnz	00048$
	lcall	icat
	.asciz	"RXONLY"
; right(p, dc.narrow ? "NARR" : "WIDE", "");
00048$:	push	dpl
	push	dph
	mov	dptr,#(_dc + CHAN_NARROW)
	movx	a,@dptr
	pop	dph
	pop	dpl
	mov	r6,#s_wide
	mov	r7,#(s_wide >> 8)
	jz	00052$
	mov	r6,#s_narr
	mov	r7,#(s_narr >> 8)
00052$:	mov	a,#s_none
	push	acc
	mov	a,#(s_none >> 8)
	push	acc
	push	ar6
	push	ar7
	lcall	_right
	mov	a,sp
	add	a,#-4
	mov	sp,a
00049$:	FR	FY			; text1(0, y + 27, P_DIM);
	add	a,#27
	mov	r6,a
	mov	r7,#0
	mov	r5,#P_DIM
	lcall	text1r

; The tones ... step row; DTMF digits or the tone heard take it over. Each ends with A = its colour.
	mov	dptr,#_buf
	jnb	_live,00060$
	mov	a,_dtmf_n		; if (live && dtmf_n >= DTMF_RUN) { cat(buf, "DTMF "); p = xcat(buf + 5, dtmf); }
	cjne	a,#DTMF_RUN,00061$
00061$:	jc	00060$
	lcall	icat
	.asciz	"DTMF "
	mov	a,#_dtmf
	push	acc
	mov	a,#(_dtmf >> 8)
	push	acc
	lcall	_xcat
	dec	sp
	dec	sp
	mov	a,#P_HI
	ljmp	00069$
; else if (live && heard && open) { cat(buf, "HEARD "); p = cat(tone_s(buf + 6, heard), " 6:copy"); }
00060$:	jnb	_live,00062$
	jnb	_open,00062$
	mov	dptr,#_heard
	movx	a,@dptr
	mov	r4,a
	inc	dptr
	movx	a,@dptr
	mov	r5,a
	orl	a,r4
	jz	00062$
	mov	dptr,#_buf
	lcall	icat
	.asciz	"HEARD "
	push	ar4
	push	ar5
	lcall	_tone_s
	dec	sp
	dec	sp
	lcall	icat
	.asciz	" 6:copy"
	mov	a,#P_OK
	ljmp	00069$
; else: "TSQL 100.0" when both tones match, else "T100.0 R88.5", then the step at the right
00062$:	mov	dptr,#(_dc + CHAN_TXT)	; r5:r4 = tx_tone, r3:r2 = rx_tone
	movx	a,@dptr
	mov	r4,a
	inc	dptr
	movx	a,@dptr
	mov	r5,a
	mov	dptr,#(_dc + CHAN_RXT)
	movx	a,@dptr
	mov	r2,a
	inc	dptr
	movx	a,@dptr
	mov	r3,a
	mov	dptr,#_buf
	mov	a,r4
	orl	a,r5
	jz	00064$
	mov	a,r4
	xrl	a,r2
	jnz	00063$
	mov	a,r5
	xrl	a,r3
	jnz	00063$
	lcall	icat
	.asciz	"TSQL"
	push	ar4
	push	ar5
	lcall	_tone_s
	dec	sp
	dec	sp
	sjmp	00066$
00063$:	mov	a,#'T'			; if (dc.tx_tone) { buf[0] = 'T'; p = tone_s(buf + 1, dc.tx_tone); *p++ = ' '; }
	movx	@dptr,a
	inc	dptr
	push	ar2
	push	ar3
	push	ar4
	push	ar5
	lcall	_tone_s
	dec	sp
	dec	sp
	pop	ar3
	pop	ar2
	lcall	space
00064$:	mov	a,r2			; if (dc.rx_tone) { *p++ = 'R'; p = tone_s(p, dc.rx_tone); }
	orl	a,r3
	jz	00066$
	mov	a,#'R'
	movx	@dptr,a
	inc	dptr
	push	ar2
	push	ar3
	lcall	_tone_s
	dec	sp
	dec	sp
; right(p, dc.mode == MOD_USB ? sstep_s[cfg.sstep] : step_s[cfg.step], "K"); p = buf + 21;
00066$:	push	dpl
	push	dph
	mov	dptr,#(_dc + CHAN_MODE)
	movx	a,@dptr
	cjne	a,#MOD_USB,00067$
	mov	dptr,#(_cfg + CFG_SSTEP)
	movx	a,@dptr
	anl	a,#CFG_SSTEP_MASK
	.rept	CFG_SSTEP_BIT
	rr	a
	.endm
	mov	dptr,#_sstep_s
	sjmp	00068$
00067$:	mov	dptr,#(_cfg + CFG_STEP)
	movx	a,@dptr
	anl	a,#CFG_STEP_MASK
	.rept	CFG_STEP_BIT
	rr	a
	.endm
	mov	dptr,#_step_s
00068$:	lcall	tblp
	pop	dph
	pop	dpl
	mov	a,#s_k
	push	acc
	mov	a,#(s_k >> 8)
	push	acc
	push	ar6
	push	ar7
	lcall	_right
	mov	a,sp
	add	a,#-4
	mov	sp,a
	mov	dptr,#(_buf + 21)
	mov	a,#P_DIM
; pad_to(p, 21); text1(0, y + 36, that colour); theme_apply();   c_bg and c_dim back
00069$:	push	acc
	mov	a,#21
	push	acc
	lcall	_pad_to
	dec	sp
	pop	ar5
	FR	FY
	add	a,#36
	mov	r6,a
	mov	r7,#0
	lcall	text1r
	lcall	_theme_apply
	mov	a,sp
	add	a,#-5
	mov	sp,a
	ret

; ---- helpers (frame offsets in them are +2: their return address) ----
; lcd_text(r7, r6, the XDATA text at r5:r4, scale r3, colour r2)
ltext:	push	ar2
	push	ar3
	push	ar4
	push	ar5
	mov	a,#GPTR_XDATA
	push	acc
	push	ar6
	mov	dpl,r7
	lcall	_lcd_text
	mov	a,sp
	add	a,#-6
	mov	sp,a
	ret
; dptr = &dc + fo: the frequency shown (dcf_a: fo in A)
dcf:	FR	FO+2
dcf_a:	add	a,#_dc
	mov	dpl,a
	clr	a
	addc	a,#(_dc >> 8)
	mov	dph,a
	ret
; dptr = &shown_c[i]
shc:	FR	FI+2
	add	a,acc
	add	a,#_shown_c
	mov	dpl,a
	clr	a
	addc	a,#(_shown_c >> 8)
	mov	dph,a
	ret
; A = 1 << i, as a uint8_t
kbit:	FR	FI+2
	mov	r0,a
	inc	r0
	mov	a,#1
	sjmp	00102$
00101$:	add	a,acc
00102$:	djnz	r0,00101$
	ret
; r3:r2 = pal[A]
palrd:	add	a,acc
	add	a,#_pal
	mov	dpl,a
	clr	a
	addc	a,#(_pal >> 8)
	mov	dph,a
	movx	a,@dptr
	mov	r2,a
	inc	dptr
	movx	a,@dptr
	mov	r3,a
	ret
; the 16 bits r3:r2 to dptr
wr2:	mov	a,r2
	movx	@dptr,a
	inc	dptr
	mov	a,r3
	movx	@dptr,a
	ret
; r7:r6:r5:r4 = the 32 bits at dptr
ld32:	movx	a,@dptr
	mov	r4,a
	inc	dptr
	movx	a,@dptr
	mov	r5,a
	inc	dptr
	movx	a,@dptr
	mov	r6,a
	inc	dptr
	movx	a,@dptr
	mov	r7,a
	ret
; dptr = buf + A
bufp:	add	a,#_buf
	mov	dpl,a
	clr	a
	addc	a,#(_buf >> 8)
	mov	dph,a
	ret
; dptr--
ddec:	mov	a,dpl
	jnz	00103$
	dec	dph
00103$:	dec	dpl
	ret
; *p++ = ' '
space:	mov	a,#' '
	movx	@dptr,a
	inc	dptr
	ret
; r7:r6 = the code string the table at dptr holds at index A
tblp:	add	a,acc
	mov	r6,a
	movc	a,@a+dptr
	xch	a,r6
	inc	a
	movc	a,@a+dptr
	mov	r7,a
	ret

s_narr:	.asciz	"NARR"
s_wide:	.asciz	"WIDE"
s_k:	.ascii	"K"
s_none:	.db	0
