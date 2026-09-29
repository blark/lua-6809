#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["MC6809"]
# ///
"""
Boot NitrOS-9 Level 2 (the anachron8 port) with the catalog type 4 loader
(emu/os9boot.py) to the Shell+ prompt on the screen console (/Term =
a8vtio + CoClassic on page $FC), then type commands on the keyboard and
check the screen: `dir`, `mfree`, scrolling (`mdir -e`, `list startup`
several times), line editing with the browser's Delete and Left keys, Ctrl-C,
cursor codes, and the serial port /T1 (`echo hi >/t1`).

    uv run tests/emu/test_os9_boot.py

Needs the port's build outputs in ~/src/nitros9/recipes/anachron8/dw
(kernel, kernel.json, l2_anachron8_dw.dsk); skipped when they are missing.
About 1.5 million instructions to the prompt.
"""

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent.parent))

from emu.os9boot import DISK, KERNEL, KERNEL_JSON, PROMPT, SCREEN0, SCREENS, OS9Session  # noqa: E402

BOOT_LIMIT = 5_000_000          # instructions; the boot takes about 1.5 million
ROWS, COLS = 25, 80

_session = None


def built():
    return all(p.exists() for p in (KERNEL, KERNEL_JSON, DISK))


def booted():
    """One booted session shared by the tests (they run in order)."""
    global _session
    if _session is None:
        s = OS9Session(trace=64)
        reason, _, err = s.wait_prompt(BOOT_LIMIT)
        assert err is None and reason == "until" and not s.halted, (reason, err, s.screen_dump())
        _session = s
    return _session


def run(s, line):
    reason, rows = s.command(line)
    assert reason == "until", (line, reason, s.screen_dump())
    assert s.at_prompt(), s.screen_dump()
    return rows


def check_hardware(s):
    # memory sizing: 256 pages; the screens VidRAM, the ROM page and page $FF NotRAM
    bm = s.blkmap()
    assert len(bm) == 256 and bytes(bm[0xF8:]) == bytes([0, 0, 0, 0, 4, 4, 0x80, 0x80])
    assert not s.drive0, s.drive0                            # the boot catalog is never touched
    other = [w for w in s.special_writes if w[0] not in SCREENS]
    assert not other, other[:5]                              # nothing writes pages $FE-$FF
    for page in SCREENS:                                     # /Term's and /W1's screens:
        high = [w for w in s.screen_writes(page) if w[0] >= 0x1000]
        assert not high, high[:5]                            # never the upper 4 KB
        assert s.peek(page, 0xFA2)[0] == 0                   # FORMAT: Classic


def test_os9_boot_to_shell():
    if not built():
        print("skip  test_os9_boot_to_shell: no NitrOS-9 build")
        return
    s = booted()
    text = "\n".join(s.screen())
    assert "NitrOS-9/6809 Level 2" in text and "Shell+" in text, s.screen_dump()
    assert s.console() == "", s.console()                    # nothing on the ACIA
    col, row = s.cursor()
    assert PROMPT.fullmatch(s.screen()[row][:col])
    assert s.peek(SCREEN0, 0x7D0)[0] == 0x0F                 # white on black
    check_hardware(s)


def test_os9_dir_and_mfree():
    if not built():
        return
    s = booted()
    text = "\n".join(run(s, "dir"))
    assert "OS9Boot" in text and "CMDS" in text and "startup" in text, s.screen_dump()
    rows = run(s, "mfree")
    assert any("Total:" in r for r in rows), s.screen_dump()
    check_hardware(s)


def test_os9_scrolling():
    if not built():
        return
    s = booted()
    rows = run(s, "mdir -e")                                 # ~55 lines: scrolls
    assert s.cursor()[1] == ROWS - 1, s.screen_dump()        # the prompt on the bottom row
    # the listing's tail, its header scrolled off the top
    assert rows[-3].endswith(" TMode") and rows[-4].endswith(" Rename"), s.screen_dump()
    assert not any("Module Directory" in r for r in rows), s.screen_dump()
    for _ in range(4):
        rows = run(s, "list startup")
    # the last listings, whole and in order, the prompt at the bottom
    assert rows[-1].startswith("{Term|") and s.cursor()[1] == ROWS - 1
    listed = [i for i, r in enumerate(rows) if r == "* Anachron8 startup"]
    assert len(listed) >= 2, s.screen_dump()
    for i in listed:
        assert rows[i + 1] == "echo * NitrOS-9 Level 2 on the Anachron8 *", s.screen_dump()
    check_hardware(s)


