from __future__ import annotations

import subprocess
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import pysubs2

from backend.app.config import get_settings
from backend.pipeline.asr.engine import detect_word_script
from backend.pipeline.captions.chunker import (
    CaptionPage,
    CaptionWord,
    chunk_words_script_aware,
)
from backend.pipeline.captions.template_loader import (
    CaptionTemplate,
    get_template_by_id,
    normalize_template_to_v1,
)

CaptionChunk = CaptionPage

ASPECT_PLAY_RES: dict[str, tuple[int, int]] = {
    "9:16": (1080, 1920),
    "1:1": (1080, 1080),
    "4:5": (1080, 1350),
}

DEFAULT_SAFE_AREAS: dict[str, dict[str, float]] = {
    "shorts_default": {"top_pct": 12.0, "bottom_pct": 25.0, "left_pct": 6.0, "right_pct": 12.0},
    "reels_default": {"top_pct": 14.0, "bottom_pct": 25.0, "left_pct": 6.0, "right_pct": 12.0},
    "tiktok_default": {"top_pct": 14.0, "bottom_pct": 28.0, "left_pct": 6.0, "right_pct": 14.0},
    "none": {"top_pct": 0.0, "bottom_pct": 0.0, "left_pct": 0.0, "right_pct": 0.0},
}


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
    norm_tmpl = normalize_template_to_v1(template)
    layout = norm_tmpl.get("layout", {})
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
        case_mode = norm_tmpl.get("case", "none")
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


def _resolve_script_font_map(template: dict[str, Any]) -> dict[str, str]:
    """Resolve primary font family per script (`latn`, `deva`, `beng`) from template `fonts` / `font_fallbacks`."""
    norm = normalize_template_to_v1(template)
    fonts_cfg = norm.get("fonts", {})
    fallbacks = norm.get("font_fallbacks", {})
    out: dict[str, str] = {}
    for sc in ("latn", "deva", "beng"):
        val = fonts_cfg.get(sc)
        if isinstance(val, dict):
            fam = str(val.get("family") or "")
        else:
            fam = str(val or "")
        if not fam and isinstance(fallbacks.get(sc), list) and fallbacks[sc]:
            fam = str(fallbacks[sc][0])
        out[sc] = fam or "Sans"
    return out


def _format_token_with_font(word_obj: dict[str, Any] | CaptionWord, fonts_cfg: dict[str, str]) -> str:
    if isinstance(word_obj, CaptionWord):
        sc = word_obj.script or detect_word_script(word_obj.w)
        txt = word_obj.w
    else:
        txt = str(word_obj.get("w", ""))
        sc = str(word_obj.get("script") or detect_word_script(txt))
    font = fonts_cfg.get(sc) or fonts_cfg.get("latn") or "Sans"
    return f"{{\\fn{font}}}{txt}"


def _join_page_lines(tokens: list[str], max_wpl: int) -> str:
    lines = []
    for i in range(0, len(tokens), max_wpl):
        lines.append(" ".join(tokens[i : i + max_wpl]))
    return "\\N".join(lines)


def _join_structured_lines(
    line_word_groups: list[list[str]],
    flat_tokens: list[str],
    max_wpl: int,
) -> str:
    if not line_word_groups:
        return _join_page_lines(flat_tokens, max_wpl)
    out_lines: list[str] = []
    idx = 0
    for grp in line_word_groups:
        cnt = len(grp)
        slice_toks = flat_tokens[idx : idx + cnt]
        if slice_toks:
            out_lines.append(" ".join(slice_toks))
        idx += cnt
    if idx < len(flat_tokens):
        out_lines.append(" ".join(flat_tokens[idx:]))
    return "\\N".join(out_lines)


