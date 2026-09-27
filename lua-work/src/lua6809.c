/*
** 6809-specific stubs and implementations for Lua
*/

#include "lua.h"
#include "lauxlib.h"
#include <stddef.h>
#include <stdio.h>
#include <stdlib.h>

/*
** Memory-mapped I/O for emulator output
** NOTE: Must be OUTSIDE stack range ($F800-$FFF0) to avoid accidental output
** when PSHS writes to stack. Using $F7F0 just before stack area.
*/
#ifndef LUA_A8
static volatile char * const OUTPUT_ADDR = (volatile char*)0xF7F0;
#endif

/* Integer power function for luai_numpow */
long luai_ipow(long base, long exp) {
  long result = 1;
  if (exp < 0) return 0;  /* no negative exponents in integer math */
  while (exp > 0) {
    if (exp & 1) result = result * base;
    exp >>= 1;
    base = base * base;
  }
  return result;
}

/*
** Stubs for parser/lexer/dumper - we only run pre-compiled bytecode
** This allows us to exclude lparser.c, llex.c, lcode.c, ldump.c
*/
#include "lparser.h"
#include "llex.h"
#include "lundump.h"

Proto *luaY_parser (lua_State *L, ZIO *z, Mbuffer *buff, const char *name) {
  (void)z; (void)buff; (void)name;
  luaL_error(L, "parser not available");
  return NULL;
}

void luaX_init (lua_State *L) {
  (void)L;
}

int luaU_dump (lua_State *L, const Proto *f, lua_Writer w, void *data, int strip) {
  (void)L; (void)f; (void)w; (void)data; (void)strip;
  return 1;  /* error: dumping not supported */
}


/*
** Simple sbrk/malloc implementation for bare metal 6809
** Heap starts after code and grows upward
*/

/* Heap area: $EB00 - $F7F0 (about 3KB)
 * Code ends at ~$E7AC. Bytecode at $E800. Heap starts after bytecode.
 * I/O port at $F7F0 (reserved, not in heap or stack).
 * Stack grows down from $FFF0 to $F800 (~2KB). */
#ifdef LUA_A8
/* Anachron8: the heap runs from the end of the program (a8heap.s, from the
 * linker) up to VRAM at $C000. The stack is MON09's user stack below $DF60. */
extern char *a8_heap_start;
#define HEAP_START a8_heap_start
#define HEAP_END   ((char*)0xC000)
static char *heap_break;
#else
#define HEAP_START ((char*)0xEB00)
#define HEAP_END   ((char*)0xF7F0)

static char *heap_break = HEAP_START;
#endif

void *sbrk(int incr) {
  char *prev_break;
#ifdef LUA_A8
  if (heap_break == NULL) heap_break = HEAP_START;
#endif
  prev_break = heap_break;
  if (incr == 0) {
    return prev_break;
  }
  if (heap_break + incr > HEAP_END || heap_break + incr < HEAP_START) {
    return (void*)-1;  /* Out of memory */
  }
  heap_break += incr;
  return prev_break;
}

/* Also provide _sbrk for newlib compatibility */
void *_sbrk(int incr) {
  return sbrk(incr);
}

static char *heap_ptr = NULL;

/* Each allocation has a 2-byte size header */
void *malloc(size_t size) {
  unsigned char *block;
  void *new_ptr;
  size_t total = size + 2;  /* 2 bytes for size header */

  if (heap_ptr == NULL) {
    heap_ptr = sbrk(0);
  }
  block = (unsigned char*)heap_ptr;
  new_ptr = sbrk((int)total);
  if (new_ptr == (void*)-1) {
    return NULL;
  }
  heap_ptr = (char*)heap_ptr + total;
  /* Store size in header (big-endian) */
  block[0] = (size >> 8) & 0xFF;
  block[1] = size & 0xFF;
  return block + 2;  /* Return pointer past header */
}

void *realloc(void *ptr, size_t size) {
  unsigned char *old;
  size_t old_size, copy_size;
  unsigned char *new_block;
  size_t i;

  if (ptr == NULL) return malloc(size);

  /* Read old size from header */
  old = (unsigned char*)ptr - 2;
  old_size = (old[0] << 8) | old[1];

  new_block = malloc(size);
  if (new_block == NULL) return NULL;

  /* Copy old data to new block */
  copy_size = (old_size < size) ? old_size : size;
  for (i = 0; i < copy_size; i++) {
    new_block[i] = ((unsigned char*)ptr)[i];
  }
  return new_block;
}

void free(void *ptr) {
  /* Simple bump allocator - no free */
  (void)ptr;
}

