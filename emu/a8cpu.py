"""
The MC6809 package's CPU with what it lacks for an operating system.

MC6809 0.6/0.9 (DragonPy's core) leaves SWI, SWI2, SWI3 and SYNC raising
NotImplementedError, makes CWAI a no-op (not even the AND), has an irq()
that neither sets E nor I (it pushes only PC and CC unless E happened to be
set) and no FIRQ or NMI, and pushes or pulls U instead of S for bit 6 of
PSHU/PULU. A8CPU fixes those and adds interrupt lines sampled before every
instruction:

  irq_source   callable, True while the IRQ line is asserted (level)
  firq_line    bool, the FIRQ line (level)
  nmi()        latch an NMI (edge)

A memory object may provide `next_event` (the CPU cycle of its next timed
event) and `advance(cycles)`: step() calls advance() once cycles reach
next_event, before sampling the lines. While CWAI or SYNC waits, step()
skips the cycle count straight to next_event.

The package counts cycles from its opcode table (plus what the addressing
code and the Anachron8 memory add), not exactly the 6809's; good enough to
drive a 60 Hz timer.
"""

from collections import deque

from MC6809.components.cpu6809 import CPU
from MC6809.components.cpu_utils.instruction_caller import opcode

NEVER = float("inf")
CC_E, CC_F, CC_I = 0x80, 0x40, 0x10


class Idle(Exception):
    """CWAI or SYNC waits and nothing will ever wake it."""


