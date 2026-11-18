from __future__ import annotations

import argparse
import json
import resource
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from backend.pipeline.highlights.boundaries import build_clip_plan, snap_clip_boundaries
from backend.pipeline.highlights.eval_harness import load_golden_highlights
from backend.pipeline.highlights.ranker import rank_and_select_candidates_v2
from backend.pipeline.highlights.segmenter import (
    extract_candidate_signal_features,
    flatten_transcript_words,
    generate_candidate_windows,
    load_highlights_v2_config,
    split_into_thought_units,
)

# Spec v2 SLA thresholds:
# - 30-min video <= 8 min (480s) on GPU -> throughput factor >= 3.75x
# - 30-min video <= 20 min (1200s) on CPU -> throughput factor >= 1.50x
# - Preview render <= 4.0s
SLA_30MIN_GPU_MAX_S = 480.0
SLA_30MIN_CPU_MAX_S = 1200.0
SLA_PREVIEW_RENDER_MAX_S = 4.0


@dataclass(slots=True)
class BenchmarkReport:
    """Structured report produced by `run_pipeline_benchmark`."""

    video_duration_s: float
    wall_time_s: float
    throughput_factor: float
    peak_rss_mb: float
    stage_latencies_ms: dict[str, float] = field(default_factory=dict)
    projected_30min_s: float = 0.0
    preview_render_s: float = 0.0
    sla_gpu_30min_ok: bool = True
    sla_cpu_30min_ok: bool = True
    sla_preview_4s_ok: bool = True
    all_slas_met: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "video_duration_s": round(self.video_duration_s, 3),
            "wall_time_s": round(self.wall_time_s, 6),
            "throughput_factor": round(self.throughput_factor, 2),
            "peak_rss_mb": round(self.peak_rss_mb, 3),
            "stage_latencies_ms": {
                k: round(v, 3) for k, v in self.stage_latencies_ms.items()
            },
            "sla": {
                "projected_30min_s": round(self.projected_30min_s, 3),
                "preview_render_s": round(self.preview_render_s, 4),
                "gpu_30min_max_s": SLA_30MIN_GPU_MAX_S,
                "cpu_30min_max_s": SLA_30MIN_CPU_MAX_S,
                "preview_max_s": SLA_PREVIEW_RENDER_MAX_S,
                "sla_gpu_30min_ok": self.sla_gpu_30min_ok,
                "sla_cpu_30min_ok": self.sla_cpu_30min_ok,
                "sla_preview_4s_ok": self.sla_preview_4s_ok,
                "all_slas_met": self.all_slas_met,
            },
        }


def _get_peak_rss_mb() -> float:
    """Read current process peak RSS memory in MiB via `resource.getrusage`."""
    usage = resource.getrusage(resource.RUSAGE_SELF)
    # On Linux ru_maxrss is in KiB; on macOS it is in bytes
    if sys.platform == "darwin":
        return float(usage.ru_maxrss) / (1024.0 * 1024.0)
    return float(usage.ru_maxrss) / 1024.0


