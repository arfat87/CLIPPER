from __future__ import annotations

import json
from typing import Any

import jsonschema

from backend.app.config import get_settings

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
                    "enum": ["highlight_word", "karaoke_fill", "pop", "typewriter", "fade_slide"],
                },
                "in": {"type": "string"},
                "out": {"type": "string"},
                "duration_ms": {"type": "integer"},
            },
        },
    },
}


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


def ensure_starter_templates() -> list[dict[str, Any]]:
    """Write the 6 starter caption templates to assets/templates/<id>/template.json if not present."""
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


def validate_template_dict(data: dict[str, Any]) -> tuple[bool, str]:
    try:
        jsonschema.validate(instance=data, schema=TEMPLATE_JSON_SCHEMA)
        return True, ""
    except jsonschema.ValidationError as exc:
        return False, exc.message


def load_all_templates() -> list[dict[str, Any]]:
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


def get_template_by_id(template_id: str) -> dict[str, Any]:
    templates = ensure_starter_templates()
    for t in templates:
        if t.get("id") == template_id and t.get("valid"):
            return t
    return templates[0]