/*
** Number conversion (simplified)
*/
long strtol(const char *s, char **endptr, int base) {
  long result = 0;
  int neg = 0;
  while (*s == ' ') s++;
  if (*s == '-') { neg = 1; s++; }
  else if (*s == '+') s++;

  if (base == 0) {
    if (*s == '0' && (s[1] == 'x' || s[1] == 'X')) { base = 16; s += 2; }
    else if (*s == '0') { base = 8; s++; }
    else base = 10;
  }

  while (*s) {
    int digit;
    if (*s >= '0' && *s <= '9') digit = *s - '0';
    else if (*s >= 'a' && *s <= 'f') digit = *s - 'a' + 10;
    else if (*s >= 'A' && *s <= 'F') digit = *s - 'A' + 10;
    else break;
    if (digit >= base) break;
    result = result * base + digit;
    s++;
  }
  if (endptr) *endptr = (char*)s;
  return neg ? -result : result;
}

unsigned long strtoul(const char *s, char **endptr, int base) {
  return (unsigned long)strtol(s, endptr, base);
}

/*
** Minimal sprintf (numbers only)
*/
int sprintf(char *buf, const char *fmt, ...) {
  /* Very minimal - just handle %ld for Lua numbers */
  /* This is a stub - real impl would use va_list */
  buf[0] = '?';
  buf[1] = '\0';
  return 1;
}

/*
** Stubs for file I/O (not supported on bare metal)
** These are defined in newlib headers but not implemented
*/
#include <reent.h>
FILE *fopen(const char *path, const char *mode) { (void)path; (void)mode; return NULL; }
FILE *freopen(const char *path, const char *mode, FILE *f) { (void)path; (void)mode; (void)f; return NULL; }
int fclose(FILE *f) { (void)f; return 0; }
size_t fread(void *ptr, size_t size, size_t n, FILE *f) { (void)ptr; (void)size; (void)n; (void)f; return 0; }
int fprintf(FILE *f, const char *fmt, ...) { (void)f; (void)fmt; return 0; }
int ungetc(int c, FILE *f) { (void)c; (void)f; return -1; }
int __srget_r(struct _reent *r, FILE *f) { (void)r; (void)f; return -1; }

#ifdef LUA_A8
/* SWI hands control back to MON09, which shows the registers and prompts.
 * D tells how the VM ended: $1A8E = normal exit, $DEAD = abort. */
void abort(void) { for (;;) __asm__ volatile ("ldd\t#0xDEAD\n\tswi"); }
void exit(int code) { (void)code; for (;;) __asm__ volatile ("ldd\t#0x1A8E\n\tswi"); }
#else
void abort(void) { while(1); }
void exit(int code) { (void)code; while(1); }
#endif
int atexit(void (*func)(void)) { (void)func; return 0; }

#ifdef LUA_A8
/* Anachron8 console: every character goes to the ACIA (USB serial, what
 * MON09 uses) and to an 80x25 text screen in VRAM. */
#define ACIA_DATA  (*(volatile unsigned char *)0x0000)
#define ACIA_STAT  (*(volatile unsigned char *)0x0001)
#define ACIA_TDRE  0x10
#define VRAM       ((volatile unsigned char *)0xC000)
#define VRAM_ATTR  (VRAM + 2000)
#define CURSOR_X   (VRAM[4000])
#define CURSOR_Y   (VRAM[4001])
#define COLS       80
#define ROWS       25
#define TEXT_ATTR  0x07   /* light grey on black */

static unsigned char scr_x, scr_y, scr_ready;

static void acia_putc(char c) {
  while (!(ACIA_STAT & ACIA_TDRE))
    ;
  ACIA_DATA = c;
}

static void screen_init(void) {
  int i;
  for (i = 0; i < COLS * ROWS; i++) {
    VRAM[i] = ' ';
    VRAM_ATTR[i] = TEXT_ATTR;
  }
  scr_x = scr_y = 0;
  scr_ready = 1;
}

static void screen_putc(char c) {
  int i;
  if (!scr_ready) screen_init();
  if (c == '\n') {
    scr_x = 0;
    scr_y++;
  } else if ((unsigned char)c < ' ') {
    return;  /* other control characters don't reach the screen */
  } else {
    VRAM[scr_y * COLS + scr_x] = c;
    if (++scr_x == COLS) { scr_x = 0; scr_y++; }
  }
  if (scr_y == ROWS) {
    for (i = 0; i < COLS * (ROWS - 1); i++) VRAM[i] = VRAM[i + COLS];
    for (; i < COLS * ROWS; i++) VRAM[i] = ' ';
    scr_y = ROWS - 1;
  }
  CURSOR_X = scr_x;
  CURSOR_Y = scr_y;
}

static void emit_char(char c) {
  if (c == '\n') acia_putc('\r');
  acia_putc(c);
  screen_putc(c);
}
#else
static void emit_char(char c) {
  *OUTPUT_ADDR = c;
}
#endif

static void emit_string(const char *s) {
  while (*s) emit_char(*s++);
}

