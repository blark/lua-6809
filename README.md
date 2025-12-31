# Lua 6809

Porting the Lua 5.1.5 VM to run on bare-metal Motorola MC6809.

## Current Status

**The VM runs.** 29 tests passing across core language features:

| Category | Tests | Features |
|----------|-------|----------|
| Arithmetic | 9 | add, sub, mul, div, mod, unary minus, overflow |
| Boolean | 2 | and/or, short-circuit evaluation |
| Comparison | 3 | ==, ~=, <, >, <=, >= |
| Conditionals | 1 | if/elseif/else |
| Functions | 4 | calls, closures, multiple returns, nested functions |
| Loops | 3 | for, while, repeat/until |
| Recursion | 4 | factorial, fibonacci, GCD |
| Tables | 3 | arrays, hash tables, nested tables |

```bash
nix run .#test
```

### What Works

- ~48KB VM binary fits in memory
- 32-bit integer math (no floats)
- Pre-compiled bytecode execution
- Memory-mapped console output
- Tables, functions, closures, recursion
- All standard control flow

### What's Missing

- Standard library (print, string, table, math modules)
- Garbage collection tuning for constrained memory
- Coroutines (untested)
- Real hardware testing (emulator only so far)

## Architecture

Scripts are compiled on the host, then only the bytecode interpreter runs on the 6809:

```
script.lua
    │
    ▼
┌─────────────────┐
│   luac-int32    │  Lua compiler patched for 32-bit integers
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│ luac_convert.py │  Fix endianness for 6809
└────────┬────────┘
         │
         ▼
  6809 bytecode ──────► lua.s19 (VM) ──────► output
  loaded at $E800       ~48KB binary        via $F7F0
```

### Memory Map

| Address | Size | Purpose |
|---------|------|---------|
| `$0000-$BFBC` | ~48KB | Lua VM code |
| `$E800-$EAFF` | 768B | Bytecode (loaded by emulator) |
| `$EB00-$F7EF` | ~3KB | Heap (grows up) |
| `$F7F0` | 1B | Output port (memory-mapped I/O) |
| `$F800-$FFF0` | ~2KB | Stack (grows down) |

## Getting Started

Requires [Nix](https://nixos.org/) with flakes enabled.

```bash
# Enter development environment
nix develop

# Or with direnv
direnv allow
```

### Commands

| Command | Description |
|---------|-------------|
| `luac6809 script.lua` | Compile Lua to 6809 bytecode |
| `regen-patch` | Regenerate patch after editing lua-work/ |
| `nix run .#test` | Run test suite |

### Running Manually

```bash
# Compile a script
luac6809 -o test.luac script.lua

# Run in emulator (verbose output)
uv run emu/harness.py test.luac

# Run in TUI emulator
uv run emu/visual.py
```

![TUI Emulator](docs/TUI.png)

## Development Workflow

The Lua source is patched, not forked. To modify:

1. Edit files in `lua-work/src/`
2. Run `regen-patch` to update `patches/6809-phase1.patch`
3. Run `direnv reload` to rebuild

### Key Files

| File | Purpose |
|------|---------|
| `lua-work/src/lua6809.c` | Bare-metal runtime (malloc, I/O, main) |
| `lua-work/src/luaconf.h` | Platform configuration |
| `patches/6809-phase1.patch` | All modifications to Lua source |
| `tools/luac_convert.py` | Bytecode endianness converter |
| `emu/runner.py` | Test harness for MC6809 emulator |

## Technical Details

### Modifications to Lua

| Change | Reason |
|--------|--------|
| `LUA_NUMBER` = `long` | 32-bit integers instead of doubles |
| Parser/lexer stubbed out | Bytecode-only mode saves ~15KB |
| Custom `malloc`/`sbrk` | Bump allocator for bare metal |
| Memory-mapped I/O | Output to $F7F0 instead of printf |
| Reduced limits | MAXCALLS=100, MAXCSTACK=128 |

### gcc6809 Workarounds

The gcc6809 cross-compiler has bugs that required workarounds:

1. **Indirect call offset bug** - After pushing to stack, indirect calls use the wrong offset. Fixed by copying function pointers to local variables before calls.

2. **32-bit multiply bug** - Patched in [gcc6809-nix](https://github.com/blark/gcc6809-nix).

### Toolchain

- [gcc6809](https://github.com/blark/gcc6809-nix) - GCC 4.3.6 cross-compiler
- [newlib](https://sourceware.org/newlib/) - C library (setjmp, memcpy, etc.)
- [MC6809](https://pypi.org/project/MC6809/) - Python emulator for testing

## License

Lua is MIT licensed. See [lua.org/license.html](https://www.lua.org/license.html).
