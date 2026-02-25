import asyncio
import functools
import json
import subprocess
import tempfile
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

from app.models import AnimationHint
from app.storage import animations_dir

_executor: ProcessPoolExecutor | None = None


def _get_executor() -> ProcessPoolExecutor:
    global _executor
    if _executor is None:
        _executor = ProcessPoolExecutor(max_workers=1)
    return _executor


# --- Style normalization ---

_STYLE_MAP = {
    # Write-like
    "write": "Write",
    "typewriter": "Write",
    "draw": "Write",
    "sketch": "Write",
    "trace": "Write",
    # FadeIn-like
    "fade_in": "FadeIn",
    "fade": "FadeIn",
    "appear": "FadeIn",
    "reveal": "FadeIn",
    "dissolve": "FadeIn",
    "emerge": "FadeIn",
    # Create-like
    "create": "Create",
    "build": "Create",
    "construct": "Create",
    "assemble": "Create",
    # GrowFromCenter-like
    "grow": "GrowFromCenter",
    "zoom_in": "GrowFromCenter",
    "scale_up": "GrowFromCenter",
    "pop": "GrowFromCenter",
    "expand": "GrowFromCenter",
    # Indicate-like
    "highlight": "Indicate",
    "pulse": "Indicate",
    "flash": "Indicate",
    "emphasize": "Indicate",
    "wiggle": "Indicate",
    "transform": "Indicate",
}

_DEFAULT_ANIMATION = "FadeIn"


def _resolve_style(style: str) -> str:
    return _STYLE_MAP.get(style.lower().strip(), _DEFAULT_ANIMATION)


def _render_segment_sync(
    hints_json: str,
    section_title: str,
    duration: float,
    output_path: str,
) -> str:
    """Build a Manim scene from hints and render to MP4. Runs in worker process."""
    hints = [json.loads(h) for h in json.loads(hints_json)]
    duration = max(duration, 1.0)

    # Build the scene script dynamically
    scene_code = _build_scene_code(hints, section_title, duration)

    # Write scene to temp file and render with manim CLI
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
            timeout=120,
        )

        if result.returncode != 0:
            raise RuntimeError(f"Manim render failed: {result.stderr[-500:]}")

        # Find the output MP4
        mp4_files = list(Path(tmpdir).rglob("*.mp4"))
        if not mp4_files:
            raise RuntimeError("No MP4 output found after manim render")

        # Copy to final destination
        import shutil
        shutil.copy2(str(mp4_files[0]), output_path)

    return output_path


def _build_scene_code(hints: list[dict], section_title: str, duration: float) -> str:
    """Generate a Manim Python scene file from animation hints."""
    num_hints = max(len(hints), 1)
    time_per_hint = duration / num_hints

    lines = [
        "from manim import *",
        "",
        "class SegmentScene(Scene):",
        "    def construct(self):",
    ]

    if not hints:
        # Title card fallback
        lines += _title_card_lines(section_title, duration)
    else:
        for i, hint in enumerate(hints):
            try:
                hint_lines = _hint_to_manim(hint, time_per_hint, i, num_hints)
                lines += hint_lines
            except Exception:
                # Fallback: simple text card for this hint
                lines += _fallback_hint_lines(hint, time_per_hint)

    return "\n".join(lines)


def _title_card_lines(title: str, duration: float) -> list[str]:
    safe_title = title.replace("\\", "\\\\").replace('"', '\\"')
    return [
        f'        title = Text("{safe_title}", font_size=36)',
        f"        self.play(FadeIn(title), run_time=1.0)",
        f"        self.wait({max(duration - 2.0, 0.5):.1f})",
        f"        self.play(FadeOut(title), run_time=1.0)",
    ]


def _fallback_hint_lines(hint: dict, time_per_hint: float) -> list[str]:
    desc = (hint.get("description") or "").replace("\\", "\\\\").replace('"', '\\"')
    if not desc:
        desc = hint.get("type", "")
    return [
        f'        _fb = Text("{desc[:80]}", font_size=28)',
        f"        self.play(FadeIn(_fb), run_time=0.5)",
        f"        self.wait({max(time_per_hint - 1.0, 0.3):.1f})",
        f"        self.play(FadeOut(_fb), run_time=0.5)",
    ]


