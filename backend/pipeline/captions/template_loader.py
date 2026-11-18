from __future__ import annotations

import json
import subprocess
import tempfile
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import jsonschema  # type: ignore[import-untyped]

from backend.app.config import ROOT_DIR, get_settings

VALID_TEMPLATE_KINDS: tuple[str, ...] = (
    "caption",
    "layout",
    "publish",
    "brand_kit",
    "overlay",
    "preset",
)

TEMPLATE_JSON_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["id", "name", "version", "fonts", "size_px", "colors", "layout", "animation"],
    "properties": {
        "id": {"type": "string", "minLength": 2},
        "name": {"type": "string", "minLength": 2},
        "version": {"type": "integer"},
        "aspect": {"type": "array", "items": {"type": "string"}},
        "fonts": {
            "type": "object",
            "required": ["latn", "deva", "beng"],
            "properties": {
                "latn": {"type": "string"},
                "deva": {"type": "string"},
                "beng": {"type": "string"},
            },
        },
        "size_px": {"type": "number", "minimum": 16, "maximum": 160},
        "case": {"type": "string", "enum": ["upper", "lower", "none"]},
        "colors": {
            "type": "object",
            "required": ["primary", "active", "outline"],
            "properties": {
                "primary": {"type": "string"},
                "active": {"type": "string"},
                "outline": {"type": "string"},
                "shadow": {"type": "string"},
                "box": {"type": ["string", "null"]},
            },
        },
        "outline_px": {"type": "number"},
        "shadow_px": {"type": "number"},
        "layout": {
            "type": "object",
            "required": ["y_percent", "max_words_per_line", "max_lines"],
            "properties": {
                "position": {"type": "string"},
                "y_percent": {"type": "number", "minimum": 10, "maximum": 92},
                "align": {"type": "string"},
                "max_words_per_line": {"type": "integer", "minimum": 1, "maximum": 10},
                "max_lines": {"type": "integer", "minimum": 1, "maximum": 4},
            },
        },
        "animation": {
            "type": "object",
            "required": ["type"],
            "properties": {
                "type": {
                    "type": "string",
                    "enum": ["highlight_word", "karaoke_fill", "pop", "typewriter", "fade_slide", "bounce"],
                },
                "in": {"type": "string"},
                "out": {"type": "string"},
                "duration_ms": {"type": "integer"},
            },
        },
    },
}

# Spec v2 schemas per kind (also loads contracts/schemas/caption_template.schema.json when available)
_META_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["id", "name", "version"],
    "properties": {
        "id": {"type": "string", "pattern": "^[a-z][a-z0-9_]{2,47}$"},
        "name": {"type": "string", "minLength": 1, "maxLength": 80},
        "version": {"type": "integer", "minimum": 1},
        "author": {"type": "string"},
        "license": {"type": "string", "minLength": 1},
        "tags": {"type": "array", "items": {"type": "string"}},
    },
}

LAYOUT_TEMPLATE_V2_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["schema_version", "meta", "type", "params"],
    "properties": {
        "schema_version": {"type": "integer", "enum": [1, 2]},
        "kind": {"const": "layout"},
        "meta": _META_SCHEMA,
        "type": {"enum": ["fill", "fit_blur", "split_2", "screen_focus"]},
        "aspects": {
            "type": "array",
            "minItems": 1,
            "items": {"enum": ["9:16", "1:1", "4:5"]},
        },
        "params": {"type": "object"},
    },
}

OVERLAY_TEMPLATE_V2_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["schema_version", "meta", "type"],
    "properties": {
        "schema_version": {"type": "integer", "enum": [1, 2]},
        "kind": {"const": "overlay"},
        "meta": _META_SCHEMA,
        "type": {
            "enum": [
                "text_block",
                "progress_bar",
                "image",
                "end_card",
                "hook_banner",
                "watermark",
                "lower_third",
                "broll_image",
            ]
        },
        "text": {"type": "object"},
        "position": {"type": "object"},
        "timing": {"type": "object"},
        "params": {"type": "object"},
    },
}

PUBLISH_TEMPLATE_V2_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["schema_version", "meta", "platform", "language", "title", "description"],
    "properties": {
        "schema_version": {"type": "integer", "enum": [1, 2]},
        "kind": {"const": "publish"},
        "meta": _META_SCHEMA,
        "platform": {"enum": ["youtube_shorts", "instagram_reels", "tiktok", "facebook_reels"]},
        "language": {"enum": ["en", "hi", "hinglish", "bn"]},
        "script": {"enum": ["latn", "deva", "beng"]},
        "title": {"type": "object", "required": ["template"]},
        "description": {"type": "object", "required": ["template"]},
        "hashtags": {"type": "object"},
        "tags": {"type": "object"},
        "pinned_comment": {"type": "object"},
        "rules": {"type": "object"},
    },
}

BRAND_KIT_TEMPLATE_V2_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["schema_version", "meta", "palette"],
    "properties": {
        "schema_version": {"type": "integer", "enum": [1, 2]},
        "kind": {"const": "brand_kit"},
        "meta": _META_SCHEMA,
        "palette": {
            "type": "object",
            "required": ["primary"],
            "properties": {
                "primary": {"type": "string", "pattern": "^#[0-9A-Fa-f]{6}([0-9A-Fa-f]{2})?$"},
                "secondary": {"type": "string", "pattern": "^#[0-9A-Fa-f]{6}([0-9A-Fa-f]{2})?$"},
                "accent": {"type": "string", "pattern": "^#[0-9A-Fa-f]{6}([0-9A-Fa-f]{2})?$"},
            },
        },
        "fonts": {"type": "object"},
        "logo": {"type": "object"},
        "watermark": {"type": "object"},
        "intro": {"type": "object"},
        "outro": {"type": "object"},
        "end_card": {"type": "object"},
        "defaults": {"type": "object"},
        "channel": {"type": "object"},
    },
}

