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

You will be given a segment's narration text, the original paper source, a target duration, and a VISUAL_STRATEGY hint. Write the BODY of a Manim Scene's `construct(self)` method. Your code will be inserted into:

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
- **NEVER use MathTex or Tex** — the LaTeX environment is unreliable. Use Text() for ALL text, including equations. For math, use Unicode characters: Text("E = mc²"), Text("O(n²)"), Text("∑"), Text("∈"), Text("≤"), etc.
- Keep all text short. The narration carries the detail — visuals should be diagrams, equations, charts, and structural layouts.
- Do NOT use external files, images, SVGs, or network resources.
- Do NOT use `self.camera` or `self.renderer` — just standard Scene methods.
- Do NOT define new classes or functions — write straight-line construct() code.
- **NEVER use GrowArrow() on CurvedArrow or CurvedDoubleArrow** — it causes an infinite hang. Use Create() instead. GrowArrow ONLY works on Arrow and DoubleArrow.
- **Sector uses `radius`, NOT `outer_radius`**: `Sector(radius=2, angle=PI/4)`. Passing `outer_radius=` causes a TypeError.
- **ONLY use colors from the whitelist below.** ORANGE has NO variants — no ORANGE_A/B/C/D/E. PURPLE has NO variants. PINK has NO variants. If you need a shade, pick from a color family that has variants (RED_A-E, BLUE_A-E, GREEN_A-E, YELLOW_A-E, TEAL_A-E, GOLD_A-E, MAROON_A-B).

## Spatial constraints — CRITICAL
The Manim frame is 14.2 × 8 units. Objects outside the safe zone are clipped or invisible.

**Safe zone**: x ∈ [-6.5, 6.5], y ∈ [-3.5, 3.5]. Never place objects beyond these bounds.

**Text size limits**:
- Title: font_size ≤ 36, max ~30 characters. Longer? Split into two lines or use scale().
- Body text: font_size ≤ 26, max ~45 characters per line.
- Labels: font_size ≤ 22.
- Never use font_size > 36 for any text.

**Axes sizing**: x_length ≤ 8, y_length ≤ 4.5. NEVER use x_length=10 or larger — it overflows the frame.
**Axes numbers**: NEVER use `include_numbers=True` or `add_coordinates()` on Axes — these depend on LaTeX and will crash. Instead, create manual Text() labels positioned with `axes.c2p()`:
```
        axes = Axes(x_range=[0, 5, 1], y_range=[0, 100, 20], x_length=7, y_length=4, tips=False)
        # Manual y-axis labels (NOT include_numbers)
        for val in [0, 20, 40, 60, 80, 100]:
            label = Text(str(val), font_size=16)
            label.next_to(axes.c2p(0, val), LEFT, buff=0.2)
            axes.add(label)
```

**Object size limits**:
- Single centered rectangle/box: width ≤ 8, height ≤ 4.
- Two-column layout: each column at x = ±3, width ≤ 5 each.
- Three-column layout: columns at x = {-4, 0, +4}, width ≤ 3.5 each.

**Overflow guard — ALWAYS add after building any VGroup, especially lists/charts**:
```
        if group.width > 12:
            group.scale_to_fit_width(12)
        if group.height > 7:
            group.scale_to_fit_height(7)
```

**Text placement & collision avoidance**:
Text objects have real width and height. Estimate bounding boxes before placing:
- font_size 36 → each character ≈ 0.35 units wide, line height ≈ 0.7 units
- font_size 26 → each character ≈ 0.25 units wide, line height ≈ 0.5 units
- font_size 22 → each character ≈ 0.21 units wide, line height ≈ 0.42 units
- font_size 16 → each character ≈ 0.15 units wide, line height ≈ 0.3 units

Label placement rules (for axes, bar charts, diagrams):
- For bar/column labels: alternate UP and DOWN to avoid overlap. E.g., even-index labels go below (DOWN), odd-index labels go above (UP).
- Always use `buff=0.4` or larger in `.next_to()` calls for labels — never use buff < 0.3.
- For y-axis labels, use `LEFT, buff=0.2` and keep labels ≤ 4 characters.
- For x-axis labels, stagger vertically or angle text if > 4 labels.

Annotation placement rules:
- Spread annotations to different compass directions (UP, DOWN, LEFT, RIGHT) — never stack multiple annotations in the same direction from a shared reference.
- Keep annotation text ≤ 20 characters. Longer? Abbreviate or use a numbered legend.
- If placing arrows + labels next to objects, always check: will the label's right edge exceed x=6.5 or left edge go below x=-6.5?

Post-placement collision check — use this pattern after building any group of labeled objects:
```
        # Verify no overlaps: check that label centers are ≥ 0.5 units apart
        labels = [label_1, label_2, label_3]
        for j in range(len(labels)):
            for k in range(j + 1, len(labels)):
                if abs(labels[j].get_center()[1] - labels[k].get_center()[1]) < 0.5:
                    labels[k].shift(DOWN * 0.5)
```

