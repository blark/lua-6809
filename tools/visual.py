#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.9"
# dependencies = [
#   "MC6809",
#   "textual",
#   "tree-sitter",
#   "tree-sitter-lua",
# ]
# ///
"""
Visual emulator for Lua VM on MC6809 using Textual TUI.

Usage: uv run test_lua_6809_visual.py [bytecode.luac]
"""

import os
import subprocess
import sys
from pathlib import Path

# Add project root to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import Static, Footer, ProgressBar, Label, TextArea
from textual.binding import Binding
from textual.message import Message
import tree_sitter
import tree_sitter_lua

from MC6809.components.cpu6809 import CPU

from config.memory_layout import (
    BYTECODE_SIZE_ADDR, BYTECODE_DATA_ADDR, BYTECODE_MAX_SIZE,
    HEAP_START, HEAP_END, OUTPUT_ADDR, STACK_BOTTOM, STACK_TOP,
    Lua6809Config, Memory64K
)


def parse_s19(filename):
    """Parse Motorola S-record file."""
    data = {}
    start_addr = None
    with open(filename, 'r') as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            if line[:2] == 'S1':
                byte_count = int(line[2:4], 16)
                addr = int(line[4:8], 16)
                data_hex = line[8:8 + (byte_count - 3) * 2]
                for i in range(0, len(data_hex), 2):
                    data[addr] = int(data_hex[i:i+2], 16)
                    addr += 1
            elif line[:2] == 'S9':
                start_addr = int(line[4:8], 16)
    return data, start_addr


class Emulator:
    """MC6809 emulator for Lua VM."""

    def __init__(self, s19_path=None):
        if s19_path is None:
            s19_path = os.environ.get('LUA6809_S19', '/tmp/lua.s19')
        self.rom_data, self.start_addr = parse_s19(s19_path)
        self.code_end = max(self.rom_data.keys())
        self.cfg = Lua6809Config({"verbosity": None, "trace": None})
        self.current_file = None
        self.instructions = 0
        self.status = "Ready"
        self.reset()

    def reset(self):
        self.memory = Memory64K(self.cfg, track_usage=True)
        self.cpu = CPU(self.memory, self.cfg)
        self.memory.cpu = self.cpu  # Enable cycle counting
        for addr, byte in self.rom_data.items():
            self.memory._mem[addr] = byte
        for i in range(256):
            self.memory._mem[BYTECODE_SIZE_ADDR + i] = 0
        self.cpu.system_stack_pointer.set(STACK_TOP)
        self.cpu.user_stack_pointer.set(STACK_TOP - 0x100)
        self.cpu.program_counter.set(self.start_addr)
        self.instructions = 0
        self.status = "Ready"

    def load_bytecode(self, data: bytes):
        size = len(data)
        self.memory._mem[BYTECODE_SIZE_ADDR] = (size >> 8) & 0xFF
        self.memory._mem[BYTECODE_SIZE_ADDR + 1] = size & 0xFF
        for i, b in enumerate(data):
            self.memory._mem[BYTECODE_DATA_ADDR + i] = b

    def load_file(self, path: str):
        with open(path, 'rb') as f:
            self.load_bytecode(f.read())
        self.current_file = path

    def step(self, count=1000) -> bool:
        """Run count instructions. Returns True if still running."""
        last_pc = None
        loop_count = 0
        for _ in range(count):
            pc = self.cpu.program_counter.value
            if pc == last_pc:
                loop_count += 1
                if loop_count > 10:
                    self.status = f"Halted ${pc:04X}"
                    return False
            else:
                loop_count = 0
            last_pc = pc
            self.cpu.get_and_call_next_op()
            self.instructions += 1
        self.status = "Running"
        return True