def resolve_safe_area_box(
    aspect: str = "9:16",
    play_res: tuple[int, int] | None = None,
    safe_area_profile: str = "shorts_default",
    y_percent: float = 72.0,
) -> dict[str, int]:
    """Compute PlayResX, PlayResY, and safe-area clamped MarginL, MarginR, MarginV, pos_x, pos_y."""
    res_x, res_y = play_res if play_res is not None else ASPECT_PLAY_RES.get(aspect, (1080, 1920))
    try:
        platforms_cfg = get_settings().load_yaml_config("platforms")
        safe_areas = platforms_cfg.get("safe_areas") if isinstance(platforms_cfg, dict) else None
    except Exception:
        safe_areas = None

    raw_safe_areas: dict[str, Any] = dict(safe_areas) if isinstance(safe_areas, dict) else {}
    cand_prof = raw_safe_areas.get(safe_area_profile)
    profile_map: dict[str, Any] = (
        dict(cand_prof)
        if isinstance(cand_prof, dict)
        else dict(DEFAULT_SAFE_AREAS.get(safe_area_profile, DEFAULT_SAFE_AREAS["shorts_default"]))
    )

    top_pct = float(profile_map.get("top_pct", 12.0))
    bottom_pct = float(profile_map.get("bottom_pct", 25.0))
    left_pct = float(profile_map.get("left_pct", 6.0))
    right_pct = float(profile_map.get("right_pct", 12.0))

    min_y_pct = top_pct
    max_y_pct = max(min_y_pct, 100.0 - bottom_pct)
    clamped_y_pct = min(max(float(y_percent), min_y_pct), max_y_pct)

    margin_l = max(24, int(round(res_x * (left_pct / 100.0))))
    margin_r = max(24, int(round(res_x * (right_pct / 100.0))))
    min_margin_v = int(round(res_y * (bottom_pct / 100.0)))
    max_margin_v = int(round(res_y * (1.0 - top_pct / 100.0)))
    raw_margin_v = int(round(res_y * (1.0 - clamped_y_pct / 100.0)))
    margin_v = min(max(raw_margin_v, max(40, min_margin_v)), max(60, max_margin_v))

    pos_x = int(round(res_x * 0.5))
    pos_y = int(round(res_y * (clamped_y_pct / 100.0)))

    return {
        "PlayResX": int(res_x),
        "PlayResY": int(res_y),
        "MarginL": int(margin_l),
        "MarginR": int(margin_r),
        "MarginV": int(margin_v),
        "pos_x": int(pos_x),
        "pos_y": int(pos_y),
        "clamped_y_percent": int(round(clamped_y_pct)),
    }


def _resolve_animation_style(template: dict[str, Any], style_override: str | None = None) -> str:
    if style_override:
        return style_override.strip().lower()
    norm = normalize_template_to_v1(template)
    anim = norm.get("animation") or {}
    t = str(anim.get("type") or "").strip().lower()
    if t in ("karaoke_fill", "highlight_word", "pop", "typewriter", "fade_slide", "bounce"):
        return t
    return "highlight_word"


