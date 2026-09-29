#!/bin/sh
# Assemble the test programs for tests/emu/test_os9_devices.py into .s19
# (committed, so the tests need no toolchain). From the repo root:
#   nix develop -c sh tests/emu/asm/build.sh
set -e
cd "$(dirname "$0")"
for s in tick kbd dw cpu; do
    as6809 -o $s.s
    aslink -n -s $s.s19 $s.rel >/dev/null
    rm -f $s.rel $s.hlr
done
ls -l *.s19
