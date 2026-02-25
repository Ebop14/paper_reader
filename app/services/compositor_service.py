import asyncio
import tempfile
from pathlib import Path

from app.models import VideoScript
from app.storage import animations_dir, audio_dir, videos_dir
from app.tasks.processing import update_task


async def _run_ffmpeg(args: list[str]) -> None:
    """Run an ffmpeg command as async subprocess."""
    proc = await asyncio.create_subprocess_exec(
        "ffmpeg", *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg failed: {stderr.decode()[-500:]}")


async def _combine_segment(
    video_path: Path,
    audio_path: Path,
    output_path: Path,
) -> None:
    """Mux one video + one audio into a single MP4.

    Loops the video if it's shorter than the audio so no speech is cut off.
    Re-encodes video (ultrafast preset) to support the loop + trim.
    """
    await _run_ffmpeg([
        "-y",
        "-stream_loop", "-1",       # loop video indefinitely
        "-i", str(video_path),
        "-i", str(audio_path),
        "-map", "0:v", "-map", "1:a",
        "-c:v", "libx264",
        "-preset", "ultrafast",
        "-crf", "28",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac",
        "-shortest",                 # trim to audio length (video loops past it)
        str(output_path),
    ])


async def composite_video(
    paper_id: str,
    script: VideoScript,
    task_id: str,
) -> Path:
    """Mux all segment videos + audio, then concat into final MP4."""
    anim_dir = animations_dir() / paper_id
    aud_dir = audio_dir() / paper_id
    vid_dir = videos_dir() / paper_id
    vid_dir.mkdir(parents=True, exist_ok=True)

    total = len(script.segments)
    muxed_files: list[Path] = []

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)

        for i, segment in enumerate(script.segments):
            update_task(
                task_id,
                stage_progress=(i / total) if total else 0,
                current_chunk=i,
                total_chunks=total,
                message=f"Compositing {i + 1}/{total}: {segment.section_title}",
            )

            video_file = anim_dir / (segment.animation_file or f"segment_{segment.segment_index:04d}.mp4")
            audio_file = aud_dir / (segment.audio_file or f"chunk_{segment.segment_index:04d}.wav")

            if not video_file.exists() or not audio_file.exists():
                continue

            muxed = tmp / f"muxed_{segment.segment_index:04d}.mp4"
            await _combine_segment(video_file, audio_file, muxed)
            muxed_files.append(muxed)

        if not muxed_files:
            raise RuntimeError("No segments to composite")

        # Write concat list
        concat_list = tmp / "concat.txt"
        concat_list.write_text(
            "\n".join(f"file '{f}'" for f in muxed_files)
        )

        final_output = vid_dir / "video.mp4"
        await _run_ffmpeg([
            "-y",
            "-f", "concat",
            "-safe", "0",
            "-i", str(concat_list),
            "-c", "copy",
            str(final_output),
        ])

    update_task(
        task_id,
        stage_progress=1.0,
        current_chunk=total,
        total_chunks=total,
        message="Compositing complete",
    )

    return final_output
