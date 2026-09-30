#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["MC6809"]
# ///
"""
The commands added to the NitrOS-9 disk (anachron8 port) for a fuller set,
on the emulated Anachron8:
  - the disk: cputype and minted in CMDS with their help (and pick's),
    no CoCo-only command (keyrpt, grfdrv, montype, wcreate, os9gen,
    cobbler, gfx2)
  - cputype: "CPU: 6809" ($10 $4F runs as CLRA on a 6809)
  - minted on an existing file: type, Ctrl-S, y, Ctrl-E; the file saved
  - format (logical), dcheck and backup on DriveWire drives (small
    images attached as /x2 and /x3)
  - minted on a new file: its "press break" wait, Ctrl-E (the quit key,
    S$Abort), then the empty buffer typed in and saved. The wait is a user
    loop with no system call: before clock_a8 edition 2 the key's IRQ
    returned carry set, the kernel masked IRQs in the loop's CC and the
    machine hung there.

    uv run tests/emu/test_os9_cmds.py

Needs the port's build outputs in ~/src/nitros9/recipes/anachron8/dw;
skipped when they are missing.
"""

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent.parent))
sys.path.insert(0, str(HERE))

from emu.os9boot import DISK, KERNEL, KERNEL_JSON, SCREEN0, OS9Session  # noqa: E402
from test_os9_ls import Disk  # noqa: E402

BOOT_LIMIT = 10_000_000
STEP = 50_000_000
KEY_STEP = 20_000_000
SAVE, QUIT = "\x13", "\x05"            # minted: Ctrl-S save; Ctrl-E exit (and the quit key)
# minted's status row (24) reads "Save file (y/n)?", then "Saving", then is
# blank again with the text redrawn; minted then needs a moment to reach its
# key read (SETTLE), or the quit key's signal lands in its last write
SETTLE = 5_000_000

_session = None


def built():
    return all(p.exists() for p in (KERNEL, KERNEL_JSON, DISK))


def skip(name):
    print(f"skip  {name}: no NitrOS-9 build")


def booted():
    global _session
    if _session is None:
        s = OS9Session(trace=64)
        reason, _, err = s.wait_prompt(BOOT_LIMIT)
        assert err is None and reason == "until" and not s.halted, (reason, err, s.screen_dump())
        _session = s
    return _session


def run(s, line):
    reason, rows = s.command(line, STEP)
    assert reason == "until", (line, reason, s.screen_dump())
    return rows


def keys(s, text, until, what):
    s.type(text, KEY_STEP)
    reason, _, _ = s.wait_screen(until, STEP)
    assert reason == "until", (what, s.screen_dump())


def file_data(s, path):
    d = Disk(s.disk)
    return d.data(d.lookup(path))


def test_disk():
    if not built():
        return skip("test_disk")
    d = Disk(DISK)
    cmds = {n for n, _ in d.entries(d.lookup("/dd/CMDS"))}
    assert {"cputype", "minted"} <= cmds, cmds
    assert not cmds & {"keyrpt", "grfdrv", "montype", "wcreate", "os9gen", "cobbler", "gfx2", "gfx"}
    help_ = d.data(d.lookup("/dd/SYS/helpmsg"))
    for topic in (b"@CPUTYPE", b"@MINTED", b"@PICK"):
        assert topic in help_, topic


def test_cputype():
    if not built():
        return skip("test_cputype")
    s = booted()
    rows = run(s, "cputype")
    i = max(i for i, r in enumerate(rows) if r.endswith("cputype"))
    assert rows[i + 1] == "CPU: 6809", rows[i:i + 3]


