"""Word-anchored annotation agent.

Reads each segment's narration + original paper source text and generates
dense, word-anchored animation hints.  Runs as a separate LLM pass after
the scriptwriter so that narration quality and visual richness are
independently optimised.
"""

import asyncio
import json
import re

import anthropic
import openai

from app.config import settings
from app.models import (
    AnimationHint, AnimationStep, ManimObject,
    PaperMeta, PaperSection, VideoScript,
)
from app.tasks.processing import update_task

# ---------------------------------------------------------------------------
# Manim reference (single source of truth — only consumed here)
# ---------------------------------------------------------------------------

MANIM_REFERENCE = """## Manim Animation Reference

You MUST use ONLY the object types, actions, colors, and positions listed below. Anything else will be rejected.

### Object types (mobject_type)
| mobject_type | Required params | Optional params |
|---|---|---|
| Text | text (str) | font_size (int, default 32), color (str) |
| MathTex | tex (str, raw LaTeX) | font_size (int), color (str) |
| BulletedList | items (list[str]) | font_size (int), buff (float) |
| Rectangle | width (float), height (float) | color (str), fill_opacity (float) |
| RoundedRectangle | width (float), height (float), corner_radius (float) | color (str), fill_opacity (float) |
| Circle | radius (float) | color (str), fill_opacity (float) |
| Arrow | start (list[3 floats]), end (list[3 floats]) | color (str), stroke_width (float) |
| Line | start (list[3 floats]), end (list[3 floats]) | color (str), stroke_width (float) |
| Dot | point (list[3 floats]) | color (str), radius (float) |
| Brace | target (str, name of another object), direction (str, e.g. "DOWN") | text (str) |
| SurroundingRectangle | target (str, name of another object) | color (str), buff (float) |
| Axes | x_range (list[3 nums]), y_range (list[3 nums]) | x_length (float), y_length (float), axis_config (dict) |
| BarChart | values (list[float]), bar_names (list[str]) | bar_colors (list[str]), y_range (list[3 nums]) |
| NumberLine | x_range (list[3 nums]) | length (float), include_numbers (bool) |
| Code | code (str), language (str) | font_size (int) |
| Table | rows (list[list[str]]) | col_labels (list[str]), row_labels (list[str]) |
| VGroup | children (list[str], names of other objects) | — |

### Actions (action)
| action | What it does | params |
|---|---|---|
| create | Draw shapes (Create animation) | run_time (float) |
| write | Write text/math (Write animation) | run_time (float) |
| fade_in | Fade in | run_time (float), shift (str, e.g. "DOWN") |
| fade_out | Fade out | run_time (float) |
| indicate | Pulse/highlight (Indicate) | scale_factor (float), color (str) |
| transform | Morph one object to another | target (str, name of destination object), run_time (float) |
| move_to | Move object to position | position (str or list[3 floats]), run_time (float) |
| scale | Scale object | scale_factor (float), run_time (float) |
| change_color | Animate color change | color (str), run_time (float) |
| wait | Pause | — |
| grow_arrow | Grow an arrow (GrowArrow) | run_time (float) |
| add_plot | Add plot line to Axes | function (str, e.g. "lambda x: x**2"), color (str), x_range (list[2 floats]) |

### Colors
WHITE, GREY, RED, BLUE, GREEN, YELLOW, ORANGE, PURPLE, TEAL, PINK, GOLD, MAROON

### Positioning
- "ORIGIN", "to_edge(UP)", "to_edge(DOWN)", "to_edge(LEFT)", "to_edge(RIGHT)"
- "to_corner(UL)", "to_corner(UR)", "to_corner(DL)", "to_corner(DR)"
- "[x, y, 0]" for explicit coordinates (x: -6 to 6, y: -3.5 to 3.5)

### Layout strategies
- **Progressive build**: Start with a title, add elements one by one
- **Side-by-side comparison**: Left vs right with arrows or labels
- **Hierarchical**: Top-level concept at top, details below
- **Before/after**: Show old approach, transform to new approach"""

ANNOTATOR_SYSTEM = f"""You are a visual annotation agent for academic paper explainer videos. You read narration text and the original paper source, then produce dense, word-anchored Manim animation hints.

{MANIM_REFERENCE}

### Your task
Given:
- **narration_text**: the voiceover for this segment
- **section_title**: which section of the paper this covers
- **paper_source_text**: the original paper text for context (equations, data, method names)

Produce 4-8 animation hints. Each hint MUST include:
- **anchor_text**: a VERBATIM quoted phrase from narration_text (3-8 words). The animation will appear exactly when these words are spoken.
- **type**: one of "equation", "diagram", "bullet_list", "highlight", "code", "graph", "image_placeholder"
- **description**: what this hint shows
- **content**: raw content (LaTeX, bullet text, etc.) — can be empty
- **objects**: array of Manim objects with full params
- **steps**: array of animation steps with full params
- **persistent**: true if objects should stay on screen for the next hint (e.g. axes that get plots added)

Rules:
- anchor_text must be an EXACT substring of narration_text (case-sensitive)
- Use REAL DATA from the paper source: actual equations, numbers, method names, results
- Each hint should create a meaningful visual — not just a title card
- Hints should progress logically: build up, compare, highlight, conclude
- Space hints across the full narration — don't cluster them all at the start
- Use persistent: true when building up complex diagrams across multiple hints

Output ONLY a JSON array of hint objects. No markdown fences, no commentary."""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_client() -> anthropic.AsyncAnthropic:
    return anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)


