#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["MC6809"]
# ///
"""
NitrOS-9's `ls` (level2/anachron8/cmds/ls.asm) on the emulated Anachron8.

The expected output is built here from the OS-9 disk itself (a small RBF
reader: directory entries, file descriptors) and compared cell by cell with
the screens (character or code point, and foreground colour) and byte by
byte with /T1 (the ACIA) and with files ls wrote:
  - Classic /Term: the grid (column-major, dirs first, sorted, widths),
    CP437 markers, colours in the attribute bytes, -1, -a, -l, --tree
    (CP437 box glyphs), --color=never
  - Super /W1 (`vmode s </w1`, ls run with stdout on /w1): icons as code
    points in the planes, colours in the fg plane, -l, --tree (U+251C ...)
  - /T1 (SCF, not a screen): SGR colours, '/' after directories, no icons;
    the full output of the long modes (the screens only show 25 rows)
  - a file and a pipe: plain names, one per line, no escapes
  - a file path, a padlocked file, long names (fewer columns), errors,
    several paths with headers

    uv run tests/emu/test_os9_ls.py [--dump] [--speed]

--dump prints `ls /dd/cmds`, `ls -l /dd` and `ls --tree /dd` on both screen
kinds (text, then the foreground colours as hex digits). --speed measures
instructions and cycles of ls against dir. Needs the port's build outputs in
~/src/nitros9/recipes/anachron8/dw; skipped when they are missing.
"""

import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent.parent))

from emu.os9boot import DISK, KERNEL, KERNEL_JSON, SCREEN0, SCREEN1, SCREENS, OS9Session  # noqa: E402

BOOT_LIMIT = 10_000_000
STEP = 200_000_000
FG = 15                                   # the devices' default foreground
CLASSIC, SUPER, SERIAL, PLAIN = "classic", "super", "serial", "plain"

# ls's classes: colour (fg after SGR), Super icon, Classic marker
DIR, EXE, SHR, TXT, DEV = range(5)
LOCK = 0x80
COLOUR = {DIR: 12, EXE: 2, SHR: 3, TXT: None, DEV: 12}
SGR = {DIR: "\x1b[1;34m", EXE: "\x1b[32m", SHR: "\x1b[33m", TXT: "", DEV: "\x1b[1;34m"}
ICON = {DIR: 0xF114, EXE: 0xF016, SHR: 0xF084, TXT: 0xF0F6, DEV: 0xF108, LOCK: 0xE0A2}
MARK = {DIR: 0xAF, EXE: 0xF9, SHR: 0xAD, TXT: 0xF0, DEV: 0xFE, LOCK: 0xAA}
GLYPHS = {SUPER: ("├── ", "└── ", "│   ", "    "),
          CLASSIC: ("\u251c\u2500\u2500 ", "\u2514\u2500\u2500 ", "\u2502   ", "    "),   # CP437 C3 C0 B3 C4
          SERIAL: ("|-- ", "`-- ", "|   ", "    ")}
GLYPHS[PLAIN] = GLYPHS[SERIAL]
CP437 = bytes(range(256)).decode("cp437")

_session = None


def built():
    return all(p.exists() for p in (KERNEL, KERNEL_JSON, DISK))


def booted():
    global _session
    if _session is None:
        s = OS9Session(trace=64)
        for page in SCREENS:
            reason, _, err = s.wait_prompt(BOOT_LIMIT, page)
            assert err is None and reason == "until" and not s.halted, (reason, err, s.screen_dump(page))
        _session = s
        run(s, "vmode s </w1")
        assert s.is_super(SCREEN1)
    return _session


def run(s, line, page=SCREEN0):
    reason, rows = s.command(line, STEP, page)
    assert reason == "until", (line, reason, s.screen_dump(page))
    return rows


