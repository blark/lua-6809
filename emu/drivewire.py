"""
An in-process DriveWire 4 server for the Anachron8 emulator's DW port.

Adapted from the server in anachron8-sw lib/test/test_dw.py (the lib/dw.s
tests): fed one byte at a time by the port, its replies queue in `reply`.

Disk operations: READEX/REREADEX, WRITE/REWRITE, GETSTAT/SETSTAT (no reply).
Others: DWINIT, TIME, INIT, TERM, NOP, the three RESETs, and the virtual
serial subset NitrOS-9's dwio polls with (SERREAD answers "nothing",
FASTWRITE bytes are kept per channel in `serial_out`, SERINIT/SERTERM,
SERGETSTAT/SERSETSTAT). An opcode not listed raises DWProtocolError, so an
unexpected request shows up at once instead of desynchronising the port;
ignore_unknown=True drops it instead and takes the next byte as an opcode,
as the ESP32's server does (anachronsole firmware/main/dw_engine.c).

Named objects (named_dir = the directory standing for the card's saves/),
as anachronsole serves them (branch dw-named, docs/DRIVEWIRE.md section 4a;
the DriveWire 4 specification, "Named Objects"):

  $01 NAMEOBJ_MOUNT  len name -> drive, 0 = failed (the file must exist)
  $02 NAMEOBJ_CREATE len name -> drive, 0 = failed (it must not; makes the
                                 directory and an empty file)

  Names: 1-64 of A-Z a-z 0-9 . _ -, not starting or ending with '.';
  matched case-insensitively. Four slots, drives 255, 254, 253, 252 (the
  first free counting down); the same name again gets its drive back
  (MOUNT) or fails (CREATE); all four in use: 0. release_named() frees them
  (the ESP32 does on a 6809 reset over SPI); a freed drive answers E$Unit.
  card=False: no card, every call answers 0. The files are raw 256-byte
  sectors: a read past the end is zeros with status 0, a write past the end
  grows the file (zeros in the gap, at most 64 sectors of it: E$Write), LSN
  65536 and up E$Sect.

Drives hold 256-byte sectors, LSN 24 bits. A drive is backed by an image
file (read at attach time) or created empty; with writeback=True every WRITE
is also written to the file, otherwise the file is never touched. A read
past the end answers E$Sect with zeros; a write past the end grows the
drive.
"""

import re
import time
from collections import deque
from pathlib import Path

OP_NAMEOBJ_MOUNT, OP_NAMEOBJ_CREATE = 0x01, 0x02
OP_NOP, OP_TIME, OP_INIT, OP_TERM, OP_DWINIT = 0x00, 0x23, 0x49, 0x54, 0x5A
OP_READEX, OP_REREADEX, OP_WRITE, OP_REWRITE = 0xD2, 0xF2, 0x57, 0x77
OP_GETSTAT, OP_SETSTAT = 0x47, 0x53
OP_RESET1, OP_RESET2, OP_RESET3 = 0xFE, 0xFF, 0xF8
OP_SERREAD, OP_SERREADM, OP_SERWRITE = 0x43, 0x63, 0xC3
OP_SERINIT, OP_SERTERM, OP_SERGETSTAT, OP_SERSETSTAT = 0x45, 0xC5, 0x44, 0xC4
OP_FASTWRITE = 0x80           # $80-$8E: one byte to channel op & $0F
SS_COMST = 0x28               # SERSETSTAT code followed by 26 bytes
E_UNIT, E_SECT, E_CRC, E_WRITE = 240, 241, 243, 245
NAMED_SLOTS, NAMED_DRIVE0 = 4, 255   # slot n is drive 255 - n
NAMED_MAX_GAP = 64                   # sectors of zeros one write may add
NAMED_MAX_SECTORS = 65536
NAME_RE = re.compile(r"[A-Za-z0-9._-]{1,64}")

SECTOR = 256


class DWProtocolError(Exception):
    pass