def _get_openai_client() -> openai.AsyncOpenAI:
    return openai.AsyncOpenAI(api_key=settings.openai_api_key)


def _parse_json_response(text: str) -> list[dict] | None:
    """Try to extract a JSON array from the response."""
    text = text.strip()
    text = re.sub(r'^```(?:json)?\s*', '', text)
    text = re.sub(r'\s*```$', '', text)
    text = text.strip()
    try:
        result = json.loads(text)
        if isinstance(result, list):
            return result
    except json.JSONDecodeError:
        pass
    match = re.search(r'\[.*\]', text, re.DOTALL)
    if match:
        try:
            result = json.loads(match.group())
            if isinstance(result, list):
                return result
        except json.JSONDecodeError:
            pass
    return None


def _parse_hint(h: dict) -> AnimationHint:
    """Parse a hint dict into an AnimationHint, handling both legacy and rich formats."""
    objects = []
    for obj in h.get("objects", []):
        objects.append(ManimObject(
            name=obj.get("name", "obj"),
            mobject_type=obj.get("mobject_type", "Text"),
            params=obj.get("params", {}),
            position=obj.get("position", ""),
        ))

    steps = []
    for step in h.get("steps", []):
        steps.append(AnimationStep(
            action=step.get("action", "fade_in"),
            target=step.get("target", ""),
            params=step.get("params", {}),
            duration=step.get("duration", 1.0),
        ))

    return AnimationHint(
        type=h.get("type", ""),
        description=h.get("description", ""),
        content=h.get("content", ""),
        style=h.get("style", ""),
        anchor_text=h.get("anchor_text", ""),
        objects=objects,
        steps=steps,
        persistent=h.get("persistent", False),
        start_fraction=h.get("start_fraction", 0.0),
        end_fraction=h.get("end_fraction", 1.0),
    )


# ---------------------------------------------------------------------------
# Anchor text → timing fraction computation
# ---------------------------------------------------------------------------

def _compute_fractions_from_anchor(
    narration_text: str, hints: list[AnimationHint],
) -> list[AnimationHint]:
    """Compute start_fraction/end_fraction from each hint's anchor_text position."""
    words = narration_text.split()
    total_words = len(words)
    if total_words == 0:
        # Even spacing fallback
        for i, hint in enumerate(hints):
            n = len(hints)
            hint.start_fraction = round(i / n, 3)
            hint.end_fraction = round((i + 1) / n, 3)
        return hints

    narration_lower = narration_text.lower()

    for i, hint in enumerate(hints):
        anchor = hint.anchor_text.strip()
        if not anchor:
            # No anchor — fall back to even spacing
            n = len(hints)
            hint.start_fraction = round(i / n, 3)
            hint.end_fraction = round((i + 1) / n, 3)
            continue

        # Find anchor in narration (case-insensitive char position, then map to word index)
        anchor_lower = anchor.lower()
        char_pos = narration_lower.find(anchor_lower)
        if char_pos == -1:
            # Anchor not found — even spacing fallback
            n = len(hints)
            hint.start_fraction = round(i / n, 3)
            hint.end_fraction = round((i + 1) / n, 3)
            continue

        # Count words before char_pos to get start word index
        prefix = narration_text[:char_pos]
        start_word_idx = len(prefix.split()) - (1 if prefix and not prefix.endswith(' ') else 0)
        start_word_idx = max(0, start_word_idx)

        anchor_word_count = len(anchor.split())
        end_word_idx = min(start_word_idx + anchor_word_count, total_words)

        hint.start_fraction = round(start_word_idx / total_words, 3)
        hint.end_fraction = round(end_word_idx / total_words, 3)

        # Ensure end > start
        if hint.end_fraction <= hint.start_fraction:
            hint.end_fraction = min(hint.start_fraction + 0.1, 1.0)

    return hints


# ---------------------------------------------------------------------------
# LLM calls
# ---------------------------------------------------------------------------

