import asyncio
import json
import re

import anthropic

from app.config import settings
from app.models import PaperMeta, PaperSection, ScriptSegment, AnimationHint, VideoScript
from app.tasks.processing import update_task

SCRIPTWRITER_SYSTEM = """You are a scriptwriter for academic paper explainer videos.

Given a section of an academic paper, write narration segments suitable for a video voiceover. Each segment should be a self-contained narration unit (30-90 seconds when spoken aloud).

Requirements:
- Write in a clear, engaging, conversational style — as if explaining to a curious, educated audience
- Explain technical terms naturally when they first appear
- Each segment should flow logically from the previous
- Include animation_hints: suggest what could be shown on screen (equations, diagrams, bullet lists, highlights)
- Do NOT add "welcome" intros or "thanks for watching" outros

Output ONLY a JSON array of segment objects. Each object has these fields:
- "section_title": string — the section this belongs to
- "narration_text": string — the voiceover narration
- "speaker_notes": string — brief notes for context (not spoken)
- "animation_hints": array of {"type": string, "description": string, "content": string, "style": string}
  - type: one of "equation", "diagram", "bullet_list", "highlight", "code", "graph", "image_placeholder"
  - description: what to show
  - content: raw content (LaTeX formula, bullet text, etc.) — can be empty
  - style: animation style suggestion (e.g. "write", "fade_in", "transform") — can be empty

Output valid JSON only. No markdown fences, no commentary."""

AGGREGATOR_SYSTEM = """You are an editor assembling a video script from individually-written sections.

You receive a paper title and an array of narration segments written by different writers. Your job:
1. Add a brief intro segment (section_title: "Introduction") that sets up the paper's topic and importance (15-30s)
2. Add smooth transitions between major section changes where needed
3. Add a brief conclusion/summary segment (section_title: "Conclusion") (15-30s)
4. Ensure consistent tone and terminology throughout
5. Fix any redundancy between segments

Output the COMPLETE list of segments as a JSON array with the same schema as the input.
Each object: {"section_title", "narration_text", "speaker_notes", "animation_hints"}.

Output valid JSON only. No markdown fences, no commentary."""


def _get_client() -> anthropic.AsyncAnthropic:
    return anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)


def _estimate_duration(text: str, wpm: float = 150.0) -> float:
    """Estimate spoken duration in seconds based on word count."""
    words = len(text.split())
    return (words / wpm) * 60.0


def _parse_json_response(text: str) -> list[dict] | None:
    """Try to extract a JSON array from Claude's response."""
    text = text.strip()
    # Strip markdown fences if present
    text = re.sub(r'^```(?:json)?\s*', '', text)
    text = re.sub(r'\s*```$', '', text)
    text = text.strip()
    try:
        result = json.loads(text)
        if isinstance(result, list):
            return result
    except json.JSONDecodeError:
        pass
    # Try to find array in the text
    match = re.search(r'\[.*\]', text, re.DOTALL)
    if match:
        try:
            result = json.loads(match.group())
            if isinstance(result, list):
                return result
        except json.JSONDecodeError:
            pass
    return None


async def _call_scriptwriter(
    section_title: str, sections: list[PaperSection]
) -> list[dict]:
    """Call Claude to write script segments for one section group."""
    client = _get_client()

    content = f"## Section: {section_title}\n\n"
    for s in sections:
        content += f"[Chunk {s.chunk_index}]\n{s.text}\n\n"

    result = ""
    async with client.messages.stream(
        model="claude-sonnet-4-20250514",
        max_tokens=8192,
        system=[{
            "type": "text",
            "text": SCRIPTWRITER_SYSTEM,
            "cache_control": {"type": "ephemeral"},
        }],
        messages=[{"role": "user", "content": content}],
    ) as stream:
        async for chunk in stream.text_stream:
            result += chunk

    parsed = _parse_json_response(result)
    if parsed is not None:
        return parsed

    # Retry once with explicit JSON instruction
    result = ""
    async with client.messages.stream(
        model="claude-sonnet-4-20250514",
        max_tokens=8192,
        system=[{
            "type": "text",
            "text": SCRIPTWRITER_SYSTEM,
            "cache_control": {"type": "ephemeral"},
        }],
        messages=[
            {"role": "user", "content": content},
            {"role": "assistant", "content": "I'll output the segments as valid JSON.\n["},
        ],
    ) as stream:
        async for chunk in stream.text_stream:
            result += chunk

    parsed = _parse_json_response("[" + result)
    if parsed is not None:
        return parsed

    # Fallback: create a single segment from the raw text
    combined_text = " ".join(s.text for s in sections)
    return [{
        "section_title": section_title,
        "narration_text": combined_text[:2000],
        "speaker_notes": "Auto-generated fallback — scriptwriter JSON parse failed",
        "animation_hints": [],
    }]


