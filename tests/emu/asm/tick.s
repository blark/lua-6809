; Tick timer IRQ: the handler counts ticks and acknowledges them.
; ctrl ($0100) goes to TICK_CTRL; enable ($0101) nonzero clears I.
        .area   TICK (ABS)
TICK_CTRL   = 0xFF4A
TICK_STATUS = 0xFF4B
TICK_COUNT  = 0xFF4C

        .org    0x0100
ctrl:   .db     0x01            ; set by the test
enable: .db     0x01            ; set by the test
count:  .db     0               ; IRQs taken
last:   .db     0               ; TICK_COUNT seen by the last IRQ
spins:  .dw     0               ; main loop iterations

        .org    0x1000
start:  lds     #0x0F00
        lda     #0x01
        sta     TICK_STATUS     ; drop a tick that passed before
        lda     ctrl
        sta     TICK_CTRL
        tst     enable
        beq     loop
        andcc   #0xEF
loop:   ldx     spins
        leax    1,x
        stx     spins
        bra     loop

irq:    inc     count
        lda     TICK_COUNT
        sta     last
        lda     #0x01
        sta     TICK_STATUS
        rti

        .org    0xFFF8
        .dw     irq
