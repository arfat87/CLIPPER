from __future__ import annotations

import io
import json
import os
import tempfile
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import jsonschema  # type: ignore[import-untyped]
from sqlmodel import Session, col, select

from backend.app.models.entities import (
    Clip,
    Job,
    PublishPack,
    Render,
    Template,
    TemplateVersion,
    Transcript,
)
from backend.app.services.admin import AdminError
from backend.app.services.storage import get_storage
from backend.pipeline.providers.base import LLMProvider
from backend.pipeline.publish import (
    VALID_LANGUAGES,
    VALID_PLATFORMS,
    build_publish_pack,
    format_publish_pack_markdown,
    load_platforms_config,
    load_publish_pack_schema,
    normalize_hashtags,
    validate_publish_pack,
)


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=".tmp_", dir=str(path.parent))
    tmp_path = Path(tmp_name)
    try:
        data = text.encode("utf-8")
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_path, path)
    except Exception:
        tmp_path.unlink(missing_ok=True)
        raise


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    _atomic_write_text(path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")


def _get_clip_and_job_or_404(session: Session, clip_id: str) -> tuple[Clip, Job]:
    clip = session.get(Clip, clip_id)
    if clip is None:
        raise AdminError(404, "VALIDATION_ERROR", f"Clip {clip_id} not found")
    job = session.get(Job, clip.job_id)
    if job is None or job.deleted_at is not None:
        raise AdminError(404, "VALIDATION_ERROR", f"Clip {clip_id} not found")
    return clip, job


def _resolve_clip_language(clip: Clip, job: Job, requested_lang: str | None = None) -> str:
    candidates = [
        requested_lang,
        job.detected_language if job.language == "auto" else job.language,
        job.detected_language,
        "en",
    ]
    for cand in candidates:
        if cand:
            norm = str(cand).strip().lower()
            if norm in VALID_LANGUAGES:
                return norm
    return "en"


def _resolve_clip_transcript(session: Session, clip: Clip, job: Job, lang: str) -> dict[str, Any]:
    job_dir = get_storage().job_dir(job.id)
    tr_row = session.exec(select(Transcript).where(Transcript.job_id == job.id)).first()
    tp = Path(tr_row.path_json) if tr_row and tr_row.path_json else (job_dir / "asr" / "transcript.json")
    if tp.exists():
        try:
            loaded = json.loads(tp.read_text(encoding="utf-8"))
            if isinstance(loaded, dict) and isinstance(loaded.get("segments"), list):
                loaded.setdefault("title", job.title)
                loaded.setdefault("source_url", job.source_url)
                loaded.setdefault("language", lang)
                return loaded
        except Exception:
            pass

    words = []
    try:
        words = json.loads(clip.words_json or "[]")
        if not isinstance(words, list):
            words = []
    except Exception:
        words = []

    text = (
        clip.transcript_snippet
        or " ".join(str(w.get("w", "")) for w in words if isinstance(w, dict)).strip()
        or clip.hook_line
        or clip.title
        or job.title
        or "Clip highlight"
    )
    start_val = float(clip.start_s if clip.start_s is not None else clip.start)
    end_val = float(clip.end_s if clip.end_s is not None else clip.end)
    script = job.script if job.script in ("latn", "deva", "beng") else (
        "deva" if lang == "hi" else ("beng" if lang == "bn" else "latn")
    )
    return {
        "schema_version": 1,
        "title": job.title,
        "source_url": job.source_url,
        "language": lang,
        "script": script,
        "duration": max(1.0, end_val),
        "engine": "clip_fallback",
        "segments": [
            {
                "id": 0,
                "start": start_val,
                "end": max(start_val + 1.0, end_val),
                "text": text,
                "words": words,
            }
        ],
    }


def _load_publish_template_dict(
    session: Session,
    template_id: str | None,
    *,
    tone: str | None = None,
) -> dict[str, Any] | None:
    result: dict[str, Any] = {}
    if template_id:
        clean_tid = template_id.split("@")[0].strip()
        tpl = session.get(Template, clean_tid)
        if tpl is not None:
            ver = session.exec(
                select(TemplateVersion)
                .where(TemplateVersion.template_id == tpl.id)
                .where(TemplateVersion.version == tpl.latest_version)
            ).first()
            if ver and ver.path and Path(ver.path).exists():
                try:
                    loaded = json.loads(Path(ver.path).read_text(encoding="utf-8"))
                    if isinstance(loaded, dict):
                        result = dict(loaded)
                except Exception:
                    pass
        if not result:
            result = {"id": clean_tid, "meta": {"id": clean_tid}}

    if tone:
        clean_tone = tone.strip()
        if clean_tone:
            vars_dict = dict(result.get("variables") or {})
            vars_dict["tone"] = clean_tone
            result["variables"] = vars_dict
            result.setdefault("id", template_id or f"default_{clean_tone}")

    return result or None


def _persist_publish_pack(
    session: Session,
    clip: Clip,
    pack: dict[str, Any],
    *,
    template_id: str | None = None,
) -> dict[str, Any]:
    job_dir = get_storage().job_dir(clip.job_id)
    clip_dir = job_dir / "clips" / clip.id
    clip_dir.mkdir(parents=True, exist_ok=True)
    json_path = clip_dir / "publish_pack.json"
    md_path = clip_dir / "publish_pack.md"

    _atomic_write_json(json_path, pack)
    _atomic_write_text(md_path, format_publish_pack_markdown(pack))

    platform = str(pack.get("platform") or "youtube_shorts")
    pp_row = session.exec(
        select(PublishPack)
        .where(PublishPack.clip_id == clip.id)
        .where(PublishPack.platform == platform)
    ).first()
    if pp_row is None:
        pp_row = session.exec(select(PublishPack).where(PublishPack.clip_id == clip.id)).first()

    now = _now_iso()
    if pp_row is None:
        pp_row = PublishPack(
            clip_id=clip.id,
            platform=platform,
            path_json=str(json_path),
            valid=bool(pack.get("valid", True)),
            warnings_json=list(pack.get("warnings") or []),
            template_id=template_id or str(pack.get("template") or ""),
            created_at=now,
            updated_at=now,
        )
    else:
        pp_row.platform = platform
        pp_row.path_json = str(json_path)
        pp_row.valid = bool(pack.get("valid", True))
        pp_row.warnings_json = list(pack.get("warnings") or [])
        if template_id or pack.get("template"):
            pp_row.template_id = template_id or str(pack.get("template") or "")
        pp_row.updated_at = now

    session.add(pp_row)
    session.commit()
    return pack


def generate_and_persist_publish_pack(
    session: Session,
    clip: Clip,
    job: Job,
    *,
    platform: str = "youtube_shorts",
    language: str | None = None,
    template_id: str | None = None,
    tone: str | None = None,
    llm_provider: LLMProvider | None = None,
) -> dict[str, Any]:
    norm_platform = (platform or "youtube_shorts").strip().lower()
    if norm_platform not in VALID_PLATFORMS:
        raise AdminError(
            422,
            "VALIDATION_ERROR",
            f"Unsupported platform {platform!r}; expected one of {VALID_PLATFORMS}",
        )

    if language is not None and language.strip().lower() not in VALID_LANGUAGES:
        raise AdminError(
            422,
            "VALIDATION_ERROR",
            f"Unsupported language {language!r}; expected one of {VALID_LANGUAGES}",
        )

    lang = _resolve_clip_language(clip, job, language)
    transcript = _resolve_clip_transcript(session, clip, job, lang)
    publish_tpl = _load_publish_template_dict(session, template_id, tone=tone)

    raw_hashtags = []
    try:
        raw_hashtags = json.loads(clip.hashtags_json or "[]")
        if not isinstance(raw_hashtags, list):
            raw_hashtags = []
    except Exception:
        raw_hashtags = []

    default_lang_titles = {
        "en": f"Key Takeaway #{clip.rank}",
        "hi": f"मुख्य विचार #{clip.rank}",
        "hinglish": f"Viral Insight #{clip.rank}",
        "bn": f"মূল পয়েন্ট #{clip.rank}",
    }
    selector_meta: dict[str, Any] = {
        "title": (clip.title or "").strip() or default_lang_titles.get(lang, f"Clip #{clip.rank}"),
        "hook_line": (clip.hook_line or clip.title or "").strip() or None,
        "reason": (clip.reason or clip.transcript_snippet or "").strip() or None,
        "hashtags": raw_hashtags or ["#Shorts", "#Viral", "#Clips"],
        "clip_index": clip.rank,
        "source_title": job.title,
        "source_url": job.source_url,
    }
    if tone:
        selector_meta["summary"] = (
            f"[{tone.strip()}] {selector_meta['reason'] or selector_meta['title']}"
        )

    job_dir = get_storage().job_dir(job.id)
    clip_dir = job_dir / "clips" / clip.id
    source_mp4: Path | None = None
    for candidate in (
        Path(clip.render_mp4_path) if clip.render_mp4_path else None,
        job_dir / "source" / "source.mp4",
    ):
        if candidate is not None and candidate.exists():
            source_mp4 = candidate
            break

    start_val = float(clip.start_s if clip.start_s is not None else clip.start)
    end_val = float(clip.end_s if clip.end_s is not None else clip.end)

    pack = build_publish_pack(
        transcript,
        clip_id=clip.id,
        clip_start=start_val,
        clip_end=end_val,
        platform=norm_platform,
        language=lang,
        selector_meta=selector_meta,
        publish_template=publish_tpl,
        llm_provider=llm_provider,
        out_dir=clip_dir,
        source_mp4=source_mp4,
        enforce_hard_limits=True,
    )
    return _persist_publish_pack(session, clip, pack, template_id=template_id)


def get_clip_publish_pack_service(
    session: Session,
    clip_id: str,
) -> dict[str, Any]:
    clip, job = _get_clip_and_job_or_404(session, clip_id)
    job_dir = get_storage().job_dir(job.id)
    clip_dir = job_dir / "clips" / clip.id

    pp_row = session.exec(select(PublishPack).where(PublishPack.clip_id == clip.id)).first()
    candidates = [
        Path(pp_row.path_json) if pp_row and pp_row.path_json else None,
        clip_dir / "publish_pack.json",
    ]
    schema = load_publish_pack_schema()
    for p in candidates:
        if p is not None and p.exists():
            try:
                loaded = json.loads(p.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    jsonschema.validate(instance=loaded, schema=schema)
                    md_path = clip_dir / "publish_pack.md"
                    if not md_path.exists():
                        _atomic_write_text(md_path, format_publish_pack_markdown(loaded))
                    return loaded
            except Exception:
                pass

    return generate_and_persist_publish_pack(session, clip, job)


def update_clip_publish_pack_service(
    session: Session,
    clip_id: str,
    updates: dict[str, Any],
) -> dict[str, Any]:
    clip, _job = _get_clip_and_job_or_404(session, clip_id)
    current = dict(get_clip_publish_pack_service(session, clip_id))

    if "platform" in updates and updates["platform"] is not None:
        plat = str(updates["platform"]).strip().lower()
        if plat not in VALID_PLATFORMS:
            raise AdminError(
                422,
                "VALIDATION_ERROR",
                f"Invalid platform {plat!r}; expected one of {VALID_PLATFORMS}",
            )
        current["platform"] = plat

    if "language" in updates and updates["language"] is not None:
        lang = str(updates["language"]).strip().lower()
        if lang not in VALID_LANGUAGES:
            raise AdminError(
                422,
                "VALIDATION_ERROR",
                f"Invalid language {lang!r}; expected one of {VALID_LANGUAGES}",
            )
        current["language"] = lang

    if "title" in updates and updates["title"] is not None:
        title_str = str(updates["title"]).strip()
        if not title_str:
            raise AdminError(422, "VALIDATION_ERROR", "title cannot be empty")
        if len(title_str) > 100:
            raise AdminError(422, "VALIDATION_ERROR", "title exceeds 100 character limit")
        current["title"] = title_str
        clip.title = title_str

    if "title_options" in updates and updates["title_options"] is not None:
        if not isinstance(updates["title_options"], list):
            raise AdminError(422, "VALIDATION_ERROR", "title_options must be a list of strings")
        opts = [str(o).strip() for o in updates["title_options"] if str(o).strip()]
        if any(len(o) > 100 for o in opts):
            raise AdminError(422, "VALIDATION_ERROR", "title_options entry exceeds 100 character limit")
        current["title_options"] = opts[:5]

    if "description" in updates and updates["description"] is not None:
        desc_str = str(updates["description"])
        if len(desc_str) > 5000:
            raise AdminError(422, "VALIDATION_ERROR", "description exceeds 5000 character limit")
        current["description"] = desc_str

    extra_warnings: list[str] = []
    if "hashtags" in updates and updates["hashtags"] is not None:
        if not isinstance(updates["hashtags"], list):
            raise AdminError(422, "VALIDATION_ERROR", "hashtags must be a list of strings")
        if len(updates["hashtags"]) > 15:
            raise AdminError(422, "VALIDATION_ERROR", "hashtags cannot exceed 15 items")
        norm_tags, h_warns = normalize_hashtags([str(h) for h in updates["hashtags"]], max_count=15)
        current["hashtags"] = norm_tags
        clip.hashtags_json = json.dumps(norm_tags, ensure_ascii=False)
        extra_warnings.extend(h_warns)

    if "tags" in updates and updates["tags"] is not None:
        if not isinstance(updates["tags"], list):
            raise AdminError(422, "VALIDATION_ERROR", "tags must be a list of strings")
        current["tags"] = [str(t).strip() for t in updates["tags"] if str(t).strip()]

    if "pinned_comment" in updates:
        pin = updates["pinned_comment"]
        current["pinned_comment"] = str(pin) if pin is not None else None

    if "hook_line" in updates:
        hk = updates["hook_line"]
        current["hook_line"] = str(hk) if hk is not None else None
        if hk is not None:
            clip.hook_line = str(hk)

    # Optional chapters / schedule_notes appended or stored cleanly in produced_by metadata
    for extra_key in ("chapters", "schedule_notes"):
        if extra_key in updates and updates[extra_key] is not None:
            prod = dict(current.get("produced_by") or {})
            prod[extra_key] = updates[extra_key]
            current["produced_by"] = prod

    cfg = load_platforms_config()
    is_valid, warnings = validate_publish_pack(
        current,
        platform=str(current.get("platform") or "youtube_shorts"),
        platforms_cfg=cfg,
    )
    for ew in extra_warnings:
        if ew not in warnings:
            warnings.append(ew)
    current["valid"] = is_valid
    current["warnings"] = warnings

    schema = load_publish_pack_schema()
    try:
        jsonschema.validate(instance=current, schema=schema)
    except jsonschema.ValidationError as exc:
        raise AdminError(
            422,
            "VALIDATION_ERROR",
            f"PublishPack schema validation failed: {exc.message}",
        ) from exc

    session.add(clip)
    return _persist_publish_pack(session, clip, current)


def regenerate_clip_publish_pack_service(
    session: Session,
    clip_id: str,
    *,
    template_id: str | None = None,
    publish_template_id: str | None = None,
    platform: str | None = None,
    language: str | None = None,
    tone: str | None = None,
    llm_provider: LLMProvider | None = None,
) -> dict[str, Any]:
    clip, job = _get_clip_and_job_or_404(session, clip_id)
    effective_tpl = publish_template_id or template_id
    effective_platform = platform or "youtube_shorts"
    return generate_and_persist_publish_pack(
        session,
        clip,
        job,
        platform=effective_platform,
        language=language,
        template_id=effective_tpl,
        tone=tone,
        llm_provider=llm_provider,
    )


def build_job_zip_bundle(
    session: Session,
    job_id: str,
    *,
    default_clip_plan_fn: Any | None = None,
) -> io.BytesIO:
    job = session.get(Job, job_id)
    if job is None or job.deleted_at is not None:
        raise AdminError(404, "VALIDATION_ERROR", f"Job {job_id} not found")

    clips = list(
        session.exec(select(Clip).where(Clip.job_id == job_id).order_by(col(Clip.rank))).all()
    )
    job_dir = get_storage().job_dir(job_id)

    buf = io.BytesIO()
    written_arcnames: set[str] = set()

    def _write_bytes_once(zf: zipfile.ZipFile, arcname: str, data: bytes) -> None:
        if arcname not in written_arcnames:
            zf.writestr(arcname, data)
            written_arcnames.add(arcname)

    def _write_file_once(zf: zipfile.ZipFile, src_path: Path, arcname: str) -> None:
        if arcname not in written_arcnames and src_path.exists() and src_path.is_file():
            zf.write(src_path, arcname)
            written_arcnames.add(arcname)

    manifest_clips: list[dict[str, Any]] = []

    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for c in clips:
            clip_dir = job_dir / "clips" / c.id
            pack = get_clip_publish_pack_service(session, c.id)
            pack_json_bytes = (json.dumps(pack, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
            pack_md_bytes = format_publish_pack_markdown(pack).encode("utf-8")

            # Spec v2 canonical paths under clips/<clip_id>/
            _write_bytes_once(zf, f"clips/{c.id}/publish_pack.json", pack_json_bytes)
            _write_bytes_once(zf, f"clips/{c.id}/publish_pack.md", pack_md_bytes)

            # Backward-compatible flat prefix
            prefix = f"clip_{c.rank:02d}_{c.id}"
            _write_bytes_once(zf, f"{prefix}_publish_pack.json", pack_json_bytes)

            # clip_plan.json if present (or build fallback if callback supplied)
            plan_candidates = [
                Path(c.plan_path) if c.plan_path else None,
                clip_dir / "clip_plan.json",
            ]
            plan_written = False
            for p_cand in plan_candidates:
                if p_cand is not None and p_cand.exists():
                    _write_file_once(zf, p_cand, f"clips/{c.id}/clip_plan.json")
                    plan_written = True
                    break
            if not plan_written and default_clip_plan_fn is not None:
                try:
                    fallback_plan = default_clip_plan_fn(c, job)
                    if isinstance(fallback_plan, dict):
                        _write_bytes_once(
                            zf,
                            f"clips/{c.id}/clip_plan.json",
                            (json.dumps(fallback_plan, ensure_ascii=False, indent=2) + "\n").encode(
                                "utf-8"
                            ),
                        )
                except Exception:
                    pass

            # Collect media, subtitle sidecars, and thumbnails from clip attributes, Render rows, and clip_dir
            renders = list(session.exec(select(Render).where(Render.clip_id == c.id)).all())

            if c.render_mp4_path and Path(c.render_mp4_path).exists():
                mp4_p = Path(c.render_mp4_path)
                _write_file_once(zf, mp4_p, f"clips/{c.id}/{mp4_p.name}")
                _write_file_once(zf, mp4_p, f"{prefix}.mp4")

            if c.render_srt_path and Path(c.render_srt_path).exists():
                srt_p = Path(c.render_srt_path)
                _write_file_once(zf, srt_p, f"clips/{c.id}/{srt_p.name}")
                _write_file_once(zf, srt_p, f"{prefix}.srt")

            if c.render_ass_path and Path(c.render_ass_path).exists():
                ass_p = Path(c.render_ass_path)
                _write_file_once(zf, ass_p, f"clips/{c.id}/{ass_p.name}")
                _write_file_once(zf, ass_p, f"{prefix}.ass")

            if c.thumb_path and Path(c.thumb_path).exists():
                thumb_p = Path(c.thumb_path)
                _write_file_once(zf, thumb_p, f"clips/{c.id}/{thumb_p.name}")

            for ren in renders:
                for attr, default_name in (
                    (ren.path_mp4, f"render_{ren.id}.mp4"),
                    (ren.path_srt, f"render_{ren.id}.srt"),
                    (ren.path_ass, f"render_{ren.id}.ass"),
                    (ren.path_thumb, f"render_{ren.id}.jpg"),
                ):
                    if attr and Path(attr).exists():
                        rp = Path(attr)
                        _write_file_once(zf, rp, f"clips/{c.id}/{rp.name or default_name}")

            if clip_dir.exists():
                for child in sorted(clip_dir.rglob("*")):
                    if not child.is_file() or child.name.startswith("."):
                        continue
                    if child.suffix.lower() in (
                        ".mp4",
                        ".ass",
                        ".srt",
                        ".vtt",
                        ".jpg",
                        ".jpeg",
                        ".png",
                        ".txt",
                        ".json",
                        ".md",
                    ):
                        rel = child.relative_to(clip_dir).as_posix()
                        _write_file_once(zf, child, f"clips/{c.id}/{rel}")

            manifest_clips.append(
                {
                    "id": c.id,
                    "rank": c.rank,
                    "start_s": c.start_s if c.start_s is not None else c.start,
                    "end_s": c.end_s if c.end_s is not None else c.end,
                    "duration": c.duration,
                    "score": c.score,
                    "title": pack.get("title") or c.title,
                    "status": c.status,
                    "aspect": c.aspect,
                    "layout": c.layout,
                    "template_id": c.template_id,
                    "publish_pack": f"clips/{c.id}/publish_pack.json",
                    "publish_pack_md": f"clips/{c.id}/publish_pack.md",
                }
            )

        job_manifest = {
            "schema_version": 1,
            "job_id": job.id,
            "title": job.title,
            "source_kind": job.source_kind,
            "source_url": job.source_url,
            "platform": job.platform,
            "language": job.language,
            "detected_language": job.detected_language,
            "script": job.script,
            "status": job.status,
            "duration_s": job.duration_s if job.duration_s is not None else job.duration,
            "created_at": job.created_at,
            "exported_at": _now_iso(),
            "clip_count": len(manifest_clips),
            "clips": manifest_clips,
        }
        _write_bytes_once(
            zf,
            "job_manifest.json",
            (json.dumps(job_manifest, ensure_ascii=False, indent=2) + "\n").encode("utf-8"),
        )

    buf.seek(0)
    return buf


__all__ = [
    "build_job_zip_bundle",
    "generate_and_persist_publish_pack",
    "get_clip_publish_pack_service",
    "regenerate_clip_publish_pack_service",
    "update_clip_publish_pack_service",
]
