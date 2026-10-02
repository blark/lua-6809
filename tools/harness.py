#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.9"
# dependencies = ["MC6809"]
# ///
"""
Test harness for running Lua VM on MC6809 emulator

Run with: uv run test_lua_6809.py [bytecode.luac]
Or make executable: chmod +x test_lua_6809.py && ./test_lua_6809.py
"""

import sys
from pathlib import Path

# Add project root to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from MC6809.components.cpu6809 import CPU

from config.memory_layout import (
    BYTECODE_SIZE_ADDR, BYTECODE_DATA_ADDR,
    HEAP_START, HEAP_END,
    OUTPUT_ADDR, STACK_TOP, STACK_BOTTOM,
    Lua6809Config, Memory64K
)


def parse_s19(filename):
    """Parse Motorola S-record file, return (data_dict, start_addr)"""
    data = {}
    start_addr = None

    with open(filename, 'r') as f:
        for line in f:
            line = line.strip()
            if not line:
                continue

            record_type = line[0:2]

            if record_type == 'S1':
                # S1: 16-bit address data record
                byte_count = int(line[2:4], 16)
                addr = int(line[4:8], 16)
                # data is after address (2 bytes) and before checksum (1 byte)
                data_hex = line[8:8 + (byte_count - 3) * 2]
                for i in range(0, len(data_hex), 2):
                    data[addr] = int(data_hex[i:i+2], 16)
                    addr += 1

            elif record_type == 'S9':
                # S9: 16-bit start address
                start_addr = int(line[4:8], 16)

    return data, start_addr


def load_bytecode(source):
    """Load bytecode from file or stdin (if source is '-')"""
    if source == '-':
        import io
        return sys.stdin.buffer.read()
    with open(source, 'rb') as f:
        return f.read()


def main():
    print()
    print("╔══════════════════════════════════════════════════════════════╗")
    print("║           Lua 5.1.5 on Motorola MC6809 Emulator              ║")
    print("╚══════════════════════════════════════════════════════════════╝")
    print()

    # Check for bytecode argument (file path or '-' for stdin)
    bytecode = None
    if len(sys.argv) > 1:
        bytecode_src = sys.argv[1]
        bytecode = load_bytecode(bytecode_src)
        src_name = "stdin" if bytecode_src == '-' else bytecode_src
        print(f"Bytecode: {src_name} ({len(bytecode)} bytes)")

    # Load binary from LUA6809_S19 env var or default
    import os
    s19_path = os.environ.get('LUA6809_S19', '/tmp/lua.s19')
    data, start_addr = parse_s19(s19_path)
    code_size = len(data)
    min_addr = min(data.keys())
    max_addr = max(data.keys())

    print(f"Loading {code_size:,} bytes (${min_addr:04X}-${max_addr:04X})...")

    # Memory layout diagnostic
    print()
    print("Memory Layout:")
    print(f"  Code:      ${min_addr:04X}-${max_addr:04X} ({code_size:,} bytes)")
    print(f"  Bytecode:  ${BYTECODE_SIZE_ADDR:04X}-${BYTECODE_SIZE_ADDR + 256:04X} (256 bytes reserved)")
    print(f"  Heap:      ${HEAP_START:04X}-${HEAP_END:04X} ({HEAP_END - HEAP_START:,} bytes)")
    print(f"  Stack:     ${HEAP_END:04X}-${STACK_TOP:04X} ({STACK_TOP - HEAP_END:,} bytes)")
    print()

    # Check for memory overlap
    if max_addr >= BYTECODE_SIZE_ADDR:
        print(f"ERROR: Code (ends at ${max_addr:04X}) overlaps bytecode area (${BYTECODE_SIZE_ADDR:04X})!")
        print(f"       Remove debug code or increase BYTECODE_SIZE_ADDR")
        return

    if BYTECODE_SIZE_ADDR + 256 > HEAP_START:
        print(f"WARNING: Bytecode area may overlap heap!")

    gap = BYTECODE_SIZE_ADDR - max_addr
    print(f"Gap between code and bytecode: {gap:,} bytes (${max_addr:04X} to ${BYTECODE_SIZE_ADDR:04X})")

    # Set up emulator
    cfg = Lua6809Config({"verbosity": None, "trace": None})
    memory = Memory64K(cfg)
    cpu = CPU(memory, cfg)
    memory.cpu = cpu  # Enable cycle counting

    # Load binary into memory
    for addr, byte in data.items():
        memory._mem[addr] = byte

    # Load bytecode into memory if provided
    if bytecode:
        bc_size = len(bytecode)
        # Write size as big-endian 16-bit
        memory._mem[BYTECODE_SIZE_ADDR] = (bc_size >> 8) & 0xFF
        memory._mem[BYTECODE_SIZE_ADDR + 1] = bc_size & 0xFF
        # Write bytecode data
        for i, b in enumerate(bytecode):
            memory._mem[BYTECODE_DATA_ADDR + i] = b
        print(f"Loaded bytecode at ${BYTECODE_SIZE_ADDR:04X}")
        # Debug: show first 12 bytes of bytecode (header)
        hdr = ' '.join(f'{memory._mem[BYTECODE_DATA_ADDR + i]:02X}' for i in range(12))
        print(f"Header: {hdr}")

    # Set up stack (above bytecode/heap area)
    cpu.system_stack_pointer.set(0xFFF0)
    cpu.user_stack_pointer.set(0xFEF0)
    cpu.program_counter.set(start_addr)

    # Debug: verify bytecode is in memory
    print(f"Mem at $D000: {memory._mem[BYTECODE_SIZE_ADDR]:02X} {memory._mem[BYTECODE_SIZE_ADDR+1]:02X}")
    print(f"Starting execution at ${start_addr:04X}...")
    print()

    # Run until halt
    last_pc = None
    loop_count = 0
    output_lines = []

    try:
        for i in range(5000000):
            pc = cpu.program_counter.value

            # Detect infinite loop (program end)
            if pc == last_pc:
                loop_count += 1
                if loop_count > 10:
                    break
            else:
                loop_count = 0
            last_pc = pc

            cpu.get_and_call_next_op()

            # Collect output lines
            if memory.console_output and memory.console_output[-1] == '\n':
                line = "".join(memory.console_output).rstrip('\n')
                if line:
                    output_lines.append(line)
                memory.console_output = []

    except Exception as e:
        print(f"Error: {e}")
        return

    # Collect any remaining output
    remaining = "".join(memory.console_output)
    if remaining:
        for line in remaining.split('\n'):
            if line.strip():
                output_lines.append(line)

    # Display results
    print("Output:")
    print("-" * 40)
    if output_lines:
        for line in output_lines:
            print(f"  {line}")
    else:
        print("  (no output)")
    print("-" * 40)
    print()
    print(f"Executed {i+1:,} instructions")
    print(f"Binary: {code_size:,} bytes | Heap: $EB00-$FE00 | Stack: $FE00-$FFF0")
    print()


if __name__ == '__main__':
    main()
