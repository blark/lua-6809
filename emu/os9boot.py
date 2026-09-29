#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["MC6809"]
# ///
"""
Boot NitrOS-9 Level 2 (the anachron8 port) on the emulated Anachron8.

load_os9() is the boot stub's catalog type 4 (anachron8 docs/MEMORY-V2-SPEC.md
section 8C), done as the stub will do it: every byte goes through the CPU's
bus under the reset map (page $00 through slot 4 at $8000, KrnBlk through
slot 5 at $A000, slot 5 restored to $01), then the trampoline's register
writes (MAP0/MAP1 slots 0 and 7, CONSTPG, MMU_CTRL, TASK) and the jump to
the reset vector with the CPU state of a reset (IRQ/FIRQ masked, DP=0).
It can therefore serve as the reference for the stub.

Page $00 (the system block) after the loader:
  $0000-$00FF  zero, except
  $005E        RTS               D.BtBug (krn and krnp2 call it with a
                                 progress character in A)
  $006B        JMP $006E         D.Crash (krn, krnp2, ioman, sysgo and the
  $006E        BRA *             clock jump here on a fatal error; FIRQ and
                                 NMI too: krn's d.XFIRQ/d.XNMI are D.Crash)
The halt loop lives inside D.Crash's own six bytes ($6B-$70): no kernel
module writes them (only the Wildbits and Pico-Thing kernels plant their own
crash code there), and krn clears only $0100-$1FFF.

Slots 1-6 keep the reset map (F9 FA FB 00 01 FC in both maps): krn does not
read them. Its memory probe writes slot 5 first, and F$SetTsk loads the
system image into all slots before anything uses them; a boot with slots
1-6 at $FF (nothing) reaches the shell the same way.

OS9Session boots with drive 0 = a dummy catalog and drive 1 = a copy of the
OS-9 disk (writes land in the copy), strict=False (the board's behaviour),
and records the D.BtBug characters, a stop at the halt loop (with the trace
ring), any DriveWire access to drive 0 and any CPU write to pages $FC-$FF.

The console is two screens (a8vtio + CoClassic): /Term on screen 0 (page
$FC) and /W1 on screen 1 (page $FD), each with a shell; the keyboard goes to
the displayed one (VIDEO_CTRL bit 0, displayed()), and Ctrl-B (SWITCH_KEY)
shows the other. The session reads prompts, the typed echo and command output
from either page (screen(), cursor(), screen_dump(); page= picks the screen,
/Term's by default). The ACIA is /T1; its output is console().

Either screen can be made Super (CoSuper: UTF-8 out, four planes, 256
colours; FORMAT bit 0) at run time, with `vmode s </w1` or ESC $20 (DWSet)
written to it; super_screen(), super_cell() and super_dump() read it, and
screen_dump() picks the right one by the page's FORMAT. The low plane of a
Super page holds ASCII as a Classic page does, so the prompt helpers work
on both.

    uv run emu/os9boot.py                       # boot to the shell prompt
    uv run emu/os9boot.py --cmd dir --cmd mfree # then type commands
    uv run emu/os9boot.py --w1 --cmd procs      # ... on /W1 (after Ctrl-B)

Defaults come from ~/src/nitros9/recipes/anachron8/dw.
"""

import argparse
import json
import re
import shutil
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from emu.a8run import Machine  # noqa: E402

RECIPE = Path.home() / "src/nitros9/recipes/anachron8/dw"
KERNEL, KERNEL_JSON, DISK = RECIPE / "kernel", RECIPE / "kernel.json", RECIPE / "l2_anachron8_dw.dsk"

# Anachron8 v2 registers and the reset map's windows
MAP0, MAP1, TASK, MMU_CTRL, CONSTPG = 0xFF80, 0xFF88, 0xFFA0, 0xFFA1, 0xFFA2
VECRAM = 0x02
WIN4, WIN5 = 0x8000, 0xA000          # slots 4 and 5 (reset map: pages $00, $01)
RESET_SLOT5 = 0x01