def build_ass_subtitles(
    words: list[dict[str, Any]],
    template: dict[str, Any],
    out_ass_path: Path,
    clip_start_offset: float = 0.0,
    play_res: tuple[int, int] = (1080, 1920),
) -> Path:
    """Build a complete ASS subtitle file with active-word highlight / karaoke / pop animations and mixed-script font fallback."""
    norm_template = normalize_template_to_v1(template)
    subs = pysubs2.SSAFile()
    res_x, res_y = play_res
    subs.info["PlayResX"] = str(res_x)
    subs.info["PlayResY"] = str(res_y)
    subs.info["WrapStyle"] = "2"
    subs.info["ScaledBorderAndShadow"] = "yes"

    colors = norm_template.get("colors", {})
    fonts_cfg = _resolve_script_font_map(norm_template)
    layout = norm_template.get("layout", {})
    y_pct = float(layout.get("y_percent", 72))
    margin_v = int(res_y * (1.0 - y_pct / 100.0))

    style = pysubs2.SSAStyle(
        fontname=fonts_cfg.get("latn", "Sans"),
        fontsize=float(norm_template.get("size_px", 70)),
        primarycolor=_hex_to_pysubs_color(colors.get("primary", "#FFFFFF")),
        secondarycolor=_hex_to_pysubs_color(colors.get("active", "#FFE600")),
        outlinecolor=_hex_to_pysubs_color(colors.get("outline", "#000000")),
        backcolor=_hex_to_pysubs_color(colors.get("shadow", "#000000AA")),
        bold=True,
        outline=float(norm_template.get("outline_px", 5.0)),
        shadow=float(norm_template.get("shadow_px", 2.0)),
        alignment=pysubs2.Alignment.BOTTOM_CENTER,
        marginv=max(60, margin_v),
        marginl=60,
        marginr=60,
    )
    if norm_template.get("box", {}).get("enabled"):
        style.borderstyle = 3
    subs.styles["Default"] = style

    pages = chunk_words_into_pages(words, norm_template, clip_start_offset=clip_start_offset)
    anim_type = _resolve_animation_style(norm_template)
    active_bgr = _hex_to_ass_bgr(colors.get("active", "#FFE600"))
    primary_bgr = _hex_to_ass_bgr(colors.get("primary", "#FFFFFF"))
    outline_bgr = _hex_to_ass_bgr(colors.get("box") or colors.get("outline") or "#000000")
    max_wpl = int(layout.get("max_words_per_line", 3))
    pos_x = int(res_x // 2)
    pos_y = int(res_y - max(60, margin_v))

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
                            f"{{\\c{active_bgr}\\3c{outline_bgr}\\fscx112\\fscy112}}{base_tok}{{\\c{primary_bgr}\\fscx100\\fscy100}}"
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

        elif anim_type == "bounce":
            ev_start = int(page["start"] * 1000)
            ev_end = int(page["end"] * 1000)
            prefix = "{\\t(0,90,\\fscy120)\\t(90,180,\\fscy100)}"
            tokens = [_format_token_with_font(w, fonts_cfg) for w in p_words]
            line_text = prefix + _join_page_lines(tokens, max_wpl)
            subs.events.append(pysubs2.SSAEvent(start=ev_start, end=max(ev_start + 200, ev_end), text=line_text))

        elif anim_type == "fade_slide":
            ev_start = int(page["start"] * 1000)
            ev_end = int(page["end"] * 1000)
            prefix = f"{{\\fad(120,120)\\move({pos_x},{pos_y + 28},{pos_x},{pos_y},0,150)}}"
            tokens = [_format_token_with_font(w, fonts_cfg) for w in p_words]
            line_text = prefix + _join_page_lines(tokens, max_wpl)
            subs.events.append(pysubs2.SSAEvent(start=ev_start, end=max(ev_start + 200, ev_end), text=line_text))

        else:
            # pop
            ev_start = int(page["start"] * 1000)
            ev_end = int(page["end"] * 1000)
            prefix = "{\\t(0,80,\\fscx115\\fscy115)\\t(80,160,\\fscx100\\fscy100)}"
            tokens = [_format_token_with_font(w, fonts_cfg) for w in p_words]
            line_text = prefix + _join_page_lines(tokens, max_wpl)
            subs.events.append(pysubs2.SSAEvent(start=ev_start, end=max(ev_start + 200, ev_end), text=line_text))

    out_ass_path.parent.mkdir(parents=True, exist_ok=True)
    subs.save(str(out_ass_path))
    return out_ass_path


def build_ass_subtitle(
    words: list[dict[str, Any]],
    template: dict[str, Any],
    out_ass_path: Path,
    clip_start_offset: float = 0.0,
    play_res: tuple[int, int] = (1080, 1920),
) -> Path:
    """Alias for `build_ass_subtitles`."""
    return build_ass_subtitles(
        words=words,
        template=template,
        out_ass_path=out_ass_path,
        clip_start_offset=clip_start_offset,
        play_res=play_res,
    )


def burn_in_captions(
    source_mp4: Path,
    ass_path: Path,
    out_mp4: Path,
    *,
    fonts_dir: Path | None = None,
) -> Path:
    """Burn an `.ass` subtitle file into `source_mp4` using FFmpeg's `ass=` filter (libass)."""
    escaped_ass = str(ass_path).replace("\\", "/").replace(":", "\\:")
    if fonts_dir is not None and Path(fonts_dir).exists():
        escaped_fonts = str(fonts_dir).replace("\\", "/").replace(":", "\\:")
        vf = f"ass='{escaped_ass}':fontsdir='{escaped_fonts}'"
    else:
        vf = f"ass='{escaped_ass}'"

    out_mp4.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        str(source_mp4),
        "-vf",
        vf,
        "-c:v",
        "libx264",
        "-preset",
        "ultrafast",
        "-crf",
        "23",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "copy",
        "-movflags",
        "+faststart",
        str(out_mp4),
    ]
    subprocess.run(cmd, check=True, capture_output=True)
    return out_mp4


