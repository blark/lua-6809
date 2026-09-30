#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["MC6809"]
# ///
"""
Double-width characters on a Super screen (CoSuper edition 2, anachron8
docs/MEMORY-V2-SPEC.md section 6: the second cell of a double-width character
holds U+0000). The width table is anachronsole web/video.js WIDE (East Asian
Wide and Fullwidth in the BMP), copied here and compared with the browser's
when that file is present.
Checks: a CJK line and mixed ASCII/wide cells, every range edge of the table
(each code written and laid out as a model of the console predicts), a wide
character in column 79 (U+0000 there, the character on the next line), wide
in column 78, overwriting the lead or the second half (the other half is
blanked, xterm), $08 and $06 stepping over a second half, BS SP BS erasing
both cells, ANSI CUP/CUF/CUB onto a second half and writing there, EL/ED from
a second half, typed input on /W1: backspace, Left, Ctrl-X over wide
characters, a wide character wrapped at column 79 erased across the line
break, Right in SCF's line editor re-echoing a wide character (the bytes the
program gets, as in test_os9_utf8_edit), and /Term (Classic) unaffected.

    uv run tests/emu/test_os9_wide.py

Needs the port's build outputs in ~/src/nitros9/recipes/anachron8/dw; skipped
when they are missing.
"""

import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent.parent))
sys.path.insert(0, str(HERE))

from emu.os9boot import SCREEN0, SCREEN1, SCREENS, OS9Session  # noqa: E402
from test_os9_super import BOOT_LIMIT, WHITE, BLACK, built, check_hardware, make_super, run, to_w1  # noqa: E402
from test_os9_utf8_edit import BS, DEL_LINE, keys, enter, piped_bytes, utf8  # noqa: E402

VIDEO_JS = Path.home() / "src/anachronsole/web/video.js"

# anachronsole web/video.js WIDE (and CoSuper's WideTb)
WIDE = [
    (0x1100, 0x115f), (0x231a, 0x231b), (0x2329, 0x232a), (0x23e9, 0x23ec), (0x23f0, 0x23f0), (0x23f3, 0x23f3),
    (0x25fd, 0x25fe), (0x2614, 0x2615), (0x2648, 0x2653), (0x267f, 0x267f), (0x2693, 0x2693), (0x26a1, 0x26a1),
    (0x26aa, 0x26ab), (0x26bd, 0x26be), (0x26c4, 0x26c5), (0x26ce, 0x26ce), (0x26d4, 0x26d4), (0x26ea, 0x26ea),
    (0x26f2, 0x26f3), (0x26f5, 0x26f5), (0x26fa, 0x26fa), (0x26fd, 0x26fd), (0x2705, 0x2705), (0x270a, 0x270b),
    (0x2728, 0x2728), (0x274c, 0x274c), (0x274e, 0x274e), (0x2753, 0x2755), (0x2757, 0x2757), (0x2795, 0x2797),
    (0x27b0, 0x27b0), (0x27bf, 0x27bf), (0x2b1b, 0x2b1c), (0x2b50, 0x2b50), (0x2b55, 0x2b55), (0x2e80, 0x303e),
    (0x3041, 0x33ff), (0x3400, 0x4dbf), (0x4e00, 0x9fff), (0xa000, 0xa4cf), (0xa960, 0xa97f), (0xac00, 0xd7a3),
    (0xf900, 0xfaff), (0xfe10, 0xfe19), (0xfe30, 0xfe6f), (0xff00, 0xff60), (0xffe0, 0xffe6),
]

ZHONG, WEN, ZI = 0x4E2D, 0x6587, 0x5B57       # 中 文 字
SP = 0x20

_session = None


def is_wide(code):
    return any(a <= code <= b for a, b in WIDE)


def hexs(text):
    """UTF-8 of `text` as display's hex arguments."""
    return " ".join(f"{b:02x}" for b in text.encode())


def booted():
    global _session
    if _session is None:
        s = OS9Session(trace=64)
        for page in SCREENS:
            reason, _, err = s.wait_prompt(BOOT_LIMIT, page)
            assert err is None and reason == "until" and not s.halted, (reason, err, s.screen_dump(page))
        _session = s
    return _session


def codes(s, row, start, n):
    return [s.super_cell(SCREEN1, row, c)[0] for c in range(start, start + n)]


def fresh():
    """/W1 Super and cleared, cursor home, /Term displayed."""
    s = booted()
    make_super(s)
    return s


