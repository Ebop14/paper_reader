from collections import OrderedDict

from app.models import PaperMeta, PaperSection, ScriptSegment, VideoScript
from app.storage import papers_dir, scripts_dir, audio_dir, animations_dir, videos_dir
from app.services.scriptwriter_service import write_script
from app.services.annotator_service import annotate_script
from app.services.hint_validator import validate_and_repair_hints
from app.services.voiceover_service import generate_voiceover
from app.services.animation_orchestrator import generate_animations
from app.services.compositor_service import composite_video
from app.tasks.processing import update_task


def _load_paper_meta(paper_id: str) -> PaperMeta:
    meta_path = papers_dir() / paper_id / "meta.json"
    if not meta_path.exists():
        raise FileNotFoundError(f"Paper {paper_id} not found")
    return PaperMeta.model_validate_json(meta_path.read_text())


def _group_sections(
    sections: list[PaperSection],
) -> list[tuple[str, list[PaperSection]]]:
    """Group PaperSections by section title, preserving order."""
    groups: OrderedDict[str, list[PaperSection]] = OrderedDict()
    for s in sections:
        groups.setdefault(s.title, []).append(s)
    return list(groups.items())


def _save_script(paper_id: str, script: VideoScript) -> None:
    out_dir = scripts_dir() / paper_id
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "script.json").write_text(
        script.model_dump_json(indent=2)
    )


async def run_pipeline(
    paper_id: str,
    task_id: str,
    voice: str = "serena",
    speed: float = 1.0,
) -> None:
    """Orchestrate the full pipeline: script generation -> voiceover."""
    try:
        # Phase 1: Load and group
        update_task(task_id, stage="loading", message="Loading paper...")
        meta = _load_paper_meta(paper_id)
        chunk_groups = _group_sections(meta.sections)

        # Phase 2: Script generation
        update_task(
            task_id,
            stage="scripting",
            stage_progress=0.0,
            message="Starting script generation...",
        )
        script = await write_script(paper_id, meta, chunk_groups, task_id)

        # Phase 2b: Voiceover (generate audio FIRST so we get real durations)
        update_task(
            task_id,
            stage="voiceover",
            stage_progress=0.0,
            message="Starting voiceover generation...",
        )
        script = await generate_voiceover(paper_id, script, voice, speed, task_id)
        _save_script(paper_id, script)

        # Phase 2c: Annotation (uses actual audio durations from voiceover)
        update_task(
            task_id,
            stage="annotating",
            stage_progress=0.0,
            message="Starting annotation pass...",
        )
        script = await annotate_script(script, meta, chunk_groups, task_id)
        _save_script(paper_id, script)

        # Phase 2d: Validate and repair animation hints
        update_task(task_id, message="Validating animation hints...")
        segment_dicts = [s.model_dump() for s in script.segments]
        repaired = validate_and_repair_hints(segment_dicts)
        script.segments = [ScriptSegment(**s) for s in repaired]
        _save_script(paper_id, script)

        # Phase 4: Animation rendering
        update_task(
            task_id,
            stage="animation",
            stage_progress=0.0,
            message="Starting animation rendering...",
        )
        script = await generate_animations(paper_id, script, task_id)
        _save_script(paper_id, script)

        # Phase 5: Compositing
        update_task(
            task_id,
            stage="compositing",
            stage_progress=0.0,
            message="Starting video compositing...",
        )
        final_video = await composite_video(paper_id, script, task_id)
        script.video_file = final_video.name
        _save_script(paper_id, script)

        # Done
        update_task(
            task_id,
            status="completed",
            stage="done",
            stage_progress=1.0,
            progress=1.0,
            message="Pipeline complete",
        )

    except Exception as e:
        update_task(task_id, status="failed", message=str(e))


def _load_script(paper_id: str) -> VideoScript:
    script_path = scripts_dir() / paper_id / "script.json"
    if not script_path.exists():
        raise FileNotFoundError(f"Script for paper {paper_id} not found")
    return VideoScript.model_validate_json(script_path.read_text())


def _clear_renders(paper_id: str) -> None:
    """Remove existing animation and video files so they get re-rendered."""
    import shutil
    for d in (animations_dir() / paper_id, videos_dir() / paper_id):
        if d.exists():
            shutil.rmtree(d)


