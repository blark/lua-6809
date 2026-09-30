"""
The Anachron8 memory model plus the devices an operating system needs.

Anachron8Board is Anachron8Memory (either map) with, at their v1 offsets
from the I/O base (anachron8 docs/IO-REGISTERS.md):

  $44 RNG_DATA     a random byte;  $45 RNG_STATUS  $00
  $46 KEY_STATUS   bit 0: a key is queued
  $47 KEY_DATA     the oldest key of the 16-entry FIFO, popped; empty: the stale slot
  $48 DW_STATUS    bit 1: an RX byte is waiting, bit 2: the TX FIFO is full
  $49 DW_DATA      read pops RX, write pushes TX (to the DWServer, if any)
  $4A TICK_CTRL    bit 0 IRQ enable, bit 1 rate (0 = 60 Hz, 1 = 50 Hz)
  $4B TICK_STATUS  bit 0 tick pending, write 1 to clear
  $4C TICK_COUNT   ticks since the CPU reset, 8 bits

The tick runs from the CPU's cycle count at the E clock (6.25 MHz, 16 sync
cycles of 100 MHz each): a tick every 1,666,667 sync cycles (60 Hz) or
2,000,000 (50 Hz), the rate taking effect at the next tick. The IRQ line
(irq_line()) is KEY_STATUS bit 0 OR (TICK_STATUS bit 0 AND TICK_CTRL bit 0);
A8CPU samples it before every instruction. FIRQ and NMI are not driven.

Map v2 also has SID1 and the 6502 player's registers (anachron8
docs/IO-REGISTERS.md "SID1", "SIDCPU"; docs/SID-PLAYER.md), modelled as far
as the 6809 sees them, with no 6502 and no SID sound (sid=True, the default):

  $FF20-$FF38 SID1 writes: logged in sid.sid1_writes while the 6809 owns the
              chip, dropped while the 6502 does; reads $FF
  $FF39-$FF3C POTX, POTY (no paddles: $FF), OSC3, ENV3 (0: no sound here)
  $FFB0/$FFB1 LADDR_HI/LO (R/W)
  $FFB2       LDATA: stores/reads [LADDR], then LADDR + 1 (the loader space:
              the 6502's 32 KB at $0000-$7FFF, the parameter block at
              $DF00-$DF07; elsewhere writes dropped, reads $FF)
  $FFB3       SID_CTRL: bit 1 OWNER, bit 0 RUN; RUN 1 -> 0 resets the SID
              (sid.resets); bits 7-2 read 0
  $FFB4       SID_STATUS: RUN, OWNER, and bit 2 IRQ seen once RUN has been
              set for one 50 Hz frame (20 ms: the driver's first VBI IRQ)
  $FFB5       SID_SONG: the parameter block's SONG byte
  $FFB6/$FFB7 read $FF

SID2 ($FF60-$FF7F, sids=2, the default; anachron8 master 282206d) is the
same at offset $40: its writes are logged in sid.sid2_writes as (cycle,
register, value) and kept in sid.sid2 (32 registers) while SID_CTRL bit 2
(OWNER2) is 0, dropped while it is 1; $FF79/$FF7A read $FF, $FF7B/$FF7C 0.
SID_CTRL keeps bit 2 and SID_STATUS shows it as bit 4; a stop of a running
player (RUN 1 -> 0) resets both chips (sid.sid2 back to zeros), and for
the reset pulse's 35 us after it SID1 and SID2 writes are consumed
(sid.dropped counts them), as on the board. sids=1 is
the bitstream before it: $FF60-$FF7F read $FF and ignore writes, SID_CTRL
bit 2 reads 0.

sid.log records every SID_CTRL write as (cycle, value). sid=False is a
bitstream without them: all of $FF20-$FF3F and $FFB0-$FFB7 read $FF and
ignore writes, strict or not.

Map v2 also has the RTC (rtc=True, the default; anachron8 master 6a0172f,
rtl/rtc.py, docs/IO-REGISTERS.md "RTC"), an unsigned 32-bit count of UTC
seconds with a 1/256 s fraction that advances with the CPU's cycles (one
fraction step per 390625 sync cycles, 24414.0625 E cycles):

  $FF58       R: RTC_SEC3, the latched seconds' bits 31-24;
              W (any value): RTC_LATCH, copy the live clock into the latch
  $FF59-$FF5B RTC_SEC2..0, bits 23-0 (big-endian)
  $FF5C       RTC_FRAC, the latched fraction (1/256 s)
  $FF5D       RTC_STATUS: bit 7 VALID (set since configuration), bits 6-0 0
  $FF5E/$FF5F RTC_OFFSET: signed minutes east of UTC, big-endian

The eight bytes read the latch (zeros until the first latch), with no side
effect; writes to $FF59-$FF5F are a BusError when strict (the board ignores
them). Like the board it counts from 0 (1970, VALID 0) at configuration
(the board's creation); set_clock() is the ESP32's SPI 0x05 01 (seconds,
fraction: VALID 1) and 0x05 02 (offset). reset() leaves it running: only
configuration resets it. rtc=False is a bitstream without it (anachron8
before c01d4a6): $FF58-$FF5F read $FF and ignore writes, strict or not, as
a free I/O address on the board; RTC_STATUS then reads $FF, whose bits 6-0
tell it apart from the RTC's.

A write to one of the read-only registers is a BusError when strict (the
board ignores it). push_key() queues a key the way the ESP32 does (False
when the FIFO is full: the key is dropped). reset() is the CPU reset: the
tick and both DW FIFOs cleared, the keyboard FIFO and the MMU kept. It
does not free the DriveWire server's named-object slots: the ESP32 does that
only for a reset over SPI; call dw.release_named() for one.
"""

