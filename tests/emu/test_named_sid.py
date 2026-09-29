#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["MC6809"]
# ///
"""
Tests for DriveWire 4 named objects in emu/drivewire.py and the SID1/SIDCPU
model of emu/board.py (the 6502 player's registers as the 6809 sees them).

    uv run tests/emu/test_named_sid.py
"""

import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent.parent))

from emu.a8run import Machine  # noqa: E402
from emu.anachron8 import BusError  # noqa: E402
from emu.board import SID_FRAME  # noqa: E402
from emu.drivewire import (  # noqa: E402
    E_SECT, E_UNIT, E_WRITE, OP_NAMEOBJ_CREATE, OP_NAMEOBJ_MOUNT, OP_READEX, OP_WRITE,
    DWProtocolError, DWServer,
)


def named(srv, op, name):
    """One named-object call; the server's one-byte reply."""
    for b in (op, len(name), *name):
        srv.feed(b)
    assert len(srv.reply) == 1
    return srv.reply.popleft()


def write(srv, drive, lsn, data):
    for b in (OP_WRITE, drive, 0, lsn >> 8, lsn & 0xFF, *data, *divmod(sum(data) & 0xFFFF, 256)):
        srv.feed(b)
    return srv.reply.popleft()


def read(srv, drive, lsn):
    for b in (OP_READEX, drive, 0, lsn >> 8, lsn & 0xFF):
        srv.feed(b)
    data = bytes(srv.reply.popleft() for _ in range(256))
    for b in divmod(sum(data) & 0xFFFF, 256):
        srv.feed(b)
    return srv.reply.popleft(), data


# --- named objects -------------------------------------------------------------------------------
def test_named_miss_create_write_reset_mount_read():
    with tempfile.TemporaryDirectory() as d:
        saves = Path(d) / "saves"                                       # made by the first CREATE
        srv = DWServer(named_dir=saves)
        srv.attach(0, sectors=4)
        assert named(srv, OP_NAMEOBJ_MOUNT, b"game.hi") == 0          # not there yet
        drive = named(srv, OP_NAMEOBJ_CREATE, b"game.hi")
        assert drive == 255 and (saves / "game.hi").read_bytes() == b""
        assert read(srv, drive, 0) == (0, bytes(256))                    # past the end: zeros, OK
        data = bytes(range(256))
        assert write(srv, drive, 0, data) == 0
        assert (saves / "game.hi").read_bytes() == data                  # written through
        assert named(srv, OP_NAMEOBJ_MOUNT, b"GAME.HI") == 255           # the same object, same drive
        assert named(srv, OP_NAMEOBJ_CREATE, b"game.hi") == 0            # mounted: it exists
        srv.release_named()                                              # a 6809 reset over SPI
        assert read(srv, 255, 0)[0] == E_UNIT
        assert named(srv, OP_NAMEOBJ_CREATE, b"Game.hi") == 0            # exists on the card
        assert named(srv, OP_NAMEOBJ_MOUNT, b"game.hi") == 255
        assert read(srv, 255, 0) == (0, data)
        assert srv.named_ops[-1] == (OP_NAMEOBJ_MOUNT, b"game.hi", 255)


def test_named_slots():
    with tempfile.TemporaryDirectory() as d:
        srv = DWServer(named_dir=d)
        srv.attach(0, sectors=1)
        drives = [named(srv, OP_NAMEOBJ_CREATE, f"f{i}".encode()) for i in range(5)]
        assert drives == [255, 254, 253, 252, 0]                         # four slots, counting down
        assert read(srv, 0, 0)[0] == 0 and read(srv, 251, 0)[0] == E_UNIT
        assert named(srv, OP_NAMEOBJ_MOUNT, b"f1") == 254
        srv.release_named()
        assert named(srv, OP_NAMEOBJ_MOUNT, b"f4") == 0                  # never created
        assert named(srv, OP_NAMEOBJ_MOUNT, b"f3") == 255                # the first free slot


