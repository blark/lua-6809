#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["MC6809"]
# ///
"""
NitrOS-9's Super screens and the palette (a8vtio + CoSuper, anachron8
docs/MEMORY-V2-SPEC.md section 6). /W1 is switched to Super at run time
(ESC $20 DWSet written to it, `vmode s </w1` = SetStat SS.ScTyp) while /Term
stays Classic. Checks: FORMAT bits, UTF-8 decoded into the four planes (code
low and high byte, fg and bg palette index), malformed input as U+FFFD,
FColor/BColor with 8-bit indices, reverse, scrolling and insert/delete line
moving all four planes, UTF-8 typed on the keyboard, the palette (ESC $31,
SS.Palet get/set through `vmode`, SS.DfPal), the Classic screen unaffected,
and the hardware rules (block map, no drive 0, no writes to pages $FE/$FF,
FORMAT written before the upper planes). Prints the cycles of a scroll on a
Super and on a Classic screen.

    uv run tests/emu/test_os9_super.py

Needs the port's build outputs in ~/src/nitros9/recipes/anachron8/dw; skipped
when they are missing.
"""

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent.parent))

from emu.anachron8 import PAL_DATA, PAL_INDEX, xterm256  # noqa: E402
from emu.os9boot import DISK, FORMAT, KERNEL, KERNEL_JSON, SCREEN0, SCREEN1, SCREENS, OS9Session  # noqa: E402

BOOT_LIMIT = 10_000_000
STEP = 50_000_000
SUPER, CLASSIC = 0x03, 0x00          # FORMAT: Super + keyboard UTF-8; Classic
WHITE, BLACK = 15, 0                 # /W1's descriptor colours
REPL = 0xFFFD

_session = None
timings = {}


def built():
    return all(p.exists() for p in (KERNEL, KERNEL_JSON, DISK))


def booted():
    global _session
    if _session is None:
        s = OS9Session(trace=64)
        for page in SCREENS:
            reason, _, err = s.wait_prompt(BOOT_LIMIT, page)
            assert err is None and reason == "until" and not s.halted, (reason, err, s.screen_dump(page))
        _session = s
    return _session


def run(s, line, page=SCREEN0):
    """A command typed on /Term (displayed), run to the next prompt."""
    reason, rows = s.command(line, STEP, page)
    assert reason == "until", (line, reason, s.screen_dump(page))
    return rows


def to_w1(s, *hexbytes):
    """Bytes written to /W1 by `display` (hex), 14 a command so the typed line
    stays on one screen row (a UTF-8 sequence may span two: the decoder state
    lives in the device statics)."""
    data = " ".join(hexbytes).split()
    for i in range(0, len(data), 14):
        run(s, "display " + " ".join(data[i:i + 14]) + " >/w1")


def acia(s, line):
    """The lines a command prints on /T1."""
    start = len(s.m.mem.console_output)
    run(s, line)
    out = "".join(s.m.mem.console_output[start:])
    return [ln for ln in out.replace("\r", "\n").split("\n") if ln]


def cells(s, row, cols, page=SCREEN1):
    return [s.super_cell(page, row, c) for c in cols]


def make_super(s):
    """/W1 Super, white on black, cleared (DWSet); /Term shown."""
    if s.displayed() != SCREEN0:
        run(s, "display 1b 21 >/term", SCREEN1)
    to_w1(s, "1b 20 01 00 00 50 19 0f 00 00")
    assert s.peek(SCREEN1, FORMAT)[0] == SUPER


def pal_hw(s, index):
    """Entry `index` read back through PAL_INDEX/PAL_DATA (as the CPU reads it)."""
    mem = s.m.mem
    mem.write_byte(PAL_INDEX, index)
    return bytes(mem.read_byte(PAL_DATA) for _ in range(3))


def pal_dump(lines):
    """`vmode p` / `vmode d` output (256 lines "ii rrggbb") as 768 bytes."""
    assert len(lines) == 256, lines[:3]
    out = bytearray()
    for i, ln in enumerate(lines):
        idx, rgb = ln.split()
        assert int(idx, 16) == i, ln
        out += bytes.fromhex(rgb)
    return bytes(out)


def check_hardware(s):
    bm = s.blkmap()
    assert len(bm) == 256 and bytes(bm[0xF8:]) == bytes([0, 0, 0, 0, 4, 4, 0x80, 0x80])
    assert not s.drive0, s.drive0
    other = [w for w in s.special_writes if w[0] not in SCREENS]
    assert not other, other[:5]
    high0 = [w for w in s.screen_writes(SCREEN0) if w[1] >= 0x1000]
    assert not high0, high0[:5]                                      # /Term is Classic throughout
    assert s.peek(SCREEN0, FORMAT)[0] == CLASSIC
    # page $FD: FORMAT turns Super before any write to the upper planes
    fmt = 0
    for page, off, val, pc in s.special_writes:
        if page != SCREEN1:
            continue
        if off == FORMAT:
            fmt = val
        elif off >= 0x1000:
            assert fmt & 1, (hex(off), hex(val), hex(pc))


