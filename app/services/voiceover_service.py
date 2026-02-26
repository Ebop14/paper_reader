from pathlib import Path

from pydub import AudioSegment

from app.models import VideoScript
from app.services.tts_service import generate_chunk
from app.storage import audio_dir
from app.tasks.processing import update_task


async def generate_voiceover(
    paper_id: str,
    script: VideoScript,
    voice: str,
    speed: float,
    task_id: str,
) -> VideoScript:
    """Two-pass voiceover generation.

    Pass 1 — Read the entire script: concatenate all segment narrations into
    one continuous text and generate TTS as a single audio file. This gives
    the TTS model full context for natural prosody across segment boundaries.

    Pass 2 — Split into clips: divide the full audio back into per-segment
    WAV files using word-count-proportional timing, then measure actual
    durations for downstream animation and compositing.

    Audio is deliberately slowed (speed * 0.82) for a measured narration pace.
    """
    # Slow TTS down so animations have time to breathe
    effective_speed = speed * 0.85
    total = len(script.segments)
    out_dir = audio_dir() / paper_id
    out_dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # Pass 1: Generate TTS for the full narration
    # ------------------------------------------------------------------
    update_task(
        task_id,
        stage_progress=0.0,
        current_chunk=0,
        total_chunks=total,
        message="Generating full narration audio...",
    )

    # Collect narration text and word counts per segment
    narration_parts: list[str] = []
    word_counts: list[int] = []
    for seg in script.segments:
        text = seg.narration_text.strip()
        narration_parts.append(text)
        word_counts.append(len(text.split()))

    total_words = sum(word_counts)

    # Join with ellipsis pause markers — gives the TTS natural breathing room
    # between segments without introducing hard silence boundaries
    full_narration = " ... ".join(narration_parts)

    # Clear stale cache so we always regenerate
    full_path = out_dir / "full_narration.wav"
    if full_path.exists():
        full_path.unlink()

    full_path = await generate_chunk(
        paper_id=paper_id,
        chunk_index=0,
        text=full_narration,
        voice=voice,
        speed=effective_speed,
        file_prefix="full_narration",
    )

    update_task(
        task_id,
        stage_progress=0.7,
        message="Splitting audio into segments...",
    )

    # ------------------------------------------------------------------
    # Pass 2: Split full audio into per-segment clips
    # ------------------------------------------------------------------
    full_audio = AudioSegment.from_wav(str(full_path))
    total_duration_ms = len(full_audio)

    offset_ms = 0
    for i, segment in enumerate(script.segments):
        proportion = word_counts[i] / total_words
        seg_dur_ms = int(proportion * total_duration_ms)

        # Last segment gets all remaining audio (avoids rounding drift)
        if i == total - 1:
            seg_audio = full_audio[offset_ms:]
        else:
            seg_audio = full_audio[offset_ms : offset_ms + seg_dur_ms]

        wav_name = f"chunk_{segment.segment_index:04d}.wav"
        wav_path = out_dir / wav_name
        seg_audio.export(str(wav_path), format="wav")

        segment.actual_duration_seconds = len(seg_audio) / 1000.0
        segment.audio_file = wav_name
        segment.tts_chunks = [segment.narration_text]

        offset_ms += seg_dur_ms

        update_task(
            task_id,
            stage_progress=0.7 + 0.3 * (i + 1) / total,
            current_chunk=i + 1,
            total_chunks=total,
            message=f"Split segment {i + 1}/{total}: {segment.section_title}",
        )

    # Clean up the full narration file
    try:
        full_path.unlink()
    except OSError:
        pass

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