async def run_reannotate(paper_id: str, task_id: str) -> None:
    """Load existing script, re-generate manim code, then re-render animations + compositing."""
    try:
        update_task(task_id, stage="loading", message="Loading script and paper...")
        script = _load_script(paper_id)
        meta = _load_paper_meta(paper_id)
        chunk_groups = _group_sections(meta.sections)

        # Clear old renders
        _clear_renders(paper_id)
        for seg in script.segments:
            seg.manim_code = ""
            seg.animation_hints = []
            seg.animation_file = None
        script.video_file = None

        # Re-annotate with fresh manim code
        update_task(
            task_id,
            stage="annotating",
            stage_progress=0.0,
            message="Re-generating animation code...",
        )
        script = await annotate_script(script, meta, chunk_groups, task_id)

        # Validate hints for UI display
        update_task(task_id, message="Validating animation hints...")
        segment_dicts = [s.model_dump() for s in script.segments]
        repaired = validate_and_repair_hints(segment_dicts)
        script.segments = [ScriptSegment(**s) for s in repaired]
        _save_script(paper_id, script)

        # Animation rendering
        update_task(
            task_id,
            stage="animation",
            stage_progress=0.0,
            message="Starting animation rendering...",
        )
        script = await generate_animations(paper_id, script, task_id)
        _save_script(paper_id, script)

        # Compositing
        update_task(
            task_id,
            stage="compositing",
            stage_progress=0.0,
            message="Starting video compositing...",
        )
        final_video = await composite_video(paper_id, script, task_id)
        script.video_file = final_video.name
        _save_script(paper_id, script)

        update_task(
            task_id,
            status="completed",
            stage="done",
            stage_progress=1.0,
            progress=1.0,
            message="Re-annotation complete",
        )

    except Exception as e:
        update_task(task_id, status="failed", message=str(e))


def _clear_audio(paper_id: str) -> None:
    """Remove existing audio and video files so they get re-generated."""
    import shutil
    for d in (audio_dir() / paper_id, videos_dir() / paper_id):
        if d.exists():
            shutil.rmtree(d)


async def run_revoice(
    paper_id: str,
    task_id: str,
    voice: str = "serena",
    speed: float = 1.0,
) -> None:
    """Load existing script, re-generate voiceover with new voice/speed, then re-composite."""
    try:
        update_task(task_id, stage="loading", message="Loading script...")
        script = _load_script(paper_id)

        # Clear old audio and video (keep animations)
        _clear_audio(paper_id)
        for seg in script.segments:
            seg.audio_file = None
            seg.actual_duration_seconds = None
        script.actual_total_duration_seconds = None
        script.video_file = None

        # Re-generate voiceover
        update_task(
            task_id,
            stage="voiceover",
            stage_progress=0.0,
            message="Re-generating voiceover...",
        )
        script = await generate_voiceover(paper_id, script, voice, speed, task_id)
        _save_script(paper_id, script)

        # Re-composite with new audio + existing animations
        update_task(
            task_id,
            stage="compositing",
            stage_progress=0.0,
            message="Starting video compositing...",
        )
        final_video = await composite_video(paper_id, script, task_id)
        script.video_file = final_video.name
        _save_script(paper_id, script)

        update_task(
            task_id,
            status="completed",
            stage="done",
            stage_progress=1.0,
            progress=1.0,
            message="Revoice complete",
        )

    except Exception as e:
        update_task(task_id, status="failed", message=str(e))


async def run_from_script(paper_id: str, task_id: str) -> None:
    """Load an existing annotated script and run animation + compositing only."""
    try:
        # Phase 1: Load script
        update_task(task_id, stage="loading", message="Loading script...")
        script = _load_script(paper_id)

        # Clear old renders so animation_service doesn't skip them
        _clear_renders(paper_id)

        # Update segment animation_file / video_file refs
        for seg in script.segments:
            seg.animation_file = None
        script.video_file = None

        # Phase 2: Animation rendering
        update_task(
            task_id,
            stage="animation",
            stage_progress=0.0,
            message="Starting animation rendering...",
        )
        script = await generate_animations(paper_id, script, task_id)
        _save_script(paper_id, script)

        # Phase 3: Compositing
        update_task(
            task_id,
            stage="compositing",
            stage_progress=0.0,
            message="Starting video compositing...",
        )
        final_video = await composite_video(paper_id, script, task_id)
        script.video_file = final_video.name
        _save_script(paper_id, script)

        # Done
        update_task(
            task_id,
            status="completed",
            stage="done",
            stage_progress=1.0,
            progress=1.0,
            message="Render complete",
        )

    except Exception as e:
        update_task(task_id, status="failed", message=str(e))
