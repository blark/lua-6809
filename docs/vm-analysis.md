# Lua 5.1.5 VM Analysis for MC6809

## Binary Summary

| Metric | Value |
|--------|-------|
| Total code + data | ~48 KB |
| Address range | $0000 - $BFBC |
| Entry point | $0007 |
| Object files | 18 modules |

## Module Breakdown

### Core VM (Required)

| Module | Size | Functions | Purpose |
|--------|------|-----------|---------|
| **lvm.c** | 59,446 | VM executor | Bytecode interpreter, main execution loop |
| **lapi.c** | 36,158 | C API | Public API (lua_push*, lua_to*, lua_get*, etc.) |
| **lgc.c** | 26,344 | GC | Mark-and-sweep garbage collector |
| **ltable.c** | 21,272 | Tables | Hash table implementation (Lua's core data structure) |
| **ldo.c** | 22,963 | Calls | Function calls, protected calls, coroutines |
| **lundump.c** | 11,003 | Loader | Bytecode loading and verification |
| **lobject.c** | 10,904 | Objects | Value representation, type conversions |
| **lstate.c** | 6,640 | State | lua_State creation, global state management |
| **lfunc.c** | 6,098 | Functions | Closure/upvalue management |
| **ltm.c** | 4,592 | Metamethods | Tag method (metamethod) dispatch |
| **lstring.c** | 4,577 | Strings | Interned string table |
| **lopcodes.c** | 4,087 | Opcodes | Opcode names and metadata |
| **lzio.c** | 2,528 | Buffered I/O | Input stream abstraction |
| **lmem.c** | 2,192 | Memory | Memory allocation wrapper |

**Subtotal: ~218 KB object files, ~35-40 KB in final binary**

### Parser/Compiler (Not needed for bytecode-only)

| Module | Size | Functions | Purpose |
|--------|------|-----------|---------|
| **lcode.c** | 43,607 | Code gen | Bytecode generation from AST |
| **llex.c** | 29,965 | Lexer | Tokenizer for Lua source |
| **lparser.c** | (stubbed) | Parser | Recursive descent parser |

**Note:** Currently `lcode.c` and `llex.c` are compiled but the parser is stubbed in `lua6809.c`. The lexer is partially needed because `luaX_init()` initializes reserved keyword strings.

### Debug Support

| Module | Size | Functions | Purpose |
|--------|------|-----------|---------|
| **ldebug.c** | 30,341 | Debug | Error messages, tracebacks, debug hooks |
| **ldump.c** | 5,993 | Dump | Bytecode serialization (write .luac files) |

### Auxiliary

| Module | Size | Functions | Purpose |
|--------|------|-----------|---------|
| **lauxlib.c** | 28,522 | Aux lib | Helper functions (luaL_*), error formatting |
| **lua6809.c** | 9,634 | Platform | 6809-specific: malloc/sbrk, stubs, I/O |

## Standard Libraries (NOT currently linked)

These exist in the source but are not compiled into the current build:

| Library | File | Functions | Notes |
|---------|------|-----------|-------|
| Base | lbaselib.c | print, type, pairs, ipairs, etc. | Core language functions |
| String | lstrlib.c | string.*, pattern matching | Large (~23KB source) |
| Table | ltablib.c | table.insert, table.sort, etc. | |
| Math | lmathlib.c | math.* | Would need integer versions |
| I/O | liolib.c | io.*, file handles | Requires filesystem |
| OS | loslib.c | os.time, os.exit, etc. | Requires OS services |
| Debug | ldblib.c | debug.* | Introspection |
| Package | loadlib.c | require, module | Dynamic loading |

## VM Opcodes (38 instructions)

```
OP_MOVE      OP_LOADK     OP_LOADBOOL  OP_LOADNIL   OP_GETUPVAL
OP_GETGLOBAL OP_GETTABLE  OP_SETGLOBAL OP_SETUPVAL  OP_SETTABLE
OP_NEWTABLE  OP_SELF      OP_ADD       OP_SUB       OP_MUL
OP_DIV       OP_MOD       OP_POW       OP_UNM       OP_NOT
OP_LEN       OP_CONCAT    OP_JMP       OP_EQ        OP_LT
OP_LE        OP_TEST      OP_TESTSET   OP_CALL      OP_TAILCALL
OP_RETURN    OP_FORLOOP   OP_FORPREP   OP_TFORLOOP  OP_SETLIST
OP_CLOSE     OP_CLOSURE   OP_VARARG
```

## 6809-Specific Configuration

### Number Type
- `LUA_NUMBER` = `long` (32-bit signed integer)
- No floating point support
- Integer power function (`luai_ipow`) for `^` operator

### Memory Limits
| Setting | Value | Default |
|---------|-------|---------|
| LUAI_MAXCALLS | 100 | 20000 |
| LUAI_MAXCSTACK | 128 | 2048 |
| LUAI_MAXCCALLS | 20 | 200 |
| LUAI_MAXVARS | 50 | 200 |
| LUAI_MAXUPVALUES | 20 | 60 |
| LUAL_BUFFERSIZE | 128 | 8192 |
| LUA_MAXINPUT | 128 | 512 |
| LUAI_MEM | int (16-bit) | size_t |

### Memory Map
```
$0000-$BFBC  Code + read-only data (~48 KB)
$E800-$EAFF  Bytecode area (768 bytes)
$EB00-$F7EF  Heap (~3 KB, grows up)
$F7F0        Console output port (memory-mapped I/O)
$F800-$FFF0  Stack (~2 KB, grows down)
```

## Size Reduction Opportunities

### Immediate Savings (Bytecode-only mode)

| Action | Potential Savings |
|--------|------------------|
| Stub `lcode.c` functions | ~8-10 KB |
| Reduce `llex.c` to just `luaX_init()` | ~5-6 KB |
| Remove `ldump.c` | ~1-2 KB |
| **Total** | **~14-18 KB** |

### Aggressive Savings (Minimal error support)

| Action | Potential Savings |
|--------|------------------|
| Stub `ldebug.c` (error codes only) | ~6-8 KB |
| Strip error message strings | ~2-3 KB |
| **Additional** | **~8-11 KB** |

### Theoretical Minimum

A minimal bytecode-only VM with basic errors could potentially reach **~35-40 KB**.

## Detailed Module Analysis

### llex.c (Lexer) - 463 lines, 29,965 bytes

**Functions:**

| Function | Lines | Purpose | Needed for bytecode? |
|----------|-------|---------|---------------------|
| `luaX_init()` | 64-72 | Init reserved keyword strings | **YES** |
| `luaX_token2str()` | 78-86 | Token to string for errors | No |
| `luaX_lexerror()` | 102-109 | Report lexer errors | No |
| `luaX_syntaxerror()` | 112-114 | Report syntax errors | No |
| `luaX_newstring()` | 117-126 | Intern string during lexing | No |
| `luaX_setinput()` | 140-151 | Setup lexer input stream | No |
| `luaX_next()` | 448-456 | Get next token | No |
| `luaX_lookahead()` | 459-462 | Peek ahead one token | No |
| `llex()` | 334-445 | Main lexer state machine | No |

**Static helpers (parser-only):** `save()`, `inclinenumber()`, `check_next()`, `buffreplace()`, `trydecpoint()`, `read_numeral()`, `read_long_string()`, `read_string()`, `skip_sep()`

**Data:**
```c
const char *const luaX_tokens[] = {
    "and", "break", "do", "else", "elseif", "end", "false", "for",
    "function", "if", "in", "local", "nil", "not", "or", "repeat",
    "return", "then", "true", "until", "while",
    "..", "...", "==", ">=", "<=", "~=",
    "<number>", "<name>", "<string>", "<eof>", NULL
};
```

**Minimal stub for bytecode-only (~15 lines):**
```c
const char *const luaX_tokens[] = { /* ... */ };

void luaX_init(lua_State *L) {
  int i;
  for (i=0; i<NUM_RESERVED; i++) {
    TString *ts = luaS_new(L, luaX_tokens[i]);
    luaS_fix(ts);
    ts->tsv.reserved = cast_byte(i+1);
  }
}
```

### ldebug.c (Debug/Errors) - 639 lines, 30,341 bytes

**Public Debug API (not needed for basic execution):**

| Function | Lines | Purpose |
|----------|-------|---------|
| `lua_sethook()` | 56-66 | Set debug hook callback |
| `lua_gethook()` | 69-71 | Get current hook |
| `lua_gethookmask()` | 74-76 | Get hook mask |
| `lua_gethookcount()` | 79-81 | Get hook count |
| `lua_getstack()` | 84-104 | Get stack frame info |
| `lua_getlocal()` | 127-135 | Get local variable |
| `lua_setlocal()` | 138-147 | Set local variable |
| `lua_getinfo()` | 232-259 | Get function debug info |

**Internal functions called by VM (NEEDED):**

| Function | Lines | Called From | Purpose |
|----------|-------|-------------|---------|
| `luaG_runerror()` | 631-637 | everywhere | Throw runtime error |
| `luaG_errormsg()` | 618-628 | error paths | Call error handler |
| `luaG_typeerror()` | 567-578 | lvm.c | "attempt to index nil" |
| `luaG_concaterror()` | 581-585 | lvm.c | "attempt to concatenate" |
| `luaG_aritherror()` | 588-593 | lvm.c | "attempt to arithmetic" |
| `luaG_ordererror()` | 596-604 | lvm.c | "attempt to compare" |
| `luaG_checkcode()` | 484-486 | lundump.c | Verify bytecode |
| `luaG_checkopenop()` | 290-301 | lvm.c | Check open calls |

**Big static functions:**

| Function | Lines | Size | Purpose |
|----------|-------|------|---------|
| `symbexec()` | 317-475 | ~160 lines | Symbolic execution for bytecode verification |
| `getobjname()` | 497-541 | ~45 lines | Find variable name for errors |
| `getfuncname()` | 544-555 | ~12 lines | Find calling function name |
| `auxgetinfo()` | 193-229 | ~37 lines | Helper for lua_getinfo |

**Minimal stubs for bytecode-only (~40 lines):**
```c
void luaG_runerror(lua_State *L, const char *fmt, ...) {
  luaD_throw(L, LUA_ERRRUN);
}

void luaG_errormsg(lua_State *L) {
  luaD_throw(L, LUA_ERRRUN);
}

void luaG_typeerror(lua_State *L, const TValue *o, const char *op) {
  luaG_runerror(L, "type error");
}

void luaG_concaterror(lua_State *L, StkId p1, StkId p2) {
  luaG_runerror(L, "concat error");
}

void luaG_aritherror(lua_State *L, const TValue *p1, const TValue *p2) {
  luaG_runerror(L, "arithmetic error");
}

int luaG_ordererror(lua_State *L, const TValue *p1, const TValue *p2) {
  luaG_runerror(L, "compare error");
  return 0;
}

int luaG_checkcode(const Proto *pt) { return 1; }  /* trust bytecode */
int luaG_checkopenop(Instruction i) { return 1; }

/* Stub debug API */
int lua_sethook(lua_State *L, lua_Hook f, int mask, int count) { return 0; }
lua_Hook lua_gethook(lua_State *L) { return NULL; }
int lua_gethookmask(lua_State *L) { return 0; }
int lua_gethookcount(lua_State *L) { return 0; }
int lua_getstack(lua_State *L, int level, lua_Debug *ar) { return 0; }
const char *lua_getlocal(lua_State *L, const lua_Debug *ar, int n) { return NULL; }
const char *lua_setlocal(lua_State *L, const lua_Debug *ar, int n) { return NULL; }
int lua_getinfo(lua_State *L, const char *what, lua_Debug *ar) { return 0; }
```

**Summary:**

| Category | Lines | Needed? |
|----------|-------|---------|
| Debug API (hooks, introspection) | ~120 | No |
| Bytecode verification (`symbexec`) | ~200 | Optional* |
| Error message formatting | ~100 | Simplify |
| Core error throwing | ~50 | Yes (stub) |

*Skipping `luaG_checkcode` is risky with untrusted bytecode but safe if you control the source.

## Dependency Graph

```
main() (lua6809.c)
  └── luaL_newstate() (lauxlib.c)
        └── lua_newstate() (lstate.c)
              ├── luaX_init() (llex.c) ← pulls in lexer
              ├── luaS_* (lstring.c)
              ├── luaT_init() (ltm.c)
              └── f_luaopen() → stack_init()

lua_call() (lapi.c)
  └── luaD_call() (ldo.c)
        └── luaV_execute() (lvm.c)
              ├── luaT_* (ltm.c) - metamethods
              ├── luaH_* (ltable.c) - table ops
              ├── luaG_* (ldebug.c) - errors
              └── luaC_* (lgc.c) - GC barriers
```

## Public C API Functions

The VM exports 116 public API functions:
- `lua_*` - Core API (state, stack, values, calls)
- `luaL_*` - Auxiliary library (convenience wrappers)

Key functions currently used:
- `luaL_newstate()` - Create new Lua state
- `lua_pushinteger()` - Push integer to stack
- `lua_tointeger()` - Get integer from stack
- `lua_close()` - Destroy Lua state

## Known gcc6809 Issues

1. **Indirect call bug** - Function pointer calls through stack with wrong offset after PSHS
   - Workaround: Copy function pointer to local variable before call

2. **ICE with 32-bit constants** - Some `(volatile long*)` patterns crash compiler
   - Workaround: Use `int` pointers or indirection

## Files Modified from Stock Lua 5.1.5

1. `luaconf.h` - 6809 platform defines, integer math, reduced limits
2. `ldo.c` - Volatile workaround for indirect call bug
3. `lstate.c` - Volatile workaround for indirect call bug
4. `lmem.c` - Volatile workaround for indirect call bug
5. `lzio.c` - Volatile workaround for indirect call bug
6. `lua6809.c` - New file: platform stubs, main(), parser stubs
