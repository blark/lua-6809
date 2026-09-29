#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["MC6809"]
# ///
"""
SCF line editing with UTF-8 input (the port assembles scf.asm with UTF8=1).
On /W1 switched to Super the keyboard sends UTF-8; a backspace must take a
whole code point out of the line buffer and echo one backspace (one cell).
Checks: backspace over 2- and 3-byte characters (the line the program gets,
byte for byte: echo piped into dump), cursor columns, backspace over ASCII,
backspace at the start of the line, the delete-line key (backspace over line,
IT.DLO 0) on a line with multi-byte characters, and ASCII editing on the
Classic /Term.

    uv run tests/emu/test_os9_utf8_edit.py

Needs the port's build outputs in ~/src/nitros9/recipes/anachron8/dw; skipped
when they are missing.
"""

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent.parent))
sys.path.insert(0, str(HERE))

from emu.os9boot import SCREEN0, SCREEN1, SCREENS, OS9Session  # noqa: E402
from test_os9_super import BOOT_LIMIT, STEP, acia, built, check_hardware, make_super  # noqa: E402

BS, DEL_LINE = "\x08", "\x18"        # IT.BSP, IT.DEL (Ctrl-X)

_session = None


def utf8(text):
    """Text as the key bytes the browser sends a Super screen."""
    return text.encode().decode("latin-1")


def booted():
    """Booted, /W1 Super and displayed, at a fresh prompt."""
    global _session
    if _session is None:
        s = OS9Session(trace=64)
        for page in SCREENS:
            reason, _, err = s.wait_prompt(BOOT_LIMIT, page)
            assert err is None and reason == "until" and not s.halted, (reason, err, s.screen_dump(page))
        make_super(s)
        assert s.switch_screen() == SCREEN1
        _session = s
        enter(s)
    return _session


def keys(s, text):
    """Type keys on the displayed screen and let the echo settle."""
    reason, _, err = s.type(text, STEP)
    assert reason == "until", (reason, err)
    s.run(200_000)                                  # the echo drains


def enter(s, page=SCREEN1):
    """Enter, then run to the next prompt; returns the screen rows."""
    keys(s, "\r")
    reason, _, _ = s.wait_prompt(STEP, page)
    assert reason == "until", s.screen_dump(page)
    return s.super_screen(page) if page == SCREEN1 else s.screen(page)


def prompt_col(s):
    col, row = s.cursor(SCREEN1)
    assert s.at_prompt(SCREEN1), s.screen_dump(SCREEN1)
    return col, row


def cell_codes(s, row, start, n):
    return [s.super_cell(SCREEN1, row, c)[0] for c in range(start, start + n)]


def piped_bytes(s, line):
    """The bytes `line` (typed keys, then ` ! dump >/t1`) writes to its standard
    output: dump reads the pipe and prints hex on /T1."""
    keys(s, line + " ! dump >/t1")
    start = len(s.m.mem.console_output)
    enter(s)
    out = "".join(s.m.mem.console_output[start:]).replace("\r", "\n")
    data = bytearray()
    for ln in out.split("\n"):
        if len(ln) > 9 and all(c in "0123456789ABCDEF" for c in ln[:8]):
            data += bytes.fromhex(ln[9:49].replace(" ", ""))
    return bytes(data), out


def test_backspace_multibyte():
    if not built():
        print("skip  test_backspace_multibyte: no NitrOS-9 build")
        return
    s = booted()
    col0, row = prompt_col(s)
    keys(s, utf8("echo aé中"))
    assert s.cursor(SCREEN1) == (col0 + 8, row)
    assert s.super_screen(SCREEN1)[row].endswith("echo aé中")
    keys(s, BS)                                     # 中 (3 bytes): one cell
    assert s.cursor(SCREEN1) == (col0 + 7, row)
    keys(s, BS)                                     # é (2 bytes): one cell
    assert s.cursor(SCREEN1) == (col0 + 6, row)
    assert cell_codes(s, row, col0 + 5, 3) == [ord("a"), 0x20, 0x20]
    keys(s, "b")
    assert s.cursor(SCREEN1) == (col0 + 7, row)
    rows = enter(s)
    assert rows[row].endswith(":echo ab") and rows[row + 1] == "ab", s.screen_dump(SCREEN1)
    check_hardware(s)


def test_line_bytes():
    """The line SCF hands the program, byte for byte: echo writes its arguments."""
    if not built():
        return
    s = booted()
    data, out = piped_bytes(s, utf8("echo aé中") + BS + BS + "b")
    assert data == b"ab \r", (data, out)          # echo keeps the blank before "!"
    data, out = piped_bytes(s, utf8("echo xΩ─yé") + BS + BS + BS + "z")
    assert data == "xΩz \r".encode(), (data, out)


def test_backspace_ascii():
    if not built():
        return
    s = booted()
    col0, row = prompt_col(s)
    keys(s, "echo xyz")
    keys(s, BS)
    assert s.cursor(SCREEN1) == (col0 + 7, row)
    keys(s, "q")
    rows = enter(s)
    assert rows[row + 1] == "xyq", s.screen_dump(SCREEN1)


def test_backspace_at_line_start():
    if not built():
        return
    s = booted()
    col0, row = prompt_col(s)
    keys(s, BS + BS)
    assert s.cursor(SCREEN1) == (col0, row)         # nothing to erase: no echo
    assert s.super_screen(SCREEN1)[row].endswith(":")
    keys(s, utf8("é") + BS + BS)                    # erase é, then nothing
    assert s.cursor(SCREEN1) == (col0, row)
    keys(s, "echo ok")
    rows = enter(s)
    assert rows[row].endswith(":echo ok") and rows[row + 1] == "ok", s.screen_dump(SCREEN1)


def test_delete_line_multibyte():
    if not built():
        return
    s = booted()
    col0, row = prompt_col(s)
    keys(s, utf8("echo é中Ωx"))
    assert s.cursor(SCREEN1) == (col0 + 9, row)
    keys(s, DEL_LINE)                               # IT.DLO 0: backspace over the line
    assert s.cursor(SCREEN1) == (col0, row), s.screen_dump(SCREEN1)
    assert cell_codes(s, row, col0, 10) == [0x20] * 10
    keys(s, "echo fine")
    rows = enter(s)
    assert rows[row].endswith(":echo fine") and rows[row + 1] == "fine", s.screen_dump(SCREEN1)
    check_hardware(s)


def test_classic_term_ascii():
    """/Term (Classic, ASCII keys only): backspace and delete line as before."""
    if not built():
        return
    s = booted()
    keys(s, "\x02")                                 # show /Term
    assert s.displayed() == SCREEN0
    try:
        col0, row = s.cursor(SCREEN0)
        keys(s, "echo abc" + BS + "d")
        assert s.cursor(SCREEN0) == (col0 + 8, row), (s.cursor(SCREEN0), col0, row)
        rows = enter(s, SCREEN0)
        assert rows[row + 1] == "abd", s.screen_dump(SCREEN0)
        col0, row = s.cursor(SCREEN0)
        keys(s, "echo gone" + DEL_LINE)
        assert s.cursor(SCREEN0) == (col0, row), (s.cursor(SCREEN0), col0, row)
        keys(s, "echo kept")
        rows = enter(s, SCREEN0)
        assert rows[row + 1] == "kept", s.screen_dump(SCREEN0)
        got = acia(s, "echo still>/t1")
        assert got == ["still"], got
    finally:
        keys(s, "\x02")                             # back to /W1
    assert s.displayed() == SCREEN1, s.displayed()
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
