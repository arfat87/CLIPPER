from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import create_engine, inspect
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, SQLModel, select

from backend.app.db import get_alembic_config, init_schema
from backend.app.models import (
    AuditLog,
    BlockerRecord,
    Clip,
    DriveFile,
    DriveState,
    FeedbackEvent,
    Font,
    Job,
    JobStatus,
    Preset,
    PromptVersion,
    PublishPack,
    Render,
    Setting,
    StageCheckpoint,
    Template,
    TemplateVersion,
    Transcript,
)

EXPECTED_TABLES = {
    "job",
    "stage_checkpoint",
    "transcript",
    "clip",
    "render",
    "publish_pack",
    "template",
    "template_version",
    "preset",
    "font",
    "prompt_version",
    "setting",
    "drive_state",
    "drive_file",
    "blocker",
    "audit_log",
    "feedback_event",
}


def _seed_all_tables(engine) -> None:
    with Session(engine) as session:
        job = Job(
            id="job_01",
            source_kind="url",
            source_url="https://youtube.com/watch?v=test",
            source_ref="yt:test",
            platform="youtube",
            title="Test Video",
            duration=120.0,
            duration_s=120.0,
            language="hinglish",
            detected_language="hinglish",
            script_pref="latn",
            script="latn",
            genre="podcast",
            clip_brief="viral hooks",
            preset_id="preset_01",
            settings_json={"aspect": "9:16", "max_clips": 5},
            versions_json={"asr": "1"},
        )
        session.add(job)

        cp = StageCheckpoint(
            job_id="job_01",
            stage="transcribe",
            status="DONE",
            input_hash="abc123",
            artefact_path="jobs/job_01/asr/transcript.json",
            attempt=1,
            metrics_json={"duration_ms": 420},
        )
        session.add(cp)

        tr = Transcript(
            id="tr_01",
            job_id="job_01",
            language="hinglish",
            script="latn",
            engine="faster_whisper",
            model_id="small",
            path_json="jobs/job_01/asr/transcript.json",
            mean_conf=0.94,
            edited=False,
        )
        session.add(tr)

        clip = Clip(
            id="clip_01",
            job_id="job_01",
            rank=1,
            start=5.0,
            end=35.0,
            start_s=5.0,
            end_s=35.0,
            duration=30.0,
            score=0.91,
            score_mode="hybrid",
            title="Best Moment",
            preset_id="preset_01",
            plan_path="jobs/job_01/clips/clip_01/clip_plan.json",
            feedback="up",
            actual_views=1500,
        )
        session.add(clip)

        render = Render(
            id="ren_01",
            clip_id="clip_01",
            plan_hash="hash_v1",
            template_versions_json={"caption": "bold_pop_yellow@1"},
            aspect="9:16",
            profile="1080p_fast",
            path_mp4="jobs/job_01/renders/clip_01.mp4",
            status="DONE",
            progress_pct=100.0,
            duration_s=30.0,
        )
        session.add(render)

        pack = PublishPack(
            id="pp_01",
            clip_id="clip_01",
            platform="youtube_shorts",
            path_json="jobs/job_01/clips/clip_01/publish_pack.json",
            valid=True,
            warnings_json=[],
            template_id="default_publish",
        )
        session.add(pack)

        tpl = Template(
            id="bold_pop_yellow",
            kind="caption",
            name="Bold Pop Yellow",
            status="published",
            latest_version=1,
            source="builtin",
        )
        session.add(tpl)

        tpl_ver = TemplateVersion(
            id="tv_01",
            template_id="bold_pop_yellow",
            version=1,
            path="assets/templates/bold_pop_yellow/template.json",
            content_hash="sha256:deadbeef",
            valid=True,
            validation_json={"errors": []},
        )
        session.add(tpl_ver)

        preset = Preset(
            id="preset_01",
            name="Default Vertical",
            caption_tpl="bold_pop_yellow@1",
            layout_tpl="fill@latest",
            overlay_tpls_json=[],
            is_default=True,
        )
        session.add(preset)

        font = Font(
            id="font_01",
            family="Noto Sans Devanagari",
            style="Bold",
            path="assets/fonts/NotoSansDevanagari-Bold.ttf",
            scripts_json={"deva": True, "latn": True},
            license="OFL-1.1",
            license_ok=True,
        )
        session.add(font)

        prompt_ver = PromptVersion(
            id="pv_01",
            name="highlight_scorer",
            version=1,
            path="config/prompts/highlight_scorer.md",
            content_hash="sha256:cafe",
            active=True,
            created_by="system",
        )
        session.add(prompt_ver)

        setting = Setting(
            key="ui.default_aspect",
            value_json={"aspect": "9:16"},
        )
        session.add(setting)

        drive_state = DriveState(
            id="singleton",
            root_folder_id="root_123",
            status="DISABLED",
        )
        session.add(drive_state)

        drive_file = DriveFile(
            id="df_01",
            parent_ids_json=["root_123"],
            name="sample.mp4",
            mime="video/mp4",
            size=1024,
            path_in_mirror="sources/sample.mp4",
            route="source",
            status="SYNCED",
        )
        session.add(drive_file)

        blocker = BlockerRecord(
            id="BLK-0001",
            type="SECRET",
            status="OPEN",
            task_id="T-4.3",
            title="Missing Gemini Key",
            payload_json={"key": "GEMINI_API_KEY"},
        )
        session.add(blocker)

        audit = AuditLog(
            id="aud_01",
            actor="admin",
            action="template.publish",
            target="bold_pop_yellow@1",
            diff_json={"status": ["draft", "published"]},
        )
        session.add(audit)

        fb = FeedbackEvent(
            id="fb_01",
            clip_id="clip_01",
            kind="rating",
            value="up",
        )
        session.add(fb)

        session.commit()


