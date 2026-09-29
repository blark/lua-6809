; DriveWire READEX of drive 0 LSN 1 into buf, polled with IRQ and FIRQ masked.
        .area   DW (ABS)
DW_STATUS = 0xFF48
DW_DATA   = 0xFF49

        .org    0x0100
status: .db     0xAA            ; the server's final status byte
sum:    .dw     0               ; checksum sent
cmd:    .db     0xD2, 0, 0, 0, 1

        .org    0x1000
start:  lds     #0x0F00
        orcc    #0x50
        ldx     #cmd
        ldb     #5
send:   lda     ,x+
        bsr     putc
        decb
        bne     send
        ldx     #buf
        ldy     #0
        clrb                    ; 256 bytes
rd:     bsr     getc
        sta     ,x+
        pshs    b
        tfr     a,b
        clra
        leay    d,y             ; checksum: 16-bit sum
        puls    b
        decb
        bne     rd
        sty     sum
        lda     sum
        bsr     putc
        lda     sum+1
        bsr     putc
        bsr     getc
        sta     status
done:   bra     done

putc:   pshs    a
wtx:    lda     DW_STATUS
        bita    #0x04
        bne     wtx
        puls    a
        sta     DW_DATA
        rts

getc:   lda     DW_STATUS
        bita    #0x02
        beq     getc
        lda     DW_DATA
        rts

        .org    0x2000
buf:    .ds     256