class RegistersWidget(Static):
    """Display CPU registers."""

    def render(self) -> str:
        emu = self.app.emu
        cpu = emu.cpu
        cc = cpu.cc_register.value
        flags = ""
        for i, (bit, name) in enumerate([
            (0x80, 'E'), (0x40, 'F'), (0x20, 'H'), (0x10, 'I'),
            (0x08, 'N'), (0x04, 'Z'), (0x02, 'V'), (0x01, 'C')
        ]):
            if cc & bit:
                flags += f"[yellow]{name}[/]"
            else:
                flags += f"[#444]{name}[/]"
        dp = cpu.direct_page.value if hasattr(cpu, 'direct_page') else 0
        return (
            f"[cyan]PC[/] [bold]${cpu.program_counter.value:04X}[/]  "
            f"[cyan]S[/] ${cpu.system_stack_pointer.value:04X}\n"
            f"[cyan]A[/]  ${cpu.accu_a.value:02X}  "
            f"[cyan]B[/] ${cpu.accu_b.value:02X}  "
            f"[cyan]D[/] ${cpu.accu_d.value:04X}\n"
            f"[cyan]X[/]  ${cpu.index_x.value:04X}  "
            f"[cyan]Y[/] ${cpu.index_y.value:04X}\n"
            f"[cyan]U[/]  ${cpu.user_stack_pointer.value:04X}  "
            f"[cyan]DP[/] ${dp:02X}\n"
            f"[cyan]CC[/] ${cc:02X} {flags}\n"
            f"\n"
            f"[#666]Cycles[/] [cyan]{cpu.cycles:,}[/]"
        )


class MemoryRowWidget(Horizontal):
    """A single row showing memory region with progress bar."""

    DEFAULT_CSS = """
    MemoryRowWidget {
        height: 1;
        width: 100%;
    }
    MemoryRowWidget .mem-label {
        width: 5;
    }
    MemoryRowWidget .mem-start {
        width: 6;
        color: #666;
    }
    MemoryRowWidget ProgressBar {
        width: 24;
        padding: 0;
    }
    MemoryRowWidget .mem-end {
        width: 6;
        color: #666;
    }
    MemoryRowWidget .mem-stats {
        width: 1fr;
        color: cyan;
    }
    """

    def __init__(self, name: str, color: str, region_id: str, **kwargs):
        super().__init__(**kwargs)
        self.region_name = name
        self.region_color = color
        self.region_id = region_id

    def compose(self) -> ComposeResult:
        yield Label(f"[{self.region_color}]{self.region_name}[/]", classes="mem-label")
        yield Label("$0000", classes="mem-start", id=f"{self.region_id}-start")
        yield ProgressBar(total=100, show_eta=False, show_percentage=False, id=f"{self.region_id}-bar")
        yield Label("$0000", classes="mem-end", id=f"{self.region_id}-end")
        yield Label("0/0", classes="mem-stats", id=f"{self.region_id}-stats")


