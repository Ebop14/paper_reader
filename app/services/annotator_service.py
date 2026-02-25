"""Manim code generation agent.

Reads each segment's narration + original paper source text and generates
complete Manim construct() body code.  Runs as a separate LLM pass after
the scriptwriter so that narration quality and visual richness are
independently optimised.
"""

import asyncio
import re

import anthropic

from app.config import settings
from app.models import (
    AnimationHint,
    PaperMeta, PaperSection, VideoScript,
)
from app.tasks.processing import update_task

# ---------------------------------------------------------------------------
# System prompt
# ---------------------------------------------------------------------------

ANIMATOR_SYSTEM = """You are an expert Manim Community Edition animator creating educational video segments for academic papers.

You will be given a segment's narration text, the original paper source, and a target duration. Write the BODY of a Manim Scene's `construct(self)` method. Your code will be inserted into:

```python
from manim import *

class SegmentScene(Scene):
    def construct(self):
        # YOUR CODE HERE
```

## Hard rules
- Write ONLY the method body, indented with 8 spaces (you are inside `def construct(self):`)
- Output ONLY Python code. No markdown fences, no commentary, no explanation.
- Your total animation run_time + wait time should approximately fill DURATION seconds.
- Always clear objects before introducing new ones — don't let the screen get cluttered.
- End the scene by fading out any remaining objects.
- Wrap ALL MathTex/Tex in try/except with a Text fallback — LaTeX compilation can fail:
    ```
        try:
            eq = MathTex(r"E = mc^2")
        except Exception:
            eq = Text("E = mc²", font_size=28)
    ```
- Keep all text short. The narration carries the detail — visuals should be diagrams, equations, charts, and structural layouts.
- Do NOT use external files, images, SVGs, or network resources.
- Do NOT use `self.camera` or `self.renderer` — just standard Scene methods.
- Do NOT define new classes or functions — write straight-line construct() code.

## Available API

**Objects**: Text, MathTex, Tex, MarkupText, BulletedList, Paragraph, Rectangle, RoundedRectangle, Square, Circle, Ellipse, Arc, Annulus, Sector, AnnularSector, Arrow, CurvedArrow, CurvedDoubleArrow, DoubleArrow, Line, DashedLine, Dot, Star, Triangle, Polygon, RegularPolygon, Brace, BraceLabel, SurroundingRectangle, BackgroundRectangle, Underline, Cross, Cutout, Axes, NumberPlane, ComplexPlane, PolarPlane, BarChart, NumberLine, Code, Table, MathTable, Matrix, DecimalMatrix, IntegerMatrix, VGroup, DecimalNumber, Integer, ValueTracker, always_redraw, TracedPath

**Animations**: Write, FadeIn, FadeOut, Create, Uncreate, DrawBorderThenFill, GrowFromCenter, GrowFromEdge, GrowFromPoint, GrowArrow, SpinInFromNothing, Indicate, Flash, Circumscribe, ShowPassingFlash, Wiggle, FocusOn, Transform, ReplacementTransform, TransformFromCopy, FadeTransform, ClockwiseTransform, CounterclockwiseTransform, ShrinkToCenter, MoveToTarget, MoveAlongPath, Rotate, ApplyWave, ShowIncreasingSubsets, ShowSubmobjectsOneByOne, AnimationGroup, Succession, LaggedStart, LaggedStartMap, AddTextLetterByLetter

**Colors**: WHITE, GRAY, GREY, RED, RED_A, RED_B, RED_C, RED_D, RED_E, BLUE, BLUE_A, BLUE_B, BLUE_C, BLUE_D, BLUE_E, GREEN, GREEN_A, GREEN_B, GREEN_C, GREEN_D, GREEN_E, YELLOW, YELLOW_A, YELLOW_B, YELLOW_C, YELLOW_D, YELLOW_E, ORANGE, PURPLE, TEAL, TEAL_A, TEAL_B, TEAL_C, TEAL_D, TEAL_E, PINK, GOLD, GOLD_A, GOLD_B, GOLD_C, GOLD_D, GOLD_E, MAROON, MAROON_A, MAROON_B, BLACK

**Positioning**: ORIGIN, UP, DOWN, LEFT, RIGHT, UL, UR, DL, DR
  .to_edge(UP), .to_corner(UL), .next_to(other, DOWN), .move_to([x, y, 0]), .shift(LEFT * 2)
  Coordinate range: x ∈ [-7, 7], y ∈ [-4, 4]

**Formatting**: .scale(), .set_color(), .arrange(DOWN), .set_opacity(), .set_stroke(), .set_fill()

## Style guide
- Use progressive reveal: build up complexity step by step
- Use color meaningfully: highlight key concepts, color-code comparisons
- Use spatial layout: comparisons side-by-side, hierarchies top-to-bottom, flow diagrams left-to-right
- Add self.wait(1) pauses after key moments to let viewers absorb
- Use REAL DATA from the paper: actual equations, actual numbers, actual method names, actual results
- Create rich visuals — diagrams, flow charts, annotated equations, data visualizations — NOT just text cards
- Group related animations with VGroup for clean transitions
- Use LaggedStart for lists and sequential reveals"""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_client() -> anthropic.AsyncAnthropic:
    return anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)


