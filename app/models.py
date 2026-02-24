from datetime import datetime

from pydantic import BaseModel, Field


class PaperSection(BaseModel):
    title: str
    text: str
    chunk_index: int


class PaperMeta(BaseModel):
    id: str
    filename: str
    num_pages: int
    sections: list[PaperSection]
    total_chars: int


class ProcessRequest(BaseModel):
    mode: str = "verbatim"  # "verbatim" or "narrated"


class TTSRequest(BaseModel):
    paper_id: str
    voice: str = "serena"
    speed: float = 1.0


class MusicGenerateRequest(BaseModel):
    prompt: str
    duration: int = 30


class MixExportRequest(BaseModel):
    paper_id: str
    music_id: str | None = None
    speech_volume: float = 1.0
    music_volume: float = 0.3


class TaskStatus(BaseModel):
    task_id: str
    status: str  # "pending", "running", "completed", "failed"
    progress: float = 0.0
    current_chunk: int = 0
    total_chunks: int = 0
    message: str = ""


# --- Video Pipeline Models ---


class AnimationHint(BaseModel):
    type: str = ""  # e.g. "equation", "diagram", "bullet_list", "highlight"
    description: str = ""
    content: str = ""  # Raw content (LaTeX, bullet text, etc.)
    style: str = ""  # e.g. "fade_in", "write", "transform"


class ScriptSegment(BaseModel):
    segment_index: int
    section_title: str
    source_chunk_indices: list[int] = Field(default_factory=list)
    narration_text: str
    speaker_notes: str = ""
    animation_hints: list[AnimationHint] = Field(default_factory=list)
    estimated_duration_seconds: float = 0.0
    actual_duration_seconds: float | None = None
    audio_file: str | None = None


class VideoScript(BaseModel):
    paper_id: str
    title: str
    created_at: str = Field(default_factory=lambda: datetime.now().isoformat())
    total_segments: int = 0
    estimated_total_duration_seconds: float = 0.0
    actual_total_duration_seconds: float | None = None
    segments: list[ScriptSegment] = Field(default_factory=list)
    metadata: dict = Field(default_factory=dict)


class PipelineRequest(BaseModel):
    voice: str = "serena"
    speed: float = 1.0
