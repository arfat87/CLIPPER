from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
from pathlib import Path

import jsonschema
import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
SAMPLES_DIR = REPO_ROOT / "assets" / "samples"
FIXTURES_DIR = SAMPLES_DIR / "fixtures"
SCHEMAS_DIR = REPO_ROOT / "contracts" / "schemas"
TEMPLATES_V2_DIR = REPO_ROOT / "assets" / "templates_v2"
SHA256SUMS_PATH = SAMPLES_DIR / "SHA256SUMS"

SAMPLE_IDS = [
    "sample_english_keynote",
    "sample_hindi_motivation",
    "sample_hinglish_podcast",
    "sample_bengali_tech",
]

FIXTURE_SCHEMA_MAP = [
    ("transcript.example.json", "transcript.schema.json"),
    ("clip_plan.example.json", "clip_plan.schema.json"),
    ("publish_pack.example.json", "publish_pack.schema.json"),
    ("drive_manifest.example.json", "drive_manifest.schema.json"),
]

EXPECTED_TEMPLATES_V2 = [
    "bold_pop_yellow",
    "boxed_black",
    "clean_white",
    "karaoke_green",
    "minimal_lowercase",
    "neon_glow",
]


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def test_sha256sums_exists_sorted_and_matches_all_files() -> None:
    assert SHA256SUMS_PATH.is_file(), f"Missing checksum manifest: {SHA256SUMS_PATH}"
    raw_lines = [
        line for line in SHA256SUMS_PATH.read_text(encoding="utf-8").splitlines() if line.strip()
    ]
    assert len(raw_lines) >= 12

    parsed: list[tuple[str, str]] = []
    for line in raw_lines:
        parts = line.split("  ", maxsplit=1)
        assert len(parts) == 2, f"Invalid SHA256SUMS line format: {line!r}"
        digest, rel_path = parts
        assert len(digest) == 64 and all(c in "0123456789abcdef" for c in digest)
        parsed.append((digest, rel_path))

    rel_paths = [rel for _, rel in parsed]
    assert rel_paths == sorted(rel_paths), "SHA256SUMS entries must be sorted by relative path"

    expected_rel_paths = sorted(
        [f"{sid}.mp4" for sid in SAMPLE_IDS]
        + [f"{sid}.transcript.json" for sid in SAMPLE_IDS]
        + [f"fixtures/{name}" for name, _ in FIXTURE_SCHEMA_MAP]
    )
    for expected in expected_rel_paths:
        assert expected in rel_paths, f"Missing {expected} from SHA256SUMS"

    for expected_digest, rel_path in parsed:
        target = SAMPLES_DIR / rel_path
        assert target.is_file(), f"Listed file missing: {target}"
        actual_digest = _sha256_file(target)
        assert actual_digest == expected_digest, (
            f"SHA-256 mismatch for {rel_path}: expected {expected_digest}, got {actual_digest}"
        )


@pytest.mark.parametrize("sample_id", SAMPLE_IDS)
def test_sample_mp4_and_transcript_valid(sample_id: str) -> None:
    mp4_path = SAMPLES_DIR / f"{sample_id}.mp4"
    transcript_path = SAMPLES_DIR / f"{sample_id}.transcript.json"

    assert mp4_path.is_file(), f"Missing sample video: {mp4_path}"
    assert mp4_path.stat().st_size > 0, f"Sample video is empty: {mp4_path}"

    assert transcript_path.is_file(), f"Missing sample transcript: {transcript_path}"
    transcript = json.loads(transcript_path.read_text(encoding="utf-8"))
    assert transcript["id"] == sample_id
    assert transcript["language"] in {"en", "hi", "hinglish", "bn"}
    assert transcript["script"] in {"latn", "deva", "beng"}
    assert float(transcript["duration"]) > 0
    assert isinstance(transcript["segments"], list) and len(transcript["segments"]) > 0
    for seg in transcript["segments"]:
        assert seg["end"] > seg["start"] >= 0
        assert isinstance(seg["text"], str) and seg["text"].strip()
        assert isinstance(seg["words"], list) and len(seg["words"]) > 0
        for word in seg["words"]:
            assert isinstance(word["w"], str) and word["w"]
            assert word["e"] >= word["s"] >= 0

    ffprobe = shutil.which("ffprobe")
    if ffprobe is not None:
        proc = subprocess.run(
            [
                ffprobe,
                "-v",
                "error",
                "-show_entries",
                "format=duration:stream=codec_type,codec_name,width,height",
                "-of",
                "json",
                str(mp4_path),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        assert proc.returncode == 0, f"ffprobe failed for {mp4_path}: {proc.stderr}"
        probe = json.loads(proc.stdout)
        streams = probe.get("streams", [])
        video_streams = [s for s in streams if s.get("codec_type") == "video"]
        assert len(video_streams) >= 1, f"No video stream in {mp4_path}"
        assert int(video_streams[0].get("width", 0)) > 0
        assert int(video_streams[0].get("height", 0)) > 0
        duration = float(probe.get("format", {}).get("duration", 0.0))
        assert duration > 0.0


@pytest.mark.parametrize("fixture_name,schema_name", FIXTURE_SCHEMA_MAP)
def test_fixture_validates_against_contract_schema(fixture_name: str, schema_name: str) -> None:
    fixture_path = FIXTURES_DIR / fixture_name
    schema_path = SCHEMAS_DIR / schema_name

    assert fixture_path.is_file(), f"Missing fixture: {fixture_path}"
    assert schema_path.is_file(), f"Missing schema: {schema_path}"

    instance = json.loads(fixture_path.read_text(encoding="utf-8"))
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    jsonschema.validate(instance=instance, schema=schema)


def test_all_v2_templates_validate_against_caption_template_schema() -> None:
    schema_path = SCHEMAS_DIR / "caption_template.schema.json"
    schema = json.loads(schema_path.read_text(encoding="utf-8"))

    template_files = sorted(TEMPLATES_V2_DIR.glob("*/template.json"))
    found_ids = [p.parent.name for p in template_files]
    assert found_ids == sorted(EXPECTED_TEMPLATES_V2)
    assert len(template_files) == 6

    for template_path in template_files:
        data = json.loads(template_path.read_text(encoding="utf-8"))
        jsonschema.validate(instance=data, schema=schema)
        assert data["meta"]["id"] == template_path.parent.name
