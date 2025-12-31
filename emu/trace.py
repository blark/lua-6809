#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.9"
# dependencies = ["MC6809"]
# ///
"""
Test harness for running Lua VM on MC6809 emulator (with tracing)

Run with: uv run test_trace.py
"""

import array
from MC6809.components.cpu6809 import CPU
from MC6809.components.memory import Memory
from MC6809.core.configs import BaseConfig


class Memory64K(Memory):
    """Memory subclass that allows full 64KB address space with I/O hooks"""

    OUTPUT_ADDR = 0xF7F0  # Console output port

    def __init__(self, cfg, **kwargs):
        # Skip parent __init__ assertion, set up manually
        self.cfg = cfg
        self.read_bus_request_queue = kwargs.get('read_bus_request_queue')
        self.read_bus_response_queue = kwargs.get('read_bus_response_queue')
        self.write_bus_queue = kwargs.get('write_bus_queue')

        self.INTERNAL_SIZE = 0x10000
        self.RAM_SIZE = self.INTERNAL_SIZE
        self.ROM_SIZE = 0

        self._mem = array.array("B", [0x00] * self.INTERNAL_SIZE)

        self._read_byte_callbacks = {}
        self._read_word_callbacks = {}
        self._write_byte_callbacks = {}
        self._write_word_callbacks = {}
        self._read_byte_middleware = {}
        self._write_byte_middleware = {}
        self._read_word_middleware = {}
        self._write_word_middleware = {}

        # Capture console output
        self.console_output = []
        self.trace_active = False
        self.trace_countdown = 0

    def write_byte(self, address, value):
        """Override to capture I/O writes"""
        self.cpu.cycles += 1
        if address == self.OUTPUT_ADDR:
            # Console output - capture the character
            if value != 0:
                self.console_output.append(chr(value))
                # Start tracing after 'T' is output
                if chr(value) == 'T':
                    self.trace_active = True
                    self.trace_countdown = 50
                    print(f"\n*** 'T' output - starting trace ({self.trace_countdown} instructions) ***")
            return
        self._mem[address] = value


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


class Lua6809Config(BaseConfig):
    # Full 64KB RAM, no ROM
    RAM_START = 0x0000
    RAM_END = 0xFFFF
    ROM_START = 0xFFFF  # Make ROM check always fail (start > end)
    ROM_END = 0x0000


def main():
    print("Loading S19 file...")
    data, start_addr = parse_s19('/tmp/lua.s19')

    print(f"Loaded {len(data)} bytes")
    print(f"Address range: ${min(data.keys()):04X} - ${max(data.keys()):04X}")
    print(f"Start address: ${start_addr:04X}")

    # Set up emulator
    cfg = Lua6809Config({
        "verbosity": None,
        "trace": None,
    })
    memory = Memory64K(cfg)
    cpu = CPU(memory, cfg)

    # Load binary into memory (directly to bypass ROM write protection)
    print("Loading into emulator memory...")
    for addr, byte in data.items():
        memory._mem[addr] = byte

    # Set up stack at top of RAM
    cpu.system_stack_pointer.set(0xFFF0)
    cpu.user_stack_pointer.set(0xEFF0)

    print(f"Starting execution at ${start_addr:04X}...")
    print("(This will likely crash - Lua needs I/O hooks)")

    # Try to run a few instructions
    cpu.program_counter.set(start_addr)

    last_pc = None
    loop_count = 0
    history = []

    try:
        for i in range(5000000):  # 5 million instructions
            pc = cpu.program_counter.value
            history.append(pc)
            if len(history) > 50:
                history.pop(0)

            # Detect infinite loop (same PC twice in a row = BRA $-2)
            if pc == last_pc:
                loop_count += 1
                if loop_count > 10:
                    print(f"\nProgram ended at ${pc:04X} after {i} instructions")
                    break
            else:
                loop_count = 0
            last_pc = pc

            cpu.get_and_call_next_op()

            # Show output as it happens
            if memory.console_output and memory.console_output[-1] == '\n':
                out = "".join(memory.console_output).rstrip('\n')
                print(f"  >> {out}")
                memory.console_output = []

            if i % 100000 == 0:
                print(f"  [{i:6d}] PC=${cpu.program_counter.value:04X}")
    except Exception as e:
        print(f"\nStopped after {i} instructions: {e}")
        print(f"  Final PC=${cpu.program_counter.value:04X}")
        print(f"  Stack S=${cpu.system_stack_pointer.value:04X}")
        print("Last 20 PCs:", " ".join(f"${p:04X}" for p in history))

    print(f"\nExecution completed {i+1} instructions")

    # Print ALL captured console output (including chars without newlines)
    all_output = "".join(memory.console_output)
    print(f"\n=== Console Output ({len(all_output)} chars) ===")
    print(repr(all_output))
    print("======================")


if __name__ == '__main__':
    main()
