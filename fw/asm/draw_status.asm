; asm-for: main.c draw_status
; c-sha256: bed0db8d8ab56b8f
; draw_status: the status bar (squelch, scan, flags, flashlight, icons, battery) and the bottom line
; (a message, or what's being typed). main.c has the C original.
; void draw_status(void) __reentrant
; Every register, B and DPTR are the caller's to lose. Text is built in buf with DPTR as the C's p;
; the helpers (asmlib.asm) keep R3-R5 and call through the C conventions.
;
; cfg's fields are asm.inc's CFG_* (offsets, and for the bitfields _BIT).
; The frame from the battery on: sp-2 pct, sp-1 n (the icons' x), sp dim (the battery's colour).

	.module draw_status
	.optsdcc -mmcs51 --model-large
	.include "../build/asm.inc"	; layout and constants from the C (tools/asmcheck.py inc)

	.globl _draw_status
	.globl _note
	.globl _saving
	.globl _entry_n
	.globl _entry
	.globl _overwrite
	.globl _scanning
	.globl _mon
	.globl _rev
	.globl _peek
	.globl _torch
	.globl _tlevel
	.globl _locked
	.globl _rang
	.globl _cfg
	.globl _buf
	.globl _icon_warn
	.globl _icon_lock
	.globl _icon_bt
	.globl _icon_bell
	.globl _draw_bottom
	.globl _dt_line
	.globl _dt_mem
	.globl _dtmf_n
	.globl _sl_mem
	.globl _u2s
	.globl _level_s
	.globl _groups_s
	.globl _num
	.globl _batt_cal
	.globl _batt_pct
	.globl icat
	.globl nthcat
	.globl fillr
	.globl text1r
	.globl iconr


	.area CSEG    (CODE)
_draw_status:
; if (tx && (dtmf_n || dt_mem)) dt_line();   the bottom line while sending DTMF
	jnb	_tx,00009$
	mov	a,_dtmf_n
	jnz	00008$
	mov	dptr,#_dt_mem
	movx	a,@dptr
	jz	00009$
00008$:	lcall	_dt_line
	ljmp	00030$
; else if (note) { draw_bottom(note, P_RED); lcd_icon(120, BOT_Y, icon_warn, P_RED); }
00009$:	mov	a,_note
	orl	a,(_note + 1)
	jz	00010$
	mov	a,#P_RED
	push	acc
	mov	dpl,_note
	mov	dph,(_note + 1)
	mov	b,#GPTR_CODE
	lcall	_draw_bottom
	dec	sp
	mov	r7,#120
	mov	r6,#BOT_Y
	mov	dptr,#_icon_warn
	mov	r5,#P_RED
	lcall	iconr
	ljmp	00030$
; else if (saving || entry_n): "SAVE CH 005? MENU=YES", "CHANNEL 12", "FREQ 146.52"
00010$:	mov	a,_saving
	orl	a,_entry_n
	jnz	00011$
	mov	r7,#0			; else lcd_fillp(0, BOT_Y, W, 8, P_BG)
	mov	r6,#BOT_Y
	mov	r5,#W
	mov	r4,#8
	mov	r3,#P_BG
	lcall	fillr
	ljmp	00030$
; p = cat(buf, overwrite ? "REPLACE " : saving ? "SAVE CH " : sl_mem() ? "CHANNEL " : "FREQ ");
00011$:	jb	_overwrite,00012$
	mov	a,_saving
	jnz	00013$
	lcall	_sl_mem
	mov	a,dpl
	mov	dptr,#_buf
	jz	00014$
	lcall	icat
	.asciz	"CHANNEL "
	sjmp	00015$
00014$:	lcall	icat
	.asciz	"FREQ "
	sjmp	00015$
00012$:	mov	dptr,#_buf
	lcall	icat
	.asciz	"REPLACE "
	sjmp	00015$
00013$:	mov	dptr,#_buf
	lcall	icat
	.asciz	"SAVE CH "
; if (saving && !entry_n) p = u2s(p, saving, 3) + 3;
00015$:	mov	a,_saving
	jz	00016$
	mov	a,_entry_n
	jnz	00016$
	mov	a,#3
	push	acc			; width
	push	_saving			; v, low then high
	clr	a
	push	acc
	lcall	_u2s			; returns p, the digits' start
	mov	a,sp
	add	a,#-3
	mov	sp,a
	inc	dptr
	inc	dptr
	inc	dptr
; for (i = 0; i < entry_n; i++) { if (i == 3 && !sl_mem()) *p++ = '.'; *p++ = entry[i]; }   r7 = i
00016$:	mov	r7,#0
00017$:	mov	a,r7
	cjne	a,_entry_n,00018$
	sjmp	00020$
00018$:	cjne	r7,#3,00019$
	push	ar7
	push	dpl
	push	dph
	lcall	_sl_mem
	mov	a,dpl
	pop	dph
	pop	dpl
	pop	ar7
	jnz	00019$
	mov	a,#'.'
	movx	@dptr,a
	inc	dptr
00019$:	push	dpl
	push	dph
	mov	a,r7
	add	a,#_entry
	mov	dpl,a
	clr	a
	addc	a,#(_entry >> 8)
	mov	dph,a
	movx	a,@dptr			; entry[i]
	pop	dph
	pop	dpl
	movx	@dptr,a
	inc	dptr
	inc	r7
	sjmp	00017$
; cat(p, saving ? "? MENU=YES" : ""); draw_bottom(buf, overwrite ? P_RED : P_HI);
00020$:	mov	a,_saving
	jz	00021$
	lcall	icat
	.asciz	"? MENU=YES"
	sjmp	00022$
00021$:	clr	a
	movx	@dptr,a
00022$:	mov	a,#P_HI
	jnb	_overwrite,00023$
	mov	a,#P_RED
00023$:	push	acc
	mov	dptr,#_buf
	mov	b,#GPTR_XDATA
	lcall	_draw_bottom
	dec	sp

; The status bar. p = level_s(cat(buf, "SQ"), cfg.sql); *p++ = ' ';   cfg.sql pushed first: level_s's argument
00030$:	mov	dptr,#(_cfg + CFG_SQL)
	movx	a,@dptr
	push	acc
	mov	dptr,#_buf
	lcall	icat
	.ascii	"SQ"
	.db	0
	lcall	_level_s
	dec	sp
	lcall	00090$
; if (scanning) { p = cat(p, "SCAN"); if (sl_mem() && cfg.group) { *p++ = ' '; p = groups_s(p, 0); } *p++ = ' '; }
	jnb	_scanning,00040$
	lcall	icat
	.asciz	"SCAN"
	push	dpl
	push	dph
	lcall	_sl_mem
	mov	a,dpl
	jz	00031$
	mov	dptr,#(_cfg + CFG_GROUP)
	movx	a,@dptr
00031$:	pop	dph
	pop	dpl
	jz	00032$
	lcall	00090$
	clr	a
	push	acc			; word 0: "G13"
	lcall	_groups_s
	dec	sp
00032$:	lcall	00090$
; The flags, each with its space: "DW ", "PRI ", "WX ", "MON ", "REV ", "ATT ", "VOX "; "DW! ", "PRI! "
; while that one peeks.   r5 = the byte with att and dw, r4 = prio, r3 = wx, r2 = vox
00040$:	push	dpl
	push	dph
	mov	dptr,#(_cfg + CFG_DW)	; att and dw share it
	movx	a,@dptr
	mov	r5,a
	mov	dptr,#(_cfg + CFG_PRIO)
	movx	a,@dptr
	mov	r4,a
	mov	dptr,#(_cfg + CFG_WX)
	movx	a,@dptr
	mov	r3,a
	mov	dptr,#(_cfg + CFG_VOX)
	movx	a,@dptr
	anl	a,#CFG_VOX_MASK
	mov	r2,a
	pop	dph
	pop	dpl
	mov	a,r5
	jnb	CFG_DW_ACC,00041$
	lcall	icat
	.asciz	"DW"
	mov	a,#PEEK_OTHER
	lcall	00091$
00041$:	mov	a,r4
	jz	00042$
	lcall	icat
	.asciz	"PRI"
	mov	a,#PEEK_PRIO
	lcall	00091$
00042$:	mov	a,r3
	jz	00043$
	lcall	icat
	.asciz	"WX "
00043$:	jnb	_mon,00044$
	lcall	icat
	.asciz	"MON "
00044$:	jnb	_rev,00045$
	lcall	icat
	.asciz	"REV "
00045$:	mov	a,r5
	jnb	CFG_ATT_ACC,00046$
	lcall	icat
	.asciz	"ATT "
00046$:	mov	a,r2
	jz	00056$
	lcall	icat
	.asciz	"VOX "
; n = torch; if (n == 1 && tlevel) n = 3 + tlevel; p = cat(p, torch_s[n]); p[-1] = 0;
00056$:	push	dpl
	push	dph
	mov	dptr,#_torch
	movx	a,@dptr
	pop	dph
	pop	dpl
	cjne	a,#1,00047$
	mov	a,_tlevel
	jz	00048$
	add	a,#3
	sjmp	00047$
00048$:	mov	a,#1
00047$:	mov	r7,#(00099$ >> 8)
	mov	r6,#00099$
	lcall	nthcat
	mov	a,dpl			; every item ends in a space: drop the last
	jnz	00049$
	dec	dph
00049$:	dec	dpl
	clr	a
	movx	@dptr,a

; pct = batt_pct(batt_cal()); n = 86 - 9 * (locked + cfg.bt + rang); buf[n / 6] = 0; dim
	lcall	_batt_cal
	lcall	_batt_pct
	push	dpl			; pct
	mov	dptr,#(_cfg + CFG_BT)
	movx	a,@dptr
	mov	c,CFG_BT_ACC
	clr	a
	addc	a,#0			; cfg.bt
	jnb	_locked,00050$
	inc	a
00050$:	jnb	_rang,00051$
	inc	a
00051$:	mov	b,#9
	mul	ab
	cpl	a
	add	a,#87			; 86 - 9k
	push	acc			; n
	mov	b,#6
	div	ab
	add	a,#_buf
	mov	dpl,a
	clr	a
	addc	a,#(_buf >> 8)
	mov	dph,a
	clr	a
	movx	@dptr,a			; text stops left of the icons
	mov	a,sp
	dec	a
	mov	r0,a
	mov	a,@r0			; pct
	add	a,#-21			; C: pct > 20
	mov	a,#P_DIM
	jc	00052$
	mov	a,#P_RED
00052$:	push	acc			; dim
; lcd_fillp(0, 0, W, 9, P_BG); text1(0, 0, P_ACC);
	mov	r7,#0
	mov	r6,#0
	mov	r5,#W
	mov	r4,#9
	mov	r3,#P_BG
	lcall	fillr
	mov	r7,#0
	mov	r6,#0
	mov	r5,#P_ACC
	lcall	text1r
; the icons at n, each moving it 9 on: lock (y 1; red when KEY LOCK stops PTT too), Bluetooth, bell
	jnb	_locked,00053$
	mov	dptr,#(_cfg + CFG_KLOCK)
	movx	a,@dptr
	mov	r5,#P_ACC
	jnb	CFG_KLOCK_ACC,00057$
	mov	r5,#P_RED
00057$:	mov	r6,#1
	mov	dptr,#_icon_lock
	lcall	00092$
00053$:	mov	dptr,#(_cfg + CFG_BT)
	movx	a,@dptr
	jnb	CFG_BT_ACC,00054$
	mov	r6,#0
	mov	dptr,#_icon_bt
	mov	r5,#P_ACC
	lcall	00092$
00054$:	jnb	_rang,00055$
	mov	r6,#0
	mov	dptr,#_icon_bell
	mov	r5,#P_ACC
	lcall	00092$
; n = num(buf, pct) - buf; buf[n] = '%'; buf[n + 1] = 0; text1(104 - 6 * n, 0, dim);
00055$:	mov	a,sp
	add	a,#-2
	mov	r0,a
	mov	a,@r0
	push	acc			; pct
	mov	dptr,#_buf
	lcall	_num
	dec	sp
	mov	a,dpl
	clr	c
	subb	a,#_buf			; n
	mov	b,#6
	mul	ab
	cpl	a
	add	a,#105			; 104 - 6n
	mov	r7,a
	mov	a,#'%'
	movx	@dptr,a
	inc	dptr
	clr	a
	movx	@dptr,a
	mov	r6,#0
	mov	r0,sp
	mov	ar5,@r0			; dim
	lcall	text1r
; the battery: a 12x7 case and its tip, filled in 4 steps
	mov	r0,sp
	mov	ar3,@r0
	mov	r7,#112
	mov	r6,#1
	mov	r5,#12
	mov	r4,#7
	lcall	fillr			; lcd_fillp(112, 1, 12, 7, dim)
	mov	r7,#113
	mov	r6,#2
	mov	r5,#10
	mov	r4,#5
	mov	r3,#P_BG
	lcall	fillr			; lcd_fillp(113, 2, 10, 5, P_BG)
	mov	r0,sp
	mov	ar3,@r0
	mov	r7,#124
	mov	r6,#3
	mov	r5,#2
	mov	r4,#3
	lcall	fillr			; lcd_fillp(124, 3, 2, 3, dim)
	mov	a,sp
	add	a,#-2
	mov	r0,a
	mov	a,@r0
	add	a,#12
	mov	b,#25
	div	ab
	add	a,acc
	mov	r5,a			; (pct + 12) / 25 * 2
	mov	r0,sp
	mov	ar3,@r0
	mov	r7,#114
	mov	r6,#3
	mov	r4,#3
	lcall	fillr			; lcd_fillp(114, 3, that, 3, dim)
	dec	sp
	dec	sp
	dec	sp
	ret

; *p++ = ' '
00090$:	mov	a,#' '
	movx	@dptr,a
	inc	dptr
	ret
; after "DW" or "PRI": '!' if peek == A (PEEK_OTHER, PEEK_PRIO), then the space
00091$:	cjne	a,_peek,00090$
	mov	a,#'!'
	movx	@dptr,a
	inc	dptr
	sjmp	00090$
; lcd_icon(n, r6, dptr, r5); n += 9   (n at sp-3 in here: past the return address)
00092$:	mov	a,sp
	add	a,#-3
	mov	r0,a
	mov	a,@r0
	mov	r7,a
	add	a,#9
	mov	@r0,a
	ljmp	iconr

; torch_s: the flashlight's mode, then its steady levels
00099$:	.db	0
	.asciz	"MOON "
	.asciz	"STROBE "
	.asciz	"SOS "
	.asciz	"LOW "
	.asciz	"MED "
	.asciz	"HIGH "
