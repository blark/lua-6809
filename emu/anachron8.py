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

map="v2" gives memory architecture v2 (anachron8 docs/MEMORY-V2-SPEC.md
sections 1-5) instead, starting from its reset map:

  $0000-$FDFF  slots 0-7 of the active task's map (MAP0 $FF80-$FF87, MAP1
               $FF88-$FF8F, TASK $FFA0 bit 0); reset map F8 F9 FA FB 00 01
               FC FE: block RAM $0000-$7FFF, SDRAM pages 0 and 1 at
               $8000/$A000, screen page $FC at $C000, the ROM page $FE at
               $E000. BANK4/BANK5 ($FF40/$FF41) are the active task's slot
               4/5 entries. A page is SDRAM $00-$F7, block RAM $F8-$FB,
               screen $FC/$FD, the ROM $FE (read-only), nothing $FF (reads
               $FF, writes ignored)
  $FE00-$FEFF  the constant page: offset $1E00 of page CONSTPG ($FFA2, reset $FE)
  $FF00-$FFEF  I/O: the v1 registers at $FF00 + their v1 address (ACIA
               $FF00/$FF01, BANK4/5 $FF40/$FF41, keyboard $FF46/$FF47), the
               MMU registers above and MMU_CTRL ($FFA1, bit 1 VECRAM)
  $FFF0-$FFFF  the vectors: offset $1FF0 of VECPG, CONSTPG when VECRAM = 1,
               else the ROM page $FE (CPU vector fetches read them too)
  $FF50-$FF52  video (section 6): VIDEO_CTRL (bit 0: screen $FC or $FD shown),
               PAL_INDEX, PAL_DATA (R, G, B of entry PAL_INDEX; every access
               moves to the next colour, after B to the next entry). The
               palette starts as xterm-256 (the configuration contents)

