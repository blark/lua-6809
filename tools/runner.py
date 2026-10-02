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
    STACK_TOP, Lua6809Config, Memory64K,
    A8_BYTECODE_SIZE_ADDR, A8_BYTECODE_DATA_ADDR, A8_BYTECODE_MAX_SIZE,
    A8_STACK_TOP, A8_STACK_BOTTOM, A8_ENTRY_CC,
)
from emu.anachron8 import ROM, BusError, new_memory

SWI_OPCODE = 0x3F
EXIT_OK, EXIT_ABORT = 0x1A8E, 0xDEAD  # D when lua6809.c exit/abort re-enter MON09


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


def run_test_a8(s19_path, luac_file):
    """Run on the Anachron8 memory model, entered the way MON09's G does."""
    if not s19_path:
        return None, "LUA6809_S19 not set"
    data, start_addr = parse_s19(s19_path)
    if not data:
        return None, "Failed to load S19"
    with open(luac_file, 'rb') as f:
        bytecode = f.read()
    if len(bytecode) > A8_BYTECODE_MAX_SIZE:
        return None, f"bytecode {len(bytecode)} bytes > {A8_BYTECODE_MAX_SIZE}"

    cfg, memory = new_memory()
    try:
        for addr, byte in sorted(data.items()):
            memory.poke(addr, [byte])
        memory.poke(A8_BYTECODE_SIZE_ADDR, [len(bytecode) >> 8, len(bytecode) & 0xFF])
        memory.poke(A8_BYTECODE_DATA_ADDR, bytecode)
    except BusError as e:
        return None, f"load: {e}"

    cpu = CPU(memory, cfg)
    memory.cpu = cpu
    cpu.program_counter.set(start_addr)
    cpu.system_stack_pointer.set(A8_STACK_TOP)
    cpu.set_cc(A8_ENTRY_CC)
    cpu.direct_page.set(0)

    # The VM ends by jumping through the reset vector back into MON09. Only
    # arriving at MON09's reset entry with the EXIT_OK marker in D counts as
    # finishing; any other way into the ROM, or any SWI, is a failure.
    reset_entry = memory.read_word(0xFFFE)
    try:
        for _ in range(50_000_000):
            pc = cpu.program_counter.value
            if pc >= ROM:
                if pc != reset_entry:
                    return None, f"jumped into the ROM at ${pc:04X}"
                break
            if memory.read_byte(pc) == SWI_OPCODE:
                return None, f"SWI at ${pc:04X} (MON09 would jump to its $DF60 vector)"
            if cpu.system_stack_pointer.value < A8_STACK_BOTTOM:
                return None, (f"stack overflow: S=${cpu.system_stack_pointer.value:04X} "
                              f"below ${A8_STACK_BOTTOM:04X} at PC ${pc:04X}")
            cpu.get_and_call_next_op()
        else:
            return None, "did not finish"
    except BusError as e:
        return None, f"bus error at PC ${cpu.last_op_address:04X}: {e}"
    except Exception as e:
        return None, f"CPU error: {e}"
    d = cpu.accu_d.value
    if d != EXIT_OK:
        why = "abort()" if d == EXIT_ABORT else f"reset without exit marker (D=${d:04X})"
        return None, f"{why}, last instruction at ${cpu.last_op_address:04X}"

    output = "".join(memory.console_output)
    # The VRAM screen must show the same text as the serial port.
    lines = output.rstrip('\n').split('\n')
    if all(len(l) <= 80 for l in lines):
        shown = lines[-25:]
        screen = memory.screen()[:len(shown)]
        if screen != [l.rstrip() for l in shown]:
            return None, f"screen {screen!r} differs from serial output {shown!r}"
    for line in lines:
        if line.startswith('=>'):
            return line[2:].strip(), None
    return None, output.strip() or "No result"


def run_test(luac_file):
    s19_path = os.environ.get('LUA6809_S19')
    if os.environ.get('LUA6809_TARGET') == 'anachron8':
        return run_test_a8(s19_path, luac_file)
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
