#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["MC6809"]
# ///
"""
NitrOS-9's console (a8vtio) and ANSI escape sequences next to the CoCo codes.
Bytes are written with `display` (hex) to /W1, switched between Classic and
Super with ESC $20 (DWSet, white on black), and to /Term (Classic, where the
shell runs). Checks: every SGR code on both formats (Classic: the attribute
byte's fg/bg nibbles; Super: the fg/bg planes), 38;5;n / 48;5;n exact on Super
and the nearest of the 16 on Classic, bold brightening 0-7, combined
parameters, CUP/HVP with clamping, CUU/CUD/CUF/CUB (no scroll), ED and EL
(0, 1, 2), save/restore (ESC [ s/u and ESC 7/8, per device), ?25 l/h, unknown
and malformed sequences swallowed, sequences split across writes and across
`display` calls, a UTF-8 sequence cut by ESC, and CoCo codes mixed in.

    uv run tests/emu/test_os9_ansi.py [--dump]

--dump prints a Classic screen with its attributes and a Super screen's
planes. Needs the port's build outputs in ~/src/nitros9/recipes/anachron8/dw;
skipped when they are missing.
"""

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent.parent))

from emu.os9boot import DISK, FORMAT, KERNEL, KERNEL_JSON, SCREEN0, SCREEN1, SCREENS, OS9Session  # noqa: E402

BOOT_LIMIT = 10_000_000
STEP = 50_000_000
SUPER, CLASSIC = 0x03, 0x00          # FORMAT values
FG, BG = 15, 0                       # the devices' colours (descriptors, DWSet below)
CHUNK = 14                           # bytes per `display` line (one screen row)

_session = None


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
    reason, rows = s.command(line, STEP, page)
    assert reason == "until", (line, reason, s.screen_dump(page))
    return rows


def hexs(data):
    """bytes, str (latin-1) or lists of them as hex words."""
    if isinstance(data, (list, tuple)):
        return [w for d in data for w in hexs(d)]
    if isinstance(data, str):
        data = data.encode("latin-1")
    return [f"{b:02x}" for b in data]


def csi(params):
    return "\x1b[" + params


def to_dev(s, data, dev="/w1", chunk=CHUNK):
    words = hexs(data)
    for i in range(0, len(words), chunk):
        run(s, "display " + " ".join(words[i:i + chunk]) + f" >{dev}")


def dwset(s, fmt):
    """/W1 Classic (0) or Super (1), white on black, cleared, cursor home."""
    if s.displayed() != SCREEN0:
        run(s, "display 1b 21 >/term", SCREEN1)
    to_dev(s, bytes([0x1B, 0x20, fmt, 0, 0, 80, 25, FG, BG, 0]))
    assert s.peek(SCREEN1, FORMAT)[0] == (SUPER if fmt else CLASSIC)
    assert s.cursor(SCREEN1) == (0, 0)


def cell(s, row, col, page=SCREEN1):
    """(code, fg, bg) on either format."""
    if s.peek(page, FORMAT)[0] & 1:
        return s.super_cell(page, row, col)
    ch = s.peek(page, row * 80 + col)[0]
    attr = s.peek(page, 0x7D0 + row * 80 + col)[0]
    return ch, attr & 15, attr >> 4


def row_text(s, row, page=SCREEN1):
    return "".join(chr(cell(s, row, c, page)[0]) for c in range(80)).rstrip()


def check_hardware(s):
    bm = s.blkmap()
    assert len(bm) == 256 and bytes(bm[0xF8:]) == bytes([0, 0, 0, 0, 4, 4, 0x80, 0x80])
    assert not s.drive0, s.drive0
    other = [w for w in s.special_writes if w[0] not in SCREENS]
    assert not other, other[:5]
    high0 = [w for w in s.screen_writes(SCREEN0) if w[0] >= 0x1000]
    assert not high0, high0[:5]                     # /Term stays Classic
    assert s.peek(SCREEN0, FORMAT)[0] == CLASSIC


