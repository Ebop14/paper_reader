import asyncio
import json
import re

import anthropic
import openai

from app.config import settings
from app.models import (
    PaperMeta, PaperSection, ScriptSegment, VideoScript,
)
from app.tasks.processing import update_task

MAX_SEGMENTS = 18

SCRIPTWRITER_SYSTEM = """You are a scriptwriter for academic paper explainer videos. You write clear, engaging narration — animation visuals are handled separately.

Given a section of an academic paper, write narration segments. Each segment should be 15-25 seconds when spoken aloud (~40-60 words). Be concise — distill the key insight, don't pad.

Requirements:
- Write in a clear, engaging, conversational style — as if explaining to a curious, educated audience
- Explain technical terms naturally when they first appear
- Each segment should flow logically from the previous
- Use specific data, equations, and method names from the paper in narration
- Do NOT add "welcome" intros or "thanks for watching" outros
- animation_hints MUST be an empty array [] — a separate annotator handles visuals

Output ONLY a JSON array of segment objects. Each object has these fields:
- "section_title": string — the section this belongs to
- "narration_text": string — the voiceover narration (40-60 words, 15-25 seconds)
- "speaker_notes": string — brief notes for context (not spoken)
- "animation_hints": [] (always empty)

Output valid JSON only. No markdown fences, no commentary."""

AGGREGATOR_SYSTEM = """You are an editor assembling a video script from individually-written sections.

TARGET: 3-5 minute total video, 12-18 segments maximum, each segment 15-25 seconds.

You receive a paper title and an array of narration segments. Your job:
1. Add a brief intro segment (section_title: "Introduction") that sets up the paper (10-15 seconds)
2. AGGRESSIVELY compress: merge redundant segments, cut filler, tighten prose
3. Add a brief conclusion segment (section_title: "Conclusion") (10-15 seconds)
4. Ensure consistent tone and terminology
5. Total segments MUST be 12-18. If you have more, merge. If fewer, that's fine.
6. Do NOT add animation_hints — keep animation_hints as an empty array []

Output the COMPLETE list of segments as a JSON array with the same schema as the input.
Each object: {"section_title", "narration_text", "speaker_notes", "animation_hints": []}.

Output valid JSON only. No markdown fences, no commentary."""


def _get_client() -> anthropic.AsyncAnthropic:
    return anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)


def _get_openai_client() -> openai.AsyncOpenAI:
    return openai.AsyncOpenAI(api_key=settings.openai_api_key)


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


async def _call_scriptwriter_claude(
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
        max_tokens=12288,
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
        max_tokens=12288,
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

    return None


async def _call_scriptwriter_openai(
    section_title: str, sections: list[PaperSection]
) -> list[dict] | None:
    """Call OpenAI as fallback scriptwriter."""
    client = _get_openai_client()

    content = f"## Section: {section_title}\n\n"
    for s in sections:
        content += f"[Chunk {s.chunk_index}]\n{s.text}\n\n"

    response = await client.chat.completions.create(
        model="gpt-4o",
        max_tokens=12288,
        messages=[
            {"role": "system", "content": SCRIPTWRITER_SYSTEM},
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
        max_tokens=12288,
        messages=[
            {"role": "system", "content": SCRIPTWRITER_SYSTEM},
            {"role": "user", "content": content},
            {"role": "assistant", "content": "["},
        ],
    )

    result = "[" + (response.choices[0].message.content or "")
    return _parse_json_response(result)


async def _call_scriptwriter(
    section_title: str, sections: list[PaperSection]
) -> list[dict]:
    """Call Claude, falling back to OpenAI on error."""
    # Try Claude first
    try:
        result = await _call_scriptwriter_claude(section_title, sections)
        if result is not None:
            return result
    except Exception:
        pass

    # Fallback to OpenAI
    if settings.openai_api_key:
        try:
            result = await _call_scriptwriter_openai(section_title, sections)
            if result is not None:
                return result
        except Exception:
            pass

    # Final fallback: raw text segment
    combined_text = " ".join(s.text for s in sections)
    return [{
        "section_title": section_title,
        "narration_text": combined_text[:2000],
        "speaker_notes": "Auto-generated fallback — both Claude and OpenAI failed",
        "animation_hints": [],
    }]


async def _call_aggregator_claude(paper_title: str, all_segments: list[dict]) -> list[dict] | None:
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
    return parsed


async def _call_aggregator_openai(paper_title: str, all_segments: list[dict]) -> list[dict] | None:
    """Call OpenAI as fallback aggregator."""
    client = _get_openai_client()

    content = f"Paper title: {paper_title}\n\nSegments:\n{json.dumps(all_segments, indent=2)}"

    response = await client.chat.completions.create(
        model="gpt-4o",
        max_tokens=16384,
        messages=[
            {"role": "system", "content": AGGREGATOR_SYSTEM},
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
        max_tokens=16384,
        messages=[
            {"role": "system", "content": AGGREGATOR_SYSTEM},
            {"role": "user", "content": content},
            {"role": "assistant", "content": "["},
        ],
    )

    result = "[" + (response.choices[0].message.content or "")
    return _parse_json_response(result)


async def _call_aggregator(paper_title: str, all_segments: list[dict]) -> list[dict]:
    """Call Claude, falling back to OpenAI on error."""
    # Try Claude first
    try:
        result = await _call_aggregator_claude(paper_title, all_segments)
        if result is not None:
            return result
    except Exception:
        pass

    # Fallback to OpenAI
    if settings.openai_api_key:
        try:
            result = await _call_aggregator_openai(paper_title, all_segments)
            if result is not None:
                return result
        except Exception:
            pass

    # Final fallback: return raw segments unmodified
    return all_segments


async def write_script(
    paper_id: str,
    meta: PaperMeta,
    chunk_groups: list[tuple[str, list[PaperSection]]],
    task_id: str,
) -> VideoScript:
    """Launch parallel scriptwriters with staggered starts, then aggregate."""
    total_groups = len(chunk_groups)

    # Launch parallel scriptwriters with 0.5s stagger to avoid rate-limit bursts
    tasks = []
    for i, (section_title, sections) in enumerate(chunk_groups):
        if i > 0:
            await asyncio.sleep(0.5)
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

    # Hard-cap at MAX_SEGMENTS
    if len(aggregated) > MAX_SEGMENTS:
        aggregated = aggregated[:MAX_SEGMENTS]

    # Build VideoScript (no animation hints — annotator handles those)
    segments: list[ScriptSegment] = []
    for idx, seg_dict in enumerate(aggregated):
        narration = seg_dict.get("narration_text", "")
        segment = ScriptSegment(
            segment_index=idx,
            section_title=seg_dict.get("section_title", ""),
            source_chunk_indices=seg_dict.get("source_chunk_indices", []),
            narration_text=narration,
            speaker_notes=seg_dict.get("speaker_notes", ""),
            animation_hints=[],
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
