import asyncio
import functools
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

from app.config import settings
from app.storage import audio_dir

# Process pool — each worker loads its own MLX model (MLX is not thread-safe,
# but separate processes each get their own Metal context).
_executor: ProcessPoolExecutor | None = None


def _get_executor() -> ProcessPoolExecutor:
    global _executor
    if _executor is None:
        _executor = ProcessPoolExecutor(
            max_workers=settings.tts_workers,
            initializer=_init_worker,
            initargs=(settings.tts_model,),
        )
    return _executor


# --- Worker process globals (one per process) ---

_worker_model = None


def _init_worker(model_name: str):
    """Called once when each worker process starts. Loads the TTS model."""
    global _worker_model
    from mlx_audio.tts import load_model
    _worker_model = load_model(model_name)


def _generate_sync(text: str, voice: str, speed: float, out_dir: str, file_prefix: str):
    """Run TTS generation synchronously inside a worker process."""
    from mlx_audio.tts.generate import generate_audio
    generate_audio(
        text=text,
        model=_worker_model,
        voice=voice,
        speed=speed,
        output_path=out_dir,
        file_prefix=file_prefix,
        audio_format="wav",
        join_audio=True,
        play=False,
        verbose=False,
    )


# --- Async API (called from FastAPI) ---

async def generate_chunk(
    paper_id: str, chunk_index: int, text: str,
    voice: str = "serena", speed: float = 1.0,
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
            out_dir=str(out_dir),
            file_prefix=file_prefix,
        ),
    )
    return output_path


async def generate_all_chunks(
    paper_id: str, sections: list[dict],
    voice: str = "serena", speed: float = 1.0,
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