def test_named_grow_and_limits():
    with tempfile.TemporaryDirectory() as d:
        srv = DWServer(named_dir=d)
        drive = named(srv, OP_NAMEOBJ_CREATE, b"big")
        (Path(d) / "big").write_bytes(b"abc")                            # a partial sector
        assert read(srv, drive, 0) == (0, b"abc".ljust(256, b"\0"))
        assert write(srv, drive, 2, bytes([9] * 256)) == 0               # the gap filled with zeros
        assert (Path(d) / "big").read_bytes() == b"abc".ljust(512, b"\0") + bytes([9] * 256)
        assert write(srv, drive, 3 + 65, bytes(256)) == E_WRITE          # a gap over 64 sectors
        assert write(srv, drive, 3 + 64, bytes(256)) == 0
        assert write(srv, drive, 65536, bytes(256)) == E_SECT


def test_named_name_rules():
    with tempfile.TemporaryDirectory() as d:
        (Path(d) / "sub").mkdir()
        srv = DWServer(named_dir=d)
        for bad in (b"", b".x", b"x.", b"..", b"sub/x", b"a b", b"a\\b", b"a\x00", b"x" * 65, b"\xe9"):
            assert named(srv, OP_NAMEOBJ_CREATE, bad) == 0, bad
        assert named(srv, OP_NAMEOBJ_MOUNT, b"sub") == 0                 # a directory
        assert named(srv, OP_NAMEOBJ_CREATE, b"A-z_0.9") == 255
        assert named(srv, OP_NAMEOBJ_CREATE, b"x" * 64) == 254
        assert sorted(f.name for f in Path(d).iterdir()) == ["A-z_0.9", "sub", "x" * 64]


def test_named_no_card():
    with tempfile.TemporaryDirectory() as d:
        (Path(d) / "x.hi").write_bytes(bytes(256))
        srv = DWServer(named_dir=d, card=False)
        assert named(srv, OP_NAMEOBJ_MOUNT, b"x.hi") == 0
        assert named(srv, OP_NAMEOBJ_CREATE, b"y.hi") == 0


def test_named_unsupported():
    srv = DWServer()
    try:
        srv.feed(OP_NAMEOBJ_MOUNT)
        raise AssertionError("no DWProtocolError")
    except DWProtocolError:
        pass
    old = DWServer(ignore_unknown=True)                                  # the ESP32 before named objects
    for b in (OP_NAMEOBJ_MOUNT, 4, *b"t.hi"):
        old.feed(b)
    assert not old.reply and old.unknown_ops == [OP_NAMEOBJ_MOUNT, 4, *b"t.hi"]
    old.feed(0x5A)
    old.feed(1)
    assert list(old.reply) == [0]                                        # still in step: DWINIT


# --- SID1 and SIDCPU -------------------------------------------------------------------------------
def io(m, reg, value=None):
    """Read or write SIDCPU/SID1 register $FF00 + reg as the 6809 would."""
    if value is None:
        return m.mem.read_byte(0xFF00 + reg)
    m.mem.write_byte(0xFF00 + reg, value)


def test_sid_loader_and_params():
    m = Machine()
    sid = m.mem.sid
    assert io(m, 0xB3) == 0 and io(m, 0xB4) == 0 and io(m, 0xB6) == 0xFF
    io(m, 0xB0, 0x10)
    io(m, 0xB1, 0x00)
    for b in (0xA9, 0x0F, 0x60):
        io(m, 0xB2, b)                                  # LDATA steps LADDR
    assert sid.ram_bytes(0x1000, 3) == bytes([0xA9, 0x0F, 0x60])
    assert (io(m, 0xB0), io(m, 0xB1)) == (0x10, 0x03)
    io(m, 0xB0, 0xDF)
    io(m, 0xB1, 0x00)
    for b in (0x00, 0x10, 0x03, 0x10, 0x02, 0x00, 0x00, 0x00, 0x55):
        io(m, 0xB2, b)                                  # 8 parameters, the 9th dropped
    assert bytes(sid.params) == bytes([0x00, 0x10, 0x03, 0x10, 0x02, 0, 0, 0])
    assert io(m, 0xB5) == 0x02                          # SID_SONG is SONG
    io(m, 0xB0, 0x10)
    io(m, 0xB1, 0x01)
    assert io(m, 0xB2) == 0x0F and io(m, 0xB2) == 0x60  # reads step too
    io(m, 0xB0, 0x90)
    assert io(m, 0xB2) == 0xFF                          # outside the loader space