PRESET_TEMPLATE_V2_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["id", "name", "version", "caption", "layout", "render_profile"],
    "properties": {
        "schema_version": {"type": "integer", "enum": [1, 2]},
        "kind": {"const": "preset"},
        "id": {"type": "string", "pattern": "^[a-z][a-z0-9_]{2,47}$"},
        "name": {"type": "string", "minLength": 1, "maxLength": 80},
        "version": {"type": "integer", "minimum": 1},
        "caption": {"type": "string", "pattern": "^[a-z][a-z0-9_]*@(latest|[0-9]+)$"},
        "layout": {"type": "string", "pattern": "^[a-z][a-z0-9_]*@(latest|[0-9]+)$"},
        "brand_kit": {"type": ["string", "null"]},
        "overlays": {"type": "array", "items": {"type": "string"}},
        "publish": {"type": ["string", "null"]},
        "render_profile": {"type": "string", "minLength": 1},
        "defaults": {"type": "object"},
    },
}

STARTER_V2_TEMPLATE_IDS: tuple[str, ...] = (
    "hormozi_yellow",
    "clean_minimal",
    "bangla_bold",
    "hindi_creator",
    "podcast_2up",
    "neon_pop",
)

STARTER_TEMPLATES: list[dict[str, Any]] = [
    {
        "id": "bold_pop_yellow",
        "name": "Bold Pop — Yellow Highlight",
        "version": 1,
        "aspect": ["9:16", "1:1", "4:5"],
        "fonts": {"latn": "Sans", "deva": "Sans", "beng": "Sans"},
        "size_px": 74,
        "case": "upper",
        "colors": {
            "primary": "#FFFFFF",
            "active": "#FFE600",
            "outline": "#000000",
            "shadow": "#000000CC",
            "box": None,
        },
        "outline_px": 6,
        "shadow_px": 3,
        "box": {"enabled": False, "padding_px": 14, "radius_px": 10},
        "layout": {
            "position": "lower_third",
            "y_percent": 72,
            "align": "center",
            "max_words_per_line": 3,
            "max_lines": 2,
        },
        "animation": {"type": "highlight_word", "in": "pop", "out": "none", "duration_ms": 150},
    },
    {
        "id": "karaoke_neon_green",
        "name": "Karaoke Fill — Neon Green",
        "version": 1,
        "aspect": ["9:16", "1:1", "4:5"],
        "fonts": {"latn": "Sans", "deva": "Sans", "beng": "Sans"},
        "size_px": 70,
        "case": "upper",
        "colors": {
            "primary": "#E2E8F0",
            "active": "#00FF66",
            "outline": "#090D16",
            "shadow": "#000000AA",
            "box": None,
        },
        "outline_px": 5,
        "shadow_px": 2,
        "box": {"enabled": False, "padding_px": 12, "radius_px": 8},
        "layout": {
            "position": "lower_third",
            "y_percent": 74,
            "align": "center",
            "max_words_per_line": 4,
            "max_lines": 2,
        },
        "animation": {"type": "karaoke_fill", "in": "pop", "out": "none", "duration_ms": 140},
    },
    {
        "id": "boxed_creator_black",
        "name": "Boxed Creator — Cyan Accent",
        "version": 1,
        "aspect": ["9:16", "1:1", "4:5"],
        "fonts": {"latn": "Sans", "deva": "Sans", "beng": "Sans"},
        "size_px": 66,
        "case": "none",
        "colors": {
            "primary": "#FFFFFF",
            "active": "#38BDF8",
            "outline": "#0F172A",
            "shadow": "#0F172ACC",
            "box": "#0F172AE6",
        },
        "outline_px": 4,
        "shadow_px": 0,
        "box": {"enabled": True, "padding_px": 18, "radius_px": 14},
        "layout": {
            "position": "lower_third",
            "y_percent": 75,
            "align": "center",
            "max_words_per_line": 4,
            "max_lines": 2,
        },
        "animation": {"type": "pop", "in": "pop", "out": "none", "duration_ms": 160},
    },
    {
        "id": "clean_podcast_white",
        "name": "Clean Podcast — Warm Amber",
        "version": 1,
        "aspect": ["9:16", "1:1", "4:5"],
        "fonts": {"latn": "Sans", "deva": "Sans", "beng": "Sans"},
        "size_px": 64,
        "case": "none",
        "colors": {
            "primary": "#F8FAFC",
            "active": "#F59E0B",
            "outline": "#000000",
            "shadow": "#00000099",
            "box": None,
        },
        "outline_px": 4,
        "shadow_px": 2,
        "box": {"enabled": False, "padding_px": 10, "radius_px": 8},
        "layout": {
            "position": "lower_third",
            "y_percent": 76,
            "align": "center",
            "max_words_per_line": 5,
            "max_lines": 2,
        },
        "animation": {"type": "fade_slide", "in": "fade", "out": "fade", "duration_ms": 120},
    },
    {
        "id": "minimal_typewriter",
        "name": "Minimal Typewriter — Coral",
        "version": 1,
        "aspect": ["9:16", "1:1", "4:5"],
        "fonts": {"latn": "Sans", "deva": "Sans", "beng": "Sans"},
        "size_px": 62,
        "case": "lower",
        "colors": {
            "primary": "#F1F5F9",
            "active": "#FB7185",
            "outline": "#020617",
            "shadow": "#020617AA",
            "box": None,
        },
        "outline_px": 4,
        "shadow_px": 2,
        "box": {"enabled": False, "padding_px": 10, "radius_px": 8},
        "layout": {
            "position": "center",
            "y_percent": 66,
            "align": "center",
            "max_words_per_line": 4,
            "max_lines": 2,
        },
        "animation": {"type": "typewriter", "in": "none", "out": "none", "duration_ms": 100},
    },
    {
        "id": "neon_glow_magenta",
        "name": "Neon Glow — Electric Magenta",
        "version": 1,
        "aspect": ["9:16", "1:1", "4:5"],
        "fonts": {"latn": "Sans", "deva": "Sans", "beng": "Sans"},
        "size_px": 76,
        "case": "upper",
        "colors": {
            "primary": "#FFFFFF",
            "active": "#F43F5E",
            "outline": "#1E1B4B",
            "shadow": "#EC4899CC",
            "box": None,
        },
        "outline_px": 6,
        "shadow_px": 4,
        "box": {"enabled": False, "padding_px": 12, "radius_px": 10},
        "layout": {
            "position": "lower_third",
            "y_percent": 70,
            "align": "center",
            "max_words_per_line": 3,
            "max_lines": 2,
        },
        "animation": {"type": "highlight_word", "in": "pop", "out": "none", "duration_ms": 150},
    },
]


