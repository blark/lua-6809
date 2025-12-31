{
  description = "Lua 5.1 for the MC6809";

  inputs = {
    gcc6809.url = "github:blark/gcc6809-nix";
    nixpkgs.follows = "gcc6809/nixpkgs";
    nixpkgs-unstable.url = "github:NixOS/nixpkgs/nixpkgs-unstable";
  };

  outputs = { self, nixpkgs, gcc6809, nixpkgs-unstable, ... }:
    let
      systems = [ "x86_64-linux" "aarch64-darwin" ];
      forAllSystems = nixpkgs.lib.genAttrs systems;

      # Shared derivations per system
      perSystem = system:
        let
          pkgs = import nixpkgs { inherit system; };
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
            nativeBuildInputs = [ toolchain pkgs.gnumake ];
            postUnpack = "cp $sourceRoot/Makefile.6809 $sourceRoot/src/Makefile";
            buildFlags = [ "-C" "src" ];
            preBuild = ''
              export CC=m6809-unknown-none-gcc
              export CFLAGS="-DLUA_USE_6809 -DLUA_CORE -I. -I${toolchain}/m6809-unknown-none/include -Os"
              export LDFLAGS="-L${toolchain}/m6809-unknown-none/lib -L${toolchain}/lib/gcc/m6809-unknown-none/4.3.6"
            '';
            installPhase = "mkdir -p $out && cp src/lua.s19 $out/";
            meta = {
              description = "Lua 5.1 VM for MC6809 processor";
              license = pkgs.lib.licenses.mit;
              platforms = [ "x86_64-linux" "aarch64-darwin" ];
            };
          };

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
            diff -ruN "${luaOriginal}/src" lua-work/src \
              | sed "s|${luaOriginal}/src|a/src|g; s|lua-work/src|b/src|g" \
              > patches/6809-phase1.patch
            echo "" >> patches/6809-phase1.patch
            diff -ruN /dev/null lua-work/Makefile.6809 \
              | sed 's|/dev/null|a/Makefile.6809|; s|lua-work/Makefile.6809|b/Makefile.6809|' \
              >> patches/6809-phase1.patch
            echo "Done. Run 'direnv reload' to rebuild."
          '';

          mc6809 = pkgsUnstable.python3Packages.buildPythonPackage rec {
            pname = "MC6809";
            version = "0.6.0";
            src = pkgsUnstable.fetchPypi {
              inherit pname version;
              sha256 = "sha256-Q5DNA+RmMmSR2I33WLIsVWyZJpknWBK820dL41Bgd74=";
            };
            pyproject = true;
            build-system = [ pkgsUnstable.python3Packages.poetry-core ];
            dependencies = [ pkgsUnstable.python3Packages.click ];
            postPatch = ''
              substituteInPlace pyproject.toml \
                --replace-fail 'poetry.masonry.api' 'poetry.core.masonry.api' \
                --replace-fail 'poetry>=0.12' 'poetry-core>=1.0.0'
            '';
            doCheck = false;
            dontCheckRuntimeDeps = true;
            meta = {
              description = "MC6809 CPU emulator written in Python";
              license = pkgsUnstable.lib.licenses.gpl3;
              platforms = pkgsUnstable.lib.platforms.unix;
            };
          };

          pythonEnv = pkgsUnstable.python3.withPackages (_: [ mc6809 ]);

        in {
          inherit pkgs pkgsUnstable luaOriginal luaSrc luac-int32 lua6809-vm
                  luac6809 regen-patch mc6809 pythonEnv toolchain;
        };

      # Cache perSystem results to avoid redundant evaluation
      cached = forAllSystems perSystem;

    in {
      packages = forAllSystems (system:
        let s = cached.${system}; in {
          default = s.lua6809-vm;
          lua-original = s.luaOriginal;
          vm = s.lua6809-vm;
          luac = s.luac6809;
        });

      apps = forAllSystems (system:
        let
          s = cached.${system};
          testScript = s.pkgs.writeShellScriptBin "lua6809-test" ''
            export LUA6809_S19="${s.lua6809-vm}/lua.s19"
            export PYTHONPATH="${s.pythonEnv}/${s.pythonEnv.sitePackages}:$PYTHONPATH"
            export PATH="${s.luac6809}/bin:$PATH"
            cd ${./.}
            ${s.pythonEnv}/bin/python3 ./run_tests.py "$@"
          '';
        in {
          test = {
            type = "app";
            program = "${testScript}/bin/lua6809-test";
            meta.description = "Run Lua 6809 test suite";
          };
        });

      devShells = forAllSystems (system:
        let s = cached.${system}; in {
          default = s.pkgs.mkShell {
            packages = [
              s.toolchain
              s.pkgs.gnumake
              s.luac-int32
              s.luac6809
              s.regen-patch
              s.lua6809-vm
              s.pkgsUnstable.uv
              s.pkgsUnstable.python3
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
        });
    };
}