/* emit_int using subtraction only - avoids broken div/mod in gcc6809 libgcc */
static void emit_int(int n) {
  int d, started;
  if (n < 0) { emit_char('-'); n = -n; }
  if (n == 0) { emit_char('0'); return; }
  started = 0;
  d = 0; while (n >= 10000) { n -= 10000; d++; }
  if (d) { emit_char('0' + d); started = 1; }
  d = 0; while (n >= 1000) { n -= 1000; d++; }
  if (d || started) { emit_char('0' + d); started = 1; }
  d = 0; while (n >= 100) { n -= 100; d++; }
  if (d || started) { emit_char('0' + d); started = 1; }
  d = 0; while (n >= 10) { n -= 10; d++; }
  if (d || started) { emit_char('0' + d); }
  emit_char('0' + n);
}

/* emit_long for 32-bit values using subtraction (avoids gcc6809 ICE with div/mod loop) */
static void emit_long(long n) {
  int neg = 0;
  int d, started = 0;

  /* Special case: INT_MIN cannot be negated without overflow */
  if (n == (-2147483647L - 1)) {
    emit_string("-2147483648");
    return;
  }

  if (n < 0) { neg = 1; n = -n; }
  if (neg) emit_char('-');
  if (n == 0) { emit_char('0'); return; }

  /* Billions */
  d = 0; while (n >= 1000000000L) { n -= 1000000000L; d++; }
  if (d) { emit_char('0' + d); started = 1; }
  /* Hundred millions */
  d = 0; while (n >= 100000000L) { n -= 100000000L; d++; }
  if (d || started) { emit_char('0' + d); started = 1; }
  /* Ten millions */
  d = 0; while (n >= 10000000L) { n -= 10000000L; d++; }
  if (d || started) { emit_char('0' + d); started = 1; }
  /* Millions */
  d = 0; while (n >= 1000000L) { n -= 1000000L; d++; }
  if (d || started) { emit_char('0' + d); started = 1; }
  /* Hundred thousands */
  d = 0; while (n >= 100000L) { n -= 100000L; d++; }
  if (d || started) { emit_char('0' + d); started = 1; }
  /* Ten thousands */
  d = 0; while (n >= 10000L) { n -= 10000L; d++; }
  if (d || started) { emit_char('0' + d); started = 1; }
  /* Thousands */
  d = 0; while (n >= 1000L) { n -= 1000L; d++; }
  if (d || started) { emit_char('0' + d); started = 1; }
  /* Hundreds */
  d = 0; while (n >= 100L) { n -= 100L; d++; }
  if (d || started) { emit_char('0' + d); started = 1; }
  /* Tens */
  d = 0; while (n >= 10L) { n -= 10L; d++; }
  if (d || started) { emit_char('0' + d); }
  /* Ones */
  emit_char('0' + (int)n);
}

/*
** Bytecode loading from memory
*/
#ifdef LUA_A8
#define BYTECODE_SIZE_ADDR ((volatile unsigned char*)0xD000)
#define BYTECODE_DATA_ADDR ((const char*)0xD002)
#else
#define BYTECODE_SIZE_ADDR ((volatile unsigned char*)0xE800)
#define BYTECODE_DATA_ADDR ((const char*)0xE802)
#endif

static int get_bytecode_size(void) {
  return (BYTECODE_SIZE_ADDR[0] << 8) | BYTECODE_SIZE_ADDR[1];
}

int main(void) {
  lua_State *L;
  int bc_size;
  int status;

  emit_string("Lua6809\n");

  bc_size = get_bytecode_size();
  if (bc_size == 0) {
    emit_string("No bytecode\n");
    return 1;
  }

  emit_string("BC:");
  emit_int(bc_size);
  emit_string(" bytes\n");

  L = luaL_newstate();
  if (L == NULL) {
    emit_string("OOM!\n");
    return 1;
  }

  status = luaL_loadbuffer(L, BYTECODE_DATA_ADDR, bc_size, "=mem");
  if (status != 0) {
    emit_string("Load err ");
    emit_int(status);
    emit_char(':');
    if (lua_isstring(L, -1)) {
      emit_string(lua_tostring(L, -1));
    }
    emit_char('\n');
    lua_close(L);
    return 1;
  }

  status = lua_pcall(L, 0, LUA_MULTRET, 0);
  if (status != 0) {
    emit_string("Run err:");
    if (lua_isstring(L, -1)) {
      emit_string(lua_tostring(L, -1));
    }
    emit_char('\n');
    lua_close(L);
    return 1;
  }

  if (lua_gettop(L) > 0) {
    emit_string("=> ");
    if (lua_isnil(L, -1)) {
      emit_string("nil");
    } else if (lua_isboolean(L, -1)) {
      emit_string(lua_toboolean(L, -1) ? "true" : "false");
    } else if (lua_isnumber(L, -1)) {
      emit_long(lua_tointeger(L, -1));
    } else if (lua_isstring(L, -1)) {
      emit_string(lua_tostring(L, -1));
    } else {
      /* TODO: table, function, userdata, thread not yet supported */
      emit_string("(unsupported type)");
    }
    emit_char('\n');
  }

  lua_close(L);
  return 0;
}
