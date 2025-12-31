#!/usr/bin/env python3
"""
Lua 6809 Test Suite Runner

Runs all .lua files in tests/lua/, compiles them with luac6809,
executes in the MC6809 emulator, and compares output to expected values.

Each test file should have a comment like:
  -- EXPECT: 42

Usage:
  python run_tests.py              # Run all tests
  python run_tests.py -v           # Verbose output
  python run_tests.py tests/lua/arithmetic/add.lua  # Run single test
"""

import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

# Colors for terminal output
GREEN = '\033[92m'
RED = '\033[91m'
YELLOW = '\033[93m'
RESET = '\033[0m'
BOLD = '\033[1m'


def extract_expected(lua_file: Path) -> str | None:
    """Extract expected value from -- EXPECT: comment"""
    with open(lua_file) as f:
        for line in f:
            match = re.match(r'--\s*EXPECT:\s*(.+)', line)
            if match:
                return match.group(1).strip()
    return None


def compile_lua(lua_file: Path, output_file: Path) -> tuple[bool, str]:
    """Compile .lua to .luac using luac6809"""
    try:
        result = subprocess.run(
            ['luac6809', '-o', str(output_file), str(lua_file)],
            capture_output=True,
            text=True,
            timeout=30
        )
        if result.returncode != 0:
            return False, result.stderr
        return True, ""
    except FileNotFoundError:
        return False, "luac6809 not found (run from nix develop shell)"
    except subprocess.TimeoutExpired:
        return False, "Compilation timed out"


def run_emulator(luac_file: Path, timeout: int = 60) -> tuple[bool, str, str]:
    """Run bytecode in MC6809 emulator, return (success, output, error)"""
    script_dir = Path(__file__).parent
    test_script = script_dir / 'emu' / 'runner.py'

    try:
        result = subprocess.run(
            [sys.executable, str(test_script), str(luac_file)],
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=str(script_dir)
        )
        return True, result.stdout, result.stderr
    except subprocess.TimeoutExpired:
        return False, "", "Emulator timed out"
    except Exception as e:
        return False, "", str(e)


def run_test(lua_file: Path, verbose: bool = False) -> tuple[bool, str]:
    """Run a single test, return (passed, message)"""
    expected = extract_expected(lua_file)
    if expected is None:
        return False, "No EXPECT comment found"

    with tempfile.NamedTemporaryFile(suffix='.luac', delete=False) as tmp:
        luac_file = Path(tmp.name)

    try:
        # Compile
        ok, err = compile_lua(lua_file, luac_file)
        if not ok:
            return False, f"Compile error: {err}"

        # Determine timeout based on test (recursion tests need more time)
        timeout = 120 if 'recursion' in str(lua_file) else 60

        # Run
        ok, stdout, stderr = run_emulator(luac_file, timeout=timeout)
        if not ok:
            return False, f"Run error: {stderr}"

        # Check for errors
        if stderr and 'ERROR:' in stderr:
            return False, stderr.strip()

        # Get result (test_runner.py outputs just the value)
        actual = stdout.strip()
        if not actual:
            if verbose:
                print(f"    stderr: {stderr[:200]}")
            return False, "No output produced"

        # Compare
        if actual == expected:
            return True, f"{actual}"
        else:
            return False, f"Expected {expected}, got {actual}"

    finally:
        if luac_file.exists():
            luac_file.unlink()


def find_tests(path: Path) -> list[Path]:
    """Find all .lua test files"""
    if path.is_file():
        return [path]
    return sorted(path.rglob('*.lua'))


def main():
    verbose = '-v' in sys.argv or '--verbose' in sys.argv
    args = [a for a in sys.argv[1:] if not a.startswith('-')]

    # Find test directory
    script_dir = Path(__file__).parent
    test_dir = script_dir / 'tests' / 'lua'

    if args:
        test_files = []
        for arg in args:
            p = Path(arg)
            if p.exists():
                test_files.extend(find_tests(p))
    else:
        if not test_dir.exists():
            print(f"Test directory not found: {test_dir}")
            sys.exit(1)
        test_files = find_tests(test_dir)

    if not test_files:
        print("No test files found")
        sys.exit(1)

    print(f"{BOLD}Lua 6809 Test Suite{RESET}")
    print(f"Running {len(test_files)} tests...\n")

    passed = 0
    failed = 0
    skipped = 0

    # Group by directory
    by_dir: dict[str, list[Path]] = {}
    for f in test_files:
        rel = f.relative_to(test_dir) if str(f).startswith(str(test_dir)) else f
        dir_name = str(rel.parent) if rel.parent != Path('.') else 'root'
        by_dir.setdefault(dir_name, []).append(f)

    for dir_name, files in sorted(by_dir.items()):
        print(f"{BOLD}{dir_name}/{RESET}")
        for lua_file in files:
            test_name = lua_file.stem

            ok, msg = run_test(lua_file, verbose=verbose)

            if ok:
                print(f"  {GREEN}PASS{RESET} {test_name}: {msg}")
                passed += 1
            else:
                print(f"  {RED}FAIL{RESET} {test_name}: {msg}")
                failed += 1
        print()

    # Summary
    print(f"{BOLD}Results:{RESET}")
    print(f"  {GREEN}Passed: {passed}{RESET}")
    if failed:
        print(f"  {RED}Failed: {failed}{RESET}")
    print(f"  Total:  {passed + failed}")

    sys.exit(0 if failed == 0 else 1)


if __name__ == '__main__':
    main()