import random
from collections import deque

from emu.anachron8 import Anachron8Memory, BusError

E_HZ = 6_250_000
SYNC_PER_E = 16
TICK_PERIOD = (1_666_667, 2_000_000)     # sync cycles: 60 Hz, 50 Hz
RNG_DATA, RNG_STATUS, KEY_STATUS, KEY_DATA = 0x44, 0x45, 0x46, 0x47
DW_STATUS, DW_DATA, TICK_CTRL, TICK_STATUS, TICK_COUNT = 0x48, 0x49, 0x4A, 0x4B, 0x4C
DW_RX, DW_TX_FULL = 0x02, 0x04
KBD_SIZE, DW_FIFO = 16, 512
SID1, SID1_MIRROR, SID1_END = 0x20, 0x39, 0x40
SID2, SID2_MIRROR, SID2_END = 0x60, 0x79, 0x80
LADDR_HI, LADDR_LO, LDATA, SID_CTRL, SID_STATUS, SID_SONG, SIDCPU_END = 0xB0, 0xB1, 0xB2, 0xB3, 0xB4, 0xB5, 0xB8
SID_RUN, SID_OWNER, SID_OWNER2, SID_IRQ_SEEN = 0x01, 0x02, 0x04, 0x04
STATUS_OWNER2 = 0x10
SID_RAM, SID_PARAMS = 0x8000, 0xDF00
SID_FRAME = E_HZ // 50                   # CPU cycles in the player's 50 Hz frame
SID_RESET_PULSE = E_HZ * 35 // 1_000_000  # CPU cycles of the SID reset pulse (35 us)
RTC, RTC_END = 0x58, 0x60                 # $FF58-$FF5F; a write to $FF58 latches
RTC_VALID = 0x80
RTC_PRESCALE = 100_000_000 // 256         # sync cycles per 1/256 s