def test_alembic_upgrade_seed_downgrade_upgrade_cycle(tmp_path: Path):
    db_path = tmp_path / "migration_test.db"
    db_url = f"sqlite:///{db_path}"
    cfg = get_alembic_config(database_url=db_url)

    # 1. Upgrade from empty DB to head
    command.upgrade(cfg, "head")

    engine = create_engine(db_url)
    try:
        inspector = inspect(engine)
        tables = set(inspector.get_table_names())
        assert EXPECTED_TABLES.issubset(tables)
        assert "alembic_version" in tables

        # Check key indexes exist
        job_indexes = {idx["name"] for idx in inspector.get_indexes("job")}
        assert "ix_job_status_created_at" in job_indexes

        cp_indexes = {idx["name"] for idx in inspector.get_indexes("stage_checkpoint")}
        assert "ix_stage_checkpoint_job_stage" in cp_indexes
        cp_uqs = {uq["name"] for uq in inspector.get_unique_constraints("stage_checkpoint")}
        assert "uq_stage_checkpoint_job_stage_attempt" in cp_uqs

        clip_indexes = {idx["name"] for idx in inspector.get_indexes("clip")}
        assert "ix_clip_job_rank" in clip_indexes

        render_indexes = {idx["name"] for idx in inspector.get_indexes("render")}
        assert "ix_render_clip_plan_hash" in render_indexes

        df_indexes = {idx["name"] for idx in inspector.get_indexes("drive_file")}
        assert "ix_drive_file_parent_ids_status" in df_indexes

        tv_indexes = {idx["name"] for idx in inspector.get_indexes("template_version")}
        assert "ix_template_version_tpl_ver" in tv_indexes

        # Verify zero metadata drift between 0001_initial_schema and SQLModel.metadata
        with engine.connect() as conn:
            mc = MigrationContext.configure(conn)
            diff = compare_metadata(mc, SQLModel.metadata)
            assert diff == [], f"Schema drift detected: {diff}"

        # 2. Seed rows in all tables and verify reads + unique constraint
        _seed_all_tables(engine)
        with Session(engine) as session:
            loaded_job = session.get(Job, "job_01")
            assert loaded_job is not None
            assert loaded_job.source_kind == "url"
            assert loaded_job.settings_json == {"aspect": "9:16", "max_clips": 5}
            assert session.exec(select(Transcript)).one().mean_conf == 0.94
            assert session.exec(select(Render)).one().plan_hash == "hash_v1"
            assert session.exec(select(PublishPack)).one().platform == "youtube_shorts"
            assert session.exec(select(FeedbackEvent)).one().value == "up"

            # Unique constraint on stage_checkpoint(job_id, stage, attempt)
            dup = StageCheckpoint(job_id="job_01", stage="transcribe", attempt=1)
            session.add(dup)
            with pytest.raises(IntegrityError):
                session.commit()
            session.rollback()
    finally:
        engine.dispose()

    # 3. Downgrade back to base cleanly
    command.downgrade(cfg, "base")
    engine_after_down = create_engine(db_url)
    try:
        tables_after_down = set(inspect(engine_after_down).get_table_names())
        assert tables_after_down.isdisjoint(EXPECTED_TABLES)
    finally:
        engine_after_down.dispose()

    # 4. Re-upgrade to head and re-seed cleanly
    command.upgrade(cfg, "head")
    engine_reup = create_engine(db_url)
    try:
        assert EXPECTED_TABLES.issubset(set(inspect(engine_reup).get_table_names()))
        _seed_all_tables(engine_reup)
        with Session(engine_reup) as session:
            assert session.get(Job, "job_01") is not None
    finally:
        engine_reup.dispose()