def test_switch_to_super():
    if not built():
        print("skip  test_switch_to_super: no NitrOS-9 build")
        return
    s = booted()
    assert s.peek(SCREEN1, FORMAT)[0] == CLASSIC
    run(s, "echo classic-text >/w1")
    make_super(s)
    assert s.peek(SCREEN0, FORMAT)[0] == CLASSIC
    assert s.cursor(SCREEN1) == (0, 0)
    blank = (0x20, WHITE, BLACK)
    assert all(s.super_cell(SCREEN1, r, c) == blank for r in range(25) for c in range(80))
    assert acia(s, "vmode </w1 >/t1") == ["Super"]                  # GetStat SS.ScTyp
    assert acia(s, "vmode >/t1") == ["Classic"]                     # /Term
    check_hardware(s)


def test_utf8_cells():
    if not built():
        return
    s = booted()
    make_super(s)
    # A, e acute (Latin-1), Greek Omega, box drawing, a CJK character (3 bytes)
    to_w1(s, "41 c3 a9 ce a9 e2 94 80 e4 b8 ad 7e")
    got = cells(s, 0, range(7))
    assert [c[0] for c in got] == [0x41, 0xE9, 0x3A9, 0x2500, 0x4E2D, 0x7E, 0x20], got
    assert all(c[1:] == (WHITE, BLACK) for c in got)
    assert s.super_screen(SCREEN1)[0] == "AéΩ─中~"
    assert s.cursor(SCREEN1) == (6, 0)
    # the high plane holds the high byte, the low plane the low byte
    assert s.peek(SCREEN1, 0x1004)[0] == 0x4E and s.peek(SCREEN1, 0x004)[0] == 0x2D
    assert s.peek(SCREEN1, 0x1001)[0] == 0x00 and s.peek(SCREEN1, 0x001)[0] == 0xE9
    check_hardware(s)


def test_malformed_utf8():
    if not built():
        return
    s = booted()
    make_super(s)
    to_w1(s,
          "80",                 # stray continuation      -> FFFD
          "c3 41",              # cut by ASCII            -> FFFD A
          "f0 9f 98 80",        # outside the BMP          -> FFFD (one cell)
          "ed a0 80",           # surrogate                -> FFFD
          "e0 80 80",           # overlong 3-byte          -> FFFD
          "c0 42",              # C0 lead                  -> FFFD B
          "e4 b8 c3 a9",        # cut by a lead byte       -> FFFD e-acute
          "e4 b8 06 43",        # cut by a control ($06 right): dropped, cursor right, C
          "ff")                 # invalid lead             -> FFFD
    got = [c[0] for c in cells(s, 0, range(14))]
    assert got == [REPL, REPL, 0x41, REPL, REPL, REPL, REPL, 0x42, REPL, 0xE9, 0x20, 0x43, REPL, 0x20], \
        [hex(c) for c in got]
    check_hardware(s)


def test_colours_and_reverse():
    if not built():
        return
    s = booted()
    make_super(s)
    to_w1(s, "1b 32 c8 1b 33 11 58",        # FColor 200, BColor 17, X
          "1f 20 59 1f 21",                 # reverse: Y, reverse off
          "1b 32 0f 1b 33 00 5a")           # back to 15/0, Z
    assert cells(s, 0, range(3)) == [(0x58, 200, 17), (0x59, 17, 200), (0x5A, 15, 0)]
    assert s.peek(SCREEN1, 0x7D0)[0] == 200 and s.peek(SCREEN1, 0x17D0)[0] == 17   # fg, bg planes
    check_hardware(s)


