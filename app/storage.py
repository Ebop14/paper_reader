from pathlib import Path
from app.config import settings


def _ensure(p: Path) -> Path:
    p.mkdir(parents=True, exist_ok=True)
    return p


def papers_dir() -> Path:
    return _ensure(settings.data_dir / "papers")


def processed_dir() -> Path:
    return _ensure(settings.data_dir / "processed")


def audio_dir() -> Path:
    return _ensure(settings.data_dir / "audio")


def music_dir() -> Path:
    return _ensure(settings.data_dir / "music")


def exports_dir() -> Path:
    return _ensure(settings.data_dir / "exports")