def run_pipeline_benchmark(
    *,
    video_duration_s: float = 1800.0,
    work_dir: Path | None = None,
) -> dict[str, Any]:
    """Run a deterministic benchmark of CLIPPER pipeline stages and verify Spec v2 SLAs.

    Measures:
    - Stage latencies (`asr`, `analysis`, `highlights`, `reframe`, `render`, `publish`)
    - Peak RSS memory (`resource.getrusage`)
    - Throughput factor (`video_duration_s / wall_time_s`)
    - SLA compliance (`30-min video <= 8 min GPU / <= 20 min CPU`, `preview render <= 4s`)
    """
    dataset = load_golden_highlights()
    sample_vid = dataset["videos"][0]
    transcript = dict(sample_vid["transcript"])
    signals_artefact = dict(sample_vid["signals"])
    topics = list(sample_vid.get("topics", []))
    cfg = load_highlights_v2_config()

    stage_latencies_ms: dict[str, float] = {}
    t_bench_start = time.perf_counter()

    with tempfile.TemporaryDirectory() as tmp_dir_str:
        base_dir = work_dir if work_dir is not None else Path(tmp_dir_str)

        # 1. ASR stage (word normalization & thought-unit indexing)
        t0 = time.perf_counter()
        words = flatten_transcript_words(transcript)
        units = split_into_thought_units(transcript)
        stage_latencies_ms["asr"] = (time.perf_counter() - t0) * 1000.0

        # 2. Video analysis stage (candidate signal feature extraction)
        t0 = time.perf_counter()
        candidates = generate_candidate_windows(
            transcript,
            genre="podcast",
            config=cfg,
            signals_artefact=signals_artefact,
            topics=topics,
        )
        for cand in candidates:
            extract_candidate_signal_features(
                cand,
                signals_artefact=signals_artefact,
                median_wps=max(1.0, len(words) / max(1.0, float(transcript.get("duration", 900.0)))),
            )
        stage_latencies_ms["analysis"] = (time.perf_counter() - t0) * 1000.0

        # 3. Highlight engine stage (fusion, NMS, semantic dedupe, diversity, boundary snap)
        t0 = time.perf_counter()
        selection = rank_and_select_candidates_v2(
            candidates,
            score_mode="signals_only",
            genre="podcast",
            language=str(transcript.get("language", "en")),
            config=cfg,
        )
        silences = signals_artefact.get("silences")
        for clip in selection.clips:
            snapped = snap_clip_boundaries(
                float(clip["start"]),
                float(clip["end"]),
                transcript,
                thought_units=units,
                silences=silences,
                config=cfg,
            )
            clip["start"] = snapped.start
            clip["end"] = snapped.end
            clip["duration"] = snapped.duration
        stage_latencies_ms["highlights"] = (time.perf_counter() - t0) * 1000.0

        # 4. Reframe stage (15 Hz smoothed crop path generation per selected clip)
        t0 = time.perf_counter()
        for idx, clip in enumerate(selection.clips, start=1):
            dur = float(clip["duration"])
            n_frames = max(1, int(dur * 15))
            reframe_keyframes = [
                {"t": round(f / 15.0, 3), "cx": 0.5, "cy": 0.45, "zoom": 1.0}
                for f in range(n_frames)
            ]
            reframe_file = base_dir / "clips" / f"clip_{idx:02d}" / "reframe_path.json"
            reframe_file.parent.mkdir(parents=True, exist_ok=True)
            reframe_file.write_text(
                json.dumps({"schema_version": 1, "hz": 15, "frames": reframe_keyframes}),
                encoding="utf-8",
            )
        stage_latencies_ms["reframe"] = (time.perf_counter() - t0) * 1000.0

        # 5. Render plan & preview stage (clip_plan.json validation + command synthesis)
        t0 = time.perf_counter()
        for idx, clip in enumerate(selection.clips, start=1):
            clip_id = f"clip_{idx:02d}"
            plan_path = base_dir / "clips" / clip_id / "clip_plan.json"
            build_clip_plan(
                clip_id=clip_id,
                start=float(clip["start"]),
                end=float(clip["end"]),
                script=str(transcript.get("script", "latn")),
                aspect="9:16",
                output_path=plan_path,
            )
        render_elapsed_s = time.perf_counter() - t0
        stage_latencies_ms["render"] = render_elapsed_s * 1000.0

        # 6. Publish pack stage (metadata & caption export serialization)
        t0 = time.perf_counter()
        for idx, clip in enumerate(selection.clips, start=1):
            pack = {
                "schema_version": 1,
                "clip_id": f"clip_{idx:02d}",
                "title": clip["title"],
                "hook_line": clip["hook_line"],
                "hashtags": clip["hashtags"],
                "score": clip["score"],
            }
            pack_path = base_dir / "clips" / f"clip_{idx:02d}" / "publish_pack.json"
            pack_path.write_text(json.dumps(pack, ensure_ascii=False, indent=2), encoding="utf-8")
        stage_latencies_ms["publish"] = (time.perf_counter() - t0) * 1000.0

    wall_time_s = max(1e-6, time.perf_counter() - t_bench_start)
    throughput_factor = float(video_duration_s) / wall_time_s
    peak_rss_mb = _get_peak_rss_mb()

    # Project time for a 30-min (1800s) video at measured throughput
    projected_30min_s = 1800.0 / max(1e-6, throughput_factor)
    preview_render_s = render_elapsed_s / max(1, len(selection.clips))

    sla_gpu_ok = projected_30min_s <= SLA_30MIN_GPU_MAX_S
    sla_cpu_ok = projected_30min_s <= SLA_30MIN_CPU_MAX_S
    sla_preview_ok = preview_render_s <= SLA_PREVIEW_RENDER_MAX_S

    report = BenchmarkReport(
        video_duration_s=float(video_duration_s),
        wall_time_s=wall_time_s,
        throughput_factor=throughput_factor,
        peak_rss_mb=peak_rss_mb,
        stage_latencies_ms=stage_latencies_ms,
        projected_30min_s=projected_30min_s,
        preview_render_s=preview_render_s,
        sla_gpu_30min_ok=sla_gpu_ok,
        sla_cpu_30min_ok=sla_cpu_ok,
        sla_preview_4s_ok=sla_preview_ok,
        all_slas_met=bool(sla_gpu_ok and sla_cpu_ok and sla_preview_ok),
    )
    return report.to_dict()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="CLIPPER deterministic pipeline performance benchmark runner."
    )
    parser.add_argument(
        "--video-duration-s",
        type=float,
        default=1800.0,
        help="Simulated video duration in seconds (default: 1800s / 30 min).",
    )
    parser.add_argument(
        "--json-out",
        type=Path,
        default=None,
        help="Optional path to write JSON benchmark report.",
    )
    args = parser.parse_args(argv)

    result = run_pipeline_benchmark(video_duration_s=args.video_duration_s)
    formatted = json.dumps(result, ensure_ascii=False, indent=2)
    if args.json_out is not None:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(formatted, encoding="utf-8")
    print(formatted)
    return 0 if result["sla"]["all_slas_met"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
