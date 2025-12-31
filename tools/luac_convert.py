#!/usr/bin/env python3
"""
Convert Lua 5.1 bytecode from Mac (little-endian, 64-bit) to 6809 (big-endian, 16-bit)

Usage: python3 luac_convert.py [-v] input.luac output.luac
"""

import struct
import sys

VERBOSE = False

def convert_bytecode(infile, outfile):
    with open(infile, 'rb') as f:
        data = f.read()

    # Parse and validate header
    if data[0:4] != b'\x1bLua':
        raise ValueError("Not a Lua bytecode file")

    version = data[4]
    if version != 0x51:
        raise ValueError(f"Expected Lua 5.1 (0x51), got {version:#x}")

    format_ver = data[5]
    endian = data[6]  # 1 = little, 0 = big
    sizeof_int = data[7]
    sizeof_size_t = data[8]
    sizeof_instruction = data[9]
    sizeof_number = data[10]
    integral = data[11]

    if VERBOSE:
        print("+" + "-"*50 + "+")
        print("| Bytecode Header Comparison                       |")
        print("+" + "-"*50 + "+")
        print(f"| {'Field':<20} | {'Input':<10} | {'Output':<10} |")
        print("+" + "-"*50 + "+")
        print(f"| {'Endian':<20} | {'little' if endian else 'big':<10} | {'big':<10} |")
        print(f"| {'sizeof(int)':<20} | {sizeof_int:<10} | {2:<10} |")
        print(f"| {'sizeof(size_t)':<20} | {sizeof_size_t:<10} | {2:<10} |")
        print(f"| {'sizeof(Instruction)':<20} | {sizeof_instruction:<10} | {4:<10} |")
        print(f"| {'sizeof(lua_Number)':<20} | {sizeof_number:<10} | {4:<10} |")
        print(f"| {'Integral':<20} | {integral:<10} | {1:<10} |")
        print("+" + "-"*50 + "+")

    if sizeof_number != 4:
        raise ValueError(f"Expected 4-byte lua_Number, got {sizeof_number}")
    if integral != 1:
        raise ValueError("Expected integral lua_Number")

    # Build new header for 6809
    # 6809: big-endian, 2-byte int, 2-byte size_t, 4-byte Instruction, 4-byte Number
    new_header = bytearray([
        0x1b, ord('L'), ord('u'), ord('a'),  # signature
        0x51,  # version
        0x00,  # format
        0x00,  # big-endian
        0x02,  # sizeof(int) = 2
        0x02,  # sizeof(size_t) = 2
        0x04,  # sizeof(Instruction) = 4
        0x04,  # sizeof(lua_Number) = 4
        0x01,  # integral = 1
    ])

    # Now we need to convert the rest of the bytecode
    # This is complex because we need to parse the entire structure
    # and convert sizes and byte orders

    pos = 12  # after header
    out = bytearray(new_header)

    def read_int():
        nonlocal pos
        if sizeof_int == 4:
            val = struct.unpack('<i', data[pos:pos+4])[0]
            pos += 4
        else:
            val = struct.unpack('<h', data[pos:pos+2])[0]
            pos += 2
        return val

    def read_size_t():
        nonlocal pos
        if sizeof_size_t == 8:
            val = struct.unpack('<Q', data[pos:pos+8])[0]
            pos += 8
        elif sizeof_size_t == 4:
            val = struct.unpack('<I', data[pos:pos+4])[0]
            pos += 4
        else:
            val = struct.unpack('<H', data[pos:pos+2])[0]
            pos += 2
        return val

    def read_number():
        nonlocal pos
        if sizeof_number == 4:
            val = struct.unpack('<i', data[pos:pos+4])[0]
            pos += 4
        else:
            val = struct.unpack('<q', data[pos:pos+8])[0]
            pos += 8
        return val

    def read_instruction():
        nonlocal pos
        val = struct.unpack('<I', data[pos:pos+4])[0]
        if VERBOSE:
            print(f"    Read @{pos:04X}: {data[pos:pos+4].hex()} -> 0x{val:08X}")
        pos += 4
        return val

    def read_byte():
        nonlocal pos
        val = data[pos]
        pos += 1
        return val

    def read_string():
        size = read_size_t()
        nonlocal pos
        if size == 0:
            return None
        s = data[pos:pos+size]
        pos += size
        return s

    def write_int(val):
        out.extend(struct.pack('>h', val & 0xFFFF))  # 2-byte big-endian

    def write_size_t(val):
        out.extend(struct.pack('>H', val & 0xFFFF))  # 2-byte big-endian

    def write_number(val):
        out.extend(struct.pack('>i', val))  # 4-byte big-endian

    # Opcodes that use sBx (signed Bx) argument
    OP_JMP = 22
    OP_FORLOOP = 31
    OP_FORPREP = 32

    # Host (32-bit int) and target (16-bit int) MAXARG_sBx values
    # Note: Lua defines MAX_INT = INT_MAX-2, so on 16-bit int it's 32765, not 32767
    HOST_MAXARG_SBX = ((1 << 18) - 1) >> 1  # 131071
    TARGET_MAXARG_SBX = 32765  # INT_MAX-2 = 32767-2

    def rebias_sbx(val):
        """Re-bias sBx opcodes from 32-bit int to 16-bit int format"""
        opcode = val & 0x3F
        if opcode in (OP_JMP, OP_FORLOOP, OP_FORPREP):
            # Extract Bx field (bits 14-31)
            bx = (val >> 14) & 0x3FFFF  # 18-bit field
            # Convert to signed value using host bias
            sbx = bx - HOST_MAXARG_SBX
            # Re-encode with target bias
            new_bx = sbx + TARGET_MAXARG_SBX
            if new_bx < 0 or new_bx > 0x3FFFF:
                raise ValueError(f"sBx overflow: {sbx} doesn't fit in target format")
            # Rebuild instruction with new Bx
            val = (val & 0x3FFF) | (new_bx << 14)
            if VERBOSE:
                print(f"    Rebias sBx: {sbx} -> Bx {new_bx}")
        return val

    def write_instruction(val):
        val = rebias_sbx(val)
        if VERBOSE:
            print(f"  Instruction @{len(out):04X}: 0x{val:08X} -> {struct.pack('>I', val).hex()}")
        out.extend(struct.pack('>I', val))  # 4-byte big-endian

    def write_byte(val):
        out.append(val & 0xFF)

    def write_string(s):
        if s is None:
            write_size_t(0)
        else:
            write_size_t(len(s))
            out.extend(s)

    def convert_function():
        # Source name
        write_string(read_string())

        # Line defined, last line defined
        write_int(read_int())
        write_int(read_int())

        # nups, numparams, is_vararg, maxstacksize
        write_byte(read_byte())
        write_byte(read_byte())
        write_byte(read_byte())
        write_byte(read_byte())

        # Code
        sizecode = read_int()
        write_int(sizecode)
        for _ in range(sizecode):
            write_instruction(read_instruction())

        # Constants
        sizek = read_int()
        write_int(sizek)
        for _ in range(sizek):
            t = read_byte()
            write_byte(t)
            if t == 0:  # nil
                pass
            elif t == 1:  # boolean
                write_byte(read_byte())
            elif t == 3:  # number
                num = read_number()
                if VERBOSE:
                    print(f"    Number constant: {num} (0x{num:08X})")
                write_number(num)
            elif t == 4:  # string
                write_string(read_string())
            else:
                raise ValueError(f"Unknown constant type {t}")

        # Protos (nested functions)
        sizep = read_int()
        write_int(sizep)
        for _ in range(sizep):
            convert_function()

        # Debug info: lineinfo
        sizelineinfo = read_int()
        write_int(sizelineinfo)
        for _ in range(sizelineinfo):
            write_int(read_int())

        # Debug info: locvars
        sizelocvars = read_int()
        write_int(sizelocvars)
        for _ in range(sizelocvars):
            write_string(read_string())
            write_int(read_int())
            write_int(read_int())

        # Debug info: upvalues
        sizeupvalues = read_int()
        write_int(sizeupvalues)
        for _ in range(sizeupvalues):
            write_string(read_string())

    convert_function()

    if pos != len(data):
        print(f"Warning: {len(data) - pos} bytes remaining")

    with open(outfile, 'wb') as f:
        f.write(out)

    print(f"Converted {infile} -> {outfile} ({len(out)} bytes)")

if __name__ == '__main__':
    args = sys.argv[1:]

    if '-v' in args:
        VERBOSE = True
        args.remove('-v')

    if len(args) != 2:
        print(f"Usage: {sys.argv[0]} [-v] input.luac output.luac")
        print("  -v  Verbose output (show header comparison)")
        sys.exit(1)

    convert_bytecode(args[0], args[1])