def _coerce_chunks(
    chunks_or_words: Sequence[CaptionPage | Mapping[str, Any]],
    template: dict[str, Any],
    *,
    clip_start_offset: float = 0.0,
    animation_style: str | None = None,
) -> list[CaptionPage]:
    if not chunks_or_words:
        return []
    first = chunks_or_words[0]
    if isinstance(first, CaptionPage):
        return [c for c in chunks_or_words if isinstance(c, CaptionPage)]
    if isinstance(first, Mapping) and "words" in first and ("start" in first or "lines" in first):
        pages: list[CaptionPage] = []
        for item in chunks_or_words:
            if not isinstance(item, Mapping):
                continue
            raw_words = item.get("words") or []
            c_words = [
                w
                if isinstance(w, CaptionWord)
                else CaptionWord(
                    w=str(w.get("w", "")),
                    s=float(w.get("s", 0.0)),
                    e=float(w.get("e", 0.1)),
                    script=str(w.get("script") or detect_word_script(str(w.get("w", "")))),
                    duration_cs=w.get("duration_cs"),
                )
                for w in raw_words
                if isinstance(w, (CaptionWord, Mapping))
            ]
            raw_lines = item.get("lines")
            if isinstance(raw_lines, list) and raw_lines:
                c_lines: list[list[CaptionWord]] = []
                for line in raw_lines:
                    c_lines.append(
                        [
                            w
                            if isinstance(w, CaptionWord)
                            else CaptionWord(
                                w=str(w.get("w", "")),
                                s=float(w.get("s", 0.0)),
                                e=float(w.get("e", 0.1)),
                                script=str(w.get("script") or detect_word_script(str(w.get("w", "")))),
                            )
                            for w in line
                        ]
                    )
            else:
                max_wpl = int((template.get("layout") or {}).get("max_words_per_line", 3))
                c_lines = [c_words[i : i + max_wpl] for i in range(0, len(c_words), max_wpl)]
            pages.append(
                CaptionPage(
                    start=float(item.get("start", c_words[0].s if c_words else 0.0)),
                    end=float(item.get("end", c_words[-1].e if c_words else 0.35)),
                    lines=c_lines,
                    words=c_words,
                    mode=str(item.get("mode", "pages")),
                    karaoke_cs=list(item.get("karaoke_cs") or []),
                )
            )
        return pages

    mode = "karaoke" if animation_style == "karaoke_fill" else None
    word_dicts: list[dict[str, Any] | CaptionWord] = [
        dict(w) for w in chunks_or_words if isinstance(w, Mapping)
    ]
    return chunk_words_script_aware(
        word_dicts,
        template,
        clip_start_offset=clip_start_offset,
        mode=mode,
    )


