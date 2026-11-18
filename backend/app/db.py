from __future__ import annotations

from collections.abc import Generator
from pathlib import Path

from alembic.config import Config as AlembicConfig
from sqlalchemy import Engine, text
from sqlmodel import Session, SQLModel, create_engine

from backend.app.config import ROOT_DIR, get_settings

_engine: Engine | None = None


def _sqlite_default_clause(col) -> str:
    default = col.default
    if default is None or not getattr(default, "is_scalar", False):
        return ""
    arg = default.arg
    if isinstance(arg, bool):
        return f" DEFAULT {1 if arg else 0}"
    if isinstance(arg, (int, float)):
        return f" DEFAULT {arg}"
    if isinstance(arg, str):
        escaped = arg.replace("'", "''")
        return f" DEFAULT '{escaped}'"
    return ""


def _sync_sqlite_missing_columns(engine: Engine) -> None:
    if engine.dialect.name != "sqlite":
        return
    with engine.begin() as conn:
        existing_tables = {
            row[0]
            for row in conn.execute(
                text("SELECT name FROM sqlite_master WHERE type='table'")
            ).fetchall()
        }
        for table in SQLModel.metadata.sorted_tables:
            if table.name not in existing_tables:
                continue
            existing_cols = {
                row[1]
                for row in conn.execute(text(f'PRAGMA table_info("{table.name}")')).fetchall()
            }
            for col in table.columns:
                if col.name in existing_cols:
                    continue
                col_type = col.type.compile(dialect=engine.dialect)
                default_clause = _sqlite_default_clause(col)
                if not col.nullable and default_clause:
                    ddl = (
                        f'ALTER TABLE "{table.name}" '
                        f'ADD COLUMN "{col.name}" {col_type} NOT NULL{default_clause}'
                    )
                else:
                    ddl = (
                        f'ALTER TABLE "{table.name}" '
                        f'ADD COLUMN "{col.name}" {col_type}{default_clause}'
                    )
                conn.execute(text(ddl))


def init_schema(engine: Engine) -> None:
    from backend.app.models import entities  # noqa: F401

    SQLModel.metadata.create_all(engine)
    _sync_sqlite_missing_columns(engine)


def get_engine() -> Engine:
    global _engine
    if _engine is None:
        settings = get_settings()
        connect_args = (
            {"check_same_thread": False} if settings.database_url.startswith("sqlite") else {}
        )
        _engine = create_engine(settings.database_url, connect_args=connect_args)
        init_schema(_engine)
    return _engine


def reset_engine() -> None:
    global _engine
    if _engine is not None:
        _engine.dispose()
        _engine = None


def init_db() -> None:
    init_schema(get_engine())


def get_session() -> Generator[Session, None, None]:
    with Session(get_engine()) as session:
        yield session


def get_alembic_config(database_url: str | None = None) -> AlembicConfig:
    ini_path = Path(ROOT_DIR) / "migrations" / "alembic.ini"
    cfg = AlembicConfig(str(ini_path))
    cfg.set_main_option("script_location", str(Path(ROOT_DIR) / "migrations"))
    if database_url is not None:
        cfg.set_main_option("sqlalchemy.url", database_url)
    return cfg
