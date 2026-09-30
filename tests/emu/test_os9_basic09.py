#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["MC6809"]
# ///
"""
BASIC09 on NitrOS-9 (the anachron8 port) on the emulated Anachron8.

BASIC09 is on the default disk (the dw recipe; Picard's decision
2026-09-30). These tests check the disk, then boot it:
  - the disk: basic09, runb, inkey, syscall in CMDS, the samples in
    /DD/BASIC09 and their help entries
  - the modules: BASIC09 and RunB edition 22, RunB byte-identical to the
    nitros9-languages build other recipes pin (sha256)
  - a procedure file typed in with `build`, loaded and run non-interactively
    (`basic09 file </dd/bye`): integer and real arithmetic, strings, a FOR
    loop, file I/O on /dd (the file checked in the disk image too)
  - PACK from BASIC09 (commands from a file), then the packed I-code module
    run by `runb` and by name from the shell
  - syscall (F$ID) and inkey (no key, then a typed key) from a procedure
  - the unittest sample (nitros9-languages' interpreter self-test): 0 failed

    uv run tests/emu/test_os9_basic09.py [--transcript]

--transcript prints an interactive BASIC09 session (banner, a direct
command, a procedure run, bye). Needs the dw recipe built in
~/src/nitros9/recipes/anachron8; skipped when missing.
"""

import hashlib
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent.parent))
sys.path.insert(0, str(HERE))

from emu.os9boot import DISK, KERNEL, KERNEL_JSON, RECIPE, SCREEN0, OS9Session  # noqa: E402
from test_os9_ls import Disk  # noqa: E402

B09_KERNEL, B09_JSON, B09_DISK = KERNEL, KERNEL_JSON, DISK
BOOT_LIMIT = 10_000_000
STEP = 200_000_000
KEY_STEP = 20_000_000

B09_CMDS = ("basic09", "runb", "inkey", "syscall")
SAMPLES = ("computepi", "dumpReal", "fibonacci", "hammurabi", "hello", "makeChange", "makeChange3",
           "mpadd", "primes", "randomReal", "sieve", "startrek", "unittest", "wumpus")
# runb_6809 from nitros9-languages' modular Microware build, as pinned by
# recipes/wildbits/wildbits.mak (RUNB_SHA256)
RUNB_SHA256 = "20ff5a997ec0e55f6aec1e37d92be49062d6c35a66e4b5404d9e680fc0783bbb"

SUMS = """PROCEDURE sums
DIM i,s:INTEGER; x:REAL; n:STRING[20]; l:STRING[40]; p:BYTE
s:=0
FOR i:=1 TO 10
s:=s+i
NEXT i
PRINT "SUM="; s; " PROD="; 6*7; " DIV="; 17/4; " MOD="; MOD(17,4)
x:=1.5*3
PRINT "REAL="; x; " SQR="; SQR(16)
n:="Ana"+"chron8"
PRINT "STR="; n; " LEN="; LEN(n); " LEFT="; LEFT$(n,3); " MID="; MID$(n,4,5)
CREATE #p,"/dd/b09out":WRITE
WRITE #p,"hello file"
PRINT #p,"line 2"
CLOSE #p
OPEN #p,"/dd/b09out":READ
READ #p,l
PRINT "READ="; l
CLOSE #p
END"""
SUMS_OUT = ["SUM=55 PROD=42 DIV=4 MOD=1",
            "REAL=4.5 SQR=4.",                    # SQR is REAL: "4."
            "STR=Anachron8 LEN=9 LEFT=Ana MID=chron",
            "READ=hello file"]

KEYS = """PROCEDURE keys
TYPE regs=cc,a,b,dp:BYTE; x,y,u:INTEGER
DIM r:regs; code:BYTE; k:STRING[1]
code:=$0C
RUN syscall(code,r)
PRINT "PID="; r.a; " CC="; LAND(r.cc,1)
RUN inkey(k)
PRINT "NOKEY=["; k; "]"
PRINT "PRESS"
REPEAT
RUN inkey(k)
UNTIL k<>""
PRINT "KEY=["; k; "]"
END"""

_session = None


def built():
    return all(p.exists() for p in (KERNEL, KERNEL_JSON, DISK))


SKIPPED = []


def skip(name, why="no NitrOS-9 build (recipes/anachron8/dw)"):
    SKIPPED.append(name)
    print(f"skip  {name}: {why}")