def build_ass_from_chunks(
    chunks_or_words: Sequence[CaptionPage | Mapping[str, Any]],
    template: dict[str, Any] | CaptionTemplate | str,
    out_ass_path: Path | None = None,
    *,
    aspect: str = "9:16",
    play_res: tuple[int, int] | None = None,
    animation_style: str | None = None,
    clip_start_offset: float = 0.0,
) -> pysubs2.SSAFile:
    """Spec v2 ASS subtitle builder supporting all 6 animation styles, mixed-script inline font switching, and safe-area clamping.

    Supported animation styles:
    - `karaoke_fill`: `{\\kf<cs>}` progressive fill tags per word
    - `highlight_word`: active word color/outline/scale tag switch (`{\\c&H...&\\3c&H...&\\fscx115\\fscy115}`)
    - `pop`: `{\\t(0,80,\\fscx115\\fscy115)\\t(80,160,\\fscx100\\fscy100)}`
    - `typewriter`: progressive word/char reveal events
    - `fade_slide`: `{\\fad(120,120)\\move(x1,y1,x2,y2,0,150)}`
    - `bounce`: `{\\t(0,90,\\fscy120)\\t(90,180,\\fscy100)}`
    """
    if isinstance(template, str):
        raw_tmpl = get_template_by_id(template)
    elif isinstance(template, CaptionTemplate):
        raw_tmpl = template.to_dict()
    else:
        raw_tmpl = dict(template)

    norm_tmpl = normalize_template_to_v1(raw_tmpl)
    layout = norm_tmpl.get("layout") or {}
    safe_profile = str(layout.get("safe_area_profile") or "shorts_default")
    y_pct = float(layout.get("y_percent", 68.0))
    safe_box = resolve_safe_area_box(
        aspect=aspect,
        play_res=play_res,
        safe_area_profile=safe_profile,
        y_percent=y_pct,
    )

    res_x = safe_box["PlayResX"]
    res_y = safe_box["PlayResY"]

    subs = pysubs2.SSAFile()
    subs.info["PlayResX"] = str(res_x)
    subs.info["PlayResY"] = str(res_y)
    subs.info["WrapStyle"] = "2"
    subs.info["ScaledBorderAndShadow"] = "yes"
    subs.info["YCbCr Matrix"] = "TV.709"

    colors = norm_tmpl.get("colors") or {}
    fonts_cfg = _resolve_script_font_map(norm_tmpl)
    box_cfg = norm_tmpl.get("box") or {}

    style = pysubs2.SSAStyle(
        fontname=fonts_cfg.get("latn", "Sans"),
        fontsize=float(norm_tmpl.get("size_px", 72)),
        primarycolor=_hex_to_pysubs_color(colors.get("primary", "#FFFFFF")),
        secondarycolor=_hex_to_pysubs_color(colors.get("active", "#FFE600")),
        outlinecolor=_hex_to_pysubs_color(
            (colors.get("box") if box_cfg.get("enabled") and colors.get("box") else colors.get("outline"))
            or "#000000"
        ),
        backcolor=_hex_to_pysubs_color(colors.get("shadow", "#000000AA")),
        bold=True,
        outline=float(norm_tmpl.get("outline_px", 5.0)),
        shadow=float(norm_tmpl.get("shadow_px", 2.0)),
        alignment=pysubs2.Alignment.BOTTOM_CENTER,
        marginl=safe_box["MarginL"],
        marginr=safe_box["MarginR"],
        marginv=safe_box["MarginV"],
    )
    if box_cfg.get("enabled"):
        style.borderstyle = 3
    subs.styles["Default"] = style

    resolved_anim = _resolve_animation_style(norm_tmpl, style_override=animation_style)
    pages = _coerce_chunks(
        chunks_or_words,
        norm_tmpl,
        clip_start_offset=clip_start_offset,
        animation_style=resolved_anim,
    )

    active_bgr = _hex_to_ass_bgr(colors.get("active", "#FFE600"))
    primary_bgr = _hex_to_ass_bgr(colors.get("primary", "#FFFFFF"))
    outline_bgr = _hex_to_ass_bgr(colors.get("box") or colors.get("outline") or "#000000")
    max_wpl = max(1, int(layout.get("max_words_per_line", 3)))
    pos_x = safe_box["pos_x"]
    pos_y = safe_box["pos_y"]

    for page in pages:
        p_words = page.words
        if not p_words:
            continue
        line_groups = [[w.w for w in line] for line in page.lines] if page.lines else []

        if resolved_anim == "karaoke_fill":
            ev_start = int(round(page.start * 1000))
            ev_end = max(ev_start + 200, int(round(page.end * 1000)))
            k_tokens: list[str] = []
            for idx, w in enumerate(p_words):
                cs = (
                    page.karaoke_cs[idx]
                    if idx < len(page.karaoke_cs)
                    else (w.duration_cs or max(8, int(round((w.e - w.s) * 100))))
                )
                k_tokens.append(f"{{\\kf{cs}}}{_format_token_with_font(w, fonts_cfg)}")
            line_text = _join_structured_lines(line_groups, k_tokens, max_wpl)
            subs.events.append(pysubs2.SSAEvent(start=ev_start, end=ev_end, text=line_text))

        elif resolved_anim == "highlight_word":
            for active_idx, aw in enumerate(p_words):
                ev_start = int(round(aw.s * 1000))
                next_s = p_words[active_idx + 1].s if active_idx + 1 < len(p_words) else page.end
                ev_end = max(ev_start + 100, int(round(max(aw.e, next_s) * 1000)))
                rendered_tokens: list[str] = []
                for idx, w in enumerate(p_words):
                    base_tok = _format_token_with_font(w, fonts_cfg)
                    if idx == active_idx:
                        rendered_tokens.append(
                            f"{{\\c{active_bgr}\\3c{outline_bgr}\\fscx115\\fscy115}}{base_tok}{{\\c{primary_bgr}\\fscx100\\fscy100}}"
                        )
                    else:
                        rendered_tokens.append(f"{{\\c{primary_bgr}}}{base_tok}")
                line_text = _join_structured_lines(line_groups, rendered_tokens, max_wpl)
                subs.events.append(pysubs2.SSAEvent(start=ev_start, end=ev_end, text=line_text))

        elif resolved_anim == "pop":
            ev_start = int(round(page.start * 1000))
            ev_end = max(ev_start + 200, int(round(page.end * 1000)))
            prefix = "{\\t(0,80,\\fscx115\\fscy115)\\t(80,160,\\fscx100\\fscy100)}"
            tokens = [_format_token_with_font(w, fonts_cfg) for w in p_words]
            line_text = prefix + _join_structured_lines(line_groups, tokens, max_wpl)
            subs.events.append(pysubs2.SSAEvent(start=ev_start, end=ev_end, text=line_text))

        elif resolved_anim == "typewriter":
            for active_idx, aw in enumerate(p_words):
                ev_start = int(round(aw.s * 1000))
                next_s = p_words[active_idx + 1].s if active_idx + 1 < len(p_words) else page.end
                ev_end = max(ev_start + 100, int(round(max(aw.e, next_s) * 1000)))
                visible = [_format_token_with_font(w, fonts_cfg) for w in p_words[: active_idx + 1]]
                visible[-1] = f"{{\\c{active_bgr}}}{visible[-1]}{{\\c{primary_bgr}}}"
                line_text = _join_page_lines(visible, max_wpl)
                subs.events.append(pysubs2.SSAEvent(start=ev_start, end=ev_end, text=line_text))

        elif resolved_anim == "fade_slide":
            ev_start = int(round(page.start * 1000))
            ev_end = max(ev_start + 200, int(round(page.end * 1000)))
            x1, y1 = pos_x, min(res_y - 20, pos_y + 32)
            x2, y2 = pos_x, pos_y
            prefix = f"{{\\fad(120,120)\\move({x1},{y1},{x2},{y2},0,150)}}"
            tokens = [_format_token_with_font(w, fonts_cfg) for w in p_words]
            line_text = prefix + _join_structured_lines(line_groups, tokens, max_wpl)
            subs.events.append(pysubs2.SSAEvent(start=ev_start, end=ev_end, text=line_text))

        elif resolved_anim == "bounce":
            ev_start = int(round(page.start * 1000))
            ev_end = max(ev_start + 200, int(round(page.end * 1000)))
            prefix = "{\\t(0,90,\\fscy120)\\t(90,180,\\fscy100)}"
            tokens = [_format_token_with_font(w, fonts_cfg) for w in p_words]
            line_text = prefix + _join_structured_lines(line_groups, tokens, max_wpl)
            subs.events.append(pysubs2.SSAEvent(start=ev_start, end=ev_end, text=line_text))

    if out_ass_path is not None:
        out_ass_path.parent.mkdir(parents=True, exist_ok=True)
        subs.save(str(out_ass_path))

    return subs


