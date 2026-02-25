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


# --- Style normalization (legacy) ---

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


# --- Routing: rich vs legacy ---

def _has_rich_hints(hints: list[dict]) -> bool:
    """Check if any hint has objects/steps (rich format)."""
    return any(
        hint.get("objects") or hint.get("steps")
        for hint in hints
    )


def _build_scene_code(hints: list[dict], section_title: str, duration: float) -> str:
    """Generate a Manim Python scene file from animation hints."""
    lines = [
        "from manim import *",
        "",
        "class SegmentScene(Scene):",
        "    def construct(self):",
    ]

    if not hints:
        lines += _title_card_lines(section_title, duration)
    elif _has_rich_hints(hints):
        try:
            rich_lines = _build_rich_scene(hints, section_title, duration)
            lines += rich_lines
        except Exception:
            # Fall back to legacy rendering
            lines += _build_legacy_scene(hints, section_title, duration)
    else:
        lines += _build_legacy_scene(hints, section_title, duration)

    return "\n".join(lines)


def _build_legacy_scene(hints: list[dict], section_title: str, duration: float) -> list[str]:
    """Legacy rendering path — dispatch by hint type."""
    num_hints = max(len(hints), 1)
    time_per_hint = duration / num_hints
    lines = []
    for i, hint in enumerate(hints):
        try:
            hint_lines = _hint_to_manim(hint, time_per_hint, i, num_hints)
            lines += hint_lines
        except Exception:
            lines += _fallback_hint_lines(hint, time_per_hint)
    return lines


# =====================================================================
# Rich scene builder — uses objects/steps from validated hints
# =====================================================================

def _build_rich_scene(hints: list[dict], section_title: str, duration: float) -> list[str]:
    """Build scene code from rich hints with objects and steps."""
    lines: list[str] = []
    # Sort hints by start_fraction
    sorted_hints = sorted(hints, key=lambda h: h.get("start_fraction", 0.0))

    persistent_objects: set[str] = set()  # names of objects still on screen

    for hint_idx, hint in enumerate(sorted_hints):
        objects = hint.get("objects", [])
        steps = hint.get("steps", [])
        persistent = hint.get("persistent", False)

        # If no objects/steps, fall through to legacy for this hint
        if not objects and not steps:
            time_per = duration / max(len(sorted_hints), 1)
            try:
                lines += _hint_to_manim(hint, time_per, hint_idx, len(sorted_hints))
            except Exception:
                lines += _fallback_hint_lines(hint, time_per)
            continue

        hint_uid = f"_hint{hint_idx}"

        # Declare objects
        declared_names: list[str] = []
        for obj in objects:
            name = _sanitize_name(obj.get("name", f"obj{hint_idx}"))
            try:
                obj_lines = _declare_object(name, obj)
                lines += obj_lines
                declared_names.append(name)
            except Exception:
                # Fallback: declare as plain text
                desc = _safe(obj.get("params", {}).get("text", obj.get("name", ""))[:80])
                lines.append(f'        {name} = Text("{desc}", font_size=28)')
                declared_names.append(name)

        # Execute steps
        for step in steps:
            try:
                step_lines = _emit_step(step, hint_uid)
                lines += step_lines
            except Exception:
                # Skip failed steps silently
                pass

        # Cleanup non-persistent objects
        if not persistent:
            for name in declared_names:
                if name not in persistent_objects:
                    lines.append(f"        try:")
                    lines.append(f"            self.play(FadeOut({name}), run_time=0.3)")
                    lines.append(f"        except Exception:")
                    lines.append(f"            pass")
        else:
            persistent_objects.update(declared_names)

    # Final cleanup of any persistent objects still on screen
    if persistent_objects:
        obj_list = ", ".join(persistent_objects)
        lines.append(f"        try:")
        lines.append(f"            self.play(*[FadeOut(m) for m in [{obj_list}] if m in self.mobjects], run_time=0.5)")
        lines.append(f"        except Exception:")
        lines.append(f"            pass")

    # Safety: ensure at least one animation
    if not lines:
        lines = _title_card_lines(section_title, duration)

    return lines