# OS-9 system block (defs/os9.d)
D_BTBUG, D_CRASH, HALT = 0x5E, 0x6B, 0x6E
RTS, JMP, BRA = 0x39, 0x7E, 0x20
VECTOR_STUBS = (0xFEEE, 0xFEF1, 0xFEF4, 0xFEF7, 0xFEFA, 0xFEFD)   # $FFF2 SWI3 .. $FFFC NMI
PROMPT = re.compile(r"\{\w+\|\d+\}[^\n]*:\s*$")    # Shell+: {Term|02}/DD:, {W1|05}/DD:
SCREEN0, SCREEN1 = 0xFC, 0xFD                     # the screen pages of /Term and /W1
SCREENS = (SCREEN0, SCREEN1)
SWITCH_KEY = "\x02"                               # Ctrl-B: a8vtio shows the other screen
FORMAT = 0xFA2                                    # a screen page's FORMAT byte (bit 0 Super)


def kernel_info(path=KERNEL_JSON):
    info = json.loads(Path(path).read_text())
    num = lambda s: int(s, 16)    # noqa: E731
    return {"load": num(info["load"]), "entry": num(info["entry"]), "krnblk": num(info["krnblk"]),
            "vectors": [num(info["vectors"][k]) for k in ("SWI3", "SWI2", "FIRQ", "IRQ", "SWI", "NMI")]}


def load_os9(m, kernel=KERNEL, info=None):
    """Catalog type 4 on Machine m (map v2, reset map): load, map, hand over. Returns the entry."""
    info = info or kernel_info()
    image = Path(kernel).read_bytes()
    krnblk, load, entry = info["krnblk"], info["load"], info["entry"]
    if info["vectors"] != list(VECTOR_STUBS):
        raise ValueError(f"kernel.json vectors {info['vectors']} are not krn's stubs at $FEEE")
    if not (0xE000 <= load and load + len(image) <= 0xFF00 and 0xE000 <= entry < 0xFE00):
        raise ValueError("kernel image or entry outside $E000-$FEFF")
    w = m.mem.write_byte
    # page $00 through slot 4 (the reset map has page $00 there)
    w(MAP0 + 4, 0x00)
    for a in range(0x100):
        w(WIN4 + a, 0)
    w(WIN4 + D_BTBUG, RTS)
    for i, b in enumerate((JMP, 0x00, HALT, BRA, 0xFE)):          # JMP $006E ; $006E: BRA *
        w(WIN4 + D_CRASH + i, b)
    # KrnBlk through slot 5: the kernel at its offset, then the vectors at $1FF0
    w(MAP0 + 5, krnblk)
    for i, b in enumerate(image):
        w(WIN5 + (load - 0xE000) + i, b)
    vectors = [0x0000] + list(VECTOR_STUBS) + [entry]               # $FFF0 (reserved) .. $FFFE
    for i, v in enumerate(vectors):
        w(WIN5 + 0x1FF0 + 2 * i, v >> 8)
        w(WIN5 + 0x1FF1 + 2 * i, v & 0xFF)
    w(MAP0 + 5, RESET_SLOT5)
    # the trampoline (it runs from $3F00, slot 1, which it does not remap)
    for base in (MAP0, MAP1):
        w(base + 0, 0x00)
        w(base + 7, krnblk)
    w(CONSTPG, krnblk)
    w(MMU_CTRL, VECRAM)
    w(TASK, 0)
    cpu = m.cpu
    cpu.set_cc(0x50)                     # IRQ and FIRQ masked, as after a reset
    cpu.direct_page.set(0)
    cpu.program_counter.set(m.mem.read_word(0xFFFE))
    return entry