# SGR cases, each after ESC [ 0 m: parameters, (fg, bg) on Classic, (fg, bg) on Super
SGR = [
    ("", (FG, BG), (FG, BG)),
    ("0", (FG, BG), (FG, BG)),
    ("30", (0, BG), (0, BG)), ("31", (1, BG), (1, BG)), ("32", (2, BG), (2, BG)),
    ("33", (3, BG), (3, BG)), ("34", (4, BG), (4, BG)), ("35", (5, BG), (5, BG)),
    ("36", (6, BG), (6, BG)), ("37", (7, BG), (7, BG)),
    ("90", (8, BG), (8, BG)), ("91", (9, BG), (9, BG)), ("94", (12, BG), (12, BG)),
    ("97", (15, BG), (15, BG)),
    ("40", (FG, 0), (FG, 0)), ("41", (FG, 1), (FG, 1)), ("42", (FG, 2), (FG, 2)),
    ("43", (FG, 3), (FG, 3)), ("44", (FG, 4), (FG, 4)), ("45", (FG, 5), (FG, 5)),
    ("46", (FG, 6), (FG, 6)), ("47", (FG, 7), (FG, 7)),
    ("100", (FG, 8), (FG, 8)), ("101", (FG, 9), (FG, 9)), ("107", (FG, 15), (FG, 15)),
    ("1", (FG, BG), (FG, BG)),                              # default fg 15: already bright
    ("1;31", (9, BG), (9, BG)), ("31;1", (9, BG), (9, BG)),
    ("1;91", (9, BG), (9, BG)), ("1;31;22", (1, BG), (1, BG)),
    ("1;41", (FG, 1), (FG, 1)),                             # bold leaves the background
    ("7", (BG, FG), (BG, FG)), ("31;44;7", (4, 1), (4, 1)), ("31;44;7;27", (1, 4), (1, 4)),
    ("31;39", (FG, BG), (FG, BG)), ("44;49", (FG, BG), (FG, BG)),
    ("31;44;0", (FG, BG), (FG, BG)), ("1;31;7;0", (FG, BG), (FG, BG)),
    ("1;31;44", (9, 4), (9, 4)), ("1;33;104", (11, 12), (11, 12)),
    ("38;5;196", (9, BG), (196, BG)), ("38;5;21", (12, BG), (21, BG)),
    ("38;5;244", (8, BG), (244, BG)), ("38;5;250", (7, BG), (250, BG)),
    ("38;5;3", (3, BG), (3, BG)), ("38;5;14", (14, BG), (14, BG)),
    ("48;5;196", (FG, 9), (FG, 196)), ("48;5;17", (FG, 4), (FG, 17)),
    ("1;38;5;2", (10, BG), (10, BG)), ("1;38;5;100", (3 + 8, BG), (100, BG)),
    ("38;5;208;48;5;22", (11, 2), (208, 22)),         # FF8700: nearer FFFF00
    ("4;5;3;9", (FG, BG), (FG, BG)),                        # unsupported codes: ignored
    ("38;2;255;0;0;41", (FG, BG), (FG, BG)),                # 38;2 ends the processing
    ("31;38;5", (1, BG), (1, BG)),                          # 38;5 without n: ends it
]


