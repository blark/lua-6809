"""
Anachron8 memory layout for the TARGET=a8 VM build (lua-a8.s19).

Kept free of emulator imports so host tools (tools/bytecode_s19.py) can use it.
"""

# Anachron8 layout (TARGET=a8 build, lua-a8.s19; hardware map in
# isp-6809/docs/MEMORY-MAP.md). Code at $1000; the heap runs from the end of
# the program (linker symbol, a8heap.s) to VRAM at $C000; the bytecode is
# loaded at $D000; the stack is MON09's user stack, which G starts at $DF60.
A8_CODE_BASE = 0x1000
A8_HEAP_END = 0xC000
A8_BYTECODE_SIZE_ADDR = 0xD000
A8_BYTECODE_DATA_ADDR = 0xD002
A8_BYTECODE_MAX_SIZE = 0x0BFE  # to $DBFF
A8_STACK_TOP = 0xDF60          # MON09 SAVS after reset
A8_STACK_BOTTOM = 0xDC00
A8_ENTRY_CC = 0xD0             # MON09 SAVCC after reset: E, F, I set
