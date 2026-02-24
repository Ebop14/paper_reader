import uuid
import json
from pathlib import Path

from fastapi import APIRouter, UploadFile, HTTPException
from fastapi.responses import FileResponse

from app.storage import music_dir

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