def _hint_to_manim(hint: dict, time_per_hint: float, index: int, total: int) -> list[str]:
    """Convert a single hint dict to Manim scene lines."""
    hint_type = hint.get("type", "").lower().strip()
    content = hint.get("content", "")
    description = hint.get("description", "")
    style = _resolve_style(hint.get("style", ""))

    wait_time = max(time_per_hint - 1.5, 0.3)
    uid = f"h{index}"

    # Dispatch by hint type
    if hint_type == "equation":
        return _equation_lines(uid, content or description, style, wait_time)
    elif hint_type == "bullet_list":
        return _bullet_list_lines(uid, content or description, style, wait_time)
    elif hint_type == "diagram":
        return _diagram_lines(uid, content or description, style, wait_time)
    elif hint_type == "highlight":
        return _highlight_lines(uid, content or description, wait_time)
    elif hint_type == "code":
        return _code_lines(uid, content or description, style, wait_time)
    elif hint_type == "graph":
        return _graph_lines(uid, description, style, wait_time)
    elif hint_type == "image_placeholder":
        return _image_placeholder_lines(uid, description, style, wait_time)
    else:
        # Generic text with resolved animation
        return _generic_text_lines(uid, description or content, style, wait_time)


def _safe(s: str) -> str:
    return s.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def _equation_lines(uid: str, content: str, style: str, wait: float) -> list[str]:
    # Try MathTex; content may or may not be LaTeX
    safe = content.replace("\\", "\\\\").replace('"', '\\"')
    return [
        f"        try:",
        f'            {uid} = MathTex(r"{safe}", font_size=36)',
        f"        except Exception:",
        f'            {uid} = Text("{_safe(content[:100])}", font_size=28)',
        f"        self.play(Write({uid}), run_time=1.0)",
        f"        self.wait({wait:.1f})",
        f"        self.play(FadeOut({uid}), run_time=0.5)",
    ]


def _bullet_list_lines(uid: str, content: str, style: str, wait: float) -> list[str]:
    # Split by newline or bullet markers
    items = [line.strip().lstrip("-*").strip() for line in content.split("\n") if line.strip()]
    if not items:
        items = [content[:80]]
    items = items[:8]  # cap at 8 bullets

    lines = [f"        {uid}_items = VGroup()"]
    for j, item in enumerate(items):
        safe_item = _safe(item[:60])
        lines.append(f'        {uid}_items.add(Text("- {safe_item}", font_size=24))')

    lines += [
        f"        {uid}_items.arrange(DOWN, aligned_edge=LEFT, buff=0.3)",
        f"        self.play(FadeIn({uid}_items, lag_ratio=0.3), run_time=1.0)",
        f"        self.wait({wait:.1f})",
        f"        self.play(FadeOut({uid}_items), run_time=0.5)",
    ]
    return lines


def _diagram_lines(uid: str, content: str, style: str, wait: float) -> list[str]:
    # Simple boxes + arrows placeholder
    safe_desc = _safe(content[:60])
    return [
        f'        {uid}_label = Text("{safe_desc}", font_size=24)',
        f"        {uid}_box1 = Rectangle(width=2, height=1, color=BLUE).shift(LEFT * 2.5)",
        f"        {uid}_box2 = Rectangle(width=2, height=1, color=GREEN).shift(RIGHT * 2.5)",
        f"        {uid}_arrow = Arrow({uid}_box1.get_right(), {uid}_box2.get_left(), color=WHITE)",
        f"        {uid}_label.to_edge(UP)",
        f"        {uid}_grp = VGroup({uid}_label, {uid}_box1, {uid}_box2, {uid}_arrow)",
        f"        self.play(Create({uid}_grp), run_time=1.0)",
        f"        self.wait({wait:.1f})",
        f"        self.play(FadeOut({uid}_grp), run_time=0.5)",
    ]