class OS9Session:
    """A booted NitrOS-9 on a Machine, with the checks the bring-up needs.

    Records: `btbug` (D.BtBug characters), `halted` (PC reached the D.Crash
    halt loop), `drive0` (DriveWire requests for drive 0), `special_writes`
    ((page, offset, value, pc) of CPU writes to pages $FC-$FF).
    """

    def __init__(self, kernel=KERNEL, info=None, disk=DISK, workdir=None, trace=256, **kw):
        self._tmp = None
        if workdir is None:
            self._tmp = tempfile.TemporaryDirectory(prefix="os9boot-")
            workdir = self._tmp.name
        workdir = Path(workdir)
        self.disk = workdir / "drive1.dsk"
        shutil.copyfile(disk, self.disk)
        self.catalog = workdir / "drive0.dsk"
        self.catalog.write_bytes(b"A8CATALOG-DUMMY\0".ljust(256, b"\0") * 4)
        self.m = Machine(strict=False, drives={0: (self.catalog, False), 1: (self.disk, True)}, **kw)
        self.entry = load_os9(self.m, kernel, info)
        self.btbug, self.drive0, self.special_writes = [], [], []
        self.halted = False
        self._until = None
        self._watch()
        if trace:
            self.m.cpu.enable_trace(trace)

    def _watch(self):
        mem, dw = self.m.mem, self.m.dw
        store = mem._store_v2

        self._screen_written = False

        def checked_store(address, value):
            page, offset = mem._where(address)
            if page >= 0xFC:
                self.special_writes.append((page, offset, value, self.m.cpu.last_op_address))
                self._screen_written = True
            store(address, value)
        mem._store_v2 = checked_store

        drive0 = dw.drives[0]
        read, write = drive0.read, drive0.write

        def read0(lsn):
            self.drive0.append(("read", lsn))
            return read(lsn)

        def write0(lsn, data):
            self.drive0.append(("write", lsn))
            return write(lsn, data)
        drive0.read, drive0.write = read0, write0

    # --- running -------------------------------------------------------------------
    def _stop(self, cpu):
        pc = cpu.program_counter.value
        if pc < 0x100 and self.m.mem.task == 0 and self.m.mem.maps[0][0] == 0x00:
            if pc == HALT:
                self.halted = True
                return True
            if pc == D_BTBUG:
                self.btbug.append(chr(cpu.accu_a.value & 0x7F))
        return self._until is not None and self._until(cpu)

    def run(self, count, until=None):
        """Run up to `count` instructions; stop at the halt loop or when until(cpu).

        Returns (reason, steps, error): reason "count", "until", "halt", "idle" or "error".
        """
        self._until = until
        reason, steps, err = self.m.run(count, until=self._stop)
        if reason == "until" and self.halted:
            reason = "halt"
        return reason, steps, err

    def console(self):
        return self.m.console()

    def wait_for(self, text, count, start=0):
        """Run until the console output after index `start` contains `text` (a string),
        or matches it (a compiled pattern, searched)."""
        out = self.m.mem.console_output
        seen = [start]
        match = text.search if hasattr(text, "search") else (lambda s: text in s)

        def found(cpu):
            if len(out) == seen[0]:
                return False
            seen[0] = len(out)
            return bool(match("".join(out[start:])))
        return self.run(count, found)

    def type(self, text, count):
        """Queue `text` into the keyboard FIFO as it drains (16 entries), running meanwhile."""
        data = text.encode("latin-1")
        pos = [0]
        mem = self.m.mem

        def feed(cpu):
            while pos[0] < len(data) and mem.push_key(data[pos[0]]):
                pos[0] += 1
            return pos[0] == len(data) and mem.keys_queued == 0
        return self.run(count, feed)

    def wait_screen(self, test, count):
        """Run until test() holds; test() is tried only after a CPU write to pages $FC-$FF."""
        def check(cpu):
            if not self._screen_written:
                return False
            self._screen_written = False
            return test()
        self._screen_written = True
        return self.run(count, check)

    def cursor_line(self, page=SCREEN0):
        """The text of the cursor's row up to the cursor, and whether the rest of it is blank."""
        cur = self.cursor(page)
        if cur is None:
            return None, False
        col, row = cur
        text = "".join(chr(c) if 32 <= c < 127 else " " for c in self.peek(page, row * 80, 80))
        return text[:col].rstrip(), not text[col:].strip()

    def at_prompt(self, page=SCREEN0):
        """True when the cursor stands just after a Shell+ prompt on an otherwise blank line."""
        before, rest_blank = self.cursor_line(page)
        return before is not None and rest_blank and bool(PROMPT.fullmatch(before))

    def wait_prompt(self, count, page=SCREEN0):
        """Run until the screen shows the shell prompt at the cursor."""
        return self.wait_screen(lambda: self.at_prompt(page), count)

    def command(self, line, count=50_000_000, page=None):
        """Type a command line on the keyboard and run until the next prompt; returns
        (reason, screen rows). The line is typed, its echo awaited, then Enter. The
        keys go to the displayed screen, which is where the echo and prompt are awaited
        unless `page` says otherwise."""
        page = self.displayed() if page is None else page
        reason, _, err = self.type(line, count)
        if reason == "until" and line:
            reason, _, err = self.wait_screen(lambda: (self.cursor_line(page)[0] or "").endswith(line), count)
        if reason == "until":
            reason, _, err = self.type("\r", count)
        if reason == "until":
            reason, _, err = self.wait_prompt(count, page)
        return (reason if not err else f"error: {err}"), self.screen(page)

    def switch_screen(self, count=5_000_000):
        """Press the switch key and run until VIDEO_CTRL changes; returns the page displayed."""
        shown = self.displayed()
        reason, _, err = self.type(SWITCH_KEY, count)
        if reason == "until":
            reason, _, err = self.run(count, lambda cpu: self.displayed() != shown)
        if reason != "until":
            raise RuntimeError(f"switch key not taken: {reason} {err}")
        return self.displayed()

    # --- inspection ------------------------------------------------------------------
    def screen(self, page=SCREEN0):
        """A Classic screen (/Term's page $FC by default) as 25 strings, trailing blanks removed."""
        return self.m.screen_text(page)

    def cursor(self, page=SCREEN0):
        """(column, row) of a screen's cursor (/Term's by default), None when hidden."""
        return self.m.cursor(page)

    def displayed(self):
        """The page on display: VIDEO_CTRL bit 0 selects $FC or $FD."""
        return SCREEN0 + (self.m.mem.video_ctrl & 1)

    def is_super(self, page=SCREEN0):
        return bool(self.peek(page, FORMAT)[0] & 1)

    def super_cell(self, page, row, col):
        """(code, fg, bg) of a Super screen's cell."""
        return self.m.mem.super_cell(page, row, col)

    def super_screen(self, page=SCREEN1):
        """A Super screen as 25 strings (Unicode), trailing blanks removed."""
        return [self.m.mem.super_text(page, r) for r in range(25)]

    def screen_dump(self, page=SCREEN0):
        """The screen framed, with the cursor cell shown as '_' when it is blank
        (a Super screen as Unicode, FORMAT shown)."""
        sup = self.is_super(page)
        rows = [r.ljust(80) for r in (self.super_screen(page) if sup else self.screen(page))]
        cur = self.cursor(page)
        if cur and rows[cur[1]][cur[0]] == " ":
            r = rows[cur[1]]
            rows[cur[1]] = r[:cur[0]] + "_" + r[cur[0] + 1:]
        shown = (" (displayed)" if page == self.displayed() else "") + \
            f" FORMAT {self.peek(page, FORMAT)[0]}" + (" Super" if sup else "")
        edge = "+" + "-" * 80 + "+"
        return "\n".join([f"page ${page:02X}{shown}", edge] + [f"|{r}|" for r in rows]
                         + [edge, f"cursor {cur}"])

    def screen_writes(self, page=SCREEN0):
        """CPU writes to a page as (offset, value, pc)."""
        return [(o, v, pc) for p, o, v, pc in self.special_writes if p == page]

    def peek(self, page, offset, n=1):
        return self.m.mem.page_bytes(page, offset, n)

    def blkmap(self):
        """The kernel's memory block map (D.BlkMap in page $00), 256 entries."""
        start = int.from_bytes(self.peek(0, 0x40, 2), "big")
        end = int.from_bytes(self.peek(0, 0x42, 2), "big")
        return self.peek(0, start, end - start)

    def read_image(self, dat, address, n):
        """n bytes at `address` of the address space described by the DAT image at `dat` (page $00)."""
        out = bytearray()
        for a in range(address, address + n):
            page = self.peek(0, dat + 2 * ((a & 0xFFFF) >> 13) + 1)[0]
            out += self.peek(page, a & 0x1FFF)
        return bytes(out)

    def modules(self):
        """The module directory: [(name, address, size, link count, DAT image pages)]."""
        start = int.from_bytes(self.peek(0, 0x44, 2), "big")
        end = int.from_bytes(self.peek(0, 0x58, 2), "big")
        out = []
        for e in range(start, end, 8):
            dat, _, mptr, links = (int.from_bytes(self.peek(0, e + i, 2), "big") for i in (0, 2, 4, 6))
            if not dat:
                continue
            head = self.read_image(dat, mptr, 9)
            nameoff = int.from_bytes(head[4:6], "big")
            name = bytearray()
            for c in self.read_image(dat, mptr + nameoff, 32):
                name.append(c & 0x7F)
                if c & 0x80:
                    break
            pages = self.peek(0, dat, 16)[1::2]
            out.append((name.decode("latin-1"), mptr, int.from_bytes(head[2:4], "big"), links,
                        " ".join(f"{p:02X}" for p in pages)))
        return out

    def report(self):
        cpu = self.m.cpu
        lines = [f"cycles {cpu.cycles} ({cpu.cycles / 6.25e6:.2f} s at 6.25 MHz), "
                 f"interrupts {cpu.interrupts}",
                 self.m.registers(),
                 f"D.BtBug: {''.join(self.btbug)!r}"]
        bm = self.blkmap()
        lines.append(f"block map: {len(bm)} pages, $F8-$FF = {' '.join(f'{b:02X}' for b in bm[0xF8:0x100])}"
                     if len(bm) == 256 else f"block map: {len(bm)} pages")
        lines.append(f"drive 0 accesses: {self.drive0 or 'none'}")
        for page in SCREENS:
            scr = self.screen_writes(page)
            high = [w for w in scr if w[0] >= 0x1000]
            lines.append(f"writes to page ${page:02X}: {len(scr)} (upper 4 KB: {len(high)}), "
                         f"FORMAT {self.peek(page, 0xFA2)[0]}")
        other = [w for w in self.special_writes if w[0] not in SCREENS]
        lines.append(f"writes to pages $FE-$FF: {len(other)}" + (f" (first {other[:5]})" if other else ""))
        lines.append(f"VIDEO_CTRL {self.m.mem.video_ctrl} (page ${self.displayed():02X} displayed)")
        return "\n".join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--kernel", default=KERNEL)
    ap.add_argument("--json", default=KERNEL_JSON)
    ap.add_argument("--disk", default=DISK, help="OS-9 disk for drive 1 (a copy is used)")
    ap.add_argument("--keep", metavar="DIR", help="keep the drive images in DIR")
    ap.add_argument("-n", "--count", type=int, default=200_000_000, help="instructions to the prompt")
    ap.add_argument("--cmd", action="append", default=[], help="command to type at the prompt")
    ap.add_argument("--w1", action="store_true", help="switch to /W1 (Ctrl-B) before the commands")
    ap.add_argument("--trace", type=int, default=64, metavar="N", help="trace entries printed on a stop")
    args = ap.parse_args(argv)

    if args.keep:
        Path(args.keep).mkdir(parents=True, exist_ok=True)
    s = OS9Session(args.kernel, kernel_info(args.json), args.disk, workdir=args.keep, trace=args.trace)
    t0 = time.monotonic()
    reason, steps, err = s.wait_prompt(args.count)
    ok = reason == "until" and not s.halted
    print(f"boot: {reason}" + (f" after {steps} instructions" if steps else "")
          + (f": {err}" if err else "") + f" ({time.monotonic() - t0:.1f} s)")
    if ok and args.w1:
        reason, _, err = s.wait_prompt(args.count, SCREEN1)
        ok = reason == "until" and s.switch_screen() == SCREEN1
        print(f"/W1: {reason}" + (f": {err}" if err else ""))
    for line in args.cmd if ok else ():
        t0 = time.monotonic()
        reason, _ = s.command(line)
        print(f"{line}: {reason} ({time.monotonic() - t0:.1f} s)")
        if reason != "until":
            ok = False
            break
    print(s.report())
    if not ok and s.m.cpu.trace:
        print("--- trace (oldest first) ---")
        print(s.m.cpu.format_trace())
    for page in SCREENS:
        print(f"--- screen {page - SCREEN0} ---")
        print(s.screen_dump(page))
    out = s.console().rstrip("\n")
    if out:
        print("--- ACIA (/T1) ---")
        print(out)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
