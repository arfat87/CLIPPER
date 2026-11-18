from __future__ import annotations

import json
import re
import shutil
import tempfile
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import jsonschema  # type: ignore[import-untyped]

from backend.app.config import ROOT_DIR

_SAFE_ID_RE = re.compile(r"^[a-z][a-z0-9_]{2,47}$")

DEFAULT_TEMPLATE_META_SCHEMA: dict[str, Any] = {
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


class DriveTemplateImportError(ValueError):
    """Raised when a Drive template fails safety or schema validation."""


def _load_validation_schema(schema_path: Path | None = None) -> dict[str, Any]:
    """Load template validation schema from `schema_path`, `template_meta.schema.json`, or `caption_template.schema.json`."""
    candidates: list[Path] = []
    if schema_path is not None:
        candidates.append(Path(schema_path))
    candidates.extend(
        [
            ROOT_DIR / "contracts" / "schemas" / "template_meta.schema.json",
            ROOT_DIR / "contracts" / "schemas" / "caption_template.schema.json",
        ]
    )
    for p in candidates:
        if p.exists():
            loaded: dict[str, Any] = json.loads(p.read_text(encoding="utf-8"))
            return loaded
    return DEFAULT_TEMPLATE_META_SCHEMA


def _extract_template_id_and_meta(raw_doc: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    """Extract and validate `(template_id, meta_obj)` from either Spec v2 (`meta.id`) or v1 (`id`) format."""
    meta = raw_doc.get("meta")
    if isinstance(meta, dict):
        meta_obj = dict(meta)
    else:
        meta_obj = {
            "id": raw_doc.get("id", ""),
            "name": raw_doc.get("name", ""),
            "version": raw_doc.get("version", 1),
            "license": raw_doc.get("license", "Owner Custom"),
        }
        if "author" in raw_doc:
            meta_obj["author"] = raw_doc["author"]
        if "tags" in raw_doc:
            meta_obj["tags"] = raw_doc["tags"]

    jsonschema.validate(instance=meta_obj, schema=DEFAULT_TEMPLATE_META_SCHEMA)
    tpl_id = str(meta_obj["id"]).strip()
    if not _SAFE_ID_RE.match(tpl_id):
        raise DriveTemplateImportError(f"Unsafe or invalid template id: {tpl_id!r}")
    return tpl_id, meta_obj


def _validate_template_payload(
    raw_doc: dict[str, Any],
    schema: dict[str, Any],
) -> tuple[str, dict[str, Any]]:
    """Validate a candidate template dict against `schema` and return `(template_id, normalized_doc)`."""
    if not isinstance(raw_doc, dict):
        raise DriveTemplateImportError("Template definition must be a JSON object.")

    tpl_id, meta_obj = _extract_template_id_and_meta(raw_doc)

    # Validate against provided schema if it applies to the full document or meta block
    schema_required = set(schema.get("required", []))
    if "meta" in schema_required and "meta" in raw_doc:
        jsonschema.validate(instance=raw_doc, schema=schema)
    elif schema_required.issubset({"id", "name", "version", "license", "author", "tags"}):
        jsonschema.validate(instance=meta_obj, schema=schema)
    elif "meta" in raw_doc:
        jsonschema.validate(instance=raw_doc, schema=schema)

    now_iso = datetime.now(UTC).isoformat()
    doc = dict(raw_doc)
    doc["id"] = tpl_id
    doc["meta"] = meta_obj
    # Safety & provenance enforcement per Spec v2
    doc["status"] = "draft"
    doc["source"] = "drive"
    doc["imported_at"] = now_iso
    doc["provenance"] = {
        "source": "drive",
        "folder": "01_Templates",
        "status": "draft",
        "imported_at": now_iso,
    }
    return tpl_id, doc


def _is_safe_zip_member(member_name: str, dest_dir: Path) -> bool:
    """Return False if `member_name` attempts path traversal (`..`, absolute path, or symlink escape)."""
    if not member_name or member_name.startswith(("/", "\\")):
        return False
    norm_parts = Path(member_name).parts
    if ".." in norm_parts:
        return False
    resolved_target = (dest_dir / member_name).resolve()
    resolved_root = dest_dir.resolve()
    return resolved_target == resolved_root or resolved_root in resolved_target.parents


def _safe_extract_zip(zip_path: Path, extract_dir: Path) -> None:
    """Safely extract a `.cliptpl.zip` archive, rejecting any path traversal entry."""
    with zipfile.ZipFile(zip_path, "r") as zf:
        for info in zf.infolist():
            if not _is_safe_zip_member(info.filename, extract_dir):
                raise DriveTemplateImportError(
                    f"Path traversal detected in archive {zip_path.name}: {info.filename!r}"
                )
        for info in zf.infolist():
            target_path = (extract_dir / info.filename).resolve()
            if info.is_dir():
                target_path.mkdir(parents=True, exist_ok=True)
            else:
                target_path.parent.mkdir(parents=True, exist_ok=True)
                with zf.open(info, "r") as src, target_path.open("wb") as dst:
                    shutil.copyfileobj(src, dst)


def _import_single_template_dir(
    src_dir: Path,
    target_templates_dir: Path,
    schema: dict[str, Any],
) -> dict[str, Any]:
    tpl_json_path = src_dir / "template.json"
    if not tpl_json_path.exists():
        # Check if template.json is nested 1 level inside zip extraction
        nested = list(src_dir.glob("*/template.json"))
        if len(nested) == 1:
            src_dir = nested[0].parent
            tpl_json_path = nested[0]
        else:
            raise DriveTemplateImportError(f"Missing template.json in {src_dir}")

    raw_doc = json.loads(tpl_json_path.read_text(encoding="utf-8"))
    tpl_id, validated_doc = _validate_template_payload(raw_doc, schema)

    dest_dir = target_templates_dir / tpl_id
    dest_dir.mkdir(parents=True, exist_ok=True)

    # Copy auxiliary assets (preview.png, fonts, etc.) safely without path traversal
    for item in src_dir.iterdir():
        if item.name == "template.json" or item.is_symlink():
            continue
        if item.is_file():
            shutil.copy2(item, dest_dir / item.name)

    out_json = dest_dir / "template.json"
    out_json.write_text(
        json.dumps(validated_doc, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return validated_doc


def import_owner_templates_from_drive(
    drive_templates_dir: Path,
    target_templates_dir: Path,
    *,
    schema_path: Path | None = None,
) -> list[dict[str, Any]]:
    """Import owner caption/layout/publish templates from a mirrored Drive `01_Templates` directory.

    Supports:
    - Template folders containing `template.json`
    - Standalone `<name>.json` template files
    - Packaged `<name>.cliptpl.zip` (or `.zip`) archives (with strict zip-slip path traversal rejection)

    Every imported template is schema-validated, marked `status = "draft"`, tagged with
    `source = "drive"` and `imported_at`, and written to `target_templates_dir/<id>/template.json`.
    """
    src_root = Path(drive_templates_dir)
    dst_root = Path(target_templates_dir)
    if not src_root.exists():
        return []

    dst_root.mkdir(parents=True, exist_ok=True)
    schema = _load_validation_schema(schema_path)
    imported: list[dict[str, Any]] = []

    for entry in sorted(src_root.iterdir(), key=lambda p: p.name):
        if entry.name.startswith("."):
            continue

        # Case 1: Template subdirectory containing template.json
        if entry.is_dir():
            if (entry / "template.json").exists():
                doc = _import_single_template_dir(entry, dst_root, schema)
                imported.append(doc)
            continue

        # Case 2: .cliptpl.zip or .zip archive
        if entry.is_file() and entry.name.endswith(".zip"):
            with tempfile.TemporaryDirectory() as tmp_extract:
                extract_path = Path(tmp_extract)
                _safe_extract_zip(entry, extract_path)
                doc = _import_single_template_dir(extract_path, dst_root, schema)
                imported.append(doc)
            continue

        # Case 3: Standalone .json template definition
        if entry.is_file() and entry.suffix.lower() == ".json":
            raw_doc = json.loads(entry.read_text(encoding="utf-8"))
            tpl_id, validated_doc = _validate_template_payload(raw_doc, schema)
            dest_dir = dst_root / tpl_id
            dest_dir.mkdir(parents=True, exist_ok=True)
            (dest_dir / "template.json").write_text(
                json.dumps(validated_doc, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            imported.append(validated_doc)

    return imported
