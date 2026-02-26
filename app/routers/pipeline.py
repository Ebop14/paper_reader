import asyncio
import json

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, StreamingResponse

from app.models import PipelineRequest
from app.storage import scripts_dir, audio_dir, exports_dir, animations_dir, videos_dir
from app.services.director_service import run_pipeline, run_from_script, run_reannotate
from app.services.audio_service import concat_speech_chunks
from app.tasks.processing import task_registry, create_task, sse_stream

router = APIRouter(prefix="/api/pipeline", tags=["pipeline"])

PIPELINE_STAGES = ["loading", "scripting", "voiceover", "annotating", "animation", "compositing", "done"]
RENDER_STAGES = ["loading", "animation", "compositing", "done"]
REANNOTATE_STAGES = ["loading", "annotating", "animation", "compositing", "done"]


@router.post("/{paper_id}/start")
async def start_pipeline(paper_id: str, req: PipelineRequest):
    task_id = f"pipeline-{paper_id}"

    existing = task_registry.get(task_id)
    if existing and existing["status"] == "running":
        return {"task_id": task_id, "status": "running"}

    create_task(task_id, stages=PIPELINE_STAGES)

    asyncio.create_task(
        run_pipeline(paper_id, task_id, req.voice, req.speed)
    )
    return {"task_id": task_id, "status": "started"}


@router.post("/{paper_id}/render")
async def start_render(paper_id: str):
    """Re-render animation + compositing from an existing annotated script."""
    task_id = f"render-{paper_id}"

    existing = task_registry.get(task_id)
    if existing and existing["status"] == "running":
        return {"task_id": task_id, "status": "running"}

    # Verify script exists
    script_path = scripts_dir() / paper_id / "script.json"
    if not script_path.exists():
        raise HTTPException(404, "No script found — run the full pipeline first")

    create_task(task_id, stages=RENDER_STAGES)

    asyncio.create_task(run_from_script(paper_id, task_id))
    return {"task_id": task_id, "status": "started"}


@router.get("/{paper_id}/render/stream")
async def stream_render(paper_id: str):
    task_id = f"render-{paper_id}"
    return StreamingResponse(sse_stream(task_id), media_type="text/event-stream")


@router.post("/{paper_id}/reannotate")
async def start_reannotate(paper_id: str):
    """Re-generate manim code from the existing script, then re-render."""
    task_id = f"reannotate-{paper_id}"

    existing = task_registry.get(task_id)
    if existing and existing["status"] == "running":
        return {"task_id": task_id, "status": "running"}

    script_path = scripts_dir() / paper_id / "script.json"
    if not script_path.exists():
        raise HTTPException(404, "No script found — run the full pipeline first")

    create_task(task_id, stages=REANNOTATE_STAGES)

    asyncio.create_task(run_reannotate(paper_id, task_id))
    return {"task_id": task_id, "status": "started"}


@router.get("/{paper_id}/reannotate/stream")
async def stream_reannotate(paper_id: str):
    task_id = f"reannotate-{paper_id}"
    return StreamingResponse(sse_stream(task_id), media_type="text/event-stream")


@router.get("/{paper_id}/stream")
async def stream_pipeline(paper_id: str):
    task_id = f"pipeline-{paper_id}"
    return StreamingResponse(sse_stream(task_id), media_type="text/event-stream")


@router.get("/{paper_id}/script")
async def get_script(paper_id: str):
    path = scripts_dir() / paper_id / "script.json"
    if not path.exists():
        raise HTTPException(404, "Script not found")
    return json.loads(path.read_text())


@router.get("/{paper_id}/audio")
async def list_audio(paper_id: str):
    chunk_dir = audio_dir() / paper_id
    if not chunk_dir.exists():
        return []
    files = sorted(chunk_dir.glob("chunk_*.wav"))
    return [{"index": i, "filename": f.name} for i, f in enumerate(files)]


@router.get("/{paper_id}/audio/{filename}")
async def serve_audio(paper_id: str, filename: str):
    path = audio_dir() / paper_id / filename
    if not path.exists():
        raise HTTPException(404, "Audio file not found")
    return FileResponse(str(path), media_type="audio/wav")


@router.post("/{paper_id}/export")
async def export_voiceover(paper_id: str):
    try:
        combined = concat_speech_chunks(paper_id)
    except FileNotFoundError as e:
        raise HTTPException(404, str(e))

    out_path = exports_dir() / f"{paper_id}_voiceover.mp3"
    combined.export(str(out_path), format="mp3")

    return FileResponse(
        str(out_path),
        media_type="audio/mpeg",
        filename=f"{paper_id}_voiceover.mp3",
    )


@router.get("/{paper_id}/animations")
async def list_animations(paper_id: str):
    anim_dir = animations_dir() / paper_id
    if not anim_dir.exists():
        return []
    files = sorted(anim_dir.glob("segment_*.mp4"))
    return [{"index": i, "filename": f.name} for i, f in enumerate(files)]


@router.get("/{paper_id}/animations/{filename}")
async def serve_animation(paper_id: str, filename: str):
    path = animations_dir() / paper_id / filename
    if not path.exists():
        raise HTTPException(404, "Animation file not found")
    return FileResponse(str(path), media_type="video/mp4")


@router.get("/{paper_id}/video")
async def serve_video(paper_id: str):
    path = videos_dir() / paper_id / "video.mp4"
    if not path.exists():
        raise HTTPException(404, "Video not found")
    return FileResponse(str(path), media_type="video/mp4")


@router.post("/{paper_id}/export-video")
async def export_video(paper_id: str):
    path = videos_dir() / paper_id / "video.mp4"
    if not path.exists():
        raise HTTPException(404, "Video not found")
    return FileResponse(
        str(path),
        media_type="video/mp4",
        filename=f"{paper_id}_video.mp4",
    )
