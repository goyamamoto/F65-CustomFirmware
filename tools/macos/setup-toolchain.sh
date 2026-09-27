#!/bin/sh
# Build the smk toolchain on macOS without Nix: SDCC 4.5.0 (the version smk's
# flake.lock pins, with the same aslink patch as nixpkgs) and the uCsim simulator
# patched for the SH68F90. Installs under ~/.local/smk and writes ~/.local/smk/env.sh.
#
# Usage: tools/macos/setup-toolchain.sh [path to this repository]   (default: the checkout this script is in)
set -eu

smk=$(cd "${1:-$(dirname "$0")/../..}" && pwd)
root="$HOME/.local/smk"
mkdir -p "$root/bin"
cd "$root"

brew install boost bison flex meson ninja
export PATH="/opt/homebrew/opt/bison/bin:/opt/homebrew/opt/flex/bin:$PATH"

tarball=sdcc-src-4.5.0.tar.bz2
[ -f "$tarball" ] || curl -sL -o "$tarball" "https://sourceforge.net/projects/sdcc/files/sdcc/4.5.0/$tarball/download"
python3 - "$tarball" <<'PY'
import base64, hashlib, sys
digest = base64.b64encode(hashlib.sha256(open(sys.argv[1], "rb").read()).digest()).decode()
sys.exit(0 if digest == "1QMEN/tDa7HZOo29v7RrqqYGEzGPT7P1hx1ygV0e7YA=" else "sdcc tarball hash mismatch")
PY
curl -sL -o sdcc-4.4.0-aslink.patch \
    "https://src.fedoraproject.org/rpms/sdcc/raw/4a7c2a7e32369461eb451fc6f4d678a010135afc/f/sdcc-4.4.0-aslink.patch"
echo "7c0a31c10a9d1d15b015ef2b01f1dd8d117f4d672c2c6ca15da54d6b6889b450  sdcc-4.4.0-aslink.patch" | shasum -a 256 -c - >/dev/null ||
    { echo "aslink patch hash mismatch" >&2; exit 1; }

# SDCC itself, mcs51 only.
rm -rf sdcc-4.5.0 && tar xjf "$tarball"
(
    cd sdcc-4.5.0
    patch -p1 < ../sdcc-4.4.0-aslink.patch
    echo '.PHONY: install' >> sim/ucsim/Makefile.in   # same workaround as nixpkgs
    flags=""
    for p in z80 z180 r2k r2ka r3ka r4k r5k r6k sm83 tlcs90 ez80_z80 z80n r800 ds390 ds400 pic14 pic16 \
             hc08 s08 stm8 pdk13 pdk14 pdk15 pdk16 mos6502 mos65c02 f8 f8l; do
        flags="$flags --disable-$p-port"
    done
    # shellcheck disable=SC2086
    CPPFLAGS="-I/opt/homebrew/opt/boost/include" ./configure --prefix="$root/sdcc-4.5.0-install" $flags \
        --disable-ucsim --disable-sdcdb --disable-non-free --disable-doc
    make -j8
    make install
)

# uCsim with smk's SH68F90 model, from a clean copy of the same source.
rm -rf ucsim45 && mkdir ucsim45 && tar xjf "$tarball" -C ucsim45
(
    cd ucsim45/sdcc-4.5.0
    patch -p1 < "$smk/tools/ucsim/sh68f90-register.patch"
    patch -p1 < "$smk/tools/ucsim/idle-poll.patch"
    cp "$smk/tools/ucsim/sh68f90.cc" "$smk/tools/ucsim/sh68f90cl.h" sim/ucsim/src/sims/s51.src/
    cd sim/ucsim
    flex -o src/core/cmd.src/cmdlex.cc src/core/cmd.src/cmdlex.l
    flags=""
    for s in avr f8 i8048 i8085 m6800 m6809 m68hc08 m68hc11 m68hc12 mos6502 oisc p1516 pblaze pdk rxk st7 stm8 tlcs xa z80; do
        flags="$flags --enable-$s-sim=no"
    done
    # shellcheck disable=SC2086
    ./configure $flags
    make -j8
    cp src/sims/s51.src/ucsim_51 "$root/bin/ucsim-sh68f90"
)

cat > "$root/env.sh" <<'ENV'
# SDCC 4.5.0 (same version as smk's flake.lock) and the patched uCsim.
export PATH="$HOME/.local/smk/sdcc-4.5.0-install/bin:$HOME/.local/smk/bin:$HOME/.cargo/bin:$PATH"
export SMK_UCSIM="$HOME/.local/smk/bin/ucsim-sh68f90"
ENV
echo "done: . $root/env.sh"