def test_minted_existing_file():
    if not built():
        return skip("test_minted_existing_file")
    s = booted()
    run(s, "echo first line >/dd/m.txt")                  # (echo keeps the space before ">")
    s.type("minted /dd/m.txt\r", KEY_STEP)
    keys(s, "", lambda: s.screen()[0] == "first line" and s.cursor(SCREEN0) == (0, 0), "open")
    keys(s, "Hello ", lambda: s.screen()[0] == "Hello first line", "type")
    keys(s, SAVE, lambda: "(y/n)?" in s.screen()[24], "save prompt")
    keys(s, "y", lambda: not s.screen()[24] and s.cursor(SCREEN0) == (6, 0), "saved")
    s.run(SETTLE)
    s.type(QUIT, KEY_STEP)
    assert s.wait_prompt(STEP)[0] == "until", s.screen_dump()
    assert file_data(s, "/dd/m.txt") == b"Hello first line \r", file_data(s, "/dd/m.txt")


def test_minted_new_file_break():
    if not built():
        return skip("test_minted_new_file_break")
    s = booted()
    s.type("minted /dd/new.txt\r", KEY_STEP)
    keys(s, "", lambda: "press break" in s.screen()[24], "not found")
    s.run(SETTLE)                        # minted clears its signal flag after the message
    keys(s, QUIT, lambda: "press break" not in s.screen()[24], "break")      # the busy loop ends
    keys(s, "abc", lambda: s.screen()[0] == "abc", "type")
    keys(s, SAVE, lambda: "(y/n)?" in s.screen()[24], "save prompt")
    keys(s, "y", lambda: not s.screen()[24] and s.cursor(SCREEN0) == (3, 0), "saved")
    s.run(SETTLE)
    s.type(QUIT, KEY_STEP)
    assert s.wait_prompt(STEP)[0] == "until", s.screen_dump()
    assert file_data(s, "/dd/new.txt") == b"abc", file_data(s, "/dd/new.txt")


def dialog(s, line, answers):
    """Type a command, answer its prompts in turn ((prompt text at the cursor, key)),
    and run to the shell prompt; returns the screen rows."""
    s.type(line + "\r", KEY_STEP)
    for prompt, key in answers:
        reason, _, _ = s.wait_screen(lambda: (s.cursor_line(SCREEN0)[0] or "").endswith(prompt), STEP)
        assert reason == "until", (line, prompt, s.screen_dump())
        s.type(key, KEY_STEP)
    reason, _, _ = s.wait_prompt(STEP)
    assert reason == "until", (line, s.screen_dump())
    return s.screen()


def test_disk_tools_on_drivewire():
    """format (logical), dcheck and backup on two small DriveWire drives (/x2, /x3)."""
    if not built():
        return skip("test_disk_tools_on_drivewire")
    s = booted()
    x2 = s.m.dw.attach(2, None, sectors=630)             # 35 cylinders of 18 sectors
    x3 = s.m.dw.attach(3, None, sectors=630)
    for dev, name in (("/x2", "scratch"), ("/x3", "other")):
        dialog(s, f"format {dev} r l \"{name}\" '35'",
               [("are you sure?", "y"), ("Physical Verify desired?", "n")])
    run(s, "copy /dd/startup /x2/s")
    rows = run(s, "dcheck /x2")
    assert "'scratch' file structure is intact" in rows, rows
    rows = dialog(s, "backup /x2 /x3", [("Ready to backup from /x2 to /x3 ?:", "y"), ("Ok ?:", "y")])
    assert "Backup Aborted" not in rows, rows
    assert x3.data == x2.data                             # a sector-for-sector copy
    rows = run(s, "dcheck /x3")
    assert "'scratch' file structure is intact" in rows, rows


def main():
    tests = [(n, f) for n, f in globals().items() if n.startswith("test_") and callable(f)]
    failed = 0
    for n, f in tests:
        try:
            f()
            print(f"ok    {n}", flush=True)
        except AssertionError as e:
            failed += 1
            line = e.__traceback__.tb_next.tb_lineno if e.__traceback__.tb_next else "?"
            print(f"FAIL  {n} (line {line}): {e}", flush=True)
    print(f"{len(tests) - failed}/{len(tests)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