def sgr_screen(s, fmt):
    """Each SGR case writes 'X' at row i//40, column 2*(i%40)."""
    dwset(s, fmt)
    data = []
    for i, (p, _, _) in enumerate(SGR):
        data.append(csi(f"{1 + i // 40};{1 + 2 * (i % 40)}H") + csi("0m") + csi(p + "m") + "X")
    to_dev(s, data)
    bad = []
    for i, (p, classic, sup) in enumerate(SGR):
        got = cell(s, i // 40, 2 * (i % 40))
        want = (ord("X"),) + (sup if fmt else classic)
        if got != want:
            bad.append((p, got, want))
    assert not bad, bad


def test_sgr_classic():
    if not built():
        print("skip  test_sgr_classic: no NitrOS-9 build")
        return
    s = booted()
    sgr_screen(s, 0)
    check_hardware(s)


def test_sgr_super():
    if not built():
        return
    s = booted()
    sgr_screen(s, 1)
    check_hardware(s)


def test_nearest16_classic():
    """38;5;n and 48;5;n on Classic: every n 0-255 against the nearest-RGB rule."""
    if not built():
        return
    s = booted()
    dwset(s, 0)
    lv = [0, 0x5F, 0x87, 0xAF, 0xD7, 0xFF]
    p16 = [tuple(bytes.fromhex(h)) for h in (
        "000000 800000 008000 808000 000080 800080 008080 C0C0C0 808080 FF0000 00FF00 FFFF00 "
        "0000FF FF00FF 00FFFF FFFFFF").split()]

    def rgb(n):
        if n < 16:
            return p16[n]
        if n < 232:
            n -= 16
            return lv[n // 36], lv[n // 6 % 6], lv[n % 6]
        g = 8 + 10 * (n - 232)
        return g, g, g

    def near(n):
        c = rgb(n)
        return min(range(16), key=lambda i: (sum((a - b) ** 2 for a, b in zip(c, p16[i])), i))

    # fg n and bg 255-n in one cell, one cell per n (every 5th n and the spot checks)
    ns = sorted(set(range(0, 256, 5)) | {16, 21, 196, 231, 232, 244, 250, 255})
    to_dev(s, [csi(f"38;5;{n};48;5;{255 - n}m") + "#" for n in ns], chunk=16)
    bad = [(n, cell(s, 0, i)[1:], (near(n), near(255 - n))) for i, n in enumerate(ns)
           if cell(s, 0, i)[1:] != (near(n), near(255 - n))]
    assert not bad, bad[:8]
    assert near(196) == 9 and near(21) == 12 and near(244) == 8 and near(250) == 7


def cursor_cases(s, fmt):
    dwset(s, fmt)
    page = SCREEN1
    to_dev(s, "TOP")                                         # row 0 marker: no scroll happens

    def at(seq, want):
        to_dev(s, seq)
        assert s.cursor(page) == want, (seq.encode(), s.cursor(page), want)

    at(csi("5;10H"), (9, 4))
    at(csi("H"), (0, 0))
    at(csi(";5H"), (4, 0))
    at(csi("5H"), (0, 4))
    at(csi("99;999H"), (79, 24))
    at(csi("0;0f"), (0, 0))
    at(csi("3;4f"), (3, 2))
    at(csi("26;81f"), (79, 24))
    at(csi("11;11H") + csi("3A"), (10, 7))
    at(csi("A"), (10, 6))
    at(csi("0A"), (10, 5))
    at(csi("99A"), (10, 0))
    at(csi("2B"), (10, 2))
    at(csi("B"), (10, 3))
    at(csi("250B"), (10, 24))
    at(csi("5C"), (15, 24))
    at(csi("C"), (16, 24))
    at(csi("200C"), (79, 24))
    at(csi("3D"), (76, 24))
    at(csi("D"), (75, 24))
    at(csi("99D"), (0, 24))
    assert row_text(s, 0) == "TOP", row_text(s, 0)          # nothing scrolled
    # the CoCo codes still move the same cursor
    at("\x02" + chr(0x20 + 7) + chr(0x20 + 3) + csi("2C"), (9, 3))
    at(csi("1;1H") + "\x0a\x06", (1, 1))                    # $0A down, $06 right


def test_cursor_classic():
    if not built():
        return
    s = booted()
    cursor_cases(s, 0)
    check_hardware(s)


def test_cursor_super():
    if not built():
        return
    s = booted()
    cursor_cases(s, 1)
    check_hardware(s)


def erase_cases(s, fmt):
    dwset(s, fmt)
    row = "ABCDEFGHIJ"

    def fill():
        to_dev(s, [csi(f"{r + 1};1H") + row for r in range(6)])
        assert all(row_text(s, r) == row for r in range(6))

    def after(seq, want_rows, want_cur):
        to_dev(s, seq)
        got = [row_text(s, r) for r in range(len(want_rows))]
        assert got == want_rows, (seq.encode(), got)
        assert s.cursor(SCREEN1) == want_cur, (seq.encode(), s.cursor(SCREEN1))

    fill()
    after(csi("1;5H") + csi("K"), ["ABCD", row, row, row, row, row], (4, 0))
    after(csi("2;5H") + csi("1K"), ["ABCD", "     FGHIJ", row, row, row, row], (4, 1))
    after(csi("3;5H") + csi("2K"), ["ABCD", "     FGHIJ", "", row, row, row], (4, 2))
    after(csi("4;9H") + csi("0K"), ["ABCD", "     FGHIJ", "", "ABCDEFGH", row, row], (8, 3))
    fill()
    after(csi("4;3H") + csi("1J"), ["", "", "", "   DEFGHIJ", row, row], (2, 3))
    after(csi("5;4H") + csi("J"), ["", "", "", "   DEFGHIJ", "ABC", ""], (3, 4))
    fill()
    after(csi("3;6H") + csi("0J"), [row, row, "ABCDE", "", "", ""], (5, 2))
    fill()
    after(csi("4;6H") + csi("2J"), [""] * 25, (5, 3))
    fill()
    after(csi("2;80H") + csi("1K"), [row, "", row], (79, 1))        # 1K at the last column
    after(csi("1;1H") + csi("3J") + csi("9K"), [row, "", row], (0, 0))  # unsupported: nothing
    # erased cells take the current background (and reverse, as the CoCo erase codes)
    to_dev(s, csi("44m") + csi("3;1H") + csi("2K") + csi("0m"))
    assert all(cell(s, 2, c) == (0x20, FG, 4) for c in range(80)), [cell(s, 2, c) for c in range(3)]
    to_dev(s, csi("41m") + csi("2J") + csi("0m"))
    assert all(cell(s, r, c) == (0x20, FG, 1) for r in (0, 12, 24) for c in (0, 40, 79))
    to_dev(s, csi("2J"))


def test_erase_classic():
    if not built():
        return
    s = booted()
    erase_cases(s, 0)
    check_hardware(s)


def test_erase_super():
    if not built():
        return
    s = booted()
    erase_cases(s, 1)
    check_hardware(s)


def test_save_restore():
    if not built():
        return
    s = booted()
    dwset(s, 0)
    to_dev(s, csi("3;7H") + csi("s") + csi("10;10H"))
    assert s.cursor(SCREEN1) == (9, 9)
    to_dev(s, csi("u"))
    assert s.cursor(SCREEN1) == (6, 2)
    to_dev(s, csi("20;30H") + "\x1b7" + csi("H"))
    assert s.cursor(SCREEN1) == (0, 0)
    to_dev(s, "\x1b8")
    assert s.cursor(SCREEN1) == (29, 19)
    # per device: /Term saves its own cursor, /W1's stays
    run(s, "display 1b 5b 73 >/term")                         # /Term saves its cursor
    to_dev(s, csi("H") + csi("u"))
    assert s.cursor(SCREEN1) == (29, 19)
    assert row_text(s, 19) == ""                               # nothing drawn
    check_hardware(s)


def test_cursor_hide():
    if not built():
        return
    s = booted()
    dwset(s, 1)
    to_dev(s, csi("5;5H") + csi("?25l"))
    assert s.cursor(SCREEN1) is None
    to_dev(s, csi("2;2H"))
    assert s.cursor(SCREEN1) is None
    to_dev(s, csi("?25h"))
    assert s.cursor(SCREEN1) == (1, 1)
    to_dev(s, csi("?25;1l") + csi("?7l") + csi("25l"))       # not exactly ?25: swallowed
    assert s.cursor(SCREEN1) == (1, 1) and row_text(s, 1) == ""


UNKNOWN = [csi("?1049h"), csi("5n"), csi("12;34r"), csi("99X"), csi("1;2;3;4;5;6;7;8;9;10m"),
           csi("!p"), csi("1:2m"), csi(">c"), csi("1?m"), csi("?25$p"), csi("4h"), csi("6n"),
           csi("255;255;255;255;255;255;255;255;255H"), csi("99999999999m"), csi("~"),
           csi("=1H"), csi("@"), csi("0;1;2;3;4;5;6;7;8;9;10;11;12;13;14;15;16;17;18;19;20A")]


def test_unknown_swallowed():
    if not built():
        return
    s = booted()
    for fmt in (0, 1):
        dwset(s, fmt)
        to_dev(s, csi("11;1H") + "ab")
        to_dev(s, UNKNOWN)
        to_dev(s, "cd")
        assert row_text(s, 10) == "abcd", row_text(s, 10)
        assert s.cursor(SCREEN1) == (4, 10)
        assert all(row_text(s, r) == "" for r in range(25) if r != 10)
        assert all(cell(s, 10, c)[1:] == (FG, BG) for c in range(4))   # colours untouched
    # 8 parameters are fine, 9 are not
    to_dev(s, csi("0;0;0;0;0;0;0;31m") + "e" + csi("0;0;0;0;0;0;0;0;32m") + "f" + csi("0m"))
    assert cell(s, 10, 4) == (ord("e"), 1, BG) and cell(s, 10, 5) == (ord("f"), 1, BG)
    # a control byte ends a sequence unfinished, then acts as usual
    to_dev(s, csi("11;7H") + csi("3") + "\x0d" + "1mg")
    assert row_text(s, 10) == "1mgdef", row_text(s, 10)
    assert cell(s, 10, 0) == (ord("1"), FG, BG) and s.cursor(SCREEN1) == (3, 10)
    # ESC inside a sequence starts a new one
    to_dev(s, csi("12;1H") + csi("3") + csi("32mh") + csi("0m"))
    assert cell(s, 11, 0) == (ord("h"), 2, BG)
    # /Term: nothing printed for an unknown sequence between two markers
    rows = run(s, "display 5a 1b 5b 3f 31 30 34 39 68 5a 1b 5b 35 6e 5a")
    assert any(r.startswith("ZZZ") for r in rows), rows
    check_hardware(s)


def test_split_sequences():
    if not built():
        return
    s = booted()
    dwset(s, 0)
    # one byte per `display` call (one Write each, separate paths)
    for b in hexs(csi("1;33;44m") + "Q"):
        run(s, f"display {b} >/w1")
    assert cell(s, 0, 0) == (ord("Q"), 11, 4)
    # across two calls, in the middle of a parameter and of a 38;5;n
    to_dev(s, csi("0m") + csi("1"), chunk=99)
    to_dev(s, "2;1H" + csi("38;5;1"), chunk=99)
    to_dev(s, "96mR" + csi("0m"), chunk=99)
    assert cell(s, 11, 0) == (ord("R"), 9, BG)                # 196 on Classic: 9
    dwset(s, 1)
    to_dev(s, csi("38;5;1"), chunk=99)
    to_dev(s, "96mR" + csi("0m"), chunk=99)
    assert cell(s, 0, 0) == (ord("R"), 196, BG)
    # a Super UTF-8 sequence cut by ESC: dropped, the CSI acts
    to_dev(s, "\xe4\xb8" + csi("31m") + "A\xc3\xa9" + csi("0m"))
    assert cell(s, 0, 1) == (ord("A"), 1, BG) and cell(s, 0, 2) == (0xE9, 1, BG)
    # /Term: split across two commands on one line (nothing printed between)
    rows = run(s, "display 1b 5b 33; display 31 6d 59 1b 5b 30 6d")
    pos = [(r, c) for r in range(25) for c in range(80) if s.peek(SCREEN0, r * 80 + c)[0] == ord("Y")]
    assert pos, rows
    r, c = pos[-1]
    assert s.peek(SCREEN0, 0x7D0 + r * 80 + c)[0] == (BG << 4 | 1)
    check_hardware(s)


def test_coco_codes_mix():
    """CoCo FColor/BColor/reverse with SGR: bold brightens a CoCo colour, SGR 0 restores."""
    if not built():
        return
    s = booted()
    for fmt in (0, 1):
        dwset(s, fmt)
        to_dev(s, "\x1b\x32\x03" + csi("1m") + "a" + csi("22m") + "b" + "\x1b\x33\x02" + csi("7m") + "c"
               + "\x1f\x21" + "d" + csi("0m") + "e" + "\x1f\x20" + csi("27m") + "f")
        assert [cell(s, 0, c) for c in range(6)] == [
            (ord("a"), 11, BG), (ord("b"), 3, BG), (ord("c"), 2, 3), (ord("d"), 3, 2),
            (ord("e"), FG, BG), (ord("f"), FG, BG)], [cell(s, 0, c) for c in range(6)]
        # ESC $21 (Select) and the palette escape are not taken for CSI
        to_dev(s, "\x1b\x31\x05\x11\x22\x33" + "g")
        assert cell(s, 0, 6) == (ord("g"), FG, BG)
    to_dev(s, "\x1b\x31\x05\x80\x00\x80")                      # palette entry 5 back (xterm)
    check_hardware(s)


def test_term_classic():
    """/Term (Classic, the shell's screen): colours on the attribute byte, and back."""
    if not built():
        return
    s = booted()
    rows = run(s, "display 1b 5b 31 3b 33 34 3b 34 31 6d 4d 1b 5b 30 6d 4e")
    pos = [(r, c) for r in range(25) for c in range(79)
           if s.peek(SCREEN0, r * 80 + c, 2) == b"MN"]
    assert pos, rows
    r, c = pos[-1]
    assert list(s.peek(SCREEN0, 0x7D0 + r * 80 + c, 2)) == [0x1C, 0x0F]   # bg 1 fg 12; 15/0
    rows = run(s, "echo after")
    r = max(i for i, t in enumerate(rows) if t == "after")
    assert s.peek(SCREEN0, 0x7D0 + r * 80)[0] == 0x0F
    check_hardware(s)


def dump_classic(s, page=SCREEN1, rows=range(3)):
    out = []
    for r in rows:
        text = "".join(chr(c) if 32 <= c < 127 else "." for c in s.peek(page, r * 80, 80)).rstrip()
        n = max(len(text), 1)
        attrs = " ".join(f"{a:02X}" for a in s.peek(page, 0x7D0 + r * 80, n))
        out.append(f"row {r:2}: {text}\n        attr (bg fg): {attrs}")
    return "\n".join(out)


def dump_super(s, page=SCREEN1, rows=range(2), n=16):
    out = []
    for r in rows:
        cells = [s.super_cell(page, r, c) for c in range(n)]
        out.append(f"row {r:2}: " + " ".join(f"{chr(c) if c >= 32 else '.'}" .ljust(3) for c, _, _ in cells))
        out.append("    fg: " + " ".join(f"{f:<3}" for _, f, _ in cells))
        out.append("    bg: " + " ".join(f"{b:<3}" for _, _, b in cells))
    return "\n".join(out)


def dumps():
    s = booted()
    dwset(s, 0)
    to_dev(s, [csi("31m") + "red" + csi("1m") + "BOLD" + csi("0;44m") + "onblue" + csi("7m") + "rev",
               csi("0m") + " " + csi("38;5;208m") + "208" + csi("38;5;33m") + "33" + csi("48;5;244m") + "g244",
               csi("0m") + csi("3;1H") + csi("1;97;100m") + "bright" + csi("0m")])
    print("Classic /W1 (attr byte: high nibble bg, low nibble fg)")
    print(dump_classic(s))
    dwset(s, 1)
    to_dev(s, [csi("31m") + "red" + csi("1m") + "B" + csi("0;38;5;208;48;5;17m") + "208/17",
               csi("7m") + "rv" + csi("0m") + csi("2;1H") + csi("38;5;196m") + "196" + csi("0m")])
    print("Super /W1 (code, fg plane, bg plane)")
    print(dump_super(s))


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
    if "--dump" in sys.argv[1:] and built():
        dumps()
    if _session is not None:
        print(_session.report())
    sys.exit(1 if failed else 0)
