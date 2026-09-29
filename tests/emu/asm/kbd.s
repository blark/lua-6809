; Keyboard IRQ: the handler drains the FIFO into buf.
; enable ($0100) nonzero clears I; bufp ($0102) points into buf ($0200).
        .area   KBD (ABS)
KEY_STATUS = 0xFF46
KEY_DATA   = 0xFF47

        .org    0x0100
enable: .db     0x01
nirq:   .db     0
bufp:   .dw     0x0200
spins:  .dw     0

        .org    0x1000
start:  lds     #0x0F00
        tst     enable
        beq     loop
        andcc   #0xEF
loop:   ldx     spins
        leax    1,x
        stx     spins
        bra     loop

irq:    inc     nirq
        ldx     bufp
drain:  lda     KEY_STATUS
        beq     done
        lda     KEY_DATA
        sta     ,x+
        bra     drain
done:   stx     bufp
        rti

        .org    0xFFF8
        .dw     irq
