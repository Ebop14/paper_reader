import asyncio
import functools
import json
import logging
import subprocess
import tempfile
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

logger = logging.getLogger(__name__)

from app.models import AnimationHint
from app.storage import animations_dir

_executor: ProcessPoolExecutor | None = None


def _get_executor() -> ProcessPoolExecutor:
    global _executor
    if _executor is None:
        _executor = ProcessPoolExecutor(max_workers=1)
    return _executor


# =====================================================================
# Rendering
# =====================================================================

def _render_scene_sync(scene_code: str, output_path: str) -> str:
    """Write a Manim scene file, render it, copy MP4 to output_path.
    Runs in worker process.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        scene_file = Path(tmpdir) / "scene.py"
        scene_file.write_text(scene_code)

        cmd = [
            "manim",
            "render",
            str(scene_file),
            "SegmentScene",
            "-ql",  # low quality for speed (854x480)
            "--format", "mp4",
            "--media_dir", tmpdir,
            "--disable_caching",
        ]

        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=180,
        )

        if result.returncode != 0:
            raise RuntimeError(
                f"Manim render failed:\n{result.stderr[-1000:]}"
            )

        # Find the output MP4
        mp4_files = list(Path(tmpdir).rglob("*.mp4"))
        if not mp4_files:
            raise RuntimeError("No MP4 output found after manim render")

        # Copy to final destination
        import shutil
        shutil.copy2(str(mp4_files[0]), output_path)

    return output_path


# =====================================================================
# Scene code builders
# =====================================================================

def _wrap_scene(construct_body: str) -> str:
    """Wrap a construct() body in a full scene file."""
    return (
        "from manim import *\n\n"
        "class SegmentScene(Scene):\n"
        "    def construct(self):\n"
        f"{construct_body}\n"
    )


def _title_card_code(title: str, duration: float) -> str:
    """Fallback: simple title card."""
    safe = title.replace("\\", "\\\\").replace('"', '\\"')
    wait = max(duration - 2.0, 0.5)
    return (
        f'        title = Text("{safe}", font_size=36)\n'
        f'        self.play(FadeIn(title), run_time=1.0)\n'
        f'        self.wait({wait:.1f})\n'
        f'        self.play(FadeOut(title), run_time=1.0)'
    )


# =====================================================================
# Async API
# =====================================================================

async def render_segment(
    paper_id: str,
    segment_index: int,
    hints: list[AnimationHint],
    section_title: str,
    duration: float,
    manim_code: str = "",
) -> Path:
    """Render animation for a single segment. Returns path to MP4 file.

    If manim_code is provided, uses it directly as the construct() body.
    Otherwise falls back to a title card.
    """
    out_dir = animations_dir() / paper_id
    out_dir.mkdir(parents=True, exist_ok=True)
    output_path = out_dir / f"segment_{segment_index:04d}.mp4"

    if output_path.exists():
        return output_path

    loop = asyncio.get_event_loop()

    # Primary path: use LLM-generated Manim code
    if manim_code.strip():
        scene_code = _wrap_scene(manim_code)
        try:
            await loop.run_in_executor(
                _get_executor(),
                functools.partial(
                    _render_scene_sync,
                    scene_code=scene_code,
                    output_path=str(output_path),
                ),
            )
            return output_path
        except Exception as exc:
            logger.error(
                "Segment %d render failed, falling back to title card: %s",
                segment_index, exc,
            )

    # Fallback: title card
    fallback_code = _title_card_code(section_title, duration)
    scene_code = _wrap_scene(fallback_code)
    await loop.run_in_executor(
        _get_executor(),
        functools.partial(
            _render_scene_sync,
            scene_code=scene_code,
            output_path=str(output_path),
        ),
    )

    return output_path
