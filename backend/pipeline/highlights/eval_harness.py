from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from backend.app.config import ROOT_DIR, get_settings
from backend.pipeline.highlights.boundaries import (
    is_timestamp_in_silence,
    snap_clip_boundaries,
)
from backend.pipeline.highlights.ranker import rank_and_select_candidates_v2
from backend.pipeline.highlights.segmenter import (
    flatten_transcript_words,
    generate_candidate_windows,
    load_highlights_v2_config,
)

GOLDEN_DEFAULT_PATH = ROOT_DIR / "assets" / "samples" / "golden_highlights.json"


@dataclass(slots=True)
class EvalMetrics:
    """Evaluation metrics for a single video or aggregated golden suite."""

    precision_at_k: float
    recall_at_k: float
    mean_iou: float
    matched_clips: int
    evaluated_k: int
    total_ground_truth: int
    boundary_quality_score: float = 1.0
    cost_tokens: int = 0
    matched_details: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "precision_at_k": round(self.precision_at_k, 4),
            "recall_at_k": round(self.recall_at_k, 4),
            "mean_iou": round(self.mean_iou, 4),
            "matched_clips": self.matched_clips,
            "evaluated_k": self.evaluated_k,
            "total_ground_truth": self.total_ground_truth,
            "boundary_quality_score": round(self.boundary_quality_score, 4),
            "cost_tokens": self.cost_tokens,
            "matched_details": list(self.matched_details),
        }


def compute_temporal_iou(
    pred_interval: tuple[float, float] | list[float],
    gt_interval: tuple[float, float] | list[float],
) -> float:
    """Compute Temporal Intersection-over-Union (IoU) in [0, 1] between two time intervals."""
    p0, p1 = float(pred_interval[0]), float(pred_interval[1])
    g0, g1 = float(gt_interval[0]), float(gt_interval[1])
    if p1 <= p0 or g1 <= g0:
        return 0.0
    inter = max(0.0, min(p1, g1) - max(p0, g0))
    if inter <= 0.0:
        return 0.0
    union = (p1 - p0) + (g1 - g0) - inter
    if union <= 0.0:
        return 0.0
    return round(inter / union, 4)


def _extract_interval(item: dict[str, Any] | tuple[float, float] | list[float]) -> tuple[float, float]:
    if isinstance(item, dict):
        return float(item.get("start", 0.0)), float(item.get("end", 0.0))
    return float(item[0]), float(item[1])


def evaluate_highlights_precision_at_k(
    predicted_clips: Sequence[dict[str, Any] | tuple[float, float] | list[float]],
    ground_truth_intervals: Sequence[dict[str, Any] | tuple[float, float] | list[float]],
    *,
    k: int = 5,
    iou_threshold: float = 0.50,
    boundary_quality_score: float = 1.0,
    cost_tokens: int = 0,
) -> EvalMetrics:
    """Compute Precision@k, Recall@k, and Mean IoU at `iou_threshold` (default 0.50)."""
    top_k_preds = list(predicted_clips[: max(1, k)])
    gt_list = [_extract_interval(g) for g in ground_truth_intervals]

    if not top_k_preds:
        return EvalMetrics(
            precision_at_k=0.0,
            recall_at_k=0.0,
            mean_iou=0.0,
            matched_clips=0,
            evaluated_k=0,
            total_ground_truth=len(gt_list),
            boundary_quality_score=boundary_quality_score,
            cost_tokens=cost_tokens,
        )

    matched_clips = 0
    matched_gt_indices: set[int] = set()
    best_ious: list[float] = []
    details: list[dict[str, Any]] = []

    for rank_idx, pred in enumerate(top_k_preds, start=1):
        p_interval = _extract_interval(pred)
        best_iou = 0.0
        best_gt_idx = -1
        for g_idx, g_interval in enumerate(gt_list):
            iou = compute_temporal_iou(p_interval, g_interval)
            if iou > best_iou:
                best_iou = iou
                best_gt_idx = g_idx

        best_ious.append(best_iou)
        is_hit = best_iou >= iou_threshold
        if is_hit:
            matched_clips += 1
            if best_gt_idx >= 0:
                matched_gt_indices.add(best_gt_idx)

        details.append(
            {
                "rank": rank_idx,
                "pred_start": round(p_interval[0], 3),
                "pred_end": round(p_interval[1], 3),
                "best_iou": round(best_iou, 4),
                "matched_gt_index": best_gt_idx if is_hit else None,
                "hit": is_hit,
            }
        )

    denom_k = min(k, len(top_k_preds)) if top_k_preds else k
    precision = matched_clips / max(1, denom_k)
    recall = (
        len(matched_gt_indices) / len(gt_list)
        if gt_list
        else 0.0
    )
    mean_iou = sum(best_ious) / len(best_ious) if best_ious else 0.0

    return EvalMetrics(
        precision_at_k=round(precision, 4),
        recall_at_k=round(recall, 4),
        mean_iou=round(mean_iou, 4),
        matched_clips=matched_clips,
        evaluated_k=denom_k,
        total_ground_truth=len(gt_list),
        boundary_quality_score=round(boundary_quality_score, 4),
        cost_tokens=cost_tokens,
        matched_details=details,
    )


