from collections import OrderedDict

from app.models import PaperMeta, PaperSection, VideoScript
from app.storage import papers_dir, scripts_dir
from app.services.scriptwriter_service import write_script
from app.services.voiceover_service import generate_voiceover
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
        _save_script(paper_id, script)

        # Phase 3: Voiceover
        update_task(
            task_id,
            stage="voiceover",
            stage_progress=0.0,
            message="Starting voiceover generation...",
        )
        script = await generate_voiceover(paper_id, script, voice, speed, task_id)
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
