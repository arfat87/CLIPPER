from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

import jsonschema  # type: ignore[import-untyped]
import numpy as np

from backend.pipeline.analysis.profile import load_highlights_config
from backend.pipeline.providers.base import LLMProvider
from backend.pipeline.reframe.tracker import SampledFrame, sample_clip_frames

DEFAULT_FRAME_INTERVAL_S = 2.5
DEFAULT_MAX_FRAMES_PER_JOB = 120
DEFAULT_ENABLED_GENRES: tuple[str, ...] = ("gaming", "screenshare_tutorial", "vlog")
DEFAULT_ANALYSIS_WIDTH = 320

VisionAnalyzeFn = Callable[[np.ndarray, float, str], Mapping[str, Any]]

__all__ = [
    "DEFAULT_ENABLED_GENRES",
    "DEFAULT_FRAME_INTERVAL_S",
    "DEFAULT_MAX_FRAMES_PER_JOB",
    "VisionAnalyzeFn",
    "analyze_visual_windows",
    "compute_frame_hash",
    "is_visual_analysis_enabled",
]


def compute_frame_hash(frame: np.ndarray | bytes) -> str:
    """Compute a deterministic SHA-256 hex digest for a frame's bytes (and shape/dtype if ndarray)."""
    hasher = hashlib.sha256()
    if isinstance(frame, np.ndarray):
        arr = np.ascontiguousarray(frame)
        header = f"{arr.shape}:{arr.dtype}:".encode("utf-8")
        hasher.update(header)
        hasher.update(arr.tobytes())
    elif isinstance(frame, (bytes, bytearray, memoryview)):
        hasher.update(bytes(frame))
    else:
        raise TypeError(f"Unsupported frame type for hashing: {type(frame).__name__}")
    return hasher.hexdigest()


def is_visual_analysis_enabled(
    *,
    enabled: bool | None = None,
    genre: str | None = None,
    enabled_genres: Sequence[str] = DEFAULT_ENABLED_GENRES,
) -> bool:
    """Determine if visual analysis should run (off by default unless enabled=True or genre matches)."""
    if enabled is not None:
        return bool(enabled)
    if genre is None:
        return False
    norm_genre = str(genre).strip().lower()
    allowed = {str(g).strip().lower() for g in enabled_genres}
    return norm_genre in allowed


def _config_hash(
    *,
    enabled: bool,
    genre: str | None,
    frame_interval_s: float,
    max_frames_per_job: int,
) -> str:
    payload = json.dumps(
        {
            "enabled": bool(enabled),
            "genre": genre,
            "frame_interval_s": round(float(frame_interval_s), 4),
            "max_frames_per_job": int(max_frames_per_job),
        },
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:12]


def _normalize_windows(
    candidate_windows: Sequence[Mapping[str, Any] | tuple[float, float]] | None,
) -> list[tuple[float, float]]:
    if not candidate_windows:
        return []
    windows: list[tuple[float, float]] = []
    for win in candidate_windows:
        if isinstance(win, Mapping):
            raw_s = win.get("start")
            raw_e = win.get("end")
            if raw_s is None or raw_e is None:
                continue
            s = max(0.0, float(raw_s))
            e = max(s, float(raw_e))
        elif isinstance(win, Sequence) and len(win) >= 2:
            s = max(0.0, float(win[0]))
            e = max(s, float(win[1]))
        else:
            continue
        if e > s:
            windows.append((round(s, 3), round(e, 3)))
    return windows