def _extract_code(text: str) -> str:
    """Extract Python code from the response, stripping markdown fences if present."""
    text = text.strip()
    # Strip markdown code fences
    match = re.search(r'```(?:python)?\s*\n(.*?)```', text, re.DOTALL)
    if match:
        return match.group(1).strip()
    # Strip leading/trailing ``` without language tag
    text = re.sub(r'^```\s*', '', text)
    text = re.sub(r'\s*```$', '', text)
    return text.strip()


def _make_title_card_code(section_title: str, duration: float) -> str:
    """Fallback: simple title card code."""
    safe = section_title.replace("\\", "\\\\").replace('"', '\\"')
    wait = max(duration - 2.0, 0.5)
    return (
        f'        title = Text("{safe}", font_size=36)\n'
        f'        self.play(FadeIn(title), run_time=1.0)\n'
        f'        self.wait({wait:.1f})\n'
        f'        self.play(FadeOut(title), run_time=1.0)'
    )


# ---------------------------------------------------------------------------
# LLM call
# ---------------------------------------------------------------------------

async def _generate_manim_code(
    narration_text: str,
    section_title: str,
    paper_source_text: str,
    duration: float,
) -> str:
    """Call Claude to generate Manim construct() body for one segment."""
    client = _get_client()

    content = (
        f"## Section: {section_title}\n"
        f"## DURATION: {duration:.0f} seconds\n\n"
        f"### Narration text (what the audience hears during this animation)\n"
        f"{narration_text}\n\n"
        f"### Paper source text (use real data from here)\n"
        f"{paper_source_text[:6000]}\n"
    )

    result = ""
    async with client.messages.stream(
        model="claude-opus-4-20250514",
        max_tokens=8192,
        system=[{
            "type": "text",
            "text": ANIMATOR_SYSTEM,
            "cache_control": {"type": "ephemeral"},
        }],
        messages=[{"role": "user", "content": content}],
    ) as stream:
        async for chunk in stream.text_stream:
            result += chunk

    code = _extract_code(result)
    if not code:
        return _make_title_card_code(section_title, duration)

    # Basic validation: must contain self.play or self.wait
    if "self.play" not in code and "self.wait" not in code and "self.add" not in code:
        return _make_title_card_code(section_title, duration)

    return code


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

async def annotate_segment(
    narration_text: str,
    section_title: str,
    paper_source_text: str,
    duration: float = 20.0,
) -> tuple[str, list[AnimationHint]]:
    """Generate Manim code and minimal display hints for a single segment.

    Returns:
        (manim_code, animation_hints) where manim_code is the construct() body
        and animation_hints is a minimal list for UI display.
    """
    code = await _generate_manim_code(
        narration_text, section_title, paper_source_text, duration,
    )

    # Create a minimal hint for UI badge display
    hints = [AnimationHint(
        type="animation",
        description=f"Manim scene ({len(code.splitlines())} lines)",
    )]

    return code, hints


async def annotate_script(
    script: VideoScript,
    meta: PaperMeta,
    chunk_groups: list[tuple[str, list[PaperSection]]],
    task_id: str,
) -> VideoScript:
    """Annotate all segments with Manim code.

    Args:
        script: VideoScript with narration-only segments.
        meta: Paper metadata with sections for source text lookup.
        chunk_groups: Section groups from director (reused from Phase 1).
        task_id: For progress reporting.

    Returns:
        The same VideoScript with manim_code and animation_hints filled in.
    """
    # Build a lookup: section_title -> combined paper source text
    source_by_title: dict[str, str] = {}
    for title, sections in chunk_groups:
        source_by_title[title] = "\n\n".join(s.text for s in sections)

    total = len(script.segments)

    for i, segment in enumerate(script.segments):
        if i > 0:
            await asyncio.sleep(0.5)  # Stagger to avoid rate limits

        # Estimated duration for timing guidance
        duration = segment.estimated_duration_seconds or 20.0

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

        code, hints = await annotate_segment(
            segment.narration_text,
            segment.section_title,
            paper_source,
            duration,
        )
        segment.manim_code = code
        segment.animation_hints = hints

        update_task(
            task_id,
            stage_progress=(i + 1) / total,
            message=f"Animating segment {i + 1}/{total}",
        )

    return script