class MemoryWidget(Vertical):
    """Display memory map with usage bars."""

    DEFAULT_CSS = """
    MemoryWidget {
        height: auto;
    }
    MemoryWidget ProgressBar Bar {
        width: 1fr;
    }
    MemoryWidget ProgressBar PercentageStatus {
        display: none;
    }
    MemoryWidget ProgressBar ETAStatus {
        display: none;
    }
    MemoryWidget #code-bar Bar > .bar--bar {
        color: magenta;
    }
    MemoryWidget #bc-bar Bar > .bar--bar {
        color: yellow;
    }
    MemoryWidget #heap-bar Bar > .bar--bar {
        color: green;
    }
    MemoryWidget #stack-bar Bar > .bar--bar {
        color: dodgerblue;
    }
    MemoryWidget .gap-row {
        height: 1;
    }
    """

    def compose(self) -> ComposeResult:
        yield MemoryRowWidget("Code", "magenta", "code")
        yield MemoryRowWidget("BC", "yellow", "bc")
        yield MemoryRowWidget("Heap", "green", "heap")
        yield MemoryRowWidget("Stk", "dodgerblue", "stack")
        yield Label("[#888]Gap[/] [cyan]0[/]", classes="gap-row", id="gap-label")

    def update_memory(self, emu) -> None:
        """Update all memory bars and labels."""
        mem = emu.memory
        code_end = emu.code_end
        code_total = BYTECODE_SIZE_ADDR

        # Code region
        code_pct = 100 * code_end / code_total if code_total else 0
        self.query_one("#code-bar", ProgressBar).update(progress=code_pct)
        self.query_one("#code-start", Label).update("$0000")
        self.query_one("#code-end", Label).update(f"${code_end:04X}")
        self.query_one("#code-stats", Label).update(f"{code_end:,}/{code_total:,}")

        # Bytecode region
        bc_size = (mem._mem[BYTECODE_SIZE_ADDR] << 8) | mem._mem[BYTECODE_SIZE_ADDR + 1]
        bc_total = HEAP_START - BYTECODE_SIZE_ADDR
        bc_pct = 100 * bc_size / bc_total if bc_total else 0
        self.query_one("#bc-bar", ProgressBar).update(progress=bc_pct)
        self.query_one("#bc-start", Label).update(f"${BYTECODE_SIZE_ADDR:04X}")
        self.query_one("#bc-end", Label).update(f"${HEAP_START:04X}")
        self.query_one("#bc-stats", Label).update(f"{bc_size:,}/{bc_total:,}")

        # Heap region
        heap_used = mem.heap_high_water - HEAP_START
        heap_total = HEAP_END - HEAP_START
        heap_pct = 100 * heap_used / heap_total if heap_total else 0
        self.query_one("#heap-bar", ProgressBar).update(progress=heap_pct)
        self.query_one("#heap-start", Label).update(f"${HEAP_START:04X}")
        self.query_one("#heap-end", Label).update(f"${HEAP_END:04X}")
        self.query_one("#heap-stats", Label).update(f"{heap_used:,}/{heap_total:,}")

        # Stack region
        stack_used = STACK_TOP - mem.stack_low_water
        stack_total = STACK_TOP - STACK_BOTTOM
        stack_pct = 100 * stack_used / stack_total if stack_total else 0
        self.query_one("#stack-bar", ProgressBar).update(progress=stack_pct)
        self.query_one("#stack-start", Label).update(f"${STACK_BOTTOM:04X}")
        self.query_one("#stack-end", Label).update(f"${STACK_TOP:04X}")
        self.query_one("#stack-stats", Label).update(f"{stack_used:,}/{stack_total:,}")

        # Gap
        gap = mem.stack_low_water - mem.heap_high_water
        gap_warn = " [red]![/]" if gap < 256 else ""
        self.query_one("#gap-label", Label).update(f"[#888]Gap[/] [cyan]{gap:,}[/]{gap_warn}")


class StackWidget(Static):
    """Display stack contents."""

    def render(self) -> str:
        emu = self.app.emu
        sp = emu.cpu.system_stack_pointer.value
        lines = []
        for i in range(7):
            addr = sp + i * 2
            if addr < 0xFFFF:
                val = (emu.memory._mem[addr] << 8) | emu.memory._mem[addr + 1]
                if i == 0:
                    lines.append(f"[yellow]→[/][#888]${addr:04X}[/] [bold]${val:04X}[/]")
                else:
                    lines.append(f" [#888]${addr:04X}[/] ${val:04X}")
        return "\n".join(lines)


class OutputWidget(Static):
    """Display console output."""

    def render(self) -> str:
        output = "".join(self.app.emu.memory.console_output)
        lines = output.split('\n')[-8:]
        return "\n".join(lines) if lines else "(no output)"


class StatusWidget(Static):
    """Display status bar."""

    def render(self) -> str:
        emu = self.app.emu
        file_info = Path(emu.current_file).name if emu.current_file else "(none)"
        status_color = "green" if emu.status == "Running" else "yellow" if "Halted" in emu.status else "white"
        return (
            f"[{status_color}]{emu.status}[/] │ "
            f"[cyan]{emu.instructions:,}[/] insns │ "
            f"[cyan]{emu.cpu.cycles:,}[/] cycles │ "
            f"[magenta]{file_info}[/]"
        )


class CodeInput(TextArea):
    """Multi-line Lua code input."""

    BINDINGS = [
        Binding("ctrl+a", "select_all", "Select All", show=False),
    ]

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.show_line_numbers = True
        self.register_language("lua", tree_sitter.Language(tree_sitter_lua.language()), tree_sitter_lua.HIGHLIGHTS_QUERY)
        self.language = "lua"

    def action_select_all(self) -> None:
        """Select all text."""
        self.select_all()