def test_init_schema_upgrades_legacy_sqlite_tables(tmp_path: Path):
    db_path = tmp_path / "legacy.db"
    conn = sqlite3.connect(db_path)
    try:
        conn.executescript(
            """
            CREATE TABLE job (
                id VARCHAR PRIMARY KEY,
                source_url VARCHAR NOT NULL,
                platform VARCHAR NOT NULL,
                title VARCHAR NOT NULL,
                duration FLOAT NOT NULL,
                language VARCHAR NOT NULL,
                detected_language VARCHAR NOT NULL,
                script_pref VARCHAR NOT NULL,
                aspect VARCHAR NOT NULL,
                auto_mode BOOLEAN NOT NULL,
                template_id VARCHAR NOT NULL,
                max_clips INTEGER NOT NULL,
                status VARCHAR NOT NULL,
                stage VARCHAR NOT NULL,
                progress_pct INTEGER NOT NULL,
                message VARCHAR NOT NULL,
                error VARCHAR,
                created_at VARCHAR NOT NULL,
                updated_at VARCHAR NOT NULL
            );
            INSERT INTO job VALUES (
                'legacy1', 'sample://sample_hinglish_podcast', 'sample', 'Legacy Job',
                30.0, 'hinglish', 'hinglish', 'auto', '9:16', 1, 'bold_pop_yellow', 5,
                'COMPLETED', 'COMPLETED', 100, 'Done', NULL, '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z'
            );
            CREATE TABLE clip (
                id VARCHAR PRIMARY KEY,
                job_id VARCHAR NOT NULL,
                rank INTEGER NOT NULL,
                start FLOAT NOT NULL,
                end FLOAT NOT NULL,
                duration FLOAT NOT NULL,
                score FLOAT NOT NULL,
                score_breakdown_json VARCHAR NOT NULL,
                title VARCHAR NOT NULL,
                hook_line VARCHAR NOT NULL,
                hashtags_json VARCHAR NOT NULL,
                reason VARCHAR NOT NULL,
                transcript_snippet VARCHAR NOT NULL,
                words_json VARCHAR NOT NULL,
                layout VARCHAR NOT NULL,
                template_id VARCHAR NOT NULL,
                aspect VARCHAR NOT NULL,
                status VARCHAR NOT NULL,
                render_mp4_path VARCHAR,
                render_ass_path VARCHAR,
                render_srt_path VARCHAR,
                thumb_path VARCHAR,
                created_at VARCHAR NOT NULL
            );
            """
        )
        conn.commit()
    finally:
        conn.close()

    engine = create_engine(f"sqlite:///{db_path}")
    try:
        init_schema(engine)
        with Session(engine) as session:
            legacy = session.get(Job, "legacy1")
            assert legacy is not None
            assert legacy.source_kind == "url"
            assert legacy.status == JobStatus.COMPLETED.value
            assert legacy.deleted_at is None
    finally:
        engine.dispose()
