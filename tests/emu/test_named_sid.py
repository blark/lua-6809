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
from emu.board import SID_FRAME  # noqa: E402
from emu.drivewire import (  # noqa: E402
    E_SECT, OP_NAMEOBJ_CREATE, OP_NAMEOBJ_MOUNT, OP_READEX, OP_WRITE, DWProtocolError, DWServer,
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
def test_named_create_write_mount_read():
    with tempfile.TemporaryDirectory() as d:
        srv = DWServer(named_dir=d)
        srv.attach(0, sectors=4)
        assert named(srv, OP_NAMEOBJ_MOUNT, b"game.hi") == 0          # not there yet
        drive = named(srv, OP_NAMEOBJ_CREATE, b"game.hi")
        assert drive == 1 and (Path(d) / "game.hi").read_bytes() == b""
        assert read(srv, drive, 0)[0] == E_SECT                          # empty object
        data = bytes(range(256))
        assert write(srv, drive, 0, data) == 0
        assert (Path(d) / "game.hi").read_bytes() == data                # written through
        assert named(srv, OP_NAMEOBJ_CREATE, b"game.hi") == 0            # exists: create fails
        srv2 = DWServer(named_dir=d)                                     # a new server (reboot)
        drive = named(srv2, OP_NAMEOBJ_MOUNT, b"game.hi")
        assert drive == 1 and read(srv2, drive, 0) == (0, data)
        assert srv2.named_ops == [(OP_NAMEOBJ_MOUNT, b"game.hi", 1)]


def test_named_lease_and_drive_numbers():
    with tempfile.TemporaryDirectory() as d:
        srv = DWServer(named_dir=d)
        srv.attach(0, sectors=1)
        srv.attach(1, sectors=1)
        (Path(d) / "a").write_bytes(bytes(256))
        (Path(d) / "b").write_bytes(bytes([7]) * 256)
        assert named(srv, OP_NAMEOBJ_MOUNT, b"a") == 2                   # lowest free drive
        assert named(srv, OP_NAMEOBJ_MOUNT, b"b") == 2                   # the lease moved to b
        assert read(srv, 2, 0) == (0, bytes([7]) * 256)
        assert named(srv, OP_NAMEOBJ_MOUNT, b"none") == 0
        assert 2 not in srv.drives                                       # released, not remounted
        assert sorted(srv.drives) == [0, 1]


def test_named_plain_names_only():
    with tempfile.TemporaryDirectory() as d:
        (Path(d) / "sub").mkdir()
        (Path(d) / "sub" / "x").write_bytes(bytes(256))
        srv = DWServer(named_dir=d)
        for bad in (b"", b".", b"..", b"sub/x", b"..\\x", b"a\x00b", b"sub"):
            assert named(srv, OP_NAMEOBJ_MOUNT, bad) == 0, bad
        assert named(srv, OP_NAMEOBJ_CREATE, b"../escape") == 0
        assert not (Path(d).parent / "escape").exists()


def test_named_directory_missing():
    """No card: the saves directory is not there, so both calls fail."""
    with tempfile.TemporaryDirectory() as d:
        srv = DWServer(named_dir=Path(d) / "nocard")
        assert named(srv, OP_NAMEOBJ_MOUNT, b"x.hi") == 0
        assert named(srv, OP_NAMEOBJ_CREATE, b"x.hi") == 0


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
    assert io(m, 0x3B) == 0 and io(m, 0x20) == 0xFF


def test_sid_absent():
    for strict in (True, False):
        m = Machine(sid=False, strict=strict)
        assert m.mem.sid is None
        for reg in (0x20, 0x3B, 0xB0, 0xB3, 0xB4, 0xB7):
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