class DWDrive:
    def __init__(self, path=None, sectors=0, writeback=False):
        self.path = Path(path) if path else None
        self.writeback = writeback and self.path is not None
        data = bytearray(self.path.read_bytes()) if self.path else bytearray()
        if len(data) % SECTOR:
            data.extend(bytes(SECTOR - len(data) % SECTOR))
        if len(data) < sectors * SECTOR:
            data.extend(bytes(sectors * SECTOR - len(data)))
        self.data = data

    @property
    def sectors(self):
        return len(self.data) // SECTOR

    def read(self, lsn):
        if lsn >= self.sectors:
            return E_SECT, bytes(SECTOR)
        return 0, bytes(self.data[lsn * SECTOR:(lsn + 1) * SECTOR])

    def write(self, lsn, data):
        end = (lsn + 1) * SECTOR
        if end > len(self.data):
            self.data.extend(bytes(end - len(self.data)))
        self.data[lsn * SECTOR:end] = data
        if self.writeback:
            with open(self.path, "r+b" if self.path.exists() else "wb") as f:
                f.seek(lsn * SECTOR)
                f.write(data)
        return 0


class NamedObject:
    """A file served as a named object: raw sectors, zeros past the end, growing."""

    def __init__(self, path):
        self.path = path

    def read(self, lsn):
        with open(self.path, "rb") as f:
            f.seek(lsn * SECTOR)
            return 0, f.read(SECTOR).ljust(SECTOR, b"\0")

    def write(self, lsn, data):
        if lsn >= NAMED_MAX_SECTORS:
            return E_SECT
        size = self.path.stat().st_size
        if lsn * SECTOR > size + NAMED_MAX_GAP * SECTOR:
            return E_WRITE
        with open(self.path, "r+b") as f:
            if lsn * SECTOR > size:
                f.seek(size)
                f.write(bytes(lsn * SECTOR - size))
            f.seek(lsn * SECTOR)
            f.write(data)
        return 0