def test_sid_run_owner_reset():
    m = Machine()
    sid = m.mem.sid
    io(m, 0x38, 0x0F)                                   # the 6809 owns SID1: taken
    io(m, 0xB3, 0x03)                                   # RUN, the 6502 owns it
    assert sid.running and io(m, 0xB4) == 0x03          # no IRQ yet
    io(m, 0x38, 0x00)                                   # dropped
    m.cpu.cycles += SID_FRAME
    assert io(m, 0xB4) == 0x07                          # IRQ seen after a frame
    io(m, 0xB3, 0x00)                                   # stop: the SID reset
    assert io(m, 0xB4) == 0 and sid.resets == 1
    io(m, 0xB3, 0x00)                                   # already stopped: no reset
    assert sid.resets == 1 and [v for _, v in sid.log] == [3, 0, 0]
    assert sid.sid1_writes == [(0x18, 0x0F)]
    assert io(m, 0x3B) == 0 and io(m, 0x39) == 0xFF and io(m, 0x20) == 0xFF   # no paddles
    try:
        io(m, 0xB4, 0)
        raise AssertionError("no BusError")
    except BusError:
        pass
    Machine(strict=False).mem.write_byte(0xFFB4, 0)                     # the board ignores it


def test_sid2():
    m = Machine()
    sid = m.mem.sid
    io(m, 0x78, 0x0A)                                   # the 6809 owns SID2
    io(m, 0xB3, 0x07)                                   # RUN, OWNER, OWNER2
    assert io(m, 0xB3) == 0x07 and io(m, 0xB4) & 0x13 == 0x13
    io(m, 0x64, 0x11)                                   # dropped: the 6502's
    io(m, 0xB3, 0x03)                                   # OWNER2 = 0
    io(m, 0x61, 0x22)
    assert [(r, v) for _, r, v in sid.sid2_writes] == [(0x18, 0x0A), (0x01, 0x22)]
    assert sid.sid2[0x04] == 0 and io(m, 0xB4) & 0x10 == 0
    assert io(m, 0x7B) == 0 and io(m, 0x79) == 0xFF and io(m, 0x60) == 0xFF
    io(m, 0xB3, 0x00)                                   # the stop resets both chips
    assert sid.sid2 == bytes(32)
    io(m, 0x78, 0x0F)                                   # during the 35 us pulse: consumed
    assert sid.sid2[0x18] == 0 and sid.dropped == 1
    m.cpu.cycles += 219
    io(m, 0x78, 0x0F)
    assert sid.sid2[0x18] == 0x0F
    one = Machine(sids=1)                               # the bitstream before SID2
    io(one, 0x78, 0x0F)
    io(one, 0xB3, 0x07)
    assert io(one, 0xB3) == 0x03 and io(one, 0x7B) == 0xFF and one.mem.sid.sid2_writes == []


def test_sid_absent():
    for strict in (True, False):
        m = Machine(sid=False, strict=strict)
        assert m.mem.sid is None
        for reg in (0x20, 0x3B, 0x60, 0x7B, 0xB0, 0xB3, 0xB4, 0xB7):
            assert io(m, reg) == 0xFF
        io(m, 0xB3, 0x03)                               # ignored
        assert io(m, 0xB3) == 0xFF


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