# --- the disk: a small RBF reader -----------------------------------------------------
class Disk:
    def __init__(self, path):
        self.img = Path(path).read_bytes()

    def sector(self, lsn):
        return self.img[lsn * 256:(lsn + 1) * 256]

    def fd(self, lsn):
        fd = self.sector(lsn)
        return {"att": fd[0], "own": int.from_bytes(fd[1:3], "big"), "dat": tuple(fd[3:8]),
                "size": int.from_bytes(fd[9:13], "big"), "fd": fd}

    def data(self, lsn):
        fd = self.fd(lsn)
        out = bytearray()
        for i in range(16, 256 - 4, 5):
            seg, n = int.from_bytes(fd["fd"][i:i + 3], "big"), int.from_bytes(fd["fd"][i + 3:i + 5], "big")
            if not n:
                break
            out += self.img[seg * 256:(seg + n) * 256]
        return bytes(out[:fd["size"]])

    def entries(self, lsn):
        """[(name, fd lsn)] of a directory, in disk order, deleted ones skipped."""
        raw, out = self.data(lsn), []
        for i in range(0, len(raw), 32):
            e = raw[i:i + 32]
            if not e[0]:
                continue
            name = bytearray()
            for c in e[:29]:
                name.append(c & 0x7F)
                if c & 0x80:
                    break
            out.append((name.decode("latin-1"), int.from_bytes(e[29:32], "big")))
        return out

    def lookup(self, path):
        """FD LSN of a path "/dd/a/b" (the device part ignored)."""
        lsn = int.from_bytes(self.img[8:11], "big")
        for part in [p for p in path.split("/")[2:] if p]:
            lsn = {n.lower(): l for n, l in self.entries(lsn)}[part.lower()]
        return lsn


def classify(att, own, user=0):
    cls = DIR if att & 0x80 else SHR if att & 0x40 else EXE if att & 0x24 else TXT
    readable = att & (0x01 if user in (0, own) else 0x08)
    return cls | (0 if readable else LOCK)


def listing(disk, path, all_=False, tree=False):
    """ls's entries of a directory, sorted: [dict(name, cls, att, own, dat, size, lsn)]."""
    out = []
    for name, lsn in disk.entries(disk.lookup(path)):
        if name.startswith("."):
            if not all_ or (tree and name in (".", "..")):
                continue
        fd = disk.fd(lsn)
        out.append(dict(name=name, lsn=lsn, cls=classify(fd["att"], fd["own"]), **{k: fd[k] for k in ("att", "own", "dat", "size")}))
    out.sort(key=lambda e: ((e["cls"] & 0x7F) != DIR, e["name"].upper()))
    return out


# --- expected output as cells (screens) or bytes (serial, plain) -----------------------
def human(n):
    if n < 1024:
        return f"{n:6d}B"
    for unit in "KMG":
        rem, n = n & 1023, n >> 10
        if n < 1024:
            break
    tenth = (rem * 10 + 512) >> 10
    if tenth == 10:
        n, tenth = n + 1, 0
        if n == 1024:
            n, unit = 1, "KMG"["KMG".index(unit) + 1]
    return f"{n:4d}.{tenth}{unit}"


def attrs(att):
    return "".join(c if att & (0x80 >> i) else "-" for i, c in enumerate("dsewrewr"))


def long_prefix(e):
    y, mo, d, h, mi = e["dat"]
    return f"{attrs(e['att'])} {e['own']:3d} {human(e['size'])} {1900 + y:4d}/{mo:02d}/{d:02d} {h:02d}:{mi:02d} "


