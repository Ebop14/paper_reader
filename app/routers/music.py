import uuid
import json
import asyncio
from pathlib import Path

from fastapi import APIRouter, UploadFile, HTTPException
from fastapi.responses import FileResponse, StreamingResponse

from app.storage import music_dir
from app.models import MusicGenerateRequest
from app.tasks.processing import create_task, update_task, sse_stream

router = APIRouter(prefix="/api/music", tags=["music"])

ALLOWED_EXTENSIONS = {".mp3", ".wav", ".ogg", ".m4a", ".flac"}


@router.post("/upload")
async def upload_music(file: UploadFile):
    if not file.filename:
        raise HTTPException(400, "No filename")

    ext = Path(file.filename).suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(400, f"Unsupported format. Allowed: {ALLOWED_EXTENSIONS}")

    music_id = uuid.uuid4().hex[:12]
    mdir = music_dir()
    content = await file.read()
    filepath = mdir / f"{music_id}{ext}"
    filepath.write_bytes(content)

    # Save metadata
    meta = {"id": music_id, "filename": file.filename, "path": str(filepath)}
    (mdir / f"{music_id}.json").write_text(json.dumps(meta))

    return meta


@router.post("/generate")
async def generate_music(req: MusicGenerateRequest):
    music_id = uuid.uuid4().hex[:12]
    task_id = f"musicgen-{music_id}"
    mdir = music_dir()
    mdir.mkdir(parents=True, exist_ok=True)
    output_path = mdir / f"{music_id}.wav"

    create_task(task_id, total_chunks=0)
    update_task(task_id, progress=0.0, message="Loading MusicGen model...")

    async def _run():
        try:
            from app.services.music_gen_service import generate_music as gen

            update_task(task_id, progress=0.5, message="Generating music...")
            await gen(req.prompt, req.duration, output_path)

            # Save metadata in same format as uploads
            display_name = req.prompt[:50]
            if len(req.prompt) > 50:
                display_name += "..."
            filename = f"Generated: {display_name}"
            meta = {"id": music_id, "filename": filename, "path": str(output_path)}
            (mdir / f"{music_id}.json").write_text(json.dumps(meta))

            update_task(task_id, status="completed", progress=1.0, message="Done")
        except Exception as e:
            update_task(task_id, status="failed", message=str(e))

    asyncio.create_task(_run())
    return {"task_id": task_id, "music_id": music_id}


@router.get("/generate/stream")
async def stream_music_gen(task_id: str):
    return StreamingResponse(sse_stream(task_id), media_type="text/event-stream")


@router.get("")
async def list_music():
    results = []
    mdir = music_dir()
    if mdir.exists():
        for f in sorted(mdir.glob("*.json")):
            results.append(json.loads(f.read_text()))
    return results


@router.get("/{music_id}")
async def serve_music(music_id: str):
    mdir = music_dir()
    # Find the audio file (any extension)
    for f in mdir.iterdir():
        if f.stem == music_id and f.suffix != ".json":
            return FileResponse(str(f))
    raise HTTPException(404, "Music file not found")


@router.delete("/{music_id}")
async def delete_music(music_id: str):
    mdir = music_dir()
    deleted = False
    for f in mdir.iterdir():
        if f.stem == music_id:
            f.unlink()
            deleted = True
    if not deleted:
        raise HTTPException(404, "Music file not found")
    return {"status": "deleted"}