def layout(seq):
    """The cells a clear screen gets from `seq` written at home: the model of
    CoSuper's rule (wide: code, U+0000; in column 79: U+0000 there first)."""
    cells, col = [], 0
    for c in seq:
        if is_wide(c):
            if col == 79:
                cells.append(0)
                col = 0
            cells += [c, 0]
            col = (col + 2) % 80
        else:
            cells.append(c)
            col = (col + 1) % 80
    return cells


def test_table_matches_browser():
    if not VIDEO_JS.exists():
        print("skip  test_table_matches_browser: no anachronsole checkout")
        return
    src = VIDEO_JS.read_text()
    body = src[src.index("const WIDE = ["):]
    body = body[:body.index("];")]
    pairs = [(int(a, 16), int(b, 16)) for a, b in re.findall(r"\[0x([0-9a-f]+), 0x([0-9a-f]+)\]", body)]
    assert pairs == WIDE, pairs


def test_cjk_line():
    if not built():
        return
    s = fresh()
    to_w1(s, hexs("中文字"))
    assert codes(s, 0, 0, 7) == [ZHONG, 0, WEN, 0, ZI, 0, SP]
    assert all(s.super_cell(SCREEN1, 0, c)[1:] == (WHITE, BLACK) for c in range(6))
    assert s.cursor(SCREEN1) == (6, 0)
    to_w1(s, "0d 0a", hexs("a中b文c"))
    assert codes(s, 1, 0, 8) == [0x61, ZHONG, 0, 0x62, WEN, 0, 0x63, SP]
    assert s.cursor(SCREEN1) == (7, 1)
    to_w1(s, "1b 32 c8 1b 33 11", hexs("字"), "1b 32 0f 1b 33 00")  # colours: both cells
    assert [s.super_cell(SCREEN1, 1, c) for c in (7, 8)] == [(ZI, 200, 17), (0, 200, 17)]
    check_hardware(s)