class A8CPU(CPU):
    def __init__(self, memory, cfg):
        super().__init__(memory, cfg)
        self.irq_source = getattr(memory, "irq_line", lambda: False)
        self.firq_line = False
        self._nmi = False
        self.waiting = None          # None, "cwai" or "sync"
        self.trace = None            # deque of trace entries (enable_trace)
        self.interrupts = {"irq": 0, "firq": 0, "nmi": 0}

    # --- interrupts ---------------------------------------------------------
    def nmi(self):
        self._nmi = True

    def irq(self):
        """Take an IRQ now if I is clear (the package's irq(), done right)."""
        if not self.I:
            self._take("irq")

    def _vector(self, vector, mask):
        self.program_counter.set(self.memory.read_word(vector))
        if mask & CC_I:
            self.I = 1
        if mask & CC_F:
            self.F = 1

    def _push_entire(self):
        self.E = 1
        self.push_irq_registers()

    def _take(self, kind):
        stacked = self.waiting == "cwai"      # CWAI has pushed the entire state already
        self.waiting = None
        self.interrupts[kind] += 1
        if kind == "nmi":
            self._nmi = False
            if not stacked:
                self._push_entire()
            self._vector(self.NMI_VECTOR, CC_I | CC_F)
            self.cycles += 19
        elif kind == "firq":
            if not stacked:
                self.E = 0
                self.push_firq_registers()
            self._vector(self.FIRQ_VECTOR, CC_I | CC_F)
            self.cycles += 10
        else:
            if not stacked:
                self._push_entire()
            self._vector(self.IRQ_VECTOR, CC_I)
            self.cycles += 19

    def _pending(self):
        """The interrupt the CPU takes now, or None (NMI, FIRQ, IRQ priority)."""
        if self._nmi:
            return "nmi"
        if self.firq_line and not self.F:
            return "firq"
        if not self.I and self.irq_source():
            return "irq"
        return None

    def step(self):
        """Advance the devices, take a pending interrupt, run one instruction."""
        memory = self.memory
        if self.cycles >= getattr(memory, "next_event", NEVER):
            memory.advance(self.cycles)
        kind = self._pending()
        if self.waiting:
            if kind is None and self.waiting == "sync" and (self.firq_line or self.irq_source()):
                self.waiting = None           # SYNC: a masked line only ends the wait
            elif kind is None:
                nxt = getattr(memory, "next_event", NEVER)
                if nxt == NEVER:
                    raise Idle(f"{self.waiting.upper()} at ${self.last_op_address:04X} waits forever")
                self.cycles = max(self.cycles + 1, nxt)
                return
        if kind:
            self._take(kind)
        if self.trace is not None:
            self.trace.append(self._trace_entry())
        self.get_and_call_next_op()

    def run_for(self, count, until_pc=None, until=None):
        """Step up to `count` times; stop early at PC `until_pc` or when until(cpu).

        Returns (reason, steps): reason "count", "pc" or "until".
        """
        pc = self.program_counter
        step = self.step
        for n in range(count):
            if pc.value == until_pc:
                return "pc", n
            if until is not None and until(self):
                return "until", n
            step()
        return "count", count

    # --- tracing --------------------------------------------------------------
    def enable_trace(self, size=256):
        self.trace = deque(maxlen=size)

    def _trace_entry(self):
        m = self.memory
        return (self.cycles, getattr(m, "task", 0), self.program_counter.value,
                self.accu_a.value, self.accu_b.value, self.index_x.value, self.index_y.value,
                self.user_stack_pointer.value, self.system_stack_pointer.value,
                self.direct_page.value, self.get_cc_value())

    def format_trace(self):
        lines = []
        for cyc, task, pc, a, b, x, y, u, s, dp, cc in self.trace or ():
            try:
                code = " ".join(f"{self.memory.read_byte((pc + i) & 0xFFFF):02X}" for i in range(4))
            except Exception:   # an I/O address with side effects or a bus error
                code = "?? ?? ?? ??"
            lines.append(f"{cyc:>11} t{task} {pc:04X}: {code}  A={a:02X} B={b:02X} X={x:04X} "
                         f"Y={y:04X} U={u:04X} S={s:04X} DP={dp:02X} CC={cc:02X}")
        return "\n".join(lines)

    # --- the missing and broken instructions ---------------------------------------
    @opcode(0x3f)  # SWI (inherent)
    def instruction_SWI(self, opcode):
        self._push_entire()
        self._vector(self.SWI_VECTOR, CC_I | CC_F)

    @opcode(0x103f)  # SWI2 (inherent): the OS-9 system call
    def instruction_SWI2(self, opcode):
        self._push_entire()
        self._vector(self.SWI2_VECTOR, 0)

    @opcode(0x113f)  # SWI3 (inherent)
    def instruction_SWI3(self, opcode):
        self._push_entire()
        self._vector(self.SWI3_VECTOR, 0)

    @opcode(0x3c)  # CWAI #imm
    def instruction_CWAI(self, opcode, m):
        self.set_cc(self.get_cc_value() & m)
        self._push_entire()
        self.waiting = "cwai"

    @opcode(0x13)  # SYNC (inherent)
    def instruction_SYNC(self, opcode):
        self.waiting = "sync"

    def _other_stack(self, register):
        return self.user_stack_pointer if register is self.system_stack_pointer else self.system_stack_pointer

    @opcode(0x34, 0x36)  # PSHS, PSHU: bit 6 is the other stack pointer
    def instruction_PSH(self, opcode, m, register):
        if m & 0x80:
            self.push_word(register, self.program_counter.value)
        if m & 0x40:
            self.push_word(register, self._other_stack(register).value)
        if m & 0x20:
            self.push_word(register, self.index_y.value)
        if m & 0x10:
            self.push_word(register, self.index_x.value)
        if m & 0x08:
            self.push_byte(register, self.direct_page.value)
        if m & 0x04:
            self.push_byte(register, self.accu_b.value)
        if m & 0x02:
            self.push_byte(register, self.accu_a.value)
        if m & 0x01:
            self.push_byte(register, self.get_cc_value())

    @opcode(0x35, 0x37)  # PULS, PULU
    def instruction_PUL(self, opcode, m, register):
        if m & 0x01:
            self.set_cc(self.pull_byte(register))
        if m & 0x02:
            self.accu_a.set(self.pull_byte(register))
        if m & 0x04:
            self.accu_b.set(self.pull_byte(register))
        if m & 0x08:
            self.direct_page.set(self.pull_byte(register))
        if m & 0x10:
            self.index_x.set(self.pull_word(register))
        if m & 0x20:
            self.index_y.set(self.pull_word(register))
        if m & 0x40:
            self._other_stack(register).set(self.pull_word(register))
        if m & 0x80:
            self.program_counter.set(self.pull_word(register))