def test_scroll_moves_four_planes():
    if not built():
        return
    s = booted()
    make_super(s)
    to_w1(s, "02 20 21 1b 32 c8 1b 33 11 e4 b8 ad 1b 32 0f 1b 33 00",   # row 1: CJK in 200/17
          "02 20 38 5a",                                                   # row 24: Z
          "1b 32 03 1b 33 04 0a")                                          # colours 3/4, LF: scroll
    assert s.super_cell(SCREEN1, 0, 0) == (0x4E2D, 200, 17)
    assert s.super_cell(SCREEN1, 1, 0) == (0x20, WHITE, BLACK)
    assert s.super_cell(SCREEN1, 23, 0) == (0x5A, WHITE, BLACK)
    assert all(s.super_cell(SCREEN1, 24, c) == (0x20, 3, 4) for c in range(80))  # new row: current colours
    assert s.cursor(SCREEN1) == (1, 24)
    to_w1(s, "01 1f 30")                                                   # home, insert line
    assert s.super_cell(SCREEN1, 1, 0) == (0x4E2D, 200, 17)
    assert s.super_cell(SCREEN1, 0, 0) == (0x20, 3, 4)
    assert s.super_cell(SCREEN1, 24, 0) == (0x5A, WHITE, BLACK)
    to_w1(s, "1f 31")                                                      # delete it again
    assert s.super_cell(SCREEN1, 0, 0) == (0x4E2D, 200, 17)
    assert s.super_cell(SCREEN1, 23, 0) == (0x5A, WHITE, BLACK)
    check_hardware(s)


def scroll_cycles(s, n=20):
    """Cycles of one scroll on /W1: n line feeds on the bottom row minus n returns."""
    def cost(code):
        to_w1(s, "02 20 38")
        c0 = s.m.cpu.cycles
        to_w1(s, *([code] * n))
        return s.m.cpu.cycles - c0
    lf, cr = cost("0a"), cost("0d")
    return (lf - cr) // n


def test_scroll_timing():
    if not built():
        return
    s = booted()
    make_super(s)
    timings["Super"] = scroll_cycles(s)
    run(s, "vmode c </w1")                                   # SetStat SS.ScTyp: Classic
    assert s.peek(SCREEN1, FORMAT)[0] == CLASSIC
    timings["Classic"] = scroll_cycles(s)
    assert 0 < timings["Classic"] < timings["Super"], timings
    run(s, "vmode s </w1")
    assert s.peek(SCREEN1, FORMAT)[0] == SUPER
    check_hardware(s)


def test_keyboard_utf8():
    if not built():
        return
    s = booted()
    make_super(s)
    assert s.switch_screen() == SCREEN1
    s.type("\r", STEP)                                       # a fresh prompt on the cleared screen
    reason, _, _ = s.wait_prompt(STEP, SCREEN1)
    assert reason == "until", s.screen_dump(SCREEN1)
    # the browser sends UTF-8 while a Super screen is shown: the echo and the output decode
    typed = "echo éΩ中".encode().decode("latin-1")
    reason, _, err = s.type(typed, STEP)
    assert reason == "until", (reason, err)
    reason, _, _ = s.wait_screen(lambda: s.super_screen(SCREEN1)[s.cursor(SCREEN1)[1]].endswith("中"), STEP)
    assert reason == "until", s.screen_dump(SCREEN1)
    s.type("\r", STEP)
    reason, _, _ = s.wait_prompt(STEP, SCREEN1)
    assert reason == "until", s.screen_dump(SCREEN1)
    rows = s.super_screen(SCREEN1)
    assert "éΩ中" in rows, s.screen_dump(SCREEN1)
    assert any(r.endswith(":echo éΩ中") for r in rows), s.screen_dump(SCREEN1)
    run(s, "display 1b 21 >/term", SCREEN1)
    assert s.displayed() == SCREEN0
    check_hardware(s)


def test_palette_escape():
    if not built():
        return
    s = booted()
    to_w1(s, "1b 31 05 12 34 56")                            # ESC $31 i r g b
    assert bytes(s.m.mem.palette[15:18]) == bytes.fromhex("123456")
    assert pal_hw(s, 5) == bytes.fromhex("123456")          # PAL_DATA reads back
    pal = pal_dump(acia(s, "vmode p >/t1"))                  # GetStat SS.Palet: the shadow
    assert pal[15:18] == bytes.fromhex("123456")
    assert pal == bytes(s.m.mem.palette)                     # shadow == hardware
    check_hardware(s)


def test_palette_sspalet():
    if not built():
        return
    s = booted()
    run(s, "vmode e 07 abcdef")                              # SS.Palet get, one entry, SS.Palet set
    run(s, "vmode e ff 010203")
    assert pal_hw(s, 7) == bytes.fromhex("abcdef") and pal_hw(s, 255) == bytes.fromhex("010203")
    pal = pal_dump(acia(s, "vmode p >/t1"))
    assert pal == bytes(s.m.mem.palette)
    assert pal[21:24] == bytes.fromhex("abcdef") and pal[765:] == bytes.fromhex("010203")
    check_hardware(s)