def test_table_edges():
    """Every range's edges (first-1, first, last, last+1) and some narrow codes
    past U+1100, laid out exactly as the model predicts (wraps included)."""
    if not built():
        return
    s = fresh()
    seq = []
    for a, b in WIDE:
        for c in (a - 1, a, b, b + 1):
            if c not in seq and not 0xD800 <= c < 0xE000:
                seq.append(c)
    seq += [0x2500, 0xFF61, 0xFFFD, 0x3A9]
    data = "".join(chr(c) for c in seq)
    to_w1(s, hexs(data))
    want = layout(seq)
    got = [s.super_cell(SCREEN1, i // 80, i % 80)[0] for i in range(len(want))]
    bad = [(i // 80, i % 80, hex(g), hex(w)) for i, (g, w) in enumerate(zip(got, want)) if g != w]
    assert not bad, bad[:8]
    n = len(want)
    assert s.cursor(SCREEN1) == (n % 80, n // 80), (s.cursor(SCREEN1), n)
    print(f"      {len(seq)} codes, {sum(map(is_wide, seq))} wide, {n} cells")


def test_wrap_at_79():
    if not built():
        return
    s = fresh()
    to_w1(s, "02 6f 20", hexs("中"))                # column 79 of row 0
    assert codes(s, 0, 79, 1) == [0] and codes(s, 1, 0, 3) == [ZHONG, 0, SP]
    assert s.cursor(SCREEN1) == (2, 1)
    to_w1(s, "02 6e 22 41", hexs("文"))             # A in 78, wide from 79
    assert codes(s, 2, 78, 2) == [0x41, 0] and codes(s, 3, 0, 2) == [WEN, 0]
    to_w1(s, "08")                                  # left over the second half
    assert s.cursor(SCREEN1) == (0, 3)
    to_w1(s, "08")                                  # up to row 2, over U+0000 in 79
    assert s.cursor(SCREEN1) == (78, 2)
    to_w1(s, "02 6e 24", hexs("字"))                # wide in 78: fits
    assert codes(s, 4, 78, 2) == [ZI, 0] and s.cursor(SCREEN1) == (0, 5)
    to_w1(s, "02 6f 38", hexs("中"))                # column 79 of the last row: scrolls
    assert codes(s, 23, 79, 1) == [0] and codes(s, 24, 0, 2) == [ZHONG, 0]
    assert s.cursor(SCREEN1) == (2, 24)
    check_hardware(s)


def test_overwrite_halves():
    if not built():
        return
    s = fresh()
    to_w1(s, hexs("中文"), "02 21 20 78")           # x into 中's second half
    assert codes(s, 0, 0, 4) == [SP, 0x78, WEN, 0]
    to_w1(s, "02 22 20 79")                         # y into 文's lead
    assert codes(s, 0, 0, 4) == [SP, 0x78, 0x79, SP]
    to_w1(s, "01", hexs("中文"), "02 21 20", hexs("字"))  # wide into a second half and a lead
    assert codes(s, 0, 0, 5) == [SP, ZI, 0, SP, SP]
    assert s.cursor(SCREEN1) == (3, 0)
    to_w1(s, "01", hexs("中文"), "02 21 20", hexs("é"))   # narrow non-ASCII too
    assert codes(s, 0, 0, 4) == [SP, 0xE9, WEN, 0]
    check_hardware(s)


def test_left_right_erase_codes():
    if not built():
        return
    s = fresh()
    to_w1(s, hexs("a中b"), "08 08")                 # b, then 中's second half: to its lead
    assert s.cursor(SCREEN1) == (1, 0)
    to_w1(s, "06")                                  # right over the second half
    assert s.cursor(SCREEN1) == (3, 0)
    to_w1(s, "08 20 08")                            # SCF's erase: BS SP BS
    assert codes(s, 0, 0, 4) == [0x61, SP, SP, 0x62] and s.cursor(SCREEN1) == (1, 0)
    to_w1(s, "02 6e 20", hexs("中"), "08")          # a pair in 78-79: left from row 1
    assert s.cursor(SCREEN1) == (78, 0)
    to_w1(s, "02 6e 20 06")                         # right from 78: over 79, next line
    assert s.cursor(SCREEN1) == (0, 1)
    check_hardware(s)


def test_ansi_moves():
    if not built():
        return
    s = fresh()
    to_w1(s, hexs("中文"), "1b 5b 31 3b 32 48")     # CUP row 1 col 2: 中's second half
    assert s.cursor(SCREEN1) == (1, 0)              # the cursor may stand there
    to_w1(s, "7a")                                  # writing there blanks the lead
    assert codes(s, 0, 0, 4) == [SP, 0x7A, WEN, 0]
    to_w1(s, "1b 5b 43")                            # CUF from the lead onto 文's second half
    assert s.cursor(SCREEN1) == (3, 0)
    to_w1(s, "1b 5b 44 1b 5b 43")                   # CUB, CUF: one cell each
    assert s.cursor(SCREEN1) == (3, 0)
    to_w1(s, "1b 5b 4b")                            # EL 0 from a second half: the lead too
    assert codes(s, 0, 0, 4) == [SP, 0x7A, SP, SP]
    to_w1(s, "0d", hexs("中文字"), "1b 5b 31 3b 34 48 1b 5b 31 4b")  # EL 1 up to 文's second half
    assert codes(s, 0, 0, 7) == [SP] * 4 + [ZI, 0, SP]
    to_w1(s, "0d", hexs("中文字"), "1b 5b 31 3b 33 48 1b 5b 31 4b")  # EL 1 up to a lead: its second half too
    assert codes(s, 0, 0, 6) == [SP] * 4 + [ZI, 0]
    to_w1(s, "02 20 21", hexs("中文"), "1b 5b 32 3b 32 48 1b 5b 4a")  # ED 0 from a second half
    assert codes(s, 1, 0, 4) == [SP] * 4
    assert codes(s, 0, 4, 2) == [ZI, 0]
    check_hardware(s)


# --- typed input on /W1 (Super, displayed) --------------------------------------

def typing():
    """/W1 Super and displayed, at a fresh prompt."""
    s = fresh()
    assert s.switch_screen() == SCREEN1
    enter(s)
    return s


def back_to_term(s):
    run(s, "display 1b 21 >/term", SCREEN1)
    assert s.displayed() == SCREEN0


def test_typed_backspace_left_ctrlx():
    if not built():
        return
    s = typing()
    try:
        col0, row = s.cursor(SCREEN1)
        keys(s, utf8("echo 中a文"))
        assert s.cursor(SCREEN1) == (col0 + 10, row)
        assert codes(s, row, col0 + 5, 5) == [ZHONG, 0, 0x61, WEN, 0]
        keys(s, BS)                                 # 文: both cells
        assert s.cursor(SCREEN1) == (col0 + 8, row)
        assert codes(s, row, col0 + 8, 2) == [SP, SP]
        keys(s, BS + "\x1c")                        # a, then Left (a8vtio: $08) over 中
        assert s.cursor(SCREEN1) == (col0 + 5, row)
        assert codes(s, row, col0 + 5, 5) == [SP] * 5
        keys(s, utf8("字"))
        rows = enter(s)
        assert rows[row].endswith(":echo 字") and rows[row + 1] == "字", s.screen_dump(SCREEN1)
        col0, row = s.cursor(SCREEN1)
        keys(s, utf8("echo 中文x字") + DEL_LINE)     # Ctrl-X: backspace over the line
        assert s.cursor(SCREEN1) == (col0, row), s.screen_dump(SCREEN1)
        assert codes(s, row, col0, 13) == [SP] * 13
        data, out = piped_bytes(s, utf8("echo 中文字") + BS + utf8("x"))
        assert data == "中文x \r".encode(), (data, out)
    finally:
        back_to_term(s)
    check_hardware(s)


def test_typed_wrap_erase():
    """A wide character typed in column 79 goes to the next line; erasing it and
    the character before it (across the line break) leaves both rows clean."""
    if not built():
        return
    s = typing()
    try:
        col0, row = s.cursor(SCREEN1)
        pad = 79 - col0 - 5                         # "echo " then ASCII up to column 78
        keys(s, "echo " + "y" * pad)
        assert s.cursor(SCREEN1) == (79, row)
        keys(s, utf8("中"))
        assert codes(s, row, 79, 1) == [0] and codes(s, row + 1, 0, 2) == [ZHONG, 0]
        assert s.cursor(SCREEN1) == (2, row + 1)
        keys(s, BS)                                 # 中
        assert s.cursor(SCREEN1) == (0, row + 1)
        assert codes(s, row + 1, 0, 2) == [SP, SP]
        keys(s, BS)                                 # the last y, in column 78
        assert s.cursor(SCREEN1) == (78, row), s.screen_dump(SCREEN1)
        assert codes(s, row, 77, 3) == [0x79, SP, 0]
        keys(s, "z")
        rows = enter(s)
        assert len(rows[row]) == 79 and rows[row].endswith("yz"), s.screen_dump(SCREEN1)
        assert "y" * (pad - 1) + "z" in rows[row + 1:row + 3], s.screen_dump(SCREEN1)
    finally:
        back_to_term(s)
    check_hardware(s)


def test_right_reechoes_wide():
    """Right ($09, IT.RPR) in SCF's line editor echoes the buffer one byte a
    press: three presses redraw 中 in place, the cursor two cells on."""
    if not built():
        return
    s = typing()
    try:
        col0, row = s.cursor(SCREEN1)
        keys(s, utf8("echo 中x") + BS + BS)
        assert s.cursor(SCREEN1) == (col0 + 5, row)
        keys(s, "\x0c")                              # Right (browser $0C -> $09)
        assert s.cursor(SCREEN1) == (col0 + 5, row)  # a lead byte: nothing drawn yet
        keys(s, "\x0c\x0c")
        assert s.cursor(SCREEN1) == (col0 + 7, row)
        assert codes(s, row, col0 + 5, 3) == [ZHONG, 0, SP]
        keys(s, "\x0c")
        assert codes(s, row, col0 + 7, 1) == [0x78]
        assert s.cursor(SCREEN1) == (col0 + 8, row)
        rows = enter(s)
        assert rows[row].endswith(":echo 中 x") and rows[row + 1] == "中 x", s.screen_dump(SCREEN1)  # U+0000 shows as a blank
        data, out = piped_bytes(s, utf8("echo 中x") + BS + BS + "\x0c\x0c\x0c\x0c")
        assert data == "中x \r".encode(), (data, out)
    finally:
        back_to_term(s)
    check_hardware(s)


def test_classic_unaffected():
    """/Term (Classic, CP437): bytes are cells, no width."""
    if not built():
        return
    s = booted()
    before = [w for w in s.screen_writes(SCREEN0) if w[0] >= 0x1000]
    run(s, "display 0c e4 b8 ad 41 >/term")
    rows = s.screen(SCREEN0)
    assert s.peek(SCREEN0, 0, 4) == bytes([0xE4, 0xB8, 0xAD, 0x41]), s.peek(SCREEN0, 0, 4)
    assert not [w for w in s.screen_writes(SCREEN0) if w[0] >= 0x1000][len(before):]
    assert rows is not None
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
    if _session is not None:
        print(_session.report())
        for page in SCREENS:
            print(_session.screen_dump(page))
    sys.exit(1 if failed else 0)