Writes to the ROM page, or to the upper half ($1000-$1FFF) of a screen page
($FC, $FD) whose FORMAT byte ($FA2) is Classic (free RAM on the board, but it
sets the screen's dirty flag; nothing uses it), are errors here, through any
slot. A Super screen (FORMAT bit 0) keeps its code high bytes and background
colours there. super_cell() reads a Super cell of either screen. The storage keeps the reset map's view in
_mem: page $F8 + n at $2000 * n, page $FC at $C000, the ROM page $FE at $E000
(offsets $1F00-$1FEF included), so code that peeks _mem under the reset map
sees the logical addresses.

strict=False (v2 only) does what the board does instead of raising: a write
to the ROM page is ignored, a write to a Classic screen's upper half is
stored (it is RAM), a free I/O address reads $FF and ignores writes. For
code that probes memory legitimately (NitrOS-9's RAM scan); the default
stays strict.
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

# map v2 (MEMORY-V2-SPEC.md): the I/O page, and the physical pages
IO2 = 0xFF00
IO2_END = 0xFFF0
SCREEN_HI = 0xD000             # the screen page's upper half (writes: BusError)
SDRAM_PAGES2 = 0xF8            # $00-$F7
PG_BRAM, PG_SCREEN0, PG_SCREEN1, PG_ROM, PG_NONE = 0xF8, 0xFC, 0xFD, 0xFE, 0xFF
RESET_MAP = (0xF8, 0xF9, 0xFA, 0xFB, 0x00, 0x01, 0xFC, 0xFE)
MAP0, MAP1, TASK, MMU_CTRL, CONSTPG = 0xFF80, 0xFF88, 0xFFA0, 0xFFA1, 0xFFA2
VECRAM = 0x02                  # MMU_CTRL bit 1
CONST_PAGE, VECTORS = 0xFE00, 0xFFF0
VIDEO_CTRL, PAL_INDEX, PAL_DATA = 0xFF50, 0xFF51, 0xFF52
FORMAT, FMT_SUPER = 0xFA2, 0x01   # a screen page's FORMAT byte and its Super bit
SUPER_HI, SUPER_ATTR = 0x1000, 0x7D0  # plane B (code high bytes), the colour planes' offset
ACIA_FREE2 = range(0xFF02, 0xFF04)  # free; MON09's INIT writes them (harmless)


class BusError(Exception):
    pass


def xterm256():
    """The palette's configuration contents (MEMORY-V2-SPEC.md section 6), 768 bytes."""
    ansi = ("000000 800000 008000 808000 000080 800080 008080 C0C0C0 "
            "808080 FF0000 00FF00 FFFF00 0000FF FF00FF 00FFFF FFFFFF").split()
    pal = bytearray(b"".join(bytes.fromhex(h) for h in ansi))
    lv = (0x00, 0x5F, 0x87, 0xAF, 0xD7, 0xFF)
    for i in range(216):
        pal += bytes((lv[i // 36], lv[i // 6 % 6], lv[i % 6]))
    for n in range(24):
        pal += bytes((8 + 10 * n,) * 3)
    return pal


class Anachron8Memory(Memory64K):
    def __init__(self, cfg, rom_hex=MON09_HEX, map="v1", strict=True, **kwargs):
        super().__init__(cfg, **kwargs)
        self.strict = strict
        if map not in ("v1", "v2"):
            raise ValueError(f"map must be v1 or v2, not {map!r}")
        self.map = map
        self.sdram = bytearray(256 * 8192)
        self.screen1 = bytearray(8192)  # v2: page $FD
        self.bank = [0, 1]                     # v1: BANK4/BANK5
        self.maps = [list(RESET_MAP), list(RESET_MAP)]   # v2: MAP0, MAP1
        self.task = 0
        self.mmu_ctrl = 0
        self.constpg = PG_ROM
        self._slots = None                      # v2: (buffer, base) of each active slot
        self.video_ctrl = 0                     # v2: VIDEO_CTRL
        self.palette = xterm256()               # v2: 256 x R, G, B
        self.pal_pos = 0                        # v2: PAL_INDEX * 3 + the colour step
        self.touched = set()  # RAM addresses written (with track_usage)
        if map == "v2" and rom_hex == MON09_HEX:
            rom_hex = None  # MON09 v1 does not run under v2
        if rom_hex and Path(rom_hex).exists():
            rom = [int(l, 16) for l in open(rom_hex) if l.strip()]
            self._mem[ROM:ROM + len(rom)] = array.array("B", rom)
        else:
            self._mem[ROM:] = array.array("B", [0xFF] * (0x10000 - ROM))

    # --- address decode -------------------------------------------------
    def _sdram_index(self, address):
        return (self.bank[(address >> 13) - 4] << 13) | (address & 0x1FFF)

    def read_byte(self, address):
        if self.map == "v2":
            return self._read_v2(address)
        if address < 0x1000:
            return self._io_read(address)
        if 0x8000 <= address < 0xC000:
            return self.sdram[self._sdram_index(address)]
        return self._mem[address]

    def write_byte(self, address, value):
        if self.cpu:
            self.cpu.cycles += 1
        if self.map == "v2":
            return self._write_v2(address, value)
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

    # --- map v2 ------------------------------------------------------------
    def _page(self, page, offset):
        """(buffer, index) of a physical page's byte, or None for page $FF."""
        if page < SDRAM_PAGES2:
            return self.sdram, page << 13 | offset
        if page < PG_SCREEN0:
            return self._mem, (page - PG_BRAM) << 13 | offset
        if page == PG_SCREEN0:
            return self._mem, 0xC000 | offset
        if page == PG_SCREEN1:
            return self.screen1, offset
        if page == PG_ROM:
            return self._mem, ROM | offset
        return None

    def _remap(self):
        """Cache where each slot of the active map reads from (after a map change)."""
        self._slots = [self._page(pg, 0) for pg in self.maps[self.task]]

    def _where(self, address):
        """(page, offset) the CPU reaches at a logical address outside the I/O page."""
        if address >= VECTORS:
            return (self.constpg if self.mmu_ctrl & VECRAM else PG_ROM), address & 0x1FFF
        if address >= CONST_PAGE:
            return self.constpg, address & 0x1FFF
        return self.maps[self.task][address >> 13], address & 0x1FFF

    def _read_v2(self, address):
        if address < CONST_PAGE:
            if self._slots is None:
                self._remap()
            where = self._slots[address >> 13]
            return where[0][where[1] | (address & 0x1FFF)] if where else 0xFF
        if IO2 <= address < IO2_END:
            return self._io_read(address)
        where = self._page(*self._where(address))
        return where[0][where[1]] if where else 0xFF

    def _write_v2(self, address, value):
        if IO2 <= address < IO2_END:
            return self._io_write(address, value)
        if self.track_usage:
            self.touched.add(address)
        self._store_v2(address, value)

    def _store_v2(self, address, value):
        """A CPU write outside the I/O page, checked."""
        page, offset = self._where(address)
        if page == PG_ROM:
            if not self.strict:
                return          # the board ignores it: the ROM page is read-only
            raise BusError(f"write ${value:02X} to ROM page $FE at ${address:04X}")
        if page in (PG_SCREEN0, PG_SCREEN1) and offset >= SUPER_HI \
                and not self.page_bytes(page, FORMAT, 1)[0] & FMT_SUPER and self.strict:
            raise BusError(f"write ${value:02X} to the upper half of Classic screen ${page:02X} at ${address:04X}")
        where = self._page(page, offset)
        if where:
            where[0][where[1]] = value

    def _video_read(self, address):
        """v2 video registers, or None."""
        if address == VIDEO_CTRL:
            return self.video_ctrl
        if address == PAL_INDEX:
            return self.pal_pos // 3
        if address == PAL_DATA:
            value = self.palette[self.pal_pos]
            self.pal_pos = (self.pal_pos + 1) % len(self.palette)
            return value
        return None

    def _video_write(self, address, value):
        """v2 video registers: True if it was one."""
        if address == VIDEO_CTRL:
            self.video_ctrl = value & 1
        elif address == PAL_INDEX:
            self.pal_pos = value * 3
        elif address == PAL_DATA:
            self.palette[self.pal_pos] = value
            self.pal_pos = (self.pal_pos + 1) % len(self.palette)
        else:
            return False
        return True

    def _mmu_read(self, address):
        """v2 MMU registers, or None."""
        if MAP0 <= address < MAP1 + 8:
            return self.maps[(address - MAP0) >> 3][address & 7]
        if address == TASK:
            return self.task
        if address == MMU_CTRL:
            return self.mmu_ctrl
        if address == CONSTPG:
            return self.constpg
        return None

    def _mmu_write(self, address, value):
        """v2 MMU registers: True if it was one."""
        if MAP0 <= address < MAP1 + 8:
            self.maps[(address - MAP0) >> 3][address & 7] = value
            self._slots = None
        elif address == TASK:
            self.task = value & 1
            self._slots = None
        elif address == MMU_CTRL:
            self.mmu_ctrl = value & VECRAM
        elif address == CONSTPG:
            self.constpg = value
        else:
            return False
        return True

    # --- I/O -------------------------------------------------------------
    def _io_v1(self, address):
        """The v1 address of an I/O register (v2: $FF00 + the v1 address)."""
        return address - IO2 if self.map == "v2" else address

    def _io_read(self, address):
        if self.map == "v2":
            value = self._mmu_read(address)
            if value is None:
                value = self._video_read(address)
            if value is not None:
                return value
            if address in (IO2 + MMU_BANK4, IO2 + MMU_BANK5):
                return self.maps[self.task][4 + address - IO2 - MMU_BANK4]
        address = self._io_v1(address)
        if address == ACIA_DATA:
            return 0x00
        if address == ACIA_STAT:
            return ACIA_TDRE
        if address in (MMU_BANK4, MMU_BANK5):
            return self.bank[address - MMU_BANK4]
        if address in (KBD_STATUS, KBD_DATA):
            return 0x00
        if self.map == "v2" and not self.strict:
            return 0xFF         # a free I/O address reads $FF on the board
        raise BusError(f"read from unmapped I/O ${address + (IO2 if self.map == 'v2' else 0):04X}")

    def _io_write(self, address, value):
        if self.map == "v2":
            if self._mmu_write(address, value) or self._video_write(address, value):
                return
            if address in (IO2 + MMU_BANK4, IO2 + MMU_BANK5):
                self.maps[self.task][4 + address - IO2 - MMU_BANK4] = value
                self._slots = None
                return
            if address == IO2 + ACIA_STAT or address in ACIA_FREE2:
                return          # ACIA reset and command (MON09's INIT): nothing to model
        address = self._io_v1(address)
        if address == ACIA_DATA:
            if value not in (0, 0x0D):
                self.console_output.append(chr(value))
            return
        if address in (MMU_BANK4, MMU_BANK5):
            self.bank[address - MMU_BANK4] = value
            return
        if self.map == "v2" and not self.strict:
            return              # and ignores writes
        raise BusError(f"write ${value:02X} to unmapped I/O ${address + (IO2 if self.map == 'v2' else 0):04X}")

    # --- loading and inspection -----------------------------------------
    def poke(self, address, data):
        """Load bytes the way MON09's L command would (through the MMU)."""
        for i, b in enumerate(data):
            a = address + i
            if a >= ROM or (a < 0x1000 and self.map == "v1"):
                raise BusError(f"load into ${a:04X} (not RAM)")
            if self.map == "v2":
                if SCREEN_HI <= a:
                    raise BusError(f"load into ${a:04X} (the screen page's upper half)")
                self._store_v2(a, b)
            elif 0x8000 <= a < 0xC000:
                self.sdram[self._sdram_index(a)] = b
            else:
                self._mem[a] = b

    def page_bytes(self, page, offset, n):
        """n bytes of physical page `page` from `offset` (v2; page $FF reads $FF)."""
        out = bytearray()
        for i in range(n):
            where = self._page(page, offset + i)
            out.append(where[0][where[1]] if where else 0xFF)
        return bytes(out)

    def super_cell(self, page, row, col):
        """(code, fg, bg) of a Super cell of screen page $FC or $FD (v2)."""
        off = row * COLS + col
        lo, fg = self.page_bytes(page, off, 1)[0], self.page_bytes(page, SUPER_ATTR + off, 1)[0]
        hi, bg = self.page_bytes(page, SUPER_HI + off, 1)[0], self.page_bytes(page, SUPER_HI + SUPER_ATTR + off, 1)[0]
        return hi << 8 | lo, fg, bg

    def super_text(self, page, row):
        """A row of a Super screen as a string, trailing blanks removed (U+0000 as a blank)."""
        return "".join(chr(self.super_cell(page, row, c)[0] or 0x20) for c in range(COLS)).rstrip()

    def screen(self):
        """The VRAM text screen as 25 strings, trailing blanks removed."""
        rows = []
        for r in range(ROWS):
            row = self._mem[VRAM + r * COLS:VRAM + (r + 1) * COLS]
            rows.append("".join(chr(c) if 32 <= c < 127 else " " for c in row).rstrip())
        return rows


class Anachron8Config(Lua6809Config):
    pass


def new_memory(track_usage=False, map="v1"):
    cfg = Anachron8Config({"verbosity": None, "trace": None})
    return cfg, Anachron8Memory(cfg, track_usage=track_usage, map=map)