def _heuristic_frame_features(image: np.ndarray) -> dict[str, Any]:
    """Compute lightweight visual statistics from an RGB frame for heuristic analysis & LLM prompt."""
    arr = np.asarray(image, dtype=np.float32)
    if arr.ndim == 2:
        gray = arr
        sat = 0.0
    elif arr.ndim == 3 and arr.shape[2] >= 3:
        r, g, b = arr[:, :, 0], arr[:, :, 1], arr[:, :, 2]
        gray = 0.299 * r + 0.587 * g + 0.114 * b
        cmax = np.maximum(np.maximum(r, g), b)
        cmin = np.minimum(np.minimum(r, g), b)
        sat = float(np.mean((cmax - cmin) / np.maximum(cmax, 1.0)))
    else:
        gray = arr.reshape(-1).astype(np.float32)
        sat = 0.0

    mean_luma = float(np.mean(gray)) / 255.0 if gray.size > 0 else 0.0
    std_luma = float(np.std(gray)) / 128.0 if gray.size > 0 else 0.0

    if gray.ndim == 2 and gray.shape[0] >= 2 and gray.shape[1] >= 2:
        gx = np.abs(np.diff(gray, axis=1))
        gy = np.abs(np.diff(gray, axis=0))
        edge_density = float((np.mean(gx > 24.0) + np.mean(gy > 24.0)) * 0.5)
        # Flat background ratio (characteristic of IDE / slides / browser screenshares)
        flat_ratio = float(np.mean(gx < 2.0))
    else:
        edge_density = 0.0
        flat_ratio = 1.0

    is_screenshare = bool(edge_density >= 0.06 and flat_ratio >= 0.55 and sat <= 0.35)
    interest = min(
        1.0,
        max(0.05, 0.45 * min(1.0, std_luma) + 0.35 * min(1.0, edge_density * 4.0) + 0.20 * min(1.0, sat * 2.0)),
    )

    if is_screenshare:
        desc = "High-contrast screen or slide view with structured text layout"
    elif interest >= 0.65:
        desc = "High-visual-activity scene with strong contrast and detail"
    elif interest <= 0.20:
        desc = "Low-detail or static background frame"
    else:
        desc = "Standard video frame with moderate visual detail"

    return {
        "mean_luma": round(mean_luma, 4),
        "contrast": round(min(1.0, std_luma), 4),
        "edge_density": round(edge_density, 4),
        "flat_ratio": round(flat_ratio, 4),
        "saturation": round(sat, 4),
        "visual_interest": round(interest, 4),
        "is_screenshare": is_screenshare,
        "description": desc,
        "on_screen_text": "",
    }


def _visual_llm_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": [
            "description",
            "on_screen_text",
            "visual_interest",
            "is_screenshare",
        ],
        "properties": {
            "description": {"type": "string", "minLength": 1, "maxLength": 240},
            "on_screen_text": {"type": "string", "maxLength": 240},
            "visual_interest": {"type": "number", "minimum": 0.0, "maximum": 1.0},
            "is_screenshare": {"type": "boolean"},
        },
    }


def _load_disk_cache(cache_path: Path | None) -> dict[str, dict[str, Any]]:
    if cache_path is None or not cache_path.exists() or not cache_path.is_file():
        return {}
    try:
        raw = json.loads(cache_path.read_text(encoding="utf-8"))
        if isinstance(raw, Mapping):
            return {
                str(k): dict(v)
                for k, v in raw.items()
                if isinstance(k, str) and isinstance(v, Mapping)
            }
    except Exception:
        return {}
    return {}


def _save_disk_cache(cache_path: Path | None, cache: Mapping[str, Mapping[str, Any]]) -> None:
    if cache_path is None:
        return
    try:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = cache_path.with_suffix(".tmp")
        tmp_path.write_text(json.dumps(dict(cache), indent=2, sort_keys=True), encoding="utf-8")
        tmp_path.replace(cache_path)
    except Exception:
        pass


def _sample_window_frames(
    *,
    video_path: Path | None,
    windows: Sequence[tuple[float, float]],
    frame_interval_s: float,
    max_frames: int,
    frames: Sequence[SampledFrame] | None,
    analysis_width: int,
    warnings: list[str],
) -> tuple[list[SampledFrame], bool]:
    """Sample frames strictly inside `windows` at `1 / frame_interval_s` up to `max_frames`."""
    budget_reached = False
    selected: list[SampledFrame] = []
    fps = 1.0 / max(frame_interval_s, 0.1)

    if frames is not None:
        sorted_given = sorted(frames, key=lambda f: float(f.timestamp))
        if not windows:
            last_t: float | None = None
            for sf in sorted_given:
                t = float(sf.timestamp)
                if last_t is None or (t - last_t) >= frame_interval_s - 1e-6:
                    if len(selected) >= max_frames:
                        budget_reached = True
                        break
                    selected.append(sf)
                    last_t = t
            return selected, budget_reached

        for w_start, w_end in windows:
            win_frames = [
                sf for sf in sorted_given if w_start - 1e-6 <= float(sf.timestamp) <= w_end + 1e-6
            ]
            last_t = None
            for sf in win_frames:
                t = float(sf.timestamp)
                if last_t is None or (t - last_t) >= frame_interval_s - 1e-6:
                    if len(selected) >= max_frames:
                        budget_reached = True
                        break
                    selected.append(sf)
                    last_t = t
            if budget_reached:
                break
        return selected, budget_reached

    if video_path is None:
        return [], False

    for w_start, w_end in windows:
        if len(selected) >= max_frames:
            budget_reached = True
            break
        remaining = max_frames - len(selected)
        # Generate target timestamps inside [w_start, w_end] at frame_interval_s steps
        n_steps = max(1, int(math.floor((w_end - w_start) / frame_interval_s)) + 1)
        try:
            sampled = sample_clip_frames(
                Path(video_path),
                start=w_start,
                end=w_end,
                base_fps=fps,
                dense_fps=fps,
                analysis_width=analysis_width,
            )
        except Exception as exc:
            warnings.append(f"visual_window_sampling_failed [{w_start}-{w_end}]: {exc}")
            continue

        if len(sampled) > n_steps:
            sampled = sampled[:n_steps]
        if len(sampled) > remaining:
            sampled = sampled[:remaining]
            budget_reached = True
        selected.extend(sampled)
        if budget_reached:
            break

    return selected, budget_reached


