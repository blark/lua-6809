#!/usr/bin/env python3
"""
Wrap 6809 Lua bytecode (from luac6809) in an S19 record file for the
Anachron8, so MON09's L command can load it next to lua-a8.s19.

The VM expects a big-endian 16-bit length at A8_BYTECODE_SIZE_ADDR followed
by the bytecode (config/anachron8.py).

Usage: tools/bytecode_s19.py script.luac [-o script.s19]
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from config.anachron8 import A8_BYTECODE_SIZE_ADDR, A8_BYTECODE_MAX_SIZE

RECORD_BYTES = 16


def s1(address, data):
    """One S1 record: count, 16-bit address, data, ones'-complement checksum."""
    body = bytes([len(data) + 3, address >> 8, address & 0xFF]) + data
    return "S1" + body.hex().upper() + f"{~sum(body) & 0xFF:02X}"


def to_s19(bytecode):
    if len(bytecode) > A8_BYTECODE_MAX_SIZE:
        sys.exit(f"bytecode is {len(bytecode)} bytes, max {A8_BYTECODE_MAX_SIZE}")
    image = len(bytecode).to_bytes(2, "big") + bytecode
    lines = [s1(A8_BYTECODE_SIZE_ADDR + i, image[i:i + RECORD_BYTES])
             for i in range(0, len(image), RECORD_BYTES)]
    lines.append("S9030000FC")
    return "\n".join(lines) + "\n"


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("luac", type=Path)
    ap.add_argument("-o", "--output", type=Path)
    args = ap.parse_args()
    out = args.output or args.luac.with_suffix(".s19")
    out.write_text(to_s19(args.luac.read_bytes()))
    print(f"{out}: {args.luac.stat().st_size} bytes at ${A8_BYTECODE_SIZE_ADDR:04X}")


if __name__ == "__main__":
    main()