class EmulatorApp(App):
    """Textual app for the 6809 emulator."""

    CSS = """
    #top { height: 9; }
    #registers {
        width: 28;
        border: round #666;
        border-title-color: #0af;
        padding: 0 1;
    }
    #memory {
        width: 1fr;
        border: round #666;
        border-title-color: #0af;
        padding: 0 1;
    }
    #stack {
        width: 20;
        border: round #666;
        border-title-color: #0af;
        padding: 0 1;
    }
    #output {
        height: 1fr;
        border: round #666;
        border-title-color: #0f0;
        padding: 0 1;
    }
    #status {
        height: 1;
        background: #1a1a2e;
        color: #888;
        padding: 0 1;
    }
    #input-area { height: 12; }
    #code-input {
        width: 100%;
        height: 100%;
        border: round #444;
    }
    """

    BINDINGS = [
        Binding("ctrl+r", "run", "Run", show=True),
        Binding("ctrl+q", "quit", "Quit", show=True),
        Binding("escape", "quit", "Quit"),
    ]

    def __init__(self, bytecode_file=None):
        super().__init__()
        self.emu = Emulator()
        self.bytecode_file = bytecode_file
        self.running = False

    def compose(self) -> ComposeResult:
        with Horizontal(id="top"):
            reg = RegistersWidget(id="registers")
            reg.border_title = "Registers"
            yield reg
            mem = MemoryWidget(id="memory")
            mem.border_title = "Memory"
            yield mem
            stk = StackWidget(id="stack")
            stk.border_title = "Stack"
            yield stk
        with Vertical(id="input-area"):
            code = CodeInput(id="code-input")
            code.border_title = "Lua Code (Ctrl+R to run)"
            yield code
        out = OutputWidget(id="output")
        out.border_title = "Console Output"
        yield out
        yield StatusWidget(id="status")
        yield Footer()

    def on_mount(self) -> None:
        # Initial memory display update
        self.query_one("#memory", MemoryWidget).update_memory(self.emu)
        if self.bytecode_file:
            self.load_and_run(self.bytecode_file)

    def load_and_run(self, path: str) -> None:
        try:
            self.emu.reset()
            self.emu.load_file(path)
            self.run_emulator()
        except FileNotFoundError:
            self.emu.status = f"Not found: {path}"
            self.refresh_display()

    def run_emulator(self) -> None:
        self.running = True
        self.emu.status = "Running"
        self.refresh_display()
        self.set_timer(0.01, self.step_emulator)

    def step_emulator(self) -> None:
        if not self.running:
            return
        if self.emu.step(2000):
            self.refresh_display()
            self.set_timer(0.01, self.step_emulator)
        else:
            self.running = False
            self.refresh_display()

    def refresh_display(self) -> None:
        self.query_one("#registers", RegistersWidget).refresh()
        self.query_one("#memory", MemoryWidget).update_memory(self.emu)
        self.query_one("#stack", StackWidget).refresh()
        self.query_one("#output", OutputWidget).refresh()
        self.query_one("#status", StatusWidget).refresh()


    def compile_and_run(self, code: str) -> None:
        """Compile Lua code with luac6809 and run it."""
        luac_output = "/tmp/stdin.luac"
        try:
            result = subprocess.run(
                ["luac6809", "-o", luac_output, "-"],
                input=code,
                capture_output=True,
                text=True,
                timeout=5
            )
            if result.returncode != 0:
                self.emu.status = f"Compile error"
                self.emu.memory.console_output = list(result.stderr or "Unknown error")
                self.refresh_display()
                return
            self.load_and_run(luac_output)
        except FileNotFoundError:
            self.emu.status = "luac6809 not found"
            self.refresh_display()
        except subprocess.TimeoutExpired:
            self.emu.status = "Compile timeout"
            self.refresh_display()

    def action_run(self) -> None:
        code = self.query_one("#code-input", CodeInput).text.strip()
        if code:
            self.compile_and_run(code)
        elif self.emu.current_file:
            self.load_and_run(self.emu.current_file)

    def action_quit(self) -> None:
        self.exit()


def main():
    bytecode = sys.argv[1] if len(sys.argv) > 1 else None
    app = EmulatorApp(bytecode)
    app.run()


if __name__ == '__main__':
    main()
