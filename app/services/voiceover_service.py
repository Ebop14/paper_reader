from pydub import AudioSegment

from app.models import VideoScript
from app.services.tts_service import generate_chunk
from app.tasks.processing import update_task


async def generate_voiceover(
    paper_id: str,
    script: VideoScript,
    voice: str,
    speed: float,
    task_id: str,
) -> VideoScript:
    """Generate TTS audio for each segment, measure durations, patch the script."""
    total = len(script.segments)

    for i, segment in enumerate(script.segments):
        update_task(
            task_id,
            stage_progress=(i / total) if total else 0,
            current_chunk=i,
            total_chunks=total,
            message=f"Voiceover {i + 1}/{total}: {segment.section_title}",
        )

        wav_path = await generate_chunk(
            paper_id=paper_id,
            chunk_index=segment.segment_index,
            text=segment.narration_text,
            voice=voice,
            speed=speed,
        )

        # Measure actual duration
        audio = AudioSegment.from_wav(str(wav_path))
        segment.actual_duration_seconds = len(audio) / 1000.0
        segment.audio_file = wav_path.name

    # Compute actual total
    script.actual_total_duration_seconds = sum(
        s.actual_duration_seconds for s in script.segments
        if s.actual_duration_seconds is not None
    )

    update_task(
        task_id,
        stage_progress=1.0,
        current_chunk=total,
        total_chunks=total,
        message="Voiceover complete",
    )

    return script
