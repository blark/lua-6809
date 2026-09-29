#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["MC6809"]
# ///
"""
Tests for the emulator pieces NitrOS-9 needs: A8CPU's interrupts and the
instructions the MC6809 package lacks, the tick timer and keyboard IRQs of
Anachron8Board, the DriveWire server behind the DW port, and strict=False.

    uv run tests/emu/test_os9_devices.py

The 6809 programs are tests/emu/asm/*.s, assembled into the committed .s19
files by tests/emu/asm/build.sh; the addresses below are their labels.
"""

import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent.parent))

from emu.a8run import Machine, main as a8run_main  # noqa: E402
from emu.anachron8 import BusError  # noqa: E402
from emu.board import SYNC_PER_E, TICK_PERIOD  # noqa: E402
from emu.drivewire import OP_READEX, DWServer  # noqa: E402

ASM = HERE / "asm"
TICK60 = TICK_PERIOD[0] / SYNC_PER_E        # CPU cycles per tick
TICK50 = TICK_PERIOD[1] / SYNC_PER_E


def machine(prog, **kw):
    m = Machine(**kw)
    m.load_s19(ASM / f"{prog}.s19")
    m.cpu.program_counter.set(0x1000)
    return m


def peek(m, addr, n=1):
    data = bytes(m.mem.read_byte(addr + i) for i in range(n))
    return data[0] if n == 1 else data


def run_cycles(m, cycles):
    """Run until the CPU cycle count reaches `cycles`."""
    reason, _, err = m.run(10_000_000, until=lambda c: c.cycles >= cycles)
    assert err is None and reason == "until", (reason, err)


# --- tick timer ------------------------------------------------------------------------
def test_tick_irq_60hz():
    m = machine("tick")
    run_cycles(m, int(3.5 * TICK60))
    assert peek(m, 0x0102) == 3 and peek(m, 0x0103) == 3      # count, last TICK_COUNT
    assert m.cpu.interrupts["irq"] == 3
    assert m.mem.tick_status == 0 and not m.mem.irq_line()   # acknowledged
    assert int.from_bytes(peek(m, 0x0104, 2), "big") > 1000    # the main loop runs


def test_tick_irq_50hz():
    m = machine("tick")
    m.mem.write_byte(0x0100, 0x03)                            # ctrl: enable, 50 Hz
    run_cycles(m, int(2.5 * TICK60))                           # the first tick is at 60 Hz spacing
    assert peek(m, 0x0102) == 2
    run_cycles(m, int(2 * TICK60 + 3.5 * TICK50))              # later ticks 50 Hz apart
    assert peek(m, 0x0102) == 5


def test_tick_disabled_sets_pending_only():
    m = machine("tick")
    m.mem.write_byte(0x0100, 0x00)                            # ctrl: IRQ disabled
    run_cycles(m, int(2.5 * TICK60))
    assert m.cpu.interrupts["irq"] == 0 and peek(m, 0x0102) == 0
    assert m.mem.read_byte(0xFF4B) == 1 and m.mem.read_byte(0xFF4C) == 2
    assert not m.mem.irq_line()
    m.mem.write_byte(0xFF4A, 0x01)                            # enabling takes the pending tick
    assert m.mem.irq_line()
    m.run(10)
    assert m.cpu.interrupts["irq"] == 1 and m.mem.read_byte(0xFF4B) == 0


def test_tick_masked_by_i():
    m = machine("tick")
    m.mem.write_byte(0x0101, 0x00)                            # enable: I stays set
    run_cycles(m, int(1.5 * TICK60))
    assert m.mem.irq_line() and m.cpu.interrupts["irq"] == 0


# --- keyboard ------------------------------------------------------------------------------
def test_keyboard_irq():
    m = machine("kbd")
    assert m.mem.push_keys("abc") == 3
    m.run(2000)
    assert peek(m, 0x0200, 3) == b"abc" and peek(m, 0x0101) == 1
    assert not m.mem.irq_line() and m.mem.read_byte(0xFF46) == 0
    m.mem.push_keys("de")
    m.run(2000)
    assert peek(m, 0x0200, 5) == b"abcde" and peek(m, 0x0101) == 2
    assert m.cpu.interrupts["irq"] == 2


def test_keyboard_fifo_full_and_stale():
    m = machine("kbd")
    assert m.mem.push_keys(bytes(range(1, 21))) == 16          # 16 entries, the rest dropped
    assert m.mem.keys_queued == 16
    for k in range(1, 17):
        assert m.mem.read_byte(0xFF47) == k
    assert m.mem.read_byte(0xFF46) == 0
    assert m.mem.read_byte(0xFF47) == 1                        # empty: the stale slot, not popped


def test_keyboard_masked():
    m = machine("kbd")
    m.mem.write_byte(0x0100, 0x00)
    m.mem.push_keys("x")
    m.run(2000)
    assert m.cpu.interrupts["irq"] == 0 and m.mem.irq_line() and peek(m, 0x0101) == 0


# --- DriveWire ------------------------------------------------------------------------------
def test_dw_readex_from_6809():
    sector = bytes((i * 7 + 3) & 0xFF for i in range(256))
    with tempfile.TemporaryDirectory() as d:
        img = Path(d) / "disk.dsk"
        img.write_bytes(bytes(256) + sector + bytes(256))
        m = machine("dw", drives={0: img})
        reason, _, err = m.run(200_000, until_pc=0x103D)
        assert err is None and reason == "pc", (reason, err)
        assert peek(m, 0x2000, 256) == sector
        assert peek(m, 0x0100) == 0                             # status OK
        assert int.from_bytes(peek(m, 0x0101, 2), "big") == sum(sector) & 0xFFFF
        assert m.dw.ops == [OP_READEX]


