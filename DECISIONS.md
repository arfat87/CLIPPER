# DECISIONS

(owner answers to DECISION blockers and ADR pointers)

## Day-0 Machine Facts (Verified 2026-11-18)
- **OS:** Linux (Debian 12 x86_64)
- **CPU / GPU:** 2 vCPU, CPU-only (`nvidia-smi` not present -> ASR `medium` / `small` `int8`, FFmpeg `libx264`)
- **RAM:** 7.8 GiB total (~7.0 GiB available)
- **Disk:** 18 GiB free on `/home/antigravity`
- **Toolchain:**
  - Python 3.11.2, `uv` installed
  - Node.js `v24.22.2`, `pnpm 12.21.0`
  - FFmpeg `5.1.9` with `--enable-libass`, `--enable-libfribidi`, `--enable-libfreetype`, `--enable-libx264` (`libass.so.9` linked to `libharfbuzz.so.0` and `libfribidi.so.0`)
- **Fonts Check (`T-0.4`):** Latin/CJK fonts present (`/usr/share/fonts/`); `fc-list :lang=hi` and `fc-list :lang=bn` currently return empty -> tracked in `BLK-0001`.
