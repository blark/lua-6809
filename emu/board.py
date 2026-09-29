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

A write to one of the read-only registers is a BusError when strict (the
board ignores it). push_key() queues a key the way the ESP32 does (False
when the FIFO is full: the key is dropped). reset() is the CPU reset: the
tick and both DW FIFOs cleared, the keyboard FIFO and the MMU kept.
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


class Anachron8Board(Anachron8Memory):
    def __init__(self, cfg, map="v2", dw=None, dw_latency=300, **kwargs):
        super().__init__(cfg, map=map, **kwargs)
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
    def _io_read(self, address):
        reg = self._io_v1(address)
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