class SidPlayer:
    """What the 6809 sees of SID1 and SIDCPU (no 6502 runs)."""

    def __init__(self, sids=2):
        self.sids = sids
        self.sid2 = bytearray(32)            # SID2's registers as written (sids=2)
        self.sid2_writes = []                # (cycle, register, value) taken while the 6809 owns SID2
        self.reset_at = None                 # the cycle of the last SID reset
        self.dropped = 0                     # SID writes consumed by a reset pulse
        self.ram = bytearray(SID_RAM)        # the 6502's RAM
        self.params = bytearray(8)           # $DF00-$DF07
        self.laddr = 0
        self.ctrl = 0
        self.run_at = None                   # the cycle RUN was set
        self.resets = 0                      # SID resets by RUN 1 -> 0
        self.log = []                        # (cycle, SID_CTRL value written)
        self.sid1_writes = []                # (register, value) taken while the 6809 owns SID1

    def load_read(self, addr):
        if addr < SID_RAM:
            return self.ram[addr]
        if SID_PARAMS <= addr < SID_PARAMS + 8:
            return self.params[addr - SID_PARAMS]
        return 0xFF

    def load_write(self, addr, value):
        if addr < SID_RAM:
            self.ram[addr] = value
        elif SID_PARAMS <= addr < SID_PARAMS + 8:
            self.params[addr - SID_PARAMS] = value

    def read(self, reg, cycles):
        if SID1 <= reg < SID1_END:
            return 0x00 if SID1_MIRROR + 2 <= reg < SID1_MIRROR + 4 else 0xFF
        if SID2 <= reg < SID2_END:
            return 0x00 if self.sids == 2 and SID2_MIRROR + 2 <= reg < SID2_MIRROR + 4 else 0xFF
        if reg == LADDR_HI:
            return self.laddr >> 8
        if reg == LADDR_LO:
            return self.laddr & 0xFF
        if reg == LDATA:
            value = self.load_read(self.laddr)
            self.laddr = (self.laddr + 1) & 0xFFFF
            return value
        if reg == SID_CTRL:
            return self.ctrl
        if reg == SID_STATUS:
            seen = self.run_at is not None and cycles - self.run_at >= SID_FRAME
            owner2 = STATUS_OWNER2 if self.ctrl & SID_OWNER2 else 0
            return (self.ctrl & (SID_RUN | SID_OWNER)) | owner2 | (SID_IRQ_SEEN if seen else 0)
        if reg == SID_SONG:
            return self.params[4]
        return 0xFF

    def write(self, reg, value, cycles):
        """False for a register that takes no writes (SID_STATUS)."""
        if reg == SID_STATUS:
            return False
        if (SID1 <= reg < SID1_END or SID2 <= reg < SID2_END) and self.reset_at is not None \
                and cycles - self.reset_at < SID_RESET_PULSE:
            self.dropped += 1                # the reset pulse takes it
            return True
        if SID1 <= reg < SID1_END:
            if not self.ctrl & SID_OWNER and reg < SID1_MIRROR:
                self.sid1_writes.append((reg - SID1, value))
        elif SID2 <= reg < SID2_END:
            if self.sids == 2 and not self.ctrl & SID_OWNER2 and reg < SID2_MIRROR:
                self.sid2[reg - SID2] = value
                self.sid2_writes.append((cycles, reg - SID2, value))
        elif reg == LADDR_HI:
            self.laddr = value << 8 | (self.laddr & 0xFF)
        elif reg == LADDR_LO:
            self.laddr = (self.laddr & 0xFF00) | value
        elif reg == LDATA:
            self.load_write(self.laddr, value)
            self.laddr = (self.laddr + 1) & 0xFFFF
        elif reg == SID_CTRL:
            self.log.append((cycles, value))
            if self.ctrl & SID_RUN and not value & SID_RUN:
                self.resets += 1
                self.reset_at = cycles
                self.sid2[:] = bytes(32)     # the core's one reset: both chips
            if value & SID_RUN and not self.ctrl & SID_RUN:
                self.run_at = cycles
            elif not value & SID_RUN:
                self.run_at = None
            self.ctrl = value & (SID_RUN | SID_OWNER | (SID_OWNER2 if self.sids == 2 else 0))
        elif reg == SID_SONG:
            self.params[4] = value
        return True

    @property
    def running(self):
        return bool(self.ctrl & SID_RUN)

    def ram_bytes(self, addr, n):
        return bytes(self.ram[addr:addr + n])