def _sanitize_name(name: str) -> str:
    """Sanitize an object name to be a valid Python identifier."""
    import re as _re
    name = _re.sub(r'[^a-zA-Z0-9_]', '_', name)
    if not name or name[0].isdigit():
        name = "obj_" + name
    return name


# --- Object declaration ---

_DIRECTION_MAP = {
    "UP": "UP", "DOWN": "DOWN", "LEFT": "LEFT", "RIGHT": "RIGHT",
    "UL": "UL", "UR": "UR", "DL": "DL", "DR": "DR",
}


def _apply_position(name: str, position: str) -> list[str]:
    """Generate positioning code for an object."""
    if not position:
        return []
    pos = position.strip()
    if pos == "ORIGIN":
        return [f"        {name}.move_to(ORIGIN)"]
    if pos.startswith("to_edge("):
        return [f"        {name}.{pos}"]
    if pos.startswith("to_corner("):
        return [f"        {name}.{pos}"]
    if pos.startswith("[") and pos.endswith("]"):
        return [f"        {name}.move_to({pos})"]
    return []


def _declare_object(name: str, obj: dict) -> list[str]:
    """Declare a Manim object. Returns lines of Python code."""
    mtype = obj.get("mobject_type", "Text")
    params = obj.get("params", {})
    position = obj.get("position", "")
    lines: list[str] = []

    if mtype == "Text":
        text = _safe(str(params.get("text", name))[:120])
        fs = params.get("font_size", 32)
        color = params.get("color", "WHITE")
        lines.append(f'        {name} = Text("{text}", font_size={fs}, color={color})')

    elif mtype == "MathTex":
        tex = str(params.get("tex", "x")).replace('"', '\\"')
        fs = params.get("font_size", 36)
        color = params.get("color", "WHITE")
        lines.append(f"        try:")
        lines.append(f'            {name} = MathTex(r"{tex}", font_size={fs}, color={color})')
        lines.append(f"        except Exception:")
        lines.append(f'            {name} = Text("{_safe(tex[:80])}", font_size=28, color={color})')

    elif mtype == "BulletedList":
        items = params.get("items", ["item"])
        items = [_safe(str(item)[:60]) for item in items[:8]]
        items_str = ", ".join(f'"{item}"' for item in items)
        fs = params.get("font_size", 24)
        lines.append(f"        {name} = BulletedList({items_str}, font_size={fs})")

    elif mtype == "Rectangle":
        w = params.get("width", 2)
        h = params.get("height", 1)
        color = params.get("color", "BLUE")
        fill = params.get("fill_opacity", 0.0)
        lines.append(f"        {name} = Rectangle(width={w}, height={h}, color={color}, fill_opacity={fill})")

    elif mtype == "RoundedRectangle":
        w = params.get("width", 2)
        h = params.get("height", 1)
        cr = params.get("corner_radius", 0.2)
        color = params.get("color", "BLUE")
        fill = params.get("fill_opacity", 0.0)
        lines.append(f"        {name} = RoundedRectangle(width={w}, height={h}, corner_radius={cr}, color={color}, fill_opacity={fill})")

    elif mtype == "Circle":
        r = params.get("radius", 1)
        color = params.get("color", "BLUE")
        fill = params.get("fill_opacity", 0.0)
        lines.append(f"        {name} = Circle(radius={r}, color={color}, fill_opacity={fill})")

    elif mtype == "Arrow":
        start = params.get("start", [-2, 0, 0])
        end = params.get("end", [2, 0, 0])
        color = params.get("color", "WHITE")
        lines.append(f"        {name} = Arrow(start={start}, end={end}, color={color})")

    elif mtype == "Line":
        start = params.get("start", [-2, 0, 0])
        end = params.get("end", [2, 0, 0])
        color = params.get("color", "WHITE")
        lines.append(f"        {name} = Line(start={start}, end={end}, color={color})")

    elif mtype == "Dot":
        point = params.get("point", [0, 0, 0])
        color = params.get("color", "WHITE")
        r = params.get("radius", 0.08)
        lines.append(f"        {name} = Dot(point={point}, color={color}, radius={r})")

    elif mtype == "Brace":
        target = _sanitize_name(params.get("target", "ORIGIN"))
        direction = params.get("direction", "DOWN")
        if direction not in _DIRECTION_MAP:
            direction = "DOWN"
        text = _safe(str(params.get("text", ""))[:40])
        lines.append(f"        try:")
        lines.append(f"            {name} = BraceLabel({target}, r\"{text}\", brace_direction={direction})")
        lines.append(f"        except Exception:")
        lines.append(f'            {name} = Text("{text}", font_size=24)')

    elif mtype == "SurroundingRectangle":
        target = _sanitize_name(params.get("target", ""))
        color = params.get("color", "YELLOW")
        buff = params.get("buff", 0.2)
        lines.append(f"        try:")
        lines.append(f"            {name} = SurroundingRectangle({target}, color={color}, buff={buff})")
        lines.append(f"        except Exception:")
        lines.append(f'            {name} = Rectangle(width=2, height=1, color={color})')

    elif mtype == "Axes":
        xr = params.get("x_range", [0, 5, 1])
        yr = params.get("y_range", [0, 5, 1])
        xl = params.get("x_length", 5)
        yl = params.get("y_length", 3)
        lines.append(f"        {name} = Axes(x_range={xr}, y_range={yr}, x_length={xl}, y_length={yl})")

    elif mtype == "BarChart":
        values = params.get("values", [1, 2, 3])
        bar_names = [_safe(str(n)[:15]) for n in params.get("bar_names", [])]
        bar_colors = params.get("bar_colors", [])
        yr = params.get("y_range", None)
        parts = [f"values={values}"]
        if bar_names:
            parts.append(f"bar_names={bar_names}")
        if bar_colors:
            parts.append(f"bar_colors=[{', '.join(bar_colors)}]")
        if yr:
            parts.append(f"y_range={yr}")
        lines.append(f"        {name} = BarChart({', '.join(parts)})")

    elif mtype == "NumberLine":
        xr = params.get("x_range", [0, 10, 1])
        length = params.get("length", 6)
        inc_nums = params.get("include_numbers", True)
        lines.append(f"        {name} = NumberLine(x_range={xr}, length={length}, include_numbers={inc_nums})")

    elif mtype == "Code":
        code = str(params.get("code", "# code"))
        # Escape for triple-quoted string
        code = code.replace("\\", "\\\\").replace('"""', '\\"\\"\\"')
        lang = params.get("language", "python")
        fs = params.get("font_size", 18)
        lines.append(f"        try:")
        lines.append(f'            {name} = Code(code="""{code}""", language="{lang}", font_size={fs}, background="window")')
        lines.append(f"        except Exception:")
        lines.append(f'            {name} = Text("Code", font_size=24)')

    elif mtype == "Table":
        rows = params.get("rows", [["a", "b"]])
        # Sanitize all cell values
        safe_rows = [[_safe(str(cell)[:30]) for cell in row] for row in rows]
        rows_str = repr(safe_rows)
        col_labels = params.get("col_labels", None)
        row_labels = params.get("row_labels", None)
        lines.append(f"        try:")
        parts = [rows_str]
        if col_labels:
            safe_cl = [_safe(str(l)[:20]) for l in col_labels]
            parts.append(f"col_labels=[{', '.join(f'Text(\"{l}\")' for l in safe_cl)}]")
        if row_labels:
            safe_rl = [_safe(str(l)[:20]) for l in row_labels]
            parts.append(f"row_labels=[{', '.join(f'Text(\"{l}\")' for l in safe_rl)}]")
        lines.append(f"            {name} = Table({', '.join(parts)})")
        lines.append(f"        except Exception:")
        lines.append(f'            {name} = Text("Table", font_size=24)')

    elif mtype == "VGroup":
        children = params.get("children", [])
        children_names = [_sanitize_name(c) for c in children]
        if children_names:
            lines.append(f"        try:")
            lines.append(f"            {name} = VGroup({', '.join(children_names)})")
            lines.append(f"        except Exception:")
            lines.append(f"            {name} = VGroup()")
        else:
            lines.append(f"        {name} = VGroup()")

    else:
        # Unknown type — fallback to Text
        text = _safe(str(params.get("text", name))[:80])
        lines.append(f'        {name} = Text("{text}", font_size=28)')

    # Apply positioning
    lines += _apply_position(name, position)

    return lines


