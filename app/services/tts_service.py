import asyncio
import functools
from pathlib import Path

from app.config import settings
from app.storage import audio_dir

# Lazy-loaded model state
_model = None
_lock = asyncio.Lock()


def _load_model():
    """Lazy-load the TTS model on first use."""
    global _model
    if _model is not None:
        return _model
    from mlx_audio.tts import load_model
    _model = load_model(settings.tts_model)
    return _model


def _generate_sync(text: str, voice: str, speed: float, out_dir: str, file_prefix: str):
    """Run TTS generation synchronously (called in executor via lock)."""
    from mlx_audio.tts.generate import generate_audio
    model = _load_model()
    generate_audio(
        text=text,
        model=model,
        voice=voice,
        speed=speed,
        output_path=out_dir,
        file_prefix=file_prefix,
        audio_format="wav",
        join_audio=True,
        play=False,
        verbose=False,
    )


async def generate_chunk(
    paper_id: str, chunk_index: int, text: str,
    voice: str = "serena", speed: float = 1.0,
) -> Path:
    """Generate TTS audio for a single text chunk. Returns path to WAV file."""
    out_dir = audio_dir() / paper_id
    out_dir.mkdir(parents=True, exist_ok=True)
    file_prefix = f"chunk_{chunk_index:04d}"
    output_path = out_dir / f"{file_prefix}.wav"

    if output_path.exists():
        return output_path

    async with _lock:
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(
            None,
            functools.partial(
                _generate_sync,
                text=text,
                voice=voice,
                speed=speed,
                out_dir=str(out_dir),
                file_prefix=file_prefix,
            ),
        )
    return output_path


async def generate_all_chunks(
    paper_id: str, sections: list[dict],
    voice: str = "serena", speed: float = 1.0,
):
    """Generate TTS for all sections, yielding (index, path) as each completes."""
    for section in sections:
        idx = section["chunk_index"]
        text = section["text"]
        path = await generate_chunk(paper_id, idx, text, voice, speed)
        yield idx, path
