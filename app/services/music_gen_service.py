import asyncio
import functools
from pathlib import Path

from app.config import settings

# Lazy-loaded model state
_model = None
_lock = asyncio.Lock()


def _load_model():
    """Lazy-load the MusicGen model on first use."""
    global _model
    if _model is not None:
        return _model
    from audiocraft.models import MusicGen
    _model = MusicGen.get_pretrained(settings.musicgen_model)
    return _model


def _generate_sync(prompt: str, duration: int, output_path: str):
    """Run MusicGen generation synchronously (called in executor via lock)."""
    import torch
    from audiocraft.data.audio import audio_write

    model = _load_model()
    model.set_generation_params(duration=duration)
    wav = model.generate([prompt])
    # wav shape: (1, channels, samples) — squeeze batch dim
    # audio_write auto-appends .wav, so pass stem without extension
    stem = Path(output_path).with_suffix("")
    audio_write(str(stem), wav[0].cpu(), model.sample_rate, strategy="loudness")


async def generate_music(prompt: str, duration: int, output_path: Path) -> Path:
    """Generate music from a text prompt. Returns path to WAV file."""
    async with _lock:
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(
            None,
            functools.partial(
                _generate_sync,
                prompt=prompt,
                duration=duration,
                output_path=str(output_path),
            ),
        )
    return output_path