class Out:
    """Lines of output as ls writes them, for one kind of standard output."""

    def __init__(self, kind, colour=True):
        self.kind, self.colour = kind, colour and kind != PLAIN
        self.lines, self.cur = [], []

    def text(self, s, fg=None):                     # plain characters, default colour
        for ch in s:
            self.cur.append((ch, fg))

    def entry(self, name, cls, root=False):
        base = cls & 0x7F
        fg = COLOUR[base] if self.colour else None
        sgr = SGR[base] if self.colour else ""
        if self.kind in (SUPER, CLASSIC):
            deco = (ICON if self.kind == SUPER else MARK)[LOCK if cls & LOCK else base]
            self.cur.append((chr(deco) if self.kind == SUPER else CP437[deco], fg, sgr))
            self.cur.append((" ", fg))
        elif sgr:
            self.cur.append(("", fg, sgr))
        self.text(name, fg)
        if sgr:
            self.cur.append(("", None, "\x1b[0m"))
        if self.kind in (SERIAL, PLAIN) and base == DIR and not root:
            self.text("/")

    def width(self, e):
        if self.kind in (SUPER, CLASSIC):
            return len(e["name"]) + 2
        return len(e["name"]) + ((e["cls"] & 0x7F) == DIR)

    def eol(self):
        self.lines.append(self.cur)
        self.cur = []

    # renderings
    def cells(self):
        """[[(char, fg)]] with the default colour filled in."""
        return [[(c[0], FG if c[1] is None else c[1]) for c in ln if c[0]] for ln in self.lines]

    def data(self, eol="\r"):
        """The bytes (as a latin-1 string; Super text as UTF-8)."""
        def one(c):
            s = (c[2] if len(c) > 2 else "") + c[0]
            return s.encode("utf-8").decode("latin-1") if self.kind == SUPER else s
        return "".join("".join(one(c) for c in ln) + eol for ln in self.lines)


