{
  description = "Lua 5.1 for the MC6809";

  inputs = {
    gcc6809 = {
      url = "github:blark/gcc6809-nix";
      inputs.anachron8-emu.follows = "anachron8-emu";
    };
    nixpkgs.follows = "gcc6809/nixpkgs";
    nixpkgs-unstable.url = "github:NixOS/nixpkgs/nixpkgs-unstable";
    anachron8-emu = {
      url = "git+https://git.sherwood.haus/blark/anachron8-emu.git";
      inputs.nixpkgs.follows = "nixpkgs";
    };
  };

  outputs =
    {
      self,
      nixpkgs,
      gcc6809,
      nixpkgs-unstable,
      anachron8-emu,
      ...
    }:
    let
      systems = [
        "x86_64-linux"
        "aarch64-darwin"
      ];
      forAllSystems = nixpkgs.lib.genAttrs systems;

      # Shared derivations per system
      perSystem =
        system:
        let
          # The emulator's Python comes from the toolchain's nixpkgs, like
          # gcc6809-nix's and anachron8-sw's; unstable is for uv and nixfmt.
          pkgs = import nixpkgs {
            inherit system;
            overlays = [ anachron8-emu.overlays.default ];
          };
          pkgsUnstable = import nixpkgs-unstable { inherit system; };
          toolchain = gcc6809.packages.${system}.default;

          luaOriginal = pkgs.fetchzip {
            url = "https://www.lua.org/ftp/lua-5.1.5.tar.gz";
            sha256 = "sha256-QnrgndxTSEgkFPXiWQhkD4aVddWpLaVZIe/ya21WAeQ=";
          };

          luaSrc = pkgs.applyPatches {
            src = luaOriginal;
            patches = [ ./patches/6809-phase1.patch ];
          };

          luac-int32 = pkgs.stdenv.mkDerivation {
            pname = "luac-int32";
            version = "5.1.5";
            src = luaOriginal;
            patches = [ ./patches/luac-int32.patch ];
            buildPhase = "make -C src luac CC=cc MYCFLAGS='-O2'";
            installPhase = "mkdir -p $out/bin && cp src/luac $out/bin/luac-int32";
            meta = {
              description = "Lua compiler patched for 32-bit integers";
              license = pkgs.lib.licenses.mit;
              platforms = pkgs.lib.platforms.unix;
            };
          };

          lua6809-vm = pkgs.stdenv.mkDerivation {
            pname = "lua6809-vm";
            version = "5.1.5";
            src = luaSrc;
            nativeBuildInputs = [
              toolchain
              pkgs.gnumake
            ];
            postUnpack = "cp $sourceRoot/Makefile.6809 $sourceRoot/src/Makefile";
            buildFlags = [
              "-C"
              "src"
            ];
            preBuild = ''
              export CC=m6809-unknown-none-gcc
              export CFLAGS="-DLUA_USE_6809 -DLUA_CORE -I. -I${toolchain}/m6809-unknown-none/include -Os"
              export LDFLAGS="-L${toolchain}/m6809-unknown-none/lib -L${toolchain}/lib/gcc/m6809-unknown-none/4.3.6"
            '';
            installPhase = "mkdir -p $out && cp src/lua.s19 $out/";
            meta = {
              description = "Lua 5.1 VM for MC6809 processor";
              license = pkgs.lib.licenses.mit;
              platforms = [
                "x86_64-linux"
                "aarch64-darwin"
              ];
            };
          };

          # Same VM for the Anachron8 memory map (code at $1000, I/O at $0000,
          # MON09 ROM at $E000); see config/memory_layout.py.
          lua6809-vm-a8 = lua6809-vm.overrideAttrs (old: {
            pname = "lua6809-vm-a8";
            buildFlags = [
              "-C"
              "src"
              "TARGET=a8"
            ];
            installPhase = "mkdir -p $out && cp src/lua-a8.s19 src/lua-a8.map $out/";
            meta = old.meta // {
              description = "Lua 5.1 VM for the Anachron8 6809 computer";
            };
          });

          luac6809 = pkgs.writeShellScriptBin "luac6809" ''
            set -e
            OUT="" INPUT=""
            [ "$1" = "-o" ] && { OUT="$2"; shift 2; }
            INPUT="$1"
            [ -z "$OUT" ] && OUT="''${INPUT%.lua}.luac"
            TMPFILE=$(mktemp)
            trap "rm -f $TMPFILE" EXIT
            ${luac-int32}/bin/luac-int32 -o "$TMPFILE" "$INPUT"
            ${pkgs.python3}/bin/python3 ${./tools/luac_convert.py} "$TMPFILE" "$OUT"
          '';

          regen-patch = pkgs.writeShellScriptBin "regen-patch" ''
            set -e
            cd "$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
            [ ! -d lua-work/src ] && { echo "Error: lua-work/src not found"; exit 1; }
            echo "Creating patch from lua-work/..."
            diff -ruN -x obj-a8 -x '*.o' -x '*.s19' -x '*.map' "${luaOriginal}/src" lua-work/src \
              | sed "s|${luaOriginal}/src|a/src|g; s|lua-work/src|b/src|g" \
              > patches/6809-phase1.patch
            echo "" >> patches/6809-phase1.patch
            diff -ruN /dev/null lua-work/Makefile.6809 \
              | sed 's|/dev/null|a/Makefile.6809|; s|lua-work/Makefile.6809|b/Makefile.6809|' \
              >> patches/6809-phase1.patch
            echo "Done. Run 'direnv reload' to rebuild."
          '';

          mc6809 = pkgs.python3Packages.mc6809;
          pythonEnv = pkgs.python3.withPackages (ps: [ ps.anachron8-emu ]);

        in
        {
          inherit
            pkgs
            pkgsUnstable
            luaOriginal
            luaSrc
            luac-int32
            lua6809-vm
            lua6809-vm-a8
            luac6809
            regen-patch
            mc6809
            pythonEnv
            toolchain
            ;
        };

      # Cache perSystem results to avoid redundant evaluation
      cached = forAllSystems perSystem;

    in
    {
      packages = forAllSystems (
        system:
        let
          s = cached.${system};
        in
        {
          default = s.lua6809-vm;
          lua-original = s.luaOriginal;
          vm = s.lua6809-vm;
          vm-a8 = s.lua6809-vm-a8;
          luac = s.luac6809;
        }
      );

      apps = forAllSystems (
        system:
        let
          s = cached.${system};
          mkTest =
            name: vm: target:
            s.pkgs.writeShellScriptBin name ''
              export LUA6809_S19="${vm}"
              export LUA6809_TARGET="${target}"
              export PYTHONPATH="${s.pythonEnv}/${s.pythonEnv.sitePackages}:$PYTHONPATH"
              export PATH="${s.luac6809}/bin:$PATH"
              cd ${./.}
              ${s.pythonEnv}/bin/python3 ./run_tests.py "$@"
            '';
          testScript = mkTest "lua6809-test" "${s.lua6809-vm}/lua.s19" "sim";
          testA8Script = mkTest "lua6809-test-a8" "${s.lua6809-vm-a8}/lua-a8.s19" "anachron8";
        in
        {
          test = {
            type = "app";
            program = "${testScript}/bin/lua6809-test";
            meta.description = "Run Lua 6809 test suite";
          };
          test-a8 = {
            type = "app";
            program = "${testA8Script}/bin/lua6809-test-a8";
            meta.description = "Run the test suite on the Anachron8 build and memory model";
          };
        }
      );

      formatter = forAllSystems (system: cached.${system}.pkgsUnstable.nixfmt-rfc-style);

      devShells = forAllSystems (
        system:
        let
          s = cached.${system};
        in
        {
          default = s.pkgs.mkShell {
            packages = [
              s.toolchain
              s.pkgs.gnumake
              s.luac-int32
              s.luac6809
              s.regen-patch
              s.lua6809-vm
              s.pkgsUnstable.uv
              s.pythonEnv
              s.pkgs.srecord
            ];
            LUA_ORIGINAL_DIR = "${s.luaOriginal}";
            LUA_SRC_DIR = "${s.luaSrc}";
            LUA6809_S19 = "${s.lua6809-vm}/lua.s19";
            shellHook = ''
              echo "Lua 6809 Development Environment"
              echo ""
              echo "Commands:"
              echo "  luac6809 script.lua    Compile Lua to 6809 bytecode"
              echo "  regen-patch            Regenerate patch from lua-work/"
              echo "  nix run .#test         Run test suite"
              echo ""
              echo "Workflow: Edit lua-work/ -> regen-patch -> direnv reload"
            '';
          };
        }
      );
    };
}