def booted():
    global _session
    if _session is None:
        s = OS9Session(kernel=B09_KERNEL, info=None, disk=B09_DISK, trace=64)
        reason, _, err = s.wait_prompt(BOOT_LIMIT)
        assert err is None and reason == "until" and not s.halted, (reason, err, s.screen_dump())
        build(s, "/dd/bye", ["bye"])
        _session = s
    return _session


def run(s, line, count=STEP):
    reason, rows = s.command(line, count)
    assert reason == "until", (line, reason, s.screen_dump())
    return rows


def typeline(s, line):
    """Type a line, wait for its echo, then Enter (the console ring drops keys typed ahead)."""
    s.type(line, KEY_STEP)
    reason, _, _ = s.wait_screen(lambda: (s.cursor_line(SCREEN0)[0] or "").endswith(line), KEY_STEP)
    assert reason == "until", (line, s.screen_dump())
    s.type("\r", KEY_STEP)


def build(s, path, lines):
    """Create a text file with `build`, a line at a time (it prompts "? ")."""
    def prompt():
        reason, _, _ = s.wait_screen(lambda: s.cursor_line(SCREEN0)[0] == "?", KEY_STEP)
        assert reason == "until", (path, s.screen_dump())
    typeline(s, f"build {path}")
    for line in lines:
        prompt()
        typeline(s, line)
    prompt()
    s.type("\r", KEY_STEP)
    reason, _, _ = s.wait_prompt(KEY_STEP)
    assert reason == "until", (path, s.screen_dump())


def output(rows, command):
    """The non-blank rows after the last `command` line, up to the next prompt."""
    start = max(i for i, r in enumerate(rows) if r.endswith(command))
    out = []
    for r in rows[start + 1:]:
        if r.startswith("{Term|"):
            break
        if r:
            out.append(r)
    return out


def file_data(s, path):
    d = Disk(s.disk)
    return d.data(d.lookup(path))


def names(disk, path):
    return {n for n, _ in disk.entries(disk.lookup(path))} - {".", ".."}


def helpmsg(disk):
    return disk.data(disk.lookup("/dd/SYS/helpmsg"))


# --- the disks -------------------------------------------------------------------------
def test_on_default_disk():
    if not built():
        return skip("test_on_default_disk")
    d = Disk(DISK)
    assert set(B09_CMDS) <= names(d, "/dd/CMDS"), names(d, "/dd/CMDS")
    assert names(d, "/dd/BASIC09") == set(SAMPLES)
    help_text = helpmsg(d)
    for topic in (b"@BASIC09", b"@RUNB", b"@INKEY"):
        assert topic in help_text, topic


def test_modules():
    if not built():
        return skip("test_modules")
    d = Disk(B09_DISK)

    def module(name):
        m = d.data(d.lookup(f"/dd/CMDS/{name}"))
        assert m[:2] == b"\x87\xcd", name
        size = int.from_bytes(m[2:4], "big")
        off = int.from_bytes(m[4:6], "big")
        mname = bytearray()
        for c in m[off:off + 32]:
            mname.append(c & 0x7F)
            if c & 0x80:
                break
        return m, size, mname.decode(), m[6], m[off + len(mname)]
    m, size, name, tylg, edition = module("basic09")
    assert (name, tylg, edition, size == len(m)) == ("Basic09", 0x11, 22, True), (name, tylg, edition)
    m, size, name, tylg, edition = module("runb")
    assert (name, tylg, edition) == ("RunB", 0x11, 22), (name, tylg, edition)
    assert hashlib.sha256(m).hexdigest() == RUNB_SHA256
    for cmd, want in (("inkey", "Inkey"), ("syscall", "SysCall")):
        _, _, name, tylg, _ = module(cmd)
        assert (name, tylg) == (want, 0x21), (cmd, name, tylg)   # Sbrtn+Objct


# --- BASIC09 running ---------------------------------------------------------------------
def test_procedure_file():
    if not built():
        return skip("test_procedure_file")
    s = booted()
    build(s, "/dd/sums.b09", SUMS.splitlines())
    rows = run(s, "basic09 /dd/sums.b09 </dd/bye")
    out = output(rows, "basic09 /dd/sums.b09 </dd/bye")
    assert out[:1] == ["sums"], out                      # the loaded procedure's name
    assert out[1:5] == SUMS_OUT, out
    assert out[5:] == ["Ready", "B:"], out               # then `bye` from /dd/bye
    assert file_data(s, "/dd/b09out") == b"hello file\rline 2\r"


