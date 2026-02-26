import logging

from app.models import VideoScript
from app.services.animation_service import render_manim_code, render_title_card
from app.services.annotator_service import annotate_segment
from app.tasks.processing import update_task

logger = logging.getLogger(__name__)

MAX_RENDER_RETRIES = 2


async def generate_animations(
    paper_id: str,
    script: VideoScript,
    task_id: str,
) -> VideoScript:
    """Render Manim animations for each segment, patch the script.

    On render failure, feeds the failed code + error back to the annotator
    LLM for a fix attempt before falling back to a title card.
    """
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
        manim_code = segment.manim_code

        # Try rendering, retrying with LLM fix on failure
        mp4_path = None
        for attempt in range(1 + MAX_RENDER_RETRIES):
            if not manim_code.strip():
                break  # no code to try, go straight to title card

            path, error = await render_manim_code(
                paper_id=paper_id,
                segment_index=segment.segment_index,
                manim_code=manim_code,
            )

            if error is None:
                mp4_path = path
                # Update segment if code was fixed on a retry
                if attempt > 0:
                    segment.manim_code = manim_code
                break

            # Last retry exhausted — don't call annotator again
            if attempt >= MAX_RENDER_RETRIES:
                logger.warning(
                    "Segment %d: exhausted %d retries, falling back to title card",
                    segment.segment_index, MAX_RENDER_RETRIES,
                )
                break

            # Ask the annotator to fix the code with error context
            logger.info(
                "Segment %d: retry %d — sending error to annotator for fix",
                segment.segment_index, attempt + 1,
            )
            update_task(
                task_id,
                message=f"Animation {i + 1}/{total}: fixing render error (attempt {attempt + 1})",
            )

            # Delete the failed output so render_manim_code doesn't skip it
            if path.exists():
                path.unlink()

            manim_code, _hints = await annotate_segment(
                narration_text=segment.narration_text,
                section_title=segment.section_title,
                paper_source_text="",  # not critical for a fix pass
                duration=duration,
                speaker_notes=segment.speaker_notes,
                visual_strategy=segment.visual_strategy,
                paper_id=paper_id,
                segment_index=segment.segment_index,
                previous_code=manim_code,
                previous_error=error,
            )

        # Fallback to title card if all attempts failed
        if mp4_path is None:
            mp4_path = await render_title_card(
                paper_id=paper_id,
                segment_index=segment.segment_index,
                section_title=segment.section_title,
                duration=duration,
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
