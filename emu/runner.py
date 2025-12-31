#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.9"
# dependencies = ["MC6809"]
# ///
"""
Minimal test runner for Lua 6809 VM.
Outputs only the result value or error message.

Usage: uv run test_runner.py <bytecode.luac>
"""

import os
import sys
from pathlib import Path

# Add project root to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from MC6809.components.cpu6809 import CPU

from config.memory_layout import (
    BYTECODE_SIZE_ADDR, BYTECODE_DATA_ADDR,
    STACK_TOP, Lua6809Config, Memory64K
)


def parse_s19(filename):
    data = {}
    start_addr = None
    with open(filename) as f:
        for line in f:
            line = line.strip()
            if not line or line[0] != 'S':
                continue
            rec_type = line[1]
            if rec_type == '1':
                byte_count = int(line[2:4], 16)
                address = int(line[4:8], 16)
                data_bytes = bytes.fromhex(line[8:8 + (byte_count - 3) * 2])
                for i, b in enumerate(data_bytes):
                    data[address + i] = b
            elif rec_type == '9':
                start_addr = int(line[4:8], 16)
    return data, start_addr


def run_test(luac_file):
    s19_path = os.environ.get('LUA6809_S19')
    if not s19_path:
        return None, "LUA6809_S19 not set"

    # Load VM
    data, start_addr = parse_s19(s19_path)
    if not data:
        return None, "Failed to load S19"

    # Load bytecode
    with open(luac_file, 'rb') as f:
        bytecode = f.read()

    # Setup memory
    cfg = Lua6809Config({"verbosity": None, "trace": None})
    memory = Memory64K(cfg)

    # Load VM code
    for addr, byte in data.items():
        memory._mem[addr] = byte

    # Load bytecode
    memory._mem[BYTECODE_SIZE_ADDR] = (len(bytecode) >> 8) & 0xFF
    memory._mem[BYTECODE_SIZE_ADDR + 1] = len(bytecode) & 0xFF
    for i, b in enumerate(bytecode):
        memory._mem[BYTECODE_DATA_ADDR + i] = b

    # Setup CPU
    cpu = CPU(memory, cfg)
    cpu.program_counter.set(start_addr)
    cpu.user_stack_pointer.set(STACK_TOP)
    cpu.system_stack_pointer.set(STACK_TOP)

    # Run
    max_instructions = 50_000_000
    prev_pc = -1
    for i in range(max_instructions):
        pc = cpu.program_counter.value
        if pc == prev_pc:
            break
        prev_pc = pc
        try:
            cpu.get_and_call_next_op()
        except Exception as e:
            return None, f"CPU error: {e}"

    # Get output
    output = "".join(memory.console_output)

    # Find result
    for line in output.split('\n'):
        if line.startswith('=>'):
            return line[2:].strip(), None

    if 'Run err' in output or 'Load err' in output:
        return None, output.strip()

    return None, "No result"


def main():
    if len(sys.argv) < 2:
        print("Usage: test_runner.py <bytecode.luac>", file=sys.stderr)
        sys.exit(1)

    result, error = run_test(sys.argv[1])
    if error:
        print(f"ERROR: {error}", file=sys.stderr)
        sys.exit(1)
    else:
        print(result)


if __name__ == '__main__':
    main()