def test_pack_and_runb():
    if not built():
        return skip("test_pack_and_runb")
    s = booted()
    if "sums.b09" not in names(Disk(s.disk), "/dd"):
        build(s, "/dd/sums.b09", SUMS.splitlines())
    build(s, "/dd/pk", ["load /dd/sums.b09", "pack sums", "bye"])
    rows = run(s, "basic09 </dd/pk")
    assert "Basic09" in rows and "What?" not in rows, rows
    d = Disk(s.disk)
    m = d.data(d.lookup("/dd/CMDS/sums"))
    assert m[:2] == b"\x87\xcd" and m[6] == 0x22, m[:9].hex()   # Sbrtn+ICode: a packed procedure
    for line in ("runb sums", "sums"):                          # RunB by hand, then by the shell
        run(s, "del /dd/b09out")
        out = output(run(s, line), line)
        assert out == SUMS_OUT, (line, out)
        assert file_data(s, "/dd/b09out") == b"hello file\rline 2\r"


def test_syscall_and_inkey():
    if not built():
        return skip("test_syscall_and_inkey")
    s = booted()
    build(s, "/dd/keys.b09", KEYS.splitlines())
    typeline(s, "basic09 /dd/keys.b09")
    reason, _, _ = s.wait_screen(lambda: "PRESS" in s.screen(), STEP)
    assert reason == "until", s.screen_dump()
    s.type("Z", KEY_STEP)
    reason, _, _ = s.wait_screen(lambda: s.cursor_line(SCREEN0)[0] == "B:", STEP)
    assert reason == "until", s.screen_dump()
    rows = s.screen()
    out = output(rows, "basic09 /dd/keys.b09")
    pid = next(r for r in out if r.startswith("PID="))
    num, cc = pid[4:].split(" CC=")
    assert 2 < int(num) < 64 and cc == "0", pid           # F$ID: our process id, no error
    assert "NOKEY=[]" in out and "ZKEY=[Z]" in out, out     # (inkey reads with echo on)
    typeline(s, "bye")
    assert s.wait_prompt(STEP)[0] == "until", s.screen_dump()


def test_unittest_sample():
    """Slow, opt-in (--slow): the 1628-line unittest sample runs far longer than the other
    tests in this emulator (~0.7 M instructions/s) and the process kept growing; it was
    never seen to finish, so there is no pass/fail result for it yet."""
    if not built():
        return skip("test_unittest_sample")
    if "--slow" not in sys.argv:
        return skip("test_unittest_sample", "slow, run with --slow")
    s = booted()
    rows = run(s, "basic09 /dd/basic09/unittest #24k </dd/bye", 2 * STEP)
    passed = next((r for r in rows if r.startswith("Passed: ")), None)
    failed = next((r for r in rows if r.startswith("Failed: ")), None)
    assert passed and failed == "Failed: 0" and int(passed.split()[1]) > 100, s.screen_dump()
    assert not any(r.startswith("FAIL") for r in rows), rows


def transcript():
    s = OS9Session(kernel=B09_KERNEL, disk=B09_DISK, trace=64)
    s.wait_prompt(BOOT_LIMIT)
    build(s, "/dd/fib.b09", ["PROCEDURE fib", "DIM a,b,t,i:INTEGER", "a:=0 \\b:=1",
                              "FOR i:=1 TO 10", "PRINT a; \" \";", "t:=a+b \\a:=b \\b:=t",
                              "NEXT i", "PRINT", "END"])
    typeline(s, "basic09")
    for line in ("PRINT 6*7, \"Anachron8\", SQR(2)", "load /dd/fib.b09", "run fib", "dir"):
        s.wait_screen(lambda: s.cursor_line(SCREEN0)[0] == "B:", STEP)
        typeline(s, line)
    s.wait_screen(lambda: s.cursor_line(SCREEN0)[0] == "B:", STEP)
    typeline(s, "bye")
    s.wait_prompt(STEP)
    print("\n".join(r for r in s.screen() if r))


def main():
    if "--transcript" in sys.argv:
        transcript()
        return 0
    tests = [(n, f) for n, f in globals().items() if n.startswith("test_") and callable(f)]
    failed = 0
    for n, f in tests:
        try:
            f()
            if n not in SKIPPED:
                print(f"ok    {n}", flush=True)
        except AssertionError as e:
            failed += 1
            line = e.__traceback__.tb_next.tb_lineno if e.__traceback__.tb_next else "?"
            print(f"FAIL  {n} (line {line}): {e}", flush=True)
    ran = len(tests) - len(SKIPPED)
    print(f"{ran - failed}/{ran} passed" + (f", {len(SKIPPED)} skipped" if SKIPPED else ""))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