**Common traps to AVOID**:
- BulletedList with long items overflows right. Prefer VGroup of short Text items with arrange(DOWN).
- Chained .next_to() calls accumulate offset — check final position with .get_center().
- .to_edge() pushes objects to y=±3.7 / x=±6.7 — leave room for labels/braces next to them.
- Placing annotations with .next_to(obj, RIGHT) when obj is already near x=5 pushes text off-screen. Use .next_to(obj, UP) or .next_to(obj, DOWN) instead, or move_to a safe absolute position.
- CurvedArrow annotations placed .next_to(arrow, RIGHT) easily overflow. Keep annotation text short (<25 chars) and position above or below.
- Axes with x_length=10: the axes alone span 10 units, leaving no room for y-axis labels or annotations. Always use x_length ≤ 8.
- Bar charts with many categories: with 6+ bars, labels overlap. Limit to 4-5 bars or use small font_size ≤ 18.
- GrowArrow(curved_arrow) hangs forever. ALWAYS use Create() for CurvedArrow and CurvedDoubleArrow.
- Sector(outer_radius=N) crashes. Use Sector(radius=N) instead.
- ORANGE_C, PURPLE_A, PINK_B, etc. do not exist — NameError crash. Only use exact colors from the whitelist.
- MathTex/Tex/add_coordinates()/include_numbers crash due to LaTeX. Use Text() with Unicode instead.
- Pointless try/except: wrapping Text() in try/except with an identical fallback does nothing. Only use try/except when the try and except bodies are DIFFERENT (e.g. MathTex → Text fallback, but since we ban MathTex, this is rarely needed).
- BarChart class requires LaTeX for labels — use manual Rectangle + Text bar charts instead.
- Bar chart value labels all placed with `.next_to(bar, UP)` overlap when bars are close together. Alternate UP/DOWN or use small font_size ≤ 16.
- Annotations at screen edges: if an object is at x > 4.5, do NOT place annotations to its RIGHT — they'll be clipped. Use UP, DOWN, or LEFT instead.
- Multiple `.next_to(obj, UP)` calls on different labels from nearby objects stack them at the same y-coordinate, causing overlap. Stagger with incremental `shift(UP * 0.4 * i)`.

## Available API

**Objects**: Text, MarkupText, BulletedList, Paragraph, Rectangle, RoundedRectangle, Square, Circle, Ellipse, Arc, Annulus, Sector (use `radius=`, NOT `outer_radius=`), AnnularSector, Arrow, CurvedArrow, CurvedDoubleArrow, DoubleArrow, Line, DashedLine, Dot, Star, Triangle, Polygon, RegularPolygon, Brace, BraceLabel, SurroundingRectangle, BackgroundRectangle, Underline, Cross, Cutout, Axes (NO include_numbers, NO add_coordinates), NumberPlane, ComplexPlane, PolarPlane, NumberLine, Code, Table, VGroup, DecimalNumber, Integer, ValueTracker, always_redraw, TracedPath, VMobject
**BANNED** (crash due to LaTeX): MathTex, Tex, BarChart, MathTable, Matrix, DecimalMatrix, IntegerMatrix. Use Text() with Unicode and manual Rectangle bar charts instead.

**Animations**: Write, FadeIn, FadeOut, Create, Uncreate, DrawBorderThenFill, GrowFromCenter, GrowFromEdge, GrowFromPoint, GrowArrow (Arrow/DoubleArrow ONLY — NEVER on CurvedArrow), SpinInFromNothing, Indicate, Flash, Circumscribe, ShowPassingFlash, Wiggle, FocusOn, Transform, ReplacementTransform, TransformFromCopy, FadeTransform, ClockwiseTransform, CounterclockwiseTransform, ShrinkToCenter, MoveToTarget, MoveAlongPath, Rotate, ApplyWave, ShowIncreasingSubsets, ShowSubmobjectsOneByOne, AnimationGroup, Succession, LaggedStart, LaggedStartMap, AddTextLetterByLetter

**Colors**: WHITE, GRAY, GREY, RED, RED_A, RED_B, RED_C, RED_D, RED_E, BLUE, BLUE_A, BLUE_B, BLUE_C, BLUE_D, BLUE_E, GREEN, GREEN_A, GREEN_B, GREEN_C, GREEN_D, GREEN_E, YELLOW, YELLOW_A, YELLOW_B, YELLOW_C, YELLOW_D, YELLOW_E, ORANGE, PURPLE, TEAL, TEAL_A, TEAL_B, TEAL_C, TEAL_D, TEAL_E, PINK, GOLD, GOLD_A, GOLD_B, GOLD_C, GOLD_D, GOLD_E, MAROON, MAROON_A, MAROON_B, BLACK

**Color semantics**: RED = problems, limitations, old approach. GREEN = solutions, improvements, new approach. BLUE = neutral methods, tools, processes. GOLD = key insights, important findings.