async def _call_aggregator(paper_title: str, all_segments: list[dict]) -> list[dict]:
    """Call Claude to add intro/outro/transitions and ensure consistency."""
    client = _get_client()

    content = f"Paper title: {paper_title}\n\nSegments:\n{json.dumps(all_segments, indent=2)}"

    result = ""
    async with client.messages.stream(
        model="claude-sonnet-4-20250514",
        max_tokens=16384,
        system=[{
            "type": "text",
            "text": AGGREGATOR_SYSTEM,
            "cache_control": {"type": "ephemeral"},
        }],
        messages=[{"role": "user", "content": content}],
    ) as stream:
        async for chunk in stream.text_stream:
            result += chunk

    parsed = _parse_json_response(result)
    if parsed is not None:
        return parsed

    # Retry once
    result = ""
    async with client.messages.stream(
        model="claude-sonnet-4-20250514",
        max_tokens=16384,
        system=[{
            "type": "text",
            "text": AGGREGATOR_SYSTEM,
            "cache_control": {"type": "ephemeral"},
        }],
        messages=[
            {"role": "user", "content": content},
            {"role": "assistant", "content": "Here is the complete edited script as JSON:\n["},
        ],
    ) as stream:
        async for chunk in stream.text_stream:
            result += chunk

    parsed = _parse_json_response("[" + result)
    if parsed is not None:
        return parsed

    # On second failure, skip aggregation and return raw segments
    return all_segments


async def write_script(
    paper_id: str,
    meta: PaperMeta,
    chunk_groups: list[tuple[str, list[PaperSection]]],
    task_id: str,
) -> VideoScript:
    """Fan out parallel scriptwriters, aggregate, build VideoScript."""
    total_groups = len(chunk_groups)

    # Launch parallel scriptwriters
    tasks = []
    for section_title, sections in chunk_groups:
        tasks.append(asyncio.create_task(
            _call_scriptwriter(section_title, sections)
        ))

    # Await in order for progress reporting
    all_raw_segments: list[dict] = []
    for i, task in enumerate(tasks):
        segments = await task
        all_raw_segments.extend(segments)
        update_task(
            task_id,
            stage_progress=(i + 1) / total_groups,
            message=f"Scripting section {i + 1}/{total_groups}",
        )

    # Aggregation pass
    update_task(task_id, message="Aggregating and polishing script...")
    aggregated = await _call_aggregator(meta.filename, all_raw_segments)

    # Build VideoScript
    segments: list[ScriptSegment] = []
    for idx, seg_dict in enumerate(aggregated):
        hints = []
        for h in seg_dict.get("animation_hints", []):
            hints.append(AnimationHint(
                type=h.get("type", ""),
                description=h.get("description", ""),
                content=h.get("content", ""),
                style=h.get("style", ""),
            ))

        narration = seg_dict.get("narration_text", "")
        segment = ScriptSegment(
            segment_index=idx,
            section_title=seg_dict.get("section_title", ""),
            source_chunk_indices=seg_dict.get("source_chunk_indices", []),
            narration_text=narration,
            speaker_notes=seg_dict.get("speaker_notes", ""),
            animation_hints=hints,
            estimated_duration_seconds=_estimate_duration(narration),
        )
        segments.append(segment)

    total_est = sum(s.estimated_duration_seconds for s in segments)

    return VideoScript(
        paper_id=paper_id,
        title=meta.filename,
        total_segments=len(segments),
        estimated_total_duration_seconds=total_est,
        segments=segments,
    )
