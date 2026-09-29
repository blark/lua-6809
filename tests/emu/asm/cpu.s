; The instructions the MC6809 package lacks: SWI, SWI2, SWI3, CWAI, SYNC,
; PSHU/PULU S. Each part ends by storing a marker; the test checks the
; variables. The tick IRQ wakes CWAI; a key ends a masked SYNC.
        .area   CPU (ABS)
TICK_CTRL   = 0xFF4A
TICK_STATUS = 0xFF4B
KEY_DATA    = 0xFF47

        .org    0x0100
code2:  .db     0               ; the byte after SWI2 (an OS-9 function code)
resa:   .db     0               ; A after SWI2 (the handler changed the stacked A)
cc2:    .db     0               ; CC inside the SWI2 handler
cc1:    .db     0               ; CC inside the SWI handler
n3:     .db     0               ; SWI3 handler runs
woke:   .db     0               ; set after CWAI returned
nirq:   .db     0               ; IRQs taken
synced: .db     0               ; set after SYNC ended
key:    .db     0               ; the key read after SYNC
pshu_y: .dw     0               ; S pushed with PSHU, pulled into Y
puls_s: .dw     0               ; S after PULU S
ccirq:  .db     0               ; CC stacked by the IRQ (E must be set)
done:   .db     0

        .org    0x1000
start:  lds     #0x0F00
        andcc   #0xAF           ; I and F clear: SWI2 must leave them so
        lda     #0x11
        swi2
        .db     0x42
        sta     resa
        swi3
        swi
; PSHU S / PULU S
        ldu     #0x0800
        pshu    s
        pulu    y
        sty     pshu_y
        ldy     #0x0E80
        pshu    y
        pulu    s
        sts     puls_s
        lds     #0x0F00
; CWAI: wait for the tick
        lda     #0x01
        sta     TICK_STATUS
        sta     TICK_CTRL
        cwai    #0xEF
        inc     woke
        clr     TICK_CTRL
; SYNC with IRQ masked: the key ends it, no IRQ taken
        orcc    #0x10
        sync
        inc     synced
        lda     KEY_DATA
        sta     key
        inc     done
fin:    bra     fin

swi2h:  ldx     10,s            ; stacked PC: the function code byte
        ldb     ,x+
        stx     10,s
        stb     code2
        tfr     cc,a
        sta     cc2
        lda     #0x99
        sta     1,s             ; stacked A
        rti
swi3h:  inc     n3
        rti
swih:   tfr     cc,a
        sta     cc1
        rti
irq:    inc     nirq
        lda     ,s
        sta     ccirq
        lda     #0x01
        sta     TICK_STATUS
        rti

        .org    0xFFF2
        .dw     swi3h, swi2h, 0, irq, swih
