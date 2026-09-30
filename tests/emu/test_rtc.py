#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["MC6809"]
# ///
"""
Tests for the RTC model of emu/board.py ($FF58-$FF5F, anachron8 rtl/rtc.py).

    uv run tests/emu/test_rtc.py
"""

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent.parent))

from emu.a8run import Machine  # noqa: E402
from emu.anachron8 import BusError  # noqa: E402
from emu.board import E_HZ  # noqa: E402

SEP29 = 1_790_729_107                   # 2026-09-30 00:45:07 UTC (Tue 20:45:07 at -240)


def regs(m):
    return bytes(m.mem.read_byte(0xFF58 + i) for i in range(8))


def latch(m):
    m.mem.write_byte(0xFF58, 0)
    return regs(m)


def test_unset_counts_from_zero():
    m = Machine()
    assert regs(m) == bytes(8)                              # the latch before any latch
    m.cpu.cycles += 3 * E_HZ + E_HZ // 2
    assert latch(m) == bytes([0, 0, 0, 3, 0x80, 0, 0, 0])   # 3.5 s, VALID 0
    assert m.mem.rtc.latches == 1


def test_set_advance_offset():
    m = Machine()
    m.mem.set_clock(SEP29, offset=-240, frac=0x40)
    r = latch(m)
    assert r == SEP29.to_bytes(4, "big") + bytes([0x40, 0x80]) + (-240 & 0xFFFF).to_bytes(2, "big")
    m.cpu.cycles += 2 * E_HZ
    assert regs(m) == r                                     # reads never latch
    assert latch(m)[:5] == (SEP29 + 2).to_bytes(4, "big") + b"\x40"
    m.mem.reset()                                           # a CPU reset: still running
    assert latch(m)[:4] == (SEP29 + 2).to_bytes(4, "big") and latch(m)[5] == 0x80


def test_wraps_at_2106():
    m = Machine()
    m.mem.set_clock(0xFFFF_FFFF, frac=0xFF)
    m.cpu.cycles += E_HZ // 256 + 1
    assert latch(m)[:5] == bytes(5)


def test_read_only_writes():
    m = Machine()
    try:
        m.mem.write_byte(0xFF5D, 0)
        raise AssertionError("no BusError")
    except BusError:
        pass
    Machine(strict=False).mem.write_byte(0xFF5D, 0)         # the board ignores it


def test_rtc_absent():
    for strict in (True, False):
        m = Machine(rtc=False, strict=strict)
        assert m.mem.rtc is None
        m.mem.write_byte(0xFF58, 0)                         # a free address: ignored
        assert regs(m) == b"\xFF" * 8                       # STATUS $FF: bits 6-0 set


if __name__ == "__main__":
    failed = 0
    tests = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_")]
    for name, fn in tests:
        try:
            fn()
            print(f"ok    {name}")
        except Exception as e:
            failed += 1
            print(f"FAIL  {name}: {type(e).__name__}: {e}")
    print(f"{len(tests) - failed}/{len(tests)} passed")
    sys.exit(1 if failed else 0)
