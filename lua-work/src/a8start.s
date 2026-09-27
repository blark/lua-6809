; a8start.s - entry point of the Anachron8 build.
; MON09's G starts a program with S=$DF60, but a start through a reset (the
; SPI loader's vector shadow) leaves S undefined, and crt0 uses the S it is
; given. Set MON09's user stack top here, then run crt0.
	.module	a8start
	.globl	__start
	.globl	_a8_start
	.area	.text
_a8_start:
	lds	#0xDF60
	jmp	__start