# --- Step emission ---

def _emit_step(step: dict, hint_uid: str) -> list[str]:
    """Emit Manim code for a single animation step."""
    action = step.get("action", "fade_in")
    target = _sanitize_name(step.get("target", "obj"))
    params = step.get("params", {})
    dur = step.get("duration", 1.0)
    run_time = params.get("run_time", dur)
    lines: list[str] = []

    if action == "create":
        lines.append(f"        self.play(Create({target}), run_time={run_time})")

    elif action == "write":
        lines.append(f"        self.play(Write({target}), run_time={run_time})")

    elif action == "fade_in":
        shift = params.get("shift", "")
        if shift and shift in _DIRECTION_MAP:
            lines.append(f"        self.play(FadeIn({target}, shift={shift}), run_time={run_time})")
        else:
            lines.append(f"        self.play(FadeIn({target}), run_time={run_time})")

    elif action == "fade_out":
        lines.append(f"        self.play(FadeOut({target}), run_time={run_time})")

    elif action == "indicate":
        sf = params.get("scale_factor", 1.2)
        color = params.get("color", "")
        if color:
            lines.append(f"        self.play(Indicate({target}, scale_factor={sf}, color={color}), run_time={run_time})")
        else:
            lines.append(f"        self.play(Indicate({target}, scale_factor={sf}), run_time={run_time})")

    elif action == "transform":
        dest = _sanitize_name(params.get("target", target))
        lines.append(f"        self.play(Transform({target}, {dest}), run_time={run_time})")

    elif action == "move_to":
        pos = params.get("position", "ORIGIN")
        if isinstance(pos, list):
            lines.append(f"        self.play({target}.animate.move_to({pos}), run_time={run_time})")
        else:
            lines.append(f"        self.play({target}.animate.move_to({pos}), run_time={run_time})")

    elif action == "scale":
        sf = params.get("scale_factor", 1.5)
        lines.append(f"        self.play({target}.animate.scale({sf}), run_time={run_time})")

    elif action == "change_color":
        color = params.get("color", "YELLOW")
        lines.append(f"        self.play({target}.animate.set_color({color}), run_time={run_time})")

    elif action == "wait":
        lines.append(f"        self.wait({dur})")

    elif action == "grow_arrow":
        lines.append(f"        self.play(GrowArrow({target}), run_time={run_time})")

    elif action == "add_plot":
        func_str = params.get("function", "lambda x: x")
        color = params.get("color", "YELLOW")
        xr = params.get("x_range", None)
        plot_name = f"{hint_uid}_plot"
        if xr:
            lines.append(f"        {plot_name} = {target}.plot({func_str}, color={color}, x_range={xr})")
        else:
            lines.append(f"        {plot_name} = {target}.plot({func_str}, color={color})")
        lines.append(f"        self.play(Create({plot_name}), run_time={run_time})")

    else:
        # Unknown action — do a simple FadeIn
        lines.append(f"        self.play(FadeIn({target}), run_time={run_time})")

    return lines


# =====================================================================
# Legacy hint rendering (kept for backward compatibility)
# =====================================================================

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
    """Convert a single hint dict to Manim scene lines (legacy path)."""
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
        return _generic_text_lines(uid, description or content, style, wait_time)


def _safe(s: str) -> str:
    return s.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def _equation_lines(uid: str, content: str, style: str, wait: float) -> list[str]:
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
    items = [line.strip().lstrip("-*").strip() for line in content.split("\n") if line.strip()]
    if not items:
        items = [content[:80]]
    items = items[:8]

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
