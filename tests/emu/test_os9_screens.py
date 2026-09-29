#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["MC6809"]
# ///
"""
NitrOS-9's two screens: /Term on screen 0 (page $FC) and /W1 on screen 1
(page $FD), both a8vtio + CoClassic, each with a shell (the startup file runs
`shell i=/w1&`). The keyboard goes to the displayed screen (VIDEO_CTRL bit 0);
Ctrl-B shows the other one, ESC $21 (CoWin's Select) written to a device shows
that device. Checks: both prompts on their own pages, commands typed after a
switch run and draw on the displayed screen only, output to the hidden screen
still lands in its page, keys queued behind the switch key follow it, and the
hardware rules (no upper-4 KB writes, FORMAT 0, block map, no drive 0).

    uv run tests/emu/test_os9_screens.py

Needs the port's build outputs in ~/src/nitros9/recipes/anachron8/dw; skipped
when they are missing.
"""

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent.parent))

from emu.os9boot import (DISK, KERNEL, KERNEL_JSON, SCREEN0, SCREEN1, SCREENS,  # noqa: E402
                         SWITCH_KEY, OS9Session)

BOOT_LIMIT = 10_000_000         # instructions to both prompts
STEP = 50_000_000

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


def text(s, page):
    return "\n".join(s.screen(page))


def run(s, line, page):
    reason, rows = s.command(line, STEP, page)
    assert reason == "until", (line, reason, s.screen_dump(page))
    assert s.at_prompt(page), s.screen_dump(page)
    return rows


def check_hardware(s):
    bm = s.blkmap()
    assert len(bm) == 256 and bytes(bm[0xF8:]) == bytes([0, 0, 0, 0, 4, 4, 0x80, 0x80])
    assert not s.drive0, s.drive0
    other = [w for w in s.special_writes if w[0] not in SCREENS]
    assert not other, other[:5]
    for page in SCREENS:
        high = [w for w in s.screen_writes(page) if w[0] >= 0x1000]
        assert not high, (hex(page), high[:5])
        assert s.peek(page, 0xFA2)[0] == 0, hex(page)


def test_two_shells():
    if not built():
        print("skip  test_two_shells: no NitrOS-9 build")
        return
    s = booted()
    assert s.displayed() == SCREEN0                          # /Term is shown after the boot
    term, w1 = text(s, SCREEN0), text(s, SCREEN1)
    assert "{Term|" in term and "{W1|" not in term, s.screen_dump(SCREEN0)
    assert "{W1|" in w1 and "Shell+" in w1 and "{Term|" not in w1, s.screen_dump(SCREEN1)
    assert "NitrOS-9 Level 2 on the Anachron8" not in w1     # the startup ran on /Term
    assert s.peek(SCREEN1, 0x7D0)[0] == 0x0F                 # white on black
    assert s.screen_writes(SCREEN1)                          # /W1 draws into page $FD
    check_hardware(s)


def test_command_on_w1():
    if not built():
        return
    s = booted()
    before = s.screen(SCREEN0)
    assert s.switch_screen() == SCREEN1
    rows = run(s, "echo on-w1", SCREEN1)
    assert "on-w1" in rows and any(r.endswith(":echo on-w1") for r in rows), s.screen_dump(SCREEN1)
    assert s.screen(SCREEN0) == before, s.screen_dump(SCREEN0)   # /Term saw nothing
    rows = run(s, "procs", SCREEN1)
    assert any("Shell" in r for r in rows), s.screen_dump(SCREEN1)
    assert s.displayed() == SCREEN1
    check_hardware(s)


def test_switch_back():
    if not built():
        return
    s = booted()
    if s.displayed() != SCREEN0:
        before = s.screen(SCREEN1)
        # the switch key and the keys queued behind it: they go to /Term
        reason, _, err = s.type(SWITCH_KEY + "echo on-term", STEP)
        assert reason == "until", (reason, err)
        assert s.displayed() == SCREEN0
        reason, _, _ = s.wait_screen(lambda: s.cursor_line(SCREEN0)[0].endswith(":echo on-term"), STEP)
        assert reason == "until", s.screen_dump(SCREEN0)
        s.type("\r", STEP)
        reason, _, _ = s.wait_prompt(STEP, SCREEN0)
        assert reason == "until", s.screen_dump(SCREEN0)
        assert "on-term" in s.screen(SCREEN0)
        assert s.screen(SCREEN1) == before, s.screen_dump(SCREEN1)
    rows = run(s, "echo term-again", SCREEN0)
    assert "term-again" in rows and "term-again" not in text(s, SCREEN1)
    check_hardware(s)


def test_output_to_hidden_screen():
    if not built():
        return
    s = booted()
    assert s.displayed() == SCREEN0
    run(s, "echo to-w1 >/w1", SCREEN0)
    # drawn on page $FD (after /W1's prompt, where its cursor was); /Term shows only the command
    assert any(r.endswith(":to-w1") for r in s.screen(SCREEN1)), s.screen_dump(SCREEN1)
    assert "to-w1" not in s.screen(SCREEN0), s.screen_dump(SCREEN0)  # no row of output here
    assert s.displayed() == SCREEN0                                   # still showing /Term
    check_hardware(s)


def test_select_escape():
    if not built():
        return
    s = booted()
    assert s.displayed() == SCREEN0
    run(s, "display 1B 21 >/w1", SCREEN0)                   # ESC $21: show /W1
    assert s.displayed() == SCREEN1
    rows = run(s, "echo selected", SCREEN1)                 # and the keyboard went with it
    assert "selected" in rows, s.screen_dump(SCREEN1)
    run(s, "display 1B 21 >/term", SCREEN1)                 # and back
    assert s.displayed() == SCREEN0
    rows = run(s, "echo home", SCREEN0)
    assert "home" in rows
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
