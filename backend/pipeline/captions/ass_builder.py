from __future__ import annotations

from pathlib import Path
from typing import Any

import pysubs2

from backend.pipeline.asr.engine import detect_word_script


def _hex_to_pysubs_color(hex_str: str) -> pysubs2.Color:
    """Convert #RRGGBB or #RRGGBBAA hex string to pysubs2.Color(r, g, b, a)."""
    h = (hex_str or "#FFFFFF").lstrip("#")
    if len(h) == 6:
        r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
        return pysubs2.Color(r, g, b, 0)
    if len(h) == 8:
        r, g, b, a = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16), int(h[6:8], 16)
        # ASS alpha is 00=opaque, FF=transparent
        return pysubs2.Color(r, g, b, 255 - a)
    return pysubs2.Color(255, 255, 255, 0)


def _hex_to_ass_bgr(hex_str: str) -> str:
    """Convert #RRGGBB to ASS override tag format &HBBGGRR&."""
    h = (hex_str or "#FFE600").lstrip("#")[:6]
    if len(h) != 6:
        h = "FFE600"
    r, g, b = h[0:2], h[2:4], h[4:6]
    return f"&H{b}{g}{r}&"


def chunk_words_into_pages(
    words: list[dict[str, Any]],
    template: dict[str, Any],
    clip_start_offset: float = 0.0,
) -> list[dict[str, Any]]:
    """Group word-level timestamps into multi-line caption pages respecting script-aware limits."""
    layout = template.get("layout", {})
    max_wpl = int(layout.get("max_words_per_line", 3))
    max_lines = int(layout.get("max_lines", 2))
    page_capacity = max(1, max_wpl * max_lines)

    # Shift timestamps relative to clip start
    rel_words: list[dict[str, Any]] = []
    for w in words:
        s = max(0.0, round(float(w["s"]) - clip_start_offset, 2))
        e = max(s + 0.08, round(float(w["e"]) - clip_start_offset, 2))
        txt = str(w.get("w", "")).strip()
        if not txt:
            continue
        sc = w.get("script") or detect_word_script(txt)
        # Apply case transform ONLY on Latin script so Devanagari/Bengali vowel matras are never corrupted
        case_mode = template.get("case", "none")
        if sc == "latn":
            if case_mode == "upper":
                txt = txt.upper()
            elif case_mode == "lower":
                txt = txt.lower()
        rel_words.append({"w": txt, "s": s, "e": e, "script": sc})

    pages: list[dict[str, Any]] = []
    buf: list[dict[str, Any]] = []

    for idx, w in enumerate(rel_words):
        buf.append(w)
        ends_sentence = w["w"].endswith((".", "!", "?", "।"))
        long_pause = (
            idx + 1 < len(rel_words)
            and (rel_words[idx + 1]["s"] - w["e"]) > 0.55
        )
        if len(buf) >= page_capacity or ends_sentence or long_pause:
            pages.append(
                {
                    "start": buf[0]["s"],
                    "end": buf[-1]["e"],
                    "words": list(buf),
                    "max_wpl": max_wpl,
                }
            )
            buf.clear()

    if buf:
        pages.append(
            {
                "start": buf[0]["s"],
                "end": buf[-1]["e"],
                "words": list(buf),
                "max_wpl": max_wpl,
            }
        )

    return pages


def _format_token_with_font(word_obj: dict[str, Any], fonts_cfg: dict[str, str]) -> str:
    sc = word_obj.get("script", "latn")
    font = fonts_cfg.get(sc) or fonts_cfg.get("latn") or "Sans"
    return f"{{\\fn{font}}}{word_obj['w']}"


def _join_page_lines(tokens: list[str], max_wpl: int) -> str:
    lines = []
    for i in range(0, len(tokens), max_wpl):
        lines.append(" ".join(tokens[i : i + max_wpl]))
    return "\\N".join(lines)