def test_default_palette():
    if not built():
        return
    s = booted()
    run(s, "vmode e 10 ffffff")
    assert bytes(s.m.mem.palette) != xterm256()
    assert pal_dump(acia(s, "vmode d >/t1")) == xterm256()  # GetStat SS.DfPal
    run(s, "vmode r")                                        # SetStat SS.DfPal
    assert bytes(s.m.mem.palette) == xterm256()
    assert pal_dump(acia(s, "vmode p >/t1")) == xterm256()
    assert pal_hw(s, 16) == b"\0\0\0" and pal_hw(s, 231) == b"\xff\xff\xff" and pal_hw(s, 232) == b"\x08" * 3
    check_hardware(s)


def test_classic_unaffected():
    if not built():
        return
    s = booted()
    make_super(s)
    to_w1(s, "1b 32 c8 41")
    rows = run(s, "echo still-classic")                      # /Term: Classic cells, attribute $0F
    assert "still-classic" in rows
    r = rows.index("still-classic")
    assert s.peek(SCREEN0, r * 80, 1)[0] == ord("s") and s.peek(SCREEN0, 0x7D0 + r * 80)[0] == 0x0F
    run(s, "display 1b 20 00 00 00 50 19 0e 01 00 >/w1")   # DWSet: /W1 back to Classic, yellow on blue
    assert s.peek(SCREEN1, FORMAT)[0] == CLASSIC
    run(s, "echo classic-again >/w1")
    assert s.screen(SCREEN1)[0] == "classic-again", s.screen_dump(SCREEN1)
    assert s.peek(SCREEN1, 0x7D0)[0] == 0x1E                 # bg 1, fg 14
    run(s, "display 1b 20 ff 00 00 50 19 0f 00 00 >/w1")   # sty $FF: keep the format
    assert s.peek(SCREEN1, FORMAT)[0] == CLASSIC and s.peek(SCREEN1, 0x7D0)[0] == 0x0F
    check_hardware(s)


def test_palette_swatches():
    """vmode p draws swatches on a Super screen (16 rows of " ii " on BColor = ii, the
    digits black or white by brightness), notes on a Classic one that the palette colours
    Super screens only, and lists hex with -v."""
    if not built():
        return
    s = booted()
    run(s, "vmode r")
    make_super(s)
    run(s, "vmode p >/w1")                                   # standard output = /W1, Super
    text = [bytes(s.peek(SCREEN1, r * 80, 64)).decode("latin-1") for r in range(25)]
    top = next(r for r, t in enumerate(text) if t.startswith(" 00  01  02 "))
    assert top + 16 <= 25, s.screen_dump(SCREEN1)
    pal = xterm256()
    for row in range(16):
        r = top + row
        assert text[r] == "".join(f" {16 * row + i:02X} " for i in range(16)), text[r]
        bg = s.peek(SCREEN1, 0x17D0 + r * 80, 64)
        fg = s.peek(SCREEN1, 0x7D0 + r * 80, 64)
        for i in range(16):
            n = 16 * row + i
            red, green, blue = pal[3 * n:3 * n + 3]
            ink = 0 if (77 * red + 150 * green + 29 * blue) >> 8 >= 0x80 else 15
            assert list(bg[4 * i:4 * i + 4]) == [n] * 4, (n, list(bg[4 * i:4 * i + 4]))
            assert list(fg[4 * i:4 * i + 4]) == [ink] * 4, (n, ink, list(fg[4 * i:4 * i + 4]))
    after = top + 16                                         # colours back after each row
    assert s.peek(SCREEN1, 0x7D0 + after * 80)[0] == 0x0F and s.peek(SCREEN1, 0x17D0 + after * 80)[0] == 0
    rows = run(s, "vmode p")                                 # standard output = /Term, Classic
    assert any("Super screens only" in r for r in rows), rows
    run(s, "vmode p -v >/w1")
    lines = [bytes(s.peek(SCREEN1, r * 80, 9)).decode("latin-1") for r in range(25)]
    assert "FF EEEEEE" in [ln.upper() for ln in lines], lines
    check_hardware(s)


if __name__ == "__main__":
    tests = [(n, f) for n, f in globals().items() if n.startswith("test_")]
    failed = 0
    for name, fn in tests:
        try:
            fn()
            print(f"ok    {name}")
        except Exception as e:  # report every test, then fail overall
            failed += 1
            print(f"FAIL  {name}: {type(e).__name__}: {e}")
    print(f"{len(tests) - failed}/{len(tests)} passed")
    if timings:
        print("scroll, cycles per line: " + ", ".join(f"{k} {v}" for k, v in timings.items()))
    if _session is not None:
        print(_session.report())
        for page in SCREENS:
            print(_session.screen_dump(page))
    sys.exit(1 if failed else 0)