class Rtc:
    """The RTC as the 6809 sees it (rtl/rtc.py): the live clock from the CPU's
    cycle count since the last set, a latch the registers read."""

    def __init__(self):
        self.base = 0                    # the clock in 1/256 s at cycle `since`
        self.since = 0
        self.valid = False
        self.offset = 0                  # minutes east of UTC, -32768..32767
        self.latch = bytes(8)
        self.latches = 0                 # writes to $FF58

    def now(self, cycles):
        """The live clock in 1/256 s (32.8 bits, wrapping as the counter does)."""
        steps = (cycles - self.since) * SYNC_PER_E // RTC_PRESCALE
        return (self.base + steps) & 0xFF_FFFF_FFFF

    def set(self, seconds, cycles, frac=0):
        """SPI 0x05 01: seconds (UTC since 1970) and fraction; VALID = 1."""
        self.base = (seconds & 0xFFFF_FFFF) << 8 | frac & 0xFF
        self.since = cycles
        self.valid = True

    def read(self, reg):
        return self.latch[reg - RTC]

    def write(self, reg, value, cycles):
        """False for a register that ignores writes (all but $FF58)."""
        if reg != RTC:
            return False
        t = self.now(cycles)
        self.latch = ((t >> 8).to_bytes(4, "big") + bytes([t & 0xFF, RTC_VALID if self.valid else 0])
                      + (self.offset & 0xFFFF).to_bytes(2, "big"))
        self.latches += 1
        return True