def build_ass_subtitles(
    words: list[dict[str, Any]],
    template: dict[str, Any],
    out_ass_path: Path,
    clip_start_offset: float = 0.0,
    play_res: tuple[int, int] = (1080, 1920),
) -> Path:
    """Build a complete ASS subtitle file with active-word highlight / karaoke / pop animations and mixed-script font fallback."""
    subs = pysubs2.SSAFile()
    res_x, res_y = play_res
    subs.info["PlayResX"] = str(res_x)
    subs.info["PlayResY"] = str(res_y)
    subs.info["WrapStyle"] = "2"

    colors = template.get("colors", {})
    fonts_cfg = template.get("fonts", {"latn": "Sans", "deva": "Sans", "beng": "Sans"})
    layout = template.get("layout", {})
    y_pct = float(layout.get("y_percent", 72))
    margin_v = int(res_y * (1.0 - y_pct / 100.0))

    style = pysubs2.SSAStyle(
        fontname=fonts_cfg.get("latn", "Sans"),
        fontsize=float(template.get("size_px", 70)),
        primarycolor=_hex_to_pysubs_color(colors.get("primary", "#FFFFFF")),
        secondarycolor=_hex_to_pysubs_color(colors.get("active", "#FFE600")),
        outlinecolor=_hex_to_pysubs_color(colors.get("outline", "#000000")),
        backcolor=_hex_to_pysubs_color(colors.get("shadow", "#000000AA")),
        bold=True,
        outline=float(template.get("outline_px", 5.0)),
        shadow=float(template.get("shadow_px", 2.0)),
        alignment=pysubs2.Alignment.BOTTOM_CENTER,
        marginv=max(60, margin_v),
        marginl=60,
        marginr=60,
    )
    if template.get("box", {}).get("enabled"):
        style.borderstyle = 3
    subs.styles["Default"] = style

    pages = chunk_words_into_pages(words, template, clip_start_offset=clip_start_offset)
    anim_type = template.get("animation", {}).get("type", "highlight_word")
    active_bgr = _hex_to_ass_bgr(colors.get("active", "#FFE600"))
    primary_bgr = _hex_to_ass_bgr(colors.get("primary", "#FFFFFF"))
    max_wpl = int(layout.get("max_words_per_line", 3))

    for page in pages:
        p_words = page["words"]
        if not p_words:
            continue

        if anim_type == "highlight_word":
            # Emit one event per active word in the page so the spoken word pops in active color + 112% scale
            for active_idx, aw in enumerate(p_words):
                ev_start = int(aw["s"] * 1000)
                ev_end = int(
                    (p_words[active_idx + 1]["s"] if active_idx + 1 < len(p_words) else aw["e"])
                    * 1000
                )
                if ev_end <= ev_start:
                    ev_end = ev_start + 120
                rendered_tokens = []
                for idx, w in enumerate(p_words):
                    base_tok = _format_token_with_font(w, fonts_cfg)
                    if idx == active_idx:
                        rendered_tokens.append(
                            f"{{\\c{active_bgr}\\fscx112\\fscy112}}{base_tok}{{\\c{primary_bgr}\\fscx100\\fscy100}}"
                        )
                    else:
                        rendered_tokens.append(f"{{\\c{primary_bgr}}}{base_tok}")
                line_text = _join_page_lines(rendered_tokens, max_wpl)
                subs.events.append(pysubs2.SSAEvent(start=ev_start, end=ev_end, text=line_text))

        elif anim_type == "typewriter":
            for active_idx, aw in enumerate(p_words):
                ev_start = int(aw["s"] * 1000)
                ev_end = int(
                    (p_words[active_idx + 1]["s"] if active_idx + 1 < len(p_words) else aw["e"])
                    * 1000
                )
                visible = [_format_token_with_font(w, fonts_cfg) for w in p_words[: active_idx + 1]]
                visible[-1] = f"{{\\c{active_bgr}}}{visible[-1]}{{\\c{primary_bgr}}}"
                line_text = _join_page_lines(visible, max_wpl)
                subs.events.append(pysubs2.SSAEvent(start=ev_start, end=max(ev_start + 100, ev_end), text=line_text))

        elif anim_type == "karaoke_fill":
            ev_start = int(page["start"] * 1000)
            ev_end = int(page["end"] * 1000)
            k_tokens = []
            for w in p_words:
                cs = max(8, int((w["e"] - w["s"]) * 100))
                k_tokens.append(f"{{\\kf{cs}}}{_format_token_with_font(w, fonts_cfg)}")
            line_text = _join_page_lines(k_tokens, max_wpl)
            subs.events.append(pysubs2.SSAEvent(start=ev_start, end=max(ev_start + 200, ev_end), text=line_text))

        else:
            # pop or fade_slide
            ev_start = int(page["start"] * 1000)
            ev_end = int(page["end"] * 1000)
            prefix = (
                "{\\t(0,90,\\fscx115\\fscy115)\\t(90,180,\\fscx100\\fscy100)}"
                if anim_type == "pop"
                else "{\\fad(90,90)}"
            )
            tokens = [_format_token_with_font(w, fonts_cfg) for w in p_words]
            line_text = prefix + _join_page_lines(tokens, max_wpl)
            subs.events.append(pysubs2.SSAEvent(start=ev_start, end=max(ev_start + 200, ev_end), text=line_text))

    out_ass_path.parent.mkdir(parents=True, exist_ok=True)
    subs.save(str(out_ass_path))
    return out_ass_path
