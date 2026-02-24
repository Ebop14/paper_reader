import anthropic
from typing import AsyncGenerator
from app.config import settings

SYSTEM_VERBATIM = """You are a text cleanup assistant for academic papers that will be read aloud.
Clean up the given text chunk for text-to-speech:
- Remove citation brackets like [1], [2,3], (Author et al., 2023)
- Remove figure/table references like "Figure 1", "Table 2", "as shown in Fig. 3"
- Expand common abbreviations on first use (e.g., "NLP" -> "natural language processing")
- Fix OCR artifacts and broken words from PDF extraction
- Remove headers, footers, page numbers
- Keep the content faithful to the original - do not add or remove meaning
- Output ONLY the cleaned text, no commentary"""

SYSTEM_NARRATED = """You are a podcast host explaining an academic paper in an engaging way.
Rewrite the given text chunk as if narrating a podcast episode:
- Use conversational, accessible language
- Explain jargon and technical terms naturally
- Add brief transitions and context
- Make it engaging and easy to follow by ear
- Keep the core information accurate
- Do not add "welcome to" intros or "thanks for listening" outros - just the content
- Output ONLY the narrated text, no commentary"""


def _get_client() -> anthropic.AsyncAnthropic:
    return anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)


async def process_single_chunk(text: str, mode: str) -> str:
    """Process a single text chunk with Claude."""
    client = _get_client()
    system = SYSTEM_VERBATIM if mode == "verbatim" else SYSTEM_NARRATED

    result = ""
    async with client.messages.stream(
        model="claude-sonnet-4-20250514",
        max_tokens=4096,
        system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
        messages=[{"role": "user", "content": text}],
    ) as stream:
        async for chunk in stream.text_stream:
            result += chunk

    return result


async def process_chunks(
    texts: list[str], mode: str
) -> AsyncGenerator[tuple[int, str], None]:
    """Process multiple chunks sequentially, yielding (index, processed_text)."""
    for i, text in enumerate(texts):
        processed = await process_single_chunk(text, mode)
        yield i, processed