def _highlight_lines(uid: str, content: str, wait: float) -> list[str]:
    safe = _safe(content[:100])
    return [
        f'        {uid} = Text("{safe}", font_size=32, color=YELLOW)',
        f"        self.play(FadeIn({uid}), run_time=0.5)",
        f"        self.play(Indicate({uid}, scale_factor=1.2), run_time=0.8)",
        f"        self.wait({max(wait - 0.8, 0.2):.1f})",
        f"        self.play(FadeOut({uid}), run_time=0.5)",
    ]


def _code_lines(uid: str, content: str, style: str, wait: float) -> list[str]:
    safe = _safe(content[:200])
    return [
        f"        try:",
        f'            {uid} = Code(code="""{safe}""", language="python", font_size=18, background="window")',
        f"        except Exception:",
        f'            {uid} = Text("{_safe(content[:80])}", font="Monospace", font_size=20)',
        f"        self.play(FadeIn({uid}), run_time=0.8)",
        f"        self.wait({wait:.1f})",
        f"        self.play(FadeOut({uid}), run_time=0.5)",
    ]


def _graph_lines(uid: str, description: str, style: str, wait: float) -> list[str]:
    safe = _safe(description[:60])
    return [
        f'        {uid}_title = Text("{safe}", font_size=24).to_edge(UP)',
        f"        {uid}_axes = Axes(x_range=[0, 5], y_range=[0, 5], x_length=5, y_length=3)",
        f"        {uid}_grp = VGroup({uid}_title, {uid}_axes)",
        f"        self.play(Create({uid}_grp), run_time=1.0)",
        f"        self.wait({wait:.1f})",
        f"        self.play(FadeOut({uid}_grp), run_time=0.5)",
    ]


def _image_placeholder_lines(uid: str, description: str, style: str, wait: float) -> list[str]:
    safe = _safe(description[:60])
    return [
        f"        {uid}_rect = Rectangle(width=5, height=3, color=GREY)",
        f'        {uid}_text = Text("{safe}", font_size=20)',
        f"        {uid}_grp = VGroup({uid}_rect, {uid}_text)",
        f"        self.play({style}({uid}_grp), run_time=0.8)",
        f"        self.wait({wait:.1f})",
        f"        self.play(FadeOut({uid}_grp), run_time=0.5)",
    ]


def _generic_text_lines(uid: str, content: str, style: str, wait: float) -> list[str]:
    safe = _safe(content[:100])
    return [
        f'        {uid} = Text("{safe}", font_size=30)',
        f"        self.play({style}({uid}), run_time=0.8)",
        f"        self.wait({wait:.1f})",
        f"        self.play(FadeOut({uid}), run_time=0.5)",
    ]


# --- Async API ---

async def render_segment(
    paper_id: str,
    segment_index: int,
    hints: list[AnimationHint],
    section_title: str,
    duration: float,
) -> Path:
    """Render animation for a single segment. Returns path to MP4 file."""
    out_dir = animations_dir() / paper_id
    out_dir.mkdir(parents=True, exist_ok=True)
    output_path = out_dir / f"segment_{segment_index:04d}.mp4"

    if output_path.exists():
        return output_path

    hints_json = json.dumps([h.model_dump_json() for h in hints])

    loop = asyncio.get_event_loop()
    try:
        await loop.run_in_executor(
            _get_executor(),
            functools.partial(
                _render_segment_sync,
                hints_json=hints_json,
                section_title=section_title,
                duration=duration,
                output_path=str(output_path),
            ),
        )
    except Exception:
        # Fallback: render a simple title card
        fallback_hints = json.dumps([])
        await loop.run_in_executor(
            _get_executor(),
            functools.partial(
                _render_segment_sync,
                hints_json=fallback_hints,
                section_title=section_title,
                duration=duration,
                output_path=str(output_path),
            ),
        )

    return output_path
