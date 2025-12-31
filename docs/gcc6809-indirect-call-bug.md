# gcc6809 Indirect Call Stack Offset Bug

## Summary

gcc6809 (GCC 4.3.6 port for Motorola 6809) generates incorrect code when calling through a function pointer that is accessed via a stack-relative offset, when there are push operations between loading the function pointer address and the call.

## Severity

**Critical** - Causes crashes, infinite loops, and unpredictable behavior in any code using function pointers with certain stack configurations.

## Affected Version

- gcc6809 based on GCC 4.3.6
- Compiler path: `m6809-unknown-none-gcc`
- From Nix package: `gcc6809-nix`

## Bug Description

When the compiler generates code to call through a function pointer stored at a stack-relative address, and push operations occur between calculating the address and making the call, the compiler uses the **old** stack offset instead of adjusting for the changed stack pointer.

### Example C Code

```c
void call_func(Pfunc f, void *ud) {
    // f is at stack offset 30
    // ud is at stack offset 32
    (*f)(L, ud);  // Call function pointer with argument
}
```

### Generated Assembly (BUGGY)

```asm
    ldx   32,s      ; load ud from stack offset 32
    pshs  x         ; push ud onto stack, S decreases by 2
    ldx   2,s       ; load L (was at offset 0, now at offset 2) - CORRECT
    jsr   [30,s]    ; BUG: calls through offset 30, but f is now at offset 32!
```

### Stack Layout Analysis

```
Before PSHS X (S=$FFAC):
  S+28: return address
  S+30: f (function pointer) <- compiler thinks f is here
  S+32: ud

After PSHS X (S=$FFAA):
  S+0:  ud (just pushed)
  S+2:  L
  S+30: return address    <- compiler reads THIS (wrong!)
  S+32: f (function pointer) <- f is actually here now
  S+34: ud (original)
```

### Result

The `JSR [30,S]` instruction reads the return address instead of the function pointer, causing:
1. Jump to wrong address (often garbage)
2. Execution of random memory as code
3. Crash or infinite loop

## Reproduction

### Minimal Test Case

```c
typedef void (*Pfunc)(void*, void*);

void target(void *a, void *b) {
    // Do something
}

void caller(Pfunc f, void *ud) {
    (*f)(NULL, ud);
}

int main(void) {
    caller(target, NULL);
    return 0;
}
```

Compile with: `m6809-unknown-none-gcc -Os -c test.c -o test.o`

Disassemble and look for the indirect call pattern where a PSHS occurs before JSR [offset,S].

### Observed in Real Code

This bug manifests in Lua 5.1.5 port at multiple locations:
1. `ldo.c:luaD_rawrunprotected()` - calling `(*f)(L, ud)`
2. `lstate.c:lua_newstate()` - calling allocator `(*f)(ud, NULL, 0, size)`
3. `lmem.c:luaM_realloc_()` - calling `(*g->frealloc)(...)`
4. `lzio.c:luaZ_fill()` - calling reader function

## Workaround

Copy the function pointer to a local variable before any operations that might push to the stack:

```c
void caller(Pfunc f, void *ud) {
    volatile Pfunc f_copy = f;      // Force copy to local/register
    volatile void *ud_copy = ud;
    (*f_copy)(NULL, (void*)ud_copy);
}
```

Using `volatile` prevents the optimizer from eliminating the copy and forces the use of register-based or local-variable-based addressing instead of stack-relative.

## Root Cause Analysis

The bug is likely in one of these GCC components:

1. **Machine Description (m6809.md)** - The 6809 backend's instruction patterns may not correctly model the stack pointer side effects of PSHS/PULS instructions.

2. **Reload Pass** - The register allocator/reloader may be computing stack offsets before final instruction scheduling, then not updating them when PSHS instructions are inserted.

3. **Stack Slot Assignment** - The logic that assigns stack offsets to parameters may not account for dynamic stack pointer changes within the function body.

## Files to Investigate

In the gcc6809 source tree:
- `gcc/config/m6809/m6809.md` - Machine description
- `gcc/config/m6809/m6809.c` - Target hooks and code generation
- `gcc/config/m6809/m6809.h` - Target macros and definitions

Look for:
- How `PUSH` instructions are modeled (do they update frame references?)
- How indirect calls through stack slots are generated
- Whether there's a "stack adjustment" tracking mechanism

## Potential Fixes

1. **Conservative Fix**: Always copy function pointers to registers before indirect calls in the backend

2. **Proper Fix**: Track stack pointer changes and adjust all stack-relative offsets in the delay slot / instruction scheduling phase

3. **Pattern Fix**: Modify the `call_indirect` pattern in m6809.md to explicitly handle the stack adjustment

## Test Verification

After any fix, verify with:

```c
// Should NOT crash
void test_indirect_call(void (*f)(int, int), int a, int b) {
    (*f)(a, b);
}

// Should NOT crash
void test_nested(void (*f)(void*, void*), void *ud) {
    (*f)((void*)0x1234, ud);
}
```

Disassemble and confirm no JSR [offset,S] uses stale offset after PSHS.

## References

- GCC Internals Manual: Machine Descriptions
- 6809 Programmer's Reference: Stack operations
- Lua 5.1.5 source: ldo.c, lstate.c, lmem.c, lzio.c
