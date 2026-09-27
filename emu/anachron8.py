"""
Anachron8 memory model for the MC6809 emulator.

Mirrors the real machine (isp-6809/docs/MEMORY-MAP.md) strictly: any access
the hardware would not do meaningfully raises BusError, so layout mistakes
fail here instead of on the board.

  $0000-$0001  ACIA data / status (TX captured as console output, TDRE always set)
  $0040-$0041  MMU bank registers for $8000-$9FFF / $A000-$BFFF (reset 0, 1)
  $0046-$0047  keyboard status / data (always empty here)
  $1000-$7FFF  RAM
  $8000-$BFFF  SDRAM through the MMU (256 banks x 8 KB)
  $C000-$CFFF  VRAM (80x25 chars, attributes at +2000, cursor X/Y at +4000)
  $D000-$DF5F  RAM ($DF60-$DFFF: MON09 variables, writes are an error here)
  $E000-$FFFF  ROM (MON09, read-only)
"""

import array
from pathlib import Path

from config.memory_layout import Lua6809Config, Memory64K

ACIA_DATA, ACIA_STAT = 0x0000, 0x0001
ACIA_TDRE = 0x10
MMU_BANK4, MMU_BANK5 = 0x0040, 0x0041
KBD_STATUS, KBD_DATA = 0x0046, 0x0047
VRAM, VRAM_END = 0xC000, 0xD000
ROM = 0xE000
MON09_RAM = 0xDF60  # MON09 variables and RAM vectors up to the ROM
COLS, ROWS = 80, 25

MON09_HEX = Path.home() / "src/isp-6809/mon09.hex"


class BusError(Exception):
    pass


class Anachron8Memory(Memory64K):
    def __init__(self, cfg, rom_hex=MON09_HEX, **kwargs):
        super().__init__(cfg, **kwargs)
        self.sdram = bytearray(256 * 8192)
        self.bank = [0, 1]
        self.touched = set()  # RAM addresses written (with track_usage)
        if rom_hex and Path(rom_hex).exists():
            rom = [int(l, 16) for l in open(rom_hex) if l.strip()]
            self._mem[ROM:ROM + len(rom)] = array.array("B", rom)
        else:
            self._mem[ROM:] = array.array("B", [0xFF] * (0x10000 - ROM))

    # --- address decode -------------------------------------------------
    def _sdram_index(self, address):
        return (self.bank[(address >> 13) - 4] << 13) | (address & 0x1FFF)

    def read_byte(self, address):
        if address < 0x1000:
            return self._io_read(address)
        if 0x8000 <= address < 0xC000:
            return self.sdram[self._sdram_index(address)]
        return self._mem[address]

    def write_byte(self, address, value):
        if self.cpu:
            self.cpu.cycles += 1
        if address < 0x1000:
            return self._io_write(address, value)
        if address >= ROM:
            raise BusError(f"write ${value:02X} to ROM at ${address:04X}")
        if address >= MON09_RAM:
            raise BusError(f"write ${value:02X} to MON09 RAM at ${address:04X}")
        if self.track_usage:
            self.touched.add(address)
        if 0x8000 <= address < 0xC000:
            self.sdram[self._sdram_index(address)] = value
        else:
            self._mem[address] = value

    def _io_read(self, address):
        if address == ACIA_DATA:
            return 0x00
        if address == ACIA_STAT:
            return ACIA_TDRE
        if address in (MMU_BANK4, MMU_BANK5):
            return self.bank[address - MMU_BANK4]
        if address in (KBD_STATUS, KBD_DATA):
            return 0x00
        raise BusError(f"read from unmapped I/O ${address:04X}")

    def _io_write(self, address, value):
        if address == ACIA_DATA:
            if value not in (0, 0x0D):
                self.console_output.append(chr(value))
            return
        if address in (MMU_BANK4, MMU_BANK5):
            self.bank[address - MMU_BANK4] = value
            return
        raise BusError(f"write ${value:02X} to unmapped I/O ${address:04X}")

    # --- loading and inspection -----------------------------------------
    def poke(self, address, data):
        """Load bytes the way MON09's L command would (through the MMU)."""
        for i, b in enumerate(data):
            a = address + i
            if a >= ROM or a < 0x1000:
                raise BusError(f"load into ${a:04X} (not RAM)")
            if 0x8000 <= a < 0xC000:
                self.sdram[self._sdram_index(a)] = b
            else:
                self._mem[a] = b

    def screen(self):
        """The VRAM text screen as 25 strings, trailing blanks removed."""
        rows = []
        for r in range(ROWS):
            row = self._mem[VRAM + r * COLS:VRAM + (r + 1) * COLS]
            rows.append("".join(chr(c) if 32 <= c < 127 else " " for c in row).rstrip())
        return rows


class Anachron8Config(Lua6809Config):
    pass


def new_memory(track_usage=False):
    cfg = Anachron8Config({"verbosity": None, "trace": None})
    return cfg, Anachron8Memory(cfg, track_usage=track_usage)