class DWServer:
    """A DriveWire 4 server subset, fed one byte at a time."""

    def __init__(self, drives=None, clock=None, named_dir=None, card=True, ignore_unknown=False):
        self.drives = dict(drives or {})     # drive number -> DWDrive
        self.clock = clock                   # callable giving the 6 TIME bytes; None: local time
        self.named_dir = Path(named_dir) if named_dir is not None else None
        self.card = card
        self.named = [None] * NAMED_SLOTS    # slot -> (name, NamedObject), or None
        self.named_ops = []                  # (opcode, name bytes, reply) of each named call
        self.ignore_unknown = ignore_unknown
        self.unknown_ops = []                # opcodes dropped with ignore_unknown
        self.reply = deque()                 # bytes for the 6809, in order
        self.ops = []                        # opcodes seen
        self.dwinit_version = None
        self.serial_out = {}                 # channel -> bytearray (FASTWRITE, SERWRITE)
        self._gen = self._serve()
        next(self._gen)

    def attach(self, number, path=None, sectors=0, writeback=False):
        self.drives[number] = DWDrive(path, sectors, writeback)
        return self.drives[number]

    def feed(self, byte):
        self._gen.send(byte)

    def time_bytes(self):
        if self.clock:
            return bytes(self.clock())
        t = time.localtime()
        return bytes([t.tm_year - 1900, t.tm_mon, t.tm_mday, t.tm_hour, t.tm_min, t.tm_sec])

    def _serve(self):
        while True:
            op = yield
            self.ops.append(op)
            if op in (OP_READEX, OP_REREADEX):
                drive, lsn = yield from self._drive_lsn()
                status, data = self._read(drive, lsn)
                self.reply.extend(data)
                hi = yield
                lo = yield
                if status == 0 and (hi << 8 | lo) != sum(data) & 0xFFFF:
                    status = E_CRC
                self.reply.append(status)
            elif op in (OP_WRITE, OP_REWRITE):
                drive, lsn = yield from self._drive_lsn()
                data = bytearray()
                for _ in range(SECTOR):
                    data.append((yield))
                hi = yield
                lo = yield
                if (hi << 8 | lo) != sum(data) & 0xFFFF:
                    status = E_CRC
                elif self._drive(drive) is None:
                    status = E_UNIT
                else:
                    status = self._drive(drive).write(lsn, data)
                self.reply.append(status)
            elif op in (OP_GETSTAT, OP_SETSTAT):
                yield                        # drive
                yield                        # code
            elif op == OP_DWINIT:
                self.dwinit_version = yield
                self.reply.append(0x00)
            elif op == OP_TIME:
                self.reply.extend(self.time_bytes())
            elif op in (OP_NOP, OP_INIT, OP_TERM, OP_RESET1, OP_RESET2, OP_RESET3):
                pass
            elif op == OP_SERREAD:
                self.reply.extend((0, 0))    # nothing waiting on any channel
            elif OP_FASTWRITE <= op <= OP_FASTWRITE + 0x0E:
                self.serial_out.setdefault(op & 0x0F, bytearray()).append((yield))
            elif op == OP_SERWRITE:
                chan = yield
                self.serial_out.setdefault(chan, bytearray()).append((yield))
            elif op in (OP_SERINIT, OP_SERTERM):
                yield                        # channel
            elif op == OP_SERGETSTAT:
                yield                        # channel
                yield                        # code
            elif op == OP_SERSETSTAT:
                yield                        # channel
                code = yield
                if code == SS_COMST:
                    for _ in range(26):
                        yield
            elif op in (OP_NAMEOBJ_MOUNT, OP_NAMEOBJ_CREATE) and self.named_dir is not None:
                name = bytearray()
                for _ in range((yield)):
                    name.append((yield))
                drive = self._named(op, bytes(name))
                self.named_ops.append((op, bytes(name), drive))
                self.reply.append(drive)
            elif self.ignore_unknown:
                self.unknown_ops.append(op)
            else:
                raise DWProtocolError(f"DriveWire opcode ${op:02X} not implemented")

    def _drive_lsn(self):
        drive = yield
        lsn = 0
        for _ in range(3):
            lsn = lsn << 8 | (yield)
        return drive, lsn

    def _named(self, op, name):
        """MOUNT or CREATE `name`: its drive, or 0 (dw_engine.c nameobj_finish)."""
        text = name.decode("latin-1")
        if not NAME_RE.fullmatch(text) or text[0] == "." or text[-1] == ".":
            return 0
        free = None
        for slot, entry in enumerate(self.named):
            if entry is None:
                free = slot if free is None else free
            elif entry[0].upper() == text.upper():
                return 0 if op == OP_NAMEOBJ_CREATE else NAMED_DRIVE0 - slot
        if free is None or not self.card:
            return 0
        found = self.named_dir.is_dir() and next(
            (f for f in self.named_dir.iterdir() if f.name.upper() == text.upper()), None)
        if op == OP_NAMEOBJ_MOUNT:
            if not found or not found.is_file():
                return 0
            path = found
        else:
            if found:
                return 0
            self.named_dir.mkdir(parents=True, exist_ok=True)
            path = self.named_dir / text
            path.write_bytes(b"")
        self.named[free] = (path.name, NamedObject(path))
        return NAMED_DRIVE0 - free

    def release_named(self):
        """Free every slot (the ESP32 does on a 6809 reset over SPI)."""
        self.named = [None] * NAMED_SLOTS

    def _drive(self, drive):
        """The DWDrive or NamedObject serving a drive number, or None."""
        slot = NAMED_DRIVE0 - drive
        if 0 <= slot < NAMED_SLOTS and self.named_dir is not None:
            return self.named[slot][1] if self.named[slot] else None
        return self.drives.get(drive)

    def _read(self, drive, lsn):
        d = self._drive(drive)
        if d is None:
            return E_UNIT, bytes(SECTOR)
        return d.read(lsn)
