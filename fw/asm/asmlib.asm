; asm-for: none
; Helpers for the hand-written screens (draw_status, ...): no C original, only asm calls them.
; Each says what it takes and which registers it changes; the rest it keeps.
;
;   icat    append the string that follows the lcall to the XDATA text at DPTR:
;               lcall icat
;               .asciz "SCAN "
;           copies it with its NUL and leaves DPTR on the NUL, as cat() returns. Changes A, B, R6, R7.
;   ccat    the same from the code string at R7:R6 (high:low); R7:R6 end past its NUL.
;   nthcat  the same from string number A of the NUL-separated list at R7:R6. Also changes R5.
;   fillr   lcd_fillp(R7, R6, R5, R4, R3)                       \
;   text1r  text1(R7, R6, R5): buf at scale 1 on c_bg            > the C conventions underneath:
;   iconr   lcd_icon(R7, R6, DPTR, R5)                           / every register changes
;   push4   push the 4 bytes at DPTR, low first, onto the caller's stack: a uint32_t argument.
;           DPTR ends past them. Changes A, R6, R7.

	.module asmlib
	.optsdcc -mmcs51 --model-large
	.include "../build/asm.inc"	; layout and constants from the C (tools/asmcheck.py inc)

	.globl icat
	.globl ccat
	.globl nthcat
	.globl fillr
	.globl text1r
	.globl iconr
	.globl push4
	.globl _lcd_fillp
	.globl _lcd_icon
	.globl _text1


	.area CSEG    (CODE)
icat:	pop	ar7			; the return address is the string: lcall pushed low, then high
	pop	ar6
	lcall	ccat
	push	ar6			; return past its NUL
	push	ar7
	ret

nthcat:	mov	r5,a
	push	dpl
	push	dph
	mov	dpl,r6
	mov	dph,r7
00001$:	mov	a,r5
	jz	00003$
00002$:	clr	a			; past one string and its NUL
	movc	a,@a+dptr
	inc	dptr
	jnz	00002$
	dec	r5
	sjmp	00001$
00003$:	mov	r6,dpl
	mov	r7,dph
	pop	dph
	pop	dpl
; fall into ccat

ccat:	lcall	00010$			; DPTR <-> R7:R6: DPTR -> the source
	clr	a
	movc	a,@a+dptr
	inc	dptr
	mov	b,a
	lcall	00010$			; back: DPTR -> the text
	mov	a,b
	movx	@dptr,a
	jz	00011$
	inc	dptr
	sjmp	ccat
00011$:	ret
00010$:	mov	a,dpl
	xch	a,r6
	mov	dpl,a
	mov	a,dph
	xch	a,r7
	mov	dph,a
	ret

fillr:	push	ar3
	push	ar4
	push	ar5
	push	ar6
	mov	dpl,r7
	lcall	_lcd_fillp
	mov	a,sp
	add	a,#-4
	mov	sp,a
	ret

text1r:	push	ar5
	push	ar6
	mov	dpl,r7
	lcall	_text1
	dec	sp
	dec	sp
	ret

iconr:	push	ar5
	push	dpl
	push	dph
	push	ar6
	mov	dpl,r7
	lcall	_lcd_icon
	mov	a,sp
	add	a,#-4
	mov	sp,a
	ret

push4:	pop	ar7			; the return address goes back on top
	pop	ar6
	mov	b,#4
00020$:	movx	a,@dptr
	push	acc
	inc	dptr
	djnz	b,00020$
	push	ar6
	push	ar7
	ret
