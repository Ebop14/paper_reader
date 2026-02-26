import asyncio
import functools
import urllib.request
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

from app.config import settings
from app.storage import audio_dir

# GitHub release URLs for Kokoro model files
_KOKORO_MODEL_URL = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/kokoro-v1.0.onnx"
_KOKORO_VOICES_URL = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/voices-v1.0.bin"

# Process pool — each worker loads its own Kokoro model instance.
_executor: ProcessPoolExecutor | None = None


def _ensure_model_files():
    """Download Kokoro model files if they don't exist on disk."""
    model_path = Path(settings.kokoro_model_path)
    voices_path = Path(settings.kokoro_voices_path)

    model_path.parent.mkdir(parents=True, exist_ok=True)

    for path, url, label in [
        (model_path, _KOKORO_MODEL_URL, "model"),
        (voices_path, _KOKORO_VOICES_URL, "voices"),
    ]:
        if not path.exists():
            print(f"[TTS] Downloading Kokoro {label} to {path}...")
            tmp_path = path.with_suffix(".tmp")
            try:
                urllib.request.urlretrieve(url, str(tmp_path))
                tmp_path.rename(path)
                print(f"[TTS] Downloaded {label} ({path.stat().st_size / 1e6:.0f} MB)")
            except Exception:
                if tmp_path.exists():
                    tmp_path.unlink()
                raise


def _get_executor() -> ProcessPoolExecutor:
    global _executor
    if _executor is None:
        _ensure_model_files()
        _executor = ProcessPoolExecutor(
            max_workers=settings.tts_workers,
            initializer=_init_worker,
            initargs=(settings.kokoro_model_path, settings.kokoro_voices_path),
        )
    return _executor


# --- Worker process globals (one per process) ---

_worker_kokoro = None


def _init_worker(model_path: str, voices_path: str):
    """Called once when each worker process starts. Loads the Kokoro model."""
    global _worker_kokoro
    from kokoro_onnx import Kokoro
    _worker_kokoro = Kokoro(model_path, voices_path)


def _generate_sync(text: str, voice: str, speed: float, out_path: str):
    """Run TTS generation synchronously inside a worker process."""
    import soundfile as sf

    samples, sample_rate = _worker_kokoro.create(
        text, voice=voice, speed=speed, lang="en-us",
    )
    sf.write(out_path, samples, sample_rate)


# --- Async API (called from FastAPI) ---

async def generate_chunk(
    paper_id: str, chunk_index: int, text: str,
    voice: str = "af_heart", speed: float = 1.0,
    file_prefix: str | None = None,
) -> Path:
    """Generate TTS audio for a single text chunk. Returns path to WAV file."""
    out_dir = audio_dir() / paper_id
    out_dir.mkdir(parents=True, exist_ok=True)
    if file_prefix is None:
        file_prefix = f"chunk_{chunk_index:04d}"
    output_path = out_dir / f"{file_prefix}.wav"

    if output_path.exists():
        return output_path

    loop = asyncio.get_event_loop()
    await loop.run_in_executor(
        _get_executor(),
        functools.partial(
            _generate_sync,
            text=text,
            voice=voice,
            speed=speed,
            out_path=str(output_path),
        ),
    )
    return output_path


async def generate_all_chunks(
    paper_id: str, sections: list[dict],
    voice: str = "af_heart", speed: float = 1.0,
):
    """Generate TTS for all sections concurrently, yielding (index, path) as each completes."""
    results: asyncio.Queue[tuple[int, Path]] = asyncio.Queue()

    async def _run(idx: int, text: str):
        path = await generate_chunk(paper_id, idx, text, voice, speed)
        await results.put((idx, path))

    tasks = []
    for section in sections:
        idx = section["chunk_index"]
        task = asyncio.create_task(_run(idx, section["text"]))
        tasks.append(task)

    for _ in range(len(sections)):
        idx, path = await results.get()
        yield idx, path
