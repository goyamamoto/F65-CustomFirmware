# Prebuilt images for the EPOMAKER x AULA F65 (V1)

| Image | Layout |
| --- | --- |
| `aula-f65-v1_ansi_smk.hex` | `ansi`: US ANSI as printed |
| `aula-f65-v1_usjis_smk.hex` | `usjis`: adds US-JIS (Fn+Tab) and the IME keys on both sides of Space (Fn and Right Ctrl swap places) |
| `aula-f65-v1_usjis-via_smk.hex` | `usjis-via`: `usjis` with the keymap editable from VIA (load `src/keyboards/aula-f65-v1/via.json` into VIA) |

All three are release builds of this repository's source: no debug console and no logging. They are built on macOS with SDCC 4.5.0 as below (a build on another platform can differ by a few bytes, see the top-level README); the top-level README's [Building](../../README.md#building) section has the full steps, including the toolchain and how to check your build against `SHA256SUMS`:

```sh
meson setup build-release --buildtype=release
meson compile -C build-release aula-f65-v1_ansi_smk.hex aula-f65-v1_usjis_smk.hex aula-f65-v1_usjis-via_smk.hex aula-f65-v1_ansi-via_smk.hex
```

Check a download with `shasum -a 256 -c SHA256SUMS`, back up the original firmware, and write one image as the top-level README describes (`sinowisp write -d aula-f75 --force <image>`). `usjis` and `usjis-via` are checked on the board; `ansi` is the same source with the `usjis` extras left out, checked on the board in an earlier build and in the simulator for this one. `ansi-via` is not prebuilt (simulator only); build it with the steps above.