def test_dw_readex_missing_drive():
    m = machine("dw")                                          # no drive 0
    reason, _, err = m.run(200_000, until_pc=0x103D)
    assert err is None and reason == "pc"
    assert peek(m, 0x0100) == 240 and peek(m, 0x2000, 256) == bytes(256)   # E$Unit, zeros


def test_dw_drive_writeback():
    with tempfile.TemporaryDirectory() as d:
        img = Path(d) / "disk.dsk"
        img.write_bytes(bytes(512))
        srv = DWServer()
        ro = srv.attach(0, img)
        rw = srv.attach(1, img, writeback=True)
        data = bytes([0x5A] * 256)
        for drive in (0, 1):
            srv.feed(0x57)
            for b in (drive, 0, 0, 3) + tuple(data) + divmod(sum(data), 256):
                srv.feed(b)
            assert srv.reply.popleft() == 0
        assert ro.read(3) == (0, data) and rw.sectors == 4
        assert img.read_bytes()[768:1024] == data and len(img.read_bytes()) == 1024


def test_dw_time_and_serial_poll():
    srv = DWServer(clock=lambda: (126, 9, 29, 12, 0, 1))
    for b in (0x23, 0x43, 0x81, ord("x"), 0x5A, 0x07):
        srv.feed(b)
    assert list(srv.reply) == [126, 9, 29, 12, 0, 1, 0, 0, 0]
    assert srv.serial_out == {1: bytearray(b"x")} and srv.dwinit_version == 0x07


# --- CPU ---------------------------------------------------------------------------------------
def test_cpu_swi_family_cwai_sync_pshu():
    m = machine("cpu")
    reason, _, err = m.run(50_000, until=lambda c: m.cpu.waiting == "sync")
    assert err is None, err
    assert peek(m, 0x0100) == 0x42 and peek(m, 0x0101) == 0x99    # SWI2 code byte, stacked A changed
    assert peek(m, 0x0102) & 0x50 == 0                            # SWI2 leaves I and F clear
    assert peek(m, 0x0103) & 0x50 == 0x50                         # SWI sets both
    assert peek(m, 0x0104) == 1                                   # SWI3 ran
    assert peek(m, 0x0109, 2) == b"\x0F\x00"                      # PSHU S pushed S
    assert peek(m, 0x010B, 2) == b"\x0E\x80"                      # PULU S loaded S
    assert peek(m, 0x0105) == 1 and peek(m, 0x0106) == 1          # CWAI woke by the tick IRQ
    assert peek(m, 0x010D) & 0x80                                 # entire state stacked (E)
    assert m.cpu.cycles >= TICK60                                 # CWAI skipped to the tick
    m.mem.push_key(ord("k"))                                      # SYNC, I set: a key ends it
    reason, _, err = m.run(1000, until_pc=0x104B)
    assert err is None and reason == "pc"
    assert peek(m, 0x0107) == 1 and peek(m, 0x0108) == ord("k") and peek(m, 0x010E) == 1
    assert m.cpu.interrupts["irq"] == 1                           # the masked key IRQ not taken


def test_trace_ring_buffer():
    m = machine("tick")
    m.cpu.enable_trace(8)
    m.run(100)
    assert len(m.cpu.trace) == 8 and m.cpu.format_trace().count("\n") == 7


# --- strict --------------------------------------------------------------------------------------
def test_strict_default_and_off():
    m = Machine()
    for probe in (lambda: m.mem.write_byte(0xE000, 1),      # the ROM page
                  lambda: m.mem.write_byte(0xD000, 1),      # a Classic screen's upper half
                  lambda: m.mem.read_byte(0xFF10),          # a free I/O address
                  lambda: m.mem.write_byte(0xFF47, 1)):     # a read-only register
        try:
            probe()
        except BusError:
            continue
        raise AssertionError("strict model accepted an access")
    m = Machine(strict=False)
    rom = m.mem.page_bytes(0xFE, 0, 1)
    m.mem.write_byte(0xE000, rom[0] ^ 0xFF)
    assert m.mem.page_bytes(0xFE, 0, 1) == rom                    # ignored, no error
    m.mem.write_byte(0xD000, 0x5A)
    assert m.mem.read_byte(0xD000) == 0x5A
    assert m.mem.read_byte(0xFF10) == 0xFF
    m.mem.write_byte(0xFF10, 1)
    m.mem.write_byte(0xFF47, 1)


def test_os9_memory_probe_pages():
    """$01 at offset $0200 of pages $10, $20, $40, $80 through slot 4: no error in either mode."""
    for strict in (True, False):
        m = Machine(strict=strict)
        for page in (0x10, 0x20, 0x40, 0x80):
            m.mem.write_byte(0xFF84, page)                    # MAP0 slot 4
            m.mem.write_byte(0x8200, 0x01)
            assert m.mem.read_byte(0x8200) == 0x01 and m.mem.sdram[page << 13 | 0x200] == 0x01


def test_runner_cli():
    with tempfile.TemporaryDirectory() as d:
        img = Path(d) / "disk.dsk"
        img.write_bytes(bytes(256) + bytes(range(256)))
        rc = a8run_main(["--s19", str(ASM / "dw.s19"), "--pc", "1000", "--drive", f"0={img}",
                         "--until-pc", "103D", "-n", "100000", "--no-screen"])
        assert rc == 0


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
