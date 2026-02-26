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

def _normalize_indent(code: str) -> str:
    """Ensure every non-blank line has at least 8 spaces of indentation.

    LLM-generated code sometimes comes back with no indentation, or with
    4-space indentation.  We need exactly 8 spaces (inside `def construct`).

    Uses the most common (mode) base indentation rather than the minimum,
    so a single mis-indented line doesn't skew the whole block.
    """
    lines = code.split("\n")
    target = 8

    # Count indentation of each non-blank, non-comment-only line
    indent_counts: dict[int, int] = {}
    for line in lines:
        stripped = line.lstrip()
        if not stripped:
            continue
        indent = len(line) - len(stripped)
        indent_counts[indent] = indent_counts.get(indent, 0) + 1

    if not indent_counts:
        return code  # all blank

    # The base indent is the most common indentation level
    base_indent = max(indent_counts, key=indent_counts.get)

    # If there are lines with LESS indent than the mode, they're outliers
    # that lost their indentation. We'll fix those individually.
    delta = target - base_indent

    if delta == 0:
        # Base is already correct; just fix any outlier lines that have
        # less than 8 spaces (they lost their indent).
        result = []
        for line in lines:
            stripped = line.lstrip()
            if not stripped:
                result.append("")
                continue
            cur_indent = len(line) - len(stripped)
            if cur_indent < target:
                # Outlier — give it the target indent
                result.append(" " * target + stripped)
            else:
                result.append(line)
        return "\n".join(result)

    # Shift everything so the base lands on target=8
    result = []
    for line in lines:
        stripped = line.lstrip()
        if not stripped:
            result.append("")
            continue
        cur_indent = len(line) - len(stripped)
        new_indent = max(target, cur_indent + delta)
        result.append(" " * new_indent + stripped)
    return "\n".join(result)


def _wrap_scene(construct_body: str) -> str:
    """Wrap a construct() body in a full scene file."""
    normalized = _normalize_indent(construct_body)
    return (
        "from manim import *\n\n"
        "class SegmentScene(Scene):\n"
        "    def construct(self):\n"
        f"{normalized}\n"
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

async def render_manim_code(
    paper_id: str,
    segment_index: int,
    manim_code: str,
) -> tuple[Path, str | None]:
    """Try to render manim_code. Returns (output_path, error_or_None).

    On success the MP4 exists at output_path and error is None.
    On failure the MP4 does NOT exist and error contains the stderr.
    """
    out_dir = animations_dir() / paper_id
    out_dir.mkdir(parents=True, exist_ok=True)
    output_path = out_dir / f"segment_{segment_index:04d}.mp4"

    if output_path.exists():
        return output_path, None

    scene_code = _wrap_scene(manim_code)
    loop = asyncio.get_event_loop()
    try:
        await loop.run_in_executor(
            _get_executor(),
            functools.partial(
                _render_scene_sync,
                scene_code=scene_code,
                output_path=str(output_path),
            ),
        )
        return output_path, None
    except Exception as exc:
        error_msg = str(exc)
        logger.error(
            "Segment %d render failed: %s", segment_index, error_msg[:300],
        )
        return output_path, error_msg


async def render_title_card(
    paper_id: str,
    segment_index: int,
    section_title: str,
    duration: float,
) -> Path:
    """Render a simple title card fallback. Always succeeds."""
    out_dir = animations_dir() / paper_id
    out_dir.mkdir(parents=True, exist_ok=True)
    output_path = out_dir / f"segment_{segment_index:04d}.mp4"

    fallback_code = _title_card_code(section_title, duration)
    scene_code = _wrap_scene(fallback_code)
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(
        _get_executor(),
        functools.partial(
            _render_scene_sync,
            scene_code=scene_code,
            output_path=str(output_path),
        ),
    )
    return output_path