def load_golden_highlights(path: Path | None = None) -> dict[str, Any]:
    """Load `assets/samples/golden_highlights.json`."""
    target = path if path is not None else GOLDEN_DEFAULT_PATH
    if not target.exists():
        settings = get_settings()
        target = settings.assets_dir / "samples" / "golden_highlights.json"
    data: dict[str, Any] = json.loads(target.read_text(encoding="utf-8"))
    return data


def _compute_boundary_quality(
    clips: Sequence[dict[str, Any]],
    transcript: dict[str, Any],
    silences: list[list[float] | tuple[float, float]] | None,
) -> float:
    if not clips:
        return 0.0
    words = flatten_transcript_words(transcript)
    sil_tuples: list[tuple[float, float]] = []
    if silences:
        for s in silences:
            if len(s) >= 2:
                sil_tuples.append((float(s[0]), float(s[1])))
    elif words:
        for i in range(len(words) - 1):
            if float(words[i + 1]["s"]) > float(words[i]["e"]):
                sil_tuples.append((float(words[i]["e"]), float(words[i + 1]["s"])))

    if not sil_tuples:
        return 1.0

    good = 0
    total = 0
    for c in clips:
        snapped = snap_clip_boundaries(
            float(c["start"]),
            float(c["end"]),
            transcript,
            silences=silences,
        )
        total += 2
        if snapped.start_in_silence or is_timestamp_in_silence(snapped.start, sil_tuples, 0.05):
            good += 1
        if snapped.end_in_silence or is_timestamp_in_silence(snapped.end, sil_tuples, 0.05):
            good += 1
    return round(good / max(1, total), 4)


def run_golden_evaluation_suite(
    golden_path: Path | None = None,
    *,
    k: int = 5,
    iou_threshold: float = 0.50,
    min_precision_target: float = 0.60,
) -> dict[str, Any]:
    """Run Highlight Engine v2 across all 4 multi-language golden videos and verify precision@k >= 0.60."""
    dataset = load_golden_highlights(golden_path)
    videos = dataset.get("videos", [])
    cfg = load_highlights_v2_config()

    per_video: dict[str, dict[str, Any]] = {}
    precisions: list[float] = []
    recalls: list[float] = []
    ious: list[float] = []
    boundary_scores: list[float] = []

    for vid in videos:
        vid_id = str(vid["id"])
        lang = str(vid["language"])
        genre = str(vid.get("genre", "podcast"))
        transcript = vid["transcript"]
        signals_artefact = vid.get("signals")
        topics = vid.get("topics")
        gt_intervals = vid.get("ground_truth", [])

        candidates = generate_candidate_windows(
            transcript,
            genre=genre,
            config=cfg,
            signals_artefact=signals_artefact,
            topics=topics,
        )

        selection = rank_and_select_candidates_v2(
            candidates,
            score_mode="signals_only",
            genre=genre,
            language=lang,
            config=cfg,
            max_clips_override=max(k, 8),
        )

        silences = (
            signals_artefact.get("silences")
            if isinstance(signals_artefact, dict)
            else None
        )
        b_quality = _compute_boundary_quality(
            selection.clips[:k],
            transcript,
            silences=silences,
        )

        metrics = evaluate_highlights_precision_at_k(
            selection.clips,
            gt_intervals,
            k=k,
            iou_threshold=iou_threshold,
            boundary_quality_score=b_quality,
            cost_tokens=0,
        )

        per_video[vid_id] = {
            "id": vid_id,
            "language": lang,
            "genre": genre,
            "metrics": metrics.to_dict(),
            "passed": metrics.precision_at_k >= min_precision_target,
        }
        precisions.append(metrics.precision_at_k)
        recalls.append(metrics.recall_at_k)
        ious.append(metrics.mean_iou)
        boundary_scores.append(b_quality)

    mean_p = sum(precisions) / max(1, len(precisions))
    mean_r = sum(recalls) / max(1, len(recalls))
    mean_i = sum(ious) / max(1, len(ious))
    mean_b = sum(boundary_scores) / max(1, len(boundary_scores))
    all_passed = bool(per_video) and all(v["passed"] for v in per_video.values())

    return {
        "schema_version": 1,
        "k": k,
        "iou_threshold": iou_threshold,
        "min_precision_target": min_precision_target,
        "video_count": len(per_video),
        "mean_precision_at_k": round(mean_p, 4),
        "mean_recall_at_k": round(mean_r, 4),
        "mean_iou": round(mean_i, 4),
        "mean_boundary_quality": round(mean_b, 4),
        "all_passed": all_passed,
        "videos": per_video,
    }
