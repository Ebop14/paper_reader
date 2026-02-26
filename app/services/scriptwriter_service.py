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
- visual_strategy should be an empty string "" — the aggregator assigns it later

Output ONLY a JSON array of segment objects. Each object has these fields:
- "section_title": string — the section this belongs to
- "narration_text": string — the voiceover narration (40-60 words, 15-25 seconds)
- "speaker_notes": string — brief notes for context (not spoken)
- "visual_strategy": "" (always empty — aggregator fills this)
- "animation_hints": [] (always empty)

Output valid JSON only. No markdown fences, no commentary."""

AGGREGATOR_SYSTEM = """You are a staff writer assembling a video script from independently-written section drafts into a single compelling narrative.

TARGET: 3-5 minute total video, 12-18 segments maximum, each segment 15-25 seconds (~40-60 words).

You receive a paper title and an array of narration segments. Your job is to transform disconnected section summaries into a flowing story.

## Narrative craft
1. **Story arc**: Open with a hook that states the problem or surprising finding. Build through approach → method → evidence → takeaway. End with a forward-looking conclusion.
2. **Transitions**: Every segment must connect to the next. Use connective phrases ("Building on this...", "But there's a catch...", "To test this idea...", "The key insight is..."). Never start consecutive segments with the same sentence structure.
3. **Rhythm variation**: Alternate short punchy sentences with longer explanatory ones. Avoid the "list of facts" cadence where every segment is "[Topic] is [definition]. It does [thing]. The result is [number]."
4. **Active voice**: Prefer "The authors discovered..." over "It was found that...". Prefer "This approach reduces error by 30%" over "A 30% error reduction was achieved."
5. **Eliminate redundancy**: Merge segments that repeat the same point. Cut throat-clearing phrases ("It is important to note that...", "In this section we will discuss...").
6. Add a brief intro segment (section_title: "Introduction") with a hook that makes the viewer care (10-15 seconds).
7. Add a brief conclusion segment (section_title: "Conclusion") that summarizes the main takeaway and looks forward (10-15 seconds).
8. Total segments MUST be 12-18. If you have more, merge. If fewer, that's fine.

## Visual strategy assignment
For EACH segment, assign one visual_strategy value based on what the narration describes:
- "data_chart" — when the segment presents quantitative results, comparisons of numbers, benchmarks, or performance metrics
- "comparison" — when contrasting two or more approaches, models, or ideas side by side
- "process_flow" — when describing a sequence of steps, a pipeline, or an algorithm
- "concept_map" — when explaining relationships between abstract concepts (write a concrete visual analogy in speaker_notes, e.g. "Show as a tree with X as root branching to Y and Z")
- "timeline" — when describing chronological development or sequential phases
- "metaphor" — when the content is qualitative and best explained via analogy (write the concrete metaphor in speaker_notes, e.g. "Like a filter that only lets certain signals through")
- "highlight_list" — when listing 3-5 key properties, features, or contributions
- "layered_diagram" — when describing architecture, layers, or hierarchical structures
- "equation" — when the segment centers on a specific mathematical formula or derivation

For "metaphor" and "concept_map" segments, you MUST write a concrete visual description in speaker_notes that the animator can use.

## TTS pacing rules (CRITICAL for audio quality)
The narration will be read by a text-to-speech engine, not a human. Follow these rules for natural-sounding output:
- **Short sentences**: 8-15 words each. The TTS engine handles short sentences much more clearly.
- **Complete thoughts**: End every segment with a complete sentence. Never leave a thought dangling across segment boundaries.
- **No parenthetical asides**: Avoid em-dashes, parentheses, and nested clauses — the TTS engine stumbles on these. Rewrite as separate sentences.
- **Avoid abbreviations**: Write "for example" not "e.g.", "that is" not "i.e.", "approximately" not "approx."
- **Numbers**: Write small numbers as words ("three layers") and large ones as digits ("1.5 million parameters").

## Transition variety
Never repeat the same transition pattern in consecutive segments. Rotate among these devices:
- **Contrast**: "But", "However", "On the other hand", "Unlike previous approaches"
- **Cause/consequence**: "As a result", "This means that", "Because of this"
- **Sequence**: "Building on this", "The next step", "From here"
- **Surprise/pivot**: "Here's where it gets interesting", "But there's a catch", "Surprisingly"
- **Question**: "So how does this actually work?", "But does this hold up in practice?"

Place transition phrases at the START of the next segment, not the end of the current one. This gives each segment a clean ending and a strong opening.

## Output
animation_hints MUST be an empty array [] — a separate annotator handles visuals.

Output the COMPLETE list of segments as a JSON array.
Each object: {"section_title", "narration_text", "speaker_notes", "visual_strategy", "animation_hints": []}.

Output valid JSON only. No markdown fences, no commentary."""


def _get_client() -> anthropic.AsyncAnthropic:
    return anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)


def _get_openai_client() -> openai.AsyncOpenAI:
    return openai.AsyncOpenAI(api_key=settings.openai_api_key)


def _estimate_duration(text: str, wpm: float = 90.0) -> float:
    """Estimate spoken duration in seconds based on word count.

    Kokoro TTS speaks at roughly 1.5 words/sec (~90 wpm) at 0.85× speed,
    slower than typical human 150 wpm.  Using 90 wpm gives estimates that
    match actual TTS output so downstream animation timing is accurate.
    """
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
        model="claude-opus-4-20250514",
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
        model="claude-opus-4-20250514",
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
            visual_strategy=seg_dict.get("visual_strategy", ""),
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