def test_os9_keyboard_editing():
    if not built():
        return
    s = booted()
    # Delete ($7F) and Left ($1C) arrive as backspace: "mfrXY" -> "mfr", then "ee"
    reason, _, err = s.type("mfrXY\x7f\x1cee", 5_000_000)
    assert reason == "until", (reason, err)
    reason, _, err = s.wait_screen(lambda: s.cursor_line()[0].endswith(":mfree"), 5_000_000)
    assert reason == "until", (reason, err, s.screen_dump())
    reason, _, err = s.type("\r", 5_000_000)
    reason, _, err = s.wait_prompt(50_000_000)
    assert reason == "until", (reason, err, s.screen_dump())
    rows = s.screen()
    typed = [r for r in rows if r.startswith("{Term|") and "mfr" in r]
    assert typed and typed[-1].endswith(":mfree"), s.screen_dump()
    assert any("Total:" in r for r in rows), s.screen_dump()


def test_os9_interrupt_key():
    if not built():
        return
    s = booted()
    # Ctrl-C (V.INTR) signals the process on /Term: sleep dies with error 3 (S$Intrpt)
    s.type("sleep 3000", 5_000_000)
    reason, _, _ = s.wait_screen(lambda: s.cursor_line()[0].endswith(":sleep 3000"), 5_000_000)
    assert reason == "until", s.screen_dump()
    s.type("\r", 5_000_000)
    reason, _, _ = s.run(300_000)
    assert reason == "count" and not s.at_prompt(), s.screen_dump()
    s.type("\x03", 5_000_000)
    reason, _, _ = s.wait_prompt(5_000_000)
    row = s.cursor()[1]                                      # error, lead-in line feed, prompt
    assert reason == "until" and s.screen()[row - 2] == "ERROR #003", s.screen_dump()


def test_os9_cursor_codes():
    if not built():
        return
    s = booted()
    # $0C clears and homes, $02 x+$20 y+$20 moves: ABC at column 10, row 5
    run(s, "display 0C 02 2A 25 41 42 43 0D 0A")
    rows = s.screen()
    assert rows[:5] == [""] * 5 and rows[5] == " " * 10 + "ABC", s.screen_dump()
    # CR LF, then Shell+'s lead-in line feed before its prompt
    assert rows[6] == "" and rows[7].startswith("{Term|") and s.cursor()[1] == 7, s.screen_dump()
    # reverse video around X: its attribute has the nibbles swapped
    run(s, "display 1F 20 58 1F 21 59 0D 0A")
    scr = bytes(s.peek(SCREEN0, 0, 2000))
    attr = bytes(s.peek(SCREEN0, 0x7D0, 2000))
    i = scr.index(b"XY")
    assert (attr[i], attr[i + 1]) == (0xF0, 0x0F), (hex(attr[i]), hex(attr[i + 1]))
    # ESC $32 2 (FColor 2) before Z, ESC $32 15 after it
    run(s, "display 1B 32 02 5A 1B 32 0F 0D 0A")
    scr = bytes(s.peek(SCREEN0, 0, 2000))
    attr = bytes(s.peek(SCREEN0, 0x7D0, 2000))
    assert attr[scr.index(b"Z")] == 0x02
    check_hardware(s)


def test_os9_t1_serial():
    if not built():
        return
    s = booted()
    run(s, "echo hi >/t1")
    assert "hi" in s.console(), repr(s.console())
    check_hardware(s)


def test_os9_files_on_dd():
    """Create, copy and delete files on /DD: the image must be as large as its format
    (the DriveWire server never grows an image, and answers E$Sect past its end)."""
    if not built():
        return
    s = booted()
    for line in ("echo hello file >/dd/t1", "copy /dd/startup /dd/t2", "makdir /dd/new",
                 "copy /dd/t2 /dd/new/t3", "list /dd/t1", "dir /dd/new"):
        run(s, line)
    text = s.screen_dump()
    assert "hello file" in text and "t3" in text and "ERROR" not in text, text
    for line in ("del /dd/new/t3", "del /dd/t1", "del /dd/t2"):
        run(s, line)
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
        print(_session.screen_dump())
    sys.exit(1 if failed else 0)
