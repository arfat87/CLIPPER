from __future__ import annotations

import json
from pathlib import Path

import pytest

from backend.pipeline.bench import main as bench_cli_main
from backend.pipeline.bench import run_pipeline_benchmark
from backend.pipeline.highlights.eval_harness import (
    compute_temporal_iou,
    evaluate_highlights_precision_at_k,
    load_golden_highlights,
    run_golden_evaluation_suite,
)


def test_compute_temporal_iou():
    """Verify temporal IoU calculation across exact, partial, disjoint, and invalid intervals."""
    assert compute_temporal_iou((10.0, 40.0), (10.0, 40.0)) == pytest.approx(1.0)
    assert compute_temporal_iou((0.0, 20.0), (20.0, 40.0)) == 0.0
    assert compute_temporal_iou((0.0, 30.0), (15.0, 45.0)) == pytest.approx(15.0 / 45.0, abs=1e-3)
    assert compute_temporal_iou((30.0, 10.0), (10.0, 30.0)) == 0.0


def test_evaluate_highlights_precision_at_k():
    """Verify Precision@k, Recall@k, and Mean IoU calculation at IoU >= 0.50."""
    gt = [
        (10.0, 40.0),
        (100.0, 130.0),
        (200.0, 230.0),
        (300.0, 330.0),
        (400.0, 430.0),
    ]
    preds = [
        {"start": 10.0, "end": 40.0},     # Hit (IoU = 1.0)
        {"start": 102.0, "end": 128.0},   # Hit (IoU = 26/30 = 0.8667)
        {"start": 200.0, "end": 230.0},   # Hit (IoU = 1.0)
        {"start": 500.0, "end": 530.0},   # Miss (IoU = 0.0)
        {"start": 305.0, "end": 330.0},   # Hit (IoU = 25/30 = 0.8333)
    ]

    metrics = evaluate_highlights_precision_at_k(preds, gt, k=5, iou_threshold=0.50)
    assert metrics.evaluated_k == 5
    assert metrics.matched_clips == 4
    assert metrics.precision_at_k == pytest.approx(0.80)
    assert metrics.recall_at_k == pytest.approx(0.80)
    assert metrics.mean_iou > 0.70

    # Empty predictions case
    empty_metrics = evaluate_highlights_precision_at_k([], gt, k=5)
    assert empty_metrics.precision_at_k == 0.0
    assert empty_metrics.matched_clips == 0


def test_golden_evaluation_suite_all_four_languages():
    """Verify Highlight Engine v2 achieves Precision@5 >= 0.60 across en, hi, hinglish, and bn golden videos."""
    dataset = load_golden_highlights()
    videos = dataset.get("videos", [])
    assert len(videos) == 4
    langs = {v["language"] for v in videos}
    assert langs == {"en", "hi", "hinglish", "bn"}
    for v in videos:
        assert float(v["duration"]) == 900.0
        assert len(v["ground_truth"]) >= 5

    suite_res = run_golden_evaluation_suite(k=5, iou_threshold=0.50, min_precision_target=0.60)
    assert suite_res["video_count"] == 4
    assert suite_res["all_passed"] is True
    assert suite_res["mean_precision_at_k"] >= 0.60

    for vid_id, vid_report in suite_res["videos"].items():
        p_at_5 = vid_report["metrics"]["precision_at_k"]
        assert p_at_5 >= 0.60, f"{vid_id} failed precision@5 target: {p_at_5}"
        assert vid_report["passed"] is True


def test_pipeline_benchmark_and_cli_json_out(tmp_path: Path):
    """Verify deterministic pipeline benchmark measures all 6 stage latencies, peak RSS, throughput, and SLAs."""
    report = run_pipeline_benchmark(video_duration_s=1800.0)
    assert report["video_duration_s"] == 1800.0
    assert report["wall_time_s"] > 0.0
    assert report["throughput_factor"] > 10.0
    assert report["peak_rss_mb"] > 1.0

    stages = report["stage_latencies_ms"]
    for required_stage in ("asr", "analysis", "highlights", "reframe", "render", "publish"):
        assert required_stage in stages
        assert stages[required_stage] >= 0.0

    sla = report["sla"]
    assert sla["sla_gpu_30min_ok"] is True
    assert sla["sla_cpu_30min_ok"] is True
    assert sla["sla_preview_4s_ok"] is True
    assert sla["all_slas_met"] is True

    # Test CLI --json-out execution
    out_file = tmp_path / "bench_report.json"
    exit_code = bench_cli_main(["--video-duration-s", "1800", "--json-out", str(out_file)])
    assert exit_code == 0
    assert out_file.exists()
    parsed = json.loads(out_file.read_text(encoding="utf-8"))
    assert parsed["sla"]["all_slas_met"] is True
