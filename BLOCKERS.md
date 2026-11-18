# BLOCKERS (auto-generated / synced from `.clipper-state/blockers/`)

| ID | Type | Status | Task | Title | Unblocks |
|---|---|---|---|---|---|
| `BLK-0001` | `ASSET` | `OPEN` | `T-0.4` | Devanagari & Bengali fonts needed for tofu-free ASS caption rendering | `T-0.4`, `T-6.3`, `T-1.6` |

### `BLK-0001` Details
- **Why:** `fc-list :lang=hi` and `fc-list :lang=bn` returned empty on this Linux VM (`T-0.4` requires Hindi + Bengali shaping without tofu boxes).
- **Needed:** SIL OFL `.ttf`/`.otf` fonts for Devanagari (`Noto Sans Devanagari` / `Mukta`) and Bengali (`Noto Sans Bengali` / `Hind Siliguri`) with `LICENSE` in `assets/fonts/` or Drive `03_Fonts/`.
- **Alternative:** Reply approving download of SIL OFL Noto Indic fonts into `assets/fonts/`.
