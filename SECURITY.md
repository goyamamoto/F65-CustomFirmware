# Security

This is firmware for a keyboard: it sees everything typed on it. What the release images do is written in the README and in `docs/keyboards/aula-f65-v1.md`; release builds have no debug console and no logging, and the images in `firmware/` are reproducible from the source (the CI rebuilds them and checks the checksums on every push, and attests them).

To report a problem in this firmware that could affect its users, use the repository's private vulnerability reporting (Security > Report a vulnerability) or open an issue if it is not sensitive. Problems in smk itself belong to [carlossless/smk](https://github.com/carlossless/smk).