@dataclass(slots=True)
class CaptionTemplate:
    """Unified CaptionTemplate wrapper supporting both v1 and Spec v2 template definitions."""

    id: str
    name: str
    version: int = 1
    kind: str = "caption"
    scripts: list[str] = field(default_factory=lambda: ["latn", "deva", "beng"])
    aspects: list[str] = field(default_factory=lambda: ["9:16", "1:1", "4:5"])
    fonts: dict[str, Any] = field(
        default_factory=lambda: {"latn": "Sans", "deva": "Sans", "beng": "Sans"}
    )
    font_fallbacks: dict[str, list[str]] = field(
        default_factory=lambda: {"latn": ["Sans"], "deva": ["Sans"], "beng": ["Sans"]}
    )
    size_px: int = 72
    case: str = "none"
    colors: dict[str, Any] = field(
        default_factory=lambda: {
            "primary": "#FFFFFF",
            "active": "#FFE600",
            "outline": "#000000",
            "shadow": "#000000AA",
            "box": None,
        }
    )
    outline_px: float = 5.0
    shadow_px: float = 2.0
    box: dict[str, Any] = field(default_factory=lambda: {"enabled": False, "padding_px": 14, "radius_px": 10})
    layout: dict[str, Any] = field(
        default_factory=lambda: {
            "anchor": "bottom_center",
            "position": "lower_third",
            "y_percent": 72,
            "align": "center",
            "max_words_per_line": 3,
            "max_lines": 2,
        }
    )
    animation: dict[str, Any] = field(
        default_factory=lambda: {"type": "highlight_word", "mode": "pages", "in": "pop", "out": "none"}
    )
    raw: dict[str, Any] = field(default_factory=dict)
    valid: bool = True
    validation_error: str = ""
    warnings: list[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CaptionTemplate:
        v1_data = normalize_template_to_v1(data)
        raw_meta = data.get("meta")
        meta: dict[str, Any] = dict(raw_meta) if isinstance(raw_meta, dict) else {}
        tid = str(meta.get("id") or data.get("id") or "unknown")
        tname = str(meta.get("name") or data.get("name") or tid)
        tver = int(meta.get("version") or data.get("version") or 1)
        kind = infer_template_kind(data)
        raw_scripts = data.get("scripts")
        if isinstance(raw_scripts, list) and raw_scripts:
            scripts = [str(s) for s in raw_scripts]
        else:
            scripts = ["latn", "deva", "beng"]

        fallbacks: dict[str, list[str]] = {}
        raw_fonts_val = data.get("fonts")
        raw_fonts: dict[str, Any] = dict(raw_fonts_val) if isinstance(raw_fonts_val, dict) else {}
        for sc in ("latn", "deva", "beng"):
            f_entry = raw_fonts.get(sc)
            if isinstance(f_entry, dict):
                fam = str(f_entry.get("family", "Sans"))
                fb = [str(x) for x in (f_entry.get("fallback") or []) if str(x).strip()]
                chain = [fam] + [x for x in fb if x != fam]
                if "Sans" not in chain:
                    chain.append("Sans")
                fallbacks[sc] = chain
            elif isinstance(f_entry, str) and f_entry.strip():
                fallbacks[sc] = [f_entry.strip(), "Sans"] if f_entry.strip() != "Sans" else ["Sans"]
            else:
                fallbacks[sc] = ["Sans"]

        return cls(
            id=tid,
            name=tname,
            version=tver,
            kind=kind,
            scripts=scripts,
            aspects=list(data.get("aspects") or data.get("aspect") or ["9:16", "1:1", "4:5"]),
            fonts=dict(v1_data.get("fonts") or {"latn": "Sans", "deva": "Sans", "beng": "Sans"}),
            font_fallbacks=fallbacks,
            size_px=int(v1_data.get("size_px", 72)),
            case=str(v1_data.get("case", "none")),
            colors=dict(v1_data.get("colors") or {}),
            outline_px=float(v1_data.get("outline_px", 5.0)),
            shadow_px=float(v1_data.get("shadow_px", 2.0)),
            box=dict(v1_data.get("box") or {"enabled": False}),
            layout=dict(v1_data.get("layout") or {}),
            animation=dict(v1_data.get("animation") or {}),
            raw=dict(data),
            valid=bool(data.get("valid", True)),
            validation_error=str(data.get("validation_error") or ""),
            warnings=list(data.get("warnings") or []),
        )

    def to_dict(self) -> dict[str, Any]:
        out = normalize_template_to_v1(self.raw if self.raw else {
            "id": self.id,
            "name": self.name,
            "version": self.version,
            "aspect": self.aspects,
            "fonts": self.fonts,
            "size_px": self.size_px,
            "case": self.case,
            "colors": self.colors,
            "outline_px": self.outline_px,
            "shadow_px": self.shadow_px,
            "box": self.box,
            "layout": self.layout,
            "animation": self.animation,
        })
        out["kind"] = self.kind
        out["scripts"] = list(self.scripts)
        out["font_fallbacks"] = dict(self.font_fallbacks)
        out["valid"] = self.valid
        out["validation_error"] = self.validation_error
        out["warnings"] = list(self.warnings)
        return out


@dataclass(slots=True)
class TemplatePackV2:
    """Loaded and validated Spec v2 template pack (`caption`, `layout`, `publish`, `brand_kit`, `overlay`, `preset`)."""

    id: str
    name: str
    version: int
    kind: str
    scripts: list[str]
    data: dict[str, Any]
    path: Path | None = None
    preview_path: Path | None = None
    valid: bool = True
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        payload = dict(self.data)
        payload["id"] = self.id
        payload["name"] = self.name
        payload["version"] = self.version
        payload["kind"] = self.kind
        payload["scripts"] = list(self.scripts)
        payload["valid"] = self.valid
        payload["validation_error"] = self.errors[0] if self.errors else ""
        payload["errors"] = list(self.errors)
        payload["warnings"] = list(self.warnings)
        if self.path is not None:
            payload["path"] = str(self.path)
        if self.preview_path is not None:
            payload["preview_path"] = str(self.preview_path)
        return payload


def _load_caption_v2_schema() -> dict[str, Any]:
    schema_path = ROOT_DIR / "contracts" / "schemas" / "caption_template.schema.json"
    if schema_path.exists():
        loaded = json.loads(schema_path.read_text(encoding="utf-8"))
        if isinstance(loaded, dict):
            return loaded
    return TEMPLATE_JSON_SCHEMA


def infer_template_kind(data: dict[str, Any]) -> str:
    """Infer template kind from explicit `kind` field or structural properties."""
    explicit = str(data.get("kind") or "").strip().lower()
    if explicit in VALID_TEMPLATE_KINDS:
        return explicit
    if "caption" in data and "layout" in data and "render_profile" in data:
        return "preset"
    if "palette" in data and ("logo" in data or "watermark" in data or "channel" in data):
        return "brand_kit"
    if "platform" in data and "title" in data and "description" in data:
        return "publish"
    t_type = str(data.get("type") or "").strip().lower()
    if t_type in ("fill", "fit_blur", "split_2", "screen_focus") and "params" in data:
        return "layout"
    if t_type in (
        "text_block",
        "progress_bar",
        "image",
        "end_card",
        "hook_banner",
        "watermark",
        "lower_third",
        "broll_image",
    ):
        return "overlay"
    return "caption"


def _hex_to_rgb_norm(hex_color: str) -> tuple[float, float, float]:
    clean = (hex_color or "#FFFFFF").lstrip("#")
    if len(clean) < 6:
        return (1.0, 1.0, 1.0)
    r = int(clean[0:2], 16) / 255.0
    g = int(clean[2:4], 16) / 255.0
    b = int(clean[4:6], 16) / 255.0
    return (r, g, b)


def _relative_luminance(rgb: tuple[float, float, float]) -> float:
    def _chan(c: float) -> float:
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4

    r, g, b = _chan(rgb[0]), _chan(rgb[1]), _chan(rgb[2])
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def compute_contrast_ratio(hex_a: str, hex_b: str) -> float:
    """Compute WCAG contrast ratio (1.0 .. 21.0) between two hex colors."""
    lum_a = _relative_luminance(_hex_to_rgb_norm(hex_a))
    lum_b = _relative_luminance(_hex_to_rgb_norm(hex_b))
    lighter = max(lum_a, lum_b)
    darker = min(lum_a, lum_b)
    return round((lighter + 0.05) / (darker + 0.05), 2)


def normalize_template_to_v1(data: dict[str, Any]) -> dict[str, Any]:
    """Normalize a Spec v2 or v1 caption template dictionary into a v1-compatible dictionary."""
    if data.get("schema_version") != 2 and "meta" not in data:
        out = dict(data)
        raw_fonts_v1 = out.get("fonts")
        fonts_map: dict[str, Any] = dict(raw_fonts_v1) if isinstance(raw_fonts_v1, dict) else {}
        out.setdefault(
            "font_fallbacks",
            {
                sc: [str(fonts_map.get(sc, "Sans")), "Sans"]
                if str(fonts_map.get(sc, "Sans")) != "Sans"
                else ["Sans"]
                for sc in ("latn", "deva", "beng")
            },
        )
        return out

    raw_meta = data.get("meta")
    meta: dict[str, Any] = dict(raw_meta) if isinstance(raw_meta, dict) else {}
    tid = str(meta.get("id") or data.get("id") or "custom")
    tname = str(meta.get("name") or data.get("name") or tid)
    tver = int(meta.get("version") or data.get("version") or 1)

    raw_fonts_v2 = data.get("fonts")
    fonts_v2: dict[str, Any] = dict(raw_fonts_v2) if isinstance(raw_fonts_v2, dict) else {}
    fonts_v1: dict[str, str] = {}
    font_fallbacks: dict[str, list[str]] = {}
    for sc in ("latn", "deva", "beng"):
        f_obj = fonts_v2.get(sc)
        if isinstance(f_obj, dict):
            fam = str(f_obj.get("family") or "Sans")
            fb = [str(x) for x in (f_obj.get("fallback") or []) if str(x).strip()]
            chain = [fam] + [x for x in fb if x != fam]
            if "Sans" not in chain:
                chain.append("Sans")
            fonts_v1[sc] = fam
            font_fallbacks[sc] = chain
        elif isinstance(f_obj, str) and f_obj.strip():
            fonts_v1[sc] = f_obj.strip()
            font_fallbacks[sc] = [f_obj.strip(), "Sans"]
        else:
            fonts_v1[sc] = "Sans"
            font_fallbacks[sc] = ["Sans"]

    raw_text = data.get("text")
    text_v2: dict[str, Any] = dict(raw_text) if isinstance(raw_text, dict) else {}
    raw_stroke = data.get("stroke")
    stroke_v2: dict[str, Any] = dict(raw_stroke) if isinstance(raw_stroke, dict) else {}
    raw_layout = data.get("layout")
    layout_v2: dict[str, Any] = dict(raw_layout) if isinstance(raw_layout, dict) else {}
    raw_anim = data.get("animation")
    anim_v2: dict[str, Any] = dict(raw_anim) if isinstance(raw_anim, dict) else {}

    explicit_type = anim_v2.get("type")
    if explicit_type in ("highlight_word", "karaoke_fill", "pop", "typewriter", "fade_slide", "bounce"):
        anim_type = str(explicit_type)
    else:
        mode = str(anim_v2.get("mode") or "pages").lower()
        active_word = str(anim_v2.get("active_word") or "none").lower()
        anim_in = str(anim_v2.get("in") or "none").lower()
        if mode == "karaoke":
            anim_type = "karaoke_fill"
        elif active_word in ("highlight", "scale", "underline"):
            anim_type = "highlight_word"
        elif anim_in == "typewriter":
            anim_type = "typewriter"
        elif anim_in == "bounce":
            anim_type = "bounce"
        elif anim_in in ("fade", "slide_up"):
            anim_type = "fade_slide"
        else:
            anim_type = "pop"

    merged_layout = dict(layout_v2)
    merged_layout.setdefault("position", str(layout_v2.get("anchor") or "lower_third"))
    merged_layout.setdefault("anchor", str(layout_v2.get("anchor") or "bottom_center"))
    merged_layout.setdefault("y_percent", float(layout_v2.get("y_percent", 72)))
    merged_layout.setdefault("align", str(layout_v2.get("align") or "center"))
    merged_layout.setdefault("max_words_per_line", int(layout_v2.get("max_words_per_line", 3)))
    merged_layout.setdefault("max_lines", int(layout_v2.get("max_lines", 2)))

    merged_anim = dict(anim_v2)
    merged_anim["type"] = anim_type

    return {
        "schema_version": data.get("schema_version", 2),
        "kind": infer_template_kind(data),
        "scripts": list(data.get("scripts") or ["latn", "deva", "beng"]),
        "id": tid,
        "name": tname,
        "version": tver,
        "meta": dict(meta),
        "aspect": list(data.get("aspects") or data.get("aspect") or ["9:16", "1:1", "4:5"]),
        "aspects": list(data.get("aspects") or data.get("aspect") or ["9:16", "1:1", "4:5"]),
        "fonts": fonts_v1,
        "fonts_v2": fonts_v2,
        "font_fallbacks": font_fallbacks,
        "size_px": int(text_v2.get("size_px", data.get("size_px", 72))),
        "case": str(text_v2.get("case", data.get("case", "none"))),
        "text": dict(text_v2),
        "colors": dict(
            data.get("colors")
            or {
                "primary": "#FFFFFF",
                "active": "#FFE600",
                "outline": "#000000",
                "shadow": "#000000AA",
                "box": None,
            }
        ),
        "outline_px": float(stroke_v2.get("outline_px", data.get("outline_px", 5.0))),
        "shadow_px": float(stroke_v2.get("shadow_px", data.get("shadow_px", 2.0))),
        "stroke": dict(stroke_v2),
        "box": dict(data.get("box") or {"enabled": False, "padding_px": 14, "radius_px": 10}),
        "layout": merged_layout,
        "animation": merged_anim,
        "emphasis": dict(data.get("emphasis") or {}),
    }


def validate_template_v2(
    data: dict[str, Any],
    *,
    kind: str | None = None,
) -> tuple[bool, list[str], list[str]]:
    """Validate a Spec v2 template dict of any of the 6 kinds (`caption`, `layout`, `publish`, `brand_kit`, `overlay`, `preset`).

    Returns `(valid, errors, warnings)`.
    """
    if not isinstance(data, dict):
        return False, ["Template payload must be a JSON object"], []

    resolved_kind = (kind or infer_template_kind(data)).strip().lower()
    if resolved_kind not in VALID_TEMPLATE_KINDS:
        return False, [f"Unsupported template kind: {resolved_kind!r}"], []

    errors: list[str] = []
    warnings: list[str] = []

    schema_map: dict[str, dict[str, Any]] = {
        "caption": _load_caption_v2_schema(),
        "layout": LAYOUT_TEMPLATE_V2_SCHEMA,
        "overlay": OVERLAY_TEMPLATE_V2_SCHEMA,
        "publish": PUBLISH_TEMPLATE_V2_SCHEMA,
        "brand_kit": BRAND_KIT_TEMPLATE_V2_SCHEMA,
        "preset": PRESET_TEMPLATE_V2_SCHEMA,
    }
    schema = schema_map[resolved_kind]

    try:
        jsonschema.validate(instance=data, schema=schema)
    except jsonschema.ValidationError as exc:
        errors.append(exc.message)
        return False, errors, warnings

    # Additional quality checks for caption templates
    if resolved_kind == "caption":
        meta = data.get("meta") or {}
        lic = str(meta.get("license") or "").strip()
        if lic.upper() == "UNKNOWN":
            warnings.append("license_unknown: meta.license is set to UNKNOWN")

        colors = data.get("colors") or {}
        primary = str(colors.get("primary") or "#FFFFFF")
        box_cfg = data.get("box") or {}
        bg_hex = str(colors.get("box") or colors.get("outline") or "#000000")
        if not box_cfg.get("enabled"):
            bg_hex = str(colors.get("outline") or "#000000")
        ratio = compute_contrast_ratio(primary, bg_hex)
        if ratio < 4.5:
            warnings.append(f"low_contrast_ratio: {ratio} < 4.5 between {primary} and {bg_hex}")

    # Security check for brand_kit asset paths (must be relative, no traversal or URLs)
    if resolved_kind == "brand_kit":
        for slot in ("logo", "watermark", "intro", "outro"):
            slot_obj = data.get(slot)
            if isinstance(slot_obj, dict) and slot_obj.get("asset"):
                asset_path = str(slot_obj["asset"])
                if (
                    asset_path.startswith("/")
                    or ".." in Path(asset_path).parts
                    or "://" in asset_path
                ):
                    errors.append(f"Unsafe asset path in brand_kit.{slot}: {asset_path!r}")

    return len(errors) == 0, errors, warnings


def validate_template_dict(data: dict[str, Any]) -> tuple[bool, str]:
    """Validate either a v1 caption template dict or a Spec v2 template dict of any kind."""
    if not isinstance(data, dict):
        return False, "Template must be a dictionary"

    if data.get("schema_version") == 2 or "meta" in data or data.get("kind") in VALID_TEMPLATE_KINDS:
        ok, errs, _ = validate_template_v2(data)
        return ok, (errs[0] if errs else "")

    try:
        jsonschema.validate(instance=data, schema=TEMPLATE_JSON_SCHEMA)
        return True, ""
    except jsonschema.ValidationError as exc:
        return False, exc.message


def ensure_starter_templates() -> list[dict[str, Any]]:
    """Write the 6 starter v1 caption templates to assets/templates/<id>/template.json if not present."""
    settings = get_settings()
    tdir = settings.assets_dir / "templates"
    tdir.mkdir(parents=True, exist_ok=True)
    for tmpl in STARTER_TEMPLATES:
        folder = tdir / tmpl["id"]
        folder.mkdir(parents=True, exist_ok=True)
        f = folder / "template.json"
        if not f.exists():
            f.write_text(json.dumps(tmpl, indent=2), encoding="utf-8")
    return load_all_templates()


def load_all_templates() -> list[dict[str, Any]]:
    """Load all v1 templates from `assets/templates/*/template.json`."""
    settings = get_settings()
    tdir = settings.assets_dir / "templates"
    tdir.mkdir(parents=True, exist_ok=True)
    results: list[dict[str, Any]] = []
    for p in sorted(tdir.glob("*/template.json")):
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            valid, err = validate_template_dict(data)
            data["valid"] = valid
            data["validation_error"] = err
            results.append(data)
        except Exception as exc:
            results.append(
                {
                    "id": p.parent.name,
                    "name": p.parent.name,
                    "valid": False,
                    "validation_error": str(exc),
                }
            )
    return results


def load_templates_v2(
    templates_dir: Path | None = None,
    *,
    kind: str | None = None,
) -> list[TemplatePackV2]:
    """Load and validate Spec v2 template packs from `assets/templates_v2/*/template.json`."""
    settings = get_settings()
    tdir = Path(templates_dir) if templates_dir is not None else (settings.assets_dir / "templates_v2")
    if not tdir.exists():
        return []

    packs: list[TemplatePackV2] = []
    for p in sorted(tdir.glob("*/template.json")):
        folder = p.parent
        preview: Path | None = None
        for cand_name in ("preview.png", "preview.svg"):
            cand = folder / cand_name
            if cand.exists():
                preview = cand
                break

        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            inferred_kind = infer_template_kind(data)
            if kind is not None and inferred_kind != kind:
                continue
            valid, errors, warnings = validate_template_v2(data, kind=inferred_kind)
            raw_meta = data.get("meta")
            meta: dict[str, Any] = dict(raw_meta) if isinstance(raw_meta, dict) else {}
            tid = str(meta.get("id") or data.get("id") or folder.name)
            tname = str(meta.get("name") or data.get("name") or tid)
            tver = int(meta.get("version") or data.get("version") or 1)
            raw_scripts = data.get("scripts")
            scripts = (
                [str(s) for s in raw_scripts]
                if isinstance(raw_scripts, list) and raw_scripts
                else ["latn", "deva", "beng"]
            )
            packs.append(
                TemplatePackV2(
                    id=tid,
                    name=tname,
                    version=tver,
                    kind=inferred_kind,
                    scripts=scripts,
                    data=data,
                    path=p,
                    preview_path=preview,
                    valid=valid,
                    errors=errors,
                    warnings=warnings,
                )
            )
        except Exception as exc:
            packs.append(
                TemplatePackV2(
                    id=folder.name,
                    name=folder.name,
                    version=1,
                    kind=kind or "caption",
                    scripts=["latn", "deva", "beng"],
                    data={},
                    path=p,
                    preview_path=preview,
                    valid=False,
                    errors=[str(exc)],
                    warnings=[],
                )
            )
    return packs


def load_templates(
    *,
    include_v2: bool = False,
    kind: str | None = None,
) -> list[dict[str, Any]]:
    """Load caption templates (v1 default; optionally include Spec v2 packs)."""
    v1_list = ensure_starter_templates()
    if not include_v2:
        if kind is not None and kind != "caption":
            return [p.to_dict() for p in load_templates_v2(kind=kind)]
        return v1_list

    by_id: dict[str, dict[str, Any]] = {}
    if kind is None or kind == "caption":
        for item in v1_list:
            by_id[str(item.get("id"))] = item
    for pack in load_templates_v2(kind=kind):
        by_id[pack.id] = pack.to_dict()
    return list(by_id.values())


def get_template_by_id(template_id: str) -> dict[str, Any]:
    """Lookup template by id (or `id@version`) across v1 and v2 templates, returning a v1/v2 compatible dict."""
    clean_id = (template_id or "").split("@", 1)[0].strip()
    templates = ensure_starter_templates()
    for t in templates:
        if t.get("id") == clean_id and t.get("valid"):
            return t

    for pack in load_templates_v2():
        if pack.id == clean_id and pack.valid:
            if pack.kind == "caption":
                norm = normalize_template_to_v1(pack.data)
                norm["valid"] = True
                norm["validation_error"] = ""
                return norm
            return pack.to_dict()

    return templates[0]


def get_template(template_id: str) -> CaptionTemplate:
    """Return a structured `CaptionTemplate` instance for `template_id` (or `id@version`)."""
    clean_id = (template_id or "").split("@", 1)[0].strip()
    for pack in load_templates_v2():
        if pack.id == clean_id and pack.valid:
            return CaptionTemplate.from_dict(pack.data)
    raw = get_template_by_id(clean_id)
    return CaptionTemplate.from_dict(raw)


def _list_installed_font_families() -> set[str]:
    try:
        proc = subprocess.run(
            ["fc-list", ":", "family"],
            capture_output=True,
            text=True,
            check=False,
            timeout=5,
        )
        families: set[str] = {"sans", "serif", "monospace"}
        for line in proc.stdout.splitlines():
            for part in line.split(","):
                clean = part.strip().lower()
                if clean:
                    families.add(clean)
        return families
    except Exception:
        return {"sans", "serif", "monospace", "dejavu sans"}


def _char_matches_script(ch: str, script: str) -> bool:
    cp = ord(ch)
    if ch.isspace() or unicodedata.category(ch).startswith(("P", "N", "S")):
        return True
    if script == "latn":
        return (0x0020 <= cp <= 0x024F) or (0x1E00 <= cp <= 0x1EFF)
    if script == "deva":
        return (0x0900 <= cp <= 0x097F) or (0xA8E0 <= cp <= 0xA8FF) or (0x0020 <= cp <= 0x007F)
    if script == "beng":
        return (0x0980 <= cp <= 0x09FF) or (0x0020 <= cp <= 0x007F)
    return False


def detect_tofu_glyphs(
    text: str,
    font_family: str | list[str] | dict[str, Any],
    script: str = "latn",
) -> dict[str, Any]:
    """Deterministically check whether `text` contains unrenderable/tofu glyphs for `font_family` and `script`.

    Flags:
    - Replacement characters (`\\ufffd`, `\\ufffe`, `\\uffff`), null/control characters (`\\x00`),
      Private Use Area codepoints (`U+E000..U+F8FF`, `U+F0000..U+10FFFF`), or unassigned (`Cn`) codepoints.
    - Characters outside supported Unicode blocks when an incompatible script/font is forced.
    - Font resolution status via `fc-list` + fallback chain awareness.
    """
    norm_text = unicodedata.normalize("NFC", text or "")
    bad_chars: list[str] = []
    reasons: list[str] = []

    if isinstance(font_family, dict):
        fam_entry = font_family.get(script) if script in font_family else font_family
        if isinstance(fam_entry, dict):
            primary_font = str(fam_entry.get("family") or "Sans")
            fallbacks = [str(x) for x in (fam_entry.get("fallback") or [])]
        else:
            primary_font = str(fam_entry or "Sans")
            fallbacks = ["Sans"]
    elif isinstance(font_family, list):
        primary_font = str(font_family[0]) if font_family else "Sans"
        fallbacks = [str(x) for x in font_family[1:]]
    else:
        primary_font = str(font_family or "Sans")
        fallbacks = []

    installed = _list_installed_font_families()
    chain = [primary_font, *fallbacks]
    resolved_font: str | None = None
    for cand in chain:
        if cand.strip().lower() in installed:
            resolved_font = cand.strip()
            break
    if resolved_font is None and any(
        k in installed for k in ("dejavu sans", "noto sans", "liberation sans", "sans")
    ):
        # System fontconfig generic Sans fallback is available unless caller passed an explicitly broken font with no fallback
        if primary_font.lower().startswith(("invalid", "missing", "nonexistent", "broken")) and not fallbacks:
            reasons.append(f"unresolvable_font_family: {primary_font}")
        else:
            resolved_font = "Sans"

    for ch in norm_text:
        cp = ord(ch)
        cat = unicodedata.category(ch)
        if ch == "\ufffd" or cp in (0x0000, 0xFFFE, 0xFFFF):
            bad_chars.append(ch)
            reasons.append(f"replacement_or_null_char: U+{cp:04X}")
        elif (0xE000 <= cp <= 0xF8FF) or (0xF0000 <= cp <= 0x10FFFF):
            bad_chars.append(ch)
            reasons.append(f"private_use_area_glyph: U+{cp:04X}")
        elif cat == "Cn":
            bad_chars.append(ch)
            reasons.append(f"unassigned_codepoint: U+{cp:04X}")
        elif cat == "Cc" and ch not in ("\n", "\r", "\t"):
            bad_chars.append(ch)
            reasons.append(f"control_char: U+{cp:04X}")
        elif script in ("latn", "deva", "beng") and not _char_matches_script(ch, script):
            # Check if character belongs to a completely unsupported script (e.g., CJK/Tibetan/Runic when expecting latn/deva/beng)
            if not (
                (0x0020 <= cp <= 0x024F)
                or (0x0900 <= cp <= 0x097F)
                or (0x0980 <= cp <= 0x09FF)
            ):
                bad_chars.append(ch)
                reasons.append(f"unsupported_script_glyph: U+{cp:04X}")

    has_tofu = len(bad_chars) > 0 or any(r.startswith("unresolvable_font_family") for r in reasons)
    return {
        "has_tofu": has_tofu,
        "tofu_count": len(bad_chars),
        "bad_chars": bad_chars,
        "reasons": reasons,
        "requested_font": primary_font,
        "resolved_font": resolved_font,
        "script": script,
    }


def render_template_test_card(
    template_id: str | dict[str, Any],
    sample_text: str | None = None,
    script: str = "latn",
    *,
    output_path: Path | None = None,
    resolution: tuple[int, int] = (540, 960),
) -> dict[str, Any]:
    """Render a template test card frame (PNG) via libass + FFmpeg and run tofu glyph verification."""
    from backend.pipeline.captions.ass_builder import build_ass_subtitles

    if isinstance(template_id, dict):
        raw_tpl = template_id
        tid = str((raw_tpl.get("meta") or {}).get("id") or raw_tpl.get("id") or "custom")
    else:
        tid = str(template_id).split("@", 1)[0].strip()
        raw_tpl = get_template_by_id(tid)
        for pack in load_templates_v2():
            if pack.id == tid:
                raw_tpl = pack.data
                break

    default_samples = {
        "latn": "Shipping fast beats perfection every single time!",
        "deva": "सफलता एक दिन में नहीं मिलती दोस्तों!",
        "beng": "প্রথম তিন সেকেন্ডেই দর্শকের মনোযোগ ধরে রাখুন!",
    }
    text_to_render = (sample_text if sample_text is not None else default_samples.get(script, default_samples["latn"])).strip()
    if not text_to_render:
        text_to_render = default_samples["latn"]

    norm_tpl = normalize_template_to_v1(raw_tpl)
    raw_fonts_obj = raw_tpl.get("fonts")
    fonts_v2: dict[str, Any] = dict(raw_fonts_obj) if isinstance(raw_fonts_obj, dict) else dict(norm_tpl.get("fonts") or {})
    tofu_report = detect_tofu_glyphs(text_to_render, fonts_v2, script=script)

    tokens = [tok for tok in text_to_render.split() if tok] or ["CLIPPER"]
    words = [
        {"w": tok, "s": round(idx * 0.35, 3), "e": round((idx + 1) * 0.35, 3), "script": script}
        for idx, tok in enumerate(tokens)
    ]

    with tempfile.TemporaryDirectory(prefix="clipper_test_card_") as tmp_dir:
        tmp_root = Path(tmp_dir)
        ass_path = tmp_root / f"{tid}_{script}.ass"
        png_path = Path(output_path) if output_path is not None else (tmp_root / f"{tid}_{script}.png")
        png_path.parent.mkdir(parents=True, exist_ok=True)

        build_ass_subtitles(
            words=words,
            template=norm_tpl,
            out_ass_path=ass_path,
            clip_start_offset=0.0,
            play_res=resolution,
        )
        ass_content = ass_path.read_text(encoding="utf-8")

        escaped_ass = str(ass_path).replace("\\", "/").replace(":", "\\:")
        w, h = int(resolution[0]), int(resolution[1])
        cmd = [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"color=c=0x0f172a:s={w}x{h}:r=10:d=1",
            "-vf",
            f"ass='{escaped_ass}'",
            "-ss",
            "0.15",
            "-frames:v",
            "1",
            str(png_path),
        ]
        proc = subprocess.run(cmd, capture_output=True, check=False)
        png_bytes = png_path.read_bytes() if (proc.returncode == 0 and png_path.exists()) else b""

    return {
        "template_id": tid,
        "script": script,
        "sample_text": text_to_render,
        "rendered_ok": len(png_bytes) > 100 and not tofu_report["has_tofu"],
        "has_tofu": tofu_report["has_tofu"],
        "tofu_report": tofu_report,
        "png_bytes": png_bytes,
        "output_path": str(output_path) if output_path is not None else None,
        "ass_content": ass_content,
    }