class Anachron8Board(Anachron8Memory):
    def __init__(self, cfg, map="v2", dw=None, dw_latency=300, sid=True, sids=2, rtc=True, **kwargs):
        super().__init__(cfg, map=map, **kwargs)
        self.sid = SidPlayer(sids) if sid and map == "v2" else None
        self.sid_absent = not sid and map == "v2"
        self.rtc = Rtc() if rtc and map == "v2" else None
        self.rtc_absent = not rtc and map == "v2"
        self.dw = dw                     # a DWServer, or None: nothing answers
        self.dw_latency = dw_latency     # cycles before a server reply shows up
        self.rng = random.Random(0xACE12026)
        self.kbd_slots = [0] * KBD_SIZE
        self.kbd_in = self.kbd_out = 0   # keys written / read
        self.reset()

    def reset(self):
        """The CPU reset's share of the devices (not the MMU, not the keyboard)."""
        self.tick_ctrl = self.tick_status = self.tick_count = 0
        start = self.cpu.cycles if self.cpu else 0
        self.next_tick = start * SYNC_PER_E + TICK_PERIOD[0]
        self.next_event = -(-self.next_tick // SYNC_PER_E)
        self.dw_tx = deque()             # bytes written with no server to take them
        self.dw_ready_at = 0
        if self.dw:
            self.dw.reply.clear()

    def set_clock(self, seconds=None, offset=None, frac=0):
        """The ESP32's SPI 0x05: 01 sets seconds (and frac, 1/256 s) and VALID,
        02 the offset (signed minutes east of UTC); None leaves either as is."""
        cycles = self.cpu.cycles if self.cpu else 0
        if seconds is not None:
            self.rtc.set(seconds, cycles, frac)
        if offset is not None:
            assert -0x8000 <= offset < 0x8000, offset
            self.rtc.offset = offset

    # --- the timer and the IRQ line ------------------------------------------------
    def advance(self, cycles):
        sync = cycles * SYNC_PER_E
        while sync >= self.next_tick:
            self.tick_status = 1
            self.tick_count = (self.tick_count + 1) & 0xFF
            self.next_tick += TICK_PERIOD[self.tick_ctrl >> 1 & 1]
        self.next_event = -(-self.next_tick // SYNC_PER_E)

    def irq_line(self):
        return self.kbd_in != self.kbd_out or bool(self.tick_status & self.tick_ctrl & 1)

    # --- keyboard ----------------------------------------------------------------------
    def push_key(self, key):
        if self.kbd_in - self.kbd_out >= KBD_SIZE:
            return False
        self.kbd_slots[self.kbd_in % KBD_SIZE] = key & 0xFF
        self.kbd_in += 1
        return True

    def push_keys(self, keys):
        """Queue a string or bytes; returns how many fitted."""
        data = keys.encode("latin-1") if isinstance(keys, str) else bytes(keys)
        return sum(1 for k in data if self.push_key(k))

    @property
    def keys_queued(self):
        return self.kbd_in - self.kbd_out

    # --- DriveWire port ------------------------------------------------------------------
    def _dw_rx_ready(self):
        return bool(self.dw and self.dw.reply) and self.cpu.cycles >= self.dw_ready_at

    # --- I/O -------------------------------------------------------------------------------
    def _sid_reg(self, reg):
        return self.map == "v2" and (SID1 <= reg < SID1_END or SID2 <= reg < SID2_END
                                     or LADDR_HI <= reg < SIDCPU_END)

    def _rtc_reg(self, reg):
        return self.map == "v2" and RTC <= reg < RTC_END and (self.rtc or self.rtc_absent)

    def _io_read(self, address):
        reg = self._io_v1(address)
        if self._sid_reg(reg) and (self.sid or self.sid_absent):
            return self.sid.read(reg, self.cpu.cycles) if self.sid else 0xFF
        if self._rtc_reg(reg):
            return self.rtc.read(reg) if self.rtc else 0xFF
        if reg == KEY_STATUS:
            return 1 if self.kbd_in != self.kbd_out else 0
        if reg == KEY_DATA:
            key = self.kbd_slots[self.kbd_out % KBD_SIZE]
            if self.kbd_out != self.kbd_in:
                self.kbd_out += 1
            return key
        if reg == DW_STATUS:
            status = DW_RX if self._dw_rx_ready() else 0
            if self.dw is None and len(self.dw_tx) >= DW_FIFO:
                status |= DW_TX_FULL
            return status
        if reg == DW_DATA:
            return self.dw.reply.popleft() if self._dw_rx_ready() else 0x00
        if reg == TICK_CTRL:
            return self.tick_ctrl
        if reg == TICK_STATUS:
            return self.tick_status
        if reg == TICK_COUNT:
            return self.tick_count
        if reg == RNG_DATA:
            return self.rng.getrandbits(8)
        if reg == RNG_STATUS:
            return 0
        return super()._io_read(address)

    def _io_write(self, address, value):
        reg = self._io_v1(address)
        if self._sid_reg(reg) and (self.sid or self.sid_absent):
            if self.sid and not self.sid.write(reg, value, self.cpu.cycles) and self.strict:
                raise BusError(f"write ${value:02X} to read-only register ${address:04X}")
            return
        if self._rtc_reg(reg):
            if self.rtc and not self.rtc.write(reg, value, self.cpu.cycles) and self.strict:
                raise BusError(f"write ${value:02X} to read-only register ${address:04X}")
            return
        if reg == DW_DATA:
            if self.dw is None:
                if len(self.dw_tx) < DW_FIFO:
                    self.dw_tx.append(value)
                return
            had_reply = bool(self.dw.reply)
            self.dw.feed(value)
            if not had_reply and self.dw.reply:
                self.dw_ready_at = self.cpu.cycles + self.dw_latency
            return
        if reg == TICK_CTRL:
            self.tick_ctrl = value & 0x03
            return
        if reg == TICK_STATUS:
            if value & 1:
                self.tick_status = 0
            return
        if reg in (RNG_DATA, RNG_STATUS, KEY_STATUS, KEY_DATA, DW_STATUS, TICK_COUNT):
            if self.strict:
                raise BusError(f"write ${value:02X} to read-only register ${address:04X}")
            return
        super()._io_write(address, value)