def _format_srt_time(seconds: float) -> str:
    ms_total = max(0, int(round(seconds * 1000)))
    hh = ms_total // 3_600_000
    rem = ms_total % 3_600_000
    mm = rem // 60_000
    rem %= 60_000
    ss = rem // 1000
    ms = rem % 1000
    return f"{hh:02d}:{mm:02d}:{ss:02d},{ms:03d}"


def _format_vtt_time(seconds: float) -> str:
    ms_total = max(0, int(round(seconds * 1000)))
    hh = ms_total // 3_600_000
    rem = ms_total % 3_600_000
    mm = rem // 60_000
    rem %= 60_000
    ss = rem // 1000
    ms = rem % 1000
    return f"{hh:02d}:{mm:02d}:{ss:02d}.{ms:03d}"


def export_subtitles_v2(
    chunks_or_words: Sequence[CaptionPage | Mapping[str, Any]],
    template: dict[str, Any] | CaptionTemplate | str,
    out_dir: Path,
    *,
    stem: str = "captions",
    aspect: str = "9:16",
    play_res: tuple[int, int] | None = None,
    animation_style: str | None = None,
    clip_start_offset: float = 0.0,
) -> dict[str, Path]:
    """Generate `.ass`, `.srt`, and `.vtt` subtitle sidecar files from `CaptionChunk` list or word timestamps."""
    if isinstance(template, str):
        raw_tmpl = get_template_by_id(template)
    elif isinstance(template, CaptionTemplate):
        raw_tmpl = template.to_dict()
    else:
        raw_tmpl = dict(template)

    norm_tmpl = normalize_template_to_v1(raw_tmpl)
    resolved_anim = _resolve_animation_style(norm_tmpl, style_override=animation_style)
    pages = _coerce_chunks(
        chunks_or_words,
        norm_tmpl,
        clip_start_offset=clip_start_offset,
        animation_style=resolved_anim,
    )

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    ass_path = out_dir / f"{stem}.ass"
    srt_path = out_dir / f"{stem}.srt"
    vtt_path = out_dir / f"{stem}.vtt"

    build_ass_from_chunks(
        pages,
        norm_tmpl,
        out_ass_path=ass_path,
        aspect=aspect,
        play_res=play_res,
        animation_style=resolved_anim,
        clip_start_offset=0.0,
    )

    srt_blocks: list[str] = []
    vtt_blocks: list[str] = ["WEBVTT"]
    for idx, page in enumerate(pages, start=1):
        txt = page.text.strip()
        if not txt:
            continue
        s_t = max(0.0, page.start)
        e_t = max(s_t + 0.1, page.end)
        srt_blocks.append(f"{idx}\n{_format_srt_time(s_t)} --> {_format_srt_time(e_t)}\n{txt}")
        vtt_blocks.append(f"{_format_vtt_time(s_t)} --> {_format_vtt_time(e_t)}\n{txt}")

    srt_path.write_text(("\n\n".join(srt_blocks) + "\n") if srt_blocks else "", encoding="utf-8")
    vtt_path.write_text("\n\n".join(vtt_blocks) + "\n", encoding="utf-8")

    return {
        "ass": ass_path,
        "srt": srt_path,
        "vtt": vtt_path,
    }
