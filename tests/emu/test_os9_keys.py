#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["MC6809"]
# ///
"""
Arrow keys and Shell+ history on the screen consoles (a8vtio edition 5,
term_a8 edition 4). The browser sends Up $0B, Down $0A, Left $1C, Right $0C;
a8vtio makes them the CoCo codes $0C, $0A, $08, $09. While Shell+ waits for
a command, Up/Down are its history signal keys and a8vtio follows them with
Shift-Right ($19, IT.DUP), so SCF prints the recalled line and the cursor ends
behind it. IT.RPR = Right turns SCF's line editor on: Left erases a
character, Right brings the next one of the buffer back.

Checks, on the Classic /Term and on /W1 switched to Super: Up recalls the
last line with the cursor at its end; Ctrl-X then blanks it on screen, the
cursor goes back after the prompt and Enter runs nothing; Up and Enter run
the recalled line; Left/Right and typing inside a recalled line; Up, Up,
Down walk the history.

    uv run tests/emu/test_os9_keys.py

Needs the port's build outputs in ~/src/nitros9/recipes/anachron8/dw; skipped
when they are missing.
"""

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent.parent))
sys.path.insert(0, str(HERE))

from emu.os9boot import SCREEN0, SCREEN1, SCREENS, OS9Session  # noqa: E402
from test_os9_super import BOOT_LIMIT, STEP, built, check_hardware, make_super  # noqa: E402

UP, DOWN, LEFT, RIGHT = "\x0b", "\x0a", "\x1c", "\x0c"   # as the browser sends them
DEL_LINE = "\x18"                                         # Ctrl-X

_session = None


def booted():
    """Booted; /W1 made Super; /Term displayed, both at a fresh prompt."""
    global _session
    if _session is None:
        s = OS9Session(trace=64)
        for page in SCREENS:
            reason, _, err = s.wait_prompt(BOOT_LIMIT, page)
            assert err is None and reason == "until" and not s.halted, (reason, err, s.screen_dump(page))
        make_super(s)
        assert s.displayed() == SCREEN0
        _session = s
    return _session


def keys(s, text):
    """Type keys on the displayed screen and let the echo settle."""
    reason, _, err = s.type(text, STEP)
    assert reason == "until", (reason, err)
    s.run(400_000)


def row_text(s, page, row):
    return (s.super_screen(page) if page == SCREEN1 else s.screen(page))[row]


def enter(s, page):
    """Enter, then run to the next prompt; returns the rows."""
    keys(s, "\r")
    reason, _, _ = s.wait_prompt(STEP, page)
    assert reason == "until", s.screen_dump(page)
    return s.super_screen(page) if page == SCREEN1 else s.screen(page)


def recall(s, page, key=UP):
    """Up or Down: Shell+ starts a new prompt line after the first one; returns
    the cursor (column, row)."""
    keys(s, key)
    return s.cursor(page)


def fresh(s, page):
    """A command run, then the prompt: (prompt column, row)."""
    col0, row = s.cursor(page)
    assert s.at_prompt(page), s.screen_dump(page)
    return col0, row


def check_up_ctrlx(s, page):
    col0, row = fresh(s, page)
    keys(s, "echo abc")
    rows = enter(s, page)
    assert rows[row + 1] == "abc", s.screen_dump(page)
    col0, row = fresh(s, page)
    prompt = row_text(s, page, row)
    col, row = recall(s, page)
    assert row_text(s, page, row) == prompt + "echo abc", s.screen_dump(page)
    assert col == col0 + 8, (col, col0, s.screen_dump(page))
    keys(s, DEL_LINE)
    assert row_text(s, page, row) == prompt, s.screen_dump(page)
    assert s.cursor(page) == (col0, row), s.screen_dump(page)
    rows = enter(s, page)                           # an empty line: nothing runs
    assert rows[row] == prompt and rows[row + 1] == prompt, s.screen_dump(page)
    col0, row = fresh(s, page)
    col, row = recall(s, page)
    assert col == col0 + 8, s.screen_dump(page)
    rows = enter(s, page)                           # the recalled line runs
    assert rows[row] == prompt + "echo abc" and rows[row + 1] == "abc", s.screen_dump(page)


def check_left_right(s, page):
    keys(s, "echo abc")
    enter(s, page)
    col0, row = fresh(s, page)
    prompt = row_text(s, page, row)
    col, row = recall(s, page)                      # echo abc
    assert col == col0 + 8, s.screen_dump(page)
    keys(s, LEFT + LEFT)                            # erases c, b
    assert s.cursor(page) == (col0 + 6, row), s.screen_dump(page)
    assert row_text(s, page, row) == prompt + "echo a", s.screen_dump(page)
    keys(s, RIGHT)                                  # b comes back
    assert s.cursor(page) == (col0 + 7, row), s.screen_dump(page)
    assert row_text(s, page, row) == prompt + "echo ab", s.screen_dump(page)
    keys(s, "d")
    rows = enter(s, page)
    assert rows[row] == prompt + "echo abd" and rows[row + 1] == "abd", s.screen_dump(page)


def check_history_walk(s, page):
    for word in ("one", "two"):
        col0, row = fresh(s, page)
        keys(s, "echo " + word)
        enter(s, page)
    col0, row = fresh(s, page)
    prompt = row_text(s, page, row)
    for key, line in ((UP, "echo two"), (UP, "echo one"), (DOWN, "echo two")):
        col, row = recall(s, page, key)             # each on a new prompt line
        assert row_text(s, page, row) == prompt + line, (key, s.screen_dump(page))
        assert col == col0 + len(line), (key, col, s.screen_dump(page))
    rows = enter(s, page)
    assert rows[row + 1] == "two", s.screen_dump(page)


def cls(s, page):
    """Clear the screen (display 0c), so the rows checked never scroll."""
    keys(s, "display 0c")
    enter(s, page)
    assert s.cursor(page)[1] < 3, s.screen_dump(page)


def on_term(check):
    if not built():
        print(f"skip  {check.__name__}: no NitrOS-9 build")
        return
    s = booted()
    assert s.displayed() == SCREEN0
    cls(s, SCREEN0)
    check(s, SCREEN0)
    check_hardware(s)


def on_w1(check):
    if not built():
        return
    s = booted()
    assert s.switch_screen() == SCREEN1
    try:
        assert s.is_super(SCREEN1)
        cls(s, SCREEN1)
        check(s, SCREEN1)
    finally:
        assert s.switch_screen() == SCREEN0
    check_hardware(s)


def test_term_up_ctrlx():
    on_term(check_up_ctrlx)


def test_term_left_right():
    on_term(check_left_right)


def test_term_history_walk():
    on_term(check_history_walk)


def test_w1_super_up_ctrlx():
    on_w1(check_up_ctrlx)


def test_w1_super_left_right():
    on_w1(check_left_right)


def test_w1_super_history_walk():
    on_w1(check_history_walk)


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
    if _session is not None and failed:
        print(_session.report())
        for page in SCREENS:
            print(_session.screen_dump(page))
    sys.exit(1 if failed else 0)
