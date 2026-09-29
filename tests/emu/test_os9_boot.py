#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["MC6809"]
# ///
"""
Boot NitrOS-9 Level 2 (the anachron8 port) with the catalog type 4 loader
(emu/os9boot.py) to the Shell+ prompt, then run `dir` and `mfree`.

    uv run tests/emu/test_os9_boot.py

Needs the port's build outputs in ~/src/nitros9/recipes/anachron8/dw
(kernel, kernel.json, l2_anachron8_dw.dsk); skipped when they are missing.
About 1.5 million instructions, a few seconds.
"""

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent.parent))

from emu.os9boot import DISK, KERNEL, KERNEL_JSON, PROMPT, OS9Session  # noqa: E402

BOOT_LIMIT = 5_000_000          # instructions; the boot takes about 1.4 million


def built():
    return all(p.exists() for p in (KERNEL, KERNEL_JSON, DISK))


def test_os9_boot_to_shell():
    if not built():
        print("skip  test_os9_boot_to_shell: no NitrOS-9 build")
        return
    s = OS9Session(trace=64)
    reason, _, err = s.wait_for(PROMPT, BOOT_LIMIT)
    assert err is None and reason == "until" and not s.halted, (reason, err, s.console())
    out = s.console()
    assert "NitrOS-9/6809 Level 2" in out and "Shell+" in out

    reason, text = s.command("dir")
    assert reason == "until", (reason, text)
    assert "OS9Boot" in text and "CMDS" in text and "startup" in text

    reason, text = s.command("mfree")
    assert reason == "until", (reason, text)
    assert "Total:" in text

    # memory sizing: 256 pages; the screens VidRAM, the ROM page and page $FF NotRAM
    bm = s.blkmap()
    assert len(bm) == 256 and bytes(bm[0xF8:]) == bytes([0, 0, 0, 0, 4, 4, 0x80, 0x80])
    assert not s.drive0, s.drive0                      # the boot catalog is never touched
    assert not s.special_writes, s.special_writes[:5]  # nothing writes pages $FC-$FF


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
    sys.exit(1 if failed else 0)
