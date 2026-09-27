; a8heap.s - start of the heap for the Anachron8 build.
; .noinit is the last area the linker places (after .bss); the heap begins
; just past it. Its only content is crt0's saved stack pointer (2 bytes).
	.module	a8heap
	.globl	s_.noinit
	.globl	_a8_heap_start
	.area	.data
_a8_heap_start:
	.word	s_.noinit+16
