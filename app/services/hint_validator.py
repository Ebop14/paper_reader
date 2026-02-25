"""Validate and repair animation hints before rendering.

Ensures all mobject_type, action, and color values are within the supported
whitelist, clamps durations, enforces minimum hint density, and normalizes
timing fractions.
"""

VALID_MOBJECT_TYPES = {
    "Text", "MathTex", "BulletedList", "Rectangle", "RoundedRectangle",
    "Circle", "Arrow", "Line", "Dot", "Brace", "SurroundingRectangle",
    "Axes", "BarChart", "NumberLine", "Code", "Table", "VGroup",
}

VALID_ACTIONS = {
    "create", "write", "fade_in", "fade_out", "indicate", "transform",
    "move_to", "scale", "change_color", "wait", "grow_arrow", "add_plot",
}

VALID_COLORS = {
    "WHITE", "GREY", "RED", "BLUE", "GREEN", "YELLOW", "ORANGE",
    "PURPLE", "TEAL", "PINK", "GOLD", "MAROON",
}

MIN_DURATION = 0.3
MAX_DURATION = 5.0
MIN_HINTS_PER_SEGMENT = 2


def _validate_color(color: str) -> str:
    """Return the color if valid, else WHITE."""
    if color.upper() in VALID_COLORS:
        return color.upper()
    return "WHITE"


def _validate_object(obj: dict) -> dict:
    """Validate and repair a single ManimObject dict."""
    obj = dict(obj)
    if obj.get("mobject_type") not in VALID_MOBJECT_TYPES:
        obj["mobject_type"] = "Text"
        # Ensure there's at least a text param for fallback
        if "text" not in obj.get("params", {}):
            obj.setdefault("params", {})["text"] = obj.get("name", "item")

    # Validate colors in params
    params = obj.get("params", {})
    if "color" in params:
        params["color"] = _validate_color(params["color"])
    if "bar_colors" in params:
        params["bar_colors"] = [_validate_color(c) for c in params["bar_colors"]]
    obj["params"] = params

    return obj


def _validate_step(step: dict) -> dict:
    """Validate and repair a single AnimationStep dict."""
    step = dict(step)
    if step.get("action") not in VALID_ACTIONS:
        step["action"] = "fade_in"

    # Clamp duration
    dur = step.get("duration", 1.0)
    try:
        dur = float(dur)
    except (ValueError, TypeError):
        dur = 1.0
    step["duration"] = max(MIN_DURATION, min(MAX_DURATION, dur))

    # Validate colors in params
    params = step.get("params", {})
    if "color" in params:
        params["color"] = _validate_color(params["color"])
    step["params"] = params

    return step


def _validate_hint(hint: dict, section_title: str = "") -> dict:
    """Validate and repair a single AnimationHint dict."""
    hint = dict(hint)

    # Validate anchor_text: if empty but objects/steps exist, use section_title
    if not hint.get("anchor_text", "").strip():
        if hint.get("objects") or hint.get("steps"):
            hint["anchor_text"] = section_title or ""

    # Validate objects
    hint["objects"] = [_validate_object(o) for o in hint.get("objects", [])]

    # Validate steps
    hint["steps"] = [_validate_step(s) for s in hint.get("steps", [])]

    # Repair empty step targets: match to declared objects by index
    obj_names = [o.get("name", "") for o in hint["objects"] if o.get("name")]
    if obj_names:
        for i, step in enumerate(hint["steps"]):
            if not step.get("target", "").strip():
                step["target"] = obj_names[min(i, len(obj_names) - 1)]

    # Clamp fractions
    sf = hint.get("start_fraction", 0.0)
    ef = hint.get("end_fraction", 1.0)
    try:
        sf = max(0.0, min(1.0, float(sf)))
    except (ValueError, TypeError):
        sf = 0.0
    try:
        ef = max(0.0, min(1.0, float(ef)))
    except (ValueError, TypeError):
        ef = 1.0
    if ef <= sf:
        ef = min(sf + 0.2, 1.0)
    hint["start_fraction"] = sf
    hint["end_fraction"] = ef

    return hint


def _make_title_card_hint(section_title: str, start: float, end: float) -> dict:
    """Create a minimal title-card hint as padding."""
    safe_title = section_title or "Section"
    return {
        "type": "highlight",
        "description": safe_title,
        "content": "",
        "style": "",
        "objects": [
            {
                "name": "title",
                "mobject_type": "Text",
                "params": {"text": safe_title, "font_size": 36, "color": "WHITE"},
                "position": "ORIGIN",
            }
        ],
        "steps": [
            {"action": "fade_in", "target": "title", "params": {"run_time": 0.5}, "duration": 0.5},
            {"action": "wait", "target": "title", "params": {}, "duration": 1.5},
            {"action": "fade_out", "target": "title", "params": {"run_time": 0.5}, "duration": 0.5},
        ],
        "persistent": False,
        "start_fraction": start,
        "end_fraction": end,
    }


def _normalize_fractions(hints: list[dict]) -> list[dict]:
    """Ensure hint fractions are sequential and non-overlapping."""
    n = len(hints)
    if n == 0:
        return hints

    # Sort by start_fraction
    hints = sorted(hints, key=lambda h: h.get("start_fraction", 0.0))

    # Assign even spacing if fractions are all defaults (0.0 to 1.0)
    all_default = all(
        h.get("start_fraction", 0.0) == 0.0 and h.get("end_fraction", 1.0) == 1.0
        for h in hints
    )

    if all_default:
        step = 1.0 / n
        for i, h in enumerate(hints):
            h["start_fraction"] = round(i * step, 3)
            h["end_fraction"] = round((i + 1) * step, 3)
    else:
        # Fix overlaps: each hint's start must be >= previous hint's end
        for i in range(1, n):
            prev_end = hints[i - 1]["end_fraction"]
            if hints[i]["start_fraction"] < prev_end:
                hints[i]["start_fraction"] = prev_end
            if hints[i]["end_fraction"] <= hints[i]["start_fraction"]:
                hints[i]["end_fraction"] = min(hints[i]["start_fraction"] + 0.1, 1.0)

    return hints


def validate_and_repair_hints(segments: list[dict]) -> list[dict]:
    """Validate and repair animation hints for all segments.

    Args:
        segments: list of segment dicts (from ScriptSegment.model_dump())

    Returns:
        list of repaired segment dicts
    """
    result = []
    for seg in segments:
        seg = dict(seg)
        section_title = seg.get("section_title", "")
        hints = [_validate_hint(h, section_title) for h in seg.get("animation_hints", [])]

        # Ensure minimum hint count
        while len(hints) < MIN_HINTS_PER_SEGMENT:
            frac_start = len(hints) / (len(hints) + 1)
            hints.append(_make_title_card_hint(
                section_title,
                start=frac_start,
                end=min(frac_start + 0.3, 1.0),
            ))

        hints = _normalize_fractions(hints)
        seg["animation_hints"] = hints
        result.append(seg)

    return result
