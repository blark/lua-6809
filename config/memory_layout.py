"""
Memory layout configuration for Lua 6809 port.

This file is the single source of truth for all memory addresses.
Used by:
  - test_lua_6809.py (Python emulator)
  - test_runner.py (minimal test harness)
  - tools/luac_convert.py (bytecode converter)
  - lua-work/src/lua6809.c (via generated header)

Memory Map (48KB code version):
  $0000-$BCC8  Code (~48KB)
  $E800-$EAFF  Bytecode area (768 bytes max)
  $EB00-$F7F0  Heap (~3KB)
  $F7F0        I/O port (console output)
  $F800-$FFF0  Stack (~2KB, grows down)
"""

import array
from MC6809.components.memory import Memory
from MC6809.core.configs import BaseConfig

# Bytecode loading area
BYTECODE_SIZE_ADDR = 0xE800  # 2-byte size (big-endian)
BYTECODE_DATA_ADDR = 0xE802  # bytecode starts here
BYTECODE_MAX_SIZE = 0x02FE   # 766 bytes max (to $EAFF)

# Heap allocation
HEAP_START = 0xEB00
HEAP_END = 0xF7F0  # ~3KB heap

# Memory-mapped I/O
OUTPUT_ADDR = 0xF7F0  # Console output port

# Stack
STACK_BOTTOM = 0xF800  # Stack starts here (grows down)
STACK_TOP = 0xFFF0     # Initial stack pointer


class Lua6809Config(BaseConfig):
    """MC6809 emulator config for Lua VM"""
    RAM_START = 0x0000
    RAM_END = 0xFFFF
    ROM_START = 0xFFFF  # Make ROM check always fail
    ROM_END = 0x0000


class Memory64K(Memory):
    """Memory subclass with 64KB address space and I/O hooks"""

    def __init__(self, cfg, track_usage=False, **kwargs):
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

        self.console_output = []
        self.cpu = None  # Set by caller if cycle counting needed
        self.track_usage = track_usage
        self.heap_high_water = HEAP_START
        self.stack_low_water = STACK_TOP

    def write_byte(self, address, value):
        if self.cpu:
            self.cpu.cycles += 1

        if address == OUTPUT_ADDR:
            if value != 0:
                self.console_output.append(chr(value))
            return

        if self.track_usage:
            if HEAP_START <= address < HEAP_END and address >= self.heap_high_water:
                self.heap_high_water = address + 1
            if STACK_BOTTOM <= address < STACK_TOP and address < self.stack_low_water:
                self.stack_low_water = address

        self._mem[address] = value

    def read_byte(self, address):
        return self._mem[address]

    def load(self, address, data):
        for i, byte in enumerate(data):
            self._mem[address + i] = byte


def generate_c_header():
    """Generate C header content for lua6809.c"""
    return f'''/* Auto-generated from config/memory_layout.py - DO NOT EDIT */

/* Bytecode loading area */
#define BYTECODE_SIZE_ADDR ((volatile unsigned char*)0x{BYTECODE_SIZE_ADDR:04X})
#define BYTECODE_DATA_ADDR ((const char*)0x{BYTECODE_DATA_ADDR:04X})
#define BYTECODE_MAX_SIZE  0x{BYTECODE_MAX_SIZE:04X}

/* Heap allocation */
#define HEAP_START ((char*)0x{HEAP_START:04X})
#define HEAP_END   ((char*)0x{HEAP_END:04X})

/* Memory-mapped I/O */
#define OUTPUT_ADDR ((volatile char*)0x{OUTPUT_ADDR:04X})

/* Stack */
#define STACK_BOTTOM 0x{STACK_BOTTOM:04X}
#define STACK_TOP    0x{STACK_TOP:04X}
'''


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "--c-header":
        print(generate_c_header())
    else:
        print(f"Memory Layout Configuration")
        print(f"============================")
        print(f"Bytecode: ${BYTECODE_SIZE_ADDR:04X}-${BYTECODE_DATA_ADDR + BYTECODE_MAX_SIZE:04X}")
        print(f"Heap:     ${HEAP_START:04X}-${HEAP_END:04X}")
        print(f"I/O:      ${OUTPUT_ADDR:04X}")
        print(f"Stack:    ${STACK_BOTTOM:04X}-${STACK_TOP:04X}")
