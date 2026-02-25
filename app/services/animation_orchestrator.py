from app.models import VideoScript
from app.services.animation_service import render_segment
from app.tasks.processing import update_task


async def generate_animations(
    paper_id: str,
    script: VideoScript,
    task_id: str,
) -> VideoScript:
    """Render Manim animations for each segment, patch the script."""
    total = len(script.segments)

    for i, segment in enumerate(script.segments):
        update_task(
            task_id,
            stage_progress=(i / total) if total else 0,
            current_chunk=i,
            total_chunks=total,
            message=f"Animation {i + 1}/{total}: {segment.section_title}",
        )

        duration = segment.actual_duration_seconds or segment.estimated_duration_seconds or 5.0

        mp4_path = await render_segment(
            paper_id=paper_id,
            segment_index=segment.segment_index,
            hints=segment.animation_hints,
            section_title=segment.section_title,
            duration=duration,
            manim_code=segment.manim_code,
        )

        segment.animation_file = mp4_path.name

    update_task(
        task_id,
        stage_progress=1.0,
        current_chunk=total,
        total_chunks=total,
        message="Animations complete",
    )

    return script