def grid(out, ents, width=80):
    if not ents:
        return out
    colw = max(out.width(e) for e in ents) + 2
    cols = max(1, (width + 1) // colw)
    rows = -(-len(ents) // cols)
    for r in range(rows):
        i = r
        while True:
            out.entry(ents[i]["name"], ents[i]["cls"])
            nxt = i + rows
            if nxt >= len(ents):
                break
            out.text(" " * (colw - out.width(ents[i])))
            i = nxt
        out.eol()
    return out


def one(out, ents, long=False):
    for e in ents:
        if long:
            out.text(long_prefix(e))
        out.entry(e["name"], e["cls"])
        out.eol()
    return out


def tree(out, disk, path, all_=False, depth=0, last=()):
    if depth == 0:
        dev = path.startswith("/") and "/" not in path[1:]
        out.entry(path, DEV if dev else DIR, root=True)
        out.eol()
    ents = listing(disk, path, all_, tree=True)
    for i, e in enumerate(ents):
        g = GLYPHS[out.kind]
        out.text("".join(g[3] if lf else g[2] for lf in last) + g[1 if i == len(ents) - 1 else 0])
        out.entry(e["name"], e["cls"])
        out.eol()
        if (e["cls"] & 0x7F) == DIR and depth < 7:
            tree(out, disk, f"{path}/{e['name']}", all_, depth + 1, last + (i == len(ents) - 1,))
    return out


# --- reading the screens --------------------------------------------------------------
def cell(s, page, row, col):
    """(char, fg) on either format (Classic: the CP437 character)."""
    if s.is_super(page):
        code, fg, _ = s.super_cell(page, row, col)
        return chr(code or 0x20), fg
    return CP437[s.peek(page, row * 80 + col)[0]], s.peek(page, 0x7D0 + row * 80 + col)[0] & 15


def out_end(s, page):
    """The row after ls's output: the cursor's on /W1; on /Term the prompt is at the
    cursor with Shell+'s blank line above it."""
    return s.cursor(page)[1] - (1 if page == SCREEN0 else 0)


def screen_lines(s, page, n):
    """The n rows of ls's output that end at out_end(), as [(char, fg)] rows with
    trailing blanks in the default colour dropped."""
    end = out_end(s, page)
    rows = []
    for r in range(end - n, end):
        row = [cell(s, page, r, c) for c in range(80)]
        while row and row[-1] == (" ", FG):
            row.pop()
        rows.append(row)
    return rows


def check_screen(s, page, out):
    want = out.cells()
    end = out_end(s, page)
    top = 0
    if page == SCREEN0:                              # below the command line, if still shown
        cmd = [r for r in range(end) if re.match(r"\{\w+\|\d+\}", s.screen(page)[r])]
        top = cmd[-1] + 1 if cmd else 0
    n = min(len(want), end - top)
    got = screen_lines(s, page, n)
    want = want[len(want) - n:]
    bad = [(i, "".join(c for c, _ in g), "".join(c for c, _ in w)) for i, (g, w) in enumerate(zip(got, want)) if g != w]
    assert not bad, (bad[:3], [(g, w) for g, w in zip(got, want) if g != w][:1])
    return n


def clear(s, page):
    run(s, "display 0c" + (" >/w1" if page == SCREEN1 else ""))


def on_classic(s, line):
    """Run on /Term (Classic), cleared first."""
    clear(s, SCREEN0)
    return run(s, line)


def on_super(s, line):
    """Run from /Term with standard output on /W1 (Super), cleared first."""
    clear(s, SCREEN1)
    return run(s, line + " >/w1")


def on_serial(s, line):
    start = len(s.m.mem.console_output)
    run(s, line + " >/t1")
    return "".join(s.m.mem.console_output[start:]).replace("\r\n", "\r").replace("\n", "\r")


def disk(s):
    return Disk(s.disk)


# --- tests ----------------------------------------------------------------------------
def test_grid_classic():
    """`ls /dd/cmds` on /Term: column-major grid, markers, colours, all rows on screen."""
    if not built():
        print("skip  test_grid_classic: no NitrOS-9 build")
        return
    s = booted()
    ents = listing(disk(s), "/dd/cmds")
    assert len(ents) > 60
    out = grid(Out(CLASSIC), ents)
    assert len(out.lines) < 23                  # the whole grid is on screen
    on_classic(s, "ls /dd/cmds")
    assert check_screen(s, SCREEN0, out) == len(out.lines)
    # the column width: the longest name + marker + space + 2
    colw = max(len(e["name"]) for e in ents) + 4
    top = screen_lines(s, SCREEN0, len(out.lines))[0]
    assert top[colw] == (CP437[MARK[EXE]], COLOUR[EXE]), top[colw]


def test_dirs_first_sorted():
    """/dd: SYS and CMDS directories before the files, names case-insensitive."""
    if not built():
        return
    s = booted()
    run(s, "makdir /dd/aDir")
    run(s, "echo x >/dd/Bfile")
    run(s, "echo x >/dd/afile")
    ents = listing(disk(s), "/dd")
    names = [e["name"] for e in ents]
    assert names[:3] == ["aDir", "CMDS", "SYS"], names
    assert names[3:5] == ["afile", "Bfile"], names
    out = grid(Out(CLASSIC), ents)
    on_classic(s, "ls /dd")
    check_screen(s, SCREEN0, out)
    run(s, "del /dd/Bfile")
    run(s, "del /dd/afile")
    run(s, "attr /dd/aDir -d")
    run(s, "del /dd/aDir")
    assert "aDir" not in [e["name"] for e in listing(disk(s), "/dd", all_=True)]


def test_one_per_line_and_all():
    """-1 lists one per line; -a adds ., .. and dot files (hidden by default)."""
    if not built():
        return
    s = booted()
    run(s, "echo hidden >/dd/.profile")
    d = disk(s)
    plain = listing(d, "/dd")
    every = listing(d, "/dd", all_=True)
    assert ".profile" not in [e["name"] for e in plain]
    assert {".", "..", ".profile"} <= {e["name"] for e in every}
    on_classic(s, "ls -1 /dd")
    check_screen(s, SCREEN0, one(Out(CLASSIC), plain))
    on_classic(s, "ls -1a /dd")
    assert check_screen(s, SCREEN0, one(Out(CLASSIC), every)) == len(every)
    on_classic(s, "ls -a /dd")
    check_screen(s, SCREEN0, grid(Out(CLASSIC), every))
    run(s, "del /dd/.profile")


def test_long_classic():
    """-l on /Term: attributes, owner, size, date, marker, colour."""
    if not built():
        return
    s = booted()
    ents = listing(disk(s), "/dd")
    out = one(Out(CLASSIC), ents, long=True)
    on_classic(s, "ls -l /dd")
    assert check_screen(s, SCREEN0, out) == len(ents)
    cmds = ents.index([e for e in ents if e["name"] == "CMDS"][0])
    row = screen_lines(s, SCREEN0, len(ents))[cmds]
    assert row[len(long_prefix(ents[cmds]))] == (CP437[MARK[DIR]], COLOUR[DIR])


def test_long_all_of_cmds_serial():
    """ls -l /dd/cmds on /T1: every line byte for byte, SGR codes, '/' after dirs."""
    if not built():
        return
    s = booted()
    ents = listing(disk(s), "/dd/cmds")
    got = on_serial(s, "ls -l /dd/cmds")
    want = one(Out(SERIAL), ents, long=True).data()
    assert got == want, [(g, w) for g, w in zip(got.split("\r"), want.split("\r")) if g != w][:3]
    assert "\x1b[32m" in got and "\x1b[0m" in got


def test_size_format():
    """Sizes: bytes up to 1023, then K with one decimal (rounded)."""
    assert human(0) == "     0B" and human(1023) == "  1023B"
    assert human(1024) == "   1.0K" and human(1075) == "   1.0K" and human(1076) == "   1.1K"
    assert human(20889) == "  20.4K" and human(1048575) == "   1.0M" and human(5 << 20) == "   5.0M"
    if not built():
        return
    s = booted()
    run(s, "echo 0123456789 >/dd/ten")
    d = disk(s)
    ten = [e for e in listing(d, "/dd") if e["name"] == "ten"][0]
    assert ten["size"] < 1024
    got = on_serial(s, "ls -l /dd/ten")
    assert got == one(Out(SERIAL), [dict(ten, name="/dd/ten")], long=True).data(), got
    assert f"{ten['size']:6d}B " in got
    # the date is today's (the DriveWire clock), in yyyy/mm/dd hh:mm
    y, mo, d_, h, mi = ten["dat"]
    assert 1900 + y >= 2024 and f"{1900 + y}/{mo:02d}/{d_:02d} {h:02d}:{mi:02d}" in got
    run(s, "del /dd/ten")


def test_tree_classic():
    """--tree on /Term: CP437 box glyphs, dirs first on each level; the full tree on /T1."""
    if not built():
        return
    s = booted()
    d = disk(s)
    out = tree(Out(CLASSIC), d, "/dd")
    on_classic(s, "ls --tree /dd")
    check_screen(s, SCREEN0, out)
    rows = screen_lines(s, SCREEN0, 24)
    assert any(r[:4] == [("\u2514", FG), ("\u2500", FG), ("\u2500", FG), (" ", FG)] for r in rows)
    assert any(r[:1] == [("\u2502", FG)] for r in rows)
    full = on_serial(s, "ls --tree /dd")
    assert full == tree(Out(SERIAL), d, "/dd").data()
    assert full.startswith("\x1b[1;34m/dd\x1b[0m\r|-- \x1b[1;34mCMDS\x1b[0m/\r|   |-- ")


def test_tree_nested_and_depth():
    """Nested directories: the through lines and blanks of the levels above; depth 8."""
    if not built():
        return
    s = booted()
    path = "/dd/t"
    run(s, "makdir /dd/t")
    for name in "abcdefghij":                          # /dd/t/a/b/.../j: 10 levels
        path += "/" + name
        run(s, f"makdir {path}")
    run(s, "echo x >/dd/t/a/f1")
    run(s, "makdir /dd/t/z")
    d = disk(s)
    got = on_serial(s, "ls --tree /dd/t")
    want = tree(Out(SERIAL), d, "/dd/t").data()
    assert got == want, (got, want)
    lines = got.split("\r")
    assert any(ln.endswith("\x1b[1;34mh\x1b[0m/") for ln in lines)
    assert not any("\x1b[1;34mi\x1b[0m" in ln for ln in lines)   # level 9 is not shown
    on_classic(s, "ls --tree /dd/t")
    check_screen(s, SCREEN0, tree(Out(CLASSIC), d, "/dd/t"))
    on_super(s, "ls --tree /dd/t")
    check_screen(s, SCREEN1, tree(Out(SUPER), d, "/dd/t"))


def test_super_grid_icons():
    """`ls /dd/cmds >/w1` on the Super /W1: icons as code points, colours in the fg plane."""
    if not built():
        return
    s = booted()
    ents = listing(disk(s), "/dd/cmds")
    out = grid(Out(SUPER), ents)
    on_super(s, "ls /dd/cmds")
    assert check_screen(s, SCREEN1, out) == len(out.lines)
    assert s.super_cell(SCREEN1, 0, 0)[:2] == (ICON[EXE], COLOUR[EXE])     # asm
    on_super(s, "ls /dd")
    rows = screen_lines(s, SCREEN1, 2)
    assert rows[0][0] == (chr(ICON[DIR]), COLOUR[DIR])


def test_super_long_and_tree():
    """-l and --tree on the Super /W1: the padlock, U+251C U+2514 U+2502 U+2500."""
    if not built():
        return
    s = booted()
    d = disk(s)
    ents = listing(d, "/dd")
    on_super(s, "ls -l /dd")
    assert check_screen(s, SCREEN1, one(Out(SUPER), ents, long=True)) == len(ents)
    kern = ents.index([e for e in ents if e["name"] == "OS9Kernel"][0])
    row = screen_lines(s, SCREEN1, len(ents))[kern]
    assert row[len(long_prefix(ents[kern]))] == (chr(ICON[EXE]), COLOUR[EXE])
    on_super(s, "ls --tree /dd/sys")
    out = tree(Out(SUPER), d, "/dd/sys")
    assert check_screen(s, SCREEN1, out) == len(out.lines)
    assert s.super_cell(SCREEN1, 1, 0)[0] == 0x251C and s.super_cell(SCREEN1, 1, 1)[0] == 0x2500
    assert s.super_cell(SCREEN1, len(out.lines) - 1, 0)[0] == 0x2514
    assert s.super_cell(SCREEN1, 0, 0)[0] == ICON[DIR]          # the root: a directory


def test_long_names_fewer_columns():
    """Long names make wider columns: 29-character names give two columns."""
    if not built():
        return
    s = booted()
    run(s, "makdir /dd/w")
    names = ["abcdefghijklmnopqrstuvwxyz012", "x", "y", "zz"]
    for n in names:
        run(s, f"echo . >/dd/w/{n}")
    ents = listing(disk(s), "/dd/w")
    out = grid(Out(CLASSIC), ents)
    assert len(out.lines) == 2                         # colw 33: two columns
    on_classic(s, "ls /dd/w")
    check_screen(s, SCREEN0, out)
    got = on_serial(s, "ls /dd/w")                    # serial: colw 31 (no marker), still two
    assert got == grid(Out(SERIAL), ents).data()
    run(s, "echo . >/dd/w/abcdefghijklmnopqrstuvwxyz01")
    ents = listing(disk(s), "/dd/w")
    assert got != grid(Out(SERIAL), ents).data()
    assert on_serial(s, "ls /dd/w") == grid(Out(SERIAL), ents).data()


def test_serial_grid():
    """/T1: SCF but no screen: colours, '/' after directories, width 80."""
    if not built():
        return
    s = booted()
    d = disk(s)
    got = on_serial(s, "ls /dd")
    assert got == grid(Out(SERIAL), listing(d, "/dd")).data(), got
    assert "\x1b[1;34mCMDS\x1b[0m/" in got
    got = on_serial(s, "ls /dd/cmds")
    assert got == grid(Out(SERIAL), listing(d, "/dd/cmds")).data()


def test_file_and_pipe_plain():
    """To a file and through a pipe: one per line, no escapes, '/' after directories."""
    if not built():
        return
    s = booted()
    run(s, "ls /dd >/dd/lsout")
    d = disk(s)
    ents = listing(d, "/dd")
    data = d.data(d.lookup("/dd/lsout")).decode("latin-1")
    want = one(Out(PLAIN), [e for e in ents]).data()
    # lsout itself was created before ls read the directory
    assert data == want, (data, want)
    assert "\x1b" not in data and "CMDS/\r" in data
    run(s, "del /dd/lsout")                           # > does not replace a file
    run(s, "ls -l /dd/cmds >/dd/lsout")
    d = disk(s)
    data = d.data(d.lookup("/dd/lsout")).decode("latin-1")
    assert data == one(Out(PLAIN), listing(d, "/dd/cmds"), long=True).data()
    run(s, "del /dd/lsout")
    run(s, "ls --color=always /dd >/dd/lsout")
    d = disk(s)
    data = d.data(d.lookup("/dd/lsout")).decode("latin-1")
    assert "\x1b[1;34mCMDS\x1b[0m/\r" in data
    # a pipe: list prints what ls wrote, plain
    on_classic(s, "ls /dd/sys ! tee")
    out = one(Out(PLAIN), listing(d, "/dd/sys"))
    got = ["".join(c for c, _ in r) for r in screen_lines(s, SCREEN0, len(out.lines))]
    assert got == ["".join(c for c, _ in ln) for ln in out.cells()], got
    run(s, "del /dd/lsout")


def test_color_never():
    """--color=never on a screen: markers stay, colours go."""
    if not built():
        return
    s = booted()
    ents = listing(disk(s), "/dd")
    on_classic(s, "ls --color=never /dd")
    check_screen(s, SCREEN0, grid(Out(CLASSIC, colour=False), ents))


def test_file_path_and_errors():
    """A file path lists itself; a missing path is an error; several paths get headers."""
    if not built():
        return
    s = booted()
    d = disk(s)
    st = [e for e in listing(d, "/dd") if e["name"] == "startup"][0]
    on_classic(s, "ls -l /dd/startup")
    check_screen(s, SCREEN0, one(Out(CLASSIC), [dict(st, name="/dd/startup")], long=True))
    rows = on_classic(s, "ls /dd/nothere")
    assert "ERROR #216" in "\n".join(rows)
    rows = on_classic(s, "ls /dd/nothere /dd/sys /dd/startup")
    text = "\n".join(rows)
    assert "ls: /dd/nothere: ERROR #216" in text and "/dd/sys:" in text, text
    got = on_serial(s, "ls /dd/sys /dd/cmds")
    want = "/dd/sys:\r" + grid(Out(SERIAL), listing(d, "/dd/sys")).data() + "\r/dd/cmds:\r" \
        + grid(Out(SERIAL), listing(d, "/dd/cmds")).data()
    assert got == want
    rows = on_classic(s, "ls -z")
    assert any(r.startswith("Use: ls") for r in rows) and "ERROR #187" in "\n".join(rows)


def test_unreadable_padlock():
    """A file without r for this user: padlock on Super, the not sign on Classic."""
    if not built():
        return
    s = booted()
    run(s, "echo secret >/dd/locked")
    run(s, "attr /dd/locked -r -pr")
    d = disk(s)
    ents = listing(d, "/dd")
    lk = [e for e in ents if e["name"] == "locked"][0]
    assert lk["cls"] == TXT | LOCK
    on_classic(s, "ls -1 /dd")
    check_screen(s, SCREEN0, one(Out(CLASSIC), ents))
    on_super(s, "ls -1 /dd")
    rows = screen_lines(s, SCREEN1, len(ents))
    assert rows[ents.index(lk)][0] == (chr(ICON[LOCK]), FG)
    run(s, "del /dd/locked")


def test_help():
    """`help ls` finds its entry in /dd/sys/helpmsg."""
    if not built():
        return
    s = booted()
    rows = on_classic(s, "help ls")
    assert any(r.startswith("Syntax: Ls") for r in rows), rows


def test_existing_screens_intact():
    """/Term stays Classic, /W1 Super, the prompt comes back on both."""
    if not built():
        return
    s = booted()
    assert not s.is_super(SCREEN0) and s.is_super(SCREEN1)
    assert s.at_prompt(SCREEN0)
    assert not [w for w in s.special_writes if w[0] not in SCREENS]


# --- dumps and speed ------------------------------------------------------------------
def dump_page(s, page, title):
    sup = s.is_super(page)
    print(f"=== {title} ({'Super /W1' if sup else 'Classic /Term'}) ===")
    rows = [[cell(s, page, r, c) for c in range(80)] for r in range(25)]
    last = max((i for i, r in enumerate(rows) if any(ch != " " for ch, _ in r)), default=0)
    for r in rows[:last + 1]:
        text = "".join(ch if ch.isprintable() or 0xE000 <= ord(ch) < 0xF900 else " " for ch, _ in r).rstrip()
        cols = "".join(" " if ch == " " and fg == FG else f"{fg:x}" if fg < 16 else "*" for ch, fg in r)
        print(f"|{text}")
        if cols.strip():
            print(f"#{cols.rstrip()}")


def dumps():
    s = booted()
    for cmd in ("ls /dd/cmds", "ls -l /dd", "ls --tree /dd"):
        on_classic(s, cmd)
        dump_page(s, SCREEN0, cmd)
        on_super(s, cmd)
        dump_page(s, SCREEN1, cmd + " >/w1")
    print("(# rows: foreground colour of each non-blank cell, hex; blank = the default 15.\n"
          " Super icons are Nerd Font code points: U+F114 folder, U+F016 file, U+F0F6 document,\n"
          " U+F084 key, U+F108 computer, U+E0A2 padlock)")


def measure(s, line):
    """(instructions, cycles) from Enter to the next prompt on /Term."""
    clear(s, SCREEN0)
    reason, _, _ = s.type(line, STEP)
    reason, _, _ = s.wait_screen(lambda: (s.cursor_line(SCREEN0)[0] or "").endswith(line), STEP)
    c0, steps = s.m.cpu.cycles, 0
    for step in (lambda: s.type("\r", STEP), lambda: s.wait_prompt(STEP, SCREEN0)):
        reason, n, err = step()
        assert reason == "until" and not err, (line, reason, err)
        steps += n
    return steps, s.m.cpu.cycles - c0


def speed():
    s = booted()
    for line in ("ls /dd/nothere", "ls /dd/cmds", "ls -l /dd/cmds", "ls -1 /dd/cmds", "ls --tree /dd",
                 "dir /dd/cmds", "dir -e /dd/cmds"):
        measure(s, line)                               # warm (RBF, shell)
        n, c = measure(s, line)
        print(f"{line:18s} {n:>10,d} instructions {c:>12,d} cycles {c / 6.25e6:6.2f} s at 6.25 MHz")


def main():
    if "--dump" in sys.argv:
        dumps()
        return 0
    if "--speed" in sys.argv:
        speed()
        return 0
    tests = [(n, f) for n, f in globals().items() if n.startswith("test_") and callable(f)]
    failed = 0
    for n, f in tests:
        try:
            f()
            print(f"ok    {n}")
        except AssertionError as e:
            failed += 1
            line = e.__traceback__.tb_next.tb_lineno if e.__traceback__.tb_next else "?"
            print(f"FAIL  {n} (line {line}): {e}")
    print(f"{len(tests) - failed}/{len(tests)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
