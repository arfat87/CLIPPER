from __future__ import annotations

from collections.abc import Generator

from sqlmodel import Session, SQLModel, create_engine

from backend.app.config import get_settings

_engine = None


def get_engine():
    global _engine
    if _engine is None:
        from backend.app.models import entities  # noqa: F401

        settings = get_settings()
        connect_args = {"check_same_thread": False} if settings.database_url.startswith("sqlite") else {}
        _engine = create_engine(settings.database_url, connect_args=connect_args)
        SQLModel.metadata.create_all(_engine)
    return _engine


def init_db() -> None:
    from backend.app.models import entities  # noqa: F401

    engine = get_engine()
    SQLModel.metadata.create_all(engine)


def get_session() -> Generator[Session, None, None]:
    with Session(get_engine()) as session:
        yield session
