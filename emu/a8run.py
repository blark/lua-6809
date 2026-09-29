#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["MC6809"]
# ///
"""
Run code on the emulated Anachron8 (memory map v2 by default) with the tick
timer, the keyboard FIFO, a DriveWire server and working interrupts: the
bench for bringing up NitrOS-9.

    uv run emu/a8run.py --raw rom.bin@FE --drive 0=nos9.dsk -n 5000000
    uv run emu/a8run.py --decb boot.bin --map0 F8,F9,FA,FB,00,01,FC,FE \\
        --until-pc C0DE --trace 64 --keys 'dir\\r'

Numbers are hex ($, 0x or bare). Loads write physical storage directly
(the ROM page included), before the run:

  --raw FILE@PAGE[:OFFSET]   raw bytes into physical page PAGE from OFFSET,
                             running on into the following pages
  --decb FILE[@PAGE]         DECB segments (the exec address becomes the PC)
  --s19 FILE[@PAGE]          S1 records (S9 gives the PC)
      without @PAGE through the active map (after --map0/--map1/--task),
      with @PAGE a 16-bit address A goes to page PAGE + A/$2000, offset A%$2000

The PC is --pc, else the last exec address loaded, else the reset vector.
The run stops after -n instructions, at --until-pc, when CWAI/SYNC wait
with nothing to wake them, or on an error; then it prints the reason, the
registers, the ACIA output and the Classic screen shown (VIDEO_CTRL bit 0).
With --trace N the last N instructions are kept and printed on an error
(and at the end with --trace-always).

Python use: Machine(...) exposes .cpu (A8CPU), .mem (Anachron8Board), .dw
(DWServer) and the loaders; see tests/emu/test_os9_devices.py.
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from emu.a8cpu import A8CPU, Idle  # noqa: E402
from emu.anachron8 import (  # noqa: E402
    COLS, FMT_SUPER, FORMAT, PG_SCREEN0, ROWS, Anachron8Config, BusError,
)
from emu.board import Anachron8Board  # noqa: E402
from emu.drivewire import DWProtocolError, DWServer  # noqa: E402

PAGE = 0x2000


def parse_s19(data):
    """{address: byte}, start address (or None) of S1/S9 text."""
    out, start = {}, None
    for line in data.split():
        if line.startswith("S1"):
            n, addr = int(line[2:4], 16), int(line[4:8], 16)
            for i, b in enumerate(bytes.fromhex(line[8:8 + (n - 3) * 2])):
                out[(addr + i) & 0xFFFF] = b
        elif line.startswith("S9"):
            start = int(line[4:8], 16)
    return out, start


def parse_decb(data):
    """{address: byte}, exec address of a DECB (CoCo LOADM) binary."""
    out, i = {}, 0
    while i + 5 <= len(data):
        kind, n, addr = data[i], data[i + 1] << 8 | data[i + 2], data[i + 3] << 8 | data[i + 4]
        i += 5
        if kind == 0xFF:
            return out, addr
        if kind != 0x00:
            raise ValueError(f"DECB: bad segment byte ${kind:02X} at {i - 5}")
        for j, b in enumerate(data[i:i + n]):
            out[(addr + j) & 0xFFFF] = b
        i += n
    raise ValueError("DECB: no end segment")


class Machine:
    def __init__(self, map="v2", strict=True, drives=None, dw=True, dw_latency=300, **mem_kwargs):
        self.cfg = Anachron8Config({"verbosity": None, "trace": None})
        self.dw = DWServer() if dw else None
        for num, spec in (drives or {}).items():
            path, writeback = (spec, False) if isinstance(spec, (str, Path)) else spec
            self.dw.attach(num, path, writeback=writeback)
        self.mem = Anachron8Board(self.cfg, map=map, dw=self.dw, dw_latency=dw_latency,
                                  strict=strict, **mem_kwargs)
        self.cpu = A8CPU(self.mem, self.cfg)
        self.mem.cpu = self.cpu
        self.cpu.set_cc(0x50)            # I and F set, as after a reset
        self.start = None                # exec address of the last load

    # --- loading -------------------------------------------------------------------------
    def poke_phys(self, page, offset, data):
        """Store bytes into physical pages directly (v2; no checks, any page but $FF)."""
        for i, b in enumerate(data):
            pg, off = page + (offset + i) // PAGE, (offset + i) % PAGE
            where = self.mem._page(pg, off)
            if where is None:
                raise BusError(f"load into page ${pg:02X} (nothing there)")
            where[0][where[1]] = b

    def poke_logical(self, address, byte):
        """Store a byte where the active map puts a logical address (outside the I/O page)."""
        if self.mem.map == "v1":
            return self.mem.poke(address, [byte])
        pg, off = self.mem._where(address)
        self.poke_phys(pg, off, [byte])
        self.mem._slots = None

    def load_raw(self, path, page, offset=0):
        self.poke_phys(page, offset, Path(path).read_bytes())

    def load_image(self, image, base_page=None):
        for addr, b in sorted(image.items()):
            if base_page is None:
                self.poke_logical(addr, b)
            else:
                self.poke_phys(base_page + addr // PAGE, addr % PAGE, [b])

    def load_decb(self, path, base_page=None):
        image, start = parse_decb(Path(path).read_bytes())
        self.load_image(image, base_page)
        self.start = start
        return start

    def load_s19(self, path, base_page=None):
        image, start = parse_s19(Path(path).read_text())
        self.load_image(image, base_page)
        if start is not None:
            self.start = start
        return start

    def set_map(self, task, pages):
        self.mem.maps[task][:] = list(pages)
        self.mem._slots = None

    # --- running -----------------------------------------------------------------------------
    def run(self, count, until_pc=None, until=None):
        """Run; returns (reason, steps, error or None). Errors are caught and returned."""
        try:
            reason, steps = self.cpu.run_for(count, until_pc, until)
            return reason, steps, None
        except Idle as e:
            return "idle", None, e
        except (BusError, DWProtocolError, NotImplementedError, RuntimeError, SystemExit) as e:
            return "error", None, e

    # --- output ------------------------------------------------------------------------------
    def console(self):
        return "".join(self.mem.console_output)

    def screen_text(self, page=None):
        """The screen page shown (or `page`) as 25 strings, trailing blanks removed."""
        if page is None:
            page = PG_SCREEN0 + (self.mem.video_ctrl & 1)
        if self.mem.page_bytes(page, FORMAT, 1)[0] & FMT_SUPER:
            return [self.mem.super_text(page, r) for r in range(ROWS)]
        rows = []
        for r in range(ROWS):
            row = self.mem.page_bytes(page, r * COLS, COLS)
            rows.append("".join(chr(c) if 32 <= c < 127 else " " for c in row).rstrip())
        return rows

    def registers(self):
        c = self.cpu
        return (f"PC={c.program_counter.value:04X} A={c.accu_a.value:02X} B={c.accu_b.value:02X} "
                f"X={c.index_x.value:04X} Y={c.index_y.value:04X} U={c.user_stack_pointer.value:04X} "
                f"S={c.system_stack_pointer.value:04X} DP={c.direct_page.value:02X} CC={c.get_cc_value():02X} "
                f"task={self.mem.task} map={' '.join(f'{p:02X}' for p in self.mem.maps[self.mem.task])}")


# --- command line ---------------------------------------------------------------------------------
def hexnum(text):
    text = text.strip()
    for prefix in ("$", "0x", "0X"):
        if text.startswith(prefix):
            text = text[len(prefix):]
    return int(text, 16)


def file_at(spec, need_page=False):
    path, _, where = spec.rpartition("@") if "@" in spec else (spec, "", "")
    if not where:
        if need_page:
            raise argparse.ArgumentTypeError(f"{spec}: FILE@PAGE[:OFFSET] expected")
        return spec, None, 0
    page, _, offset = where.partition(":")
    return path, hexnum(page), hexnum(offset) if offset else 0


def pages(text):
    out = [hexnum(p) for p in text.split(",")]
    if len(out) != 8:
        raise argparse.ArgumentTypeError("8 pages expected")
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--map", choices=("v1", "v2"), default="v2", dest="memmap")
    ap.add_argument("--no-strict", action="store_true", help="board behaviour instead of BusError")
    ap.add_argument("--raw", action="append", default=[], metavar="FILE@PAGE[:OFFSET]")
    ap.add_argument("--decb", action="append", default=[], metavar="FILE[@PAGE]")
    ap.add_argument("--s19", action="append", default=[], metavar="FILE[@PAGE]")
    ap.add_argument("--map0", type=pages)
    ap.add_argument("--map1", type=pages)
    ap.add_argument("--task", type=hexnum)
    ap.add_argument("--constpg", type=hexnum)
    ap.add_argument("--mmu-ctrl", type=hexnum)
    ap.add_argument("--pc", type=hexnum)
    ap.add_argument("--s", type=hexnum, help="S register")
    ap.add_argument("--cc", type=hexnum, default=0x50)
    ap.add_argument("--drive", action="append", default=[], metavar="N=PATH[:rw]",
                    help="DriveWire drive backed by an image (:rw writes changes back)")
    ap.add_argument("-n", "--count", type=int, default=10_000_000, help="instructions")
    ap.add_argument("--until-pc", type=hexnum)
    ap.add_argument("--keys", default="", help="keys queued at the start (\\r, \\n, \\e escapes)")
    ap.add_argument("--trace", type=int, default=0, metavar="N")
    ap.add_argument("--trace-always", action="store_true")
    ap.add_argument("--no-screen", action="store_true")
    args = ap.parse_args(argv)

    drives = {}
    for spec in args.drive:
        num, _, path = spec.partition("=")
        rw = path.endswith(":rw")
        drives[int(num)] = (path[:-3] if rw else path, rw)
    m = Machine(map=args.memmap, strict=not args.no_strict, drives=drives)
    mem = m.mem
    if args.map0:
        m.set_map(0, args.map0)
    if args.map1:
        m.set_map(1, args.map1)
    if args.task is not None:
        mem.task = args.task & 1
        mem._slots = None
    if args.constpg is not None:
        mem.constpg = args.constpg
    if args.mmu_ctrl is not None:
        mem.mmu_ctrl = args.mmu_ctrl & 0x02
    for spec in args.raw:
        path, page, offset = file_at(spec, need_page=True)
        m.load_raw(path, page, offset)
    for spec in args.decb:
        path, page, _ = file_at(spec)
        m.load_decb(path, page)
    for spec in args.s19:
        path, page, _ = file_at(spec)
        m.load_s19(path, page)
    cpu = m.cpu
    pc = args.pc if args.pc is not None else m.start if m.start is not None else mem.read_word(0xFFFE)
    cpu.program_counter.set(pc)
    if args.s is not None:
        cpu.system_stack_pointer.set(args.s)
    cpu.set_cc(args.cc)
    keys = args.keys.encode().decode("unicode_escape")
    mem.push_keys(keys.replace("\\e", "\x1b"))
    if args.trace:
        cpu.enable_trace(args.trace)

    reason, steps, err = m.run(args.count, args.until_pc)
    print(f"stop: {reason}" + (f" after {steps} instructions" if steps is not None else "")
          + (f": {type(err).__name__}: {err}" if err else ""))
    print(f"cycles {cpu.cycles} ({cpu.cycles / 6.25e6:.3f} s at 6.25 MHz), "
          f"interrupts {cpu.interrupts}, last instruction ${cpu.last_op_address:04X}")
    print(m.registers())
    if cpu.trace and (err or args.trace_always):
        print("--- trace (oldest first) ---")
        print(cpu.format_trace())
    out = m.console()
    if out:
        print("--- ACIA ---")
        print(out.rstrip("\n"))
    if not args.no_screen:
        rows = m.screen_text()
        while rows and not rows[-1]:
            rows.pop()
        if rows:
            print("--- screen ---")
            print("\n".join(rows))
    return 1 if reason == "error" else 0


if __name__ == "__main__":
    sys.exit(main())
