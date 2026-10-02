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

from emu.config.memory_layout import Lua6809Config, Memory64K

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

# Anachron8 layout constants come from the installed emulator package.
from emu.config.anachron8 import *  # noqa: E402,F401,F403



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
