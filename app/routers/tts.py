import asyncio
import json
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, StreamingResponse

from app.models import TTSRequest
from app.storage import audio_dir, processed_dir
from app.services.tts_service import generate_all_chunks
from app.tasks.processing import task_registry, create_task, update_task, sse_stream

router = APIRouter(prefix="/api/tts", tags=["tts"])


@router.post("/generate")
async def start_tts(req: TTSRequest):
    # Load processed text
    proc_dir = processed_dir() / req.paper_id
    # Try narrated first, then verbatim, then raw sections
    sections = None
    for mode in ("narrated", "verbatim"):
        path = proc_dir / f"{mode}.json"
        if path.exists():
            sections = json.loads(path.read_text())
            break

    if sections is None:
        # Fall back to raw extracted sections
        from app.storage import papers_dir
        meta_path = papers_dir() / req.paper_id / "meta.json"
        if not meta_path.exists():
            raise HTTPException(404, "Paper not found")
        meta = json.loads(meta_path.read_text())
        sections = meta["sections"]

    task_id = f"tts-{req.paper_id}"
    existing = task_registry.get(task_id)
    if existing and existing["status"] == "running":
        return {"task_id": task_id, "status": "running"}

    create_task(task_id, total_chunks=len(sections))

    asyncio.create_task(
        _run_tts(task_id, req.paper_id, sections, req.voice, req.speed)
    )
    return {"task_id": task_id, "status": "started"}


async def _run_tts(
    task_id: str, paper_id: str, sections: list[dict],
    voice: str, speed: float,
):
    try:
        async for idx, path in generate_all_chunks(paper_id, sections, voice, speed):
            update_task(
                task_id, status="running", current_chunk=idx + 1,
                message=f"Generated audio for chunk {idx + 1}/{len(sections)}"
            )
        update_task(task_id, status="completed", message="TTS generation complete")
    except Exception as e:
        update_task(task_id, status="failed", message=str(e))


@router.get("/{paper_id}/stream")
async def stream_tts(paper_id: str):
    task_id = f"tts-{paper_id}"
    return StreamingResponse(sse_stream(task_id), media_type="text/event-stream")


@router.get("/{paper_id}/chunks")
async def list_chunks(paper_id: str):
    """List all generated audio chunks for a paper."""
    chunk_dir = audio_dir() / paper_id
    if not chunk_dir.exists():
        return []
    files = sorted(chunk_dir.glob("chunk_*.wav"))
    return [{"index": i, "filename": f.name} for i, f in enumerate(files)]


@router.get("/{paper_id}/{filename}")
async def serve_audio(paper_id: str, filename: str):
    path = audio_dir() / paper_id / filename
    if not path.exists():
        raise HTTPException(404, "Audio file not found")
    return FileResponse(str(path), media_type="audio/wav")