**Positioning**: ORIGIN, UP, DOWN, LEFT, RIGHT, UL, UR, DL, DR
  .to_edge(UP), .to_corner(UL), .next_to(other, DOWN), .move_to([x, y, 0]), .shift(LEFT * 2)

**Formatting**: .scale(), .set_color(), .arrange(DOWN), .set_opacity(), .set_stroke(), .set_fill()

## Visual strategy patterns
Follow the VISUAL_STRATEGY hint to choose the right layout. Every segment MUST have at least 3 distinct visual elements — NOT just text cards.

**data_chart**: Use Axes (x_length ≤ 8, y_length ≤ 4.5, NO include_numbers) with plotted points/lines, or manual bar charts with Rectangle + Text labels. Show real numbers from the paper. Add Text labels and a title. Animate bars growing (GrowFromEdge) or points appearing sequentially. Do NOT use BarChart class (requires LaTeX).

**comparison**: Two-column layout. Left column (RED) = old/baseline, right column (GREEN) = new/proposed. Use RoundedRectangles as containers with Text labels inside. Connect with Arrows showing improvement. Animate left side first, then right side, then comparison arrows.

**process_flow**: Left-to-right or top-to-bottom sequence of RoundedRectangle boxes connected by Arrows. 3-5 steps max. Animate each box appearing then its connecting arrow. Use color gradient from BLUE to GREEN to show progression.

**concept_map**: Central node (Circle or RoundedRectangle) with branching connections (Lines/Arrows) to satellite nodes. Use different colors for different branches. Animate center first, then branches with LaggedStart.

**timeline**: Horizontal NumberLine or Line with Dots at key points and Text labels above/below alternating. Animate left to right. Use color to highlight the current paper's contribution.

**metaphor**: Build the concrete visual analogy from speaker_notes. Use shapes, arrows, and labels to illustrate the metaphor. E.g., "filter" → show objects passing through a barrier with some blocked (RED) and some passing (GREEN). At least 4 visual objects.

**highlight_list**: Vertical stack of 3-5 items. Each item is an icon shape (Circle, Star, Arrow) + short Text label in a row. Animate with LaggedStart. Highlight the most important item with Indicate or color change.

**layered_diagram**: Stacked horizontal rectangles (like a layer cake) with labels inside each. Bottom = foundation, top = application. Animate bottom-up. Use Braces on the side to group related layers.

**equation**: Center the key equation using Text() with Unicode math symbols (², ³, ∑, ∈, ≤, ≥, →, ×, ÷, π, θ, α, β, λ). Surround with annotating arrows/braces pointing to terms with Text labels explaining each part. Animate: show equation → highlight and label each term sequentially.

**auto** (fallback): Analyze the narration content and pick the most appropriate pattern from above. Qualitative content → concept_map or metaphor. Quantitative → data_chart. Sequential → process_flow.

## Duration filling
Your animations will likely finish before the voiceover ends. The compositor will freeze the last frame, but a static freeze looks bad. Plan for this:
1. After all animations finish, calculate remaining time: `remaining = DURATION - (sum of all run_times and waits so far)`.
2. If remaining > 1 second, add `self.wait(remaining - 1.0)` to hold the final visual.
3. As the very last action, fade out all remaining objects: `self.play(FadeOut(*self.mobjects), run_time=1.0)`.
4. This ensures the scene ends on a clean black frame rather than a frozen mid-animation state.
5. Spread `self.wait()` pauses throughout the animation, not just at the end — this looks more natural.

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
    speaker_notes: str = "",
    visual_strategy: str = "",
) -> str:
    """Call Claude to generate Manim construct() body for one segment."""
    client = _get_client()

    content = (
        f"## Section: {section_title}\n"
        f"## DURATION: {duration:.0f} seconds\n"
        f"## VISUAL_STRATEGY: {visual_strategy or 'auto'}\n\n"
        f"### Narration text (what the audience hears during this animation)\n"
        f"{narration_text}\n\n"
    )
    if speaker_notes:
        content += f"### Speaker notes (visual intent from the writer)\n{speaker_notes}\n\n"
    content += (
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
    speaker_notes: str = "",
    visual_strategy: str = "",
) -> tuple[str, list[AnimationHint]]:
    """Generate Manim code and minimal display hints for a single segment.

    Returns:
        (manim_code, animation_hints) where manim_code is the construct() body
        and animation_hints is a minimal list for UI display.
    """
    code = await _generate_manim_code(
        narration_text, section_title, paper_source_text, duration,
        speaker_notes=speaker_notes, visual_strategy=visual_strategy,
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

        # Use actual audio duration (voiceover runs before annotation now)
        duration = segment.actual_duration_seconds or segment.estimated_duration_seconds or 20.0

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
            speaker_notes=segment.speaker_notes,
            visual_strategy=segment.visual_strategy,
        )
        segment.manim_code = code
        segment.animation_hints = hints

        update_task(
            task_id,
            stage_progress=(i + 1) / total,
            message=f"Animating segment {i + 1}/{total}",
        )

    return script
