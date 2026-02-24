from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from app.models import MixExportRequest
from app.services.audio_service import mix_audio

router = APIRouter(prefix="/api/mix", tags=["mix"])


@router.post("/export")
async def export_mix(req: MixExportRequest):
    try:
        output_path = mix_audio(
            paper_id=req.paper_id,
            music_id=req.music_id,
            speech_volume=req.speech_volume,
            music_volume=req.music_volume,
        )
    except FileNotFoundError as e:
        raise HTTPException(404, str(e))

    return FileResponse(
        str(output_path),
        media_type="audio/mpeg",
        filename=f"{req.paper_id}_mixed.mp3",
    )
