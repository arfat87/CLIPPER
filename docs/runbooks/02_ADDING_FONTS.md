# Runbook 2 — Adding & Verifying Multi-Script Fonts (`latn`, `deva`, `beng`)

## When to Use
- You need to resolve `BLK-0001` (installing production SIL OFL Devanagari and Bengali fonts) or add custom brand fonts for caption templates.
- The tofu detector (`detect_tofu_glyphs`) flags missing glyph coverage for Hindi (`hi`) or Bengali (`bn`).

## Procedure
1. **Option A: Drop fonts into `assets/fonts/` (or Google Drive `CLIPPER/04_Assets/fonts/`)**:
   - Place `.ttf` or `.otf` files (e.g., `NotoSansDevanagari-Bold.ttf`, `NotoSansBengali-Bold.ttf`, `HindSiliguri-Bold.ttf`, `Montserrat-ExtraBold.ttf`) into `assets/fonts/<script>/` alongside their `OFL.txt` license file.
2. **Option B: System-level installation (Debian/Ubuntu / Docker)**:
   ```bash
   sudo apt-get update && sudo apt-get install -y fonts-noto-core fonts-noto-unhinted fontconfig
   fc-cache -fv
   ```
3. **Verify Font Discovery**:
   ```bash
   fc-list :lang=hi family
   fc-list :lang=bn family
   ```
4. **Resolve `BLK-0001`**:
   ```bash
   .venv/bin/python -m hitl.cli resolve BLK-0001 --note "Installed Noto Sans Devanagari and Noto Sans Bengali"
   ```