def analyze_visual_windows(
    video_path: Path | None = None,
    candidate_windows: Sequence[Mapping[str, Any] | tuple[float, float]] | None = None,
    *,
    genre: str | None = None,
    enabled: bool | None = None,
    frame_interval_s: float | None = None,
    max_frames_per_job: int | None = None,
    llm_provider: LLMProvider | None = None,
    vision_analyzer: VisionAnalyzeFn | None = None,
    frame_cache: dict[str, dict[str, Any]] | None = None,
    cache_path: Path | None = None,
    frames: Sequence[SampledFrame] | None = None,
    highlights_cfg: Mapping[str, Any] | None = None,
    analysis_width: int = DEFAULT_ANALYSIS_WIDTH,
) -> dict[str, Any]:
    """Run optional, frame-hash-cached, budget-capped visual analysis over candidate windows."""
    warnings: list[str] = []
    cfg = dict(highlights_cfg) if highlights_cfg is not None else load_highlights_config()
    vis_cfg = cfg.get("visual_analysis")
    vis_map = vis_cfg if isinstance(vis_cfg, Mapping) else {}

    cfg_genres = vis_map.get("enabled_genres")
    enabled_genres: Sequence[str] = (
        [str(g) for g in cfg_genres]
        if isinstance(cfg_genres, Sequence) and not isinstance(cfg_genres, (str, bytes))
        else DEFAULT_ENABLED_GENRES
    )

    resolved_interval = float(
        frame_interval_s
        if frame_interval_s is not None
        else vis_map.get("frame_interval_s", DEFAULT_FRAME_INTERVAL_S)
    )
    if resolved_interval <= 0.0:
        raise ValueError("frame_interval_s must be > 0")

    resolved_budget = int(
        max_frames_per_job
        if max_frames_per_job is not None
        else vis_map.get("max_frames_per_job", DEFAULT_MAX_FRAMES_PER_JOB)
    )
    if resolved_budget < 0:
        raise ValueError("max_frames_per_job must be >= 0")

    is_enabled = is_visual_analysis_enabled(
        enabled=enabled,
        genre=genre,
        enabled_genres=enabled_genres,
    )
    cfg_hash = _config_hash(
        enabled=is_enabled,
        genre=genre,
        frame_interval_s=resolved_interval,
        max_frames_per_job=resolved_budget,
    )

    if not is_enabled:
        return {
            "schema_version": 1,
            "produced_by": {
                "stage": "analyze_visual_windows",
                "version": "1.0.0",
                "config_hash": cfg_hash,
            },
            "enabled": False,
            "genre": genre,
            "frame_interval_s": round(resolved_interval, 3),
            "budget_max_frames": resolved_budget,
            "frames_analyzed": 0,
            "cache_hits": 0,
            "llm_calls": 0,
            "frames": [],
            "warnings": warnings,
        }

    windows = _normalize_windows(candidate_windows)
    if not windows and frames is None:
        warnings.append("no_candidate_windows_provided")
        return {
            "schema_version": 1,
            "produced_by": {
                "stage": "analyze_visual_windows",
                "version": "1.0.0",
                "config_hash": cfg_hash,
            },
            "enabled": True,
            "genre": genre,
            "frame_interval_s": round(resolved_interval, 3),
            "budget_max_frames": resolved_budget,
            "frames_analyzed": 0,
            "cache_hits": 0,
            "llm_calls": 0,
            "frames": [],
            "warnings": warnings,
        }

    sampled_frames, budget_reached = _sample_window_frames(
        video_path=video_path,
        windows=windows,
        frame_interval_s=resolved_interval,
        max_frames=resolved_budget,
        frames=frames,
        analysis_width=analysis_width,
        warnings=warnings,
    )
    if budget_reached:
        warnings.append(f"visual_budget_capped_at_{resolved_budget}_frames")

    active_cache: dict[str, dict[str, Any]] = (
        frame_cache if frame_cache is not None else _load_disk_cache(cache_path)
    )

    schema = _visual_llm_schema()
    frame_entries: list[dict[str, Any]] = []
    cache_hits = 0
    llm_calls = 0

    for sf in sampled_frames[:resolved_budget]:
        t = round(float(sf.timestamp), 3)
        f_hash = compute_frame_hash(sf.image)

        cached = active_cache.get(f_hash)
        if isinstance(cached, Mapping):
            cache_hits += 1
            frame_entries.append(
                {
                    "t": t,
                    "frame_hash": f_hash,
                    "description": str(cached.get("description") or "Cached frame"),
                    "on_screen_text": str(cached.get("on_screen_text") or ""),
                    "visual_interest": round(float(cached.get("visual_interest", 0.5)), 4),
                    "is_screenshare": bool(cached.get("is_screenshare", False)),
                    "cached": True,
                }
            )
            continue

        heur = _heuristic_frame_features(sf.image)
        entry_payload: dict[str, Any] = {
            "description": heur["description"],
            "on_screen_text": heur["on_screen_text"],
            "visual_interest": heur["visual_interest"],
            "is_screenshare": heur["is_screenshare"],
        }

        if vision_analyzer is not None:
            try:
                llm_calls += 1
                raw_vis = vision_analyzer(sf.image, t, f_hash)
                entry_payload = {
                    "description": str(raw_vis.get("description") or heur["description"])[:240],
                    "on_screen_text": str(raw_vis.get("on_screen_text") or "")[:240],
                    "visual_interest": round(
                        min(1.0, max(0.0, float(raw_vis.get("visual_interest", heur["visual_interest"])))),
                        4,
                    ),
                    "is_screenshare": bool(raw_vis.get("is_screenshare", heur["is_screenshare"])),
                }
            except Exception as exc:
                warnings.append(f"vision_analyzer_failed at t={t}: {exc}")
        elif llm_provider is not None:
            try:
                llm_calls += 1
                data_summary = (
                    f"frame_timestamp={t}s, frame_hash={f_hash[:16]}, "
                    f"genre={genre or 'unknown'}, "
                    f"mean_luma={heur['mean_luma']}, contrast={heur['contrast']}, "
                    f"edge_density={heur['edge_density']}, flat_ratio={heur['flat_ratio']}, "
                    f"saturation={heur['saturation']}"
                )
                prompt = (
                    "Estimate visual interest (0-1), screenshare status, on-screen text summary, "
                    "and a short visual description for this sampled video frame."
                )
                res = llm_provider.generate_json(
                    prompt=prompt,
                    data_text=data_summary,
                    schema=schema,
                    temperature=0.1,
                )
                jsonschema.validate(instance=res.data, schema=schema)
                entry_payload = {
                    "description": str(res.data["description"])[:240],
                    "on_screen_text": str(res.data["on_screen_text"])[:240],
                    "visual_interest": round(
                        min(1.0, max(0.0, float(res.data["visual_interest"]))),
                        4,
                    ),
                    "is_screenshare": bool(res.data["is_screenshare"]),
                }
            except Exception as exc:
                warnings.append(f"visual_llm_fallback at t={t}: {exc}")

        active_cache[f_hash] = dict(entry_payload)
        frame_entries.append(
            {
                "t": t,
                "frame_hash": f_hash,
                "description": entry_payload["description"],
                "on_screen_text": entry_payload["on_screen_text"],
                "visual_interest": entry_payload["visual_interest"],
                "is_screenshare": entry_payload["is_screenshare"],
                "cached": False,
            }
        )

    if cache_path is not None:
        _save_disk_cache(cache_path, active_cache)

    return {
        "schema_version": 1,
        "produced_by": {
            "stage": "analyze_visual_windows",
            "version": "1.0.0",
            "config_hash": cfg_hash,
        },
        "enabled": True,
        "genre": genre,
        "frame_interval_s": round(resolved_interval, 3),
        "budget_max_frames": resolved_budget,
        "frames_analyzed": len(frame_entries),
        "cache_hits": cache_hits,
        "llm_calls": llm_calls,
        "frames": frame_entries,
        "warnings": warnings,
    }