async def _call_annotator_claude(
    narration_text: str, section_title: str, paper_source_text: str,
) -> list[dict] | None:
    """Call Claude to generate annotation hints for one segment."""
    client = _get_client()

    content = (
        f"## Section: {section_title}\n\n"
        f"### Narration text\n{narration_text}\n\n"
        f"### Paper source text\n{paper_source_text}\n"
    )

    result = ""
    async with client.messages.stream(
        model="claude-sonnet-4-20250514",
        max_tokens=8192,
        system=[{
            "type": "text",
            "text": ANNOTATOR_SYSTEM,
            "cache_control": {"type": "ephemeral"},
        }],
        messages=[{"role": "user", "content": content}],
    ) as stream:
        async for chunk in stream.text_stream:
            result += chunk

    parsed = _parse_json_response(result)
    if parsed is not None:
        return parsed

    # Retry once with prefilled assistant response
    result = ""
    async with client.messages.stream(
        model="claude-sonnet-4-20250514",
        max_tokens=8192,
        system=[{
            "type": "text",
            "text": ANNOTATOR_SYSTEM,
            "cache_control": {"type": "ephemeral"},
        }],
        messages=[
            {"role": "user", "content": content},
            {"role": "assistant", "content": "[\n"},
        ],
    ) as stream:
        async for chunk in stream.text_stream:
            result += chunk

    return _parse_json_response("[" + result)


async def _call_annotator_openai(
    narration_text: str, section_title: str, paper_source_text: str,
) -> list[dict] | None:
    """Call OpenAI as fallback annotator."""
    client = _get_openai_client()

    content = (
        f"## Section: {section_title}\n\n"
        f"### Narration text\n{narration_text}\n\n"
        f"### Paper source text\n{paper_source_text}\n"
    )

    response = await client.chat.completions.create(
        model="gpt-4o",
        max_tokens=8192,
        messages=[
            {"role": "system", "content": ANNOTATOR_SYSTEM},
            {"role": "user", "content": content},
        ],
    )

    result = response.choices[0].message.content or ""
    parsed = _parse_json_response(result)
    if parsed is not None:
        return parsed

    # Retry once
    response = await client.chat.completions.create(
        model="gpt-4o",
        max_tokens=8192,
        messages=[
            {"role": "system", "content": ANNOTATOR_SYSTEM},
            {"role": "user", "content": content},
            {"role": "assistant", "content": "["},
        ],
    )

    result = "[" + (response.choices[0].message.content or "")
    return _parse_json_response(result)


async def _call_annotator(
    narration_text: str, section_title: str, paper_source_text: str,
) -> list[dict]:
    """Call Claude, falling back to OpenAI on error."""
    try:
        result = await _call_annotator_claude(narration_text, section_title, paper_source_text)
        if result is not None:
            return result
    except Exception:
        pass

    if settings.openai_api_key:
        try:
            result = await _call_annotator_openai(narration_text, section_title, paper_source_text)
            if result is not None:
                return result
        except Exception:
            pass

    # Final fallback: empty hints (validator will add title cards)
    return []


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

async def annotate_segment(
    narration_text: str, section_title: str, paper_source_text: str,
) -> list[AnimationHint]:
    """Generate word-anchored animation hints for a single segment."""
    raw_hints = await _call_annotator(narration_text, section_title, paper_source_text)
    hints = [_parse_hint(h) for h in raw_hints]
    hints = _compute_fractions_from_anchor(narration_text, hints)
    return hints


async def annotate_script(
    script: VideoScript,
    meta: PaperMeta,
    chunk_groups: list[tuple[str, list[PaperSection]]],
    task_id: str,
) -> VideoScript:
    """Annotate all segments in a script with word-anchored animation hints.

    Args:
        script: VideoScript with narration-only segments (empty animation_hints).
        meta: Paper metadata with sections for source text lookup.
        chunk_groups: Section groups from director (reused from Phase 1).
        task_id: For progress reporting.

    Returns:
        The same VideoScript with animation_hints filled in.
    """
    # Build a lookup: section_title -> combined paper source text
    source_by_title: dict[str, str] = {}
    for title, sections in chunk_groups:
        source_by_title[title] = "\n\n".join(s.text for s in sections)

    total = len(script.segments)

    for i, segment in enumerate(script.segments):
        if i > 0:
            await asyncio.sleep(0.5)  # Stagger to avoid rate limits

        # Find the best matching source text for this segment
        paper_source = source_by_title.get(segment.section_title, "")
        if not paper_source:
            # Try partial match
            for title, text in source_by_title.items():
                if title.lower() in segment.section_title.lower() or \
                   segment.section_title.lower() in title.lower():
                    paper_source = text
                    break
        if not paper_source:
            # Fall back to all source text (truncated)
            paper_source = "\n\n".join(
                s.text for s in meta.sections
            )[:4000]

        hints = await annotate_segment(
            segment.narration_text,
            segment.section_title,
            paper_source,
        )
        segment.animation_hints = hints

        update_task(
            task_id,
            stage_progress=(i + 1) / total,
            message=f"Annotating segment {i + 1}/{total}",
        )

    return script
